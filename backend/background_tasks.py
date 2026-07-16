# -*- coding: utf-8 -*-
"""Celery task entry points. All messages carry identifiers only."""

from __future__ import annotations

import os
import hashlib
import logging
import threading
from contextlib import contextmanager

from celery.exceptions import SoftTimeLimitExceeded

from background_jobs import BackgroundJobLeaseError, BackgroundJobRepository
from observability import configure_logging
from task_queue import (
    QUEUE_LLM,
    QUEUE_MARKET,
    TASK_AGENT_RUN,
    TASK_DECISION_CHECKS,
    TASK_DISPATCH_QUEUED,
    TASK_LLM_TOOL,
    TASK_MARKET_DATA,
    TASK_MARKET_TOOL,
    TASK_OBJECT_CLEANUP,
    TASK_OCR,
    TASK_OUTCOME_SCHEDULES,
    TASK_STRATEGY_SHADOW,
    TASK_WATCHLIST_SCAN,
    celery_app,
    enqueue_agent_run,
    enqueue_background_job,
)


configure_logging()
logger = logging.getLogger("background-tasks")


def _worker_id(task) -> str:
    return f"{task.request.hostname or 'worker'}:{task.request.id or 'unknown'}"


@contextmanager
def _job_heartbeat(repository, job_id: str, worker_id: str, lease_seconds: int):
    stop = threading.Event()

    def beat():
        while not stop.wait(max(15, lease_seconds // 3)):
            if not repository.heartbeat(
                job_id, worker_id, lease_seconds=lease_seconds
            ):
                return

    thread = threading.Thread(target=beat, name=f"job-heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def _execute_tool_job(task, job_id: str, expected_queue: str) -> dict:
    jobs = BackgroundJobRepository()
    worker_id = _worker_id(task)
    job = jobs.claim_job(
        str(job_id),
        worker_id,
        lease_seconds=int(os.getenv("TOOL_JOB_LEASE_SECONDS", "300")),
    )
    if job is None:
        return {"job_id": str(job_id), "status": "not_claimed"}
    if job["status"] in {"succeeded", "partial", "failed", "cancelled"}:
        return {"job_id": str(job_id), "status": job["status"]}
    if job.get("queue_name") != expected_queue or job.get("job_type") != "tool_execution":
        jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="TASK_ROUTE_MISMATCH",
            error_message="后台任务队列与工具类型不匹配",
            retryable=False,
        )
        return {"job_id": str(job_id), "status": "failed"}

    try:
        payload = job.get("payload") or {}
        from agent.queued_tools import tool_queue
        from agent.worker import registry

        tool_name = str(payload.get("tool_name") or "")
        tool_version = str(payload.get("tool_version") or "")
        if tool_queue(tool_name) != expected_queue:
            raise ValueError("工具不允许在当前 Worker 执行")
        definition = registry.get(tool_name, tool_version)
        lease_seconds = int(os.getenv("TOOL_JOB_LEASE_SECONDS", "300"))
        with _job_heartbeat(jobs, str(job_id), worker_id, lease_seconds):
            output = definition.handler(dict(payload.get("input") or {}))
        if not isinstance(output, dict):
            raise TypeError("工具必须返回结构化对象")
        declared = str(output.get("status") or "").lower()
        status = "partial" if declared in {"partial", "unavailable", "insufficient"} else "succeeded"
        jobs.complete_job(str(job_id), worker_id, output, status=status)
        return {"job_id": str(job_id), "status": status}
    except SoftTimeLimitExceeded:
        updated = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="TOOL_SOFT_TIMEOUT",
            error_message="后台工具达到执行时限",
            retryable=True,
            retry_delay_seconds=30,
        )
    except BackgroundJobLeaseError:
        return {"job_id": str(job_id), "status": "lease_lost"}
    except Exception as error:
        updated = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="TOOL_EXECUTION_FAILED",
            error_message=str(error),
            retryable=True,
            retry_delay_seconds=min(120, 15 * int(job.get("attempt_count") or 1)),
        )
    if updated["status"] == "queued":
        raise task.retry(countdown=30, max_retries=2)
    return {"job_id": str(job_id), "status": updated["status"]}


@celery_app.task(bind=True, name=TASK_MARKET_TOOL, ignore_result=True)
def execute_market_tool_job(self, job_id: str):
    return _execute_tool_job(self, job_id, QUEUE_MARKET)


@celery_app.task(bind=True, name=TASK_MARKET_DATA, ignore_result=True, max_retries=1)
def execute_market_data_job(self, job_id: str):
    jobs = BackgroundJobRepository()
    worker_id = _worker_id(self)
    lease_seconds = int(os.getenv("MARKET_DATA_JOB_LEASE_SECONDS", "360"))
    job = jobs.claim_job(str(job_id), worker_id, lease_seconds=lease_seconds)
    if job is None:
        return {"job_id": str(job_id), "status": "not_claimed"}
    if job["status"] in {"succeeded", "partial", "failed", "cancelled"}:
        return {"job_id": str(job_id), "status": job["status"]}
    if (
        job.get("queue_name") != QUEUE_MARKET
        or job.get("job_type") != "market_data_operation"
    ):
        jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="TASK_ROUTE_MISMATCH",
            error_message="market-data task route mismatch",
            retryable=False,
        )
        return {"job_id": str(job_id), "status": "failed"}

    try:
        from market_data_operations import execute_operation

        payload = job.get("payload") or {}
        operation = str(payload.get("operation") or "")
        operation_input = dict(payload.get("input") or {})
        if operation.startswith("portfolio.") and str(
            operation_input.get("user_id") or ""
        ) != str(job.get("user_id") or ""):
            raise ValueError("portfolio task user scope does not match job owner")
        with _job_heartbeat(jobs, str(job_id), worker_id, lease_seconds):
            result = execute_operation(
                operation,
                operation_input,
            )
        jobs.complete_job(str(job_id), worker_id, result)
        return {"job_id": str(job_id), "status": "succeeded"}
    except SoftTimeLimitExceeded:
        updated = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="MARKET_DATA_TIMEOUT",
            error_message="market-data operation reached its execution limit",
            retryable=True,
            retry_delay_seconds=15,
        )
    except BackgroundJobLeaseError:
        return {"job_id": str(job_id), "status": "lease_lost"}
    except (ValueError, PermissionError) as error:
        updated = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="MARKET_INPUT_INVALID",
            error_message=str(error),
            retryable=False,
        )
    except Exception as error:
        client_status = getattr(error, "http_status", None)
        updated = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code=(
                f"MARKET_CLIENT_{int(client_status)}"
                if client_status in {400, 404, 409}
                else "MARKET_DATA_FAILED"
            ),
            error_message=str(error),
            retryable=client_status not in {400, 404, 409},
            retry_delay_seconds=15,
        )
    if updated["status"] == "queued":
        raise self.retry(countdown=15)
    return {"job_id": str(job_id), "status": updated["status"]}


@celery_app.task(bind=True, name=TASK_LLM_TOOL, ignore_result=True)
def execute_llm_tool_job(self, job_id: str):
    return _execute_tool_job(self, job_id, QUEUE_LLM)


@celery_app.task(bind=True, name=TASK_OCR, ignore_result=True, max_retries=2)
def process_ocr_job(self, job_id: str):
    from holdings import recognize_image
    from object_assets import ObjectAssetRepository
    from object_storage import AliyunObjectStorage, ObjectStorageIntegrityError

    jobs = BackgroundJobRepository()
    worker_id = _worker_id(self)
    job = jobs.claim_job(
        str(job_id),
        worker_id,
        lease_seconds=int(os.getenv("OCR_JOB_LEASE_SECONDS", "180")),
    )
    if job is None:
        return {"job_id": str(job_id), "status": "not_claimed"}
    if job["status"] in {"succeeded", "partial", "failed", "cancelled"}:
        return {"job_id": str(job_id), "status": job["status"]}
    if job.get("job_type") != "ocr" or job.get("queue_name") != "ocr":
        jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="TASK_ROUTE_MISMATCH",
            error_message="OCR 任务路由不匹配",
            retryable=False,
        )
        return {"job_id": str(job_id), "status": "failed"}

    assets = ObjectAssetRepository()
    asset_id = str(job.get("object_asset_id") or (job.get("payload") or {}).get("asset_id") or "")
    asset = assets.get(asset_id)
    if (
        not asset
        or asset.get("status") != "available"
        or asset.get("user_id") != job.get("user_id")
    ):
        jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="OCR_OBJECT_UNAVAILABLE",
            error_message="OCR 私有对象不存在、状态无效或用户不匹配",
            retryable=False,
        )
        return {"job_id": str(job_id), "status": "failed"}

    retryable = True
    try:
        storage = AliyunObjectStorage()
        if storage.bucket != asset["bucket"]:
            raise ObjectStorageIntegrityError("OCR 对象 Bucket 与当前配置不一致")
        image_bytes = storage.get_bytes(
            str(asset["object_key"]), max_bytes=8 * 1024 * 1024
        )
        actual_sha256 = hashlib.sha256(image_bytes).hexdigest()
        if actual_sha256 != asset["sha256"]:
            assets.mark_quarantined(asset_id, "sha256_mismatch")
            retryable = False
            raise ObjectStorageIntegrityError("OCR 对象 SHA-256 校验失败")
        result = recognize_image(image_bytes, str(asset["content_type"]))
        result["source_asset_id"] = asset_id
        result["source_sha256"] = asset["sha256"]
        jobs.complete_job(str(job_id), worker_id, result)
        try:
            storage.delete(str(asset["object_key"]))
            assets.mark_deleted(asset_id)
        except Exception:
            logger.exception("OCR 成功后的临时对象删除失败:%s", asset_id)
        return {"job_id": str(job_id), "status": "succeeded"}
    except BackgroundJobLeaseError:
        return {"job_id": str(job_id), "status": "lease_lost"}
    except Exception as error:
        updated = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="OCR_EXECUTION_FAILED",
            error_message=str(error),
            retryable=retryable,
            retry_delay_seconds=30,
        )
        if updated["status"] == "queued":
            raise self.retry(countdown=30)
        return {"job_id": str(job_id), "status": updated["status"]}


@celery_app.task(name=TASK_OBJECT_CLEANUP, ignore_result=True)
def cleanup_expired_objects():
    from object_assets import ObjectAssetRepository
    from object_storage import AliyunObjectStorage

    assets = ObjectAssetRepository()
    storage = AliyunObjectStorage()
    deleted = 0
    failed = 0
    for asset in assets.list_expired(limit=100):
        try:
            if asset["bucket"] != storage.bucket:
                raise RuntimeError("对象 Bucket 与当前配置不一致")
            storage.delete(str(asset["object_key"]))
            assets.mark_deleted(str(asset["id"]))
            deleted += 1
        except Exception:
            failed += 1
            logger.exception("过期私有对象删除失败:%s", asset.get("id"))
    return {"deleted": deleted, "failed": failed}


@celery_app.task(bind=True, name=TASK_AGENT_RUN, ignore_result=True, max_retries=3)
def execute_agent_run(self, run_id: str):
    from agent.worker import _ensure_terminal_observations, repository, runner

    existing = repository.get_run(str(run_id), include_details=False)
    if existing is None:
        return {"run_id": str(run_id), "status": "not_found"}
    if existing["status"] in {"completed", "partial", "failed", "cancelled", "abstained"}:
        return {"run_id": str(run_id), "status": existing["status"]}
    worker_id = _worker_id(self)
    claimed = repository.claim_run(
        str(run_id),
        worker_id,
        lease_seconds=int(os.getenv("AGENT_RUN_LEASE_SECONDS", "7200")),
    )
    if claimed is None:
        return {"run_id": str(run_id), "status": "not_claimed"}

    stop = threading.Event()

    def heartbeat():
        while not stop.wait(60):
            if not repository.renew_run_lease(
                str(run_id),
                worker_id,
                lease_seconds=int(os.getenv("AGENT_RUN_LEASE_SECONDS", "7200")),
            ):
                return

    thread = threading.Thread(target=heartbeat, name=f"agent-heartbeat-{run_id}", daemon=True)
    thread.start()
    try:
        finished = runner.execute(claimed)
        _ensure_terminal_observations(finished)
        return {"run_id": str(run_id), "status": finished["status"]}
    except Exception as error:
        raise self.retry(
            exc=RuntimeError("agent orchestration infrastructure failure"),
            countdown=30,
        ) from error
    finally:
        stop.set()
        thread.join(timeout=2)


@celery_app.task(name=TASK_DISPATCH_QUEUED, ignore_result=True)
def dispatch_queued_runs():
    from agent.worker import repository

    agent_recovered = repository.recover_expired_run_leases(limit=100)
    agent_dispatched = 0
    for run_id in repository.list_queued_run_ids(limit=100):
        enqueue_agent_run(run_id, repository)
        agent_dispatched += 1
    jobs = BackgroundJobRepository()
    jobs_recovered = jobs.recover_expired_jobs(limit=100)
    job_dispatched = 0
    for job in jobs.list_dispatchable_jobs(limit=100):
        enqueue_background_job(job, jobs)
        job_dispatched += 1
    return {
        "agent_runs_dispatched": agent_dispatched,
        "agent_runs_recovered": agent_recovered,
        "background_jobs_dispatched": job_dispatched,
        "background_jobs_recovered": jobs_recovered,
    }


@celery_app.task(name=TASK_OUTCOME_SCHEDULES, ignore_result=True)
def process_outcome_schedules():
    from agent.worker import outcome_worker

    handled = 0
    while handled < 10 and outcome_worker.run_once():
        handled += 1
    return {"handled": handled}


@celery_app.task(name=TASK_STRATEGY_SHADOW, ignore_result=True)
def process_strategy_shadow():
    from agent.worker import strategy_shadow_worker

    handled = 0
    while handled < 10 and strategy_shadow_worker.run_once():
        handled += 1
    return {"handled": handled}


@celery_app.task(name=TASK_DECISION_CHECKS, ignore_result=True)
def process_decision_checks():
    from decision_check_worker import worker

    handled = 0
    while handled < 10 and worker.run_once():
        handled += 1
    return {"handled": handled}


@celery_app.task(name=TASK_WATCHLIST_SCAN, ignore_result=True)
def scan_watchlist():
    import monitor

    monitor.trigger_scan_now()
    return {"status": "completed"}
