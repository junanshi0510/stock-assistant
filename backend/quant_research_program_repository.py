# -*- coding: utf-8 -*-
"""Immutable research-program registry and non-skippable cycle ledger."""

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
from quant_selection_repository import (
    canonical_json,
    sha256_payload,
    sha256_text,
)


PROGRAM_SCHEMA_VERSION = "quant_research_program.v1"
CYCLE_SCHEMA_VERSION = "quant_research_cycle.v1"
TERMINAL_CYCLE_STATUSES = {
    "research_only",
    "forward_enrolled",
    "failed",
    "retired_unrun",
}
REQUIRED_TABLES = {
    "quant_research_programs",
    "quant_research_program_events",
    "quant_research_cycles",
    "quant_research_cycle_events",
}


class QuantResearchProgramError(RuntimeError):
    pass


class QuantResearchProgramNotFound(QuantResearchProgramError):
    pass


class QuantResearchProgramConflict(QuantResearchProgramError):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS quant_research_programs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    name TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    schedule_json TEXT NOT NULL,
    schedule_sha256 TEXT NOT NULL,
    acknowledgement_json TEXT NOT NULL,
    acknowledgement_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quant_research_programs_scope
ON quant_research_programs(tenant_id, user_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS quant_research_program_events (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL
        REFERENCES quant_research_programs(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(program_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS quant_research_cycles (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL
        REFERENCES quant_research_programs(id) ON DELETE RESTRICT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    scheduled_for TEXT NOT NULL,
    scheduled_local TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'scheduled','dispatching','run_queued','run_running',
        'research_only','forward_enrolled','failed','retired_unrun'
    )),
    run_id TEXT REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    mandate_id TEXT
        REFERENCES quant_selection_shadow_mandates(id) ON DELETE RESTRICT,
    validation_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    outcome_json TEXT,
    outcome_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(program_id, slot_key),
    UNIQUE(program_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_quant_research_cycles_due
ON quant_research_cycles(status, scheduled_for, id);
CREATE INDEX IF NOT EXISTS idx_quant_research_cycles_scope
ON quant_research_cycles(tenant_id, user_id, scheduled_for DESC, id DESC);

CREATE TABLE IF NOT EXISTS quant_research_cycle_events (
    id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL
        REFERENCES quant_research_cycles(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(cycle_id, sequence_no)
);

CREATE TRIGGER IF NOT EXISTS trg_quant_research_programs_no_update
BEFORE UPDATE ON quant_research_programs
BEGIN SELECT RAISE(ABORT, 'quant research programs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_programs_no_delete
BEFORE DELETE ON quant_research_programs
BEGIN SELECT RAISE(ABORT, 'quant research programs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_program_events_no_update
BEFORE UPDATE ON quant_research_program_events
BEGIN SELECT RAISE(ABORT, 'quant research program events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_program_events_no_delete
BEFORE DELETE ON quant_research_program_events
BEGIN SELECT RAISE(ABORT, 'quant research program events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_cycle_events_no_update
BEFORE UPDATE ON quant_research_cycle_events
BEGIN SELECT RAISE(ABORT, 'quant research cycle events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_cycle_events_no_delete
BEFORE DELETE ON quant_research_cycle_events
BEGIN SELECT RAISE(ABORT, 'quant research cycle events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_cycles_no_delete
BEFORE DELETE ON quant_research_cycles
BEGIN SELECT RAISE(ABORT, 'quant research cycles cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_cycle_input_immutable
BEFORE UPDATE OF
    program_id, tenant_id, user_id, schema_version, slot_key, sequence_no,
    scheduled_for, scheduled_local, created_at
ON quant_research_cycles
BEGIN SELECT RAISE(ABORT, 'quant research cycle schedule is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_cycle_links_immutable
BEFORE UPDATE OF run_id, mandate_id, validation_id
ON quant_research_cycles
WHEN (OLD.run_id IS NOT NULL AND NEW.run_id IS NOT OLD.run_id)
  OR (OLD.mandate_id IS NOT NULL AND NEW.mandate_id IS NOT OLD.mandate_id)
  OR (OLD.validation_id IS NOT NULL AND NEW.validation_id IS NOT OLD.validation_id)
BEGIN SELECT RAISE(ABORT, 'quant research cycle links are immutable once set'); END;
CREATE TRIGGER IF NOT EXISTS trg_quant_research_cycle_outcome_immutable
BEFORE UPDATE OF outcome_json, outcome_sha256
ON quant_research_cycles
WHEN OLD.outcome_json IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'quant research cycle outcome is immutable'); END;
"""


class QuantResearchProgramRepository:
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
        owner_column: str,
        owner_id: str,
        prefix: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = connection.execute(
            f"""
            SELECT sequence_no, event_hash FROM {table}
            WHERE {owner_column}=?
            ORDER BY sequence_no DESC LIMIT 1
            """,
            (owner_id,),
        ).fetchone()
        event = {
            "id": _id(prefix),
            owner_column: owner_id,
            "sequence_no": int(
                previous["sequence_no"] if previous else 0
            )
            + 1,
            "event_type": event_type,
            "actor_id": actor_id,
            "details": details or {},
            "previous_hash": (
                str(previous["event_hash"]) if previous else None
            ),
            "created_at": _iso(),
        }
        event_hash = sha256_payload(event)
        connection.execute(
            f"""
            INSERT INTO {table}(
                id, {owner_column}, sequence_no, event_type, actor_id,
                details_json, previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                owner_id,
                event["sequence_no"],
                event_type,
                actor_id,
                canonical_json(event["details"]),
                event["previous_hash"],
                event_hash,
                event["created_at"],
            ),
        )
        return {**event, "event_hash": event_hash}

    @staticmethod
    def _program_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        policy_json = str(item.pop("policy_json", ""))
        schedule_json = str(item.pop("schedule_json", ""))
        acknowledgement_json = str(
            item.pop("acknowledgement_json", "")
        )
        item["policy"] = _load(policy_json, {})
        item["schedule"] = _load(schedule_json, {})
        item["acknowledgement"] = _load(acknowledgement_json, {})
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == PROGRAM_SCHEMA_VERSION
                and sha256_text(policy_json) == item.get("policy_sha256")
                and sha256_text(schedule_json)
                == item.get("schedule_sha256")
                and sha256_text(acknowledgement_json)
                == item.get("acknowledgement_sha256")
            )
        }
        return item

    @staticmethod
    def _cycle_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        outcome_json = item.pop("outcome_json", None)
        item["outcome"] = _load(outcome_json, None)
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == CYCLE_SCHEMA_VERSION
                and (
                    outcome_json is None
                    or sha256_text(str(outcome_json))
                    == item.get("outcome_sha256")
                )
            )
        }
        return item

    def create_program(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        name: str,
        policy: dict[str, Any],
        schedule: dict[str, Any],
        acknowledgement: dict[str, Any],
        slots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not slots:
            raise ValueError("研究计划至少需要一个预登记批次")
        program_id = _id("qprog")
        created_at = _iso()
        policy_json = canonical_json(policy)
        schedule_json = canonical_json(schedule)
        acknowledgement_json = canonical_json(acknowledgement)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO quant_research_programs(
                        id, tenant_id, user_id, actor_id, schema_version,
                        name, policy_json, policy_sha256, schedule_json,
                        schedule_sha256, acknowledgement_json,
                        acknowledgement_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        program_id,
                        tenant_id,
                        user_id,
                        actor_id,
                        PROGRAM_SCHEMA_VERSION,
                        str(name)[:100],
                        policy_json,
                        sha256_text(policy_json),
                        schedule_json,
                        sha256_text(schedule_json),
                        acknowledgement_json,
                        sha256_text(acknowledgement_json),
                        created_at,
                    ),
                )
                self._append_event(
                    connection,
                    table="quant_research_program_events",
                    owner_column="program_id",
                    owner_id=program_id,
                    prefix="qprog_evt",
                    event_type="program_created",
                    actor_id=actor_id,
                    details={
                        "planned_cycle_count": len(slots),
                        "policy_sha256": sha256_text(policy_json),
                        "schedule_sha256": sha256_text(schedule_json),
                    },
                )
                for slot in slots:
                    cycle_id = _id("qcycle")
                    connection.execute(
                        """
                        INSERT INTO quant_research_cycles(
                            id, program_id, tenant_id, user_id,
                            schema_version, slot_key, sequence_no,
                            scheduled_for, scheduled_local, status,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
                        """,
                        (
                            cycle_id,
                            program_id,
                            tenant_id,
                            user_id,
                            CYCLE_SCHEMA_VERSION,
                            str(slot["slot_key"]),
                            int(slot["sequence_no"]),
                            str(slot["scheduled_for"]),
                            str(slot["scheduled_local"]),
                            created_at,
                        ),
                    )
                    self._append_event(
                        connection,
                        table="quant_research_cycle_events",
                        owner_column="cycle_id",
                        owner_id=cycle_id,
                        prefix="qcycle_evt",
                        event_type="cycle_preregistered",
                        actor_id=actor_id,
                        details={
                            "sequence_no": int(slot["sequence_no"]),
                            "scheduled_for": str(slot["scheduled_for"]),
                            "scheduled_local": str(
                                slot["scheduled_local"]
                            ),
                        },
                    )
            return self.get_program(
                program_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except INTEGRITY_ERRORS as error:
            raise QuantResearchProgramConflict(
                "研究计划或日历批次发生唯一性冲突"
            ) from error

    def _program_state(self, connection, program_id: str) -> str:
        row = connection.execute(
            """
            SELECT event_type FROM quant_research_program_events
            WHERE program_id=? ORDER BY sequence_no DESC LIMIT 1
            """,
            (program_id,),
        ).fetchone()
        return (
            "retired"
            if row and row["event_type"] == "program_retired"
            else "active"
        )

    def _decorate_program(self, connection, row) -> dict[str, Any]:
        item = self._program_from_row(row)
        assert item is not None
        cycles = [
            self._cycle_from_row(cycle)
            for cycle in connection.execute(
                """
                SELECT * FROM quant_research_cycles
                WHERE program_id=? ORDER BY sequence_no
                """,
                (item["id"],),
            ).fetchall()
        ]
        item["state"] = self._program_state(connection, item["id"])
        item["cycles"] = cycles
        item["summary"] = {
            "planned_cycle_count": len(cycles),
            "completed_cycle_count": sum(
                cycle["status"] in TERMINAL_CYCLE_STATUSES
                for cycle in cycles
            ),
            "forward_enrolled_count": sum(
                cycle["status"] == "forward_enrolled"
                for cycle in cycles
            ),
            "research_only_count": sum(
                cycle["status"] == "research_only"
                for cycle in cycles
            ),
            "failed_count": sum(
                cycle["status"] == "failed" for cycle in cycles
            ),
            "next_scheduled_for": next(
                (
                    cycle["scheduled_for"]
                    for cycle in cycles
                    if cycle["status"] == "scheduled"
                ),
                None,
            ),
        }
        return item

    def get_program(
        self,
        program_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM quant_research_programs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (program_id, tenant_id, user_id),
            ).fetchone()
            return self._decorate_program(connection, row) if row else None

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
                SELECT * FROM quant_research_programs
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (tenant_id, user_id, max(1, min(int(limit), 100))),
            ).fetchall()
            return [
                self._decorate_program(connection, row) for row in rows
            ]

    def list_reconcilable(
        self,
        *,
        now: dt.datetime | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
        user_id: str | None = None,
        program_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now_text = _iso(now)
        filters = [
            """(
                (c.status='scheduled' AND c.scheduled_for<=?)
                OR c.status IN ('dispatching','run_queued','run_running')
            )"""
        ]
        parameters: list[Any] = [now_text]
        for column, value in (
            ("c.tenant_id", tenant_id),
            ("c.user_id", user_id),
            ("c.program_id", program_id),
        ):
            if value is not None:
                filters.append(f"{column}=?")
                parameters.append(value)
        parameters.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.*, p.actor_id AS program_actor_id,
                       p.name AS program_name,
                       p.schema_version AS program_schema_version,
                       p.policy_json, p.policy_sha256,
                       p.schedule_json, p.schedule_sha256,
                       p.acknowledgement_json,
                       p.acknowledgement_sha256
                FROM quant_research_cycles c
                JOIN quant_research_programs p ON p.id=c.program_id
                WHERE {" AND ".join(filters)}
                ORDER BY
                    CASE WHEN c.status='scheduled' THEN 0 ELSE 1 END,
                    c.scheduled_for, c.id
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
            output = []
            for row in rows:
                raw = dict(row)
                cycle = self._cycle_from_row(raw)
                assert cycle is not None
                for key in (
                    "program_actor_id",
                    "program_name",
                    "program_schema_version",
                    "policy_json",
                    "policy_sha256",
                    "schedule_json",
                    "schedule_sha256",
                    "acknowledgement_json",
                    "acknowledgement_sha256",
                ):
                    cycle.pop(key, None)
                program_integrity = bool(
                    raw.get("program_schema_version")
                    == PROGRAM_SCHEMA_VERSION
                    and sha256_text(str(raw.get("policy_json") or ""))
                    == raw.get("policy_sha256")
                    and sha256_text(str(raw.get("schedule_json") or ""))
                    == raw.get("schedule_sha256")
                    and sha256_text(
                        str(raw.get("acknowledgement_json") or "")
                    )
                    == raw.get("acknowledgement_sha256")
                )
                cycle["program"] = {
                    "id": cycle["program_id"],
                    "actor_id": raw.get("program_actor_id"),
                    "name": raw.get("program_name"),
                    "policy": _load(raw.get("policy_json"), {}),
                    "policy_sha256": raw.get("policy_sha256"),
                    "schedule": _load(raw.get("schedule_json"), {}),
                    "acknowledgement": _load(
                        raw.get("acknowledgement_json"), {}
                    ),
                    "integrity": {"verified": program_integrity},
                    "state": self._program_state(
                        connection, cycle["program_id"]
                    ),
                }
                output.append(cycle)
            return output

    def claim_cycle(
        self,
        cycle_id: str,
        *,
        actor_id: str,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE quant_research_cycles
                SET status='dispatching', attempt_count=attempt_count+1,
                    started_at=COALESCE(started_at, ?)
                WHERE id=? AND status='scheduled'
                """,
                (_iso(), cycle_id),
            )
            if cursor.rowcount != 1:
                return False
            self._append_event(
                connection,
                table="quant_research_cycle_events",
                owner_column="cycle_id",
                owner_id=cycle_id,
                prefix="qcycle_evt",
                event_type="dispatch_claimed",
                actor_id=actor_id,
            )
            return True

    def attach_run(
        self,
        cycle_id: str,
        *,
        run_id: str,
        run_status: str,
        actor_id: str,
    ) -> dict[str, Any]:
        status = (
            "run_running" if run_status == "running" else "run_queued"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE quant_research_cycles
                SET run_id=?, status=?
                WHERE id=? AND status='dispatching' AND run_id IS NULL
                """,
                (run_id, status, cycle_id),
            )
            if cursor.rowcount != 1:
                raise QuantResearchProgramConflict(
                    "研究批次已经绑定运行或状态已经变化"
                )
            self._append_event(
                connection,
                table="quant_research_cycle_events",
                owner_column="cycle_id",
                owner_id=cycle_id,
                prefix="qcycle_evt",
                event_type="run_attached",
                actor_id=actor_id,
                details={"run_id": run_id, "run_status": run_status},
            )
            row = connection.execute(
                "SELECT * FROM quant_research_cycles WHERE id=?",
                (cycle_id,),
            ).fetchone()
            return self._cycle_from_row(row)

    def mark_run_status(
        self,
        cycle_id: str,
        *,
        status: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if status not in {"run_queued", "run_running"}:
            raise ValueError("无效研究运行状态")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE quant_research_cycles SET status=?
                WHERE id=? AND status IN ('run_queued','run_running')
                """,
                (status, cycle_id),
            )
            if cursor.rowcount == 1:
                self._append_event(
                    connection,
                    table="quant_research_cycle_events",
                    owner_column="cycle_id",
                    owner_id=cycle_id,
                    prefix="qcycle_evt",
                    event_type=status,
                    actor_id=actor_id,
                )
            row = connection.execute(
                "SELECT * FROM quant_research_cycles WHERE id=?",
                (cycle_id,),
            ).fetchone()
            if row is None:
                raise QuantResearchProgramNotFound("研究批次不存在")
            return self._cycle_from_row(row)

    def complete_cycle(
        self,
        cycle_id: str,
        *,
        status: str,
        actor_id: str,
        outcome: dict[str, Any],
        mandate_id: str | None = None,
        validation_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in TERMINAL_CYCLE_STATUSES:
            raise ValueError("无效研究批次终态")
        outcome_json = canonical_json(outcome)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE quant_research_cycles
                SET status=?, mandate_id=COALESCE(mandate_id, ?),
                    validation_id=COALESCE(validation_id, ?),
                    outcome_json=?, outcome_sha256=?,
                    error_code=?, error_message=?, completed_at=?
                WHERE id=? AND outcome_json IS NULL
                  AND status NOT IN (
                    'research_only','forward_enrolled','failed','retired_unrun'
                  )
                """,
                (
                    status,
                    mandate_id,
                    validation_id,
                    outcome_json,
                    sha256_text(outcome_json),
                    error_code,
                    str(error_message or "")[:1000] or None,
                    _iso(),
                    cycle_id,
                ),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT * FROM quant_research_cycles WHERE id=?",
                    (cycle_id,),
                ).fetchone()
                if row is None:
                    raise QuantResearchProgramNotFound("研究批次不存在")
                existing = self._cycle_from_row(row)
                if existing and existing["status"] == status:
                    return existing
                raise QuantResearchProgramConflict("研究批次已经终结")
            self._append_event(
                connection,
                table="quant_research_cycle_events",
                owner_column="cycle_id",
                owner_id=cycle_id,
                prefix="qcycle_evt",
                event_type=f"cycle_{status}",
                actor_id=actor_id,
                details={
                    "outcome_sha256": sha256_text(outcome_json),
                    "mandate_id": mandate_id,
                    "validation_id": validation_id,
                    "error_code": error_code,
                },
            )
            row = connection.execute(
                "SELECT * FROM quant_research_cycles WHERE id=?",
                (cycle_id,),
            ).fetchone()
            return self._cycle_from_row(row)

    def retire_program(
        self,
        program_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id FROM quant_research_programs
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (program_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise QuantResearchProgramNotFound("研究计划不存在")
            if self._program_state(connection, program_id) == "retired":
                return self._decorate_program(
                    connection,
                    connection.execute(
                        "SELECT * FROM quant_research_programs WHERE id=?",
                        (program_id,),
                    ).fetchone(),
                )
            self._append_event(
                connection,
                table="quant_research_program_events",
                owner_column="program_id",
                owner_id=program_id,
                prefix="qprog_evt",
                event_type="program_retired",
                actor_id=actor_id,
                details={"reason": str(reason)[:500]},
            )
            future = connection.execute(
                """
                SELECT id, scheduled_for FROM quant_research_cycles
                WHERE program_id=? AND status='scheduled'
                ORDER BY sequence_no
                """,
                (program_id,),
            ).fetchall()
            for cycle in future:
                outcome = {
                    "reason": "program_retired",
                    "retirement_reason": str(reason)[:500],
                    "scheduled_for": cycle["scheduled_for"],
                }
                outcome_json = canonical_json(outcome)
                connection.execute(
                    """
                    UPDATE quant_research_cycles
                    SET status='retired_unrun', outcome_json=?,
                        outcome_sha256=?, error_code='PROGRAM_RETIRED',
                        error_message=?, completed_at=?
                    WHERE id=? AND status='scheduled'
                    """,
                    (
                        outcome_json,
                        sha256_text(outcome_json),
                        str(reason)[:1000],
                        _iso(),
                        cycle["id"],
                    ),
                )
                self._append_event(
                    connection,
                    table="quant_research_cycle_events",
                    owner_column="cycle_id",
                    owner_id=cycle["id"],
                    prefix="qcycle_evt",
                    event_type="cycle_retired_unrun",
                    actor_id=actor_id,
                    details={"reason": str(reason)[:500]},
                )
            program_row = connection.execute(
                "SELECT * FROM quant_research_programs WHERE id=?",
                (program_id,),
            ).fetchone()
            return self._decorate_program(connection, program_row)


repository = QuantResearchProgramRepository()
