# -*- coding: utf-8 -*-
"""Public API for durable, evidence-first Agent runs."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from agent.comparison import compare_run_results
from agent.outcomes import DecisionOutcomeService, OutcomeEvaluationError
from agent.repository import RUN_TERMINAL_STATUSES
from agent.worker import registry, repository, start_worker


router = APIRouter(prefix="/api/v1/agent", tags=["投资 Agent"])


class CreateAgentRunRequest(BaseModel):
    intent: Literal["fund_deep_research"] = "fund_deep_research"
    code: str
    months: int = Field(default=60, ge=6, le=120)
    include_estimate: bool = False
    include_disclosure_changes: bool = False
    include_alternatives: bool = False
    include_portfolio_context: bool = True
    planned_amount: float | None = Field(default=None, ge=0)
    alternative_limit: int = Field(default=5, ge=3, le=8)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = str(value or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("基金代码需要是 6 位数字")
        return code


class OutcomeScheduleRequest(BaseModel):
    enabled: bool
    interval_hours: int = Field(default=24, ge=12, le=168)
    run_immediately: bool = False


def _get_run_or_404(run_id: str) -> dict:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent Run 不存在")
    return run


def _encode_cursor(run: dict) -> str:
    payload = json.dumps(
        [str(run["created_at"]), str(run["id"])],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as error:
        raise HTTPException(status_code=400, detail="Agent 历史分页游标无效") from error
    if (
        not isinstance(payload, list)
        or len(payload) != 2
        or not all(isinstance(item, str) and item for item in payload)
    ):
        raise HTTPException(status_code=400, detail="Agent 历史分页游标无效")
    return payload[0], payload[1]


def _history_item(run: dict) -> dict:
    result = run.get("result") or {}
    fund = result.get("fund") or {}
    conclusion = result.get("conclusion") or {}
    return {
        "id": run["id"],
        "intent": run["intent"],
        "status": run["status"],
        "input": run.get("input") or {},
        "summary": {
            "code": fund.get("code") or (run.get("input") or {}).get("code"),
            "name": fund.get("name"),
            "as_of": fund.get("as_of"),
            "headline": conclusion.get("headline"),
        },
        "error_code": run.get("error_code"),
        "error_message": run.get("error_message"),
        "parent_run_id": run.get("parent_run_id"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
    }


def _schedule_view(schedule: dict | None) -> dict | None:
    if schedule is None:
        return None
    return {
        key: schedule.get(key)
        for key in (
            "id",
            "run_id",
            "status",
            "interval_hours",
            "next_run_at",
            "attempt_count",
            "consecutive_failures",
            "last_started_at",
            "last_finished_at",
            "last_success_at",
            "last_provider_as_of",
            "last_evidence_id",
            "last_error_code",
            "last_error_message",
            "created_at",
            "updated_at",
        )
    }


def _outcome_service() -> DecisionOutcomeService:
    return DecisionOutcomeService(repository, registry)


@router.get("/tools")
def get_agent_tool_catalog():
    return {
        "items": [
            item for item in registry.catalog()
            if item["risk_level"] in {"R0", "R1"}
        ],
        "policy": "R0 只读取公共市场数据；R1 只读取用户已确认组合或执行确定性个人风险门禁，均不下单。",
    }


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_agent_run(
    request: CreateAgentRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 个字符")
    if idempotency_key:
        existing = repository.get_run_by_idempotency_key("anonymous", idempotency_key)
        if existing is not None:
            return {"created": False, "run": existing}
    max_pending = max(1, int(os.getenv("AGENT_MAX_PENDING_RUNS", "20")))
    if repository.count_active_runs() >= max_pending:
        raise HTTPException(
            status_code=429,
            detail="Agent 任务队列已满，请等待现有真实数据任务完成后再试",
        )
    run, created = repository.create_run(
        request.intent,
        request.model_dump(),
        tenant_id="public",
        user_id="anonymous",
        idempotency_key=idempotency_key,
    )
    start_worker()
    return {"created": created, "run": repository.get_run(run["id"])}


@router.get("/runs")
def list_agent_runs(
    limit: int = Query(default=8, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=8, max_length=500),
    run_status: Literal[
        "queued", "running", "completed", "partial", "failed", "cancelled", "abstained"
    ] | None = Query(default=None, alias="status"),
    code: str | None = Query(default=None, pattern=r"^\d{6}$"),
):
    before = _decode_cursor(cursor) if cursor else None
    runs, has_more = repository.list_runs(
        tenant_id="public",
        user_id="anonymous",
        limit=limit,
        before=before,
        status=run_status,
        code=code,
    )
    items = [_history_item(run) for run in runs]
    return {
        "items": items,
        "next_cursor": _encode_cursor(runs[-1]) if has_more and runs else None,
        "has_more": has_more,
    }


@router.get("/runs/{run_id}")
def get_agent_run(run_id: str):
    return _get_run_or_404(run_id)


@router.get("/runs/{run_id}/evaluations")
def list_agent_run_evaluations(run_id: str):
    _get_run_or_404(run_id)
    items = repository.list_evidence_by_type(run_id, "outcome_observation")
    return {
        "items": [
            {
                **(item.get("payload") or {}),
                "evidence_id": item["id"],
                "payload_sha256": item["payload_sha256"],
                "integrity_verified": item.get("integrity_verified"),
                "created_at": item["created_at"],
            }
            for item in items
        ],
        "count": len(items),
        "policy": "每个真实净值截止日只保存一份不可变评估；新净值产生新 Evidence，不覆盖旧评估。",
    }


@router.get("/runs/{run_id}/outcome-schedule")
def get_agent_run_outcome_schedule(run_id: str):
    run = _get_run_or_404(run_id)
    service = _outcome_service()
    return {
        "eligibility": service.eligibility(run),
        "schedule": _schedule_view(repository.get_outcome_schedule(run_id)),
        "policy": (
            "只有包含方向性动作且拥有不可变确认净值基线的终态基金 Run 才能自动观察；"
            "计划保存在数据库中，由带租约的 Worker 执行，进程重启不会丢失。"
        ),
    }


@router.put("/runs/{run_id}/outcome-schedule")
def configure_agent_run_outcome_schedule(run_id: str, request: OutcomeScheduleRequest):
    run = _get_run_or_404(run_id)
    service = _outcome_service()
    eligibility = service.eligibility(run)
    existing = repository.get_outcome_schedule(run_id)
    if request.enabled and not eligibility["eligible"]:
        raise HTTPException(
            status_code=409,
            detail=f"当前 Run 不可启用自动结果观察:{eligibility['reason']}",
        )
    if not request.enabled and existing is None:
        return {
            "changed": False,
            "eligibility": eligibility,
            "schedule": None,
        }
    schedule, changed = repository.configure_outcome_schedule(
        run_id,
        enabled=request.enabled,
        interval_hours=request.interval_hours,
        run_immediately=request.run_immediately,
        actor_id="anonymous",
    )
    if request.enabled:
        start_worker()
    return {
        "changed": changed,
        "eligibility": eligibility,
        "schedule": _schedule_view(schedule),
    }


@router.post("/runs/{run_id}/evaluate")
def evaluate_agent_run(run_id: str):
    try:
        return _outcome_service().evaluate_run(
            run_id,
            actor_type="user",
            actor_id="anonymous",
        )
    except OutcomeEvaluationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error


@router.get("/runs/{run_id}/comparison")
def get_agent_run_comparison(run_id: str):
    current = _get_run_or_404(run_id)
    parent_run_id = current.get("parent_run_id")
    if not parent_run_id:
        raise HTTPException(status_code=409, detail="只有重跑任务才能与来源 Run 对比")
    if current["status"] not in RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="当前重跑任务尚未完成，暂时不能比较")

    parent = repository.get_run(parent_run_id)
    if (
        parent is None
        or parent.get("tenant_id") != current.get("tenant_id")
        or parent.get("user_id") != current.get("user_id")
    ):
        raise HTTPException(status_code=404, detail="来源 Agent Run 不存在")
    if parent["status"] not in RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="来源 Agent Run 尚未完成，暂时不能比较")
    if not current.get("result") or not parent.get("result"):
        raise HTTPException(status_code=409, detail="父子任务均需形成研究结果后才能比较")

    current_integrity = repository.verify_run_evidence_integrity(current["id"])
    parent_integrity = repository.verify_run_evidence_integrity(parent["id"])
    if not current_integrity["verified"] or not parent_integrity["verified"]:
        raise HTTPException(status_code=409, detail="父子 Run 的 Evidence 完整性校验未通过，已拒绝比较")
    try:
        comparison = compare_run_results(current, parent)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    comparison["integrity"] = {
        "current": current_integrity,
        "parent": parent_integrity,
    }
    return comparison


@router.post("/runs/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
def rerun_agent_run(
    run_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    source = _get_run_or_404(run_id)
    if source["status"] not in RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="运行中的 Agent Run 不能重复启动")
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 个字符")
    if idempotency_key:
        existing = repository.get_run_by_idempotency_key(source["user_id"], idempotency_key)
        if existing is not None:
            return {"created": False, "run": existing}
    run, created = repository.create_run(
        source["intent"],
        source.get("input") or {},
        tenant_id=source["tenant_id"],
        user_id=source["user_id"],
        idempotency_key=idempotency_key,
        parent_run_id=source["id"],
    )
    start_worker()
    return {"created": created, "run": repository.get_run(run["id"])}


@router.post("/runs/{run_id}/cancel")
def cancel_agent_run(run_id: str):
    _get_run_or_404(run_id)
    return repository.request_cancel(run_id, actor_id="anonymous")


@router.get("/runs/{run_id}/evidence/{evidence_id}")
def get_agent_evidence(run_id: str, evidence_id: str):
    _get_run_or_404(run_id)
    evidence = repository.get_evidence(run_id, evidence_id, include_payload=True)
    if evidence is None:
        raise HTTPException(status_code=404, detail="该 Agent Run 中不存在此 Evidence")
    return evidence


@router.get("/runs/{run_id}/audit")
def get_agent_audit(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
):
    _get_run_or_404(run_id)
    items = repository.list_audit_events(run_id)
    verification = repository.verify_audit_chain(run_id)
    return {
        "items": items[-limit:],
        "count": len(items),
        "verification": verification,
    }
