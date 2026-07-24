# -*- coding: utf-8 -*-
"""Durable, user-scoped storage for portfolio quant research runs.

Quant inputs are frozen before market-data work starts.  Completed results,
run events, and paper rebalance mandates are content-addressed so a later
change to holdings, policy, or UI code cannot silently rewrite an experiment.
"""

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


RUN_SCHEMA_VERSION = "portfolio_quant_run.v1"
MANDATE_SCHEMA_VERSION = "portfolio_quant_paper_mandate.v1"
TERMINAL_RUN_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
REQUIRED_TABLES = {
    "portfolio_quant_runs",
    "portfolio_quant_run_events",
    "portfolio_quant_mandates",
}


class PortfolioQuantRepositoryError(RuntimeError):
    pass


class PortfolioQuantNotFoundError(PortfolioQuantRepositoryError):
    pass


class PortfolioQuantConflictError(PortfolioQuantRepositoryError):
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_quant_runs (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    schema_version        TEXT NOT NULL,
    engine_version        TEXT NOT NULL,
    status                TEXT NOT NULL CHECK(
        status IN ('queued','running','succeeded','partial','failed','cancelled')
    ),
    job_id                TEXT,
    holdings_sha256       TEXT NOT NULL,
    profile_version_id    TEXT,
    valuation_snapshot_id TEXT,
    policy_json           TEXT NOT NULL,
    policy_sha256         TEXT NOT NULL,
    evidence_json         TEXT NOT NULL,
    evidence_sha256       TEXT NOT NULL,
    progress_json         TEXT NOT NULL,
    result_json           TEXT,
    result_sha256         TEXT,
    error_code            TEXT,
    error_message         TEXT,
    created_at            TEXT NOT NULL,
    started_at            TEXT,
    completed_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_portfolio_quant_runs_scope
ON portfolio_quant_runs(tenant_id, user_id, created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS trg_portfolio_quant_run_input_immutable
BEFORE UPDATE OF
    tenant_id, user_id, actor_id, schema_version, engine_version,
    holdings_sha256, profile_version_id, valuation_snapshot_id,
    policy_json, policy_sha256, evidence_json, evidence_sha256, created_at
ON portfolio_quant_runs
BEGIN
    SELECT RAISE(ABORT, 'portfolio quant run input is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_portfolio_quant_run_result_immutable
BEFORE UPDATE OF result_json, result_sha256 ON portfolio_quant_runs
WHEN OLD.result_json IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'portfolio quant run result is immutable');
END;

CREATE TABLE IF NOT EXISTS portfolio_quant_run_events (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES portfolio_quant_runs(id) ON DELETE RESTRICT,
    sequence_no   INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    previous_hash TEXT,
    event_hash    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_quant_run_events
ON portfolio_quant_run_events(run_id, sequence_no);
CREATE TRIGGER IF NOT EXISTS trg_portfolio_quant_run_events_no_update
BEFORE UPDATE ON portfolio_quant_run_events
BEGIN
    SELECT RAISE(ABORT, 'portfolio quant run events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_quant_run_events_no_delete
BEFORE DELETE ON portfolio_quant_run_events
BEGIN
    SELECT RAISE(ABORT, 'portfolio quant run events are immutable');
END;

CREATE TABLE IF NOT EXISTS portfolio_quant_mandates (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    run_id                TEXT NOT NULL REFERENCES portfolio_quant_runs(id) ON DELETE RESTRICT,
    schema_version        TEXT NOT NULL,
    engine_version        TEXT NOT NULL,
    result_sha256         TEXT NOT NULL,
    holdings_sha256       TEXT NOT NULL,
    profile_version_id    TEXT,
    valuation_snapshot_id TEXT,
    evidence_json         TEXT NOT NULL,
    evidence_sha256       TEXT NOT NULL,
    target_json           TEXT NOT NULL,
    target_sha256         TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_quant_mandates_scope
ON portfolio_quant_mandates(tenant_id, user_id, created_at DESC, id DESC);
CREATE TRIGGER IF NOT EXISTS trg_portfolio_quant_mandates_no_update
BEFORE UPDATE ON portfolio_quant_mandates
BEGIN
    SELECT RAISE(ABORT, 'portfolio quant mandates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_quant_mandates_no_delete
BEFORE DELETE ON portfolio_quant_mandates
BEGIN
    SELECT RAISE(ABORT, 'portfolio quant mandates are immutable');
END;
"""


class PortfolioQuantRepository:
    def __init__(
        self,
        database_target: str | os.PathLike[str] | None = None,
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
    def _append_event(
        connection,
        run_id: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = connection.execute(
            """
            SELECT sequence_no, event_hash
            FROM portfolio_quant_run_events
            WHERE run_id=?
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        event = {
            "id": _new_id("quant_evt"),
            "run_id": str(run_id),
            "sequence_no": sequence_no,
            "event_type": str(event_type),
            "actor_id": str(actor_id),
            "details": details or {},
            "previous_hash": previous["event_hash"] if previous else None,
            "created_at": _iso(),
        }
        event_hash = sha256_payload(event)
        connection.execute(
            """
            INSERT INTO portfolio_quant_run_events(
                id, run_id, sequence_no, event_type, actor_id,
                details_json, previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["run_id"],
                event["sequence_no"],
                event["event_type"],
                event["actor_id"],
                canonical_json(event["details"]),
                event["previous_hash"],
                event_hash,
                event["created_at"],
            ),
        )
        return {**event, "event_hash": event_hash}

    @staticmethod
    def _event_from_row(row) -> dict[str, Any]:
        item = dict(row)
        item["details"] = _load(item.pop("details_json", None), {})
        return item

    @classmethod
    def _run_from_row(
        cls,
        row,
        *,
        include_payload: bool = True,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        policy_json = str(item.pop("policy_json", ""))
        evidence_json = str(item.pop("evidence_json", ""))
        progress_json = str(item.pop("progress_json", ""))
        result_json = item.pop("result_json", None)
        policy_payload = _load(policy_json, {})
        result_payload = _load(result_json, None)
        item["progress"] = _load(progress_json, {})
        item["policy_verified"] = sha256_text(policy_json) == item.get(
            "policy_sha256"
        )
        item["evidence_verified"] = sha256_text(evidence_json) == item.get(
            "evidence_sha256"
        )
        item["result_verified"] = bool(
            result_json is not None
            and sha256_text(str(result_json)) == item.get("result_sha256")
        )
        if include_payload:
            item["policy"] = policy_payload
            item["evidence"] = _load(evidence_json, {})
            item["result"] = result_payload
        else:
            item["policy_summary"] = {
                key: policy_payload.get(key)
                for key in (
                    "construction_method",
                    "lookback_days",
                    "rebalance_days",
                )
            }
            if isinstance(result_payload, dict):
                item["result_summary"] = {
                    "selected_method": result_payload.get(
                        "selected_method"
                    ),
                    "selected_method_label": result_payload.get(
                        "selected_method_label"
                    ),
                    "fold_count": (
                        result_payload.get("walk_forward") or {}
                    ).get("fold_count"),
                    "gate_status": (
                        result_payload.get("promotion_gate") or {}
                    ).get("status"),
                    "paper_mandate_eligible": bool(
                        (
                            result_payload.get("promotion_gate")
                            or {}
                        ).get("paper_mandate_eligible")
                    ),
                }
        if events is not None:
            item["events"] = events
            item["audit"] = cls.verify_event_rows(events)
        item["integrity"] = {
            "verified": bool(
                item["policy_verified"]
                and item["evidence_verified"]
                and (
                    result_json is None
                    or item["result_verified"]
                )
                and (
                    events is None
                    or (item.get("audit") or {}).get("verified")
                )
            ),
            "policy_verified": item["policy_verified"],
            "evidence_verified": item["evidence_verified"],
            "result_verified": item["result_verified"],
            "audit_verified": (
                None
                if events is None
                else bool((item.get("audit") or {}).get("verified"))
            ),
        }
        return item

    @staticmethod
    def verify_event_rows(events: list[dict[str, Any]]) -> dict[str, Any]:
        previous_hash = None
        for expected_sequence, item in enumerate(events, start=1):
            canonical = {
                "id": item.get("id"),
                "run_id": item.get("run_id"),
                "sequence_no": item.get("sequence_no"),
                "event_type": item.get("event_type"),
                "actor_id": item.get("actor_id"),
                "details": item.get("details") or {},
                "previous_hash": item.get("previous_hash"),
                "created_at": item.get("created_at"),
            }
            if (
                int(item.get("sequence_no") or 0) != expected_sequence
                or item.get("previous_hash") != previous_hash
                or item.get("event_hash") != sha256_payload(canonical)
            ):
                return {
                    "verified": False,
                    "event_count": len(events),
                    "failing_sequence": item.get("sequence_no"),
                    "chain_head": previous_hash,
                }
            previous_hash = item.get("event_hash")
        return {
            "verified": bool(events),
            "event_count": len(events),
            "failing_sequence": None,
            "chain_head": previous_hash,
        }

    @staticmethod
    def _mandate_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        evidence_json = str(item.pop("evidence_json", ""))
        target_json = str(item.pop("target_json", ""))
        item["evidence"] = _load(evidence_json, {})
        item["target"] = _load(target_json, {})
        item["integrity"] = {
            "verified": bool(
                sha256_text(evidence_json) == item.get("evidence_sha256")
                and sha256_text(target_json) == item.get("target_sha256")
                and item.get("schema_version") == MANDATE_SCHEMA_VERSION
            )
        }
        return item

    def create_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        engine_version: str,
        holdings_sha256: str,
        profile_version_id: str | None,
        valuation_snapshot_id: str | None,
        policy: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(policy, dict) or not isinstance(evidence, dict):
            raise TypeError("quant policy and evidence must be objects")
        policy_json = canonical_json(policy)
        evidence_json = canonical_json(evidence)
        run_id = _new_id("quant_run")
        now = _iso()
        progress = {
            "stage": "queued",
            "completed": 0,
            "total": len(evidence.get("eligible_holdings") or []),
            "message": "等待量化组合实验",
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO portfolio_quant_runs(
                    id, tenant_id, user_id, actor_id, schema_version,
                    engine_version, status, holdings_sha256,
                    profile_version_id, valuation_snapshot_id,
                    policy_json, policy_sha256, evidence_json,
                    evidence_sha256, progress_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(tenant_id),
                    str(user_id),
                    str(actor_id),
                    RUN_SCHEMA_VERSION,
                    str(engine_version),
                    str(holdings_sha256),
                    profile_version_id,
                    valuation_snapshot_id,
                    policy_json,
                    sha256_text(policy_json),
                    evidence_json,
                    sha256_text(evidence_json),
                    canonical_json(progress),
                    now,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run.created",
                actor_id,
                {
                    "holdings_sha256": holdings_sha256,
                    "policy_sha256": sha256_text(policy_json),
                    "evidence_sha256": sha256_text(evidence_json),
                },
            )
        created = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if created is None:
            raise PortfolioQuantRepositoryError(
                "created quant run disappeared"
            )
        return created

    def bind_job(
        self,
        run_id: str,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE portfolio_quant_runs
                SET job_id=?
                WHERE id=? AND tenant_id=? AND user_id=?
                  AND status='queued' AND job_id IS NULL
                """,
                (job_id, run_id, tenant_id, user_id),
            )
            if cursor.rowcount != 1:
                raise PortfolioQuantConflictError(
                    "量化实验已经派发或状态已变化"
                )

    def mark_running(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status FROM portfolio_quant_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise PortfolioQuantNotFoundError("量化实验不存在")
            if str(row["status"]) in TERMINAL_RUN_STATUSES:
                raise PortfolioQuantConflictError("量化实验已经结束")
            progress = {
                "stage": "market_data",
                "completed": 0,
                "total": 0,
                "message": "正在读取冻结股票池的真实复权日线",
            }
            connection.execute(
                """
                UPDATE portfolio_quant_runs
                SET status='running', started_at=COALESCE(started_at, ?),
                    progress_json=?
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (
                    _iso(),
                    canonical_json(progress),
                    run_id,
                    tenant_id,
                    user_id,
                ),
            )
            self._append_event(
                connection, run_id, "run.started", actor_id
            )

    def update_progress(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        progress: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE portfolio_quant_runs
                SET progress_json=?
                WHERE id=? AND tenant_id=? AND user_id=?
                  AND status IN ('queued','running')
                """,
                (
                    canonical_json(progress),
                    run_id,
                    tenant_id,
                    user_id,
                ),
            )

    def complete_run(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        result: dict[str, Any],
        status: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "partial"}:
            raise ValueError("invalid quant run completion status")
        result_json = canonical_json(result)
        result_sha256 = sha256_text(result_json)
        progress = {
            "stage": "completed",
            "completed": int(
                (result.get("data_quality") or {}).get(
                    "eligible_asset_count"
                )
                or 0
            ),
            "total": int(
                (result.get("data_quality") or {}).get(
                    "requested_asset_count"
                )
                or 0
            ),
            "message": (
                "量化组合实验完成"
                if status == "succeeded"
                else "量化组合实验部分完成"
            ),
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, result_json
                FROM portfolio_quant_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise PortfolioQuantNotFoundError("量化实验不存在")
            if (
                row["result_json"] is not None
                or str(row["status"]) in TERMINAL_RUN_STATUSES
            ):
                raise PortfolioQuantConflictError("量化实验结果已经冻结")
            connection.execute(
                """
                UPDATE portfolio_quant_runs
                SET status=?, progress_json=?, result_json=?,
                    result_sha256=?, completed_at=?,
                    error_code=NULL, error_message=NULL
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (
                    status,
                    canonical_json(progress),
                    result_json,
                    result_sha256,
                    _iso(),
                    run_id,
                    tenant_id,
                    user_id,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run.completed",
                actor_id,
                {
                    "status": status,
                    "result_sha256": result_sha256,
                    "gate_status": (
                        result.get("promotion_gate") or {}
                    ).get("status"),
                },
            )
        completed = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if completed is None:
            raise PortfolioQuantRepositoryError(
                "completed quant run disappeared"
            )
        return completed

    def fail_run(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        error_code: str,
        error_message: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status FROM portfolio_quant_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise PortfolioQuantNotFoundError("量化实验不存在")
            if str(row["status"]) not in TERMINAL_RUN_STATUSES:
                progress = {
                    "stage": "failed",
                    "completed": 0,
                    "total": 0,
                    "message": "量化组合实验失败",
                }
                connection.execute(
                    """
                    UPDATE portfolio_quant_runs
                    SET status='failed', progress_json=?, error_code=?,
                        error_message=?, completed_at=?
                    WHERE id=? AND tenant_id=? AND user_id=?
                    """,
                    (
                        canonical_json(progress),
                        str(error_code)[:80],
                        str(error_message)[:500],
                        _iso(),
                        run_id,
                        tenant_id,
                        user_id,
                    ),
                )
                self._append_event(
                    connection,
                    run_id,
                    "run.failed",
                    actor_id,
                    {"error_code": str(error_code)[:80]},
                )
        failed = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if failed is None:
            raise PortfolioQuantRepositoryError(
                "failed quant run disappeared"
            )
        return failed

    def get_run(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        include_payload: bool = True,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_quant_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                return None
            event_rows = connection.execute(
                """
                SELECT * FROM portfolio_quant_run_events
                WHERE run_id=? ORDER BY sequence_no
                """,
                (run_id,),
            ).fetchall()
        events = [self._event_from_row(item) for item in event_rows]
        return self._run_from_row(
            row,
            include_payload=include_payload,
            events=events,
        )

    def list_runs(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM portfolio_quant_runs
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (
                    tenant_id,
                    user_id,
                    max(1, min(100, int(limit))),
                ),
            ).fetchall()
        return [
            item
            for row in rows
            if (
                item := self._run_from_row(
                    row,
                    include_payload=False,
                    events=None,
                )
            )
            is not None
        ]

    def create_mandate(
        self,
        *,
        run_id: str,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        evidence: dict[str, Any],
        target: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        run = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if run is None:
            raise PortfolioQuantNotFoundError("量化实验不存在")
        if (
            run.get("status") not in {"succeeded", "partial"}
            or not run.get("result_verified")
            or not run.get("result_sha256")
        ):
            raise PortfolioQuantConflictError(
                "量化实验尚无可冻结的完整结果"
            )
        evidence_json = canonical_json(evidence)
        target_json = canonical_json(target)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM portfolio_quant_mandates
                WHERE tenant_id=? AND user_id=? AND run_id=?
                """,
                (tenant_id, user_id, run_id),
            ).fetchone()
            if existing is not None:
                item = self._mandate_from_row(existing)
                if item is None:
                    raise PortfolioQuantRepositoryError(
                        "existing quant mandate disappeared"
                    )
                return item, False
            mandate_id = _new_id("quant_mandate")
            connection.execute(
                """
                INSERT INTO portfolio_quant_mandates(
                    id, tenant_id, user_id, actor_id, run_id,
                    schema_version, engine_version, result_sha256,
                    holdings_sha256, profile_version_id,
                    valuation_snapshot_id, evidence_json,
                    evidence_sha256, target_json, target_sha256,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mandate_id,
                    tenant_id,
                    user_id,
                    actor_id,
                    run_id,
                    MANDATE_SCHEMA_VERSION,
                    run.get("engine_version"),
                    run.get("result_sha256"),
                    run.get("holdings_sha256"),
                    run.get("profile_version_id"),
                    run.get("valuation_snapshot_id"),
                    evidence_json,
                    sha256_text(evidence_json),
                    target_json,
                    sha256_text(target_json),
                    _iso(),
                ),
            )
            saved = connection.execute(
                "SELECT * FROM portfolio_quant_mandates WHERE id=?",
                (mandate_id,),
            ).fetchone()
        item = self._mandate_from_row(saved)
        if item is None:
            raise PortfolioQuantRepositoryError(
                "created quant mandate disappeared"
            )
        return item, True

    def get_mandate(
        self,
        mandate_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_quant_mandates
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (mandate_id, tenant_id, user_id),
            ).fetchone()
        return self._mandate_from_row(row)

    def list_mandates(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM portfolio_quant_mandates
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (
                    tenant_id,
                    user_id,
                    max(1, min(100, int(limit))),
                ),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._mandate_from_row(row)) is not None
        ]


repository = PortfolioQuantRepository()
