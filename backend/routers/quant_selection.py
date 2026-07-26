# -*- coding: utf-8 -*-
"""HTTP boundary for point-in-time multi-factor portfolio research."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auth import AuthPrincipal, principal_from_request
from background_jobs import sanitize_worker_error
from quant_selection_repository import (
    QuantSelectionConflictError,
    QuantSelectionNotFoundError,
    repository,
)
from quant_selection_forward_repository import (
    QuantSelectionForwardConflictError,
    QuantSelectionForwardNotFoundError,
)
from quant_research_program_repository import (
    QuantResearchProgramConflict,
    QuantResearchProgramNotFound,
    repository as research_program_repository,
)
import quant_selection_forward_service
import quant_selection_service
import quant_research_program_service
from task_queue import TaskQueueUnavailableError


router = APIRouter(
    prefix="/api/v1/quant-selection",
    tags=["组合选股实验室"],
)
MarketName = Literal["A股", "港股", "美股"]


def _tenant_id(_principal: object) -> str:
    return "public"


def _subject_id(principal: object) -> str:
    return (
        principal.subject_id
        if isinstance(principal, AuthPrincipal)
        else "default"
    )


def _actor_id(principal: object) -> str:
    return (
        principal.user_id
        if isinstance(principal, AuthPrincipal)
        else "default"
    )


class QuantSelectionSymbolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    name: str = Field(default="", max_length=80)


class QuantSelectionFactorWeightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    momentum: float = Field(default=35, ge=0, le=100)
    trend_quality: float = Field(default=25, ge=0, le=100)
    low_volatility: float = Field(default=25, ge=0, le=100)
    liquidity: float = Field(default=15, ge=0, le=100)
    fundamental_quality: float = Field(default=0, ge=0, le=100)
    value: float = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def at_least_one_factor(self):
        if (
            self.momentum
            + self.trend_quality
            + self.low_volatility
            + self.liquidity
            + self.fundamental_quality
            + self.value
            <= 0
        ):
            raise ValueError("至少一个因子权重必须大于 0")
        return self


class QuantSelectionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="历史多因子组合实验", min_length=2, max_length=80)
    market: MarketName = "A股"
    universe_mode: Literal[
        "frozen_symbols", "tushare_index"
    ] = "tushare_index"
    universe_attestation: Literal[
        "current_snapshot", "historical_membership"
    ] = "current_snapshot"
    index_code: Literal[
        "000300.SH", "000905.SH", "000852.SH"
    ] = "000300.SH"
    index_member_limit: int = Field(default=12, ge=8, le=24)
    symbols: list[QuantSelectionSymbolRequest] = Field(
        default_factory=list,
        max_length=40,
    )
    benchmark_symbol: str | None = Field(
        default=None,
        max_length=16,
    )
    history_months: int = Field(default=60, ge=36, le=120)
    lookback_days: Literal[126, 252] = 252
    minimum_history_days: int = Field(default=252, ge=126, le=504)
    rebalance_days: Literal[21, 63] = 21
    oos_segment_days: Literal[126, 252] = 126
    factor_weights: QuantSelectionFactorWeightsRequest = Field(
        default_factory=QuantSelectionFactorWeightsRequest
    )
    minimum_composite_score: float = Field(default=55, ge=0, le=100)
    minimum_price: float = Field(default=1, ge=0.01, le=10_000)
    minimum_average_turnover: float = Field(
        default=1_000_000,
        ge=0,
        le=1_000_000_000_000,
    )
    max_price_staleness_days: int = Field(default=7, ge=3, le=30)
    max_fundamental_staleness_days: int = Field(
        default=550, ge=90, le=900
    )
    max_valuation_staleness_days: int = Field(
        default=7, ge=3, le=30
    )
    construction_method: Literal[
        "equal_weight",
        "inverse_volatility",
        "score_inverse_volatility",
    ] = "score_inverse_volatility"
    max_positions: int = Field(default=6, ge=2, le=12)
    max_position_pct: float = Field(default=20, ge=5, le=50)
    minimum_cash_pct: float = Field(default=10, ge=0, le=60)
    initial_capital: float = Field(
        default=1_000_000,
        ge=100_000,
        le=100_000_000,
    )
    minimum_order_notional: float = Field(
        default=1_000,
        ge=0,
        le=10_000_000,
    )
    commission_bps: float = Field(default=5, ge=0, le=100)
    slippage_bps: float = Field(default=8, ge=0, le=200)
    impact_bps: float = Field(default=20, ge=0, le=500)
    sell_tax_bps: float = Field(default=0, ge=0, le=200)
    max_volume_participation_pct: float = Field(
        default=2.5,
        ge=0.1,
        le=20,
    )
    max_order_age_sessions: int = Field(default=3, ge=1, le=10)
    maximum_drawdown_pct: float = Field(default=25, ge=5, le=60)

    @model_validator(mode="after")
    def universe_matches_market(self):
        if self.universe_mode == "tushare_index" and self.market != "A股":
            raise ValueError("Tushare 历史指数成分模式当前只支持 A股")
        if self.universe_mode == "frozen_symbols" and len(self.symbols) < 6:
            raise ValueError("冻结自定义股票池至少需要 6 只股票")
        if self.market != "A股" and (
            self.factor_weights.fundamental_quality > 0
            or self.factor_weights.value > 0
        ):
            raise ValueError("披露日财务与时点估值因子当前只支持 A 股")
        return self


class QuantSelectionShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: bool
    expected_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuantSelectionForwardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: bool
    expected_snapshot_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )


class QuantResearchScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence: Literal["monthly", "quarterly"] = "monthly"
    first_run_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    run_time_local: str = Field(
        default="20:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    planned_cycles: int = Field(default=6, ge=6, le=24)


class QuantResearchProgramRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)
    policy: QuantSelectionRunRequest
    schedule: QuantResearchScheduleRequest = Field(
        default_factory=QuantResearchScheduleRequest
    )
    acknowledged: bool


class QuantResearchProgramRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=8, max_length=500)


@router.get("/overview")
def get_quant_selection_overview(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return quant_selection_service.overview(
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            limit=limit,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "组合选股实验室读取失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.get("/research-programs")
def get_quant_research_programs(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    return quant_research_program_service.overview(
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
        limit=limit,
    )


@router.post(
    "/research-programs",
    status_code=status.HTTP_201_CREATED,
)
def create_quant_research_program(
    request: QuantResearchProgramRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return quant_research_program_service.create_program(
            request.model_dump(mode="json"),
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except QuantResearchProgramConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/research-programs/{program_id}")
def get_quant_research_program(
    program_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = research_program_repository.get_program(
        program_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="量化研究计划不存在")
    return item


@router.post("/research-programs/{program_id}/reconcile")
def reconcile_quant_research_program(
    program_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = research_program_repository.get_program(
        program_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="量化研究计划不存在")
    return quant_research_program_service.reconcile_due_programs(
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
        program_id=program_id,
        actor_id=_actor_id(principal),
    )


@router.post("/research-programs/{program_id}/retire")
def retire_quant_research_program(
    program_id: str,
    request: QuantResearchProgramRetireRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return quant_research_program_service.retire_program(
            program_id,
            reason=request.reason,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except QuantResearchProgramNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_quant_selection_run(
    request: QuantSelectionRunRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return quant_selection_service.start_run(
            request.model_dump(mode="json"),
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except QuantSelectionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "组合选股实验任务队列暂不可用:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "组合选股实验创建失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.get("/runs")
def list_quant_selection_runs(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = repository.list_runs(
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/runs/{run_id}")
def get_quant_selection_run(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item = quant_selection_service.refresh_run_status(
            run_id,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
        )
    except QuantSelectionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if item is None:
        raise HTTPException(status_code=404, detail="组合选股实验不存在")
    return item


@router.post(
    "/runs/{run_id}/shadow-mandates",
    status_code=status.HTTP_201_CREATED,
)
def create_quant_selection_shadow_mandate(
    run_id: str,
    request: QuantSelectionShadowRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item, created = quant_selection_service.freeze_shadow_mandate(
            run_id,
            acknowledged=request.acknowledged,
            expected_result_sha256=request.expected_result_sha256,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
        return {"item": item, "created": created}
    except QuantSelectionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except QuantSelectionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/shadow-mandates")
def list_quant_selection_shadow_mandates(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = repository.list_shadow_mandates(
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/shadow-mandates/{mandate_id}")
def get_quant_selection_shadow_mandate(
    mandate_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = repository.get_shadow_mandate(
        mandate_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="前向纸面策略不存在")
    return item


@router.get("/forward-validations")
def list_quant_selection_forward_validations(
    limit: int = Query(default=100, ge=1, le=200),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return quant_selection_forward_service.forward_overview(
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            limit=limit,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "量化前向验证读取失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post(
    "/shadow-mandates/{mandate_id}/forward-validations",
    status_code=status.HTTP_201_CREATED,
)
def create_quant_selection_forward_validation(
    mandate_id: str,
    request: QuantSelectionForwardRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item, created = (
            quant_selection_forward_service.enroll_validation(
                mandate_id,
                acknowledged=request.acknowledged,
                expected_snapshot_sha256=(
                    request.expected_snapshot_sha256
                ),
                tenant_id=_tenant_id(principal),
                user_id=_subject_id(principal),
                actor_id=_actor_id(principal),
            )
        )
        return {"item": item, "created": created}
    except QuantSelectionForwardNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except QuantSelectionForwardConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post(
    "/forward-validations/{validation_id}/observations",
    status_code=status.HTTP_202_ACCEPTED,
)
def observe_quant_selection_forward_validation(
    validation_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return quant_selection_forward_service.request_observation(
            validation_id,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
        )
    except QuantSelectionForwardNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except QuantSelectionForwardConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "量化前向观察队列暂不可用:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "量化前向观察失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
