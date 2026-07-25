# -*- coding: utf-8 -*-
"""PostgreSQL schema for the point-in-time quant selection lab."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "quant-selection-lab.v1"
MIGRATION_LOCK_ID = 7_941_223_508_641_127

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS quant_selection_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('queued','running','succeeded','partial','failed','cancelled')
    ),
    job_id TEXT,
    policy_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    result_json TEXT,
    result_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_runs_scope
ON quant_selection_runs(tenant_id, user_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS quant_selection_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_run_events
ON quant_selection_run_events(run_id, sequence_no);

CREATE TABLE IF NOT EXISTS quant_selection_shadow_mandates (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_mandates_scope
ON quant_selection_shadow_mandates(
    tenant_id, user_id, created_at DESC, id DESC
);
"""

POSTGRES_GUARDS = """
CREATE OR REPLACE FUNCTION stock_assistant_quant_selection_run_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR NEW.engine_version IS DISTINCT FROM OLD.engine_version
       OR NEW.policy_json IS DISTINCT FROM OLD.policy_json
       OR NEW.policy_sha256 IS DISTINCT FROM OLD.policy_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'quant selection run input is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.result_json IS NOT NULL AND (
       NEW.result_json IS DISTINCT FROM OLD.result_json
       OR NEW.result_sha256 IS DISTINCT FROM OLD.result_sha256) THEN
        RAISE EXCEPTION 'quant selection run result is immutable'
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


def install_quant_selection_lab_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    # The shared immutable-row function is installed by earlier migrations.
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
        "quant_selection_run_events",
        "quant_selection_shadow_mandates",
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
        "DROP TRIGGER IF EXISTS trg_quant_selection_run_guard_pg "
        "ON quant_selection_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_selection_run_guard_pg "
        "BEFORE UPDATE ON quant_selection_runs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_quant_selection_run_guard()"
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
        raise ValueError(
            "组合选股实验室生产迁移只接受 PostgreSQL DATABASE_URL"
        )
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
            install_quant_selection_lab_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Quant Selection Lab v1 schema"
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
