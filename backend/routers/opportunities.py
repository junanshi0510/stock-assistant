# -*- coding: utf-8 -*-
"""API boundary for versioned cross-market opportunity campaigns."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auth import AuthPrincipal, principal_from_request
from market_data_gateway import MarketDataGatewayError, execute_market_operation
from opportunity_repository import (
    OpportunityConflictError,
    OpportunityNotFoundError,
    repository,
)
from opportunity_profit_repository import repository as profit_repository
from opportunity_committee_repository import (
    repository as committee_repository,
)
import opportunity_committee_service
import opportunity_profit_service
import opportunity_service
from task_queue import TaskQueueConfigurationError, TaskQueueUnavailableError


router = APIRouter(prefix="/api/v1/opportunities", tags=["机会工厂"])
MarketName = Literal["A股", "港股", "美股"]


def _subject_id(principal: AuthPrincipal) -> str:
    return principal.subject_id if isinstance(principal, AuthPrincipal) else "default"


def _actor_id(principal: AuthPrincipal) -> str:
    return principal.user_id if isinstance(principal, AuthPrincipal) else "default"


def _raise_domain(error: Exception):
    if isinstance(error, OpportunityNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, OpportunityConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, (TaskQueueConfigurationError, TaskQueueUnavailableError)):
        raise HTTPException(status_code=503, detail=f"机会扫描任务队列不可用:{error}") from error
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error


class UniverseSymbolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: MarketName
    symbol: str = Field(min_length=1, max_length=16)
    name: str = Field(default="", max_length=80)


class UniverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_presets: bool = True
    include_watchlist: bool = True
    hot_lists: list[Literal["active", "gainers", "losers"]] = Field(
        default_factory=list, max_length=3
    )
    hot_limit_per_market: int = Field(default=8, ge=5, le=20)
    symbols: list[UniverseSymbolRequest] = Field(default_factory=list, max_length=80)


class FactorWeightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    momentum: float = Field(default=30, ge=0, le=100)
    value: float = Field(default=15, ge=0, le=100)
    quality: float = Field(default=20, ge=0, le=100)
    growth: float = Field(default=15, ge=0, le=100)
    risk: float = Field(default=20, ge=0, le=100)

    @model_validator(mode="after")
    def at_least_one_factor(self):
        if self.momentum + self.value + self.quality + self.growth + self.risk <= 0:
            raise ValueError("至少一个因子权重必须大于 0")
        return self


class GatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_history_days: int = Field(default=180, ge=60, le=1000)
    max_data_age_days: int = Field(default=10, ge=3, le=45)
    min_technical_score: float = Field(default=45, ge=0, le=100)
    min_return_3m: float = Field(default=-15, ge=-100, le=300)
    max_annual_vol: float = Field(default=80, ge=5, le=300)
    max_drawdown_pct: float = Field(default=60, ge=5, le=100)
    min_factor_coverage: float = Field(default=0.4, ge=0.2, le=1)
    min_composite_score: float = Field(default=58, ge=0, le=100)
    require_fundamentals: bool = False


class PortfolioPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_positions: int = Field(default=8, ge=2, le=12)
    max_position_pct: float = Field(default=20, ge=5, le=50)
    min_cash_pct: float = Field(default=10, ge=0, le=60)
    max_pair_correlation: float = Field(default=0.85, ge=0, le=1)
    defensive_cash_add_pct: float = Field(default=10, ge=0, le=30)
    weighting: Literal["score_inverse_vol", "inverse_vol", "equal"] = "score_inverse_vol"


class StrategyDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(default="custom", max_length=60)
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=8, max_length=300)
    markets: list[MarketName] = Field(min_length=1, max_length=3)
    history_months: int = Field(default=18, ge=9, le=60)
    universe: UniverseRequest = Field(default_factory=UniverseRequest)
    factors: FactorWeightsRequest = Field(default_factory=FactorWeightsRequest)
    gates: GatesRequest = Field(default_factory=GatesRequest)
    portfolio: PortfolioPolicyRequest = Field(default_factory=PortfolioPolicyRequest)


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str = Field(min_length=20, max_length=96)


class ProfitPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_horizons: list[int] = Field(
        default_factory=lambda: [5, 20, 60], min_length=1, max_length=5
    )
    primary_horizon: int = Field(default=20, ge=3, le=252)
    round_trip_cost_bps: float = Field(default=30, ge=10, le=500)
    minimum_coverage_pct: float = Field(default=90, ge=80, le=100)
    minimum_mature_baskets: int = Field(default=6, ge=6, le=100)
    minimum_mean_excess_return_pct: float = Field(default=0.5, ge=0, le=20)
    minimum_positive_excess_rate_pct: float = Field(default=55, ge=50, le=100)
    maximum_cohort_drawdown_pct: float = Field(default=15, ge=3, le=25)
    maximum_manual_pilot_pct: float = Field(default=5, ge=0.5, le=5)
    latest_basket_max_age_days: int = Field(default=14, ge=3, le=30)

    @model_validator(mode="after")
    def primary_is_a_horizon(self):
        if self.primary_horizon not in self.evaluation_horizons:
            raise ValueError("主验证窗口必须属于观察窗口")
        if len(set(self.evaluation_horizons)) != len(self.evaluation_horizons):
            raise ValueError("观察窗口不能重复")
        return self


@router.get("/templates")
def get_opportunity_templates():
    return {
        "policy_version": opportunity_service.POLICY_VERSION,
        "items": opportunity_service.strategy_templates(),
        "scope_notice": "内置模板使用候选种子池，不代表交易所全量股票。",
    }


@router.get("/overview")
def get_opportunity_overview(
    principal: AuthPrincipal = Depends(principal_from_request),
):
    user_id = _subject_id(principal)
    strategies = repository.list_strategies(user_id=user_id, limit=50)
    runs = repository.list_runs(user_id=user_id, limit=30)
    baskets = repository.list_paper_baskets(user_id=user_id, limit=12)
    return {
        "policy_version": opportunity_service.POLICY_VERSION,
        "strategies": strategies,
        "runs": runs,
        "paper_baskets": baskets,
        "summary": {
            "strategy_count": len(strategies),
            "active_run_count": sum(1 for item in runs if item["status"] in {"queued", "running"}),
            "completed_run_count": sum(1 for item in runs if item["status"] in {"succeeded", "partial"}),
            "paper_basket_count": len(baskets),
        },
    }


@router.get("/profit-lab")
def get_opportunity_profit_lab(
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return opportunity_profit_service.profit_lab_overview(
            user_id=_subject_id(principal)
        )
    except Exception as error:
        _raise_domain(error)


@router.get("/committee")
def get_opportunity_investment_committee(
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return opportunity_committee_service.current_committee(
            user_id=_subject_id(principal)
        )
    except Exception as error:
        _raise_domain(error)


@router.post(
    "/committee/mandates",
    status_code=status.HTTP_201_CREATED,
)
def create_opportunity_committee_mandate(
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item, created = opportunity_committee_service.freeze_committee(
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
        return {"item": item, "created": created}
    except Exception as error:
        _raise_domain(error)


@router.get("/committee/mandates")
def list_opportunity_committee_mandates(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    return opportunity_committee_service.mandate_history(
        user_id=_subject_id(principal), limit=limit
    )


@router.get("/committee/mandates/{mandate_id}")
def get_opportunity_committee_mandate(
    mandate_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = committee_repository.get_mandate(
        mandate_id, user_id=_subject_id(principal)
    )
    if item is None:
        raise HTTPException(
            status_code=404, detail="策略投资委员会指令不存在"
        )
    return item


@router.get("/strategies/{strategy_id}/profit-policy")
def get_opportunity_profit_policy(
    strategy_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return opportunity_profit_service.get_policy(
            strategy_id, user_id=_subject_id(principal)
        )
    except Exception as error:
        _raise_domain(error)


@router.post(
    "/strategies/{strategy_id}/profit-policy/versions",
    status_code=status.HTTP_201_CREATED,
)
def create_opportunity_profit_policy(
    strategy_id: str,
    request: ProfitPolicyRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return opportunity_profit_service.save_policy(
            strategy_id,
            request.model_dump(),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except Exception as error:
        _raise_domain(error)


@router.post(
    "/strategies/{strategy_id}/profit-scorecards",
    status_code=status.HTTP_201_CREATED,
)
def create_opportunity_profit_scorecard(
    strategy_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item, created = opportunity_profit_service.persist_scorecard(
            strategy_id,
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
        return {"item": item, "created": created}
    except Exception as error:
        _raise_domain(error)


@router.get("/profit-scorecards")
def list_opportunity_profit_scorecards(
    strategy_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = profit_repository.list_scorecards(
        user_id=_subject_id(principal),
        strategy_id=strategy_id,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/profit-scorecards/{scorecard_id}")
def get_opportunity_profit_scorecard(
    scorecard_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = profit_repository.get_scorecard(
        scorecard_id, user_id=_subject_id(principal)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="收益验证记分卡不存在")
    return item


@router.get("/strategies")
def list_opportunity_strategies(
    include_archived: bool = Query(False),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = repository.list_strategies(
        user_id=_subject_id(principal), include_archived=include_archived
    )
    return {"items": items, "count": len(items)}


@router.post("/strategies", status_code=status.HTTP_201_CREATED)
def create_opportunity_strategy(
    request: StrategyDefinitionRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        definition = opportunity_service.normalize_definition(request.model_dump())
        return repository.create_strategy(
            user_id=_subject_id(principal),
            definition=definition,
            actor_id=_actor_id(principal),
        )
    except Exception as error:
        _raise_domain(error)

@router.get("/strategies/{strategy_id}")
def get_opportunity_strategy(
    strategy_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = repository.get_strategy(strategy_id, user_id=_subject_id(principal))
    if item is None:
        raise HTTPException(status_code=404, detail="机会策略不存在")
    return item


@router.post("/strategies/{strategy_id}/versions", status_code=status.HTTP_201_CREATED)
def create_opportunity_strategy_version(
    strategy_id: str,
    request: StrategyDefinitionRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        definition = opportunity_service.normalize_definition(request.model_dump())
        return repository.add_strategy_version(
            strategy_id,
            user_id=_subject_id(principal),
            definition=definition,
            actor_id=_actor_id(principal),
        )
    except Exception as error:
        _raise_domain(error)


@router.delete("/strategies/{strategy_id}")
def archive_opportunity_strategy(
    strategy_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return repository.archive_strategy(strategy_id, user_id=_subject_id(principal))
    except Exception as error:
        _raise_domain(error)


@router.get("/runs")
def list_opportunity_runs(
    strategy_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = repository.list_runs(
        user_id=_subject_id(principal), strategy_id=strategy_id, limit=limit
    )
    return {"items": items, "count": len(items)}


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_opportunity_run(
    request: CreateRunRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return opportunity_service.start_run(
            request.strategy_id,
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except Exception as error:
        _raise_domain(error)


@router.get("/runs/{run_id}")
def get_opportunity_run(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item = opportunity_service.refresh_run_status(
            run_id, user_id=_subject_id(principal)
        )
    except Exception as error:
        _raise_domain(error)
    if item is None:
        raise HTTPException(status_code=404, detail="机会扫描不存在")
    return item


@router.post("/runs/{run_id}/paper-baskets", status_code=status.HTTP_201_CREATED)
def create_opportunity_paper_basket(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item, created = opportunity_service.create_paper_basket(
            run_id, user_id=_subject_id(principal)
        )
        return {"item": item, "created": created}
    except Exception as error:
        _raise_domain(error)


@router.get("/paper-baskets")
def list_opportunity_paper_baskets(
    limit: int = Query(default=30, ge=1, le=200),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    items = repository.list_paper_baskets(user_id=_subject_id(principal), limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/paper-baskets/{basket_id}")
def get_opportunity_paper_basket(
    basket_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = repository.get_paper_basket(basket_id, user_id=_subject_id(principal))
    if item is None:
        raise HTTPException(status_code=404, detail="纸面组合不存在")
    return item


@router.post("/paper-baskets/{basket_id}/observations")
def observe_opportunity_paper_basket(
    basket_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    user_id = _subject_id(principal)
    try:
        return execute_market_operation(
            "opportunity.observe",
            {"basket_id": basket_id, "user_id": user_id},
            tenant_id="public",
            user_id=user_id,
            timeout_seconds=300,
            max_attempts=1,
        )
    except MarketDataGatewayError as error:
        suffix = f" [job_id={error.job_id}]" if error.job_id else ""
        raise HTTPException(
            status_code=error.status_code,
            detail=f"纸面组合真实行情观察失败:{error}{suffix}",
        ) from error
    except Exception as error:
        _raise_domain(error)
