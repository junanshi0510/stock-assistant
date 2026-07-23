# -*- coding: utf-8 -*-
"""Immutable mandates produced by the opportunity investment committee."""

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


MANDATE_SCHEMA_VERSION = "opportunity_committee_mandate.v1"
RESULT_SCHEMA_VERSION = "opportunity_investment_committee.v1"
REQUIRED_TABLES = {"opportunity_committee_mandates"}


class OpportunityCommitteeRepositoryError(RuntimeError):
    pass


class OpportunityCommitteeMandateNotFoundError(
    OpportunityCommitteeRepositoryError
):
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
    return current.astimezone(dt.timezone.utc).isoformat(
        timespec="milliseconds"
    )


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunity_committee_mandates (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    actor_id            TEXT NOT NULL,
    schema_version      TEXT NOT NULL,
    engine_version      TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(
        status IN ('active','concentrated','collecting','degraded')
    ),
    evidence_cutoff_at  TEXT,
    evidence_json       TEXT NOT NULL,
    evidence_sha256     TEXT NOT NULL,
    result_json         TEXT NOT NULL,
    result_sha256       TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(user_id, engine_version, evidence_sha256)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_committee_scope
ON opportunity_committee_mandates(user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_opportunity_committee_evidence
ON opportunity_committee_mandates(
    user_id, evidence_sha256, created_at DESC
);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_committee_no_update
BEFORE UPDATE ON opportunity_committee_mandates BEGIN
    SELECT RAISE(ABORT, 'opportunity committee mandates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_committee_no_delete
BEFORE DELETE ON opportunity_committee_mandates BEGIN
    SELECT RAISE(ABORT, 'opportunity committee mandates are immutable');
END;
"""


class OpportunityCommitteeRepository:
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
        return connect_database(
            self.database_target, close_on_exit=True
        )

    @staticmethod
    def _integrity(item: dict[str, Any]) -> dict[str, Any]:
        evidence_verified = (
            isinstance(item.get("evidence"), dict)
            and sha256_payload(item["evidence"])
            == item.get("evidence_sha256")
        )
        result_verified = (
            isinstance(item.get("result"), dict)
            and sha256_payload(item["result"])
            == item.get("result_sha256")
        )
        schema_verified = bool(
            item.get("schema_version") == MANDATE_SCHEMA_VERSION
            and (item.get("result") or {}).get("schema_version")
            == RESULT_SCHEMA_VERSION
            and (item.get("result") or {}).get("engine_version")
            == item.get("engine_version")
        )
        evidence_binding_verified = bool(
            (item.get("result") or {}).get("evidence_sha256")
            == item.get("evidence_sha256")
        )
        return {
            "verified": bool(
                evidence_verified
                and result_verified
                and schema_verified
                and evidence_binding_verified
            ),
            "evidence_verified": evidence_verified,
            "result_verified": result_verified,
            "schema_verified": schema_verified,
            "evidence_binding_verified": evidence_binding_verified,
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
            item["result"] = _load(
                item.pop("result_json", None), {}
            )
        else:
            item.pop("result_json", None)
        if include_evidence and include_result:
            item["integrity"] = cls._integrity(item)
        return item

    def create_mandate(
        self,
        *,
        user_id: str,
        actor_id: str,
        engine_version: str,
        status: str,
        evidence_cutoff_at: str | None,
        evidence: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        if status not in {
            "active",
            "concentrated",
            "collecting",
            "degraded",
        }:
            raise ValueError("invalid opportunity committee status")
        if not isinstance(evidence, dict) or not isinstance(result, dict):
            raise TypeError("committee evidence and result must be objects")
        evidence_json = canonical_json(evidence)
        result_json = canonical_json(result)
        evidence_sha256 = sha256_text(evidence_json)
        result_sha256 = sha256_text(result_json)
        if result.get("evidence_sha256") != evidence_sha256:
            raise ValueError("committee result is not bound to its evidence")
        mandate_id = f"opp_committee_{uuid.uuid4().hex}"
        created_at = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO opportunity_committee_mandates(
                    id, user_id, actor_id, schema_version,
                    engine_version, status, evidence_cutoff_at,
                    evidence_json, evidence_sha256, result_json,
                    result_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    user_id, engine_version, evidence_sha256
                ) DO NOTHING
                """,
                (
                    mandate_id,
                    user_id,
                    actor_id,
                    MANDATE_SCHEMA_VERSION,
                    engine_version,
                    status,
                    evidence_cutoff_at,
                    evidence_json,
                    evidence_sha256,
                    result_json,
                    result_sha256,
                    created_at,
                ),
            )
            saved = connection.execute(
                """
                SELECT * FROM opportunity_committee_mandates
                WHERE user_id=? AND engine_version=?
                  AND evidence_sha256=?
                """,
                (user_id, engine_version, evidence_sha256),
            ).fetchone()
        item = self._from_row(saved)
        if item is None:
            raise OpportunityCommitteeRepositoryError(
                "委员会指令保存后不可读取"
            )
        return item, item.get("id") == mandate_id

    def get_mandate(
        self, mandate_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM opportunity_committee_mandates
                WHERE id=? AND user_id=?
                """,
                (mandate_id, user_id),
            ).fetchone()
        return self._from_row(row)

    def latest_mandate(
        self, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM opportunity_committee_mandates
                WHERE user_id=?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return self._from_row(row)

    def list_mandates(
        self, *, user_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM opportunity_committee_mandates
                WHERE user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (user_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            item
            for item in (
                self._from_row(row, include_evidence=False)
                for row in rows
            )
            if item is not None
        ]

    def verify_mandate(
        self, mandate_id: str, *, user_id: str
    ) -> dict[str, Any]:
        item = self.get_mandate(mandate_id, user_id=user_id)
        if item is None:
            raise OpportunityCommitteeMandateNotFoundError(
                "策略投资委员会指令不存在"
            )
        return {"mandate_id": mandate_id, **item["integrity"]}


repository = OpportunityCommitteeRepository()
