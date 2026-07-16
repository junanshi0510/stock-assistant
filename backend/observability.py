# -*- coding: utf-8 -*-
"""Structured logging, correlation context and Prometheus metrics."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from prometheus_client import Counter, Gauge, Histogram


request_id_var = contextvars.ContextVar("request_id", default=None)
task_id_var = contextvars.ContextVar("task_id", default=None)
run_id_var = contextvars.ContextVar("run_id", default=None)
job_id_var = contextvars.ContextVar("job_id", default=None)

HTTP_REQUESTS = Counter(
    "stock_assistant_http_requests_total",
    "HTTP requests handled by the API",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "stock_assistant_http_request_duration_seconds",
    "HTTP request latency",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
HTTP_IN_PROGRESS = Gauge(
    "stock_assistant_http_requests_in_progress",
    "HTTP requests currently in progress",
    ("method",),
)
QUEUE_DEPTH = Gauge(
    "stock_assistant_queue_depth",
    "Redis queue depth",
    ("queue",),
)
BACKGROUND_TASKS = Counter(
    "stock_assistant_background_tasks_total",
    "Celery task terminal outcomes",
    ("task", "status"),
)


_SECRET_PATTERN = re.compile(
    r"(?i)(access[_-]?key|secret|token|password|authorization|api[_-]?key)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)([a-z][a-z0-9+.-]*://(?:[^:/@\s]+)?:)[^@\s]+@"
)


def sanitize_log_value(value: Any, *, limit: int = 2000) -> str:
    message = str(value or "")
    message = _SECRET_PATTERN.sub(r"\1=***", message)
    message = _URL_CREDENTIAL_PATTERN.sub(r"\1***@", message)
    return message[:limit]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_log_value(record.getMessage()),
            "service": os.getenv("SERVICE_NAME", "stock-assistant-api"),
        }
        for key, variable in (
            ("request_id", request_id_var),
            ("task_id", task_id_var),
            ("run_id", run_id_var),
            ("job_id", job_id_var),
        ):
            value = variable.get()
            if value:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": sanitize_log_value(record.exc_info[1]),
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, str(os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO)
    json_enabled = str(os.getenv("LOG_FORMAT") or "").lower() == "json"
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    formatter: logging.Formatter = (
        JsonFormatter()
        if json_enabled
        else logging.Formatter("%(asctime)s [%(name)s] %(message)s")
    )
    for handler in root.handlers:
        handler.setFormatter(formatter)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.setFormatter(formatter)
    _configured = True


async def observe_http_request(request, call_next):
    supplied = str(request.headers.get("x-request-id") or "").strip()
    request_id = supplied[:80] if re.fullmatch(r"[A-Za-z0-9_.:-]{8,80}", supplied) else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    method = request.method.upper()
    HTTP_IN_PROGRESS.labels(method=method).inc()
    started = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = int(response.status_code)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", None) or request.url.path
        HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
        HTTP_DURATION.labels(method=method, route=route).observe(time.monotonic() - started)
        HTTP_IN_PROGRESS.labels(method=method).dec()
        request_id_var.reset(token)


try:
    from celery.signals import (
        after_setup_logger,
        after_setup_task_logger,
        task_postrun,
        task_prerun,
    )

    def _configure_celery_handler(logger):
        formatter: logging.Formatter = (
            JsonFormatter()
            if str(os.getenv("LOG_FORMAT") or "").lower() == "json"
            else logging.Formatter("%(asctime)s [%(name)s] %(message)s")
        )
        for handler in logger.handlers:
            handler.setFormatter(formatter)

    @after_setup_logger.connect
    def _after_setup_logger(logger=None, **_kwargs):
        if logger:
            _configure_celery_handler(logger)

    @after_setup_task_logger.connect
    def _after_setup_task_logger(logger=None, **_kwargs):
        if logger:
            _configure_celery_handler(logger)

    @task_prerun.connect
    def _task_context_start(task_id=None, task=None, args=None, **_kwargs):
        task_id_var.set(str(task_id or ""))
        name = str(getattr(task, "name", "") or "")
        values = list(args or [])
        if name.endswith("execute_run") and values:
            run_id_var.set(str(values[0]))
        elif values:
            job_id_var.set(str(values[0]))

    @task_postrun.connect
    def _task_context_end(task_id=None, task=None, state=None, **_kwargs):
        BACKGROUND_TASKS.labels(
            task=str(getattr(task, "name", "unknown")), status=str(state or "UNKNOWN")
        ).inc()
        task_id_var.set(None)
        run_id_var.set(None)
        job_id_var.set(None)
except ImportError:
    pass
