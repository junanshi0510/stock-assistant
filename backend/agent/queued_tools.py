# -*- coding: utf-8 -*-
"""Route provider and LLM tools through durable queue jobs."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from background_jobs import BackgroundJobRepository
from task_queue import QUEUE_LLM, QUEUE_MARKET, enqueue_background_job


class QueuedToolError(RuntimeError):
    pass


class QueuedToolTimeout(TimeoutError):
    pass


def tool_queue(tool_name: str) -> str | None:
    name = str(tool_name)
    if name.startswith("llm."):
        return QUEUE_LLM
    if name.startswith("fund.") and name != "fund.personalized_decision.evaluate":
        return QUEUE_MARKET
    if name == "portfolio.exposure.snapshot":
        return QUEUE_MARKET
    return None


def execute_queued_tool(
    *,
    run_id: str,
    tool_name: str,
    tool_version: str,
    input_payload: dict[str, Any],
    timeout_seconds: float,
    agent_repository,
    cancel_check: Callable[[], bool],
) -> dict[str, Any]:
    queue_name = tool_queue(tool_name)
    if queue_name is None:
        raise QueuedToolError(f"工具未配置独立 Worker 队列: {tool_name}")
    run = agent_repository.get_run(run_id, include_details=False)
    if run is None:
        raise QueuedToolError("Agent Run 不存在")
    canonical = json.dumps(
        input_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    jobs = BackgroundJobRepository(agent_repository.db_path)
    job, created = jobs.create_job(
        job_type="tool_execution",
        queue_name=queue_name,
        payload={
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "input": input_payload,
        },
        tenant_id=str(run.get("tenant_id") or "public"),
        user_id=str(run.get("user_id") or "anonymous"),
        idempotency_key=f"{run_id}:{tool_name}:{tool_version}:{input_hash}",
        max_attempts=3,
    )
    if created or (job["status"] == "queued" and not job.get("celery_task_id")):
        enqueue_background_job(job, jobs)

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while True:
        if cancel_check():
            jobs.request_cancel(str(job["id"]), str(run.get("user_id") or "anonymous"))
            raise QueuedToolError("Agent Run 已请求取消")
        current = jobs.get_job(str(job["id"]), include_payload=True)
        if current is None or not current.get("payload_verified"):
            raise QueuedToolError("后台工具任务输入完整性校验失败")
        if current["status"] in {"succeeded", "partial"}:
            if not current.get("result_verified") or not isinstance(current.get("result"), dict):
                raise QueuedToolError("后台工具任务输出完整性校验失败")
            return current["result"]
        if current["status"] in {"failed", "cancelled"}:
            raise QueuedToolError(
                str(current.get("error_message") or current.get("error_code") or "后台工具任务失败")
            )
        if time.monotonic() >= deadline:
            jobs.request_cancel(str(job["id"]), str(run.get("user_id") or "anonymous"))
            raise QueuedToolTimeout(
                f"后台工具超过 {timeout_seconds} 秒执行时限: {tool_name}@{tool_version}"
            )
        time.sleep(0.25)

