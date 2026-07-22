# -*- coding: utf-8 -*-
"""Immutable persistence for portfolio decision-twin runs."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from database import (
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
)


RUN_SCHEMA_VERSION = "portfolio_twin_run.v1"
REQUIRED_TABLES = {"portfolio_twin_runs"}


class PortfolioTwinRepositoryError(RuntimeError):
    pass


class PortfolioTwinNotFoundError(PortfolioTwinRepositoryError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_twin_runs (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    actor_id                TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    method_version          TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK(status IN ('complete','partial','blocked')),
    scenario_json           TEXT NOT NULL,
    scenario_sha256         TEXT NOT NULL,
    holdings_json           TEXT NOT NULL,
    holdings_sha256         TEXT NOT NULL,
    exposure_snapshot_id    TEXT,
    exposure_json           TEXT NOT NULL,
    exposure_sha256         TEXT NOT NULL,
    profile_version_id      TEXT,
    profile_json            TEXT NOT NULL,
    profile_sha256          TEXT NOT NULL,
    result_json             TEXT NOT NULL,
    result_sha256           TEXT NOT NULL,
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_twin_runs_scope
ON portfolio_twin_runs(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_twin_runs_evidence
ON portfolio_twin_runs(user_id, holdings_sha256, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_portfolio_twin_runs_no_update
BEFORE UPDATE ON portfolio_twin_runs BEGIN
    SELECT RAISE(ABORT, 'portfolio twin runs are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_twin_runs_no_delete
BEFORE DELETE ON portfolio_twin_runs BEGIN
    SELECT RAISE(ABORT, 'portfolio twin runs are immutable');
END;
"""


class PortfolioTwinRepository:
    def __init__(self, database_target: str | os.PathLike[str] | None = None) -> None:
        self.database_target = str(
            database_target
            or configured_database_target(
                str(Path(__file__).resolve().parent / "stock_assistant.db")
            )
        )
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with connect_database(self.database_target, close_on_exit=True) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
            self._schema_ready = True

    def _connect(self):
        self._ensure_schema()
        return connect_database(self.database_target, close_on_exit=True)

    @staticmethod
    def _from_row(
        row,
        *,
        include_evidence: bool = True,
        include_result: bool = True,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        json_fields = ("scenario",)
        if include_result:
            json_fields += ("result",)
        if include_evidence:
            json_fields += ("holdings", "exposure", "profile")
        for name in json_fields:
            raw = item.pop(f"{name}_json", None)
            item[name] = _load(raw, [] if name == "holdings" else {})
        item["integrity"] = PortfolioTwinRepository._integrity(item)
        return item

    @staticmethod
    def _integrity(item: dict[str, Any]) -> dict[str, Any]:
        expected = ("scenario", "holdings", "exposure", "profile", "result")
        checks: dict[str, bool] = {}
        for name in expected:
            if name not in item:
                continue
            checks[name] = sha256_text(canonical_json(item[name])) == item.get(
                f"{name}_sha256"
            )
        checks_complete = all(name in item for name in expected)
        return {
            "verified": checks_complete and all(checks.values()),
            "available_checks_verified": bool(checks) and all(checks.values()),
            "checks_complete": checks_complete,
            "checks": checks,
            "schema_version": item.get("schema_version"),
        }

    def create_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        method_version: str,
        status: str,
        scenario: dict[str, Any],
        holdings: list[dict[str, Any]],
        exposure: dict[str, Any],
        profile: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if status not in {"complete", "partial", "blocked"}:
            raise ValueError("invalid portfolio twin run status")
        values = {
            "scenario": canonical_json(scenario),
            "holdings": canonical_json(holdings),
            "exposure": canonical_json(exposure),
            "profile": canonical_json(profile),
            "result": canonical_json(result),
        }
        run_id = f"portfolio_twin_{uuid.uuid4().hex}"
        created_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
        snapshot = exposure.get("snapshot") if isinstance(exposure, dict) else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_twin_runs(
                    id, tenant_id, user_id, actor_id, schema_version,
                    method_version, status, scenario_json, scenario_sha256,
                    holdings_json, holdings_sha256, exposure_snapshot_id,
                    exposure_json, exposure_sha256, profile_version_id,
                    profile_json, profile_sha256, result_json, result_sha256,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    tenant_id,
                    user_id,
                    actor_id,
                    RUN_SCHEMA_VERSION,
                    method_version,
                    status,
                    values["scenario"],
                    sha256_text(values["scenario"]),
                    values["holdings"],
                    sha256_text(values["holdings"]),
                    (snapshot or {}).get("id") if isinstance(snapshot, dict) else None,
                    values["exposure"],
                    sha256_text(values["exposure"]),
                    profile.get("profile_version_id") if isinstance(profile, dict) else None,
                    values["profile"],
                    sha256_text(values["profile"]),
                    values["result"],
                    sha256_text(values["result"]),
                    created_at,
                ),
            )
        created = self.get_run(
            run_id, tenant_id=tenant_id, user_id=user_id, include_evidence=True
        )
        if created is None:
            raise PortfolioTwinRepositoryError("created portfolio twin run disappeared")
        return created

    def get_run(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        include_evidence: bool = True,
    ) -> dict[str, Any] | None:
        evidence_fields = (
            ", holdings_json, exposure_json, profile_json" if include_evidence else ""
        )
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT id, tenant_id, user_id, actor_id, schema_version,
                       method_version, status, scenario_json, scenario_sha256,
                       holdings_sha256, exposure_snapshot_id, exposure_sha256,
                       profile_version_id, profile_sha256, result_json,
                       result_sha256, created_at{evidence_fields}
                FROM portfolio_twin_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
        return self._from_row(row, include_evidence=include_evidence)

    def list_runs(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, tenant_id, user_id, actor_id, schema_version,
                       method_version, status, scenario_json, scenario_sha256,
                       holdings_sha256, exposure_snapshot_id, exposure_sha256,
                       profile_version_id, profile_sha256, result_sha256,
                       created_at
                FROM portfolio_twin_runs
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (tenant_id, user_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            self._from_row(row, include_evidence=False, include_result=False)
            for row in rows
        ]

    def verify_run(self, run_id: str, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        item = self.get_run(
            run_id, tenant_id=tenant_id, user_id=user_id, include_evidence=True
        )
        if item is None:
            raise PortfolioTwinNotFoundError("组合数字孪生运行不存在")
        return {"run_id": run_id, **item["integrity"]}
