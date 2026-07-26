# -*- coding: utf-8 -*-
"""PostgreSQL schema for the point-in-time A-share factor warehouse."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "quant-factor-warehouse.v1"
MIGRATION_LOCK_ID = 6_815_334_927_410_853

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS quant_factor_backfill_plans (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    dataset TEXT NOT NULL CHECK(dataset IN (
        'valuation_daily','financial_indicator'
    )),
    provider TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    symbols_json TEXT NOT NULL,
    symbols_sha256 TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'active','paused','completed','cancelled'
    )),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_plans_status
ON quant_factor_backfill_plans(status, updated_at, id);

CREATE TABLE IF NOT EXISTS quant_factor_sync_runs (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    plan_id TEXT REFERENCES quant_factor_backfill_plans(id)
        ON DELETE RESTRICT,
    request_key TEXT NOT NULL UNIQUE,
    dataset TEXT NOT NULL CHECK(dataset IN (
        'valuation_daily','financial_indicator'
    )),
    provider TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN (
        'live_incremental','historical_backfill'
    )),
    target_date TEXT,
    target_symbol TEXT,
    period_start TEXT,
    period_end TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'queued','running','succeeded','partial','failed'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    actor_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    stats_json TEXT,
    stats_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    lease_expires_at TEXT,
    CHECK(
        (dataset='valuation_daily'
            AND target_date IS NOT NULL
            AND target_symbol IS NULL)
        OR
        (dataset='financial_indicator'
            AND target_symbol IS NOT NULL
            AND period_start IS NOT NULL
            AND period_end IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_sync_status
ON quant_factor_sync_runs(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_quant_factor_sync_target
ON quant_factor_sync_runs(
    dataset, provider, target_date, target_symbol, completed_at DESC
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_sync_plan
ON quant_factor_sync_runs(plan_id, status, created_at, id);

CREATE TABLE IF NOT EXISTS quant_factor_sync_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES quant_factor_sync_runs(id)
        ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS quant_factor_daily_observations (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market='A股'),
    symbol TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    pe_ttm DOUBLE PRECISION,
    pb DOUBLE PRECISION,
    dividend_yield_ttm DOUBLE PRECISION,
    total_market_value DOUBLE PRECISION,
    circulating_market_value DOUBLE PRECISION,
    free_turnover_rate DOUBLE PRECISION,
    provider TEXT NOT NULL,
    capture_mode TEXT NOT NULL CHECK(capture_mode IN (
        'live_incremental','historical_backfill'
    )),
    retrieved_at TEXT NOT NULL,
    source_run_id TEXT NOT NULL REFERENCES quant_factor_sync_runs(id)
        ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_daily_symbol_date
ON quant_factor_daily_observations(
    symbol, trade_date, retrieved_at, id
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_daily_date
ON quant_factor_daily_observations(
    trade_date, symbol, retrieved_at, id
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_daily_source
ON quant_factor_daily_observations(source_run_id, id);

CREATE TABLE IF NOT EXISTS quant_factor_financial_observations (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market='A股'),
    symbol TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    announcement_date TEXT NOT NULL,
    report_end_date TEXT NOT NULL,
    roe DOUBLE PRECISION,
    gross_profit_margin DOUBLE PRECISION,
    operating_cashflow_to_revenue DOUBLE PRECISION,
    debt_to_assets DOUBLE PRECISION,
    update_flag TEXT,
    provider TEXT NOT NULL,
    capture_mode TEXT NOT NULL CHECK(capture_mode IN (
        'live_incremental','historical_backfill'
    )),
    retrieved_at TEXT NOT NULL,
    source_run_id TEXT NOT NULL REFERENCES quant_factor_sync_runs(id)
        ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_financial_symbol_date
ON quant_factor_financial_observations(
    symbol, announcement_date, report_end_date, retrieved_at, id
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_financial_announcement
ON quant_factor_financial_observations(
    announcement_date, symbol, report_end_date, retrieved_at, id
);
CREATE INDEX IF NOT EXISTS idx_quant_factor_financial_source
ON quant_factor_financial_observations(source_run_id, id);
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

CREATE OR REPLACE FUNCTION stock_assistant_quant_factor_plan_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR NEW.dataset IS DISTINCT FROM OLD.dataset
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.start_date IS DISTINCT FROM OLD.start_date
       OR NEW.end_date IS DISTINCT FROM OLD.end_date
       OR NEW.symbols_json IS DISTINCT FROM OLD.symbols_json
       OR NEW.symbols_sha256 IS DISTINCT FROM OLD.symbols_sha256
       OR NEW.policy_json IS DISTINCT FROM OLD.policy_json
       OR NEW.policy_sha256 IS DISTINCT FROM OLD.policy_sha256
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'quant factor plan policy is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('completed','cancelled')
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'terminal quant factor plan cannot transition'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_quant_factor_run_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
       OR NEW.request_key IS DISTINCT FROM OLD.request_key
       OR NEW.dataset IS DISTINCT FROM OLD.dataset
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.target_date IS DISTINCT FROM OLD.target_date
       OR NEW.target_symbol IS DISTINCT FROM OLD.target_symbol
       OR NEW.period_start IS DISTINCT FROM OLD.period_start
       OR NEW.period_end IS DISTINCT FROM OLD.period_end
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.request_json IS DISTINCT FROM OLD.request_json
       OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'quant factor sync request is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.stats_json IS NOT NULL AND (
       NEW.stats_json IS DISTINCT FROM OLD.stats_json
       OR NEW.stats_sha256 IS DISTINCT FROM OLD.stats_sha256) THEN
        RAISE EXCEPTION 'quant factor sync result is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('succeeded','partial')
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'completed quant factor sync cannot transition'
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


def install_quant_factor_warehouse_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARDS)
    for table in (
        "quant_factor_sync_events",
        "quant_factor_daily_observations",
        "quant_factor_financial_observations",
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
        "DROP TRIGGER IF EXISTS trg_quant_factor_plans_guard_pg "
        "ON quant_factor_backfill_plans"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_factor_plans_guard_pg "
        "BEFORE UPDATE ON quant_factor_backfill_plans FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_quant_factor_plan_guard()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_quant_factor_plans_no_delete_pg "
        "ON quant_factor_backfill_plans"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_factor_plans_no_delete_pg "
        "BEFORE DELETE ON quant_factor_backfill_plans FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_reject_mutation()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_quant_factor_runs_guard_pg "
        "ON quant_factor_sync_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_factor_runs_guard_pg "
        "BEFORE UPDATE ON quant_factor_sync_runs FOR EACH ROW "
        "EXECUTE FUNCTION stock_assistant_quant_factor_run_guard()"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_quant_factor_runs_no_delete_pg "
        "ON quant_factor_sync_runs"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER trg_quant_factor_runs_no_delete_pg "
        "BEFORE DELETE ON quant_factor_sync_runs FOR EACH ROW "
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
        raise ValueError("量化因子仓库生产迁移只接受 PostgreSQL DATABASE_URL")
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
            install_quant_factor_warehouse_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Point-in-time Quant Factor Warehouse v1 schema"
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
