# -*- coding: utf-8 -*-
"""PostgreSQL schema for pre-registered quant research programs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "quant-research-program.v1"
MIGRATION_LOCK_ID = 7_941_223_508_641_129

POSTGRES_DDL = """
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
    validation_id TEXT
        REFERENCES quant_selection_forward_validations(id) ON DELETE RESTRICT,
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
"""

POSTGRES_GUARDS = """
CREATE OR REPLACE FUNCTION stock_assistant_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'integrity_constraint_violation',
        MESSAGE = TG_TABLE_NAME || ' is immutable';
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_quant_research_cycle_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.program_id IS DISTINCT FROM OLD.program_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR NEW.slot_key IS DISTINCT FROM OLD.slot_key
       OR NEW.sequence_no IS DISTINCT FROM OLD.sequence_no
       OR NEW.scheduled_for IS DISTINCT FROM OLD.scheduled_for
       OR NEW.scheduled_local IS DISTINCT FROM OLD.scheduled_local
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'quant research cycle schedule is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.run_id IS NOT NULL AND NEW.run_id IS DISTINCT FROM OLD.run_id THEN
        RAISE EXCEPTION 'quant research cycle run link is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.mandate_id IS NOT NULL
       AND NEW.mandate_id IS DISTINCT FROM OLD.mandate_id THEN
        RAISE EXCEPTION 'quant research cycle mandate link is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.validation_id IS NOT NULL
       AND NEW.validation_id IS DISTINCT FROM OLD.validation_id THEN
        RAISE EXCEPTION 'quant research cycle validation link is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.outcome_json IS NOT NULL AND (
       NEW.outcome_json IS DISTINCT FROM OLD.outcome_json
       OR NEW.outcome_sha256 IS DISTINCT FROM OLD.outcome_sha256) THEN
        RAISE EXCEPTION 'quant research cycle outcome is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def _postgres_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return value
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix(
            "postgres://"
        )
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix(
            "postgresql://"
        )
    return value


def install_quant_research_program_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    for table in (
        "quant_research_programs",
        "quant_research_program_events",
        "quant_research_cycle_events",
    ):
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(
            f'DROP TRIGGER IF EXISTS "{trigger}" ON {table}'
        )
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE '
            f"ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION stock_assistant_reject_mutation()"
        )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS "
        "trg_quant_research_cycles_guard_pg ON quant_research_cycles"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_research_cycles_guard_pg "
        "BEFORE UPDATE ON quant_research_cycles FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_quant_research_cycle_guard()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS "
        "trg_quant_research_cycles_no_delete_pg ON quant_research_cycles"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_research_cycles_no_delete_pg "
        "BEFORE DELETE ON quant_research_cycles FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_reject_mutation()"
    )
    checksum = hashlib.sha256(
        (POSTGRES_DDL + POSTGRES_GUARDS).encode("utf-8")
    ).hexdigest()
    connection.execute(
        text(
            """
            INSERT INTO platform_schema_migrations(
                migration_id, checksum, applied_at
            ) VALUES (:migration_id, :checksum, :applied_at)
            ON CONFLICT (migration_id) DO UPDATE
            SET checksum=EXCLUDED.checksum,
                applied_at=EXCLUDED.applied_at
            """
        ),
        {
            "migration_id": MIGRATION_ID,
            "checksum": checksum,
            "applied_at": dt.datetime.now(
                dt.timezone.utc
            ).isoformat(timespec="milliseconds"),
        },
    )


def migrate(database_url: str) -> None:
    if not is_postgres_target(database_url):
        raise ValueError("量化研究计划生产迁移只接受 PostgreSQL DATABASE_URL")
    engine = create_engine(
        _postgres_url(database_url),
        future=True,
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_quant_research_program_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Quant Research Program v1 schema"
    )
    parser.add_argument(
        "--database-url",
        default=(
            os.getenv("STOCK_ASSISTANT_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        ),
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url 或 STOCK_ASSISTANT_DATABASE_URL 必须提供")
    migrate(str(args.database_url))
    print(f"applied {MIGRATION_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
