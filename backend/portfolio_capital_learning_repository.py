# -*- coding: utf-8 -*-
"""Immutable persistence for capital-plan execution and outcome learning."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from database import (
    INTEGRITY_ERRORS,
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
)
from portfolio_capital_repository import (
    canonical_json,
    sha256_payload,
    sha256_text,
)


EXECUTION_SCHEMA_VERSION = "portfolio_capital_execution_event.v1"
OUTCOME_SCHEMA_VERSION = "portfolio_capital_outcome_snapshot.v1"
ENGINE_VERSION = "capital_plan_execution_learning@1.0.0"
REQUIRED_TABLES = {
    "portfolio_capital_decision_plans",
    "portfolio_capital_execution_events",
    "portfolio_capital_transaction_bindings",
    "portfolio_capital_outcome_snapshots",
}


class PortfolioCapitalLearningError(RuntimeError):
    pass


class PortfolioCapitalLearningConflictError(
    PortfolioCapitalLearningError
):
    pass


class PortfolioCapitalExecutionNotFoundError(
    PortfolioCapitalLearningError
):
    pass


class PortfolioCapitalOutcomeNotFoundError(
    PortfolioCapitalLearningError
):
    pass


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
CREATE TABLE IF NOT EXISTS portfolio_capital_execution_events (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    actor_id                TEXT NOT NULL,
    plan_id                 TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    event_no                INTEGER NOT NULL CHECK(event_no > 0),
    previous_event_hash     TEXT,
    status                  TEXT NOT NULL CHECK(status IN ('partial','reconciled','deviated','reviewed')),
    transaction_count       INTEGER NOT NULL CHECK(transaction_count > 0),
    planned_amount_cny      REAL NOT NULL CHECK(planned_amount_cny >= 0),
    settled_amount_cny      REAL NOT NULL CHECK(settled_amount_cny > 0),
    evidence_json           TEXT NOT NULL,
    evidence_sha256         TEXT NOT NULL,
    result_json             TEXT NOT NULL,
    result_sha256           TEXT NOT NULL,
    event_hash              TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES portfolio_capital_decision_plans(id),
    UNIQUE(tenant_id, user_id, plan_id, event_no),
    UNIQUE(tenant_id, user_id, plan_id, evidence_sha256)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_execution_scope
ON portfolio_capital_execution_events(
    tenant_id, user_id, plan_id, event_no DESC
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_execution_due
ON portfolio_capital_execution_events(
    created_at DESC, tenant_id, user_id, plan_id
);

CREATE TABLE IF NOT EXISTS portfolio_capital_transaction_bindings (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    plan_id                 TEXT NOT NULL,
    first_event_id          TEXT NOT NULL,
    transaction_id          INTEGER NOT NULL,
    transaction_sha256      TEXT NOT NULL,
    settled_amount_cny      REAL NOT NULL CHECK(settled_amount_cny > 0),
    created_at              TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES portfolio_capital_decision_plans(id),
    FOREIGN KEY(first_event_id) REFERENCES portfolio_capital_execution_events(id),
    UNIQUE(tenant_id, user_id, transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_binding_plan
ON portfolio_capital_transaction_bindings(
    tenant_id, user_id, plan_id, transaction_id
);

CREATE TABLE IF NOT EXISTS portfolio_capital_outcome_snapshots (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    actor_id                TEXT NOT NULL,
    plan_id                 TEXT NOT NULL,
    execution_event_id      TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    observed_at             TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK(status IN ('collecting','partial','complete')),
    evidence_json           TEXT NOT NULL,
    evidence_sha256         TEXT NOT NULL,
    result_json             TEXT NOT NULL,
    result_sha256           TEXT NOT NULL,
    idempotency_key         TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES portfolio_capital_decision_plans(id),
    FOREIGN KEY(execution_event_id) REFERENCES portfolio_capital_execution_events(id),
    UNIQUE(tenant_id, user_id, execution_event_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_outcome_scope
ON portfolio_capital_outcome_snapshots(
    tenant_id, user_id, plan_id, observed_at DESC, id DESC
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_outcome_event
ON portfolio_capital_outcome_snapshots(
    execution_event_id, observed_at DESC, id DESC
);

CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_execution_no_update
BEFORE UPDATE ON portfolio_capital_execution_events BEGIN
    SELECT RAISE(ABORT, 'portfolio capital execution events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_execution_no_delete
BEFORE DELETE ON portfolio_capital_execution_events BEGIN
    SELECT RAISE(ABORT, 'portfolio capital execution events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_bindings_no_update
BEFORE UPDATE ON portfolio_capital_transaction_bindings BEGIN
    SELECT RAISE(ABORT, 'portfolio capital transaction bindings are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_bindings_no_delete
BEFORE DELETE ON portfolio_capital_transaction_bindings BEGIN
    SELECT RAISE(ABORT, 'portfolio capital transaction bindings are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_outcomes_no_update
BEFORE UPDATE ON portfolio_capital_outcome_snapshots BEGIN
    SELECT RAISE(ABORT, 'portfolio capital outcome snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_capital_outcomes_no_delete
BEFORE DELETE ON portfolio_capital_outcome_snapshots BEGIN
    SELECT RAISE(ABORT, 'portfolio capital outcome snapshots are immutable');
END;
"""


class PortfolioCapitalLearningRepository:
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
    def _execution_hash_payload(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "tenant_id": item.get("tenant_id"),
            "user_id": item.get("user_id"),
            "plan_id": item.get("plan_id"),
            "event_no": item.get("event_no"),
            "previous_event_hash": item.get("previous_event_hash"),
            "evidence_sha256": item.get("evidence_sha256"),
            "result_sha256": item.get("result_sha256"),
            "created_at": item.get("created_at"),
        }

    @classmethod
    def _execution_integrity(
        cls, item: dict[str, Any]
    ) -> dict[str, Any]:
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
            item.get("schema_version") == EXECUTION_SCHEMA_VERSION
            and item.get("engine_version") == ENGINE_VERSION
            and (item.get("result") or {}).get("schema_version")
            == "portfolio_capital_execution_reconciliation.v1"
        )
        event_hash_verified = (
            sha256_payload(cls._execution_hash_payload(item))
            == item.get("event_hash")
        )
        return {
            "verified": bool(
                evidence_verified
                and result_verified
                and schema_verified
                and event_hash_verified
            ),
            "evidence_verified": evidence_verified,
            "result_verified": result_verified,
            "schema_verified": schema_verified,
            "event_hash_verified": event_hash_verified,
        }

    @staticmethod
    def _outcome_integrity(item: dict[str, Any]) -> dict[str, Any]:
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
            item.get("schema_version") == OUTCOME_SCHEMA_VERSION
            and item.get("engine_version") == ENGINE_VERSION
            and (item.get("result") or {}).get("schema_version")
            == "portfolio_capital_outcome_learning.v1"
        )
        return {
            "verified": bool(
                evidence_verified and result_verified and schema_verified
            ),
            "evidence_verified": evidence_verified,
            "result_verified": result_verified,
            "schema_verified": schema_verified,
        }

    @classmethod
    def _execution_from_row(
        cls, row: Any
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["evidence"] = _load(item.pop("evidence_json", None), {})
        item["result"] = _load(item.pop("result_json", None), {})
        item["integrity"] = cls._execution_integrity(item)
        return item

    @classmethod
    def _outcome_from_row(
        cls, row: Any
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["evidence"] = _load(item.pop("evidence_json", None), {})
        item["result"] = _load(item.pop("result_json", None), {})
        item["integrity"] = cls._outcome_integrity(item)
        return item

    def create_execution_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        plan_id: str,
        status: str,
        planned_amount_cny: float,
        settled_amount_cny: float,
        evidence: dict[str, Any],
        result: dict[str, Any],
        transaction_bindings: list[dict[str, Any]],
        expected_previous_event_hash: str | None,
        now: dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if status not in {
            "partial",
            "reconciled",
            "deviated",
            "reviewed",
        }:
            raise ValueError("invalid capital execution status")
        if not transaction_bindings:
            raise ValueError("capital execution requires transactions")
        evidence_json = canonical_json(evidence)
        result_json = canonical_json(result)
        evidence_sha256 = sha256_text(evidence_json)
        result_sha256 = sha256_text(result_json)
        created_at = _iso(now)
        event_id = f"capital_execution_{uuid.uuid4().hex}"

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                latest_row = connection.execute(
                    """
                    SELECT * FROM portfolio_capital_execution_events
                    WHERE tenant_id=? AND user_id=? AND plan_id=?
                    ORDER BY event_no DESC LIMIT 1
                    """,
                    (tenant_id, user_id, plan_id),
                ).fetchone()
                latest = self._execution_from_row(latest_row)
                previous_hash = (
                    str(latest.get("event_hash")) if latest else None
                )
                existing_row = connection.execute(
                    """
                    SELECT * FROM portfolio_capital_execution_events
                    WHERE tenant_id=? AND user_id=? AND plan_id=?
                      AND evidence_sha256=?
                    """,
                    (
                        tenant_id,
                        user_id,
                        plan_id,
                        evidence_sha256,
                    ),
                ).fetchone()
                if existing_row is not None:
                    existing = self._execution_from_row(existing_row)
                    if existing is None:
                        raise PortfolioCapitalLearningError(
                            "执行记录幂等读取失败"
                        )
                    return existing, False
                if previous_hash != expected_previous_event_hash:
                    raise PortfolioCapitalLearningConflictError(
                        "执行记录已变化，请刷新后再提交"
                    )

                transaction_ids = [
                    int(item["transaction_id"])
                    for item in transaction_bindings
                ]
                placeholders = ",".join("?" for _ in transaction_ids)
                rows = connection.execute(
                    f"""
                    SELECT transaction_id, plan_id, transaction_sha256,
                           settled_amount_cny
                    FROM portfolio_capital_transaction_bindings
                    WHERE tenant_id=? AND user_id=?
                      AND transaction_id IN ({placeholders})
                    """,
                    (tenant_id, user_id, *transaction_ids),
                ).fetchall()
                existing_bindings = {
                    int(row["transaction_id"]): dict(row) for row in rows
                }
                for binding in transaction_bindings:
                    transaction_id = int(binding["transaction_id"])
                    existing_binding = existing_bindings.get(transaction_id)
                    if not existing_binding:
                        continue
                    if existing_binding["plan_id"] != plan_id:
                        raise PortfolioCapitalLearningConflictError(
                            f"交易 {transaction_id} 已绑定其他资本计划"
                        )
                    if (
                        existing_binding["transaction_sha256"]
                        != binding["transaction_sha256"]
                        or round(
                            float(
                                existing_binding["settled_amount_cny"]
                            ),
                            2,
                        )
                        != round(
                            float(binding["settled_amount_cny"]), 2
                        )
                    ):
                        raise PortfolioCapitalLearningConflictError(
                            f"交易 {transaction_id} 的既有确认信息不可改写"
                        )

                event_no = int((latest or {}).get("event_no") or 0) + 1
                event_shell = {
                    "id": event_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "event_no": event_no,
                    "previous_event_hash": previous_hash,
                    "evidence_sha256": evidence_sha256,
                    "result_sha256": result_sha256,
                    "created_at": created_at,
                }
                event_hash = sha256_payload(event_shell)
                connection.execute(
                    """
                    INSERT INTO portfolio_capital_execution_events(
                        id, tenant_id, user_id, actor_id, plan_id,
                        schema_version, engine_version, event_no,
                        previous_event_hash, status, transaction_count,
                        planned_amount_cny, settled_amount_cny,
                        evidence_json, evidence_sha256, result_json,
                        result_sha256, event_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        tenant_id,
                        user_id,
                        actor_id,
                        plan_id,
                        EXECUTION_SCHEMA_VERSION,
                        ENGINE_VERSION,
                        event_no,
                        previous_hash,
                        status,
                        len(transaction_bindings),
                        round(float(planned_amount_cny), 2),
                        round(float(settled_amount_cny), 2),
                        evidence_json,
                        evidence_sha256,
                        result_json,
                        result_sha256,
                        event_hash,
                        created_at,
                    ),
                )
                for binding in transaction_bindings:
                    transaction_id = int(binding["transaction_id"])
                    if transaction_id in existing_bindings:
                        continue
                    connection.execute(
                        """
                        INSERT INTO portfolio_capital_transaction_bindings(
                            id, tenant_id, user_id, plan_id,
                            first_event_id, transaction_id,
                            transaction_sha256, settled_amount_cny,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"capital_binding_{uuid.uuid4().hex}",
                            tenant_id,
                            user_id,
                            plan_id,
                            event_id,
                            transaction_id,
                            binding["transaction_sha256"],
                            round(
                                float(binding["settled_amount_cny"]), 2
                            ),
                            created_at,
                        ),
                    )
                saved_row = connection.execute(
                    """
                    SELECT * FROM portfolio_capital_execution_events
                    WHERE id=?
                    """,
                    (event_id,),
                ).fetchone()
        except PortfolioCapitalLearningConflictError:
            raise
        except INTEGRITY_ERRORS as error:
            raise PortfolioCapitalLearningConflictError(
                "执行记录与现有审计链冲突，请刷新后重试"
            ) from error

        saved = self._execution_from_row(saved_row)
        if saved is None:
            raise PortfolioCapitalLearningError(
                "执行记录保存后不可读取"
            )
        return saved, True

    def get_execution_event(
        self,
        event_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_capital_execution_events
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (event_id, tenant_id, user_id),
            ).fetchone()
        return self._execution_from_row(row)

    def latest_execution(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_capital_execution_events
                WHERE plan_id=? AND tenant_id=? AND user_id=?
                ORDER BY event_no DESC LIMIT 1
                """,
                (plan_id, tenant_id, user_id),
            ).fetchone()
        return self._execution_from_row(row)

    def list_executions(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM portfolio_capital_execution_events
                WHERE plan_id=? AND tenant_id=? AND user_id=?
                ORDER BY event_no DESC
                """,
                (plan_id, tenant_id, user_id),
            ).fetchall()
        return [
            item
            for item in (
                self._execution_from_row(row) for row in rows
            )
            if item is not None
        ]

    def list_execution_scopes(
        self, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event.*
                FROM portfolio_capital_execution_events AS event
                JOIN (
                    SELECT tenant_id, user_id, plan_id, MAX(event_no) AS event_no
                    FROM portfolio_capital_execution_events
                    GROUP BY tenant_id, user_id, plan_id
                ) AS latest
                  ON latest.tenant_id=event.tenant_id
                 AND latest.user_id=event.user_id
                 AND latest.plan_id=event.plan_id
                 AND latest.event_no=event.event_no
                ORDER BY event.created_at DESC
                LIMIT ?
                """,
                (max(1, min(5000, int(limit))),),
            ).fetchall()
        return [
            item
            for item in (
                self._execution_from_row(row) for row in rows
            )
            if item is not None
        ]

    def list_bindings(
        self,
        *,
        tenant_id: str,
        user_id: str,
        plan_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM portfolio_capital_transaction_bindings
            WHERE tenant_id=? AND user_id=?
        """
        parameters: list[Any] = [tenant_id, user_id]
        if plan_id:
            query += " AND plan_id=?"
            parameters.append(plan_id)
        query += " ORDER BY transaction_id ASC"
        with self._connect() as connection:
            rows = connection.execute(
                query, tuple(parameters)
            ).fetchall()
        return [dict(row) for row in rows]

    def create_outcome_snapshot(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        plan_id: str,
        execution_event_id: str,
        observed_at: str,
        status: str,
        evidence: dict[str, Any],
        result: dict[str, Any],
        idempotency_key: str,
        now: dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if status not in {"collecting", "partial", "complete"}:
            raise ValueError("invalid capital outcome status")
        evidence_json = canonical_json(evidence)
        result_json = canonical_json(result)
        evidence_sha256 = sha256_text(evidence_json)
        result_sha256 = sha256_text(result_json)
        snapshot_id = f"capital_outcome_{uuid.uuid4().hex}"
        created_at = _iso(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing_row = connection.execute(
                    """
                    SELECT * FROM portfolio_capital_outcome_snapshots
                    WHERE tenant_id=? AND user_id=?
                      AND execution_event_id=? AND idempotency_key=?
                    """,
                    (
                        tenant_id,
                        user_id,
                        execution_event_id,
                        idempotency_key,
                    ),
                ).fetchone()
                if existing_row is not None:
                    existing = self._outcome_from_row(existing_row)
                    if existing is None:
                        raise PortfolioCapitalLearningError(
                            "结果观察幂等读取失败"
                        )
                    return existing, False
                connection.execute(
                    """
                    INSERT INTO portfolio_capital_outcome_snapshots(
                        id, tenant_id, user_id, actor_id, plan_id,
                        execution_event_id, schema_version,
                        engine_version, observed_at, status,
                        evidence_json, evidence_sha256, result_json,
                        result_sha256, idempotency_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        tenant_id,
                        user_id,
                        actor_id,
                        plan_id,
                        execution_event_id,
                        OUTCOME_SCHEMA_VERSION,
                        ENGINE_VERSION,
                        observed_at,
                        status,
                        evidence_json,
                        evidence_sha256,
                        result_json,
                        result_sha256,
                        idempotency_key,
                        created_at,
                    ),
                )
                saved_row = connection.execute(
                    """
                    SELECT * FROM portfolio_capital_outcome_snapshots
                    WHERE id=?
                    """,
                    (snapshot_id,),
                ).fetchone()
        except INTEGRITY_ERRORS as error:
            raise PortfolioCapitalLearningConflictError(
                "结果观察与现有审计记录冲突"
            ) from error
        saved = self._outcome_from_row(saved_row)
        if saved is None:
            raise PortfolioCapitalLearningError(
                "结果观察保存后不可读取"
            )
        return saved, True

    def get_outcome(
        self,
        outcome_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_capital_outcome_snapshots
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (outcome_id, tenant_id, user_id),
            ).fetchone()
        return self._outcome_from_row(row)

    def latest_outcome(
        self,
        execution_event_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_capital_outcome_snapshots
                WHERE execution_event_id=? AND tenant_id=? AND user_id=?
                ORDER BY observed_at DESC, id DESC LIMIT 1
                """,
                (execution_event_id, tenant_id, user_id),
            ).fetchone()
        return self._outcome_from_row(row)

    def list_outcomes(
        self,
        *,
        tenant_id: str,
        user_id: str,
        plan_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM portfolio_capital_outcome_snapshots
            WHERE tenant_id=? AND user_id=?
        """
        parameters: list[Any] = [tenant_id, user_id]
        if plan_id:
            query += " AND plan_id=?"
            parameters.append(plan_id)
        query += " ORDER BY observed_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(1000, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                query, tuple(parameters)
            ).fetchall()
        return [
            item
            for item in (
                self._outcome_from_row(row) for row in rows
            )
            if item is not None
        ]


repository = PortfolioCapitalLearningRepository()
