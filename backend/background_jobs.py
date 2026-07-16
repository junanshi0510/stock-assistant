# -*- coding: utf-8 -*-
"""Durable background-job envelope used by Celery workers.

Redis messages contain only job IDs. Inputs, outputs, leases, retries and the
tamper-evident event chain remain in the authoritative database.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
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


TERMINAL_JOB_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
ACTIVE_JOB_STATUSES = {"queued", "running"}


class BackgroundJobError(RuntimeError):
    pass


class BackgroundJobLeaseError(BackgroundJobError):
    pass


def _utc_now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _utc_now(value).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _default_target() -> str:
    return configured_database_target(
        str(Path(__file__).resolve().parent / "stock_assistant.db")
    )


def sanitize_worker_error(value: Any) -> str:
    message = re.sub(r"\s+", " ", str(value or "background job failed")).strip()
    message = re.sub(
        r"(?i)(access[_-]?key|secret|token|password|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+",
        r"\1=***",
        message,
    )
    message = re.sub(
        r"(?i)([a-z][a-z0-9+.-]*://(?:[^:/@\s]+)?:)[^@\s]+@",
        r"\1***@",
        message,
    )
    return message[:500]


class BackgroundJobRepository:
    def __init__(self, target: str | os.PathLike[str] | None = None) -> None:
        self.target = str(target or _default_target())
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        self.ensure_schema()

    def _connect(self):
        return connect_database(self.target, close_on_exit=True)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(
                        connection, {"background_jobs", "background_job_events"}
                    )
                else:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS background_jobs (
                            id TEXT PRIMARY KEY,
                            tenant_id TEXT NOT NULL,
                            user_id TEXT NOT NULL,
                            job_type TEXT NOT NULL,
                            queue_name TEXT NOT NULL,
                            status TEXT NOT NULL,
                            idempotency_key TEXT,
                            payload_json TEXT NOT NULL,
                            payload_sha256 TEXT NOT NULL,
                            object_asset_id TEXT,
                            result_json TEXT,
                            result_sha256 TEXT,
                            error_code TEXT,
                            error_message TEXT,
                            attempt_count INTEGER NOT NULL DEFAULT 0,
                            max_attempts INTEGER NOT NULL DEFAULT 3,
                            celery_task_id TEXT,
                            worker_id TEXT,
                            lease_expires_at TEXT,
                            heartbeat_at TEXT,
                            available_at TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            started_at TEXT,
                            completed_at TEXT,
                            cancel_requested INTEGER NOT NULL DEFAULT 0,
                            UNIQUE(user_id, job_type, idempotency_key)
                        );
                        CREATE INDEX IF NOT EXISTS idx_background_jobs_queue
                        ON background_jobs(queue_name, status, available_at, created_at);
                        CREATE TABLE IF NOT EXISTS background_job_events (
                            id TEXT PRIMARY KEY,
                            job_id TEXT NOT NULL REFERENCES background_jobs(id) ON DELETE RESTRICT,
                            sequence_no INTEGER NOT NULL,
                            event_type TEXT NOT NULL,
                            actor_type TEXT NOT NULL,
                            actor_id TEXT NOT NULL,
                            details_json TEXT NOT NULL,
                            previous_hash TEXT,
                            event_hash TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            UNIQUE(job_id, sequence_no)
                        );
                        CREATE TRIGGER IF NOT EXISTS trg_background_job_events_no_update
                        BEFORE UPDATE ON background_job_events BEGIN
                            SELECT RAISE(ABORT, 'background job events are immutable');
                        END;
                        CREATE TRIGGER IF NOT EXISTS trg_background_job_events_no_delete
                        BEFORE DELETE ON background_job_events BEGIN
                            SELECT RAISE(ABORT, 'background job events are immutable');
                        END;
                        """
                    )
            self._schema_ready = True

    def _append_event(
        self,
        connection,
        job_id: str,
        event_type: str,
        *,
        actor_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = connection.execute(
            """
            SELECT sequence_no, event_hash FROM background_job_events
            WHERE job_id=? ORDER BY sequence_no DESC LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        created_at = _iso()
        payload = {
            "id": _new_id("job_evt"),
            "job_id": job_id,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "actor_type": str(actor_type),
            "actor_id": str(actor_id),
            "details": details or {},
            "previous_hash": previous["event_hash"] if previous else None,
            "created_at": created_at,
        }
        event_hash = _sha256(_json(payload))
        connection.execute(
            """
            INSERT INTO background_job_events(
                id, job_id, sequence_no, event_type, actor_type, actor_id,
                details_json, previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                job_id,
                sequence_no,
                event_type,
                actor_type,
                actor_id,
                _json(details or {}),
                payload["previous_hash"],
                event_hash,
                created_at,
            ),
        )
        return {**payload, "event_hash": event_hash}

    @staticmethod
    def _from_row(row, *, include_payload: bool = False) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload_json = str(item.pop("payload_json", ""))
        result_json = item.pop("result_json", None)
        item["payload_verified"] = _sha256(payload_json) == item.get("payload_sha256")
        item["result_verified"] = (
            result_json is None or _sha256(str(result_json)) == item.get("result_sha256")
        )
        if include_payload:
            item["payload"] = _load(payload_json, {})
            item["result"] = _load(result_json, None)
        item["cancel_requested"] = bool(item.get("cancel_requested"))
        return item

    def create_job(
        self,
        *,
        job_type: str,
        queue_name: str,
        payload: dict[str, Any],
        tenant_id: str,
        user_id: str,
        idempotency_key: str | None = None,
        object_asset_id: str | None = None,
        max_attempts: int = 3,
    ) -> tuple[dict[str, Any], bool]:
        payload_json = _json(payload)
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    """
                    SELECT * FROM background_jobs
                    WHERE user_id=? AND job_type=? AND idempotency_key=?
                    """,
                    (user_id, job_type, idempotency_key),
                ).fetchone()
                if existing is not None:
                    parsed = self._from_row(existing, include_payload=True)
                    if not parsed or not parsed["payload_verified"]:
                        raise BackgroundJobError("已存在任务的输入完整性校验失败")
                    if parsed["payload"] != payload:
                        raise BackgroundJobError("幂等键已绑定不同任务输入")
                    return parsed, False
            job_id = _new_id("job")
            connection.execute(
                """
                INSERT INTO background_jobs(
                    id, tenant_id, user_id, job_type, queue_name, status,
                    idempotency_key, payload_json, payload_sha256, object_asset_id,
                    attempt_count, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    user_id,
                    job_type,
                    queue_name,
                    idempotency_key,
                    payload_json,
                    _sha256(payload_json),
                    object_asset_id,
                    max(1, min(10, int(max_attempts))),
                    now,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                job_id,
                "job_queued",
                actor_type="system",
                actor_id="api",
                details={"job_type": job_type, "queue_name": queue_name},
            )
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._from_row(row, include_payload=True), True

    def get_job(self, job_id: str, *, include_payload: bool = False) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._from_row(row, include_payload=include_payload)

    def list_dispatchable_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM background_jobs
                WHERE status='queued' AND cancel_requested=0 AND available_at<=?
                  AND queue_name IN ('market-data', 'llm', 'ocr')
                ORDER BY available_at, created_at, id
                LIMIT ?
                """,
                (_iso(), max(1, min(1000, int(limit)))),
            ).fetchall()
        return [self._from_row(row, include_payload=False) for row in rows]

    def recover_expired_jobs(self, limit: int = 100) -> int:
        now = _iso()
        recovered = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id FROM background_jobs
                WHERE status='running' AND lease_expires_at IS NOT NULL
                  AND lease_expires_at<?
                ORDER BY lease_expires_at, id LIMIT ?
                """,
                (now, max(1, min(1000, int(limit)))),
            ).fetchall()
            for row in rows:
                job_id = str(row["id"])
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status='queued', worker_id=NULL, lease_expires_at=NULL,
                        heartbeat_at=NULL, available_at=?, updated_at=?
                    WHERE id=? AND status='running'
                    """,
                    (now, now, job_id),
                )
                self._append_event(
                    connection,
                    job_id,
                    "job_lease_expired",
                    actor_type="system",
                    actor_id="scheduler",
                )
                recovered += 1
        return recovered

    def mark_dispatched(self, job_id: str, celery_task_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, celery_task_id FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None or str(row["status"]) not in ACTIVE_JOB_STATUSES:
                return
            if str(row["celery_task_id"] or "") == str(celery_task_id):
                return
            connection.execute(
                "UPDATE background_jobs SET celery_task_id=?, updated_at=? WHERE id=?",
                (celery_task_id, _iso(), job_id),
            )
            self._append_event(
                connection,
                job_id,
                "job_dispatched",
                actor_type="system",
                actor_id="dispatcher",
                details={"task_id": str(celery_task_id)},
            )

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 300,
        now: dt.datetime | None = None,
    ) -> dict[str, Any] | None:
        now_dt = _utc_now(now)
        now_text = _iso(now_dt)
        lease = _iso(now_dt + dt.timedelta(seconds=max(60, int(lease_seconds))))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
            job = self._from_row(row, include_payload=True)
            if not job or not job["payload_verified"]:
                return None
            if job["status"] in TERMINAL_JOB_STATUSES:
                return job
            if job["cancel_requested"]:
                connection.execute(
                    """
                    UPDATE background_jobs SET status='cancelled', completed_at=?,
                        updated_at=?, worker_id=NULL, lease_expires_at=NULL
                    WHERE id=?
                    """,
                    (now_text, now_text, job_id),
                )
                self._append_event(
                    connection,
                    job_id,
                    "job_cancelled",
                    actor_type="worker",
                    actor_id=worker_id,
                )
                cancelled = connection.execute(
                    "SELECT * FROM background_jobs WHERE id=?", (job_id,)
                ).fetchone()
                return self._from_row(cancelled, include_payload=True)
            available = str(job.get("available_at") or "") <= now_text
            expired = str(job.get("lease_expires_at") or "") < now_text
            if not (
                (job["status"] == "queued" and available)
                or (job["status"] == "running" and expired)
            ):
                return None
            connection.execute(
                """
                UPDATE background_jobs
                SET status='running', worker_id=?, lease_expires_at=?, heartbeat_at=?,
                    attempt_count=attempt_count+1, started_at=COALESCE(started_at, ?),
                    updated_at=?, error_code=NULL, error_message=NULL
                WHERE id=?
                """,
                (worker_id, lease, now_text, now_text, now_text, job_id),
            )
            self._append_event(
                connection,
                job_id,
                "job_started",
                actor_type="worker",
                actor_id=worker_id,
                details={"lease_expires_at": lease},
            )
            claimed = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._from_row(claimed, include_payload=True)

    def heartbeat(
        self, job_id: str, worker_id: str, *, lease_seconds: int = 300
    ) -> bool:
        now_dt = _utc_now()
        cursor = None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=?
                WHERE id=? AND status='running' AND worker_id=?
                """,
                (
                    _iso(now_dt),
                    _iso(now_dt + dt.timedelta(seconds=max(60, int(lease_seconds)))),
                    _iso(now_dt),
                    job_id,
                    worker_id,
                ),
            )
        return bool(cursor and cursor.rowcount == 1)

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        result: dict[str, Any],
        *,
        status: str = "succeeded",
    ) -> dict[str, Any]:
        if status not in {"succeeded", "partial"}:
            raise ValueError("invalid completion status")
        result_json = _json(result)
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET status=?, result_json=?, result_sha256=?, completed_at=?, updated_at=?,
                    worker_id=NULL, lease_expires_at=NULL, heartbeat_at=NULL
                WHERE id=? AND status='running' AND worker_id=?
                """,
                (status, result_json, _sha256(result_json), now, now, job_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise BackgroundJobLeaseError("后台任务租约已失效")
            self._append_event(
                connection,
                job_id,
                "job_completed",
                actor_type="worker",
                actor_id=worker_id,
                details={"status": status, "result_sha256": _sha256(result_json)},
            )
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._from_row(row, include_payload=True)

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int = 30,
    ) -> dict[str, Any]:
        now_dt = _utc_now()
        now = _iso(now_dt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "running" or row["worker_id"] != worker_id:
                raise BackgroundJobLeaseError("后台任务租约已失效")
            should_retry = bool(retryable) and int(row["attempt_count"]) < int(
                row["max_attempts"]
            )
            status = "queued" if should_retry else "failed"
            available_at = _iso(
                now_dt + dt.timedelta(seconds=max(1, int(retry_delay_seconds)))
            )
            message = sanitize_worker_error(error_message)
            connection.execute(
                """
                UPDATE background_jobs
                SET status=?, error_code=?, error_message=?, available_at=?,
                    completed_at=?, updated_at=?, worker_id=NULL,
                    lease_expires_at=NULL, heartbeat_at=NULL
                WHERE id=?
                """,
                (
                    status,
                    str(error_code)[:80],
                    message,
                    available_at,
                    None if should_retry else now,
                    now,
                    job_id,
                ),
            )
            self._append_event(
                connection,
                job_id,
                "job_retry_scheduled" if should_retry else "job_failed",
                actor_type="worker",
                actor_id=worker_id,
                details={
                    "error_code": str(error_code)[:80],
                    "retryable": should_retry,
                    "available_at": available_at if should_retry else None,
                },
            )
            updated = connection.execute(
                "SELECT * FROM background_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._from_row(updated, include_payload=True)

    def request_cancel(self, job_id: str, user_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM background_jobs WHERE id=? AND user_id=?",
                (job_id, user_id),
            ).fetchone()
            if row is None or row["status"] in TERMINAL_JOB_STATUSES:
                return False
            connection.execute(
                "UPDATE background_jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                (_iso(), job_id),
            )
            self._append_event(
                connection,
                job_id,
                "cancel_requested",
                actor_type="user",
                actor_id=user_id,
            )
        return True

    def verify_event_chain(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM background_job_events WHERE job_id=? ORDER BY sequence_no",
                (job_id,),
            ).fetchall()
        previous_hash = None
        for expected_sequence, row in enumerate(rows, start=1):
            item = dict(row)
            payload = {
                "id": item["id"],
                "job_id": item["job_id"],
                "sequence_no": item["sequence_no"],
                "event_type": item["event_type"],
                "actor_type": item["actor_type"],
                "actor_id": item["actor_id"],
                "details": _load(item["details_json"], {}),
                "previous_hash": item["previous_hash"],
                "created_at": item["created_at"],
            }
            if (
                int(item["sequence_no"]) != expected_sequence
                or item["previous_hash"] != previous_hash
                or _sha256(_json(payload)) != item["event_hash"]
            ):
                return {
                    "verified": False,
                    "event_count": len(rows),
                    "failing_sequence": expected_sequence,
                }
            previous_hash = item["event_hash"]
        return {
            "verified": bool(rows),
            "event_count": len(rows),
            "failing_sequence": None if rows else 0,
            "chain_head": previous_hash,
        }
