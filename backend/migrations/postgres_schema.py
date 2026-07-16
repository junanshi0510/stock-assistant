# -*- coding: utf-8 -*-
"""PostgreSQL-only schema additions and integrity triggers."""

from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import text


PLATFORM_SCHEMA_VERSION = "postgres-platform.v1"

IMMUTABLE_TABLES = (
    "agent_batch_allocation_events",
    "agent_batch_purchase_attribution_snapshots",
    "agent_batch_purchase_transaction_bindings",
    "agent_batch_purchase_execution_events",
    "agent_batch_purchase_preflight_events",
    "auth_audit_events",
    "decision_check_events",
    "decision_task_events",
    "fund_switch_cost_reviews",
    "fund_switch_execution_reviews",
    "fund_switch_lifecycle_events",
    "fund_switch_quote_events",
    "holding_thesis_versions",
    "background_job_events",
    "object_asset_events",
)

UPDATE_IMMUTABLE_TABLES = (
    "portfolio_action_reports",
    "portfolio_exposure_snapshots",
)


INFRASTRUCTURE_DDL = """
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS object_assets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'available', 'quarantined', 'deleted')),
    retention_until TEXT,
    encryption_mode TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE (bucket, object_key)
);

CREATE INDEX IF NOT EXISTS idx_object_assets_user_recent
ON object_assets(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS object_asset_events (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES object_assets(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (asset_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_object_asset_events_chain
ON object_asset_events(asset_id, sequence_no);

CREATE TABLE IF NOT EXISTS background_jobs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')
    ),
    idempotency_key TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    object_asset_id TEXT REFERENCES object_assets(id) ON DELETE RESTRICT,
    result_json TEXT,
    result_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    celery_task_id TEXT,
    worker_id TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    UNIQUE (user_id, job_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_queue
ON background_jobs(queue_name, status, available_at, created_at);

CREATE INDEX IF NOT EXISTS idx_background_jobs_user_recent
ON background_jobs(user_id, created_at DESC);

ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS lease_expires_at TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS celery_task_id TEXT;

CREATE TABLE IF NOT EXISTS background_job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES background_jobs(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (job_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_background_job_events_chain
ON background_job_events(job_id, sequence_no);
"""


TRIGGER_FUNCTIONS_DDL = """
CREATE OR REPLACE FUNCTION stock_assistant_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'integrity_constraint_violation',
        MESSAGE = TG_TABLE_NAME || ' is immutable';
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_reject_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'integrity_constraint_violation',
        MESSAGE = TG_TABLE_NAME || ' cannot be updated';
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_profile_payload_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.version_no IS DISTINCT FROM OLD.version_no
       OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
       OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
       OR NEW.validation_json IS DISTINCT FROM OLD.validation_json
       OR NEW.questionnaire_version IS DISTINCT FROM OLD.questionnaire_version
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'investment profile version payload is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_profile_activation_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'draft' AND (
       NEW.consent_version IS DISTINCT FROM OLD.consent_version
       OR NEW.consent_text_sha256 IS DISTINCT FROM OLD.consent_text_sha256
       OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
       OR NEW.review_due_at IS DISTINCT FROM OLD.review_due_at) THEN
        RAISE EXCEPTION 'investment profile activation metadata is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION stock_assistant_profile_status_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT (
       NEW.status = OLD.status
       OR (OLD.status = 'draft' AND NEW.status = 'active')
       OR (OLD.status = 'active' AND NEW.status = 'superseded')) THEN
        RAISE EXCEPTION 'invalid investment profile status transition'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def _execute_script(connection, script: str) -> None:
    for statement in script.split(";\n"):
        sql = statement.strip()
        if sql:
            connection.execute(text(sql))


def install_postgres_runtime_schema(connection) -> None:
    """Install infrastructure tables and database-enforced audit invariants."""
    # Functions contain semicolons, so send these two scripts as complete PostgreSQL blocks.
    connection.exec_driver_sql(INFRASTRUCTURE_DDL)
    connection.exec_driver_sql(TRIGGER_FUNCTIONS_DDL)

    for table in IMMUTABLE_TABLES:
        trigger = f"trg_{table}_immutable_pg"
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE OR DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION stock_assistant_reject_mutation()"
        )

    for table in UPDATE_IMMUTABLE_TABLES:
        trigger = f"trg_{table}_update_immutable_pg"
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"')
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION stock_assistant_reject_update()"
        )

    profile_triggers = (
        (
            "trg_investment_profile_payload_immutable_pg",
            "stock_assistant_profile_payload_guard",
        ),
        (
            "trg_investment_profile_activation_immutable_pg",
            "stock_assistant_profile_activation_guard",
        ),
        (
            "trg_investment_profile_status_transition_pg",
            "stock_assistant_profile_status_guard",
        ),
    )
    for trigger, function in profile_triggers:
        connection.exec_driver_sql(
            f'DROP TRIGGER IF EXISTS "{trigger}" ON investment_profile_versions'
        )
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE ON investment_profile_versions '
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )

    checksum = hashlib.sha256(
        (INFRASTRUCTURE_DDL + TRIGGER_FUNCTIONS_DDL).encode("utf-8")
    ).hexdigest()
    applied_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
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
            "migration_id": PLATFORM_SCHEMA_VERSION,
            "checksum": checksum,
            "applied_at": applied_at,
        },
    )
