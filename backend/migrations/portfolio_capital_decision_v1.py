# -*- coding: utf-8 -*-
"""PostgreSQL migration for immutable portfolio capital decision plans."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "portfolio-capital-decision.v1"
MIGRATION_LOCK_ID = 7_941_223_508_641_107

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_capital_decision_plans (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ready','watch','blocked')),
    decision_date TEXT NOT NULL,
    profile_version_id TEXT,
    valuation_snapshot_id TEXT,
    action_report_id TEXT,
    exposure_snapshot_id TEXT,
    evidence_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, engine_version, evidence_sha256)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_plans_scope
ON portfolio_capital_decision_plans(
    tenant_id, user_id, created_at DESC, id DESC
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_plans_evidence
ON portfolio_capital_decision_plans(
    user_id, evidence_sha256, created_at DESC
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


def install_portfolio_capital_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    trigger = "trg_portfolio_capital_decision_plans_immutable_pg"
    connection.exec_driver_sql(
        f'DROP TRIGGER IF EXISTS "{trigger}" '
        "ON portfolio_capital_decision_plans"
    )
    connection.exec_driver_sql(
        f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE '
        "ON portfolio_capital_decision_plans FOR EACH ROW "
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
        raise ValueError(
            "组合资金决策引擎生产迁移只接受 PostgreSQL DATABASE_URL"
        )
    engine = create_engine(
        _postgres_url(database_url), future=True, pool_pre_ping=True
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_portfolio_capital_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Portfolio Capital Decision v1 schema"
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
