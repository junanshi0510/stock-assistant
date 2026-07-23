# -*- coding: utf-8 -*-
"""Dependency readiness for API and deployment probes."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from database import (
    configured_database_target,
    connect_database,
    database_dialect,
    redact_database_url,
    table_exists,
)
from observability import QUEUE_DEPTH
from task_queue import (
    QUEUE_AGENT,
    QUEUE_LLM,
    QUEUE_MARKET,
    QUEUE_OCR,
    QUEUE_SCHEDULER,
    celery_app,
    redis_readiness,
    uses_celery_queue,
)


_cache_lock = threading.Lock()
_cached: tuple[float, dict[str, Any]] | None = None


def _database_readiness() -> dict[str, Any]:
    target = configured_database_target(
        str(Path(__file__).resolve().parent / "stock_assistant.db")
    )
    try:
        with connect_database(target, close_on_exit=True) as connection:
            connection.execute("SELECT 1 AS ready").fetchone()
            migrated = (
                table_exists(connection, "platform_schema_migrations")
                if database_dialect(connection) == "postgresql"
                else True
            )
            opportunity_schema = (
                all(
                    table_exists(connection, table)
                    for table in (
                        "opportunity_strategies",
                        "opportunity_strategy_versions",
                        "opportunity_runs",
                        "opportunity_run_events",
                        "opportunity_paper_baskets",
                        "opportunity_paper_observations",
                    )
                )
                if database_dialect(connection) == "postgresql"
                else True
            )
            opportunity_profit_schema = (
                all(
                    table_exists(connection, table)
                    for table in (
                        "opportunity_profit_policy_versions",
                        "opportunity_profit_scorecards",
                    )
                )
                if database_dialect(connection) == "postgresql"
                else True
            )
            portfolio_capital_schema = (
                table_exists(
                    connection, "portfolio_capital_decision_plans"
                )
                if database_dialect(connection) == "postgresql"
                else True
            )
            portfolio_twin_schema = (
                table_exists(connection, "portfolio_twin_runs")
                if database_dialect(connection) == "postgresql"
                else True
            )
            portfolio_valuation_schema = (
                all(
                    table_exists(connection, table)
                    for table in (
                        "market_observations",
                        "portfolio_valuation_snapshots",
                    )
                )
                if database_dialect(connection) == "postgresql"
                else True
            )
            availability_schema = (
                all(
                    table_exists(connection, table)
                    for table in (
                        "availability_probe_runs",
                        "availability_incident_events",
                    )
                )
                if database_dialect(connection) == "postgresql"
                else True
            )
        return {
            "ready": bool(
                migrated
                and opportunity_schema
                and opportunity_profit_schema
                and portfolio_capital_schema
                and portfolio_twin_schema
                and portfolio_valuation_schema
                and availability_schema
            ),
            "dialect": database_dialect(target),
            "target": redact_database_url(target),
            "platform_schema": bool(migrated),
            "opportunity_schema": bool(opportunity_schema),
            "opportunity_profit_schema": bool(opportunity_profit_schema),
            "portfolio_capital_schema": bool(portfolio_capital_schema),
            "portfolio_twin_schema": bool(portfolio_twin_schema),
            "portfolio_valuation_schema": bool(portfolio_valuation_schema),
            "availability_schema": bool(availability_schema),
        }
    except Exception as error:
        return {
            "ready": False,
            "dialect": database_dialect(target),
            "target": redact_database_url(target),
            "error": type(error).__name__,
        }


def _worker_readiness() -> dict[str, Any]:
    if not uses_celery_queue():
        return {"ready": True, "mode": "embedded", "queues": {}}
    required = {
        item.strip()
        for item in str(
            os.getenv(
                "REQUIRED_WORKER_QUEUES",
                f"{QUEUE_AGENT},{QUEUE_MARKET},{QUEUE_LLM},{QUEUE_OCR},{QUEUE_SCHEDULER}",
            )
        ).split(",")
        if item.strip()
    }
    try:
        active = celery_app.control.inspect(timeout=1.5).active_queues() or {}
        available: set[str] = set()
        workers: dict[str, list[str]] = {}
        for worker, queues in active.items():
            names = sorted({str(item.get("name")) for item in queues or [] if item.get("name")})
            workers[str(worker)] = names
            available.update(names)
        missing = sorted(required - available)
        return {
            "ready": not missing,
            "mode": "celery",
            "workers": workers,
            "missing_queues": missing,
        }
    except Exception as error:
        return {"ready": False, "mode": "celery", "error": type(error).__name__}


def _object_storage_readiness() -> dict[str, Any]:
    required = str(os.getenv("REQUIRE_OBJECT_STORAGE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not required:
        return {"ready": True, "required": False, "configured": False}
    try:
        from object_storage import AliyunObjectStorage

        result = AliyunObjectStorage().readiness()
        result["required"] = True
        result["configured"] = True
        return result
    except Exception as error:
        return {
            "ready": False,
            "required": True,
            "configured": False,
            "error": type(error).__name__,
        }


def readiness(*, use_cache: bool = True) -> dict[str, Any]:
    global _cached
    now = time.monotonic()
    with _cache_lock:
        if use_cache and _cached and now - _cached[0] < 10:
            return _cached[1]
    database = _database_readiness()
    redis = redis_readiness()
    for queue, depth in (redis.get("queue_depths") or {}).items():
        QUEUE_DEPTH.labels(queue=queue).set(depth)
    workers = _worker_readiness()
    objects = _object_storage_readiness()
    traffic_ready = bool(database.get("ready"))
    full_service_ready = all(
        item.get("ready") for item in (database, redis, workers, objects)
    )
    result = {
        # Readiness means that the API can safely serve authoritative, persisted
        # facts. Optional/async capabilities are reported separately so a worker
        # outage does not unnecessarily remove read-only traffic.
        "ready": traffic_ready,
        "traffic_ready": traffic_ready,
        "full_service_ready": full_service_ready,
        "status": (
            "operational" if full_service_ready
            else "degraded" if traffic_ready
            else "outage"
        ),
        "database": database,
        "redis": redis,
        "workers": workers,
        "object_storage": objects,
    }
    with _cache_lock:
        _cached = (now, result)
    return result
