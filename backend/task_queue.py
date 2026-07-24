# -*- coding: utf-8 -*-
"""Celery/Redis task protocol and fail-closed dispatch helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from celery import Celery
from redis import Redis

from database import configured_database_target, is_postgres_target


QUEUE_AGENT = "agent"
QUEUE_MARKET = "market-data"
QUEUE_LLM = "llm"
QUEUE_OCR = "ocr"
QUEUE_SCHEDULER = "scheduler"

TASK_AGENT_RUN = "stock_assistant.agent.execute_run"
TASK_MARKET_TOOL = "stock_assistant.market.execute_tool_job"
TASK_MARKET_DATA = "stock_assistant.market.execute_data_job"
TASK_OPPORTUNITY_SCAN = "stock_assistant.market.execute_opportunity_scan"
TASK_PORTFOLIO_QUANT_RUN = (
    "stock_assistant.market.execute_portfolio_quant_run"
)
TASK_LLM_TOOL = "stock_assistant.llm.execute_tool_job"
TASK_OCR = "stock_assistant.ocr.process_job"
TASK_OBJECT_CLEANUP = "stock_assistant.ocr.cleanup_expired_objects"
TASK_DISPATCH_QUEUED = "stock_assistant.scheduler.dispatch_queued_runs"
TASK_OUTCOME_SCHEDULES = "stock_assistant.scheduler.outcome_schedules"
TASK_STRATEGY_SHADOW = "stock_assistant.scheduler.strategy_shadow"
TASK_DECISION_CHECKS = "stock_assistant.scheduler.decision_checks"
TASK_OPPORTUNITY_OBSERVATIONS = (
    "stock_assistant.scheduler.opportunity_observations"
)
TASK_CAPITAL_OUTCOMES = (
    "stock_assistant.scheduler.capital_plan_outcomes"
)
TASK_WATCHLIST_SCAN = "stock_assistant.scheduler.watchlist_scan"
TASK_AVAILABILITY_PROBE = "stock_assistant.scheduler.availability_probe"


class TaskQueueConfigurationError(RuntimeError):
    pass


class TaskQueueUnavailableError(RuntimeError):
    pass


def task_queue_mode() -> str:
    configured = str(os.getenv("TASK_QUEUE_MODE") or "").strip().lower()
    if configured:
        if configured not in {"celery", "embedded"}:
            raise TaskQueueConfigurationError("TASK_QUEUE_MODE 只能是 celery 或 embedded")
        return configured
    target = configured_database_target(
        str(Path(__file__).resolve().parent / "stock_assistant.db")
    )
    return "celery" if is_postgres_target(target) else "embedded"


def uses_celery_queue() -> bool:
    return task_queue_mode() == "celery"


def _redis_url() -> str:
    value = str(os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL") or "").strip()
    if uses_celery_queue() and not value:
        raise TaskQueueConfigurationError(
            "PostgreSQL 生产模式必须配置 REDIS_URL；系统不会回退到进程内任务"
        )
    return value or "memory://"


def _result_backend(broker_url: str) -> str | None:
    configured = str(os.getenv("CELERY_RESULT_BACKEND") or "").strip()
    if configured:
        return configured
    if broker_url.startswith(("redis://", "rediss://")):
        parsed = urlsplit(broker_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "/1", parsed.query, parsed.fragment))
    return None


def _redact_url(value: str) -> str:
    if not value.startswith(("redis://", "rediss://")):
        return value
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"***@{host}" if parsed.password else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


BROKER_URL = _redis_url()
celery_app = Celery(
    "stock_assistant",
    broker=BROKER_URL,
    backend=_result_backend(BROKER_URL),
    include=["background_tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=5,
    broker_pool_limit=10,
    broker_transport_options={
        "visibility_timeout": 7200,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
    },
    result_expires=3600,
    task_routes={
        TASK_AGENT_RUN: {"queue": QUEUE_AGENT},
        TASK_MARKET_TOOL: {"queue": QUEUE_MARKET},
        TASK_MARKET_DATA: {"queue": QUEUE_MARKET},
        TASK_OPPORTUNITY_SCAN: {"queue": QUEUE_MARKET},
        TASK_PORTFOLIO_QUANT_RUN: {"queue": QUEUE_MARKET},
        TASK_LLM_TOOL: {"queue": QUEUE_LLM},
        TASK_OCR: {"queue": QUEUE_OCR},
        TASK_OBJECT_CLEANUP: {"queue": QUEUE_OCR},
        TASK_DISPATCH_QUEUED: {"queue": QUEUE_SCHEDULER},
        TASK_OUTCOME_SCHEDULES: {"queue": QUEUE_SCHEDULER},
        TASK_STRATEGY_SHADOW: {"queue": QUEUE_SCHEDULER},
        TASK_DECISION_CHECKS: {"queue": QUEUE_SCHEDULER},
        TASK_OPPORTUNITY_OBSERVATIONS: {"queue": QUEUE_SCHEDULER},
        TASK_CAPITAL_OUTCOMES: {"queue": QUEUE_SCHEDULER},
        TASK_WATCHLIST_SCAN: {"queue": QUEUE_MARKET},
        TASK_AVAILABILITY_PROBE: {"queue": QUEUE_SCHEDULER},
    },
    task_annotations={
        TASK_AGENT_RUN: {"soft_time_limit": 3600, "time_limit": 3900},
        TASK_MARKET_TOOL: {"soft_time_limit": 180, "time_limit": 210},
        TASK_MARKET_DATA: {"soft_time_limit": 300, "time_limit": 330},
        TASK_OPPORTUNITY_SCAN: {"soft_time_limit": 900, "time_limit": 960},
        TASK_PORTFOLIO_QUANT_RUN: {
            "soft_time_limit": 900,
            "time_limit": 960,
        },
        TASK_LLM_TOOL: {"soft_time_limit": 150, "time_limit": 180},
        TASK_OCR: {"soft_time_limit": 120, "time_limit": 150},
        TASK_AVAILABILITY_PROBE: {"soft_time_limit": 120, "time_limit": 150},
    },
    beat_schedule={
        "dispatch-durable-agent-runs": {
            "task": TASK_DISPATCH_QUEUED,
            "schedule": 30.0,
        },
        "evaluate-agent-outcomes": {
            "task": TASK_OUTCOME_SCHEDULES,
            "schedule": 60.0,
        },
        "evaluate-shadow-outcomes": {
            "task": TASK_STRATEGY_SHADOW,
            "schedule": 60.0,
        },
        "run-decision-checks": {
            "task": TASK_DECISION_CHECKS,
            "schedule": 60.0,
        },
        "observe-opportunity-baskets": {
            "task": TASK_OPPORTUNITY_OBSERVATIONS,
            "schedule": max(
                900.0,
                float(
                    os.getenv(
                        "OPPORTUNITY_OBSERVATION_INTERVAL_SECONDS", "3600"
                    )
                ),
            ),
            "options": {"expires": 900},
        },
        "observe-capital-plan-outcomes": {
            "task": TASK_CAPITAL_OUTCOMES,
            "schedule": max(
                900.0,
                float(
                    os.getenv(
                        "CAPITAL_OUTCOME_INTERVAL_SECONDS", "3600"
                    )
                ),
            ),
            "options": {"expires": 900},
        },
        "scan-watchlist": {
            "task": TASK_WATCHLIST_SCAN,
            "schedule": 3600.0,
        },
        "cleanup-expired-private-objects": {
            "task": TASK_OBJECT_CLEANUP,
            "schedule": 3600.0,
        },
        "record-platform-availability": {
            "task": TASK_AVAILABILITY_PROBE,
            "schedule": max(
                60.0,
                float(os.getenv("AVAILABILITY_PROBE_INTERVAL_SECONDS", "300")),
            ),
            "options": {"expires": 240},
        },
    },
)


def redis_readiness() -> dict[str, Any]:
    if not uses_celery_queue():
        return {"ready": True, "mode": "embedded", "broker": None}
    if not BROKER_URL.startswith(("redis://", "rediss://")):
        return {
            "ready": False,
            "mode": "celery",
            "broker": _redact_url(BROKER_URL),
            "error": "REDIS_URL 必须使用 redis:// 或 rediss://",
        }
    try:
        client = Redis.from_url(
            BROKER_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        ready = bool(client.ping())
        depths = {
            queue: int(client.llen(queue))
            for queue in (QUEUE_AGENT, QUEUE_MARKET, QUEUE_LLM, QUEUE_OCR, QUEUE_SCHEDULER)
        }
        return {
            "ready": ready,
            "mode": "celery",
            "broker": _redact_url(BROKER_URL),
            "queue_depths": depths,
        }
    except Exception as error:
        return {
            "ready": False,
            "mode": "celery",
            "broker": _redact_url(BROKER_URL),
            "error": type(error).__name__,
        }


def _assert_queue_ready() -> None:
    readiness = redis_readiness()
    if not readiness.get("ready"):
        raise TaskQueueUnavailableError(
            f"Redis 任务队列不可用: {readiness.get('error') or 'ping failed'}"
        )


def enqueue_agent_run(run_id: str, repository=None) -> str:
    if not uses_celery_queue():
        raise TaskQueueConfigurationError("当前不是 Celery 任务模式")
    _assert_queue_ready()
    result = celery_app.send_task(
        TASK_AGENT_RUN,
        args=[str(run_id)],
        queue=QUEUE_AGENT,
        task_id=f"agent-{run_id}",
    )
    if repository is not None:
        repository.bind_celery_task(str(run_id), str(result.id))
    return str(result.id)


def enqueue_background_job(job: dict[str, Any], repository) -> str:
    if not uses_celery_queue():
        raise TaskQueueConfigurationError("后台作业只允许在 Celery 模式派发")
    queue = str(job.get("queue_name") or "")
    task_by_kind = {
        (QUEUE_MARKET, "tool_execution"): TASK_MARKET_TOOL,
        (QUEUE_MARKET, "market_data_operation"): TASK_MARKET_DATA,
        (QUEUE_MARKET, "opportunity_scan"): TASK_OPPORTUNITY_SCAN,
        (QUEUE_MARKET, "portfolio_quant_run"): TASK_PORTFOLIO_QUANT_RUN,
        (QUEUE_LLM, "tool_execution"): TASK_LLM_TOOL,
        (QUEUE_OCR, "ocr"): TASK_OCR,
    }
    task_name = task_by_kind.get((queue, str(job.get("job_type") or "")))
    if not task_name:
        raise TaskQueueConfigurationError(f"不允许的任务队列: {queue}")
    _assert_queue_ready()
    job_id = str(job["id"])
    result = celery_app.send_task(
        task_name,
        args=[job_id],
        queue=queue,
        task_id=f"job-{job_id}",
    )
    repository.mark_dispatched(job_id, str(result.id))
    return str(result.id)


def enqueue_scheduler_task(task_name: str) -> str:
    allowed = {
        TASK_DISPATCH_QUEUED,
        TASK_OUTCOME_SCHEDULES,
        TASK_STRATEGY_SHADOW,
        TASK_DECISION_CHECKS,
        TASK_OPPORTUNITY_OBSERVATIONS,
        TASK_CAPITAL_OUTCOMES,
        TASK_WATCHLIST_SCAN,
        TASK_OBJECT_CLEANUP,
        TASK_AVAILABILITY_PROBE,
    }
    if task_name not in allowed:
        raise TaskQueueConfigurationError("不允许的调度任务")
    _assert_queue_ready()
    if task_name == TASK_WATCHLIST_SCAN:
        queue = QUEUE_MARKET
    elif task_name == TASK_OBJECT_CLEANUP:
        queue = QUEUE_OCR
    else:
        queue = QUEUE_SCHEDULER
    result = celery_app.send_task(task_name, queue=queue)
    return str(result.id)
