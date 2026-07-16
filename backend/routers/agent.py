# -*- coding: utf-8 -*-
"""Public API for durable, evidence-first Agent runs."""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
import json
import os
import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator

import storage
from auth import AuthPrincipal, principal_from_request
from agent import (
    batch_allocations,
    batch_purchase_attribution,
    batch_purchase_execution,
    batch_purchase_preflight,
)
from agent.batches import summarize_batch
from agent.comparison import compare_run_results
from agent.outcomes import DecisionOutcomeService, OutcomeEvaluationError
from agent.repository import AgentQueueCapacityError, RUN_TERMINAL_STATUSES
from agent.worker import (
    registry,
    repository,
    start_worker,
    strategy_governance,
    strategy_shadow_service,
    synthesis_service,
)
from task_queue import (
    TASK_OUTCOME_SCHEDULES,
    TaskQueueUnavailableError,
    enqueue_agent_run,
    enqueue_scheduler_task,
    uses_celery_queue,
)


router = APIRouter(prefix="/api/v1/agent", tags=["投资 Agent"])


class CreateAgentRunRequest(BaseModel):
    intent: Literal["fund_deep_research"] = "fund_deep_research"
    code: str
    months: int = Field(default=60, ge=6, le=120)
    include_estimate: bool = False
    include_disclosure_changes: bool = True
    include_alternatives: bool = True
    include_market_intelligence: bool = True
    include_ai_synthesis: bool = True
    include_portfolio_context: bool = True
    question: str = Field(
        default="结合未来 3-12 个月的市场、底层持仓、新闻和我的组合约束，我现在应该如何管理这只基金？",
        min_length=8,
        max_length=500,
    )
    planned_amount: float | None = Field(default=None, ge=0)
    alternative_limit: int = Field(default=5, ge=3, le=8)
    intelligence_holding_limit: int = Field(default=4, ge=2, le=6)
    news_per_holding: int = Field(default=3, ge=1, le=5)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = str(value or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("基金代码需要是 6 位数字")
        return code

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        question = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(question) < 8:
            raise ValueError("研究目标至少需要 8 个字符")
        return question


class CreateAgentBatchRequest(BaseModel):
    intent: Literal["fund_deep_research"] = "fund_deep_research"
    codes: list[str] = Field(min_length=2, max_length=8)
    months: int = Field(default=60, ge=6, le=120)
    include_estimate: bool = False
    include_disclosure_changes: bool = True
    include_alternatives: bool = True
    include_market_intelligence: bool = True
    include_ai_synthesis: bool = True
    include_portfolio_context: bool = True
    planned_amount: float | None = Field(default=None, gt=0, le=100_000_000)
    acknowledged_available_cash: bool = False
    question: str = Field(
        default="比较这些基金未来 3-12 个月的市场证据、底层持仓、风险和组合重合度，我应该如何逐只管理？",
        min_length=8,
        max_length=500,
    )
    alternative_limit: int = Field(default=5, ge=3, le=8)
    intelligence_holding_limit: int = Field(default=4, ge=2, le=6)
    news_per_holding: int = Field(default=3, ge=1, le=5)

    @field_validator("codes")
    @classmethod
    def validate_codes(cls, values: list[str]) -> list[str]:
        codes = [str(value or "").strip() for value in values]
        invalid = [code for code in codes if not re.fullmatch(r"\d{6}", code)]
        if invalid:
            raise ValueError(f"基金代码需要是 6 位数字:{','.join(invalid[:3])}")
        if len(codes) != len(set(codes)):
            raise ValueError("批量研究基金代码不能重复")
        return codes

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        question = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(question) < 8:
            raise ValueError("研究目标至少需要 8 个字符")
        return question

    @model_validator(mode="after")
    def validate_batch_budget(self):
        if self.planned_amount is not None and not self.acknowledged_available_cash:
            raise ValueError("填写批次总预算时，必须确认资金尚未投入且不占用应急资金")
        if self.acknowledged_available_cash and self.planned_amount is None:
            raise ValueError("确认可用资金前必须填写本批次唯一总预算")
        if self.planned_amount is not None and not self.include_portfolio_context:
            raise ValueError("批次资金分配必须启用真实持仓与投资政策")
        if self.planned_amount is not None and not self.include_market_intelligence:
            raise ValueError("批次资金分配必须启用真实披露持仓情报以核验基金重合度")
        return self


class CreateBatchAllocationRequest(BaseModel):
    expected_batch_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BatchPurchaseQuoteRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    platform_name: str = Field(min_length=2, max_length=80)
    quoted_at: datetime
    currency: Literal["CNY"] = "CNY"
    order_amount_yuan: float = Field(gt=0, le=100_000_000)
    entry_fee_yuan: float | None = Field(default=None, ge=0, le=10_000_000)
    purchase_status: Literal["available", "limited", "unavailable", "unknown"]
    purchase_limit_yuan: float | None = Field(default=None, gt=0, le=100_000_000)
    expected_confirmation_date: date | None = None

    @field_validator("platform_name")
    @classmethod
    def normalize_platform_name(cls, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @field_validator("quoted_at")
    @classmethod
    def require_quote_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("平台报价时间必须包含时区")
        return value


class CreateBatchPurchasePreflightRequest(BaseModel):
    expected_allocation_event_id: str = Field(min_length=8, max_length=120)
    expected_allocation_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_previous_event_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    acknowledged_platform_quotes: bool
    quotes: list[BatchPurchaseQuoteRequest] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_quote_set(self):
        codes = [quote.code for quote in self.quotes]
        if len(codes) != len(set(codes)):
            raise ValueError("同一基金只能提交一条本次平台报价")
        if not self.acknowledged_platform_quotes:
            raise ValueError("必须确认全部信息来自销售平台本次申购页")
        return self


class BatchPurchaseExecutionOutcomeRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    resolution: Literal["purchased", "not_purchased"]
    transaction_id: int | None = Field(default=None, gt=0)
    purchase_submitted_at: datetime | None = None
    acknowledged_order_variance: bool = False
    not_purchased_reason: Literal[
        "platform_unavailable",
        "limit_insufficient",
        "insufficient_cash",
        "risk_reassessment",
        "user_cancelled",
        "other",
    ] | None = None
    not_purchased_detail: str = Field(default="", max_length=200)

    @field_validator("purchase_submitted_at")
    @classmethod
    def require_submission_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("申购提交时间必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_resolution_fields(self):
        if self.resolution == "purchased":
            if self.transaction_id is None or self.purchase_submitted_at is None:
                raise ValueError("已申购结果必须绑定真实买入流水和包含时区的提交时间")
            if self.not_purchased_reason is not None:
                raise ValueError("已申购结果不能填写未申购原因")
        else:
            if self.not_purchased_reason is None:
                raise ValueError("未申购结果必须填写原因")
            if self.transaction_id is not None or self.purchase_submitted_at is not None:
                raise ValueError("未申购结果不能绑定交易流水或申购提交时间")
        return self


class CreateBatchPurchaseExecutionRequest(BaseModel):
    expected_preflight_event_id: str = Field(min_length=8, max_length=120)
    expected_preflight_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_previous_event_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    outcomes: list[BatchPurchaseExecutionOutcomeRequest] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_outcome_set(self):
        codes = [item.code for item in self.outcomes]
        if len(codes) != len(set(codes)):
            raise ValueError("同一只基金只能提交一条成交结果")
        transaction_ids = [
            item.transaction_id for item in self.outcomes if item.transaction_id is not None
        ]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError("同一笔交易流水不能绑定多只基金")
        return self


class CreateBatchPurchaseReconciliationRequest(BaseModel):
    expected_purchase_event_id: str = Field(min_length=8, max_length=120)
    expected_purchase_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_previous_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CreateBatchPurchaseAttributionRequest(BaseModel):
    expected_reconciliation_event_id: str = Field(min_length=8, max_length=120)
    expected_reconciliation_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_previous_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class OutcomeScheduleRequest(BaseModel):
    enabled: bool
    interval_hours: int = Field(default=24, ge=12, le=168)
    run_immediately: bool = False


def _agent_user_id(principal: object) -> str:
    if isinstance(principal, AuthPrincipal) and not principal.auth_disabled:
        return principal.subject_id
    return "anonymous"


def _portfolio_user_id(principal: object) -> str:
    if isinstance(principal, AuthPrincipal) and not principal.auth_disabled:
        return principal.subject_id
    return "default"


def _actor_id(principal: object) -> str:
    if isinstance(principal, AuthPrincipal) and not principal.auth_disabled:
        return principal.user_id
    return "anonymous"


def _dispatch_run(run_id: str) -> None:
    if not uses_celery_queue():
        start_worker()
        return
    try:
        enqueue_agent_run(run_id, repository)
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "task_queue_unavailable",
                "message": str(error),
                "run_id": run_id,
                "durable_status": "queued",
            },
        ) from error


def _dispatch_batch(batch: dict) -> None:
    if not uses_celery_queue():
        start_worker()
        return
    for item in batch.get("items") or []:
        run_id = str((item.get("run") or {}).get("id") or "")
        if run_id:
            _dispatch_run(run_id)


def _can_access(owner_user_id: str, principal: object) -> bool:
    if not isinstance(principal, AuthPrincipal):
        return owner_user_id == "anonymous"
    return principal.is_admin or owner_user_id == _agent_user_id(principal)


def _get_run_or_404(run_id: str, principal: object) -> dict:
    run = repository.get_run(run_id)
    if run is None or not _can_access(str(run.get("user_id") or ""), principal):
        raise HTTPException(status_code=404, detail="Agent Run 不存在")
    return run


def _get_batch_or_404(batch_id: str, principal: object) -> dict:
    batch = repository.get_batch(batch_id)
    if batch is None or not _can_access(str(batch.get("user_id") or ""), principal):
        raise HTTPException(status_code=404, detail="Agent Batch 不存在")
    return batch


def _summarize_batch_public(
    batch: dict,
    *,
    include_execution_details: bool = True,
) -> dict:
    summary = summarize_batch(batch)
    summary["purchase_preflight"] = (
        batch_purchase_preflight.decorate_batch_purchase_preflight(
            repository,
            batch,
            user_id=str(batch.get("user_id") or "anonymous"),
        )
    )
    summary["purchase_execution"] = (
        batch_purchase_execution.decorate_batch_purchase_execution(
            repository,
            batch,
            user_id=str(batch.get("user_id") or "anonymous"),
        )
        if include_execution_details else None
    )
    summary["purchase_attribution"] = (
        batch_purchase_attribution.decorate_batch_purchase_attribution(
            repository,
            batch,
            user_id=str(batch.get("user_id") or "anonymous"),
        )
        if include_execution_details else None
    )
    return summary


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
        "policy": "R0 只读取公共市场数据；R1 读取用户确认的聚合组合上下文或调用已批准模型，均不下单。",
    }


@router.get("/model/status")
def get_agent_model_status():
    return {
        "model": synthesis_service.public_status(),
        "policy": (
            "未配置模型时不会返回模板化 AI 结论；私有持仓只有在用户勾选且服务器明确允许时，"
            "才会以聚合摘要进入模型上下文。"
        ),
    }


@router.get("/strategies")
def list_agent_strategies():
    items = strategy_governance.list_public()
    return {
        "items": items,
        "count": len(items),
        "policy": (
            "策略状态和发布检查只读公开；状态写入仅允许服务器 SSH 运维命令。"
            "只有 active 或命中 canary 且所有发布检查通过的精确版本才能影响个人决策。"
        ),
    }


@router.get("/strategies/{strategy_id}/{strategy_version}")
def get_agent_strategy(strategy_id: str, strategy_version: str):
    item = strategy_governance.get_public(strategy_id, strategy_version)
    if item is None:
        raise HTTPException(status_code=404, detail="策略版本不存在")
    events = repository.list_strategy_audit_events(strategy_id, strategy_version)
    public_events = []
    for event in events:
        details = event.get("details") or {}
        public_events.append({
            "sequence_no": event["sequence_no"],
            "event_type": event["event_type"],
            "actor_role": event["actor_role"],
            "details": {
                key: details[key]
                for key in (
                    "initial_status",
                    "manifest_sha256",
                    "strategy_kind",
                    "from_status",
                    "to_status",
                    "release_assessment",
                )
                if key in details
            },
            "previous_hash": event["previous_hash"],
            "event_hash": event["event_hash"],
            "created_at": event["created_at"],
        })
    return {
        "strategy": item,
        "audit": {
            "items": public_events,
            "verification": repository.verify_strategy_audit_chain(
                strategy_id,
                strategy_version,
            ),
        },
    }


@router.get("/strategies/{strategy_id}/{strategy_version}/shadow-outcomes")
def get_agent_strategy_shadow_outcomes(
    strategy_id: str,
    strategy_version: str,
    limit: int = Query(default=50, ge=1, le=500),
):
    report = strategy_shadow_service.report(
        strategy_id,
        strategy_version,
        limit=limit,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="策略版本不存在")
    return report


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_agent_run(
    request: CreateAgentRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    user_id = _agent_user_id(principal)
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 个字符")
    if idempotency_key:
        existing = repository.get_run_by_idempotency_key(user_id, idempotency_key)
        if existing is not None:
            if existing.get("status") == "queued":
                _dispatch_run(str(existing["id"]))
            return {"created": False, "run": existing}
    max_pending = max(1, int(os.getenv("AGENT_MAX_PENDING_RUNS", "20")))
    if repository.count_active_runs() >= max_pending:
        raise HTTPException(
            status_code=429,
            detail="Agent 任务队列已满，请等待现有真实数据任务完成后再试",
        )
    input_payload = request.model_dump()
    profile = storage.get_investment_profile(user_id=_portfolio_user_id(principal))
    profile_version_id = (
        str(profile.get("profile_version_id"))
        if request.include_portfolio_context and profile.get("configured") and profile.get("profile_version_id")
        else None
    )
    if profile_version_id:
        input_payload["profile_version_id"] = profile_version_id
    run, created = repository.create_run(
        request.intent,
        input_payload,
        tenant_id="public",
        user_id=user_id,
        idempotency_key=idempotency_key,
        profile_version_id=profile_version_id,
    )
    _dispatch_run(str(run["id"]))
    return {"created": created, "run": repository.get_run(run["id"])}


@router.post("/batches", status_code=status.HTTP_202_ACCEPTED)
def create_agent_batch(
    request: CreateAgentBatchRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    user_id = _agent_user_id(principal)
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 个字符")
    if idempotency_key:
        existing = repository.get_batch_by_idempotency_key(user_id, idempotency_key)
        if existing is not None:
            _dispatch_batch(existing)
            return {"created": False, "batch": _summarize_batch_public(existing)}

    max_batch_size = max(2, min(8, int(os.getenv("AGENT_MAX_BATCH_SIZE", "6"))))
    if len(request.codes) > max_batch_size:
        raise HTTPException(
            status_code=400,
            detail=f"单批最多研究 {max_batch_size} 只基金；请拆分批次以保护真实数据源。",
        )
    max_pending = max(1, int(os.getenv("AGENT_MAX_PENDING_RUNS", "20")))
    input_payload = request.model_dump()
    profile = storage.get_investment_profile(user_id=_portfolio_user_id(principal))
    profile_version_id = (
        str(profile.get("profile_version_id"))
        if request.include_portfolio_context and profile.get("configured") and profile.get("profile_version_id")
        else None
    )
    if profile_version_id:
        input_payload["profile_version_id"] = profile_version_id
    try:
        batch, created = repository.create_batch(
            request.intent,
            input_payload,
            tenant_id="public",
            user_id=user_id,
            idempotency_key=idempotency_key,
            profile_version_id=profile_version_id,
            max_active_runs=max_pending,
        )
    except AgentQueueCapacityError as error:
        raise HTTPException(
            status_code=429,
            detail=(
                f"当前已有 {error.active} 个活动任务，本批需要 {error.requested} 个队列名额，"
                f"超过系统上限 {error.maximum}。"
            ),
        ) from error
    _dispatch_batch(batch)
    return {"created": created, "batch": _summarize_batch_public(batch)}


@router.get("/batches")
def list_agent_batches(
    limit: int = Query(default=6, ge=1, le=20),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = [
        _summarize_batch_public(batch, include_execution_details=False)
        for batch in repository.list_batches(
            tenant_id="public",
            user_id=_agent_user_id(principal),
            limit=limit,
        )
    ]
    return {"items": items, "count": len(items)}


@router.get("/batches/{batch_id}")
def get_agent_batch(
    batch_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    return _summarize_batch_public(_get_batch_or_404(batch_id, principal))


@router.post("/batches/{batch_id}/allocation")
def create_agent_batch_allocation(
    batch_id: str,
    request: CreateBatchAllocationRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    batch = _get_batch_or_404(batch_id, principal)
    try:
        event, created = batch_allocations.create_batch_allocation(
            repository,
            batch,
            expected_batch_input_hash=request.expected_batch_input_hash,
            user_id=_agent_user_id(principal),
            actor_id=_actor_id(principal),
        )
    except batch_allocations.BatchAllocationValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except batch_allocations.BatchAllocationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    refreshed = repository.get_batch(batch_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Agent Batch 不存在")
    return {
        "created": created,
        "allocation_event_id": event.get("id"),
        "batch": _summarize_batch_public(refreshed),
    }


@router.post("/batches/{batch_id}/purchase-preflight")
def create_agent_batch_purchase_preflight(
    batch_id: str,
    request: CreateBatchPurchasePreflightRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    batch = _get_batch_or_404(batch_id, principal)
    try:
        event, created = batch_purchase_preflight.create_batch_purchase_preflight(
            repository,
            batch,
            request.model_dump(mode="json"),
            user_id=str(batch.get("user_id") or "anonymous"),
            actor_id=_actor_id(principal),
        )
    except batch_purchase_preflight.BatchPurchasePreflightValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except batch_purchase_preflight.BatchPurchasePreflightConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    refreshed = repository.get_batch(batch_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Agent Batch 不存在")
    return {
        "created": created,
        "purchase_preflight_event_id": event.get("id"),
        "batch": _summarize_batch_public(refreshed),
    }


@router.post("/batches/{batch_id}/purchase-execution")
def record_agent_batch_purchase_execution(
    batch_id: str,
    request: CreateBatchPurchaseExecutionRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    batch = _get_batch_or_404(batch_id, principal)
    try:
        event, created = batch_purchase_execution.record_batch_purchase_execution(
            repository,
            batch,
            request.model_dump(mode="json"),
            user_id=str(batch.get("user_id") or "anonymous"),
            actor_id=_actor_id(principal),
        )
    except batch_purchase_execution.BatchPurchaseExecutionValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except batch_purchase_execution.BatchPurchaseExecutionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    refreshed = repository.get_batch(batch_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Agent Batch 不存在")
    return {
        "created": created,
        "purchase_execution_event_id": event.get("id"),
        "batch": _summarize_batch_public(refreshed),
    }


@router.post("/batches/{batch_id}/purchase-reconciliation")
def reconcile_agent_batch_purchase_holdings(
    batch_id: str,
    request: CreateBatchPurchaseReconciliationRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    batch = _get_batch_or_404(batch_id, principal)
    try:
        event, created = batch_purchase_execution.reconcile_batch_purchase_holdings(
            repository,
            batch,
            request.model_dump(mode="json"),
            user_id=str(batch.get("user_id") or "anonymous"),
            actor_id=_actor_id(principal),
        )
    except batch_purchase_execution.BatchPurchaseExecutionValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except batch_purchase_execution.BatchPurchaseExecutionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    refreshed = repository.get_batch(batch_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Agent Batch 不存在")
    return {
        "created": created,
        "purchase_reconciliation_event_id": event.get("id"),
        "batch": _summarize_batch_public(refreshed),
    }


@router.post("/batches/{batch_id}/purchase-attribution")
def create_agent_batch_purchase_attribution(
    batch_id: str,
    request: CreateBatchPurchaseAttributionRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    batch = _get_batch_or_404(batch_id, principal)
    try:
        snapshot, created = (
            batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                repository,
                batch,
                request.model_dump(mode="json"),
                user_id=str(batch.get("user_id") or "anonymous"),
                actor_id=_actor_id(principal),
            )
        )
    except batch_purchase_attribution.BatchPurchaseAttributionValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except batch_purchase_attribution.BatchPurchaseAttributionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    refreshed = repository.get_batch(batch_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Agent Batch 不存在")
    return {
        "created": created,
        "purchase_attribution_snapshot_id": snapshot.get("id"),
        "batch": _summarize_batch_public(refreshed),
    }


@router.post("/batches/{batch_id}/cancel")
def cancel_agent_batch(
    batch_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    batch = _get_batch_or_404(batch_id, principal)
    requested = 0
    for item in batch.get("items") or []:
        run = item.get("run") or {}
        if run.get("status") in RUN_TERMINAL_STATUSES:
            continue
        repository.request_cancel(str(run["id"]), actor_id=_actor_id(principal))
        requested += 1
    refreshed = repository.get_batch(batch_id)
    return {
        "cancel_requested_count": requested,
        "batch": _summarize_batch_public(refreshed or batch),
    }


@router.get("/runs")
def list_agent_runs(
    limit: int = Query(default=8, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=8, max_length=500),
    run_status: Literal[
        "queued", "running", "completed", "partial", "failed", "cancelled", "abstained"
    ] | None = Query(default=None, alias="status"),
    code: str | None = Query(default=None, pattern=r"^\d{6}$"),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    before = _decode_cursor(cursor) if cursor else None
    runs, has_more = repository.list_runs(
        tenant_id="public",
        user_id=_agent_user_id(principal),
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
def get_agent_run(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    return _get_run_or_404(run_id, principal)


@router.get("/runs/{run_id}/strategy-shadow-outcome")
def get_agent_run_strategy_shadow_outcome(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    run = _get_run_or_404(run_id, principal)
    eligibility = strategy_shadow_service.eligibility(run)
    enrollment = repository.get_strategy_shadow_enrollment(run_id)
    verification = (
        strategy_shadow_service.verify_enrollment(enrollment)
        if enrollment is not None
        else None
    )
    cohort = (
        repository.get_strategy_shadow_cohort(str(enrollment["id"]))
        if enrollment is not None
        else None
    )
    cohort_verification = (
        strategy_shadow_service.verify_cohort(cohort, enrollment)
        if enrollment is not None
        else None
    )
    result_strategy = (run.get("result") or {}).get("strategy") or {}
    strategy_id = str(result_strategy.get("strategy_id") or "")
    strategy_version = str(result_strategy.get("strategy_version") or "")
    report = (
        strategy_shadow_service.report(strategy_id, strategy_version, limit=20)
        if strategy_id and strategy_version
        else None
    )
    observations = repository.list_evidence_by_type(
        run_id,
        "strategy_shadow_outcome",
        include_payload=True,
    )
    return {
        "eligibility": eligibility,
        "enrollment": strategy_shadow_service.public_enrollment(enrollment),
        "verification": verification,
        "cohort": strategy_shadow_service.public_cohort(cohort),
        "cohort_verification": cohort_verification,
        "observations": [
            {
                "evidence_id": item["id"],
                "as_of": item.get("as_of"),
                "quality_status": item.get("quality_status"),
                "schema_version": item.get("schema_version"),
                "payload_sha256": item.get("payload_sha256"),
                "integrity_verified": item.get("integrity_verified"),
                "created_at": item.get("created_at"),
            }
            for item in observations
        ],
        "strategy_summary": report,
        "policy": "策略 Shadow 样本由终态 Run 自动入组并绑定不可变市场、资产、周期和信号状态 Cohort；公网仅可读，不能手工挑样本、跨 Cohort 池化、提前结算或改写基线。",
    }


@router.get("/runs/{run_id}/evaluations")
def list_agent_run_evaluations(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _get_run_or_404(run_id, principal)
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
def get_agent_run_outcome_schedule(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    run = _get_run_or_404(run_id, principal)
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
def configure_agent_run_outcome_schedule(
    run_id: str,
    request: OutcomeScheduleRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    run = _get_run_or_404(run_id, principal)
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
        actor_id=_actor_id(principal),
    )
    if request.enabled:
        if uses_celery_queue():
            try:
                enqueue_scheduler_task(TASK_OUTCOME_SCHEDULES)
            except TaskQueueUnavailableError as error:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "task_queue_unavailable",
                        "message": str(error),
                        "schedule_id": schedule.get("id"),
                        "durable_status": schedule.get("status"),
                    },
                ) from error
        else:
            start_worker()
    return {
        "changed": changed,
        "eligibility": eligibility,
        "schedule": _schedule_view(schedule),
    }


@router.post("/runs/{run_id}/evaluate")
def evaluate_agent_run(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _get_run_or_404(run_id, principal)
    try:
        return _outcome_service().evaluate_run(
            run_id,
            actor_type="user",
            actor_id=_actor_id(principal),
        )
    except OutcomeEvaluationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error


@router.get("/runs/{run_id}/comparison")
def get_agent_run_comparison(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    current = _get_run_or_404(run_id, principal)
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
    principal: AuthPrincipal = Depends(principal_from_request),
):
    source = _get_run_or_404(run_id, principal)
    if source["status"] not in RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="运行中的 Agent Run 不能重复启动")
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 个字符")
    if idempotency_key:
        existing = repository.get_run_by_idempotency_key(source["user_id"], idempotency_key)
        if existing is not None:
            if existing.get("status") == "queued":
                _dispatch_run(str(existing["id"]))
            return {"created": False, "run": existing}
    input_payload = dict(source.get("input") or {})
    profile = storage.get_investment_profile(user_id=source["user_id"])
    profile_version_id = (
        str(profile.get("profile_version_id"))
        if input_payload.get("include_portfolio_context", True)
        and profile.get("configured")
        and profile.get("profile_version_id")
        else None
    )
    if profile_version_id:
        input_payload["profile_version_id"] = profile_version_id
    else:
        input_payload.pop("profile_version_id", None)
    run, created = repository.create_run(
        source["intent"],
        input_payload,
        tenant_id=source["tenant_id"],
        user_id=source["user_id"],
        idempotency_key=idempotency_key,
        parent_run_id=source["id"],
        profile_version_id=profile_version_id,
    )
    _dispatch_run(str(run["id"]))
    return {"created": created, "run": repository.get_run(run["id"])}


@router.post("/runs/{run_id}/cancel")
def cancel_agent_run(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _get_run_or_404(run_id, principal)
    return repository.request_cancel(run_id, actor_id=_actor_id(principal))


@router.get("/runs/{run_id}/evidence/{evidence_id}")
def get_agent_evidence(
    run_id: str,
    evidence_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _get_run_or_404(run_id, principal)
    evidence = repository.get_evidence(run_id, evidence_id, include_payload=True)
    if evidence is None:
        raise HTTPException(status_code=404, detail="该 Agent Run 中不存在此 Evidence")
    return evidence


@router.get("/runs/{run_id}/audit")
def get_agent_audit(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _get_run_or_404(run_id, principal)
    items = repository.list_audit_events(run_id)
    verification = repository.verify_audit_chain(run_id)
    return {
        "items": items[-limit:],
        "count": len(items),
        "verification": verification,
    }
