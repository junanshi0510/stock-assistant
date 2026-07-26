# -*- coding: utf-8 -*-
"""Authenticated control plane for the A-share factor warehouse."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auth import AuthPrincipal, principal_from_request, require_admin
from background_jobs import sanitize_worker_error
from quant_factor_repository import (
    QuantFactorConflictError,
    QuantFactorNotFoundError,
    repository,
)
import quant_factor_warehouse_service as factor_service
from task_queue import TaskQueueUnavailableError


router = APIRouter(
    prefix="/api/v1/quant-factors",
    tags=["A股时点因子仓库"],
)


class QuantFactorBackfillPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Literal[
        "valuation_daily", "financial_indicator"
    ] = "valuation_daily"
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    symbols: list[str] = Field(default_factory=list, max_length=40)
    acknowledged: bool

    @model_validator(mode="after")
    def acknowledgement_required(self):
        if not self.acknowledged:
            raise ValueError(
                "必须确认回填会按额度逐批执行，不能保证立即完成"
            )
        return self


class QuantFactorSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Literal[
        "valuation_daily", "financial_indicator"
    ] = "valuation_daily"
    mode: Literal[
        "live_incremental", "historical_backfill"
    ] = "live_incremental"
    target_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    target_symbol: str | None = Field(default=None, max_length=16)
    period_start: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    period_end: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )


class QuantFactorPlanActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume", "cancel"]


@router.get("/overview")
def get_quant_factor_overview(
    plan_limit: int = Query(default=30, ge=1, le=100),
    run_limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return factor_service.overview(
            can_manage=principal.is_admin,
            plan_limit=plan_limit,
            run_limit=run_limit,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "时点因子仓库读取失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.get("/sync-runs/{run_id}")
def get_quant_factor_sync_run(
    run_id: str,
    _principal: AuthPrincipal = Depends(require_admin),
):
    item = repository.get_sync_run(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="量化因子同步任务不存在")
    return item


@router.post(
    "/backfill-plans",
    status_code=status.HTTP_201_CREATED,
)
def create_quant_factor_backfill_plan(
    request: QuantFactorBackfillPlanRequest,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return factor_service.create_backfill_plan(
            request.model_dump(
                mode="json",
                exclude={"acknowledged"},
            ),
            actor_id=principal.user_id,
        )
    except (ValueError, QuantFactorConflictError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "时点因子仓库任务队列不可用:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "时点因子回填计划创建失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post("/backfill-plans/{plan_id}/actions")
def update_quant_factor_backfill_plan(
    plan_id: str,
    request: QuantFactorPlanActionRequest,
    _principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return factor_service.transition_backfill_plan(
            plan_id,
            request.action,
        )
    except QuantFactorNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except QuantFactorConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/sync-runs", status_code=status.HTTP_202_ACCEPTED)
def create_quant_factor_sync_run(
    request: QuantFactorSyncRequest,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return factor_service.request_sync(
            request.model_dump(mode="json", exclude_none=True),
            actor_id=principal.user_id,
        )
    except QuantFactorConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "时点因子仓库任务队列不可用:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "时点因子同步创建失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post("/schedule", status_code=status.HTTP_202_ACCEPTED)
def schedule_next_quant_factor_target(
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return factor_service.schedule_due_sync(
            actor_id=f"{principal.user_id}:manual-scheduler"
        )
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "时点因子仓库任务队列不可用:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "时点因子调度失败:"
                f"{sanitize_worker_error(error)}"
            ),
        ) from error
