# -*- coding: utf-8 -*-
"""Durable, tenant-scoped storage for the Alpha probability laboratory."""

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


PROGRAM_SCHEMA_VERSION = "alpha_forecast_program.v1"
RUN_SCHEMA_VERSION = "alpha_forecast_run.v1"
FORECAST_SCHEMA_VERSION = "alpha_forecast_fact.v1"
OUTCOME_SCHEMA_VERSION = "alpha_forecast_outcome.v1"
TERMINAL_RUN_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
REQUIRED_TABLES = {
    "alpha_forecast_programs",
    "alpha_forecast_program_events",
    "alpha_forecast_runs",
    "alpha_forecast_run_events",
    "alpha_forecasts",
    "alpha_forecast_outcomes",
}


class AlphaForecastRepositoryError(RuntimeError):
    pass


class AlphaForecastNotFound(AlphaForecastRepositoryError):
    pass


class AlphaForecastConflict(AlphaForecastRepositoryError):
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
CREATE TABLE IF NOT EXISTS alpha_forecast_programs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    name            TEXT NOT NULL,
    asset_type      TEXT NOT NULL CHECK(asset_type IN ('stock','fund')),
    market          TEXT NOT NULL,
    status          TEXT NOT NULL CHECK(status IN ('active','paused','retired')),
    policy_json     TEXT NOT NULL,
    policy_sha256   TEXT NOT NULL,
    next_run_at     TEXT,
    last_run_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alpha_programs_scope
ON alpha_forecast_programs(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_alpha_programs_due
ON alpha_forecast_programs(status, next_run_at);

CREATE TRIGGER IF NOT EXISTS trg_alpha_program_input_immutable
BEFORE UPDATE OF
    tenant_id, user_id, actor_id, schema_version, engine_version,
    name, asset_type, market, policy_json, policy_sha256, created_at
ON alpha_forecast_programs
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast program input is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_program_no_delete
BEFORE DELETE ON alpha_forecast_programs
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast program is immutable');
END;

CREATE TABLE IF NOT EXISTS alpha_forecast_program_events (
    id            TEXT PRIMARY KEY,
    program_id    TEXT NOT NULL REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    sequence_no   INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    previous_hash TEXT,
    event_hash    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(program_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_alpha_program_events
ON alpha_forecast_program_events(program_id, sequence_no);
CREATE TRIGGER IF NOT EXISTS trg_alpha_program_events_no_update
BEFORE UPDATE ON alpha_forecast_program_events
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast program events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_program_events_no_delete
BEFORE DELETE ON alpha_forecast_program_events
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast program events are immutable');
END;

CREATE TABLE IF NOT EXISTS alpha_forecast_runs (
    id              TEXT PRIMARY KEY,
    program_id      TEXT NOT NULL REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    tenant_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK(
        status IN ('queued','running','succeeded','partial','failed','cancelled')
    ),
    request_key     TEXT NOT NULL,
    task_id         TEXT,
    as_of_date      TEXT NOT NULL,
    input_json      TEXT NOT NULL,
    input_sha256    TEXT NOT NULL,
    progress_json   TEXT NOT NULL,
    result_json     TEXT,
    result_sha256   TEXT,
    error_code      TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    UNIQUE(program_id, request_key)
);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_scope
ON alpha_forecast_runs(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_program
ON alpha_forecast_runs(program_id, created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS trg_alpha_run_input_immutable
BEFORE UPDATE OF
    program_id, tenant_id, user_id, actor_id, schema_version,
    engine_version, request_key, as_of_date, input_json,
    input_sha256, created_at
ON alpha_forecast_runs
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast run input is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_run_result_immutable
BEFORE UPDATE OF result_json, result_sha256 ON alpha_forecast_runs
WHEN OLD.result_json IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast run result is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_run_no_delete
BEFORE DELETE ON alpha_forecast_runs
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast run is immutable');
END;

CREATE TABLE IF NOT EXISTS alpha_forecast_run_events (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES alpha_forecast_runs(id) ON DELETE RESTRICT,
    sequence_no   INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    previous_hash TEXT,
    event_hash    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_alpha_run_events
ON alpha_forecast_run_events(run_id, sequence_no);
CREATE TRIGGER IF NOT EXISTS trg_alpha_run_events_no_update
BEFORE UPDATE ON alpha_forecast_run_events
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast run events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_run_events_no_delete
BEFORE DELETE ON alpha_forecast_run_events
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast run events are immutable');
END;

CREATE TABLE IF NOT EXISTS alpha_forecasts (
    id                      TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL REFERENCES alpha_forecast_runs(id) ON DELETE RESTRICT,
    program_id              TEXT NOT NULL REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    asset_type              TEXT NOT NULL,
    market                  TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    name                    TEXT NOT NULL,
    horizon_sessions        INTEGER NOT NULL,
    as_of_date              TEXT NOT NULL,
    eligible_after          TEXT NOT NULL,
    calibrated_probability  REAL,
    base_rate               REAL,
    stance                  TEXT NOT NULL,
    historical_gate_passed  INTEGER NOT NULL,
    decision_eligible       INTEGER NOT NULL,
    payload_json            TEXT NOT NULL,
    payload_sha256          TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    UNIQUE(run_id, symbol, horizon_sessions)
);
CREATE INDEX IF NOT EXISTS idx_alpha_forecasts_pending
ON alpha_forecasts(eligible_after, created_at, id);
CREATE INDEX IF NOT EXISTS idx_alpha_forecasts_scope
ON alpha_forecasts(tenant_id, user_id, created_at DESC, id DESC);
CREATE TRIGGER IF NOT EXISTS trg_alpha_forecasts_no_update
BEFORE UPDATE ON alpha_forecasts
BEGIN
    SELECT RAISE(ABORT, 'alpha forecasts are immutable facts');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_forecasts_no_delete
BEFORE DELETE ON alpha_forecasts
BEGIN
    SELECT RAISE(ABORT, 'alpha forecasts are immutable facts');
END;

CREATE TABLE IF NOT EXISTS alpha_forecast_outcomes (
    id                    TEXT PRIMARY KEY,
    forecast_id           TEXT NOT NULL UNIQUE REFERENCES alpha_forecasts(id) ON DELETE RESTRICT,
    run_id                TEXT NOT NULL REFERENCES alpha_forecast_runs(id) ON DELETE RESTRICT,
    program_id            TEXT NOT NULL REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    tenant_id             TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    schema_version        TEXT NOT NULL,
    observed_date         TEXT NOT NULL,
    target_return_pct     REAL NOT NULL,
    realized_label        INTEGER NOT NULL,
    payload_json          TEXT NOT NULL,
    payload_sha256        TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alpha_outcomes_program
ON alpha_forecast_outcomes(program_id, observed_date, id);
CREATE TRIGGER IF NOT EXISTS trg_alpha_outcomes_no_update
BEFORE UPDATE ON alpha_forecast_outcomes
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast outcomes are immutable facts');
END;
CREATE TRIGGER IF NOT EXISTS trg_alpha_outcomes_no_delete
BEFORE DELETE ON alpha_forecast_outcomes
BEGIN
    SELECT RAISE(ABORT, 'alpha forecast outcomes are immutable facts');
END;
"""


class AlphaForecastRepository:
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
        *,
        table: str,
        parent_column: str,
        parent_id: str,
        id_prefix: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            ("alpha_forecast_program_events", "program_id"),
            ("alpha_forecast_run_events", "run_id"),
        }
        if (table, parent_column) not in allowed:
            raise ValueError("unsupported alpha event table")
        previous = connection.execute(
            f"""
            SELECT sequence_no, event_hash
            FROM {table}
            WHERE {parent_column}=?
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (parent_id,),
        ).fetchone()
        event = {
            "id": _new_id(id_prefix),
            parent_column: str(parent_id),
            "sequence_no": int(previous["sequence_no"] if previous else 0) + 1,
            "event_type": str(event_type),
            "actor_id": str(actor_id),
            "details": details or {},
            "previous_hash": (
                str(previous["event_hash"]) if previous else None
            ),
            "created_at": _iso(),
        }
        hash_payload = {
            key: value
            for key, value in event.items()
            if key != "details"
        }
        hash_payload["details"] = event["details"]
        event["event_hash"] = sha256_payload(hash_payload)
        connection.execute(
            f"""
            INSERT INTO {table}(
                id, {parent_column}, sequence_no, event_type, actor_id,
                details_json, previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                parent_id,
                event["sequence_no"],
                event["event_type"],
                event["actor_id"],
                canonical_json(event["details"]),
                event["previous_hash"],
                event["event_hash"],
                event["created_at"],
            ),
        )
        return event

    @staticmethod
    def _event_from_row(row, parent_column: str) -> dict[str, Any]:
        item = dict(row)
        item["details"] = _load(item.pop("details_json", ""), {})
        return item

    @staticmethod
    def _verify_event_chain(
        events: list[dict[str, Any]],
        *,
        parent_column: str,
    ) -> dict[str, Any]:
        previous_hash = None
        for expected_sequence, event in enumerate(events, start=1):
            payload = {
                "id": event.get("id"),
                parent_column: event.get(parent_column),
                "sequence_no": event.get("sequence_no"),
                "event_type": event.get("event_type"),
                "actor_id": event.get("actor_id"),
                "previous_hash": event.get("previous_hash"),
                "created_at": event.get("created_at"),
                "details": event.get("details") or {},
            }
            if (
                int(event.get("sequence_no") or 0) != expected_sequence
                or event.get("previous_hash") != previous_hash
                or sha256_payload(payload) != event.get("event_hash")
            ):
                return {
                    "verified": False,
                    "event_count": len(events),
                    "failed_sequence": expected_sequence,
                }
            previous_hash = str(event.get("event_hash") or "")
        return {
            "verified": True,
            "event_count": len(events),
            "head_hash": previous_hash,
        }

    @classmethod
    def _program_from_row(
        cls,
        row,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        policy_json = str(item.pop("policy_json", ""))
        item["policy"] = _load(policy_json, {})
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == PROGRAM_SCHEMA_VERSION
                and sha256_text(policy_json) == item.get("policy_sha256")
            )
        }
        if events is not None:
            item["events"] = events
            chain = cls._verify_event_chain(
                events, parent_column="program_id"
            )
            item["integrity"]["event_chain"] = chain
            item["integrity"]["verified"] = bool(
                item["integrity"]["verified"] and chain["verified"]
            )
        return item

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
        input_json = str(item.pop("input_json", ""))
        progress_json = str(item.pop("progress_json", ""))
        result_json = item.pop("result_json", None)
        item["input"] = _load(input_json, {})
        item["progress"] = _load(progress_json, {})
        if include_result:
            item["result"] = _load(result_json, None)
        input_ok = sha256_text(input_json) == item.get("input_sha256")
        result_ok = (
            result_json is None
            or sha256_text(str(result_json)) == item.get("result_sha256")
        )
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == RUN_SCHEMA_VERSION
                and input_ok
                and result_ok
            ),
            "input_verified": input_ok,
            "result_verified": result_ok,
        }
        if events is not None:
            item["events"] = events
            chain = cls._verify_event_chain(
                events, parent_column="run_id"
            )
            item["integrity"]["event_chain"] = chain
            item["integrity"]["verified"] = bool(
                item["integrity"]["verified"] and chain["verified"]
            )
        return item

    @staticmethod
    def _forecast_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload_json = str(item.pop("payload_json", ""))
        item["payload"] = _load(payload_json, {})
        item["historical_gate_passed"] = bool(
            item.get("historical_gate_passed")
        )
        item["decision_eligible"] = bool(item.get("decision_eligible"))
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == FORECAST_SCHEMA_VERSION
                and sha256_text(payload_json) == item.get("payload_sha256")
            )
        }
        return item

    @staticmethod
    def _outcome_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload_json = str(item.pop("payload_json", ""))
        item["payload"] = _load(payload_json, {})
        item["realized_label"] = int(item.get("realized_label") or 0)
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == OUTCOME_SCHEMA_VERSION
                and sha256_text(payload_json) == item.get("payload_sha256")
            )
        }
        return item

    def create_program(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        engine_version: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        policy_json = canonical_json(policy)
        program_id = _new_id("alpha_prog")
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO alpha_forecast_programs(
                    id, tenant_id, user_id, actor_id, schema_version,
                    engine_version, name, asset_type, market, status,
                    policy_json, policy_sha256, next_run_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    program_id,
                    tenant_id,
                    user_id,
                    actor_id,
                    PROGRAM_SCHEMA_VERSION,
                    engine_version,
                    str(policy["name"]),
                    str(policy["asset_type"]),
                    str(policy["market"]),
                    policy_json,
                    sha256_text(policy_json),
                    now,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                table="alpha_forecast_program_events",
                parent_column="program_id",
                parent_id=program_id,
                id_prefix="alpha_pevt",
                event_type="program.created",
                actor_id=actor_id,
                details={"policy_sha256": sha256_text(policy_json)},
            )
        item = self.get_program(
            program_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise AlphaForecastRepositoryError(
                "created alpha program disappeared"
            )
        return item

    def get_program(
        self,
        program_id: str,
        *,
        tenant_id: str,
        user_id: str,
        include_events: bool = True,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM alpha_forecast_programs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (program_id, tenant_id, user_id),
            ).fetchone()
            events = None
            if row is not None and include_events:
                events = [
                    self._event_from_row(item, "program_id")
                    for item in connection.execute(
                        """
                        SELECT * FROM alpha_forecast_program_events
                        WHERE program_id=? ORDER BY sequence_no
                        """,
                        (program_id,),
                    ).fetchall()
                ]
        return self._program_from_row(row, events)

    def get_program_unscoped(
        self,
        program_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM alpha_forecast_programs WHERE id=?",
                (program_id,),
            ).fetchone()
        return self._program_from_row(row)

    def list_programs(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM alpha_forecast_programs
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (tenant_id, user_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._program_from_row(row)) is not None
        ]

    def list_due_programs(self, *, limit: int = 5) -> list[dict[str, Any]]:
        now = _iso()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.* FROM alpha_forecast_programs p
                WHERE p.status='active'
                  AND p.next_run_at IS NOT NULL
                  AND p.next_run_at<=?
                  AND NOT EXISTS (
                    SELECT 1 FROM alpha_forecast_runs r
                    WHERE r.program_id=p.id
                      AND r.status IN ('queued','running')
                  )
                ORDER BY p.next_run_at, p.id LIMIT ?
                """,
                (now, max(1, min(20, int(limit)))),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._program_from_row(row)) is not None
        ]

    def transition_program(
        self,
        program_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        action: str,
    ) -> dict[str, Any]:
        transitions = {
            ("active", "pause"): "paused",
            ("active", "retire"): "retired",
            ("paused", "resume"): "active",
            ("paused", "retire"): "retired",
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status FROM alpha_forecast_programs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (program_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise AlphaForecastNotFound("概率研究项目不存在")
            current = str(row["status"])
            target = transitions.get((current, action))
            if target is None:
                raise AlphaForecastConflict(
                    f"项目状态 {current} 不允许执行 {action}"
                )
            now = _iso()
            connection.execute(
                """
                UPDATE alpha_forecast_programs
                SET status=?, next_run_at=CASE WHEN ?='active' THEN ? ELSE next_run_at END,
                    updated_at=?
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (
                    target,
                    target,
                    now,
                    now,
                    program_id,
                    tenant_id,
                    user_id,
                ),
            )
            self._append_event(
                connection,
                table="alpha_forecast_program_events",
                parent_column="program_id",
                parent_id=program_id,
                id_prefix="alpha_pevt",
                event_type=f"program.{action}",
                actor_id=actor_id,
                details={"from": current, "to": target},
            )
        item = self.get_program(
            program_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise AlphaForecastRepositoryError(
                "transitioned alpha program disappeared"
            )
        return item

    def create_run(
        self,
        program_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        request_key: str,
        as_of_date: str,
    ) -> tuple[dict[str, Any], bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            program_row = connection.execute(
                """
                SELECT * FROM alpha_forecast_programs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (program_id, tenant_id, user_id),
            ).fetchone()
            program = self._program_from_row(program_row)
            if program is None:
                raise AlphaForecastNotFound("概率研究项目不存在")
            existing = connection.execute(
                """
                SELECT * FROM alpha_forecast_runs
                WHERE program_id=? AND request_key=?
                """,
                (program_id, request_key),
            ).fetchone()
            if existing is not None:
                item = self._run_from_row(existing)
                if item is None:
                    raise AlphaForecastRepositoryError(
                        "existing alpha run could not be decoded"
                    )
                return item, False
            if program["status"] != "active":
                raise AlphaForecastConflict("只有 active 项目可以创建运行")
            active = connection.execute(
                """
                SELECT id FROM alpha_forecast_runs
                WHERE program_id=? AND status IN ('queued','running')
                LIMIT 1
                """,
                (program_id,),
            ).fetchone()
            if active is not None:
                raise AlphaForecastConflict("该项目已有运行中的概率任务")
            run_id = _new_id("alpha_run")
            input_payload = {
                "schema_version": "alpha_forecast_run_input.v1",
                "program_id": program_id,
                "program_policy_sha256": program["policy_sha256"],
                "as_of_date": as_of_date,
                "request_key": request_key,
            }
            input_json = canonical_json(input_payload)
            progress = {
                "stage": "queued",
                "completed": 0,
                "total": len(program["policy"].get("symbols") or []),
                "message": "等待 market-data Worker",
            }
            now = _iso()
            connection.execute(
                """
                INSERT INTO alpha_forecast_runs(
                    id, program_id, tenant_id, user_id, actor_id,
                    schema_version, engine_version, status, request_key,
                    as_of_date, input_json, input_sha256, progress_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    program_id,
                    tenant_id,
                    user_id,
                    actor_id,
                    RUN_SCHEMA_VERSION,
                    program["engine_version"],
                    request_key,
                    as_of_date,
                    input_json,
                    sha256_text(input_json),
                    canonical_json(progress),
                    now,
                ),
            )
            self._append_event(
                connection,
                table="alpha_forecast_run_events",
                parent_column="run_id",
                parent_id=run_id,
                id_prefix="alpha_revt",
                event_type="run.created",
                actor_id=actor_id,
                details={"input_sha256": sha256_text(input_json)},
            )
            self._append_event(
                connection,
                table="alpha_forecast_program_events",
                parent_column="program_id",
                parent_id=program_id,
                id_prefix="alpha_pevt",
                event_type="run.created",
                actor_id=actor_id,
                details={"run_id": run_id, "request_key": request_key},
            )
            created = connection.execute(
                "SELECT * FROM alpha_forecast_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        item = self._run_from_row(created)
        if item is None:
            raise AlphaForecastRepositoryError("created alpha run disappeared")
        return item, True

    def bind_task(
        self,
        run_id: str,
        task_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE alpha_forecast_runs SET task_id=?
                WHERE id=? AND tenant_id=? AND user_id=?
                  AND task_id IS NULL
                """,
                (task_id, run_id, tenant_id, user_id),
            )
            if cursor.rowcount != 1:
                raise AlphaForecastConflict("概率运行已经派发或状态已变化")

    def mark_running(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status FROM alpha_forecast_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise AlphaForecastNotFound("概率运行不存在")
            if str(row["status"]) in TERMINAL_RUN_STATUSES:
                raise AlphaForecastConflict("概率运行已经结束")
            progress = {
                "stage": "fetching",
                "completed": 0,
                "total": 0,
                "message": "正在读取冻结资产池的真实历史序列",
            }
            connection.execute(
                """
                UPDATE alpha_forecast_runs
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
                connection,
                table="alpha_forecast_run_events",
                parent_column="run_id",
                parent_id=run_id,
                id_prefix="alpha_revt",
                event_type="run.started",
                actor_id=actor_id,
            )
        item = self.get_run(
            run_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise AlphaForecastRepositoryError("started alpha run disappeared")
        return item

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
                UPDATE alpha_forecast_runs SET progress_json=?
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
        forecasts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if status not in {"succeeded", "partial"}:
            raise ValueError("invalid alpha completion status")
        result_json = canonical_json(result)
        result_sha256 = sha256_text(result_json)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT r.*, p.asset_type, p.market, p.policy_json
                FROM alpha_forecast_runs r
                JOIN alpha_forecast_programs p ON p.id=r.program_id
                WHERE r.id=? AND r.tenant_id=? AND r.user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise AlphaForecastNotFound("概率运行不存在")
            if (
                row["result_json"] is not None
                or str(row["status"]) in TERMINAL_RUN_STATUSES
            ):
                raise AlphaForecastConflict("概率运行结果已经冻结")
            program_id = str(row["program_id"])
            policy = _load(row["policy_json"], {})
            for forecast in forecasts:
                payload_json = canonical_json(forecast)
                connection.execute(
                    """
                    INSERT INTO alpha_forecasts(
                        id, run_id, program_id, tenant_id, user_id,
                        schema_version, asset_type, market, symbol, name,
                        horizon_sessions, as_of_date, eligible_after,
                        calibrated_probability, base_rate, stance,
                        historical_gate_passed, decision_eligible,
                        payload_json, payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("alpha_fcst"),
                        run_id,
                        program_id,
                        tenant_id,
                        user_id,
                        FORECAST_SCHEMA_VERSION,
                        str(row["asset_type"]),
                        str(row["market"]),
                        str(forecast["symbol"]),
                        str(forecast.get("name") or forecast["symbol"]),
                        int(forecast["horizon_sessions"]),
                        str(forecast["as_of_date"]),
                        str(forecast["eligible_after"]),
                        forecast.get("shadow_calibrated_probability"),
                        forecast.get("base_rate"),
                        str(forecast.get("stance") or ""),
                        int(bool(forecast.get("historical_gate_passed"))),
                        int(bool(forecast.get("decision_eligible"))),
                        payload_json,
                        sha256_text(payload_json),
                        _iso(),
                    ),
                )
            completed = _iso()
            progress = {
                "stage": "completed",
                "completed": int(
                    (result.get("data_quality") or {}).get("loaded_assets")
                    or 0
                ),
                "total": int(
                    (result.get("data_quality") or {}).get("requested_assets")
                    or 0
                ),
                "message": (
                    "多周期概率研究完成"
                    if status == "succeeded"
                    else "多周期概率研究部分完成"
                ),
            }
            connection.execute(
                """
                UPDATE alpha_forecast_runs
                SET status=?, progress_json=?, result_json=?,
                    result_sha256=?, error_code=NULL, error_message=NULL,
                    completed_at=?
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (
                    status,
                    canonical_json(progress),
                    result_json,
                    result_sha256,
                    completed,
                    run_id,
                    tenant_id,
                    user_id,
                ),
            )
            cadence_days = int(policy.get("cadence_days") or 7)
            next_run = _iso(
                dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(days=cadence_days)
            )
            connection.execute(
                """
                UPDATE alpha_forecast_programs
                SET last_run_at=?, next_run_at=?, updated_at=?
                WHERE id=?
                """,
                (completed, next_run, completed, program_id),
            )
            self._append_event(
                connection,
                table="alpha_forecast_run_events",
                parent_column="run_id",
                parent_id=run_id,
                id_prefix="alpha_revt",
                event_type="run.completed",
                actor_id=actor_id,
                details={
                    "status": status,
                    "result_sha256": result_sha256,
                    "forecast_count": len(forecasts),
                },
            )
            self._append_event(
                connection,
                table="alpha_forecast_program_events",
                parent_column="program_id",
                parent_id=program_id,
                id_prefix="alpha_pevt",
                event_type="run.completed",
                actor_id=actor_id,
                details={
                    "run_id": run_id,
                    "status": status,
                    "result_sha256": result_sha256,
                    "next_run_at": next_run,
                },
            )
        item = self.get_run(
            run_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise AlphaForecastRepositoryError(
                "completed alpha run disappeared"
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
                SELECT status FROM alpha_forecast_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise AlphaForecastNotFound("概率运行不存在")
            if str(row["status"]) not in TERMINAL_RUN_STATUSES:
                progress = {
                    "stage": "failed",
                    "completed": 0,
                    "total": 0,
                    "message": "多周期概率研究失败",
                }
                connection.execute(
                    """
                    UPDATE alpha_forecast_runs
                    SET status='failed', progress_json=?, error_code=?,
                        error_message=?, completed_at=?
                    WHERE id=? AND tenant_id=? AND user_id=?
                    """,
                    (
                        canonical_json(progress),
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
                    table="alpha_forecast_run_events",
                    parent_column="run_id",
                    parent_id=run_id,
                    id_prefix="alpha_revt",
                    event_type="run.failed",
                    actor_id=actor_id,
                    details={
                        "error_code": str(error_code)[:100],
                        "error_message": str(error_message)[:500],
                    },
                )
        item = self.get_run(
            run_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise AlphaForecastRepositoryError("failed alpha run disappeared")
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
                SELECT * FROM alpha_forecast_runs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (run_id, tenant_id, user_id),
            ).fetchone()
            events = None
            if row is not None and include_events:
                events = [
                    self._event_from_row(item, "run_id")
                    for item in connection.execute(
                        """
                        SELECT * FROM alpha_forecast_run_events
                        WHERE run_id=? ORDER BY sequence_no
                        """,
                        (run_id,),
                    ).fetchall()
                ]
        return self._run_from_row(row, events)

    def get_run_unscoped(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM alpha_forecast_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return self._run_from_row(row)

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
                    id, program_id, tenant_id, user_id, actor_id,
                    schema_version, engine_version, status, request_key,
                    task_id, as_of_date, input_json, input_sha256,
                    progress_json, result_json, result_sha256, error_code,
                    error_message, created_at, started_at, completed_at
                FROM alpha_forecast_runs
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (tenant_id, user_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._run_from_row(row)) is not None
        ]

    def list_pending_forecasts(
        self,
        *,
        limit: int = 200,
        tenant_id: str | None = None,
        user_id: str | None = None,
        program_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "f.eligible_after<=?",
            "o.id IS NULL",
        ]
        params: list[Any] = [dt.date.today().isoformat()]
        if tenant_id is not None:
            clauses.append("f.tenant_id=?")
            params.append(tenant_id)
        if user_id is not None:
            clauses.append("f.user_id=?")
            params.append(user_id)
        if program_id is not None:
            clauses.append("f.program_id=?")
            params.append(program_id)
        params.append(max(1, min(1000, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT f.* FROM alpha_forecasts f
                LEFT JOIN alpha_forecast_outcomes o ON o.forecast_id=f.id
                WHERE {' AND '.join(clauses)}
                ORDER BY f.eligible_after, f.created_at, f.id
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._forecast_from_row(row)) is not None
        ]

    def record_outcome(
        self,
        forecast_id: str,
        *,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        payload_json = canonical_json(payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            forecast_row = connection.execute(
                "SELECT * FROM alpha_forecasts WHERE id=?",
                (forecast_id,),
            ).fetchone()
            forecast = self._forecast_from_row(forecast_row)
            if forecast is None:
                raise AlphaForecastNotFound("概率预测事实不存在")
            existing = connection.execute(
                """
                SELECT * FROM alpha_forecast_outcomes
                WHERE forecast_id=?
                """,
                (forecast_id,),
            ).fetchone()
            if existing is not None:
                item = self._outcome_from_row(existing)
                if item is None:
                    raise AlphaForecastRepositoryError(
                        "existing alpha outcome could not be decoded"
                    )
                if item.get("payload_sha256") != sha256_text(payload_json):
                    raise AlphaForecastConflict("该预测的真实结果已经冻结")
                return item, False
            outcome_id = _new_id("alpha_out")
            connection.execute(
                """
                INSERT INTO alpha_forecast_outcomes(
                    id, forecast_id, run_id, program_id, tenant_id,
                    user_id, schema_version, observed_date,
                    target_return_pct, realized_label, payload_json,
                    payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    forecast_id,
                    forecast["run_id"],
                    forecast["program_id"],
                    forecast["tenant_id"],
                    forecast["user_id"],
                    OUTCOME_SCHEMA_VERSION,
                    str(payload["observed_date"]),
                    float(payload["target_return_pct"]),
                    int(payload["realized_label"]),
                    payload_json,
                    sha256_text(payload_json),
                    _iso(),
                ),
            )
            self._append_event(
                connection,
                table="alpha_forecast_program_events",
                parent_column="program_id",
                parent_id=str(forecast["program_id"]),
                id_prefix="alpha_pevt",
                event_type="forecast.outcome_recorded",
                actor_id=str(payload.get("actor_id") or "system"),
                details={
                    "forecast_id": forecast_id,
                    "outcome_id": outcome_id,
                    "payload_sha256": sha256_text(payload_json),
                },
            )
            saved = connection.execute(
                "SELECT * FROM alpha_forecast_outcomes WHERE id=?",
                (outcome_id,),
            ).fetchone()
        item = self._outcome_from_row(saved)
        if item is None:
            raise AlphaForecastRepositoryError(
                "created alpha outcome disappeared"
            )
        return item, True

    def list_forward_evidence(
        self,
        program_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    f.id AS forecast_id, f.run_id, f.program_id,
                    f.asset_type, f.symbol, f.name,
                    f.horizon_sessions, f.as_of_date,
                    f.calibrated_probability, f.base_rate, f.stance,
                    f.historical_gate_passed, f.decision_eligible,
                    f.payload_json,
                    f.payload_sha256 AS forecast_sha256,
                    o.id AS outcome_id, o.observed_date,
                    o.target_return_pct, o.realized_label,
                    o.payload_sha256 AS outcome_sha256
                FROM alpha_forecasts f
                LEFT JOIN alpha_forecast_outcomes o ON o.forecast_id=f.id
                WHERE f.program_id=? AND f.tenant_id=? AND f.user_id=?
                ORDER BY f.as_of_date, f.horizon_sessions, f.symbol
                """,
                (program_id, tenant_id, user_id),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            payload = _load(item.pop("payload_json", ""), {})
            item["decision_source_eligible"] = bool(
                payload.get("decision_source_eligible")
            )
            items.append(item)
        return items


repository = AlphaForecastRepository()
