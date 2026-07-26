# -*- coding: utf-8 -*-
"""Authenticated HTTP boundary for the Alpha probability laboratory."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auth import AuthPrincipal, principal_from_request, require_admin
from background_jobs import sanitize_worker_error
from alpha_forecast_repository import (
    AlphaForecastConflict,
    AlphaForecastNotFound,
    repository,
)
import alpha_forecast_service as service
from task_queue import TaskQueueUnavailableError


router = APIRouter(
    prefix="/api/v1/alpha-forecasts",
    tags=["多周期 Alpha 概率实验室"],
)


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


class AlphaAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    name: str = Field(default="", max_length=80)


class AlphaProgramRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=80)
    asset_type: Literal["stock", "fund"] = "stock"
    market: Literal["A股", "港股", "美股", "基金"] = "A股"
    symbols: list[AlphaAssetRequest] = Field(
        min_length=4,
        max_length=12,
    )
    benchmark_symbol: str | None = Field(default=None, max_length=16)
    history_months: int = Field(default=60, ge=36, le=120)
    cadence_days: Literal[7, 14, 30] = 7
    round_trip_cost_bps: float = Field(default=30, ge=0, le=300)
    acknowledged: bool

    @model_validator(mode="after")
    def validate_asset_contract(self):
        if not self.acknowledged:
            raise ValueError(
                "必须确认概率不是收益承诺，且项目不会自动下单"
            )
        if self.asset_type == "fund":
            if self.market != "基金":
                raise ValueError("基金项目的市场必须选择 基金")
            if self.history_months < 60:
                raise ValueError("基金概率研究至少需要 60 个月历史")
        elif self.market == "基金":
            raise ValueError("股票项目不能选择 基金 市场")
        return self


class AlphaRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: bool

    @model_validator(mode="after")
    def acknowledgement_required(self):
        if not self.acknowledged:
            raise ValueError("必须确认本次运行只形成研究与前瞻证据")
        return self


class AlphaProgramActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume", "retire"]


@router.get("/overview")
def get_alpha_forecast_overview(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return service.overview(
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            limit=limit,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Alpha 概率实验室读取失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post(
    "/programs",
    status_code=status.HTTP_201_CREATED,
)
def create_alpha_forecast_program(
    request: AlphaProgramRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return service.create_program(
            request.model_dump(
                mode="json",
                exclude={"acknowledged"},
                exclude_none=True,
            ),
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AlphaForecastConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Alpha 概率任务队列不可用:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Alpha 概率项目创建失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.get("/programs/{program_id}")
def get_alpha_forecast_program(
    program_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    program = repository.get_program(
        program_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    if program is None:
        raise HTTPException(status_code=404, detail="概率研究项目不存在")
    program["forward_scorecard"] = service.forward_scorecard(
        program_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    return program


@router.post(
    "/programs/{program_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_alpha_forecast_run(
    program_id: str,
    _request: AlphaRunRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return service.start_program_run(
            program_id,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except AlphaForecastNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AlphaForecastConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Alpha 概率运行创建失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post("/programs/{program_id}/actions")
def update_alpha_forecast_program(
    program_id: str,
    request: AlphaProgramActionRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return service.transition_program(
            program_id,
            request.action,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
    except AlphaForecastNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AlphaForecastConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/runs/{run_id}")
def get_alpha_forecast_run(
    run_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    run = repository.get_run(
        run_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    if run is None:
        raise HTTPException(status_code=404, detail="概率运行不存在")
    return run


@router.post(
    "/programs/{program_id}/settle",
    status_code=status.HTTP_202_ACCEPTED,
)
def settle_alpha_forecast_program(
    program_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    program = repository.get_program(
        program_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
        include_events=False,
    )
    if program is None:
        raise HTTPException(status_code=404, detail="概率研究项目不存在")
    try:
        settlement = service.request_program_settlement(
            program_id,
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
        )
        return {
            **settlement,
            "forward_scorecard": service.forward_scorecard(
                program_id,
                tenant_id=_tenant_id(principal),
                user_id=_subject_id(principal),
            ),
        }
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "真实前瞻结果结算失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post("/maintenance")
def maintain_alpha_forecast_lab(
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return service.maintain_programs(
            limit=5,
            actor_id=f"{principal.user_id}:manual-maintenance",
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Alpha 概率实验室维护失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
