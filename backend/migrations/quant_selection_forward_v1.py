# -*- coding: utf-8 -*-
"""PostgreSQL schema for quant-selection forward validation links."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "quant-selection-forward-validation.v1"
MIGRATION_LOCK_ID = 7_941_223_508_641_128

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS quant_selection_forward_validations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    quant_mandate_id TEXT NOT NULL
        REFERENCES quant_selection_shadow_mandates(id) ON DELETE RESTRICT,
    quant_run_id TEXT NOT NULL
        REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    quant_snapshot_sha256 TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    opportunity_strategy_id TEXT NOT NULL
        REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    opportunity_strategy_version_id TEXT NOT NULL
        REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    opportunity_run_id TEXT NOT NULL
        REFERENCES opportunity_runs(id) ON DELETE RESTRICT,
    opportunity_basket_id TEXT NOT NULL
        REFERENCES opportunity_paper_baskets(id) ON DELETE RESTRICT,
    profit_policy_id TEXT NOT NULL
        REFERENCES opportunity_profit_policy_versions(id) ON DELETE RESTRICT,
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, quant_mandate_id),
    UNIQUE(opportunity_basket_id)
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_forward_scope
ON quant_selection_forward_validations(
    tenant_id, user_id, created_at DESC, id DESC
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_forward_strategy
ON quant_selection_forward_validations(
    user_id, opportunity_strategy_id, created_at DESC
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


def install_quant_selection_forward_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    trigger = "trg_quant_selection_forward_validations_immutable_pg"
    connection.exec_driver_sql(
        f'DROP TRIGGER IF EXISTS "{trigger}" '
        "ON quant_selection_forward_validations"
    )
    connection.exec_driver_sql(
        f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE '
        "ON quant_selection_forward_validations FOR EACH ROW "
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
            "量化前向验证生产迁移只接受 PostgreSQL DATABASE_URL"
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
            install_quant_selection_forward_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Quant Selection Forward Validation v1 schema"
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
