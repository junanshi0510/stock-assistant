# -*- coding: utf-8 -*-
"""Public API for durable, evidence-first Agent runs."""

from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from agent.worker import registry, repository, start_worker


router = APIRouter(prefix="/api/v1/agent", tags=["投资 Agent"])


class CreateAgentRunRequest(BaseModel):
    intent: Literal["fund_deep_research"] = "fund_deep_research"
    code: str
    months: int = Field(default=36, ge=6, le=120)
    include_estimate: bool = False
    include_disclosure_changes: bool = False
    include_alternatives: bool = False
    alternative_limit: int = Field(default=5, ge=3, le=8)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = str(value or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("基金代码需要是 6 位数字")
        return code


def _get_run_or_404(run_id: str) -> dict:
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent Run 不存在")
    return run


@router.get("/tools")
def get_agent_tool_catalog():
    return {
        "items": [item for item in registry.catalog() if item["risk_level"] == "R0"],
        "policy": "当前公开 Agent 只注册 R0 公共只读工具。",
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


@router.get("/runs/{run_id}")
def get_agent_run(run_id: str):
    return _get_run_or_404(run_id)


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
