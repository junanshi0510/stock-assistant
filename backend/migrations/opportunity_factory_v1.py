# -*- coding: utf-8 -*-
"""Incremental PostgreSQL schema for the Opportunity Factory milestone."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "opportunity-factory.v1"
MIGRATION_LOCK_ID = 6_715_083_214_229_004

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS opportunity_strategies (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
    current_version_no INTEGER NOT NULL CHECK(current_version_no >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opportunity_strategies_user
ON opportunity_strategies(user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_strategy_versions (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK(version_no >= 1),
    schema_version TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(strategy_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_strategy_versions
ON opportunity_strategy_versions(strategy_id, version_no DESC);

CREATE TABLE IF NOT EXISTS opportunity_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    strategy_version_id TEXT NOT NULL REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    strategy_version_no INTEGER NOT NULL,
    strategy_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled')),
    job_id TEXT,
    progress_json TEXT NOT NULL,
    result_json TEXT,
    result_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_opportunity_runs_user
ON opportunity_runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunity_runs_strategy
ON opportunity_runs(strategy_id, created_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES opportunity_runs(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_run_events
ON opportunity_run_events(run_id, sequence_no);

CREATE TABLE IF NOT EXISTS opportunity_paper_baskets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES opportunity_runs(id) ON DELETE RESTRICT,
    schema_version TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_paper_baskets_user
ON opportunity_paper_baskets(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_paper_observations (
    id TEXT PRIMARY KEY,
    basket_id TEXT NOT NULL REFERENCES opportunity_paper_baskets(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(basket_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_paper_observations
ON opportunity_paper_observations(basket_id, sequence_no DESC);
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

CREATE OR REPLACE FUNCTION stock_assistant_opportunity_run_result_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.result_json IS NOT NULL AND (
       NEW.result_json IS DISTINCT FROM OLD.result_json
       OR NEW.result_sha256 IS DISTINCT FROM OLD.result_sha256) THEN
        RAISE EXCEPTION 'opportunity run result is immutable'
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


def install_opportunity_factory_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    for table in (
        "opportunity_strategy_versions",
        "opportunity_run_events",
        "opportunity_paper_baskets",
        "opportunity_paper_observations",
    ):
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION stock_assistant_reject_mutation()"
        )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_opportunity_run_result_guard_pg ON opportunity_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_opportunity_run_result_guard_pg "
        "BEFORE UPDATE ON opportunity_runs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_opportunity_run_result_guard()"
    )
    checksum = hashlib.sha256(
        (POSTGRES_DDL + POSTGRES_GUARDS).encode("utf-8")
    ).hexdigest()
    connection.execute(
        text(
            """
            INSERT INTO platform_schema_migrations(migration_id, checksum, applied_at)
            VALUES (:migration_id, :checksum, :applied_at)
            ON CONFLICT (migration_id) DO UPDATE
            SET checksum=EXCLUDED.checksum, applied_at=EXCLUDED.applied_at
            """
        ),
        {
            "migration_id": MIGRATION_ID,
            "checksum": checksum,
            "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="milliseconds"
            ),
        },
    )


def migrate(database_url: str) -> None:
    if not is_postgres_target(database_url):
        raise ValueError("机会工厂生产迁移只接受 PostgreSQL DATABASE_URL")
    engine = create_engine(_postgres_url(database_url), future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_opportunity_factory_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Opportunity Factory v1 schema")
    parser.add_argument(
        "--database-url",
        default=os.getenv("STOCK_ASSISTANT_DATABASE_URL") or os.getenv("DATABASE_URL"),
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url 或 STOCK_ASSISTANT_DATABASE_URL 必须提供")
    migrate(str(args.database_url))
    print(f"applied {MIGRATION_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
