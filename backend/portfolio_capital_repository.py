# -*- coding: utf-8 -*-
"""Immutable persistence for whole-portfolio capital decision plans."""

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


PLAN_SCHEMA_VERSION = "portfolio_capital_plan.v1"
REQUIRED_TABLES = {"portfolio_capital_decision_plans"}


class PortfolioCapitalRepositoryError(RuntimeError):
    pass


class PortfolioCapitalPlanNotFoundError(PortfolioCapitalRepositoryError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_payload(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_capital_decision_plans (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    actor_id                TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK(status IN ('ready','watch','blocked')),
    decision_date           TEXT NOT NULL,
    profile_version_id      TEXT,
    valuation_snapshot_id   TEXT,
    action_report_id        TEXT,
    exposure_snapshot_id    TEXT,
    evidence_json           TEXT NOT NULL,
    evidence_sha256         TEXT NOT NULL,
    result_json             TEXT NOT NULL,
    result_sha256           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, engine_version, evidence_sha256)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_plans_scope
ON portfolio_capital_decision_plans(
    tenant_id, user_id, created_at DESC, id DESC
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_plans_evidence
ON portfolio_capital_decision_plans(
    user_id, evidence_sha256, created_at DESC
);
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_plans_no_update
BEFORE UPDATE ON portfolio_capital_decision_plans BEGIN
    SELECT RAISE(ABORT, 'portfolio capital decision plans are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_plans_no_delete
BEFORE DELETE ON portfolio_capital_decision_plans BEGIN
    SELECT RAISE(ABORT, 'portfolio capital decision plans are immutable');
END;
"""


class PortfolioCapitalRepository:
    def __init__(
        self, database_target: str | os.PathLike[str] | None = None
    ) -> None:
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
            with connect_database(
                self.database_target, close_on_exit=True
            ) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
            self._schema_ready = True

    def _connect(self):
        self._ensure_schema()
        return connect_database(self.database_target, close_on_exit=True)

    @staticmethod
    def _integrity(item: dict[str, Any]) -> dict[str, Any]:
        evidence_verified = (
            isinstance(item.get("evidence"), dict)
            and sha256_payload(item["evidence"])
            == item.get("evidence_sha256")
        )
        result_verified = (
            isinstance(item.get("result"), dict)
            and sha256_payload(item["result"]) == item.get("result_sha256")
        )
        bindings = (item.get("evidence") or {}).get("bindings") or {}
        binding_checks = {
            "profile_version_id": item.get("profile_version_id")
            == bindings.get("profile_version_id"),
            "valuation_snapshot_id": item.get("valuation_snapshot_id")
            == bindings.get("valuation_snapshot_id"),
            "action_report_id": item.get("action_report_id")
            == bindings.get("action_report_id"),
            "exposure_snapshot_id": item.get("exposure_snapshot_id")
            == bindings.get("exposure_snapshot_id"),
        }
        schema_verified = (
            item.get("schema_version") == PLAN_SCHEMA_VERSION
            and (item.get("result") or {}).get("schema_version")
            == "portfolio_capital_decision.v1"
            and (item.get("result") or {}).get("engine_version")
            == item.get("engine_version")
        )
        return {
            "verified": bool(
                evidence_verified
                and result_verified
                and schema_verified
                and all(binding_checks.values())
            ),
            "evidence_verified": evidence_verified,
            "result_verified": result_verified,
            "schema_verified": schema_verified,
            "binding_checks": binding_checks,
        }

    @classmethod
    def _from_row(
        cls,
        row,
        *,
        include_evidence: bool = True,
        include_result: bool = True,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        if include_evidence:
            item["evidence"] = _load(
                item.pop("evidence_json", None), {}
            )
        else:
            item.pop("evidence_json", None)
        if include_result:
            item["result"] = _load(item.pop("result_json", None), {})
        else:
            item.pop("result_json", None)
        if include_evidence and include_result:
            item["integrity"] = cls._integrity(item)
        return item

    def create_plan(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        engine_version: str,
        status: str,
        decision_date: str,
        evidence: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        if status not in {"ready", "watch", "blocked"}:
            raise ValueError("invalid portfolio capital plan status")
        if not isinstance(evidence, dict) or not isinstance(result, dict):
            raise TypeError("portfolio capital plan evidence and result must be objects")
        bindings = evidence.get("bindings") or {}
        evidence_json = canonical_json(evidence)
        result_json = canonical_json(result)
        evidence_sha256 = sha256_text(evidence_json)
        result_sha256 = sha256_text(result_json)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM portfolio_capital_decision_plans
                WHERE tenant_id=? AND user_id=? AND engine_version=?
                  AND evidence_sha256=?
                """,
                (
                    tenant_id,
                    user_id,
                    engine_version,
                    evidence_sha256,
                ),
            ).fetchone()
            if existing is not None:
                item = self._from_row(existing)
                if item is None:
                    raise PortfolioCapitalRepositoryError(
                        "资金决策计划去重读取失败"
                    )
                return item, False
            plan_id = f"capital_plan_{uuid.uuid4().hex}"
            created_at = _iso()
            connection.execute(
                """
                INSERT INTO portfolio_capital_decision_plans(
                    id, tenant_id, user_id, actor_id, schema_version,
                    engine_version, status, decision_date,
                    profile_version_id, valuation_snapshot_id,
                    action_report_id, exposure_snapshot_id,
                    evidence_json, evidence_sha256, result_json,
                    result_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    tenant_id,
                    user_id,
                    actor_id,
                    PLAN_SCHEMA_VERSION,
                    engine_version,
                    status,
                    decision_date,
                    bindings.get("profile_version_id"),
                    bindings.get("valuation_snapshot_id"),
                    bindings.get("action_report_id"),
                    bindings.get("exposure_snapshot_id"),
                    evidence_json,
                    evidence_sha256,
                    result_json,
                    result_sha256,
                    created_at,
                ),
            )
            saved = connection.execute(
                """
                SELECT * FROM portfolio_capital_decision_plans
                WHERE id=?
                """,
                (plan_id,),
            ).fetchone()
        item = self._from_row(saved)
        if item is None:
            raise PortfolioCapitalRepositoryError(
                "资金决策计划保存后不可读取"
            )
        return item, True

    def get_plan(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_capital_decision_plans
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (plan_id, tenant_id, user_id),
            ).fetchone()
        return self._from_row(row)

    def latest_plan(
        self,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_capital_decision_plans
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (tenant_id, user_id),
            ).fetchone()
        return self._from_row(row)

    def list_plans(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM portfolio_capital_decision_plans
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (
                    tenant_id,
                    user_id,
                    max(1, min(100, int(limit))),
                ),
            ).fetchall()
        return [
            item
            for item in (
                self._from_row(row, include_evidence=False)
                for row in rows
            )
            if item is not None
        ]

    def verify_plan(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        item = self.get_plan(
            plan_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise PortfolioCapitalPlanNotFoundError(
                "资金决策计划不存在"
            )
        return {"plan_id": plan_id, **item["integrity"]}


repository = PortfolioCapitalRepository()
