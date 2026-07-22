# -*- coding: utf-8 -*-
"""PostgreSQL schema for durable market observations and portfolio valuation."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "portfolio-valuation.v1"
MIGRATION_LOCK_ID = 5_203_114_729_447_821

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS market_observations (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('price','nav','fx')),
    asset_type TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    currency TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL CHECK(value > 0),
    as_of TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    quality_status TEXT NOT NULL CHECK(quality_status IN ('primary','fallback','identity')),
    retrieved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_observation_latest
ON market_observations(kind, market, symbol, retrieved_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_market_observation_expiry
ON market_observations(expires_at, kind);

CREATE TABLE IF NOT EXISTS portfolio_valuation_snapshots (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    holdings_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('complete','partial','blocked')),
    base_currency TEXT NOT NULL,
    total_value DOUBLE PRECISION,
    coverage_pct DOUBLE PRECISION NOT NULL,
    decision_eligible INTEGER NOT NULL CHECK(decision_eligible IN (0, 1)),
    fresh_until TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_valuation_scope
ON portfolio_valuation_snapshots(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_valuation_binding
ON portfolio_valuation_snapshots(user_id, holdings_sha256, created_at DESC);
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


def install_portfolio_valuation_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARD)
    for table in ("market_observations", "portfolio_valuation_snapshots"):
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION stock_assistant_reject_mutation()"
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
        raise ValueError("组合估值生产迁移只接受 PostgreSQL DATABASE_URL")
    engine = create_engine(_postgres_url(database_url), future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_portfolio_valuation_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Portfolio Valuation v1 schema")
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
