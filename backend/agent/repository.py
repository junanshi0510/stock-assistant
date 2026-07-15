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
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any


RUN_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "abstained"}
STEP_REUSABLE_STATUSES = {"succeeded", "partial"}
STRATEGY_STATUSES = {"draft", "review", "shadow", "canary", "active", "paused", "retired"}


class AgentQueueCapacityError(RuntimeError):
    def __init__(self, *, active: int, requested: int, maximum: int) -> None:
        self.active = int(active)
        self.requested = int(requested)
        self.maximum = int(maximum)
        super().__init__(
            f"活动任务 {self.active} + 本次 {self.requested} 超过队列上限 {self.maximum}"
        )


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite's context manager, then always close."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _as_utc_datetime(value: str | dt.datetime | None = None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _utc_iso(value: str | dt.datetime | None = None) -> str:
    return _as_utc_datetime(value).isoformat(timespec="milliseconds")


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
                        profile_version_id TEXT,
                        exposure_snapshot_id TEXT,
                        UNIQUE(user_id, idempotency_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_runs_queue
                    ON agent_runs(status, created_at);

                    CREATE INDEX IF NOT EXISTS idx_agent_runs_input
                    ON agent_runs(user_id, input_hash, status);

                    CREATE TABLE IF NOT EXISTS agent_batches (
                        id                TEXT PRIMARY KEY,
                        tenant_id         TEXT NOT NULL,
                        user_id           TEXT NOT NULL,
                        intent            TEXT NOT NULL,
                        input_json        TEXT NOT NULL,
                        input_hash        TEXT NOT NULL,
                        idempotency_key   TEXT,
                        created_at        TEXT NOT NULL,
                        updated_at        TEXT NOT NULL,
                        UNIQUE(user_id, idempotency_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_batches_user
                    ON agent_batches(tenant_id, user_id, created_at DESC);

                    CREATE TABLE IF NOT EXISTS agent_batch_items (
                        batch_id       TEXT NOT NULL REFERENCES agent_batches(id) ON DELETE CASCADE,
                        run_id         TEXT NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
                        sequence_no    INTEGER NOT NULL,
                        code           TEXT NOT NULL,
                        PRIMARY KEY (batch_id, run_id),
                        UNIQUE(batch_id, sequence_no),
                        UNIQUE(batch_id, code)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_batch_items_batch
                    ON agent_batch_items(batch_id, sequence_no);

                    CREATE TABLE IF NOT EXISTS agent_batch_allocation_events (
                        id                   TEXT PRIMARY KEY,
                        batch_id             TEXT NOT NULL UNIQUE REFERENCES agent_batches(id),
                        tenant_id            TEXT NOT NULL,
                        user_id              TEXT NOT NULL,
                        sequence_no          INTEGER NOT NULL CHECK(sequence_no = 1),
                        event_type           TEXT NOT NULL,
                        schema_version       TEXT NOT NULL,
                        strategy_id          TEXT NOT NULL,
                        strategy_version     TEXT NOT NULL,
                        batch_input_hash     TEXT NOT NULL,
                        run_set_hash         TEXT NOT NULL,
                        payload_json         TEXT NOT NULL,
                        payload_sha256       TEXT NOT NULL,
                        previous_hash        TEXT,
                        event_hash           TEXT NOT NULL,
                        actor_id             TEXT NOT NULL,
                        created_at           TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_batch_allocations_user
                    ON agent_batch_allocation_events(user_id, created_at DESC);

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_allocation_no_update
                    BEFORE UPDATE ON agent_batch_allocation_events
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch allocation events are immutable');
                    END;

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_allocation_no_delete
                    BEFORE DELETE ON agent_batch_allocation_events
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch allocation events are immutable');
                    END;

                    CREATE TABLE IF NOT EXISTS agent_batch_purchase_preflight_events (
                        id                         TEXT PRIMARY KEY,
                        batch_id                   TEXT NOT NULL REFERENCES agent_batches(id),
                        tenant_id                  TEXT NOT NULL,
                        user_id                    TEXT NOT NULL,
                        sequence_no                INTEGER NOT NULL,
                        event_type                 TEXT NOT NULL,
                        schema_version             TEXT NOT NULL,
                        strategy_id                TEXT NOT NULL,
                        strategy_version           TEXT NOT NULL,
                        allocation_event_id        TEXT NOT NULL REFERENCES agent_batch_allocation_events(id),
                        allocation_event_hash      TEXT NOT NULL,
                        allocation_payload_sha256  TEXT NOT NULL,
                        request_sha256             TEXT NOT NULL,
                        payload_json               TEXT NOT NULL,
                        payload_sha256             TEXT NOT NULL,
                        previous_hash              TEXT,
                        event_hash                 TEXT NOT NULL,
                        actor_id                   TEXT NOT NULL,
                        created_at                 TEXT NOT NULL,
                        UNIQUE(batch_id, sequence_no),
                        UNIQUE(batch_id, request_sha256)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_batch_purchase_preflight_user
                    ON agent_batch_purchase_preflight_events(user_id, created_at DESC);

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_purchase_preflight_no_update
                    BEFORE UPDATE ON agent_batch_purchase_preflight_events
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch purchase preflight events are immutable');
                    END;

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_purchase_preflight_no_delete
                    BEFORE DELETE ON agent_batch_purchase_preflight_events
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch purchase preflight events are immutable');
                    END;

                    CREATE TABLE IF NOT EXISTS agent_batch_purchase_execution_events (
                        id                         TEXT PRIMARY KEY,
                        batch_id                   TEXT NOT NULL REFERENCES agent_batches(id),
                        tenant_id                  TEXT NOT NULL,
                        user_id                    TEXT NOT NULL,
                        sequence_no                INTEGER NOT NULL,
                        event_type                 TEXT NOT NULL,
                        schema_version             TEXT NOT NULL,
                        strategy_id                TEXT NOT NULL,
                        strategy_version           TEXT NOT NULL,
                        preflight_event_id          TEXT NOT NULL REFERENCES agent_batch_purchase_preflight_events(id),
                        preflight_event_hash        TEXT NOT NULL,
                        preflight_payload_sha256    TEXT NOT NULL,
                        request_sha256              TEXT NOT NULL,
                        payload_json                TEXT NOT NULL,
                        payload_sha256              TEXT NOT NULL,
                        previous_hash               TEXT,
                        event_hash                  TEXT NOT NULL,
                        actor_id                    TEXT NOT NULL,
                        created_at                  TEXT NOT NULL,
                        UNIQUE(batch_id, sequence_no),
                        UNIQUE(batch_id, request_sha256)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_batch_purchase_execution_user
                    ON agent_batch_purchase_execution_events(user_id, created_at DESC);

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_purchase_execution_no_update
                    BEFORE UPDATE ON agent_batch_purchase_execution_events
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch purchase execution events are immutable');
                    END;

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_purchase_execution_no_delete
                    BEFORE DELETE ON agent_batch_purchase_execution_events
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch purchase execution events are immutable');
                    END;

                    CREATE TABLE IF NOT EXISTS agent_batch_purchase_transaction_bindings (
                        user_id            TEXT NOT NULL,
                        transaction_id      INTEGER NOT NULL,
                        batch_id            TEXT NOT NULL REFERENCES agent_batches(id),
                        first_event_id       TEXT NOT NULL REFERENCES agent_batch_purchase_execution_events(id),
                        transaction_sha256   TEXT NOT NULL,
                        created_at           TEXT NOT NULL,
                        PRIMARY KEY(user_id, transaction_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_batch_purchase_binding_batch
                    ON agent_batch_purchase_transaction_bindings(batch_id, created_at);

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_purchase_binding_no_update
                    BEFORE UPDATE ON agent_batch_purchase_transaction_bindings
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch purchase transaction bindings are immutable');
                    END;

                    CREATE TRIGGER IF NOT EXISTS trg_agent_batch_purchase_binding_no_delete
                    BEFORE DELETE ON agent_batch_purchase_transaction_bindings
                    BEGIN
                        SELECT RAISE(ABORT, 'agent batch purchase transaction bindings are immutable');
                    END;

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

                    CREATE TABLE IF NOT EXISTS agent_outcome_schedules (
                        id                    TEXT PRIMARY KEY,
                        run_id                TEXT NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
                        tenant_id             TEXT NOT NULL,
                        user_id               TEXT NOT NULL,
                        status                TEXT NOT NULL,
                        interval_hours        INTEGER NOT NULL,
                        next_run_at           TEXT,
                        lease_owner           TEXT,
                        lease_expires_at      TEXT,
                        attempt_count         INTEGER NOT NULL DEFAULT 0,
                        consecutive_failures  INTEGER NOT NULL DEFAULT 0,
                        last_started_at       TEXT,
                        last_finished_at      TEXT,
                        last_success_at       TEXT,
                        last_provider_as_of   TEXT,
                        last_evidence_id      TEXT REFERENCES agent_evidence(id) ON DELETE SET NULL,
                        last_error_code       TEXT,
                        last_error_message    TEXT,
                        created_at            TEXT NOT NULL,
                        updated_at            TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_outcome_schedule_due
                    ON agent_outcome_schedules(status, next_run_at, lease_expires_at);

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

                    CREATE TABLE IF NOT EXISTS agent_strategy_versions (
                        strategy_id       TEXT NOT NULL,
                        strategy_version  TEXT NOT NULL,
                        name              TEXT NOT NULL,
                        strategy_kind     TEXT NOT NULL,
                        owner_id          TEXT NOT NULL,
                        status            TEXT NOT NULL,
                        previous_status   TEXT,
                        manifest_json     TEXT NOT NULL,
                        manifest_sha256   TEXT NOT NULL,
                        registered_at     TEXT NOT NULL,
                        status_updated_at TEXT NOT NULL,
                        PRIMARY KEY (strategy_id, strategy_version)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_strategy_status
                    ON agent_strategy_versions(status, strategy_id, strategy_version);

                    CREATE TABLE IF NOT EXISTS agent_strategy_audit_events (
                        id                TEXT PRIMARY KEY,
                        strategy_id       TEXT NOT NULL,
                        strategy_version  TEXT NOT NULL,
                        sequence_no       INTEGER NOT NULL,
                        event_type        TEXT NOT NULL,
                        actor_role        TEXT NOT NULL,
                        actor_id          TEXT NOT NULL,
                        details_json      TEXT NOT NULL,
                        previous_hash     TEXT,
                        event_hash        TEXT NOT NULL,
                        created_at        TEXT NOT NULL,
                        FOREIGN KEY (strategy_id, strategy_version)
                            REFERENCES agent_strategy_versions(strategy_id, strategy_version)
                            ON DELETE RESTRICT,
                        UNIQUE(strategy_id, strategy_version, sequence_no)
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_strategy_audit
                    ON agent_strategy_audit_events(strategy_id, strategy_version, sequence_no);

                    CREATE TABLE IF NOT EXISTS agent_strategy_shadow_enrollments (
                        id                    TEXT PRIMARY KEY,
                        run_id                TEXT NOT NULL UNIQUE
                                                  REFERENCES agent_runs(id) ON DELETE CASCADE,
                        tenant_id             TEXT NOT NULL,
                        user_id               TEXT NOT NULL,
                        strategy_id           TEXT NOT NULL,
                        strategy_version      TEXT NOT NULL,
                        manifest_sha256       TEXT NOT NULL,
                        strategy_status       TEXT NOT NULL,
                        governance_evidence_id TEXT NOT NULL
                                                   REFERENCES agent_evidence(id) ON DELETE RESTRICT,
                        signal_evidence_id    TEXT NOT NULL
                                                   REFERENCES agent_evidence(id) ON DELETE RESTRICT,
                        fund_code             TEXT NOT NULL,
                        fund_name             TEXT,
                        baseline_as_of        TEXT NOT NULL,
                        baseline_nav          REAL NOT NULL,
                        signal_direction      TEXT NOT NULL,
                        signal_decision       TEXT NOT NULL,
                        confidence_level      TEXT NOT NULL,
                        horizon               TEXT NOT NULL,
                        observation_days      INTEGER NOT NULL,
                        signal_snapshot_json  TEXT NOT NULL,
                        signal_snapshot_sha256 TEXT NOT NULL,
                        status                TEXT NOT NULL,
                        exclusion_reason      TEXT,
                        blocking_enrollment_id TEXT,
                        next_run_at           TEXT,
                        lease_owner           TEXT,
                        lease_expires_at      TEXT,
                        attempt_count         INTEGER NOT NULL DEFAULT 0,
                        consecutive_failures  INTEGER NOT NULL DEFAULT 0,
                        last_started_at       TEXT,
                        last_finished_at      TEXT,
                        last_provider_as_of   TEXT,
                        observed_as_of        TEXT,
                        last_evidence_id      TEXT REFERENCES agent_evidence(id) ON DELETE SET NULL,
                        last_error_code       TEXT,
                        last_error_message    TEXT,
                        created_at            TEXT NOT NULL,
                        updated_at            TEXT NOT NULL,
                        FOREIGN KEY (strategy_id, strategy_version)
                            REFERENCES agent_strategy_versions(strategy_id, strategy_version)
                            ON DELETE RESTRICT,
                        FOREIGN KEY (blocking_enrollment_id)
                            REFERENCES agent_strategy_shadow_enrollments(id)
                            ON DELETE RESTRICT
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_strategy_shadow_due
                    ON agent_strategy_shadow_enrollments(status, next_run_at, lease_expires_at);

                    CREATE INDEX IF NOT EXISTS idx_agent_strategy_shadow_version
                    ON agent_strategy_shadow_enrollments(
                        strategy_id, strategy_version, fund_code, horizon, baseline_as_of
                    );

                    CREATE TABLE IF NOT EXISTS agent_strategy_shadow_cohorts (
                        id                              TEXT PRIMARY KEY,
                        enrollment_id                   TEXT NOT NULL UNIQUE
                                                              REFERENCES agent_strategy_shadow_enrollments(id)
                                                              ON DELETE CASCADE,
                        run_id                          TEXT NOT NULL
                                                              REFERENCES agent_runs(id) ON DELETE CASCADE,
                        strategy_id                     TEXT NOT NULL,
                        strategy_version                TEXT NOT NULL,
                        fund_code                       TEXT NOT NULL,
                        horizon                         TEXT NOT NULL,
                        observation_days                INTEGER NOT NULL,
                        taxonomy_id                     TEXT NOT NULL,
                        taxonomy_version                TEXT NOT NULL,
                        market_profile_evidence_id      TEXT NOT NULL
                                                              REFERENCES agent_evidence(id)
                                                              ON DELETE RESTRICT,
                        market_profile_payload_sha256   TEXT NOT NULL,
                        signal_evidence_id              TEXT NOT NULL
                                                              REFERENCES agent_evidence(id)
                                                              ON DELETE RESTRICT,
                        signal_payload_sha256           TEXT NOT NULL,
                        evidence_id                     TEXT NOT NULL UNIQUE
                                                              REFERENCES agent_evidence(id)
                                                              ON DELETE RESTRICT,
                        market_primary                  TEXT NOT NULL,
                        asset_class                     TEXT NOT NULL,
                        vehicle_type                    TEXT NOT NULL,
                        trend_regime                    TEXT NOT NULL,
                        drawdown_regime                 TEXT NOT NULL,
                        release_cohort_key              TEXT NOT NULL,
                        regime_cohort_key               TEXT NOT NULL,
                        release_eligible                INTEGER NOT NULL,
                        cohort_json                     TEXT NOT NULL,
                        cohort_sha256                   TEXT NOT NULL,
                        created_at                      TEXT NOT NULL,
                        FOREIGN KEY (strategy_id, strategy_version)
                            REFERENCES agent_strategy_versions(strategy_id, strategy_version)
                            ON DELETE RESTRICT
                    );

                    CREATE INDEX IF NOT EXISTS idx_agent_strategy_shadow_cohort_version
                    ON agent_strategy_shadow_cohorts(
                        strategy_id, strategy_version, release_cohort_key, created_at
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
                run_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()
                }
                if "profile_version_id" not in run_columns:
                    connection.execute(
                        "ALTER TABLE agent_runs ADD COLUMN profile_version_id TEXT"
                    )
                if "exposure_snapshot_id" not in run_columns:
                    connection.execute(
                        "ALTER TABLE agent_runs ADD COLUMN exposure_snapshot_id TEXT"
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

    def _append_strategy_audit(
        self,
        connection: sqlite3.Connection,
        strategy_id: str,
        strategy_version: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        *,
        actor_role: str,
        actor_id: str,
    ) -> dict[str, Any]:
        previous = connection.execute(
            """
            SELECT event_hash, sequence_no
            FROM agent_strategy_audit_events
            WHERE strategy_id=? AND strategy_version=?
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (strategy_id, strategy_version),
        ).fetchone()
        event_id = _new_id("strategy_audit")
        created_at = _utc_now()
        previous_hash = previous["event_hash"] if previous else None
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        canonical = {
            "id": event_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "actor_role": actor_role,
            "actor_id": actor_id,
            "details": details or {},
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        event_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO agent_strategy_audit_events (
                id, strategy_id, strategy_version, sequence_no, event_type,
                actor_role, actor_id, details_json, previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                strategy_id,
                strategy_version,
                sequence_no,
                event_type,
                actor_role,
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
    def _batch_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["input"] = _load(item.pop("input_json", None), {})
        return item

    @staticmethod
    def _batch_allocation_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload = _load(item.pop("payload_json", None), {})
        payload_verified = (
            hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
            == item.get("payload_sha256")
        )
        canonical = {
            "id": item.get("id"),
            "batch_id": item.get("batch_id"),
            "tenant_id": item.get("tenant_id"),
            "user_id": item.get("user_id"),
            "sequence_no": item.get("sequence_no"),
            "event_type": item.get("event_type"),
            "schema_version": item.get("schema_version"),
            "strategy_id": item.get("strategy_id"),
            "strategy_version": item.get("strategy_version"),
            "batch_input_hash": item.get("batch_input_hash"),
            "run_set_hash": item.get("run_set_hash"),
            "payload": payload,
            "payload_sha256": item.get("payload_sha256"),
            "previous_hash": item.get("previous_hash"),
            "actor_id": item.get("actor_id"),
            "created_at": item.get("created_at"),
        }
        item["payload"] = payload
        item["integrity_verified"] = bool(
            payload_verified
            and hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            == item.get("event_hash")
        )
        return item

    @staticmethod
    def _batch_purchase_preflight_from_row(
        row: sqlite3.Row | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload = _load(item.pop("payload_json", None), {})
        payload_verified = (
            hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
            == item.get("payload_sha256")
        )
        canonical = {
            "id": item.get("id"),
            "batch_id": item.get("batch_id"),
            "tenant_id": item.get("tenant_id"),
            "user_id": item.get("user_id"),
            "sequence_no": item.get("sequence_no"),
            "event_type": item.get("event_type"),
            "schema_version": item.get("schema_version"),
            "strategy_id": item.get("strategy_id"),
            "strategy_version": item.get("strategy_version"),
            "allocation_event_id": item.get("allocation_event_id"),
            "allocation_event_hash": item.get("allocation_event_hash"),
            "allocation_payload_sha256": item.get("allocation_payload_sha256"),
            "request_sha256": item.get("request_sha256"),
            "payload": payload,
            "payload_sha256": item.get("payload_sha256"),
            "previous_hash": item.get("previous_hash"),
            "actor_id": item.get("actor_id"),
            "created_at": item.get("created_at"),
        }
        item["payload"] = payload
        item["integrity_verified"] = bool(
            payload_verified
            and hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            == item.get("event_hash")
        )
        return item

    @staticmethod
    def _batch_purchase_execution_from_row(
        row: sqlite3.Row | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload = _load(item.pop("payload_json", None), {})
        payload_verified = (
            hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
            == item.get("payload_sha256")
        )
        canonical = {
            "id": item.get("id"),
            "batch_id": item.get("batch_id"),
            "tenant_id": item.get("tenant_id"),
            "user_id": item.get("user_id"),
            "sequence_no": item.get("sequence_no"),
            "event_type": item.get("event_type"),
            "schema_version": item.get("schema_version"),
            "strategy_id": item.get("strategy_id"),
            "strategy_version": item.get("strategy_version"),
            "preflight_event_id": item.get("preflight_event_id"),
            "preflight_event_hash": item.get("preflight_event_hash"),
            "preflight_payload_sha256": item.get("preflight_payload_sha256"),
            "request_sha256": item.get("request_sha256"),
            "payload": payload,
            "payload_sha256": item.get("payload_sha256"),
            "previous_hash": item.get("previous_hash"),
            "actor_id": item.get("actor_id"),
            "created_at": item.get("created_at"),
        }
        item["payload"] = payload
        item["integrity_verified"] = bool(
            payload_verified
            and hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            == item.get("event_hash")
        )
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

    @staticmethod
    def _outcome_schedule_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _strategy_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        manifest = _load(item.pop("manifest_json", None), {})
        item["manifest"] = manifest
        item["manifest_integrity_verified"] = (
            hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()
            == item.get("manifest_sha256")
        )
        return item

    @staticmethod
    def _strategy_audit_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["details"] = _load(item.pop("details_json", None), {})
        return item

    @staticmethod
    def _strategy_shadow_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        snapshot = _load(item.pop("signal_snapshot_json", None), {})
        item["signal_snapshot"] = snapshot
        item["signal_snapshot_integrity_verified"] = (
            hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()
            == item.get("signal_snapshot_sha256")
        )
        return item

    @staticmethod
    def _strategy_shadow_cohort_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        cohort = _load(item.pop("cohort_json", None), {})
        item["cohort"] = cohort
        item["release_eligible"] = bool(item.get("release_eligible"))
        item["cohort_integrity_verified"] = (
            hashlib.sha256(_json(cohort).encode("utf-8")).hexdigest()
            == item.get("cohort_sha256")
        )
        return item

    def create_batch(
        self,
        intent: str,
        input_payload: dict[str, Any],
        *,
        tenant_id: str = "public",
        user_id: str = "anonymous",
        idempotency_key: str | None = None,
        profile_version_id: str | None = None,
        max_active_runs: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        codes = [str(code).strip() for code in (input_payload.get("codes") or [])]
        if len(codes) < 2:
            raise ValueError("批量研究至少需要 2 只基金")
        if len(codes) != len(set(codes)):
            raise ValueError("批量研究基金代码不能重复")

        normalized_input = _json(input_payload)
        input_hash = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()
        now = _utc_now()
        batch_id = ""
        created = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT id FROM agent_batches WHERE user_id=? AND idempotency_key=?",
                    (user_id, idempotency_key),
                ).fetchone()
                if existing:
                    batch_id = str(existing["id"])

            if not batch_id:
                if max_active_runs is not None:
                    maximum = max(1, int(max_active_runs))
                    active_row = connection.execute(
                        "SELECT COUNT(*) AS count FROM agent_runs WHERE status IN ('queued', 'running')"
                    ).fetchone()
                    active = int(active_row["count"] if active_row else 0)
                    if active + len(codes) > maximum:
                        raise AgentQueueCapacityError(
                            active=active,
                            requested=len(codes),
                            maximum=maximum,
                        )
                batch_id = _new_id("batch")
                connection.execute(
                    """
                    INSERT INTO agent_batches (
                        id, tenant_id, user_id, intent, input_json, input_hash,
                        idempotency_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        tenant_id,
                        user_id,
                        intent,
                        normalized_input,
                        input_hash,
                        idempotency_key,
                        now,
                        now,
                    ),
                )
                common_input = {
                    key: value
                    for key, value in input_payload.items()
                    if key != "codes"
                }
                for sequence_no, code in enumerate(codes, start=1):
                    run_id = _new_id("run")
                    child_input = {
                        **common_input,
                        "code": code,
                        "batch_id": batch_id,
                        "allocation_scope": "portfolio_batch",
                    }
                    normalized_child = _json(child_input)
                    child_hash = hashlib.sha256(normalized_child.encode("utf-8")).hexdigest()
                    connection.execute(
                        """
                        INSERT INTO agent_runs (
                            id, tenant_id, user_id, intent, input_json, input_hash,
                            idempotency_key, status, cancel_requested, created_at,
                            updated_at, parent_run_id, profile_version_id
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'queued', 0, ?, ?, NULL, ?)
                        """,
                        (
                            run_id,
                            tenant_id,
                            user_id,
                            intent,
                            normalized_child,
                            child_hash,
                            now,
                            now,
                            profile_version_id,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO agent_batch_items (batch_id, run_id, sequence_no, code)
                        VALUES (?, ?, ?, ?)
                        """,
                        (batch_id, run_id, sequence_no, code),
                    )
                    self._append_audit(
                        connection,
                        run_id,
                        "run.created",
                        {
                            "intent": intent,
                            "input_hash": child_hash,
                            "status": "queued",
                            "batch_id": batch_id,
                            "batch_input_hash": input_hash,
                            "batch_sequence_no": sequence_no,
                            "profile_version_id": profile_version_id,
                        },
                        actor_type="user",
                        actor_id=user_id,
                    )
                created = True
        batch = self.get_batch(batch_id)
        if batch is None:
            raise RuntimeError(f"Agent Batch 创建后不可读取:{batch_id}")
        return batch, created

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            batch_row = connection.execute(
                "SELECT * FROM agent_batches WHERE id=?",
                (batch_id,),
            ).fetchone()
            batch = self._batch_from_row(batch_row)
            if batch is None:
                return None
            rows = connection.execute(
                """
                SELECT runs.*, items.sequence_no AS batch_sequence_no,
                       items.code AS batch_code
                FROM agent_batch_items AS items
                JOIN agent_runs AS runs ON runs.id=items.run_id
                WHERE items.batch_id=?
                ORDER BY items.sequence_no
                """,
                (batch_id,),
            ).fetchall()
            allocation_row = connection.execute(
                "SELECT * FROM agent_batch_allocation_events WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            purchase_preflight_row = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_preflight_events
                WHERE batch_id=?
                ORDER BY sequence_no DESC
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
            purchase_execution_row = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_execution_events
                WHERE batch_id=?
                ORDER BY sequence_no DESC
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
        items = []
        for row in rows:
            run = self._run_from_row(row)
            sequence_no = int(run.pop("batch_sequence_no"))
            code = str(run.pop("batch_code"))
            items.append({"sequence_no": sequence_no, "code": code, "run": run})
        batch["items"] = items
        batch["allocation_event"] = self._batch_allocation_from_row(allocation_row)
        batch["purchase_preflight_event"] = self._batch_purchase_preflight_from_row(
            purchase_preflight_row
        )
        batch["purchase_execution_event"] = self._batch_purchase_execution_from_row(
            purchase_execution_row
        )
        return batch

    def create_batch_allocation_event(
        self,
        batch_id: str,
        payload: dict[str, Any],
        *,
        user_id: str,
        actor_id: str,
    ) -> tuple[dict[str, Any], bool]:
        schema_version = str(payload.get("schema_version") or "")
        strategy_id = str(payload.get("strategy_id") or "")
        strategy_version = str(payload.get("strategy_version") or "")
        bindings = payload.get("bindings") or {}
        batch_input_hash = str(bindings.get("batch_input_sha256") or "")
        run_set_hash = str(bindings.get("run_set_sha256") or "")
        if (
            not schema_version
            or not strategy_id
            or not strategy_version
            or len(batch_input_hash) != 64
            or len(run_set_hash) != 64
        ):
            raise ValueError("批次资金分配快照缺少版本或证据绑定")

        payload_json = _json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch_row = connection.execute(
                "SELECT * FROM agent_batches WHERE id=? AND user_id=?",
                (str(batch_id), str(user_id)),
            ).fetchone()
            if batch_row is None:
                raise KeyError(f"Agent Batch 不存在:{batch_id}")
            if str(batch_row["input_hash"] or "") != batch_input_hash:
                raise ValueError("批次资金分配绑定的输入哈希已变化")
            if str(bindings.get("batch_id") or "") != str(batch_id):
                raise ValueError("批次资金分配绑定了错误的 Batch ID")

            existing = connection.execute(
                "SELECT * FROM agent_batch_allocation_events WHERE batch_id=?",
                (str(batch_id),),
            ).fetchone()
            if existing is not None:
                parsed = self._batch_allocation_from_row(existing)
                if not parsed or not parsed.get("integrity_verified"):
                    raise ValueError("已保存的批次资金分配事件完整性失败")
                if parsed.get("payload_sha256") != payload_sha256:
                    raise ValueError("该 Batch 已绑定另一份不可变资金分配快照")
                return parsed, False

            run_rows = connection.execute(
                """
                SELECT items.sequence_no, items.code, runs.id, runs.status, runs.result_json
                FROM agent_batch_items AS items
                JOIN agent_runs AS runs ON runs.id=items.run_id
                WHERE items.batch_id=?
                ORDER BY items.sequence_no
                """,
                (str(batch_id),),
            ).fetchall()
            run_set = []
            for row in run_rows:
                result = _load(row["result_json"], None)
                run_set.append({
                    "sequence_no": int(row["sequence_no"]),
                    "code": str(row["code"]),
                    "run_id": str(row["id"]),
                    "status": str(row["status"]),
                    "result_sha256": (
                        hashlib.sha256(_json(result).encode("utf-8")).hexdigest()
                        if isinstance(result, dict) else ""
                    ),
                })
            actual_run_set_hash = hashlib.sha256(
                _json(run_set).encode("utf-8")
            ).hexdigest()
            if actual_run_set_hash != run_set_hash or run_set != (bindings.get("run_set") or []):
                raise ValueError("批次子 Run 集合或结果哈希已变化")
            if not run_rows or any(
                str(row["status"]) not in RUN_TERMINAL_STATUSES for row in run_rows
            ):
                raise ValueError("只有全部子 Run 到达终态后才能固化组合资金分配")

            event_id = _new_id("batch_allocation")
            created_at = _utc_now()
            canonical = {
                "id": event_id,
                "batch_id": str(batch_id),
                "tenant_id": str(batch_row["tenant_id"]),
                "user_id": str(user_id),
                "sequence_no": 1,
                "event_type": "batch_allocation.created",
                "schema_version": schema_version,
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "batch_input_hash": batch_input_hash,
                "run_set_hash": run_set_hash,
                "payload": payload,
                "payload_sha256": payload_sha256,
                "previous_hash": None,
                "actor_id": str(actor_id or "anonymous"),
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO agent_batch_allocation_events (
                    id, batch_id, tenant_id, user_id, sequence_no, event_type,
                    schema_version, strategy_id, strategy_version, batch_input_hash,
                    run_set_hash, payload_json, payload_sha256, previous_hash,
                    event_hash, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(batch_id),
                    str(batch_row["tenant_id"]),
                    str(user_id),
                    1,
                    "batch_allocation.created",
                    schema_version,
                    strategy_id,
                    strategy_version,
                    batch_input_hash,
                    run_set_hash,
                    payload_json,
                    payload_sha256,
                    None,
                    event_hash,
                    str(actor_id or "anonymous"),
                    created_at,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM agent_batch_allocation_events WHERE id=?",
                (event_id,),
            ).fetchone()
        parsed = self._batch_allocation_from_row(stored)
        if parsed is None or not parsed.get("integrity_verified"):
            raise RuntimeError("批次资金分配事件保存后完整性校验失败")
        return parsed, True

    def append_batch_purchase_preflight_event(
        self,
        batch_id: str,
        payload: dict[str, Any],
        *,
        user_id: str,
        actor_id: str,
        expected_previous_event_hash: str | None,
    ) -> tuple[dict[str, Any], bool]:
        schema_version = str(payload.get("schema_version") or "")
        strategy_id = str(payload.get("strategy_id") or "")
        strategy_version = str(payload.get("strategy_version") or "")
        bindings = payload.get("bindings") or {}
        allocation_event_id = str(bindings.get("allocation_event_id") or "")
        allocation_event_hash = str(bindings.get("allocation_event_hash") or "")
        allocation_payload_sha256 = str(
            bindings.get("allocation_payload_sha256") or ""
        )
        request_sha256 = str(bindings.get("request_sha256") or "")
        if (
            not schema_version
            or not strategy_id
            or not strategy_version
            or not allocation_event_id
            or len(allocation_event_hash) != 64
            or len(allocation_payload_sha256) != 64
            or len(request_sha256) != 64
        ):
            raise ValueError("批量申购复核缺少策略版本、分配事件或请求哈希绑定")

        payload_json = _json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch_row = connection.execute(
                "SELECT * FROM agent_batches WHERE id=? AND user_id=?",
                (str(batch_id), str(user_id)),
            ).fetchone()
            if batch_row is None:
                raise KeyError(f"Agent Batch 不存在:{batch_id}")
            if str(bindings.get("batch_id") or "") != str(batch_id):
                raise ValueError("批量申购复核绑定了错误的 Batch ID")
            if str(bindings.get("batch_input_sha256") or "") != str(
                batch_row["input_hash"] or ""
            ):
                raise ValueError("批量申购复核绑定的 Batch 输入哈希已变化")

            allocation_row = connection.execute(
                """
                SELECT * FROM agent_batch_allocation_events
                WHERE id=? AND batch_id=? AND user_id=?
                """,
                (allocation_event_id, str(batch_id), str(user_id)),
            ).fetchone()
            allocation = self._batch_allocation_from_row(allocation_row)
            if allocation is None or not allocation.get("integrity_verified"):
                raise ValueError("绑定的组合资金分配事件不存在或完整性失败")
            if (
                allocation.get("event_hash") != allocation_event_hash
                or allocation.get("payload_sha256") != allocation_payload_sha256
            ):
                raise ValueError("组合资金分配事件哈希已变化")

            duplicate = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_preflight_events
                WHERE batch_id=? AND request_sha256=?
                """,
                (str(batch_id), request_sha256),
            ).fetchone()
            if duplicate is not None:
                parsed_duplicate = self._batch_purchase_preflight_from_row(duplicate)
                if not parsed_duplicate or not parsed_duplicate.get("integrity_verified"):
                    raise ValueError("已保存的同请求申购复核事件完整性失败")
                return parsed_duplicate, False

            execution_started = connection.execute(
                """
                SELECT 1 FROM agent_batch_purchase_execution_events
                WHERE batch_id=?
                LIMIT 1
                """,
                (str(batch_id),),
            ).fetchone()
            if execution_started is not None:
                raise ValueError("批次已回填真实成交，不能再创建新的执行前复核")

            previous = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_preflight_events
                WHERE batch_id=?
                ORDER BY sequence_no DESC
                LIMIT 1
                """,
                (str(batch_id),),
            ).fetchone()
            previous_hash = str(previous["event_hash"]) if previous else None
            expected_previous = (
                str(expected_previous_event_hash) if expected_previous_event_hash else None
            )
            if previous_hash != expected_previous:
                raise ValueError("批量申购复核已产生新版本，请刷新后重新核对")
            sequence_no = int(previous["sequence_no"] if previous else 0) + 1
            event_id = _new_id("batch_purchase_preflight")
            created_at = _utc_now()
            canonical = {
                "id": event_id,
                "batch_id": str(batch_id),
                "tenant_id": str(batch_row["tenant_id"]),
                "user_id": str(user_id),
                "sequence_no": sequence_no,
                "event_type": "batch_purchase_preflight.created",
                "schema_version": schema_version,
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "allocation_event_id": allocation_event_id,
                "allocation_event_hash": allocation_event_hash,
                "allocation_payload_sha256": allocation_payload_sha256,
                "request_sha256": request_sha256,
                "payload": payload,
                "payload_sha256": payload_sha256,
                "previous_hash": previous_hash,
                "actor_id": str(actor_id or "anonymous"),
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO agent_batch_purchase_preflight_events (
                    id, batch_id, tenant_id, user_id, sequence_no, event_type,
                    schema_version, strategy_id, strategy_version,
                    allocation_event_id, allocation_event_hash,
                    allocation_payload_sha256, request_sha256, payload_json,
                    payload_sha256, previous_hash, event_hash, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(batch_id),
                    str(batch_row["tenant_id"]),
                    str(user_id),
                    sequence_no,
                    "batch_purchase_preflight.created",
                    schema_version,
                    strategy_id,
                    strategy_version,
                    allocation_event_id,
                    allocation_event_hash,
                    allocation_payload_sha256,
                    request_sha256,
                    payload_json,
                    payload_sha256,
                    previous_hash,
                    event_hash,
                    str(actor_id or "anonymous"),
                    created_at,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM agent_batch_purchase_preflight_events WHERE id=?",
                (event_id,),
            ).fetchone()
        parsed = self._batch_purchase_preflight_from_row(stored)
        if parsed is None or not parsed.get("integrity_verified"):
            raise RuntimeError("批量申购复核事件保存后完整性校验失败")
        return parsed, True

    def verify_batch_purchase_preflight_audit(
        self,
        batch_id: str,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            owner = connection.execute(
                "SELECT id FROM agent_batches WHERE id=? AND user_id=?",
                (str(batch_id), str(user_id)),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_preflight_events
                WHERE batch_id=? AND user_id=?
                ORDER BY sequence_no
                """,
                (str(batch_id), str(user_id)),
            ).fetchall()
        if owner is None:
            return {
                "verified": False,
                "event_count": 0,
                "chain_head": None,
                "reason": "batch_not_found",
            }
        previous_hash = None
        for expected_sequence, row in enumerate(rows, start=1):
            item = self._batch_purchase_preflight_from_row(row)
            if item is None or not item.get("integrity_verified"):
                return {
                    "verified": False,
                    "event_count": len(rows),
                    "chain_head": previous_hash,
                    "failing_sequence": expected_sequence,
                    "reason": "event_integrity_failed",
                }
            if (
                int(item.get("sequence_no") or 0) != expected_sequence
                or item.get("previous_hash") != previous_hash
            ):
                return {
                    "verified": False,
                    "event_count": len(rows),
                    "chain_head": previous_hash,
                    "failing_sequence": expected_sequence,
                    "reason": "event_chain_broken",
                }
            previous_hash = item.get("event_hash")
        return {
            "verified": bool(rows),
            "event_count": len(rows),
            "chain_head": previous_hash,
            "failing_sequence": None,
            "reason": None if rows else "purchase_preflight_events_missing",
        }

    def append_batch_purchase_execution_event(
        self,
        batch_id: str,
        payload: dict[str, Any],
        *,
        user_id: str,
        actor_id: str,
        expected_previous_event_hash: str | None,
        transaction_bindings: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        schema_version = str(payload.get("schema_version") or "")
        strategy_id = str(payload.get("strategy_id") or "")
        strategy_version = str(payload.get("strategy_version") or "")
        event_type = str(payload.get("event_type") or "")
        bindings = payload.get("bindings") or {}
        preflight_event_id = str(bindings.get("preflight_event_id") or "")
        preflight_event_hash = str(bindings.get("preflight_event_hash") or "")
        preflight_payload_sha256 = str(bindings.get("preflight_payload_sha256") or "")
        request_sha256 = str(bindings.get("request_sha256") or "")
        if (
            event_type not in {"purchases_recorded", "holdings_reconciled"}
            or not schema_version
            or not strategy_id
            or not strategy_version
            or not preflight_event_id
            or len(preflight_event_hash) != 64
            or len(preflight_payload_sha256) != 64
            or len(request_sha256) != 64
        ):
            raise ValueError("批次申购执行事件缺少策略版本、事件类型或证据哈希绑定")

        normalized_transaction_bindings: list[tuple[int, str]] = []
        for item in transaction_bindings:
            transaction_id = int(item.get("transaction_id") or 0)
            transaction_sha256 = str(item.get("transaction_sha256") or "")
            if transaction_id <= 0 or len(transaction_sha256) != 64:
                raise ValueError("批次申购执行事件包含无效的交易流水绑定")
            normalized_transaction_bindings.append((transaction_id, transaction_sha256))
        if len({item[0] for item in normalized_transaction_bindings}) != len(
            normalized_transaction_bindings
        ):
            raise ValueError("同一笔交易流水不能在一个执行事件中重复绑定")

        payload_json = _json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch_row = connection.execute(
                "SELECT * FROM agent_batches WHERE id=? AND user_id=?",
                (str(batch_id), str(user_id)),
            ).fetchone()
            if batch_row is None:
                raise KeyError(f"Agent Batch 不存在：{batch_id}")
            if str(bindings.get("batch_id") or "") != str(batch_id):
                raise ValueError("批次申购执行事件绑定了错误的 Batch ID")
            if str(bindings.get("batch_input_sha256") or "") != str(
                batch_row["input_hash"] or ""
            ):
                raise ValueError("批次申购执行事件绑定的 Batch 输入哈希已变化")

            preflight_row = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_preflight_events
                WHERE id=? AND batch_id=? AND user_id=?
                """,
                (preflight_event_id, str(batch_id), str(user_id)),
            ).fetchone()
            preflight = self._batch_purchase_preflight_from_row(preflight_row)
            latest_preflight_row = connection.execute(
                """
                SELECT id FROM agent_batch_purchase_preflight_events
                WHERE batch_id=? ORDER BY sequence_no DESC LIMIT 1
                """,
                (str(batch_id),),
            ).fetchone()
            if (
                preflight is None
                or not preflight.get("integrity_verified")
                or latest_preflight_row is None
                or str(latest_preflight_row["id"]) != preflight_event_id
            ):
                raise ValueError("绑定的批次申购执行前复核不是当前完整版本")
            if (
                preflight.get("event_hash") != preflight_event_hash
                or preflight.get("payload_sha256") != preflight_payload_sha256
            ):
                raise ValueError("绑定的批次申购执行前复核哈希已变化")

            duplicate = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_execution_events
                WHERE batch_id=? AND request_sha256=?
                """,
                (str(batch_id), request_sha256),
            ).fetchone()
            if duplicate is not None:
                parsed_duplicate = self._batch_purchase_execution_from_row(duplicate)
                if not parsed_duplicate or not parsed_duplicate.get("integrity_verified"):
                    raise ValueError("已保存的同请求批次执行事件完整性失败")
                return parsed_duplicate, False

            previous = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_execution_events
                WHERE batch_id=? ORDER BY sequence_no DESC LIMIT 1
                """,
                (str(batch_id),),
            ).fetchone()
            previous_hash = str(previous["event_hash"]) if previous else None
            expected_previous = (
                str(expected_previous_event_hash) if expected_previous_event_hash else None
            )
            if previous_hash != expected_previous:
                raise ValueError("批次申购执行链已产生新版本，请刷新后重新核对")
            previous_type = str(previous["event_type"]) if previous else None
            if previous_type == "holdings_reconciled":
                raise ValueError("批次真实持仓已完成对账，执行链不能继续修改")
            if event_type == "holdings_reconciled":
                if previous is None or previous_type != "purchases_recorded":
                    raise ValueError("必须先回填最新一版真实申购成交才能进行持仓对账")
                if (
                    str(bindings.get("purchase_event_id") or "") != str(previous["id"])
                    or str(bindings.get("purchase_event_hash") or "") != previous_hash
                ):
                    raise ValueError("持仓对账未绑定最新一版真实申购成交事件")

            table_names = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if normalized_transaction_bindings and "portfolio_transactions" not in table_names:
                raise ValueError("真实交易流水表尚未初始化")
            for transaction_id, expected_transaction_hash in normalized_transaction_bindings:
                transaction_row = connection.execute(
                    """
                    SELECT id, user_id, asset_type, market, code, name, trade_type,
                           trade_date, shares, unit_price, fee,
                           COALESCE(source, 'manual') AS source, created_at
                    FROM portfolio_transactions
                    WHERE id=? AND user_id=?
                    """,
                    (transaction_id, str(user_id)),
                ).fetchone()
                if transaction_row is None:
                    raise ValueError("绑定的真实交易流水不存在或不属于当前用户")
                actual_transaction_hash = hashlib.sha256(
                    _json(dict(transaction_row)).encode("utf-8")
                ).hexdigest()
                if actual_transaction_hash != expected_transaction_hash:
                    raise ValueError("绑定的真实交易流水内容哈希已变化")
                if "fund_switch_lifecycle_events" in table_names:
                    switch_binding = connection.execute(
                        """
                        SELECT 1 FROM fund_switch_lifecycle_events
                        WHERE user_id=? AND event_type='purchase_recorded'
                          AND purchase_transaction_id=?
                        LIMIT 1
                        """,
                        (str(user_id), transaction_id),
                    ).fetchone()
                    if switch_binding is not None:
                        raise ValueError("该真实申购流水已绑定基金替换批次")
                existing_binding = connection.execute(
                    """
                    SELECT * FROM agent_batch_purchase_transaction_bindings
                    WHERE user_id=? AND transaction_id=?
                    """,
                    (str(user_id), transaction_id),
                ).fetchone()
                if existing_binding is not None and (
                    str(existing_binding["batch_id"]) != str(batch_id)
                    or str(existing_binding["transaction_sha256"]) != expected_transaction_hash
                ):
                    raise ValueError("该真实申购流水已绑定其他批次或内容哈希冲突")

            sequence_no = int(previous["sequence_no"] if previous else 0) + 1
            event_id = _new_id("batch_purchase_execution")
            created_at = _utc_now()
            canonical = {
                "id": event_id,
                "batch_id": str(batch_id),
                "tenant_id": str(batch_row["tenant_id"]),
                "user_id": str(user_id),
                "sequence_no": sequence_no,
                "event_type": event_type,
                "schema_version": schema_version,
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "preflight_event_id": preflight_event_id,
                "preflight_event_hash": preflight_event_hash,
                "preflight_payload_sha256": preflight_payload_sha256,
                "request_sha256": request_sha256,
                "payload": payload,
                "payload_sha256": payload_sha256,
                "previous_hash": previous_hash,
                "actor_id": str(actor_id or "anonymous"),
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO agent_batch_purchase_execution_events (
                    id, batch_id, tenant_id, user_id, sequence_no, event_type,
                    schema_version, strategy_id, strategy_version,
                    preflight_event_id, preflight_event_hash,
                    preflight_payload_sha256, request_sha256, payload_json,
                    payload_sha256, previous_hash, event_hash, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(batch_id),
                    str(batch_row["tenant_id"]),
                    str(user_id),
                    sequence_no,
                    event_type,
                    schema_version,
                    strategy_id,
                    strategy_version,
                    preflight_event_id,
                    preflight_event_hash,
                    preflight_payload_sha256,
                    request_sha256,
                    payload_json,
                    payload_sha256,
                    previous_hash,
                    event_hash,
                    str(actor_id or "anonymous"),
                    created_at,
                ),
            )
            for transaction_id, transaction_sha256 in normalized_transaction_bindings:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO agent_batch_purchase_transaction_bindings (
                        user_id, transaction_id, batch_id, first_event_id,
                        transaction_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(user_id),
                        transaction_id,
                        str(batch_id),
                        event_id,
                        transaction_sha256,
                        created_at,
                    ),
                )
            stored = connection.execute(
                "SELECT * FROM agent_batch_purchase_execution_events WHERE id=?",
                (event_id,),
            ).fetchone()
        parsed = self._batch_purchase_execution_from_row(stored)
        if parsed is None or not parsed.get("integrity_verified"):
            raise RuntimeError("批次申购执行事件保存后完整性校验失败")
        return parsed, True

    def list_batch_purchase_execution_events(
        self,
        batch_id: str,
        *,
        user_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_execution_events
                WHERE batch_id=? AND user_id=? ORDER BY sequence_no
                """,
                (str(batch_id), str(user_id)),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._batch_purchase_execution_from_row(row)) is not None
        ]

    def get_batch_purchase_transaction_binding(
        self,
        transaction_id: int,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_transaction_bindings
                WHERE user_id=? AND transaction_id=?
                """,
                (str(user_id), int(transaction_id)),
            ).fetchone()
        return dict(row) if row else None

    def list_batch_purchase_transaction_bindings(
        self,
        *,
        user_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_transaction_bindings
                WHERE user_id=? ORDER BY transaction_id
                """,
                (str(user_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def verify_batch_purchase_execution_audit(
        self,
        batch_id: str,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            owner = connection.execute(
                "SELECT id FROM agent_batches WHERE id=? AND user_id=?",
                (str(batch_id), str(user_id)),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT * FROM agent_batch_purchase_execution_events
                WHERE batch_id=? AND user_id=? ORDER BY sequence_no
                """,
                (str(batch_id), str(user_id)),
            ).fetchall()
        if owner is None:
            return {
                "verified": False,
                "event_count": 0,
                "chain_head": None,
                "reason": "batch_not_found",
            }
        previous_hash = None
        for expected_sequence, row in enumerate(rows, start=1):
            item = self._batch_purchase_execution_from_row(row)
            if item is None or not item.get("integrity_verified"):
                return {
                    "verified": False,
                    "event_count": len(rows),
                    "chain_head": previous_hash,
                    "failing_sequence": expected_sequence,
                    "reason": "event_integrity_failed",
                }
            if (
                int(item.get("sequence_no") or 0) != expected_sequence
                or item.get("previous_hash") != previous_hash
            ):
                return {
                    "verified": False,
                    "event_count": len(rows),
                    "chain_head": previous_hash,
                    "failing_sequence": expected_sequence,
                    "reason": "event_chain_broken",
                }
            previous_hash = item.get("event_hash")
        return {
            "verified": bool(rows),
            "event_count": len(rows),
            "chain_head": previous_hash,
            "failing_sequence": None,
            "reason": None if rows else "purchase_execution_events_missing",
        }

    def get_batch_by_idempotency_key(
        self,
        user_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM agent_batches WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
        return self.get_batch(str(row["id"])) if row else None

    def list_batches(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(30, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM agent_batches
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (tenant_id, user_id, page_size),
            ).fetchall()
        return [batch for row in rows if (batch := self.get_batch(str(row["id"]))) is not None]

    def create_run(
        self,
        intent: str,
        input_payload: dict[str, Any],
        *,
        tenant_id: str = "public",
        user_id: str = "anonymous",
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
        profile_version_id: str | None = None,
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
                    updated_at, parent_run_id, profile_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
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
                    profile_version_id,
                ),
            )
            self._append_audit(
                connection,
                run_id,
                "run.created",
                {
                    "intent": intent,
                    "input_hash": input_hash,
                    "status": "queued",
                    "parent_run_id": parent_run_id,
                    "profile_version_id": profile_version_id,
                },
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

    def bind_exposure_snapshot(self, run_id: str, snapshot_id: str) -> dict[str, Any]:
        """Bind exactly one immutable portfolio exposure snapshot to a Run."""
        snapshot_id = str(snapshot_id or "").strip()
        if not snapshot_id:
            raise ValueError("exposure snapshot id is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT exposure_snapshot_id, status FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Agent Run 不存在")
            existing = str(row["exposure_snapshot_id"] or "")
            if existing and existing != snapshot_id:
                raise ValueError("Agent Run 已绑定不同的组合穿透快照")
            if not existing:
                connection.execute(
                    "UPDATE agent_runs SET exposure_snapshot_id=?, updated_at=? WHERE id=?",
                    (snapshot_id, _utc_now(), run_id),
                )
                self._append_audit(
                    connection,
                    run_id,
                    "run.exposure_snapshot_bound",
                    {"exposure_snapshot_id": snapshot_id},
                )
            updated = connection.execute(
                "SELECT * FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return self._run_from_row(updated)

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

    def list_runs(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int,
        before: tuple[str, str] | None = None,
        status: str | None = None,
        code: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        page_size = max(1, min(100, int(limit)))
        conditions = ["tenant_id=?", "user_id=?"]
        parameters: list[Any] = [tenant_id, user_id]
        if before:
            conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
            parameters.extend([before[0], before[0], before[1]])
        if status:
            conditions.append("status=?")
            parameters.append(status)
        if code:
            conditions.append("json_extract(input_json, '$.code')=?")
            parameters.append(code)
        parameters.append(page_size + 1)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM agent_runs
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        has_more = len(rows) > page_size
        return [self._run_from_row(row) for row in rows[:page_size]], has_more

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

    def add_post_run_evidence(
        self,
        run_id: str,
        *,
        evidence_type: str,
        subject_type: str,
        subject_id: str,
        provider: str,
        source_url: str | None,
        as_of: str,
        schema_version: str,
        quality_status: str,
        payload: dict[str, Any],
        actor_type: str = "user",
        actor_id: str = "anonymous",
    ) -> tuple[dict[str, Any], bool]:
        """Append immutable evidence after a Run completes, idempotent per observed snapshot."""
        if not str(as_of or "").strip():
            raise ValueError("追加 Evidence 必须提供数据截止时间")
        now = _utc_now()
        payload_json = _json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Agent Run 不存在:{run_id}")
            if run["status"] not in RUN_TERMINAL_STATUSES:
                raise ValueError("只有终态 Agent Run 才能追加结果评估 Evidence")
            existing = connection.execute(
                """
                SELECT * FROM agent_evidence
                WHERE run_id=? AND evidence_type=? AND as_of=? AND schema_version=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (run_id, evidence_type, as_of, schema_version),
            ).fetchone()
            if existing is not None:
                return self._evidence_from_row(existing, include_payload=True), False

            evidence_id = _new_id("ev")
            connection.execute(
                """
                INSERT INTO agent_evidence (
                    id, run_id, step_id, evidence_type, subject_type, subject_id,
                    provider, source_url, observed_at, as_of, schema_version,
                    quality_status, payload_json, payload_sha256, created_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    run_id,
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
            self._append_audit(
                connection,
                run_id,
                "evidence.created",
                {
                    "evidence_id": evidence_id,
                    "step_id": None,
                    "payload_sha256": payload_hash,
                    "quality_status": quality_status,
                },
                actor_type=actor_type,
                actor_id=actor_id,
            )
            self._append_audit(
                connection,
                run_id,
                "outcome.evaluation.created",
                {
                    "evidence_id": evidence_id,
                    "evidence_type": evidence_type,
                    "as_of": as_of,
                    "schema_version": schema_version,
                },
                actor_type=actor_type,
                actor_id=actor_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_evidence WHERE id=?",
                (evidence_id,),
            ).fetchone()
        return self._evidence_from_row(row, include_payload=True), True

    def list_evidence_by_type(
        self,
        run_id: str,
        evidence_type: str,
        *,
        include_payload: bool = True,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_evidence
                WHERE run_id=? AND evidence_type=?
                ORDER BY as_of DESC, created_at DESC, id DESC
                """,
                (run_id, evidence_type),
            ).fetchall()
        return [
            self._evidence_from_row(row, include_payload=include_payload)
            for row in rows
        ]

    def get_outcome_schedule(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return self._outcome_schedule_from_row(row)

    def list_unscheduled_terminal_runs(
        self,
        *,
        actions: tuple[str, ...],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not actions:
            return []
        placeholders = ",".join("?" for _ in actions)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT runs.*
                FROM agent_runs AS runs
                LEFT JOIN agent_outcome_schedules AS schedules ON schedules.run_id=runs.id
                WHERE schedules.id IS NULL
                  AND runs.intent='fund_deep_research'
                  AND runs.status IN ('completed', 'partial')
                  AND runs.result_json IS NOT NULL
                  AND json_extract(
                      runs.result_json,
                      '$.personalized_decision.decision.action'
                  ) IN ({placeholders})
                ORDER BY runs.completed_at DESC, runs.id DESC
                LIMIT ?
                """,
                (*actions, max(1, min(int(limit), 1000))),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def ensure_outcome_schedule(
        self,
        run_id: str,
        *,
        interval_hours: int = 24,
        actor_type: str = "system",
        actor_id: str = "agent-runtime-v1",
        now: str | dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create an active schedule once without overriding a later user pause."""
        interval = int(interval_hours)
        if interval < 12 or interval > 168:
            raise ValueError("结果观察间隔必须在 12 至 168 小时之间")
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        next_run_at = _utc_iso(now_dt + dt.timedelta(hours=interval))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                return self._outcome_schedule_from_row(existing), False
            run = connection.execute(
                "SELECT tenant_id, user_id FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Agent Run 不存在:{run_id}")
            schedule_id = _new_id("schedule")
            connection.execute(
                """
                INSERT INTO agent_outcome_schedules (
                    id, run_id, tenant_id, user_id, status, interval_hours,
                    next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    run_id,
                    run["tenant_id"],
                    run["user_id"],
                    interval,
                    next_run_at,
                    now_text,
                    now_text,
                ),
            )
            self._append_audit(
                connection,
                run_id,
                "outcome.schedule.created",
                {
                    "schedule_id": schedule_id,
                    "interval_hours": interval,
                    "next_run_at": next_run_at,
                    "status": "active",
                },
                actor_type=actor_type,
                actor_id=actor_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
        return self._outcome_schedule_from_row(row), True

    def configure_outcome_schedule(
        self,
        run_id: str,
        *,
        enabled: bool,
        interval_hours: int = 24,
        run_immediately: bool = False,
        actor_id: str = "anonymous",
        now: str | dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist a user's schedule setting and preserve it across process restarts."""
        interval = int(interval_hours)
        if interval < 12 or interval > 168:
            raise ValueError("结果观察间隔必须在 12 至 168 小时之间")
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        next_run_at = (
            now_text
            if enabled and run_immediately
            else _utc_iso(now_dt + dt.timedelta(hours=interval)) if enabled else None
        )
        desired_status = "active" if enabled else "paused"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT tenant_id, user_id FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Agent Run 不存在:{run_id}")
            existing = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE run_id=?",
                (run_id,),
            ).fetchone()
            changed = (
                existing is None
                or existing["status"] != desired_status
                or int(existing["interval_hours"]) != interval
                or bool(run_immediately and enabled)
            )
            if existing is None:
                schedule_id = _new_id("schedule")
                connection.execute(
                    """
                    INSERT INTO agent_outcome_schedules (
                        id, run_id, tenant_id, user_id, status, interval_hours,
                        next_run_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        schedule_id,
                        run_id,
                        run["tenant_id"],
                        run["user_id"],
                        desired_status,
                        interval,
                        next_run_at,
                        now_text,
                        now_text,
                    ),
                )
                event_type = "outcome.schedule.created"
            else:
                schedule_id = existing["id"]
                if changed:
                    if enabled:
                        if existing["status"] == "paused":
                            connection.execute(
                                """
                                UPDATE agent_outcome_schedules
                                SET status=?, interval_hours=?, next_run_at=?,
                                    consecutive_failures=0, last_error_code=NULL,
                                    last_error_message=NULL, updated_at=?
                                WHERE id=?
                                """,
                                (desired_status, interval, next_run_at, now_text, schedule_id),
                            )
                        else:
                            connection.execute(
                                """
                                UPDATE agent_outcome_schedules
                                SET status=?, interval_hours=?, next_run_at=?, updated_at=?
                                WHERE id=?
                                """,
                                (desired_status, interval, next_run_at, now_text, schedule_id),
                            )
                    else:
                        connection.execute(
                            """
                            UPDATE agent_outcome_schedules
                            SET status=?, interval_hours=?, next_run_at=NULL,
                                lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                            WHERE id=?
                            """,
                            (desired_status, interval, now_text, schedule_id),
                        )
                event_type = "outcome.schedule.resumed" if enabled else "outcome.schedule.paused"
            if changed:
                self._append_audit(
                    connection,
                    run_id,
                    event_type,
                    {
                        "schedule_id": schedule_id,
                        "interval_hours": interval,
                        "next_run_at": next_run_at,
                        "status": desired_status,
                        "run_immediately": bool(run_immediately),
                    },
                    actor_type="user",
                    actor_id=actor_id,
                )
            row = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
        return self._outcome_schedule_from_row(row), changed

    def claim_due_outcome_schedule(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any] | None:
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        lease_expires_at = _utc_iso(now_dt + dt.timedelta(seconds=max(60, int(lease_seconds))))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM agent_outcome_schedules
                WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at<=?
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                ORDER BY next_run_at, id
                LIMIT 1
                """,
                (now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE agent_outcome_schedules
                SET lease_owner=?, lease_expires_at=?, last_started_at=?,
                    attempt_count=attempt_count+1, updated_at=?
                WHERE id=? AND status='active'
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                """,
                (worker_id, lease_expires_at, now_text, now_text, row["id"], now_text),
            ).rowcount
            if changed != 1:
                return None
            self._append_audit(
                connection,
                row["run_id"],
                "outcome.schedule.started",
                {
                    "schedule_id": row["id"],
                    "worker_id": worker_id,
                    "lease_expires_at": lease_expires_at,
                },
                actor_id=worker_id,
            )
            claimed = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (row["id"],),
            ).fetchone()
        return self._outcome_schedule_from_row(claimed)

    def complete_outcome_schedule(
        self,
        schedule_id: str,
        worker_id: str,
        *,
        provider_as_of: str,
        evidence_id: str,
        evidence_created: bool,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any]:
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedule = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            if schedule is None:
                raise KeyError(f"结果观察计划不存在:{schedule_id}")
            next_run_at = _utc_iso(
                now_dt + dt.timedelta(hours=int(schedule["interval_hours"]))
            )
            changed = connection.execute(
                """
                UPDATE agent_outcome_schedules
                SET next_run_at=?, lease_owner=NULL, lease_expires_at=NULL,
                    consecutive_failures=0, last_finished_at=?, last_success_at=?,
                    last_provider_as_of=?, last_evidence_id=?, last_error_code=NULL,
                    last_error_message=NULL, updated_at=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    next_run_at,
                    now_text,
                    now_text,
                    str(provider_as_of),
                    evidence_id,
                    now_text,
                    schedule_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("结果观察计划租约已失效，拒绝提交完成状态")
            self._append_audit(
                connection,
                schedule["run_id"],
                "outcome.schedule.succeeded",
                {
                    "schedule_id": schedule_id,
                    "worker_id": worker_id,
                    "provider_as_of": str(provider_as_of),
                    "evidence_id": evidence_id,
                    "evidence_created": bool(evidence_created),
                    "next_run_at": next_run_at,
                },
                actor_id=worker_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
        return self._outcome_schedule_from_row(row)

    def fail_outcome_schedule(
        self,
        schedule_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any]:
        retry_delays = (900, 3600, 14400, 43200)
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedule = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            if schedule is None:
                raise KeyError(f"结果观察计划不存在:{schedule_id}")
            failure_count = int(schedule["consecutive_failures"]) + 1
            retry_exhausted = bool(retryable and failure_count > len(retry_delays))
            should_retry = bool(retryable and not retry_exhausted)
            delay = retry_delays[min(failure_count - 1, len(retry_delays) - 1)]
            next_run_at = _utc_iso(now_dt + dt.timedelta(seconds=delay)) if should_retry else None
            next_status = "active" if should_retry else "paused"
            changed = connection.execute(
                """
                UPDATE agent_outcome_schedules
                SET status=?, next_run_at=?, lease_owner=NULL, lease_expires_at=NULL,
                    consecutive_failures=?, last_finished_at=?, last_error_code=?,
                    last_error_message=?, updated_at=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    next_status,
                    next_run_at,
                    failure_count,
                    now_text,
                    str(error_code or "OUTCOME_EVALUATION_FAILED")[:100],
                    str(error_message or "")[:500],
                    now_text,
                    schedule_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("结果观察计划租约已失效，拒绝提交失败状态")
            self._append_audit(
                connection,
                schedule["run_id"],
                "outcome.schedule.failed",
                {
                    "schedule_id": schedule_id,
                    "worker_id": worker_id,
                    "error_code": str(error_code),
                    "retryable": bool(retryable),
                    "retry_exhausted": retry_exhausted,
                    "consecutive_failures": failure_count,
                    "next_run_at": next_run_at,
                    "status": next_status,
                },
                actor_id=worker_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_outcome_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
        return self._outcome_schedule_from_row(row)

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

    def verify_run_evidence_integrity(self, run_id: str) -> dict[str, Any]:
        audit = self.verify_audit_chain(run_id)
        events = self.list_audit_events(run_id)
        evidence_events = [item for item in events if item["event_type"] == "evidence.created"]
        audit_hashes = {
            item["details"].get("evidence_id"): item["details"].get("payload_sha256")
            for item in evidence_events
        }
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_evidence WHERE run_id=? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
            run_row = connection.execute(
                "SELECT exposure_snapshot_id FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            exposure_rows = connection.execute(
                """
                SELECT evidence.*
                FROM agent_evidence AS evidence
                JOIN agent_steps AS steps ON steps.id=evidence.step_id
                WHERE evidence.run_id=? AND steps.step_key='portfolio_exposure'
                """,
                (run_id,),
            ).fetchall()
        evidence_ids = {row["id"] for row in rows}
        event_ids = [item["details"].get("evidence_id") for item in evidence_events]
        if (
            not audit["verified"]
            or not rows
            or len(event_ids) != len(set(event_ids))
            or set(event_ids) != evidence_ids
        ):
            return {
                "verified": False,
                "evidence_count": len(rows),
                "failing_evidence_id": None,
                "audit_verified": audit["verified"],
                "reason": "audit_evidence_mismatch" if audit["verified"] else "audit_chain_invalid",
            }
        for row in rows:
            evidence = self._evidence_from_row(row, include_payload=True)
            evidence_id = evidence["id"]
            if (
                not evidence["integrity_verified"]
                or audit_hashes.get(evidence_id) != evidence["payload_sha256"]
            ):
                return {
                    "verified": False,
                    "evidence_count": len(rows),
                    "failing_evidence_id": evidence_id,
                    "audit_verified": audit["verified"],
                    "reason": "payload_hash_mismatch",
                }
        bound_snapshot_id = str((run_row or {})["exposure_snapshot_id"] or "") if run_row else ""
        binding_events = [
            item for item in events
            if item["event_type"] == "run.exposure_snapshot_bound"
        ]
        if bound_snapshot_id or exposure_rows or binding_events:
            if len(exposure_rows) != 1 or len(binding_events) != 1 or not bound_snapshot_id:
                return {
                    "verified": False,
                    "evidence_count": len(rows),
                    "failing_evidence_id": exposure_rows[0]["id"] if exposure_rows else None,
                    "audit_verified": True,
                    "reason": "exposure_snapshot_binding_mismatch",
                }
            exposure_evidence = self._evidence_from_row(exposure_rows[0], include_payload=True)
            exposure_payload = exposure_evidence.get("payload") or {}
            snapshot = exposure_payload.get("snapshot") or {}
            snapshot_integrity = exposure_payload.get("integrity") or {}
            event_snapshot_id = str(binding_events[0]["details"].get("exposure_snapshot_id") or "")
            if (
                str(snapshot.get("id") or "") != bound_snapshot_id
                or event_snapshot_id != bound_snapshot_id
                or not snapshot_integrity.get("verified")
                or str(snapshot_integrity.get("payload_sha256") or "")
                != str(snapshot.get("payload_sha256") or "")
            ):
                return {
                    "verified": False,
                    "evidence_count": len(rows),
                    "failing_evidence_id": exposure_evidence["id"],
                    "audit_verified": True,
                    "reason": "exposure_snapshot_binding_mismatch",
                }
        return {
            "verified": True,
            "evidence_count": len(rows),
            "failing_evidence_id": None,
            "audit_verified": True,
            "reason": None,
        }

    def register_strategy_version(
        self,
        manifest: dict[str, Any],
        *,
        initial_status: str,
        actor_role: str = "system",
        actor_id: str = "strategy-bootstrap-v1",
    ) -> tuple[dict[str, Any], bool]:
        strategy_id = str(manifest.get("strategy_id") or "").strip()
        strategy_version = str(manifest.get("strategy_version") or "").strip()
        name = str(manifest.get("name") or "").strip()
        strategy_kind = str(manifest.get("strategy_kind") or "").strip()
        owner_id = str(manifest.get("owner_id") or "").strip()
        if not all((strategy_id, strategy_version, name, strategy_kind, owner_id)):
            raise ValueError("策略清单缺少 id、版本、名称、类型或负责人")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", strategy_id):
            raise ValueError("策略 ID 必须是 3-128 位小写稳定标识")
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", strategy_version):
            raise ValueError("策略版本必须使用语义化版本")
        if initial_status not in STRATEGY_STATUSES:
            raise ValueError(f"无效策略状态:{initial_status}")
        if initial_status not in {"draft", "shadow"}:
            raise ValueError("新策略版本只能以 draft 或迁移期 shadow 状态注册")
        manifest_json = _json(manifest)
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                """
                SELECT * FROM agent_strategy_versions
                WHERE strategy_id=? AND strategy_version=?
                """,
                (strategy_id, strategy_version),
            ).fetchone()
            if existing_row is not None:
                existing = self._strategy_from_row(existing_row)
                if existing["manifest_sha256"] != manifest_hash:
                    raise ValueError(
                        f"策略版本内容已存在且哈希不同:{strategy_id}@{strategy_version}"
                    )
                return existing, False
            connection.execute(
                """
                INSERT INTO agent_strategy_versions (
                    strategy_id, strategy_version, name, strategy_kind, owner_id,
                    status, previous_status, manifest_json, manifest_sha256,
                    registered_at, status_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    strategy_version,
                    name,
                    strategy_kind,
                    owner_id,
                    initial_status,
                    manifest_json,
                    manifest_hash,
                    now,
                    now,
                ),
            )
            self._append_strategy_audit(
                connection,
                strategy_id,
                strategy_version,
                "strategy.version.registered",
                {
                    "initial_status": initial_status,
                    "manifest_sha256": manifest_hash,
                    "strategy_kind": strategy_kind,
                },
                actor_role=actor_role,
                actor_id=actor_id,
            )
            created_row = connection.execute(
                """
                SELECT * FROM agent_strategy_versions
                WHERE strategy_id=? AND strategy_version=?
                """,
                (strategy_id, strategy_version),
            ).fetchone()
        return self._strategy_from_row(created_row), True

    def get_strategy_version(
        self,
        strategy_id: str,
        strategy_version: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_strategy_versions
                WHERE strategy_id=? AND strategy_version=?
                """,
                (strategy_id, strategy_version),
            ).fetchone()
        return self._strategy_from_row(row)

    def list_strategy_versions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_strategy_versions
                ORDER BY strategy_id, registered_at DESC, strategy_version DESC
                """
            ).fetchall()
        return [self._strategy_from_row(row) for row in rows]

    def transition_strategy_status(
        self,
        strategy_id: str,
        strategy_version: str,
        *,
        expected_status: str,
        target_status: str,
        actor_role: str,
        actor_id: str,
        reason: str,
        release_assessment: dict[str, Any],
    ) -> dict[str, Any]:
        if target_status not in STRATEGY_STATUSES:
            raise ValueError(f"无效策略状态:{target_status}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM agent_strategy_versions
                WHERE strategy_id=? AND strategy_version=?
                """,
                (strategy_id, strategy_version),
            ).fetchone()
            if row is None:
                raise KeyError(f"策略版本不存在:{strategy_id}@{strategy_version}")
            current_status = str(row["status"])
            if current_status != expected_status:
                raise RuntimeError(
                    f"策略状态已变化，期望 {expected_status}，实际 {current_status}"
                )
            if current_status == target_status:
                return self._strategy_from_row(row)
            now = _utc_now()
            connection.execute(
                """
                UPDATE agent_strategy_versions
                SET previous_status=status, status=?, status_updated_at=?
                WHERE strategy_id=? AND strategy_version=? AND status=?
                """,
                (target_status, now, strategy_id, strategy_version, expected_status),
            )
            self._append_strategy_audit(
                connection,
                strategy_id,
                strategy_version,
                "strategy.status.changed",
                {
                    "from_status": current_status,
                    "to_status": target_status,
                    "reason": reason,
                    "release_assessment": release_assessment,
                },
                actor_role=actor_role,
                actor_id=actor_id,
            )
            updated = connection.execute(
                """
                SELECT * FROM agent_strategy_versions
                WHERE strategy_id=? AND strategy_version=?
                """,
                (strategy_id, strategy_version),
            ).fetchone()
        return self._strategy_from_row(updated)

    def list_strategy_audit_events(
        self,
        strategy_id: str,
        strategy_version: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_strategy_audit_events
                WHERE strategy_id=? AND strategy_version=?
                ORDER BY sequence_no
                """,
                (strategy_id, strategy_version),
            ).fetchall()
        return [self._strategy_audit_from_row(row) for row in rows]

    def verify_strategy_audit_chain(
        self,
        strategy_id: str,
        strategy_version: str,
    ) -> dict[str, Any]:
        items = self.list_strategy_audit_events(strategy_id, strategy_version)
        previous_hash = None
        for expected_sequence, item in enumerate(items, start=1):
            canonical = {
                "id": item["id"],
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "sequence_no": item["sequence_no"],
                "event_type": item["event_type"],
                "actor_role": item["actor_role"],
                "actor_id": item["actor_id"],
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
            "verified": bool(items),
            "event_count": len(items),
            "failing_sequence": None if items else 0,
            "chain_head": previous_hash,
        }

    def list_unenrolled_strategy_shadow_runs(
        self,
        *,
        limit: int = 100,
        after_completed_at: str | None = None,
        after_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return governance-aware runs in chronological order for unbiased backfill."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT runs.*
                FROM agent_runs AS runs
                LEFT JOIN agent_strategy_shadow_enrollments AS enrollments
                    ON enrollments.run_id=runs.id
                WHERE enrollments.id IS NULL
                  AND runs.intent='fund_deep_research'
                  AND runs.status IN ('completed', 'partial')
                  AND runs.result_json IS NOT NULL
                  AND json_extract(runs.result_json, '$.schema_version')
                      IN ('fund_deep_research.v4', 'fund_deep_research.v5', 'fund_deep_research.v6')
                  AND json_extract(runs.result_json, '$.strategy.signal.direction')
                      IN ('positive', 'negative')
                  AND (
                      ? IS NULL
                      OR runs.completed_at>?
                      OR (runs.completed_at=? AND runs.id>?)
                  )
                ORDER BY runs.completed_at ASC, runs.id ASC
                LIMIT ?
                """,
                (
                    after_completed_at,
                    after_completed_at,
                    after_completed_at,
                    after_run_id or "",
                    max(1, min(int(limit), 1000)),
                ),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def get_strategy_shadow_enrollment(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return self._strategy_shadow_from_row(row)

    def list_strategy_shadow_enrollments(
        self,
        strategy_id: str,
        strategy_version: str,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_strategy_shadow_enrollments
                WHERE strategy_id=? AND strategy_version=?
                ORDER BY baseline_as_of DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                (
                    strategy_id,
                    strategy_version,
                    max(1, min(int(limit), 2000)),
                ),
            ).fetchall()
        return [self._strategy_shadow_from_row(row) for row in rows]

    def count_strategy_shadow_enrollments(
        self,
        strategy_id: str,
        strategy_version: str,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM agent_strategy_shadow_enrollments
                WHERE strategy_id=? AND strategy_version=?
                """,
                (strategy_id, strategy_version),
            ).fetchone()
        return int(row["count"] if row else 0)

    def ensure_strategy_shadow_enrollment(
        self,
        run_id: str,
        *,
        strategy_id: str,
        strategy_version: str,
        manifest_sha256: str,
        strategy_status: str,
        governance_evidence_id: str,
        signal_evidence_id: str,
        fund_code: str,
        fund_name: str | None,
        baseline_as_of: str,
        baseline_nav: float,
        signal_direction: str,
        signal_decision: str,
        confidence_level: str,
        horizon: str,
        observation_days: int,
        signal_snapshot: dict[str, Any],
        due_at: str,
        actor_id: str = "strategy-shadow-runtime-v1",
        now: str | dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist every eligible signal, including deterministic overlap exclusions."""
        if signal_direction not in {"positive", "negative"}:
            raise ValueError("Shadow 入组信号必须是 positive 或 negative")
        if int(observation_days) < 1:
            raise ValueError("Shadow 入组观测窗口必须大于 0")
        snapshot_json = _json(signal_snapshot)
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        now_text = _utc_iso(now)
        due_text = _utc_iso(due_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                item = self._strategy_shadow_from_row(existing)
                if item["signal_snapshot_sha256"] != snapshot_hash:
                    raise ValueError("Run 已经存在不同的 Shadow 入组快照")
                return item, False
            run = connection.execute(
                "SELECT tenant_id, user_id FROM agent_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Agent Run 不存在:{run_id}")
            candidates = connection.execute(
                """
                SELECT * FROM agent_strategy_shadow_enrollments
                WHERE strategy_id=? AND strategy_version=? AND fund_code=? AND horizon=?
                  AND baseline_as_of<=?
                  AND status IN ('scheduled', 'retry_wait', 'blocked', 'observed')
                ORDER BY baseline_as_of DESC, created_at DESC, id DESC
                """,
                (
                    strategy_id,
                    strategy_version,
                    fund_code,
                    horizon,
                    baseline_as_of,
                ),
            ).fetchall()
            blocker = None
            exclusion_reason = None
            for candidate in candidates:
                if candidate["status"] == "observed":
                    if str(candidate["observed_as_of"] or "") < str(baseline_as_of):
                        continue
                    exclusion_reason = "overlaps_observed_window"
                elif candidate["status"] == "blocked":
                    exclusion_reason = "prior_window_unresolved"
                else:
                    exclusion_reason = "prior_window_in_progress"
                blocker = candidate
                break
            status = "excluded" if blocker is not None else "scheduled"
            next_run_at = None if blocker is not None else due_text
            enrollment_id = _new_id("shadow")
            connection.execute(
                """
                INSERT INTO agent_strategy_shadow_enrollments (
                    id, run_id, tenant_id, user_id, strategy_id, strategy_version,
                    manifest_sha256, strategy_status, governance_evidence_id,
                    signal_evidence_id, fund_code, fund_name, baseline_as_of,
                    baseline_nav, signal_direction, signal_decision, confidence_level,
                    horizon, observation_days, signal_snapshot_json,
                    signal_snapshot_sha256, status, exclusion_reason,
                    blocking_enrollment_id, next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    enrollment_id,
                    run_id,
                    run["tenant_id"],
                    run["user_id"],
                    strategy_id,
                    strategy_version,
                    manifest_sha256,
                    strategy_status,
                    governance_evidence_id,
                    signal_evidence_id,
                    fund_code,
                    fund_name,
                    baseline_as_of,
                    float(baseline_nav),
                    signal_direction,
                    signal_decision,
                    confidence_level,
                    horizon,
                    int(observation_days),
                    snapshot_json,
                    snapshot_hash,
                    status,
                    exclusion_reason,
                    blocker["id"] if blocker is not None else None,
                    next_run_at,
                    now_text,
                    now_text,
                ),
            )
            self._append_audit(
                connection,
                run_id,
                "strategy.shadow.enrolled",
                {
                    "enrollment_id": enrollment_id,
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "manifest_sha256": manifest_sha256,
                    "signal_snapshot_sha256": snapshot_hash,
                    "fund_code": fund_code,
                    "baseline_as_of": baseline_as_of,
                    "signal_direction": signal_direction,
                    "horizon": horizon,
                    "observation_days": int(observation_days),
                    "status": status,
                    "exclusion_reason": exclusion_reason,
                    "blocking_enrollment_id": blocker["id"] if blocker is not None else None,
                    "next_run_at": next_run_at,
                },
                actor_id=actor_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
        return self._strategy_shadow_from_row(row), True

    def get_strategy_shadow_cohort(self, enrollment_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_cohorts WHERE enrollment_id=?",
                (enrollment_id,),
            ).fetchone()
        return self._strategy_shadow_cohort_from_row(row)

    def list_strategy_shadow_cohorts(
        self,
        strategy_id: str,
        strategy_version: str,
        *,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_strategy_shadow_cohorts
                WHERE strategy_id=? AND strategy_version=?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (
                    strategy_id,
                    strategy_version,
                    max(1, min(int(limit), 2000)),
                ),
            ).fetchall()
        return [self._strategy_shadow_cohort_from_row(row) for row in rows]

    def list_strategy_shadow_enrollments_missing_cohort(
        self,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT enrollments.*
                FROM agent_strategy_shadow_enrollments AS enrollments
                LEFT JOIN agent_strategy_shadow_cohorts AS cohorts
                    ON cohorts.enrollment_id=enrollments.id
                WHERE cohorts.id IS NULL
                ORDER BY enrollments.created_at ASC, enrollments.id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 10000)),),
            ).fetchall()
        return [self._strategy_shadow_from_row(row) for row in rows]

    def ensure_strategy_shadow_cohort(
        self,
        enrollment_id: str,
        *,
        cohort: dict[str, Any],
        actor_id: str = "strategy-shadow-cohort-runtime-v1",
        now: str | dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically persist immutable Cohort Evidence, index fields, and audit binding."""
        cohort_json = _json(cohort)
        cohort_hash = hashlib.sha256(cohort_json.encode("utf-8")).hexdigest()
        taxonomy = cohort.get("taxonomy") or {}
        binding = cohort.get("enrollment_binding") or {}
        sources = cohort.get("source_evidence") or {}
        market_source = sources.get("market_profile") or {}
        signal_source = sources.get("signal") or {}
        dimensions = cohort.get("dimensions") or {}
        horizon = dimensions.get("horizon") or {}
        market = dimensions.get("market") or {}
        asset = dimensions.get("asset_class") or {}
        vehicle = dimensions.get("vehicle") or {}
        regime = dimensions.get("signal_regime") or {}
        keys = cohort.get("keys") or {}
        release = cohort.get("release_classification") or {}
        required = {
            "taxonomy_id": taxonomy.get("id"),
            "taxonomy_version": taxonomy.get("version"),
            "run_id": binding.get("run_id"),
            "strategy_id": binding.get("strategy_id"),
            "strategy_version": binding.get("strategy_version"),
            "fund_code": binding.get("fund_code"),
            "horizon": horizon.get("name"),
            "market_evidence_id": market_source.get("evidence_id"),
            "market_payload_hash": market_source.get("payload_sha256"),
            "signal_evidence_id": signal_source.get("evidence_id"),
            "signal_payload_hash": signal_source.get("payload_sha256"),
            "market_primary": market.get("primary"),
            "asset_class": asset.get("primary"),
            "vehicle_type": vehicle.get("type"),
            "release_cohort_key": keys.get("release_cohort"),
            "regime_cohort_key": keys.get("regime_cohort"),
        }
        if any(not str(value or "").strip() for value in required.values()):
            raise ValueError("Shadow Cohort 缺少不可变绑定字段")
        observation_days = int(horizon.get("confirmed_nav_observations") or 0)
        if observation_days < 1:
            raise ValueError("Shadow Cohort 缺少有效确认净值窗口")
        now_text = _utc_iso(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM agent_strategy_shadow_cohorts WHERE enrollment_id=?",
                (enrollment_id,),
            ).fetchone()
            if existing is not None:
                item = self._strategy_shadow_cohort_from_row(existing)
                if item.get("cohort_sha256") != cohort_hash:
                    raise ValueError("Shadow 入组已经绑定不同的 Cohort 快照")
                return item, False
            enrollment = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
            if enrollment is None:
                raise KeyError(f"Shadow 入组记录不存在:{enrollment_id}")
            bound_values = {
                "id": binding.get("enrollment_id"),
                "run_id": binding.get("run_id"),
                "strategy_id": binding.get("strategy_id"),
                "strategy_version": binding.get("strategy_version"),
                "manifest_sha256": binding.get("manifest_sha256"),
                "signal_snapshot_sha256": binding.get("signal_snapshot_sha256"),
                "fund_code": binding.get("fund_code"),
                "baseline_as_of": binding.get("baseline_as_of"),
                "signal_direction": binding.get("signal_direction"),
                "horizon": horizon.get("name"),
                "observation_days": observation_days,
            }
            for key, expected in bound_values.items():
                if str(enrollment[key]) != str(expected):
                    raise ValueError(f"Shadow Cohort 与入组字段不一致:{key}")
            if enrollment["signal_evidence_id"] != signal_source["evidence_id"]:
                raise ValueError("Shadow Cohort 信号 Evidence 与入组记录不一致")
            for source, label in (
                (market_source, "市场画像"),
                (signal_source, "策略信号"),
            ):
                evidence = connection.execute(
                    "SELECT run_id, payload_sha256 FROM agent_evidence WHERE id=?",
                    (source["evidence_id"],),
                ).fetchone()
                if (
                    evidence is None
                    or evidence["run_id"] != enrollment["run_id"]
                    or evidence["payload_sha256"] != source["payload_sha256"]
                ):
                    raise ValueError(f"Shadow Cohort {label} Evidence 绑定失败")

            evidence_id = _new_id("ev")
            quality_status = "complete" if bool(release.get("eligible")) else "partial"
            connection.execute(
                """
                INSERT INTO agent_evidence (
                    id, run_id, step_id, evidence_type, subject_type, subject_id,
                    provider, source_url, observed_at, as_of, schema_version,
                    quality_status, payload_json, payload_sha256, created_at
                ) VALUES (?, ?, NULL, 'strategy_shadow_cohort', 'fund_strategy', ?,
                          'strategy_shadow_cohort_taxonomy', NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    enrollment["run_id"],
                    f"{enrollment['strategy_id']}@{enrollment['strategy_version']}:{enrollment['fund_code']}",
                    now_text,
                    enrollment["baseline_as_of"],
                    str(cohort.get("schema_version") or "strategy_shadow_cohort.v1"),
                    quality_status,
                    cohort_json,
                    cohort_hash,
                    now_text,
                ),
            )
            cohort_id = _new_id("cohort")
            connection.execute(
                """
                INSERT INTO agent_strategy_shadow_cohorts (
                    id, enrollment_id, run_id, strategy_id, strategy_version,
                    fund_code, horizon, observation_days, taxonomy_id,
                    taxonomy_version, market_profile_evidence_id,
                    market_profile_payload_sha256, signal_evidence_id,
                    signal_payload_sha256, evidence_id, market_primary,
                    asset_class, vehicle_type, trend_regime, drawdown_regime,
                    release_cohort_key, regime_cohort_key, release_eligible,
                    cohort_json, cohort_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cohort_id,
                    enrollment_id,
                    enrollment["run_id"],
                    enrollment["strategy_id"],
                    enrollment["strategy_version"],
                    enrollment["fund_code"],
                    enrollment["horizon"],
                    int(enrollment["observation_days"]),
                    required["taxonomy_id"],
                    required["taxonomy_version"],
                    market_source["evidence_id"],
                    market_source["payload_sha256"],
                    signal_source["evidence_id"],
                    signal_source["payload_sha256"],
                    evidence_id,
                    required["market_primary"],
                    required["asset_class"],
                    required["vehicle_type"],
                    str(regime.get("trend") or ""),
                    str(regime.get("drawdown_band") or ""),
                    required["release_cohort_key"],
                    required["regime_cohort_key"],
                    int(bool(release.get("eligible"))),
                    cohort_json,
                    cohort_hash,
                    now_text,
                ),
            )
            self._append_audit(
                connection,
                enrollment["run_id"],
                "evidence.created",
                {
                    "evidence_id": evidence_id,
                    "step_id": None,
                    "payload_sha256": cohort_hash,
                    "quality_status": quality_status,
                    "evidence_type": "strategy_shadow_cohort",
                },
                actor_id=actor_id,
            )
            self._append_audit(
                connection,
                enrollment["run_id"],
                "strategy.shadow.cohort.bound",
                {
                    "cohort_id": cohort_id,
                    "enrollment_id": enrollment_id,
                    "evidence_id": evidence_id,
                    "cohort_sha256": cohort_hash,
                    "taxonomy_id": required["taxonomy_id"],
                    "taxonomy_version": required["taxonomy_version"],
                    "market_profile_evidence_id": market_source["evidence_id"],
                    "signal_evidence_id": signal_source["evidence_id"],
                    "release_cohort_key": required["release_cohort_key"],
                    "regime_cohort_key": required["regime_cohort_key"],
                    "release_eligible": bool(release.get("eligible")),
                },
                actor_id=actor_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_cohorts WHERE id=?",
                (cohort_id,),
            ).fetchone()
        return self._strategy_shadow_cohort_from_row(row), True

    def claim_due_strategy_shadow_enrollment(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any] | None:
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        lease_expires_at = _utc_iso(now_dt + dt.timedelta(seconds=max(60, int(lease_seconds))))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM agent_strategy_shadow_enrollments
                WHERE status IN ('scheduled', 'retry_wait')
                  AND next_run_at IS NOT NULL AND next_run_at<=?
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                ORDER BY next_run_at, id
                LIMIT 1
                """,
                (now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE agent_strategy_shadow_enrollments
                SET lease_owner=?, lease_expires_at=?, last_started_at=?,
                    attempt_count=attempt_count+1, updated_at=?
                WHERE id=? AND status IN ('scheduled', 'retry_wait')
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                """,
                (worker_id, lease_expires_at, now_text, now_text, row["id"], now_text),
            ).rowcount
            if changed != 1:
                return None
            self._append_audit(
                connection,
                row["run_id"],
                "strategy.shadow.observation.started",
                {
                    "enrollment_id": row["id"],
                    "worker_id": worker_id,
                    "lease_expires_at": lease_expires_at,
                },
                actor_id=worker_id,
            )
            claimed = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (row["id"],),
            ).fetchone()
        return self._strategy_shadow_from_row(claimed)

    def mark_strategy_shadow_pending(
        self,
        enrollment_id: str,
        worker_id: str,
        *,
        provider_as_of: str,
        available_observations: int,
        retry_hours: int = 24,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any]:
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        next_run_at = _utc_iso(now_dt + dt.timedelta(hours=max(12, min(int(retry_hours), 168))))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            enrollment = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
            if enrollment is None:
                raise KeyError(f"Shadow 入组记录不存在:{enrollment_id}")
            changed = connection.execute(
                """
                UPDATE agent_strategy_shadow_enrollments
                SET status='scheduled', next_run_at=?, lease_owner=NULL,
                    lease_expires_at=NULL, consecutive_failures=0,
                    last_finished_at=?, last_provider_as_of=?,
                    last_error_code=NULL, last_error_message=NULL, updated_at=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    next_run_at,
                    now_text,
                    str(provider_as_of or ""),
                    now_text,
                    enrollment_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Shadow 观测租约已失效，拒绝提交等待状态")
            self._append_audit(
                connection,
                enrollment["run_id"],
                "strategy.shadow.observation.pending",
                {
                    "enrollment_id": enrollment_id,
                    "worker_id": worker_id,
                    "provider_as_of": str(provider_as_of or ""),
                    "available_observations": int(available_observations),
                    "required_observations": int(enrollment["observation_days"]),
                    "status": "scheduled",
                    "next_run_at": next_run_at,
                },
                actor_id=worker_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
        return self._strategy_shadow_from_row(row)

    def complete_strategy_shadow_enrollment(
        self,
        enrollment_id: str,
        worker_id: str,
        *,
        provider_as_of: str,
        observed_as_of: str,
        evidence_id: str,
        evidence_created: bool,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any]:
        now_text = _utc_iso(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            enrollment = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
            if enrollment is None:
                raise KeyError(f"Shadow 入组记录不存在:{enrollment_id}")
            changed = connection.execute(
                """
                UPDATE agent_strategy_shadow_enrollments
                SET status='observed', next_run_at=NULL, lease_owner=NULL,
                    lease_expires_at=NULL, consecutive_failures=0,
                    last_finished_at=?, last_provider_as_of=?, observed_as_of=?,
                    last_evidence_id=?, last_error_code=NULL,
                    last_error_message=NULL, updated_at=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    now_text,
                    str(provider_as_of),
                    str(observed_as_of),
                    evidence_id,
                    now_text,
                    enrollment_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Shadow 观测租约已失效，拒绝提交完成状态")
            self._append_audit(
                connection,
                enrollment["run_id"],
                "strategy.shadow.observation.completed",
                {
                    "enrollment_id": enrollment_id,
                    "worker_id": worker_id,
                    "provider_as_of": str(provider_as_of),
                    "observed_as_of": str(observed_as_of),
                    "evidence_id": evidence_id,
                    "evidence_created": bool(evidence_created),
                    "status": "observed",
                    "next_run_at": None,
                },
                actor_id=worker_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
        return self._strategy_shadow_from_row(row)

    def fail_strategy_shadow_enrollment(
        self,
        enrollment_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: str | dt.datetime | None = None,
    ) -> dict[str, Any]:
        retry_delays = (900, 3600, 14400, 43200, 86400, 86400, 86400)
        now_dt = _as_utc_datetime(now)
        now_text = _utc_iso(now_dt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            enrollment = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
            if enrollment is None:
                raise KeyError(f"Shadow 入组记录不存在:{enrollment_id}")
            failure_count = int(enrollment["consecutive_failures"]) + 1
            retry_exhausted = bool(retryable and failure_count > len(retry_delays))
            should_retry = bool(retryable and not retry_exhausted)
            delay = retry_delays[min(failure_count - 1, len(retry_delays) - 1)]
            next_run_at = _utc_iso(now_dt + dt.timedelta(seconds=delay)) if should_retry else None
            status = "retry_wait" if should_retry else "blocked"
            changed = connection.execute(
                """
                UPDATE agent_strategy_shadow_enrollments
                SET status=?, next_run_at=?, lease_owner=NULL, lease_expires_at=NULL,
                    consecutive_failures=?, last_finished_at=?, last_error_code=?,
                    last_error_message=?, updated_at=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    status,
                    next_run_at,
                    failure_count,
                    now_text,
                    str(error_code or "STRATEGY_SHADOW_OUTCOME_FAILED")[:100],
                    str(error_message or "")[:500],
                    now_text,
                    enrollment_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Shadow 观测租约已失效，拒绝提交失败状态")
            self._append_audit(
                connection,
                enrollment["run_id"],
                "strategy.shadow.observation.failed",
                {
                    "enrollment_id": enrollment_id,
                    "worker_id": worker_id,
                    "error_code": str(error_code),
                    "retryable": bool(retryable),
                    "retry_exhausted": retry_exhausted,
                    "consecutive_failures": failure_count,
                    "status": status,
                    "next_run_at": next_run_at,
                },
                actor_id=worker_id,
            )
            row = connection.execute(
                "SELECT * FROM agent_strategy_shadow_enrollments WHERE id=?",
                (enrollment_id,),
            ).fetchone()
        return self._strategy_shadow_from_row(row)
