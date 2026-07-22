# -*- coding: utf-8 -*-
"""PostgreSQL schema for immutable portfolio decision-twin runs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "portfolio-decision-twin.v1"
MIGRATION_LOCK_ID = 4_118_203_627_715_902

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_twin_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    method_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('complete','partial','blocked')),
    scenario_json TEXT NOT NULL,
    scenario_sha256 TEXT NOT NULL,
    holdings_json TEXT NOT NULL,
    holdings_sha256 TEXT NOT NULL,
    exposure_snapshot_id TEXT,
    exposure_json TEXT NOT NULL,
    exposure_sha256 TEXT NOT NULL,
    profile_version_id TEXT,
    profile_json TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_twin_runs_scope
ON portfolio_twin_runs(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_twin_runs_evidence
ON portfolio_twin_runs(user_id, holdings_sha256, created_at DESC);
"""

POSTGRES_GUARD = """
CREATE OR REPLACE FUNCTION stock_assistant_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'integrity_constraint_violation',
        MESSAGE = TG_TABLE_NAME || ' is immutable';
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


def install_portfolio_twin_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARD)
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_portfolio_twin_runs_immutable_pg ON portfolio_twin_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_portfolio_twin_runs_immutable_pg "
        "BEFORE UPDATE OR DELETE ON portfolio_twin_runs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_reject_mutation()"
    )
    checksum = hashlib.sha256((POSTGRES_DDL + POSTGRES_GUARD).encode("utf-8")).hexdigest()
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
            "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        },
    )


def migrate(database_url: str) -> None:
    if not is_postgres_target(database_url):
        raise ValueError("组合数字孪生生产迁移只接受 PostgreSQL DATABASE_URL")
    engine = create_engine(_postgres_url(database_url), future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_portfolio_twin_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Portfolio Decision Twin v1 schema")
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
