# -*- coding: utf-8 -*-
"""Synchronous API compatibility gateway backed by durable market jobs."""

from __future__ import annotations

import os
import time
from typing import Any

from background_jobs import BackgroundJobRepository
from task_queue import (
    TaskQueueConfigurationError,
    TaskQueueUnavailableError,
    enqueue_background_job,
    uses_celery_queue,
)


class MarketDataGatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.error_code = str(error_code)
        self.job_id = job_id


def execute_market_operation(
    operation: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
    tenant_id: str = "platform",
    user_id: str = "interactive-api",
    max_attempts: int = 2,
) -> dict[str, Any]:
    if not uses_celery_queue():
        from market_data_operations import execute_operation

        return execute_operation(operation, payload)

    jobs = BackgroundJobRepository()
    job, _ = jobs.create_job(
        job_type="market_data_operation",
        queue_name="market-data",
        payload={"operation": str(operation), "input": dict(payload)},
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        max_attempts=max_attempts,
    )
    job_id = str(job["id"])
    try:
        enqueue_background_job(job, jobs)
    except (TaskQueueConfigurationError, TaskQueueUnavailableError) as error:
        raise MarketDataGatewayError(
            "market-data worker queue is unavailable",
            status_code=503,
            error_code="MARKET_QUEUE_UNAVAILABLE",
            job_id=job_id,
        ) from error

    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else os.getenv("MARKET_DATA_API_WAIT_SECONDS", "210")
    )
    deadline = time.monotonic() + max(1.0, min(600.0, timeout))
    poll_seconds = max(
        0.05, min(1.0, float(os.getenv("MARKET_DATA_POLL_SECONDS", "0.2")))
    )
    while time.monotonic() < deadline:
        current = jobs.get_job(job_id, include_payload=True)
        if current is None:
            raise MarketDataGatewayError(
                "durable market-data job disappeared",
                status_code=502,
                error_code="MARKET_JOB_MISSING",
                job_id=job_id,
            )
        status = str(current["status"])
        if status in {"succeeded", "partial"}:
            result = current.get("result")
            if not current.get("result_verified") or not isinstance(result, dict):
                raise MarketDataGatewayError(
                    "market-data result integrity check failed",
                    status_code=502,
                    error_code="MARKET_RESULT_INTEGRITY_FAILED",
                    job_id=job_id,
                )
            return result
        if status in {"failed", "cancelled"}:
            error_code = str(current.get("error_code") or "MARKET_JOB_FAILED")
            status_code = 400 if error_code == "MARKET_INPUT_INVALID" else 502
            if error_code.startswith("MARKET_CLIENT_"):
                try:
                    candidate = int(error_code.removeprefix("MARKET_CLIENT_"))
                    if candidate in {400, 404, 409}:
                        status_code = candidate
                except ValueError:
                    pass
            raise MarketDataGatewayError(
                str(current.get("error_message") or "market-data job failed"),
                status_code=status_code,
                error_code=error_code,
                job_id=job_id,
            )
        time.sleep(poll_seconds)

    raise MarketDataGatewayError(
        "market-data job exceeded the API wait limit and continues in background",
        status_code=504,
        error_code="MARKET_JOB_TIMEOUT",
        job_id=job_id,
    )
