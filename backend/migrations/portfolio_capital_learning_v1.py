# -*- coding: utf-8 -*-
"""PostgreSQL migration for capital-plan execution and outcome learning."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "portfolio-capital-learning.v1"
MIGRATION_LOCK_ID = 7_941_223_508_641_109

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_capital_execution_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES portfolio_capital_decision_plans(id),
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    event_no INTEGER NOT NULL CHECK(event_no > 0),
    previous_event_hash TEXT,
    status TEXT NOT NULL CHECK(
        status IN ('partial','reconciled','deviated','reviewed')
    ),
    transaction_count INTEGER NOT NULL CHECK(transaction_count > 0),
    planned_amount_cny DOUBLE PRECISION NOT NULL CHECK(planned_amount_cny >= 0),
    settled_amount_cny DOUBLE PRECISION NOT NULL CHECK(settled_amount_cny > 0),
    evidence_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, plan_id, event_no),
    UNIQUE(tenant_id, user_id, plan_id, evidence_sha256)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_execution_scope
ON portfolio_capital_execution_events(
    tenant_id, user_id, plan_id, event_no DESC
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_execution_due
ON portfolio_capital_execution_events(
    created_at DESC, tenant_id, user_id, plan_id
);

CREATE TABLE IF NOT EXISTS portfolio_capital_transaction_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES portfolio_capital_decision_plans(id),
    first_event_id TEXT NOT NULL REFERENCES portfolio_capital_execution_events(id),
    transaction_id BIGINT NOT NULL,
    transaction_sha256 TEXT NOT NULL,
    settled_amount_cny DOUBLE PRECISION NOT NULL CHECK(settled_amount_cny > 0),
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_binding_plan
ON portfolio_capital_transaction_bindings(
    tenant_id, user_id, plan_id, transaction_id
);

CREATE TABLE IF NOT EXISTS portfolio_capital_outcome_snapshots (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES portfolio_capital_decision_plans(id),
    execution_event_id TEXT NOT NULL REFERENCES portfolio_capital_execution_events(id),
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('collecting','partial','complete')
    ),
    evidence_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, execution_event_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_outcome_scope
ON portfolio_capital_outcome_snapshots(
    tenant_id, user_id, plan_id, observed_at DESC, id DESC
);
CREATE INDEX IF NOT EXISTS idx_portfolio_capital_outcome_event
ON portfolio_capital_outcome_snapshots(
    execution_event_id, observed_at DESC, id DESC
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

IMMUTABLE_TABLES = (
    "portfolio_capital_execution_events",
    "portfolio_capital_transaction_bindings",
    "portfolio_capital_outcome_snapshots",
)


def _postgres_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return value
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def install_portfolio_capital_learning_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    for table in IMMUTABLE_TABLES:
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(
            f'DROP TRIGGER IF EXISTS "{trigger}" ON {table}'
        )
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE '
            f"ON {table} FOR EACH ROW "
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
            "资本计划兑现与学习生产迁移只接受 PostgreSQL DATABASE_URL"
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
            install_portfolio_capital_learning_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Portfolio Capital Learning v1 schema"
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
