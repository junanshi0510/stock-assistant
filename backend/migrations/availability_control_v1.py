# -*- coding: utf-8 -*-
"""PostgreSQL schema for availability probes and incident transitions."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os

from sqlalchemy import create_engine, text

from database import is_postgres_target


MIGRATION_ID = "availability-control.v1"
MIGRATION_LOCK_ID = 5_203_114_729_447_824

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS availability_probe_runs (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    method_version TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('scheduled','manual','manual_deep','deployment')),
    actor_id TEXT NOT NULL,
    overall_status TEXT NOT NULL CHECK(overall_status IN ('operational','degraded','outage','unknown')),
    effective_status TEXT NOT NULL CHECK(effective_status IN ('operational','degraded','outage','unknown')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_availability_probe_recent
ON availability_probe_runs(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS availability_incident_events (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    component_id TEXT NOT NULL,
    category TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('incident_opened','severity_changed','incident_resolved')),
    from_state TEXT NOT NULL CHECK(from_state IN ('operational','degraded','outage','unknown')),
    to_state TEXT NOT NULL CHECK(to_state IN ('operational','degraded','outage','unknown')),
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(incident_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_availability_incident_recent
ON availability_incident_events(created_at DESC, incident_id, sequence_no DESC);
"""

POSTGRES_GUARD = """
CREATE OR REPLACE FUNCTION stock_assistant_reject_availability_mutation()
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


def install_availability_schema(connection) -> None:
    connection.exec_driver_sql(POSTGRES_DDL)
    connection.exec_driver_sql(POSTGRES_GUARD)
    for table in ("availability_probe_runs", "availability_incident_events"):
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION stock_assistant_reject_availability_mutation()"
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
        raise ValueError("高可用控制面生产迁移只接受 PostgreSQL DATABASE_URL")
    engine = create_engine(_postgres_url(database_url), future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            install_availability_schema(connection)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Availability Control v1 schema")
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
