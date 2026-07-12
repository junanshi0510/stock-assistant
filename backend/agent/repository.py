# -*- coding: utf-8 -*-
"""Durable SQLite repository for the first agent workflow slice.

The schema mirrors the target PostgreSQL entities from the PRD. SQLite remains
the current deployment store; the repository boundary keeps the runtime logic
independent so the next migration can replace persistence without changing the
tool or workflow contracts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any


RUN_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "abstained"}
STEP_REUSABLE_STATUSES = {"succeeded", "partial"}


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite's context manager, then always close."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _default_db_path() -> str:
    configured = os.getenv("AGENT_DB_PATH") or os.getenv("STOCK_ASSISTANT_DB_PATH")
    if configured:
        return configured
    return str(Path(__file__).resolve().parents[1] / "stock_assistant.db")


class AgentRepository:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = str(db_path or _default_db_path())
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30,
            check_same_thread=False,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            path = Path(self.db_path)
            if self.db_path != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS agent_runs (
                        id                TEXT PRIMARY KEY,
                        tenant_id         TEXT NOT NULL,
                        user_id           TEXT NOT NULL,
                        intent            TEXT NOT NULL,
                        input_json        TEXT NOT NULL,
                        input_hash        TEXT NOT NULL,
                        idempotency_key   TEXT,
                        status            TEXT NOT NULL,
                        cancel_requested  INTEGER NOT NULL DEFAULT 0,
                        result_json       TEXT,
                        error_code        TEXT,
                        error_message     TEXT,
                        worker_id         TEXT,
                        created_at        TEXT NOT NULL,
                        updated_at        TEXT NOT NULL,
                        started_at        TEXT,
                        completed_at      TEXT,
                        parent_run_id     TEXT,
                        UNIQUE(user_id, idempotency_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_runs_queue
                    ON agent_runs(status, created_at);

                    CREATE INDEX IF NOT EXISTS idx_agent_runs_input
                    ON agent_runs(user_id, input_hash, status);

                    CREATE TABLE IF NOT EXISTS agent_steps (
                        id             TEXT PRIMARY KEY,
                        run_id         TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                        step_key       TEXT NOT NULL,
                        sequence_no    INTEGER NOT NULL,
                        tool_name      TEXT NOT NULL,
                        tool_version   TEXT NOT NULL,
                        required       INTEGER NOT NULL,
                        status         TEXT NOT NULL,
                        input_json     TEXT NOT NULL,
                        output_json    TEXT,
                        evidence_id    TEXT,
                        error_code     TEXT,
                        error_message  TEXT,
                        started_at     TEXT,
                        completed_at   TEXT,
                        UNIQUE(run_id, step_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_steps_run
                    ON agent_steps(run_id, sequence_no);

                    CREATE TABLE IF NOT EXISTS agent_evidence (
                        id              TEXT PRIMARY KEY,
                        run_id          TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                        step_id         TEXT REFERENCES agent_steps(id) ON DELETE SET NULL,
                        evidence_type   TEXT NOT NULL,
                        subject_type    TEXT NOT NULL,
                        subject_id      TEXT NOT NULL,
                        provider        TEXT NOT NULL,
                        source_url      TEXT,
                        observed_at     TEXT NOT NULL,
                        as_of           TEXT,
                        schema_version  TEXT NOT NULL,
                        quality_status  TEXT NOT NULL,
                        payload_json    TEXT NOT NULL,
                        payload_sha256  TEXT NOT NULL,
                        created_at      TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_evidence_run
                    ON agent_evidence(run_id, created_at);

                    CREATE TABLE IF NOT EXISTS agent_claims (
                        id            TEXT PRIMARY KEY,
                        run_id        TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                        claim_key     TEXT NOT NULL,
                        claim_type    TEXT NOT NULL,
                        claim_text    TEXT NOT NULL,
                        value_json    TEXT,
                        evidence_id   TEXT NOT NULL REFERENCES agent_evidence(id),
                        created_at    TEXT NOT NULL,
                        UNIQUE(run_id, claim_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_claims_run
                    ON agent_claims(run_id, created_at);

                    CREATE TABLE IF NOT EXISTS agent_audit_events (
                        id             TEXT PRIMARY KEY,
                        run_id         TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                        sequence_no    INTEGER NOT NULL,
                        event_type     TEXT NOT NULL,
                        actor_type     TEXT NOT NULL,
                        actor_id       TEXT NOT NULL,
                        details_json   TEXT NOT NULL,
                        previous_hash  TEXT,
                        event_hash     TEXT NOT NULL,
                        created_at     TEXT NOT NULL
                    );

                    """
                )
                audit_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(agent_audit_events)").fetchall()
                }
                if "sequence_no" not in audit_columns:
                    connection.execute(
                        "ALTER TABLE agent_audit_events ADD COLUMN sequence_no INTEGER NOT NULL DEFAULT 0"
                    )
                    connection.execute(
                        "UPDATE agent_audit_events SET sequence_no=rowid WHERE sequence_no=0"
                    )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_audit_sequence
                    ON agent_audit_events(run_id, sequence_no)
                    """
                )
            self._schema_ready = True

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        *,
        actor_type: str = "system",
        actor_id: str = "agent-runtime-v1",
    ) -> dict[str, Any]:
        previous = connection.execute(
            """
            SELECT event_hash, sequence_no
            FROM agent_audit_events
            WHERE run_id=?
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        event_id = _new_id("audit")
        created_at = _utc_now()
        previous_hash = previous["event_hash"] if previous else None
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        canonical = {
            "id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "sequence_no": sequence_no,
            "details": details or {},
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        event_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO agent_audit_events (
                id, run_id, sequence_no, event_type, actor_type, actor_id, details_json,
                previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                sequence_no,
                event_type,
                actor_type,
                actor_id,
                _json(details or {}),
                previous_hash,
                event_hash,
                created_at,
            ),
        )
        return {**canonical, "event_hash": event_hash}

    @staticmethod
    def _run_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["input"] = _load(item.pop("input_json", None), {})
        item["result"] = _load(item.pop("result_json", None), None)
        item["cancel_requested"] = bool(item.get("cancel_requested"))
        return item

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["input"] = _load(item.pop("input_json", None), {})
        item["output"] = _load(item.pop("output_json", None), None)
        item["required"] = bool(item.get("required"))
        return item

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row, *, include_payload: bool = False) -> dict[str, Any]:
        item = dict(row)
        raw_payload = item.pop("payload_json", None)
        if include_payload:
            payload = _load(raw_payload, {})
            item["payload"] = payload
            item["integrity_verified"] = (
                hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
                == item.get("payload_sha256")
            )
        return item

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["value"] = _load(item.pop("value_json", None), None)
        return item

    def create_run(
        self,
        intent: str,
        input_payload: dict[str, Any],
        *,
        tenant_id: str = "public",
        user_id: str = "anonymous",
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        normalized_input = _json(input_payload)
        input_hash = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM agent_runs WHERE user_id=? AND idempotency_key=?",
                    (user_id, idempotency_key),
                ).fetchone()
                if existing:
                    return self._run_from_row(existing), False
            existing = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE user_id=? AND intent=? AND input_hash=?
                  AND status IN ('queued', 'running')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, intent, input_hash),
            ).fetchone()
            if existing:
                return self._run_from_row(existing), False

            run_id = _new_id("run")
            connection.execute(
                """
                INSERT INTO agent_runs (
                    id, tenant_id, user_id, intent, input_json, input_hash,
                    idempotency_key, status, cancel_requested, created_at,
                    updated_at, parent_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (
                    run_id,
                    tenant_id,
                    user_id,
                    intent,
                    normalized_input,
                    input_hash,
                    idempotency_key,
                    now,
                    now,
                    parent_run_id,
                ),
            )
            self._append_audit(
                connection,
                run_id,
                "run.created",
                {"intent": intent, "input_hash": input_hash, "status": "queued"},
                actor_type="user",
                actor_id=user_id,
            )
            row = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            return self._run_from_row(row), True

    def get_run(self, run_id: str, *, include_details: bool = True) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            run = self._run_from_row(row)
            if run is None or not include_details:
                return run
            step_rows = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id=? ORDER BY sequence_no, id",
                (run_id,),
            ).fetchall()
            claim_rows = connection.execute(
                "SELECT * FROM agent_claims WHERE run_id=? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT id, run_id, step_id, evidence_type, subject_type, subject_id,
                       provider, source_url, observed_at, as_of, schema_version,
                       quality_status, payload_sha256, created_at
                FROM agent_evidence
                WHERE run_id=?
                ORDER BY created_at, id
                """,
                (run_id,),
            ).fetchall()
        run["steps"] = [self._step_from_row(item) for item in step_rows]
        run["claims"] = [self._claim_from_row(item) for item in claim_rows]
        run["evidence"] = [dict(item) for item in evidence_rows]
        return run

    def get_run_by_idempotency_key(
        self,
        user_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
        run = self._run_from_row(row)
        return self.get_run(run["id"]) if run else None

    def count_active_runs(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_runs WHERE status IN ('queued', 'running')"
            ).fetchone()
        return int(row["count"] if row else 0)

    def recover_interrupted_runs(self) -> int:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, cancel_requested FROM agent_runs WHERE status='running'"
            ).fetchall()
            for row in rows:
                run_id = row["id"]
                cancelled = bool(row["cancel_requested"])
                next_status = "cancelled" if cancelled else "queued"
                connection.execute(
                    """
                    UPDATE agent_runs
                    SET status=?, worker_id=NULL, updated_at=?, completed_at=?
                    WHERE id=? AND status='running'
                    """,
                    (next_status, now, now if cancelled else None, run_id),
                )
                connection.execute(
                    """
                    UPDATE agent_steps
                    SET status=?, started_at=NULL, completed_at=?
                    WHERE run_id=? AND status='running'
                    """,
                    ("cancelled" if cancelled else "queued", now if cancelled else None, run_id),
                )
                self._append_audit(
                    connection,
                    run_id,
                    "run.cancelled" if cancelled else "run.recovered",
                    {"previous_status": "running", "new_status": next_status},
                )
        return len(rows)

    def claim_next_run(self, worker_id: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE status='queued' AND cancel_requested=0
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            run_id = row["id"]
            changed = connection.execute(
                """
                UPDATE agent_runs
                SET status='running', worker_id=?, started_at=COALESCE(started_at, ?), updated_at=?
                WHERE id=? AND status='queued' AND cancel_requested=0
                """,
                (worker_id, now, now, run_id),
            ).rowcount
            if changed != 1:
                return None
            self._append_audit(
                connection,
                run_id,
                "run.started",
                {"worker_id": worker_id, "status": "running"},
                actor_id=worker_id,
            )
            claimed = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return self._run_from_row(claimed)

    def get_step(self, run_id: str, step_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id=? AND step_key=?",
                (run_id, step_key),
            ).fetchone()
        return self._step_from_row(row) if row else None

    def start_step(
        self,
        run_id: str,
        *,
        step_key: str,
        sequence_no: int,
        tool_name: str,
        tool_version: str,
        required: bool,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id=? AND step_key=?",
                (run_id, step_key),
            ).fetchone()
            if existing and existing["status"] in STEP_REUSABLE_STATUSES:
                return self._step_from_row(existing)
            step_id = existing["id"] if existing else _new_id("step")
            if existing:
                connection.execute(
                    """
                    UPDATE agent_steps
                    SET sequence_no=?, tool_name=?, tool_version=?, required=?, status='running',
                        input_json=?, output_json=NULL, evidence_id=NULL, error_code=NULL,
                        error_message=NULL, started_at=?, completed_at=NULL
                    WHERE id=?
                    """,
                    (
                        sequence_no,
                        tool_name,
                        tool_version,
                        int(required),
                        _json(input_payload),
                        now,
                        step_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO agent_steps (
                        id, run_id, step_key, sequence_no, tool_name, tool_version,
                        required, status, input_json, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (
                        step_id,
                        run_id,
                        step_key,
                        sequence_no,
                        tool_name,
                        tool_version,
                        int(required),
                        _json(input_payload),
                        now,
                    ),
                )
            self._append_audit(
                connection,
                run_id,
                "tool.call.started",
                {
                    "step_id": step_id,
                    "step_key": step_key,
                    "tool": tool_name,
                    "tool_version": tool_version,
                },
            )
            row = connection.execute("SELECT * FROM agent_steps WHERE id=?", (step_id,)).fetchone()
        return self._step_from_row(row)

    def complete_step_with_evidence(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str,
        payload: dict[str, Any],
        evidence_type: str,
        subject_type: str,
        subject_id: str,
        provider: str,
        source_url: str | None,
        as_of: str | None,
        quality_status: str,
        schema_version: str = "1.0.0",
    ) -> dict[str, Any]:
        if status not in STEP_REUSABLE_STATUSES:
            raise ValueError(f"不能将工具步骤完成为状态:{status}")
        now = _utc_now()
        payload_json = _json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        evidence_id = _new_id("ev")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO agent_evidence (
                    id, run_id, step_id, evidence_type, subject_type, subject_id,
                    provider, source_url, observed_at, as_of, schema_version,
                    quality_status, payload_json, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    run_id,
                    step_id,
                    evidence_type,
                    subject_type,
                    subject_id,
                    provider,
                    source_url,
                    now,
                    as_of,
                    schema_version,
                    quality_status,
                    payload_json,
                    payload_hash,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE agent_steps
                SET status=?, output_json=?, evidence_id=?, error_code=NULL,
                    error_message=NULL, completed_at=?
                WHERE id=? AND run_id=?
                """,
                (
                    status,
                    _json({"evidence_id": evidence_id, "quality_status": quality_status}),
                    evidence_id,
                    now,
                    step_id,
                    run_id,
                ),
            )
            self._append_audit(
                connection,
                run_id,
                "evidence.created",
                {
                    "evidence_id": evidence_id,
                    "step_id": step_id,
                    "payload_sha256": payload_hash,
                    "quality_status": quality_status,
                },
            )
            self._append_audit(
                connection,
                run_id,
                "tool.call.completed",
                {"step_id": step_id, "status": status, "evidence_id": evidence_id},
            )
            row = connection.execute(
                "SELECT * FROM agent_evidence WHERE id=?",
                (evidence_id,),
            ).fetchone()
        return self._evidence_from_row(row, include_payload=True)

    def fail_step(
        self,
        run_id: str,
        step_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        now = _utc_now()
        message = str(error_message or "")[:500]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE agent_steps
                SET status='failed', error_code=?, error_message=?, completed_at=?
                WHERE id=? AND run_id=?
                """,
                (error_code, message, now, step_id, run_id),
            )
            self._append_audit(
                connection,
                run_id,
                "tool.call.failed",
                {"step_id": step_id, "error_code": error_code, "error": message},
            )

    def cancel_step(self, run_id: str, step_id: str, *, reason: str) -> None:
        now = _utc_now()
        message = str(reason or "Agent Run 已取消")[:500]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE agent_steps
                SET status='cancelled', error_code='TOOL_CANCELLED',
                    error_message=?, completed_at=?
                WHERE id=? AND run_id=? AND status='running'
                """,
                (message, now, step_id, run_id),
            )
            self._append_audit(
                connection,
                run_id,
                "tool.call.cancelled",
                {"step_id": step_id, "reason": message},
            )

    def get_evidence(
        self,
        run_id: str,
        evidence_id: str,
        *,
        include_payload: bool = True,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_evidence WHERE id=? AND run_id=?",
                (evidence_id, run_id),
            ).fetchone()
        return self._evidence_from_row(row, include_payload=include_payload) if row else None

    def add_claim(
        self,
        run_id: str,
        *,
        claim_key: str,
        claim_type: str,
        claim_text: str,
        value: Any,
        evidence_id: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        claim_id = _new_id("claim")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM agent_claims WHERE run_id=? AND claim_key=?",
                (run_id, claim_key),
            ).fetchone()
            if existing:
                claim_id = existing["id"]
                connection.execute(
                    """
                    UPDATE agent_claims
                    SET claim_type=?, claim_text=?, value_json=?, evidence_id=?
                    WHERE id=?
                    """,
                    (claim_type, claim_text, _json(value), evidence_id, claim_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO agent_claims (
                        id, run_id, claim_key, claim_type, claim_text,
                        value_json, evidence_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        run_id,
                        claim_key,
                        claim_type,
                        claim_text,
                        _json(value),
                        evidence_id,
                        now,
                    ),
                )
            row = connection.execute("SELECT * FROM agent_claims WHERE id=?", (claim_id,)).fetchone()
        return self._claim_from_row(row)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, Any] | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in RUN_TERMINAL_STATUSES:
            raise ValueError(f"无效 Agent Run 终态:{status}")
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if current is None:
                raise KeyError(f"Agent Run 不存在:{run_id}")
            if current["status"] in RUN_TERMINAL_STATUSES:
                row = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                return self._run_from_row(row)
            connection.execute(
                """
                UPDATE agent_runs
                SET status=?, result_json=?, error_code=?, error_message=?,
                    worker_id=NULL, updated_at=?, completed_at=?
                WHERE id=?
                """,
                (
                    status,
                    _json(result) if result is not None else None,
                    error_code,
                    str(error_message or "")[:500] or None,
                    now,
                    now,
                    run_id,
                ),
            )
            self._append_audit(
                connection,
                run_id,
                f"run.{status}",
                {
                    "status": status,
                    "error_code": error_code,
                    "claim_count": connection.execute(
                        "SELECT COUNT(*) AS count FROM agent_claims WHERE run_id=?",
                        (run_id,),
                    ).fetchone()["count"],
                },
            )
            row = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return self._run_from_row(row)

    def request_cancel(self, run_id: str, *, actor_id: str = "anonymous") -> dict[str, Any] | None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            if row["status"] in RUN_TERMINAL_STATUSES:
                return self._run_from_row(row)
            next_status = "cancelled" if row["status"] == "queued" else row["status"]
            completed_at = now if next_status == "cancelled" else None
            connection.execute(
                """
                UPDATE agent_runs
                SET cancel_requested=1, status=?, updated_at=?, completed_at=?
                WHERE id=?
                """,
                (next_status, now, completed_at, run_id),
            )
            self._append_audit(
                connection,
                run_id,
                "run.cancel_requested",
                {"previous_status": row["status"], "status": next_status},
                actor_type="user",
                actor_id=actor_id,
            )
            updated = connection.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return self._run_from_row(updated)

    def is_cancel_requested(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def list_audit_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_audit_events
                WHERE run_id=?
                ORDER BY sequence_no
                """,
                (run_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["details"] = _load(item.pop("details_json", None), {})
            items.append(item)
        return items

    def verify_audit_chain(self, run_id: str) -> dict[str, Any]:
        items = self.list_audit_events(run_id)
        previous_hash = None
        for expected_sequence, item in enumerate(items, start=1):
            canonical = {
                "id": item["id"],
                "run_id": item["run_id"],
                "event_type": item["event_type"],
                "actor_type": item["actor_type"],
                "actor_id": item["actor_id"],
                "sequence_no": item["sequence_no"],
                "details": item["details"],
                "previous_hash": item["previous_hash"],
                "created_at": item["created_at"],
            }
            calculated_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            if (
                int(item["sequence_no"]) != expected_sequence
                or item["previous_hash"] != previous_hash
                or item["event_hash"] != calculated_hash
            ):
                return {
                    "verified": False,
                    "event_count": len(items),
                    "failing_sequence": item["sequence_no"],
                    "chain_head": previous_hash,
                }
            previous_hash = item["event_hash"]
        return {
            "verified": True,
            "event_count": len(items),
            "failing_sequence": None,
            "chain_head": previous_hash,
        }
