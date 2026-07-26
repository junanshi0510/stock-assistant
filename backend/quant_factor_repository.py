# -*- coding: utf-8 -*-
"""Durable storage for point-in-time factor collection and replay.

The warehouse is shared market data. Raw factor observations and sync events
are append-only; plans and runs only expose narrowly guarded lifecycle fields.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from database import (
    INTEGRITY_ERRORS,
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
)


PLAN_SCHEMA_VERSION = "quant_factor_backfill_plan.v1"
RUN_SCHEMA_VERSION = "quant_factor_sync_run.v1"
EVENT_SCHEMA_VERSION = "quant_factor_sync_event.v1"
DAILY_SCHEMA_VERSION = "quant_factor_daily_observation.v1"
FINANCIAL_SCHEMA_VERSION = "quant_factor_financial_observation.v1"
DATASETS = {"valuation_daily", "financial_indicator"}
TERMINAL_RUN_STATUSES = {"succeeded", "partial", "failed"}
REQUIRED_TABLES = {
    "quant_factor_backfill_plans",
    "quant_factor_sync_runs",
    "quant_factor_sync_events",
    "quant_factor_daily_observations",
    "quant_factor_financial_observations",
}


class QuantFactorRepositoryError(RuntimeError):
    pass


class QuantFactorConflictError(QuantFactorRepositoryError):
    pass


class QuantFactorNotFoundError(QuantFactorRepositoryError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_payload(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="milliseconds")


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


SQLITE_SCHEMA = """
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
    pe_ttm REAL,
    pb REAL,
    dividend_yield_ttm REAL,
    total_market_value REAL,
    circulating_market_value REAL,
    free_turnover_rate REAL,
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
    roe REAL,
    gross_profit_margin REAL,
    operating_cashflow_to_revenue REAL,
    debt_to_assets REAL,
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

CREATE TRIGGER IF NOT EXISTS trg_quant_factor_events_no_update
BEFORE UPDATE ON quant_factor_sync_events BEGIN
    SELECT RAISE(ABORT, 'quant factor sync events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_events_no_delete
BEFORE DELETE ON quant_factor_sync_events BEGIN
    SELECT RAISE(ABORT, 'quant factor sync events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_daily_no_update
BEFORE UPDATE ON quant_factor_daily_observations BEGIN
    SELECT RAISE(ABORT, 'quant factor daily observations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_daily_no_delete
BEFORE DELETE ON quant_factor_daily_observations BEGIN
    SELECT RAISE(ABORT, 'quant factor daily observations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_financial_no_update
BEFORE UPDATE ON quant_factor_financial_observations BEGIN
    SELECT RAISE(ABORT, 'quant factor financial observations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_financial_no_delete
BEFORE DELETE ON quant_factor_financial_observations BEGIN
    SELECT RAISE(ABORT, 'quant factor financial observations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_plans_immutable_policy
BEFORE UPDATE ON quant_factor_backfill_plans
WHEN NEW.schema_version != OLD.schema_version
  OR NEW.dataset != OLD.dataset
  OR NEW.provider != OLD.provider
  OR NEW.start_date != OLD.start_date
  OR NEW.end_date != OLD.end_date
  OR NEW.symbols_json != OLD.symbols_json
  OR NEW.symbols_sha256 != OLD.symbols_sha256
  OR NEW.policy_json != OLD.policy_json
  OR NEW.policy_sha256 != OLD.policy_sha256
  OR NEW.actor_id != OLD.actor_id
  OR NEW.created_at != OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'quant factor plan policy is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_plans_terminal
BEFORE UPDATE ON quant_factor_backfill_plans
WHEN OLD.status IN ('completed','cancelled') AND NEW.status != OLD.status
BEGIN
    SELECT RAISE(ABORT, 'terminal quant factor plan cannot transition');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_plans_no_delete
BEFORE DELETE ON quant_factor_backfill_plans BEGIN
    SELECT RAISE(ABORT, 'quant factor plans cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_runs_immutable_request
BEFORE UPDATE ON quant_factor_sync_runs
WHEN NEW.schema_version != OLD.schema_version
  OR COALESCE(NEW.plan_id, '') != COALESCE(OLD.plan_id, '')
  OR NEW.request_key != OLD.request_key
  OR NEW.dataset != OLD.dataset
  OR NEW.provider != OLD.provider
  OR NEW.mode != OLD.mode
  OR COALESCE(NEW.target_date, '') != COALESCE(OLD.target_date, '')
  OR COALESCE(NEW.target_symbol, '') != COALESCE(OLD.target_symbol, '')
  OR COALESCE(NEW.period_start, '') != COALESCE(OLD.period_start, '')
  OR COALESCE(NEW.period_end, '') != COALESCE(OLD.period_end, '')
  OR NEW.actor_id != OLD.actor_id
  OR NEW.request_json != OLD.request_json
  OR NEW.request_sha256 != OLD.request_sha256
  OR NEW.created_at != OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'quant factor sync request is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_runs_immutable_result
BEFORE UPDATE ON quant_factor_sync_runs
WHEN OLD.stats_json IS NOT NULL AND (
    COALESCE(NEW.stats_json, '') != COALESCE(OLD.stats_json, '')
    OR COALESCE(NEW.stats_sha256, '') != COALESCE(OLD.stats_sha256, '')
)
BEGIN
    SELECT RAISE(ABORT, 'quant factor sync result is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_runs_terminal
BEFORE UPDATE ON quant_factor_sync_runs
WHEN OLD.status IN ('succeeded','partial') AND NEW.status != OLD.status
BEGIN
    SELECT RAISE(ABORT, 'completed quant factor sync cannot transition');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_factor_runs_no_delete
BEFORE DELETE ON quant_factor_sync_runs BEGIN
    SELECT RAISE(ABORT, 'quant factor sync runs cannot be deleted');
END;
"""


class QuantFactorRepository:
    def __init__(
        self,
        database_target: str | os.PathLike[str] | None = None,
    ) -> None:
        self.database_target = str(
            database_target
            or configured_database_target(
                str(Path(__file__).resolve().parent / "stock_assistant.db")
            )
        )
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with connect_database(
                self.database_target,
                close_on_exit=True,
            ) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
            self._schema_ready = True

    def _connect(self):
        self._ensure_schema()
        return connect_database(self.database_target, close_on_exit=True)

    @staticmethod
    def _assert_writable_sync_attempt(
        connection,
        run_id: str,
        *,
        expected_attempt: int,
    ) -> None:
        row = connection.execute(
            """
            SELECT status, attempt_count, lease_expires_at
            FROM quant_factor_sync_runs WHERE id=?
            """,
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise QuantFactorNotFoundError("量化因子同步任务不存在")
        if (
            str(row["status"]) != "running"
            or int(row["attempt_count"] or 0) != int(expected_attempt)
        ):
            raise QuantFactorConflictError(
                "量化因子同步任务租约已失效，旧 Worker 不能写入观察"
            )
        lease_value = row["lease_expires_at"]
        if lease_value and str(lease_value) <= _iso():
            raise QuantFactorConflictError(
                "量化因子同步任务租约已过期，旧 Worker 不能写入观察"
            )

    @staticmethod
    def _plan_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["symbols"] = _load(item.pop("symbols_json", None), [])
        item["policy"] = _load(item.pop("policy_json", None), {})
        item["integrity"] = {
            "symbols_verified": (
                sha256_payload(item["symbols"])
                == item.get("symbols_sha256")
            ),
            "policy_verified": (
                sha256_payload(item["policy"])
                == item.get("policy_sha256")
            ),
        }
        return item

    @staticmethod
    def _run_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["request"] = _load(item.pop("request_json", None), {})
        item["stats"] = _load(item.pop("stats_json", None), None)
        item["integrity"] = {
            "request_verified": (
                sha256_payload(item["request"])
                == item.get("request_sha256")
            ),
            "stats_verified": (
                item.get("stats_sha256") is None
                or sha256_payload(item["stats"])
                == item.get("stats_sha256")
            ),
        }
        return item

    @staticmethod
    def _event_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["details"] = _load(item.pop("details_json", None), {})
        return item

    def create_plan(
        self,
        policy: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        dataset = str(policy.get("dataset") or "")
        if dataset not in DATASETS:
            raise ValueError("invalid quant factor dataset")
        symbols = list(policy.get("symbols") or [])
        policy_text = canonical_json(policy)
        symbols_text = canonical_json(symbols)
        plan_id = _new_id("qf_plan")
        now = _iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quant_factor_backfill_plans(
                    id, schema_version, dataset, provider,
                    start_date, end_date, symbols_json, symbols_sha256,
                    policy_json, policy_sha256, status, actor_id,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL)
                """,
                (
                    plan_id,
                    PLAN_SCHEMA_VERSION,
                    dataset,
                    str(policy.get("provider") or "tushare"),
                    str(policy.get("start_date") or ""),
                    str(policy.get("end_date") or ""),
                    symbols_text,
                    sha256_text(symbols_text),
                    policy_text,
                    sha256_text(policy_text),
                    str(actor_id),
                    now,
                    now,
                ),
            )
        item = self.get_plan(plan_id)
        if item is None:
            raise QuantFactorRepositoryError(
                "created quant factor plan disappeared"
            )
        return item

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM quant_factor_backfill_plans WHERE id=?",
                (str(plan_id),),
            ).fetchone()
        return self._plan_from_row(row)

    def list_plans(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM quant_factor_backfill_plans
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._plan_from_row(row)) is not None
        ]

    def list_active_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM quant_factor_backfill_plans
                WHERE status='active'
                ORDER BY updated_at, created_at, id LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._plan_from_row(row)) is not None
        ]

    def transition_plan(
        self,
        plan_id: str,
        status: str,
    ) -> dict[str, Any]:
        if status not in {"active", "paused", "completed", "cancelled"}:
            raise ValueError("invalid quant factor plan status")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM quant_factor_backfill_plans WHERE id=?",
                (str(plan_id),),
            ).fetchone()
            if row is None:
                raise QuantFactorNotFoundError("量化因子回填计划不存在")
            current = str(row["status"])
            if current in {"completed", "cancelled"} and current != status:
                raise QuantFactorConflictError(
                    "已结束的量化因子回填计划不能再次变更"
                )
            now = _iso()
            connection.execute(
                """
                UPDATE quant_factor_backfill_plans
                SET status=?, updated_at=?,
                    completed_at=CASE
                        WHEN ? IN ('completed','cancelled')
                        THEN COALESCE(completed_at, ?)
                        ELSE NULL
                    END
                WHERE id=?
                """,
                (status, now, status, now, str(plan_id)),
            )
        item = self.get_plan(plan_id)
        if item is None:
            raise QuantFactorRepositoryError(
                "updated quant factor plan disappeared"
            )
        return item

    def create_sync_run(
        self,
        request: dict[str, Any],
        *,
        actor_id: str,
    ) -> tuple[dict[str, Any], bool]:
        dataset = str(request.get("dataset") or "")
        if dataset not in DATASETS:
            raise ValueError("invalid quant factor dataset")
        request_payload = dict(request)
        request_text = canonical_json(request_payload)
        request_key = sha256_text(
            canonical_json(
                {
                    key: request_payload.get(key)
                    for key in (
                        "dataset",
                        "provider",
                        "target_date",
                        "target_symbol",
                        "period_start",
                        "period_end",
                    )
                }
            )
        )
        run_id = _new_id("qf_sync")
        created_at = _iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quant_factor_sync_runs(
                    id, schema_version, plan_id, request_key,
                    dataset, provider, mode, target_date, target_symbol,
                    period_start, period_end, status, attempt_count,
                    actor_id, request_json, request_sha256,
                    stats_json, stats_sha256, error_code, error_message,
                    created_at, started_at, completed_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0,
                          ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL)
                ON CONFLICT(request_key) DO NOTHING
                """,
                (
                    run_id,
                    RUN_SCHEMA_VERSION,
                    request_payload.get("plan_id"),
                    request_key,
                    dataset,
                    str(request_payload.get("provider") or "tushare"),
                    str(
                        request_payload.get("mode")
                        or "historical_backfill"
                    ),
                    request_payload.get("target_date"),
                    request_payload.get("target_symbol"),
                    request_payload.get("period_start"),
                    request_payload.get("period_end"),
                    str(actor_id),
                    request_text,
                    sha256_text(request_text),
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM quant_factor_sync_runs WHERE request_key=?",
                (request_key,),
            ).fetchone()
        item = self._run_from_row(row)
        if item is None:
            raise QuantFactorRepositoryError(
                "created quant factor sync disappeared"
            )
        created = item["id"] == run_id
        if created:
            self.append_event(
                run_id,
                "queued",
                actor_id=actor_id,
                details={"request_sha256": item["request_sha256"]},
            )
        return item, created

    def get_sync_run(
        self,
        run_id: str,
        *,
        include_events: bool = True,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM quant_factor_sync_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
            events = (
                connection.execute(
                    """
                    SELECT * FROM quant_factor_sync_events
                    WHERE run_id=? ORDER BY sequence_no
                    """,
                    (str(run_id),),
                ).fetchall()
                if row is not None and include_events
                else []
            )
        item = self._run_from_row(row)
        if item is not None and include_events:
            item["events"] = [
                event
                for event_row in events
                if (event := self._event_from_row(event_row)) is not None
            ]
            previous = None
            verified = True
            for event in item["events"]:
                material = {
                    "run_id": event["run_id"],
                    "sequence_no": event["sequence_no"],
                    "event_type": event["event_type"],
                    "actor_id": event["actor_id"],
                    "details": event["details"],
                    "previous_hash": previous,
                    "created_at": event["created_at"],
                }
                expected = sha256_payload(material)
                verified = (
                    verified
                    and event.get("previous_hash") == previous
                    and event.get("event_hash") == expected
                )
                previous = event.get("event_hash")
            item["integrity"]["event_chain_verified"] = verified
        return item

    def list_sync_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM quant_factor_sync_runs
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._run_from_row(row)) is not None
        ]

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created_at = _iso()
        payload = dict(details or {})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT id FROM quant_factor_sync_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
            if run is None:
                raise QuantFactorNotFoundError("量化因子同步任务不存在")
            previous = connection.execute(
                """
                SELECT sequence_no, event_hash
                FROM quant_factor_sync_events
                WHERE run_id=? ORDER BY sequence_no DESC LIMIT 1
                """,
                (str(run_id),),
            ).fetchone()
            sequence = int(previous["sequence_no"]) + 1 if previous else 1
            previous_hash = str(previous["event_hash"]) if previous else None
            material = {
                "run_id": str(run_id),
                "sequence_no": sequence,
                "event_type": str(event_type),
                "actor_id": str(actor_id),
                "details": payload,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            event_hash = sha256_payload(material)
            event_id = _new_id("qf_event")
            connection.execute(
                """
                INSERT INTO quant_factor_sync_events(
                    id, run_id, sequence_no, event_type, actor_id,
                    details_json, previous_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(run_id),
                    sequence,
                    str(event_type),
                    str(actor_id),
                    canonical_json(payload),
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM quant_factor_sync_events WHERE id=?",
                (event_id,),
            ).fetchone()
        item = self._event_from_row(row)
        if item is None:
            raise QuantFactorRepositoryError(
                "created quant factor sync event disappeared"
            )
        item["schema_version"] = EVENT_SCHEMA_VERSION
        return item

    def claim_sync_run(
        self,
        run_id: str,
        *,
        actor_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM quant_factor_sync_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) != "queued":
                return self.get_sync_run(run_id, include_events=False)
            now = _iso()
            lease_seconds = max(
                360,
                int(
                    os.getenv(
                        "QUANT_FACTOR_SYNC_LEASE_SECONDS",
                        "600",
                    )
                ),
            )
            lease_expires_at = _iso(
                _utc_now() + dt.timedelta(seconds=lease_seconds)
            )
            connection.execute(
                """
                UPDATE quant_factor_sync_runs
                SET status='running', attempt_count=attempt_count+1,
                    started_at=?, completed_at=NULL,
                    error_code=NULL, error_message=NULL,
                    lease_expires_at=?
                WHERE id=? AND status='queued'
                """,
                (now, lease_expires_at, str(run_id)),
            )
        self.append_event(
            run_id,
            "running",
            actor_id=actor_id,
            details={},
        )
        return self.get_sync_run(run_id, include_events=False)

    def complete_sync_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        stats: dict[str, Any],
        partial: bool = False,
    ) -> dict[str, Any]:
        stats_text = canonical_json(stats)
        status = "partial" if partial else "succeeded"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, stats_json FROM quant_factor_sync_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise QuantFactorNotFoundError("量化因子同步任务不存在")
            if str(row["status"]) in {"succeeded", "partial"}:
                item = self.get_sync_run(run_id)
                if item is None:
                    raise QuantFactorRepositoryError(
                        "completed quant factor sync disappeared"
                    )
                return item
            if str(row["status"]) != "running":
                raise QuantFactorConflictError(
                    "量化因子同步任务不在运行状态"
                )
            connection.execute(
                """
                UPDATE quant_factor_sync_runs
                SET status=?, stats_json=?, stats_sha256=?,
                    error_code=NULL, error_message=NULL, completed_at=?,
                    lease_expires_at=NULL
                WHERE id=?
                """,
                (
                    status,
                    stats_text,
                    sha256_text(stats_text),
                    _iso(),
                    str(run_id),
                ),
            )
        self.append_event(
            run_id,
            status,
            actor_id=actor_id,
            details={
                "stats_sha256": sha256_text(stats_text),
                "inserted_rows": int(stats.get("inserted_rows") or 0),
            },
        )
        item = self.get_sync_run(run_id)
        if item is None:
            raise QuantFactorRepositoryError(
                "completed quant factor sync disappeared"
            )
        return item

    def fail_sync_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        message = str(error_message or "")[:1000]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM quant_factor_sync_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise QuantFactorNotFoundError("量化因子同步任务不存在")
            if str(row["status"]) in {"succeeded", "partial"}:
                raise QuantFactorConflictError(
                    "已完成的量化因子同步任务不能失败"
                )
            connection.execute(
                """
                UPDATE quant_factor_sync_runs
                SET status='failed', error_code=?, error_message=?,
                    completed_at=?, lease_expires_at=NULL
                WHERE id=?
                """,
                (
                    str(error_code)[:100],
                    message,
                    _iso(),
                    str(run_id),
                ),
            )
        self.append_event(
            run_id,
            "failed",
            actor_id=actor_id,
            details={
                "error_code": str(error_code)[:100],
                "error_message": message,
            },
        )
        item = self.get_sync_run(run_id)
        if item is None:
            raise QuantFactorRepositoryError(
                "failed quant factor sync disappeared"
            )
        return item

    def requeue_failed_sync(
        self,
        run_id: str,
        *,
        actor_id: str,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, attempt_count
                FROM quant_factor_sync_runs WHERE id=?
                """,
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise QuantFactorNotFoundError("量化因子同步任务不存在")
            if str(row["status"]) != "failed":
                raise QuantFactorConflictError("只有失败任务可以重新排队")
            if int(row["attempt_count"] or 0) >= int(max_attempts):
                raise QuantFactorConflictError("量化因子同步任务已达到重试上限")
            connection.execute(
                """
                UPDATE quant_factor_sync_runs
                SET status='queued', error_code=NULL, error_message=NULL,
                    started_at=NULL, completed_at=NULL,
                    lease_expires_at=NULL
                WHERE id=?
                """,
                (str(run_id),),
            )
        self.append_event(
            run_id,
            "requeued",
            actor_id=actor_id,
            details={},
        )
        item = self.get_sync_run(run_id)
        if item is None:
            raise QuantFactorRepositoryError(
                "requeued quant factor sync disappeared"
            )
        return item

    def has_active_sync(self) -> bool:
        return self.oldest_active_sync() is not None

    def oldest_active_sync(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM quant_factor_sync_runs
                WHERE status IN ('queued','running')
                ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,
                         created_at, id
                LIMIT 1
                """
            ).fetchone()
        return self._run_from_row(row)

    def recover_stale_syncs(
        self,
        *,
        actor_id: str,
        now: dt.datetime | None = None,
    ) -> list[str]:
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=dt.timezone.utc)
        current_text = _iso(current.astimezone(dt.timezone.utc))
        recovered: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id FROM quant_factor_sync_runs
                WHERE status='running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at<=?
                ORDER BY lease_expires_at, id
                """,
                (current_text,),
            ).fetchall()
            recovered = [str(row["id"]) for row in rows]
            for run_id in recovered:
                connection.execute(
                    """
                    UPDATE quant_factor_sync_runs
                    SET status='failed',
                        error_code='QUANT_FACTOR_WORKER_LEASE_EXPIRED',
                        error_message='量化因子采集 Worker 租约过期，任务已安全回收',
                        completed_at=?, lease_expires_at=NULL
                    WHERE id=? AND status='running'
                    """,
                    (current_text, run_id),
                )
        for run_id in recovered:
            self.append_event(
                run_id,
                "lease_expired",
                actor_id=actor_id,
                details={
                    "error_code": "QUANT_FACTOR_WORKER_LEASE_EXPIRED"
                },
            )
        return recovered

    def target_run(
        self,
        *,
        dataset: str,
        target_date: str | None = None,
        target_symbol: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            if dataset == "valuation_daily":
                row = connection.execute(
                    """
                    SELECT * FROM quant_factor_sync_runs
                    WHERE dataset='valuation_daily' AND target_date=?
                    ORDER BY created_at DESC, id DESC LIMIT 1
                    """,
                    (str(target_date),),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM quant_factor_sync_runs
                    WHERE dataset='financial_indicator'
                      AND target_symbol=?
                      AND period_start=? AND period_end=?
                    ORDER BY created_at DESC, id DESC LIMIT 1
                    """,
                    (
                        str(target_symbol),
                        str(period_start),
                        str(period_end),
                    ),
                ).fetchone()
        return self._run_from_row(row)

    def target_resolved(
        self,
        *,
        dataset: str,
        target_date: str | None = None,
        target_symbol: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            if dataset == "valuation_daily":
                observation = connection.execute(
                    """
                    SELECT 1 FROM quant_factor_daily_observations
                    WHERE trade_date=? LIMIT 1
                    """,
                    (str(target_date),),
                ).fetchone()
                run = connection.execute(
                    """
                    SELECT 1 FROM quant_factor_sync_runs
                    WHERE dataset='valuation_daily' AND target_date=?
                      AND status IN ('succeeded','partial')
                    LIMIT 1
                    """,
                    (str(target_date),),
                ).fetchone()
            else:
                observation = connection.execute(
                    """
                    SELECT 1 FROM quant_factor_financial_observations
                    WHERE symbol=? AND report_end_date>=?
                      AND report_end_date<=? LIMIT 1
                    """,
                    (
                        str(target_symbol),
                        str(period_start),
                        str(period_end),
                    ),
                ).fetchone()
                run = connection.execute(
                    """
                    SELECT 1 FROM quant_factor_sync_runs
                    WHERE dataset='financial_indicator'
                      AND target_symbol=?
                      AND period_start=? AND period_end=?
                      AND status IN ('succeeded','partial')
                    LIMIT 1
                    """,
                    (
                        str(target_symbol),
                        str(period_start),
                        str(period_end),
                    ),
                ).fetchone()
        return observation is not None or run is not None

    def resolved_daily_dates(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT trade_date AS target
                FROM quant_factor_daily_observations
                WHERE trade_date>=? AND trade_date<=?
                UNION
                SELECT target_date AS target
                FROM quant_factor_sync_runs
                WHERE dataset='valuation_daily'
                  AND target_date>=? AND target_date<=?
                  AND status IN ('succeeded','partial')
                """,
                (
                    str(start_date),
                    str(end_date),
                    str(start_date),
                    str(end_date),
                ),
            ).fetchall()
        return {
            str(row["target"])
            for row in rows
            if row["target"] is not None
        }

    def resolved_financial_symbols(
        self,
        symbols: list[str],
        *,
        period_start: str,
        period_end: str,
    ) -> set[str]:
        values = sorted({str(symbol) for symbol in symbols if symbol})
        if not values:
            return set()
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT symbol AS target
                FROM quant_factor_financial_observations
                WHERE symbol IN ({placeholders})
                  AND report_end_date>=? AND report_end_date<=?
                GROUP BY symbol
                UNION
                SELECT target_symbol AS target
                FROM quant_factor_sync_runs
                WHERE dataset='financial_indicator'
                  AND target_symbol IN ({placeholders})
                  AND period_start=? AND period_end=?
                  AND status IN ('succeeded','partial')
                """,
                (
                    *values,
                    str(period_start),
                    str(period_end),
                    *values,
                    str(period_start),
                    str(period_end),
                ),
            ).fetchall()
        return {
            str(row["target"])
            for row in rows
            if row["target"] is not None
        }

    def save_daily_observations(
        self,
        run_id: str,
        rows: list[dict[str, Any]],
        *,
        expected_attempt: int,
        provider: str,
        capture_mode: str,
        retrieved_at: str,
    ) -> dict[str, int]:
        prepared: list[tuple[Any, ...]] = []
        hashes_by_key: dict[tuple[str, str], set[str]] = {}
        for row in rows:
            payload = dict(row.get("payload") or {})
            payload_text = canonical_json(payload)
            payload_hash = sha256_text(payload_text)
            symbol = str(row["symbol"])
            trade_date = str(row["trade_date"])
            identity = canonical_json(
                {
                    "dataset": "valuation_daily",
                    "provider": provider,
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "payload_sha256": payload_hash,
                }
            )
            observation_id = f"qf_daily_{sha256_text(identity)[:40]}"
            hashes_by_key.setdefault((symbol, trade_date), set()).add(
                payload_hash
            )
            prepared.append(
                (
                    observation_id,
                    DAILY_SCHEMA_VERSION,
                    "A股",
                    symbol,
                    str(row["ts_code"]),
                    trade_date,
                    _finite_or_none(row.get("pe_ttm")),
                    _finite_or_none(row.get("pb")),
                    _finite_or_none(row.get("dividend_yield_ttm")),
                    _finite_or_none(row.get("total_market_value")),
                    _finite_or_none(
                        row.get("circulating_market_value")
                    ),
                    _finite_or_none(row.get("free_turnover_rate")),
                    str(provider),
                    str(capture_mode),
                    str(retrieved_at),
                    str(run_id),
                    payload_text,
                    payload_hash,
                )
            )
        unique_ids = {item[0] for item in prepared}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_writable_sync_attempt(
                connection,
                run_id,
                expected_attempt=expected_attempt,
            )
            existing_rows = connection.execute(
                """
                SELECT symbol, trade_date, payload_sha256
                FROM quant_factor_daily_observations
                WHERE provider=? AND trade_date IN (
                    SELECT DISTINCT trade_date
                    FROM quant_factor_daily_observations
                    WHERE source_run_id=?
                )
                """,
                (str(provider), str(run_id)),
            ).fetchall()
            for existing in existing_rows:
                hashes_by_key.setdefault(
                    (
                        str(existing["symbol"]),
                        str(existing["trade_date"]),
                    ),
                    set(),
                ).add(str(existing["payload_sha256"]))
            if prepared:
                connection.executemany(
                    """
                    INSERT INTO quant_factor_daily_observations(
                        id, schema_version, market, symbol, ts_code,
                        trade_date, pe_ttm, pb, dividend_yield_ttm,
                        total_market_value, circulating_market_value,
                        free_turnover_rate, provider, capture_mode,
                        retrieved_at, source_run_id, payload_json,
                        payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    prepared,
                )
            stored = connection.execute(
                """
                SELECT COUNT(*) AS value
                FROM quant_factor_daily_observations
                WHERE source_run_id=?
                """,
                (str(run_id),),
            ).fetchone()
            if rows:
                dates = sorted({str(row["trade_date"]) for row in rows})
                placeholders = ",".join("?" for _ in dates)
                all_versions = connection.execute(
                    f"""
                    SELECT symbol, trade_date, payload_sha256
                    FROM quant_factor_daily_observations
                    WHERE provider=? AND trade_date IN ({placeholders})
                    """,
                    (str(provider), *dates),
                ).fetchall()
                for existing in all_versions:
                    hashes_by_key.setdefault(
                        (
                            str(existing["symbol"]),
                            str(existing["trade_date"]),
                        ),
                        set(),
                    ).add(str(existing["payload_sha256"]))
        inserted = int(stored["value"] if stored else 0)
        return {
            "input_rows": len(rows),
            "unique_rows": len(unique_ids),
            "inserted_rows": inserted,
            "duplicate_rows": max(0, len(rows) - inserted),
            "conflict_keys": sum(
                len(hashes) > 1 for hashes in hashes_by_key.values()
            ),
        }

    def save_financial_observations(
        self,
        run_id: str,
        rows: list[dict[str, Any]],
        *,
        expected_attempt: int,
        provider: str,
        capture_mode: str,
        retrieved_at: str,
    ) -> dict[str, int]:
        prepared: list[tuple[Any, ...]] = []
        keys = set()
        for row in rows:
            payload = dict(row.get("payload") or {})
            payload_text = canonical_json(payload)
            payload_hash = sha256_text(payload_text)
            symbol = str(row["symbol"])
            announcement = str(row["announcement_date"])
            report_end = str(row["report_end_date"])
            identity = canonical_json(
                {
                    "dataset": "financial_indicator",
                    "provider": provider,
                    "symbol": symbol,
                    "announcement_date": announcement,
                    "report_end_date": report_end,
                    "payload_sha256": payload_hash,
                }
            )
            observation_id = (
                f"qf_financial_{sha256_text(identity)[:40]}"
            )
            keys.add((symbol, announcement, report_end))
            prepared.append(
                (
                    observation_id,
                    FINANCIAL_SCHEMA_VERSION,
                    "A股",
                    symbol,
                    str(row["ts_code"]),
                    announcement,
                    report_end,
                    _finite_or_none(row.get("roe")),
                    _finite_or_none(row.get("gross_profit_margin")),
                    _finite_or_none(
                        row.get("operating_cashflow_to_revenue")
                    ),
                    _finite_or_none(row.get("debt_to_assets")),
                    (
                        str(row.get("update_flag"))
                        if row.get("update_flag") is not None
                        else None
                    ),
                    str(provider),
                    str(capture_mode),
                    str(retrieved_at),
                    str(run_id),
                    payload_text,
                    payload_hash,
                )
            )
        unique_ids = {item[0] for item in prepared}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_writable_sync_attempt(
                connection,
                run_id,
                expected_attempt=expected_attempt,
            )
            if prepared:
                connection.executemany(
                    """
                    INSERT INTO quant_factor_financial_observations(
                        id, schema_version, market, symbol, ts_code,
                        announcement_date, report_end_date, roe,
                        gross_profit_margin, operating_cashflow_to_revenue,
                        debt_to_assets, update_flag, provider, capture_mode,
                        retrieved_at, source_run_id, payload_json,
                        payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    prepared,
                )
            stored = connection.execute(
                """
                SELECT COUNT(*) AS value
                FROM quant_factor_financial_observations
                WHERE source_run_id=?
                """,
                (str(run_id),),
            ).fetchone()
            conflict_keys = 0
            for symbol, announcement, report_end in keys:
                row = connection.execute(
                    """
                    SELECT COUNT(DISTINCT payload_sha256) AS value
                    FROM quant_factor_financial_observations
                    WHERE provider=? AND symbol=?
                      AND announcement_date=? AND report_end_date=?
                    """,
                    (
                        str(provider),
                        symbol,
                        announcement,
                        report_end,
                    ),
                ).fetchone()
                if row and int(row["value"] or 0) > 1:
                    conflict_keys += 1
        inserted = int(stored["value"] if stored else 0)
        return {
            "input_rows": len(rows),
            "unique_rows": len(unique_ids),
            "inserted_rows": inserted,
            "duplicate_rows": max(0, len(rows) - inserted),
            "conflict_keys": conflict_keys,
        }

    def load_daily_rows(
        self,
        symbols: list[str],
        *,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        values = sorted({str(symbol) for symbol in symbols if symbol})
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM quant_factor_daily_observations
                WHERE symbol IN ({placeholders})
                  AND trade_date>=? AND trade_date<=?
                ORDER BY symbol, trade_date, retrieved_at, id
                """,
                (*values, str(start_date), str(end_date)),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load(item.pop("payload_json", None), {})
            output.append(item)
        return output

    def load_financial_rows(
        self,
        symbols: list[str],
        *,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        values = sorted({str(symbol) for symbol in symbols if symbol})
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM quant_factor_financial_observations
                WHERE symbol IN ({placeholders})
                  AND announcement_date>=? AND announcement_date<=?
                ORDER BY symbol, announcement_date, report_end_date,
                         retrieved_at, id
                """,
                (*values, str(start_date), str(end_date)),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load(item.pop("payload_json", None), {})
            output.append(item)
        return output

    def dataset_stats(self) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            daily = connection.execute(
                """
                SELECT COUNT(*) AS row_count,
                       COUNT(DISTINCT symbol) AS symbol_count,
                       COUNT(DISTINCT trade_date) AS date_count,
                       MIN(trade_date) AS first_date,
                       MAX(trade_date) AS last_date,
                       MAX(retrieved_at) AS last_retrieved_at
                FROM quant_factor_daily_observations
                """
            ).fetchone()
            financial = connection.execute(
                """
                SELECT COUNT(*) AS row_count,
                       COUNT(DISTINCT symbol) AS symbol_count,
                       COUNT(DISTINCT announcement_date) AS date_count,
                       MIN(announcement_date) AS first_date,
                       MAX(announcement_date) AS last_date,
                       MAX(retrieved_at) AS last_retrieved_at
                FROM quant_factor_financial_observations
                """
            ).fetchone()
            daily_conflicts = connection.execute(
                """
                SELECT COUNT(*) AS value FROM (
                    SELECT provider, symbol, trade_date
                    FROM quant_factor_daily_observations
                    GROUP BY provider, symbol, trade_date
                    HAVING COUNT(DISTINCT payload_sha256)>1
                ) conflicts
                """
            ).fetchone()
            financial_conflicts = connection.execute(
                """
                SELECT COUNT(*) AS value FROM (
                    SELECT provider, symbol, announcement_date,
                           report_end_date
                    FROM quant_factor_financial_observations
                    GROUP BY provider, symbol, announcement_date,
                             report_end_date
                    HAVING COUNT(DISTINCT payload_sha256)>1
                ) conflicts
                """
            ).fetchone()
            latest_daily = connection.execute(
                """
                SELECT trade_date, COUNT(DISTINCT symbol) AS symbol_count
                FROM quant_factor_daily_observations
                WHERE trade_date=(
                    SELECT MAX(trade_date)
                    FROM quant_factor_daily_observations
                )
                GROUP BY trade_date
                """
            ).fetchone()
        return {
            "valuation_daily": {
                **dict(daily),
                "conflict_key_count": int(
                    daily_conflicts["value"] if daily_conflicts else 0
                ),
                "latest_cross_section_symbol_count": int(
                    latest_daily["symbol_count"] if latest_daily else 0
                ),
            },
            "financial_indicator": {
                **dict(financial),
                "conflict_key_count": int(
                    financial_conflicts["value"]
                    if financial_conflicts
                    else 0
                ),
            },
        }

    def symbol_coverage(
        self,
        symbols: list[str],
    ) -> list[dict[str, Any]]:
        values = sorted({str(symbol) for symbol in symbols if symbol})
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            daily = connection.execute(
                f"""
                SELECT symbol, COUNT(*) AS row_count,
                       COUNT(DISTINCT trade_date) AS date_count,
                       MIN(trade_date) AS first_date,
                       MAX(trade_date) AS last_date
                FROM quant_factor_daily_observations
                WHERE symbol IN ({placeholders})
                GROUP BY symbol
                """,
                tuple(values),
            ).fetchall()
            financial = connection.execute(
                f"""
                SELECT symbol, COUNT(*) AS row_count,
                       COUNT(DISTINCT report_end_date) AS report_count,
                       MIN(announcement_date) AS first_announcement_date,
                       MAX(announcement_date) AS last_announcement_date
                FROM quant_factor_financial_observations
                WHERE symbol IN ({placeholders})
                GROUP BY symbol
                """,
                tuple(values),
            ).fetchall()
        daily_by_symbol = {
            str(row["symbol"]): dict(row) for row in daily
        }
        financial_by_symbol = {
            str(row["symbol"]): dict(row) for row in financial
        }
        return [
            {
                "symbol": symbol,
                "valuation": daily_by_symbol.get(
                    symbol,
                    {
                        "row_count": 0,
                        "date_count": 0,
                        "first_date": None,
                        "last_date": None,
                    },
                ),
                "financial": financial_by_symbol.get(
                    symbol,
                    {
                        "row_count": 0,
                        "report_count": 0,
                        "first_announcement_date": None,
                        "last_announcement_date": None,
                    },
                ),
            }
            for symbol in values
        ]


repository = QuantFactorRepository()
