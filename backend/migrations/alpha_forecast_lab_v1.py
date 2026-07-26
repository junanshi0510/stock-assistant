# -*- coding: utf-8 -*-
"""PostgreSQL schema for the calibrated multi-horizon Alpha laboratory."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "alpha-forecast-lab.v1"
MIGRATION_LOCK_ID = 6_118_947_325_770_421

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS alpha_forecast_programs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK(asset_type IN ('stock','fund')),
    market TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','paused','retired')),
    policy_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alpha_programs_scope
ON alpha_forecast_programs(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_alpha_programs_due
ON alpha_forecast_programs(status, next_run_at);

CREATE TABLE IF NOT EXISTS alpha_forecast_program_events (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL
        REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(program_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_alpha_program_events
ON alpha_forecast_program_events(program_id, sequence_no);

CREATE TABLE IF NOT EXISTS alpha_forecast_runs (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL
        REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('queued','running','succeeded','partial','failed','cancelled')
    ),
    request_key TEXT NOT NULL,
    task_id TEXT,
    as_of_date TEXT NOT NULL,
    input_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    result_json TEXT,
    result_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(program_id, request_key)
);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_scope
ON alpha_forecast_runs(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_alpha_runs_program
ON alpha_forecast_runs(program_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS alpha_forecast_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL
        REFERENCES alpha_forecast_runs(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_alpha_run_events
ON alpha_forecast_run_events(run_id, sequence_no);

CREATE TABLE IF NOT EXISTS alpha_forecasts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL
        REFERENCES alpha_forecast_runs(id) ON DELETE RESTRICT,
    program_id TEXT NOT NULL
        REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    horizon_sessions INTEGER NOT NULL,
    as_of_date TEXT NOT NULL,
    eligible_after TEXT NOT NULL,
    calibrated_probability DOUBLE PRECISION,
    base_rate DOUBLE PRECISION,
    stance TEXT NOT NULL,
    historical_gate_passed INTEGER NOT NULL,
    decision_eligible INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, symbol, horizon_sessions)
);
CREATE INDEX IF NOT EXISTS idx_alpha_forecasts_pending
ON alpha_forecasts(eligible_after, created_at, id);
CREATE INDEX IF NOT EXISTS idx_alpha_forecasts_scope
ON alpha_forecasts(tenant_id, user_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS alpha_forecast_outcomes (
    id TEXT PRIMARY KEY,
    forecast_id TEXT NOT NULL UNIQUE
        REFERENCES alpha_forecasts(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL
        REFERENCES alpha_forecast_runs(id) ON DELETE RESTRICT,
    program_id TEXT NOT NULL
        REFERENCES alpha_forecast_programs(id) ON DELETE RESTRICT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    target_return_pct DOUBLE PRECISION NOT NULL,
    realized_label INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alpha_outcomes_program
ON alpha_forecast_outcomes(program_id, observed_date, id);
"""

POSTGRES_GUARDS = """
CREATE OR REPLACE FUNCTION stock_assistant_alpha_program_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR NEW.engine_version IS DISTINCT FROM OLD.engine_version
       OR NEW.name IS DISTINCT FROM OLD.name
       OR NEW.asset_type IS DISTINCT FROM OLD.asset_type
       OR NEW.market IS DISTINCT FROM OLD.market
       OR NEW.policy_json IS DISTINCT FROM OLD.policy_json
       OR NEW.policy_sha256 IS DISTINCT FROM OLD.policy_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'alpha forecast program input is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_alpha_run_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.program_id IS DISTINCT FROM OLD.program_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR NEW.engine_version IS DISTINCT FROM OLD.engine_version
       OR NEW.request_key IS DISTINCT FROM OLD.request_key
       OR NEW.as_of_date IS DISTINCT FROM OLD.as_of_date
       OR NEW.input_json IS DISTINCT FROM OLD.input_json
       OR NEW.input_sha256 IS DISTINCT FROM OLD.input_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'alpha forecast run input is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.result_json IS NOT NULL AND (
       NEW.result_json IS DISTINCT FROM OLD.result_json
       OR NEW.result_sha256 IS DISTINCT FROM OLD.result_sha256) THEN
        RAISE EXCEPTION 'alpha forecast run result is immutable'
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
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def install_alpha_forecast_lab_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION stock_assistant_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = 'integrity_constraint_violation',
                MESSAGE = TG_TABLE_NAME || ' is immutable';
        END;
        $$;
        """
    )
    connection.exec_driver_sql(POSTGRES_GUARDS)
    for table in (
        "alpha_forecast_program_events",
        "alpha_forecast_run_events",
        "alpha_forecasts",
        "alpha_forecast_outcomes",
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
        "DROP TRIGGER IF EXISTS trg_alpha_program_guard_pg "
        "ON alpha_forecast_programs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_alpha_program_guard_pg "
        "BEFORE UPDATE ON alpha_forecast_programs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_alpha_program_guard()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_alpha_program_delete_pg "
        "ON alpha_forecast_programs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_alpha_program_delete_pg "
        "BEFORE DELETE ON alpha_forecast_programs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_reject_mutation()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_alpha_run_guard_pg "
        "ON alpha_forecast_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_alpha_run_guard_pg "
        "BEFORE UPDATE ON alpha_forecast_runs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_alpha_run_guard()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_alpha_run_delete_pg "
        "ON alpha_forecast_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_alpha_run_delete_pg "
        "BEFORE DELETE ON alpha_forecast_runs FOR EACH ROW "
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
        raise ValueError("Alpha 概率实验室生产迁移只接受 PostgreSQL DATABASE_URL")
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
            install_alpha_forecast_lab_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install calibrated Alpha forecast lab v1 schema"
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
        parser.error(
            "--database-url 或 STOCK_ASSISTANT_DATABASE_URL 必须提供"
        )
    migrate(str(args.database_url))
    print(f"applied {MIGRATION_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
