# -*- coding: utf-8 -*-
"""Durable storage for point-in-time stock-selection research."""

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


RUN_SCHEMA_VERSION = "quant_selection_run.v1"
MANDATE_SCHEMA_VERSION = "quant_selection_shadow_mandate.v1"
TERMINAL_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
REQUIRED_TABLES = {
    "quant_selection_runs",
    "quant_selection_run_events",
    "quant_selection_shadow_mandates",
}


class QuantSelectionRepositoryError(RuntimeError):
    pass


class QuantSelectionNotFoundError(QuantSelectionRepositoryError):
    pass


class QuantSelectionConflictError(QuantSelectionRepositoryError):
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS quant_selection_runs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK(
        status IN ('queued','running','succeeded','partial','failed','cancelled')
    ),
    job_id          TEXT,
    policy_json     TEXT NOT NULL,
    policy_sha256   TEXT NOT NULL,
    progress_json   TEXT NOT NULL,
    result_json     TEXT,
    result_sha256   TEXT,
    error_code      TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_runs_scope
ON quant_selection_runs(tenant_id, user_id, created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS trg_quant_selection_run_input_immutable
BEFORE UPDATE OF
    tenant_id, user_id, actor_id, schema_version, engine_version,
    policy_json, policy_sha256, created_at
ON quant_selection_runs
BEGIN
    SELECT RAISE(ABORT, 'quant selection run input is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_quant_selection_run_result_immutable
BEFORE UPDATE OF result_json, result_sha256 ON quant_selection_runs
WHEN OLD.result_json IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'quant selection run result is immutable');
END;

CREATE TABLE IF NOT EXISTS quant_selection_run_events (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    sequence_no   INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    previous_hash TEXT,
    event_hash    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_run_events
ON quant_selection_run_events(run_id, sequence_no);

CREATE TRIGGER IF NOT EXISTS trg_quant_selection_events_no_update
BEFORE UPDATE ON quant_selection_run_events
BEGIN
    SELECT RAISE(ABORT, 'quant selection run events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_selection_events_no_delete
BEFORE DELETE ON quant_selection_run_events
BEGIN
    SELECT RAISE(ABORT, 'quant selection run events are immutable');
END;

CREATE TABLE IF NOT EXISTS quant_selection_shadow_mandates (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    schema_version  TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    result_sha256   TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_mandates_scope
ON quant_selection_shadow_mandates(
    tenant_id, user_id, created_at DESC, id DESC
);

CREATE TRIGGER IF NOT EXISTS trg_quant_selection_mandates_no_update
BEFORE UPDATE ON quant_selection_shadow_mandates
BEGIN
    SELECT RAISE(ABORT, 'quant selection shadow mandates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_selection_mandates_no_delete
BEFORE DELETE ON quant_selection_shadow_mandates
BEGIN
    SELECT RAISE(ABORT, 'quant selection shadow mandates are immutable');
END;
"""


class QuantSelectionRepository:
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
            FROM quant_selection_run_events
            WHERE run_id=?
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        event = {
            "id": _new_id("qsel_evt"),
            "run_id": str(run_id),
            "sequence_no": int(
                previous["sequence_no"] if previous else 0
            )
            + 1,
            "event_type": str(event_type),
            "actor_id": str(actor_id),
            "details": details or {},
            "previous_hash": (
                str(previous["event_hash"]) if previous else None
            ),
            "created_at": _iso(),
        }
        event_hash = sha256_payload(event)
        connection.execute(
            """
            INSERT INTO quant_selection_run_events(
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
        item["details"] = _load(item.pop("details_json", ""), {})
        return item

    @classmethod
    def _verify_event_chain(
        cls, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        previous_hash = None
        for expected_sequence, event in enumerate(events, 1):
            payload = {
                "id": event["id"],
                "run_id": event["run_id"],
                "sequence_no": int(event["sequence_no"]),
                "event_type": event["event_type"],
                "actor_id": event["actor_id"],
                "details": event.get("details") or {},
                "previous_hash": event.get("previous_hash"),
                "created_at": event["created_at"],
            }
            if (
                int(event["sequence_no"]) != expected_sequence
                or event.get("previous_hash") != previous_hash
                or sha256_payload(payload) != event.get("event_hash")
            ):
                return {
                    "verified": False,
                    "event_count": len(events),
                    "failing_sequence": expected_sequence,
                    "chain_head": previous_hash,
                }
            previous_hash = str(event["event_hash"])
        return {
            "verified": True,
            "event_count": len(events),
            "failing_sequence": None,
            "chain_head": previous_hash,
        }

    @classmethod
    def _run_from_row(
        cls,
        row,
        events: list[dict[str, Any]] | None = None,
        *,
        include_result: bool = True,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        policy_json = str(item.pop("policy_json", ""))
        result_json = item.pop("result_json", None)
        item["policy"] = _load(policy_json, {})
        item["progress"] = _load(item.pop("progress_json", ""), {})
        item["result"] = (
            _load(result_json, None) if include_result else None
        )
        input_verified = bool(
            item.get("schema_version") == RUN_SCHEMA_VERSION
            and sha256_text(policy_json) == item.get("policy_sha256")
        )
        result_verified = (
            bool(
                result_json is not None
                and sha256_text(str(result_json))
                == item.get("result_sha256")
            )
            if include_result
            else None
        )
        item["integrity"] = {
            "input_verified": input_verified,
            "result_verified": result_verified,
            "verified": input_verified
            and (
                bool(result_verified)
                if include_result
                and item.get("status") in {"succeeded", "partial"}
                else True
            ),
        }
        if events is not None:
            item["events"] = events
            item["integrity"]["event_chain"] = cls._verify_event_chain(
                events
            )
            item["integrity"]["verified"] = bool(
                item["integrity"]["verified"]
                and item["integrity"]["event_chain"]["verified"]
            )
        return item

    @staticmethod
    def _mandate_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        snapshot_json = str(item.pop("snapshot_json", ""))
        item["snapshot"] = _load(snapshot_json, {})
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == MANDATE_SCHEMA_VERSION
                and sha256_text(snapshot_json)
                == item.get("snapshot_sha256")
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
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(policy, dict):
            raise TypeError("quant selection policy must be an object")
        policy_json = canonical_json(policy)
        run_id = _new_id("qsel_run")
        progress = {
            "stage": "queued",
            "completed": 0,
            "total": 0,
            "message": "等待历史股票池与行情任务",
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO quant_selection_runs(
                    id, tenant_id, user_id, actor_id, schema_version,
                    engine_version, status, policy_json, policy_sha256,
                    progress_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(tenant_id),
                    str(user_id),
                    str(actor_id),
                    RUN_SCHEMA_VERSION,
                    str(engine_version),
                    policy_json,
                    sha256_text(policy_json),
                    canonical_json(progress),
                    _iso(),
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run.created",
                actor_id,
                {"policy_sha256": sha256_text(policy_json)},
            )
        created = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if created is None:
            raise QuantSelectionRepositoryError(
                "created quant selection run disappeared"
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
                UPDATE quant_selection_runs
                SET job_id=?
                WHERE id=? AND tenant_id=? AND user_id=?
                  AND status='queued' AND job_id IS NULL
                """,
                (job_id, run_id, tenant_id, user_id),
            )
            if cursor.rowcount != 1:
                raise QuantSelectionConflictError(
                    "组合选股实验已经派发或状态已变化"
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
                SELECT status FROM quant_selection_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise QuantSelectionNotFoundError(
                    "组合选股实验不存在"
                )
            if str(row["status"]) in TERMINAL_STATUSES:
                raise QuantSelectionConflictError(
                    "组合选股实验已经结束"
                )
            connection.execute(
                """
                UPDATE quant_selection_runs
                SET status='running', started_at=COALESCE(started_at, ?),
                    progress_json=?
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (
                    _iso(),
                    canonical_json(
                        {
                            "stage": "universe",
                            "completed": 0,
                            "total": 0,
                            "message": "正在冻结历史时点股票池",
                        }
                    ),
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
                UPDATE quant_selection_runs
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
        actor_id: str,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if status not in {"succeeded", "partial"}:
            raise ValueError("invalid quant selection completion status")
        result_json = canonical_json(result)
        result_sha256 = sha256_text(result_json)
        quality = result.get("data_quality") or {}
        progress = {
            "stage": "completed",
            "completed": int(quality.get("loaded_asset_count") or 0),
            "total": int(quality.get("requested_asset_count") or 0),
            "message": (
                "组合选股实验完成"
                if status == "succeeded"
                else "组合选股实验部分完成"
            ),
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, result_json FROM quant_selection_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise QuantSelectionNotFoundError(
                    "组合选股实验不存在"
                )
            if (
                row["result_json"] is not None
                or str(row["status"]) in TERMINAL_STATUSES
            ):
                raise QuantSelectionConflictError(
                    "组合选股实验结果已经冻结"
                )
            connection.execute(
                """
                UPDATE quant_selection_runs
                SET status=?, progress_json=?, result_json=?,
                    result_sha256=?, error_code=NULL,
                    error_message=NULL, completed_at=?
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
        item = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if item is None:
            raise QuantSelectionRepositoryError(
                "completed quant selection run disappeared"
            )
        return item

    def fail_run(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status FROM quant_selection_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise QuantSelectionNotFoundError(
                    "组合选股实验不存在"
                )
            if str(row["status"]) not in TERMINAL_STATUSES:
                connection.execute(
                    """
                    UPDATE quant_selection_runs
                    SET status='failed', progress_json=?,
                        error_code=?, error_message=?, completed_at=?
                    WHERE id=? AND tenant_id=? AND user_id=?
                    """,
                    (
                        canonical_json(
                            {
                                "stage": "failed",
                                "completed": 0,
                                "total": 0,
                                "message": "组合选股实验失败",
                            }
                        ),
                        str(error_code)[:100],
                        str(error_message)[:1000],
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
                    {
                        "error_code": str(error_code)[:100],
                        "error_message": str(error_message)[:500],
                    },
                )
        item = self.get_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if item is None:
            raise QuantSelectionRepositoryError(
                "failed quant selection run disappeared"
            )
        return item

    def get_run(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        include_events: bool = True,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM quant_selection_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            events = None
            if row is not None and include_events:
                events = [
                    self._event_from_row(item)
                    for item in connection.execute(
                        """
                        SELECT * FROM quant_selection_run_events
                        WHERE run_id=?
                        ORDER BY sequence_no
                        """,
                        (run_id,),
                    ).fetchall()
                ]
        return self._run_from_row(row, events)

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
                SELECT
                    id, tenant_id, user_id, actor_id, schema_version,
                    engine_version, status, job_id, policy_json,
                    policy_sha256, progress_json, NULL AS result_json,
                    result_sha256, error_code, error_message,
                    created_at, started_at, completed_at
                FROM quant_selection_runs
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
                    row, include_result=False
                )
            )
            is not None
        ]

    def create_shadow_mandate(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        snapshot_json = canonical_json(snapshot)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run_row = connection.execute(
                """
                SELECT * FROM quant_selection_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            run = self._run_from_row(run_row)
            if run is None:
                raise QuantSelectionNotFoundError(
                    "组合选股实验不存在"
                )
            if run.get("status") not in {"succeeded", "partial"}:
                raise QuantSelectionConflictError(
                    "组合选股实验尚未完成"
                )
            existing = connection.execute(
                """
                SELECT * FROM quant_selection_shadow_mandates
                WHERE tenant_id=? AND user_id=? AND run_id=?
                """,
                (tenant_id, user_id, run_id),
            ).fetchone()
            if existing is not None:
                item = self._mandate_from_row(existing)
                if item is None:
                    raise QuantSelectionRepositoryError(
                        "shadow mandate could not be decoded"
                    )
                return item, False
            mandate_id = _new_id("qsel_shadow")
            connection.execute(
                """
                INSERT INTO quant_selection_shadow_mandates(
                    id, tenant_id, user_id, actor_id, run_id,
                    schema_version, engine_version, result_sha256,
                    snapshot_json, snapshot_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    snapshot_json,
                    sha256_text(snapshot_json),
                    _iso(),
                ),
            )
            self._append_event(
                connection,
                run_id,
                "shadow_mandate.created",
                actor_id,
                {
                    "mandate_id": mandate_id,
                    "snapshot_sha256": sha256_text(snapshot_json),
                },
            )
            saved = connection.execute(
                """
                SELECT * FROM quant_selection_shadow_mandates
                WHERE id=?
                """,
                (mandate_id,),
            ).fetchone()
        item = self._mandate_from_row(saved)
        if item is None:
            raise QuantSelectionRepositoryError(
                "created shadow mandate disappeared"
            )
        return item, True

    def get_shadow_mandate(
        self,
        mandate_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM quant_selection_shadow_mandates
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (mandate_id, tenant_id, user_id),
            ).fetchone()
        return self._mandate_from_row(row)

    def list_shadow_mandates(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM quant_selection_shadow_mandates
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


repository = QuantSelectionRepository()
