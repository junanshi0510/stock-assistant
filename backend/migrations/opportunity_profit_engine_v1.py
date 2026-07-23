# -*- coding: utf-8 -*-
"""PostgreSQL migration for the opportunity profit-validation engine."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "opportunity-profit-engine.v1"
MIGRATION_LOCK_ID = 6_715_083_214_229_009

POSTGRES_DDL = """
ALTER TABLE opportunity_paper_observations
ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunity_observation_idempotency
ON opportunity_paper_observations(user_id, basket_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS opportunity_profit_policy_versions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    strategy_version_id TEXT NOT NULL REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL CHECK(version_no >= 1),
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, strategy_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_profit_policy_strategy
ON opportunity_profit_policy_versions(user_id, strategy_id, version_no DESC);

CREATE TABLE IF NOT EXISTS opportunity_profit_scorecards (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    strategy_version_id TEXT NOT NULL REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    policy_id TEXT NOT NULL REFERENCES opportunity_profit_policy_versions(id) ON DELETE RESTRICT,
    schema_version TEXT NOT NULL,
    evidence_cutoff_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, strategy_id, policy_id, payload_sha256)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_profit_scorecards_strategy
ON opportunity_profit_scorecards(user_id, strategy_id, created_at DESC);
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
"""


def _postgres_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return value
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def install_opportunity_profit_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    for table in (
        "opportunity_profit_policy_versions",
        "opportunity_profit_scorecards",
    ):
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION stock_assistant_reject_mutation()"
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
        raise ValueError("机会收益引擎生产迁移只接受 PostgreSQL DATABASE_URL")
    engine = create_engine(_postgres_url(database_url), future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_opportunity_profit_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Opportunity Profit Engine v1 schema"
    )
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
