# -*- coding: utf-8 -*-
"""Authenticated boundary for the multi-horizon Alpha capital router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha_capital_repository import (
    AlphaCapitalConflict,
    repository,
)
import alpha_capital_router as service
from auth import AuthPrincipal, principal_from_request
from background_jobs import sanitize_worker_error


router = APIRouter(
    prefix="/api/v1/alpha-capital",
    tags=["多周期 Alpha 资本路由"],
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


class FreezeAlphaCapitalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    research_only_acknowledged: bool

    @model_validator(mode="after")
    def acknowledgement_required(self):
        if not self.research_only_acknowledged:
            raise ValueError(
                "必须确认模型权重仅供研究，不是订单、收益承诺或自动交易授权"
            )
        return self


@router.get("")
def get_alpha_capital_route(
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        return service.current_alpha_capital_route(
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Alpha 资本路由读取失败："
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.post(
    "/mandates",
    status_code=status.HTTP_201_CREATED,
)
def freeze_alpha_capital_mandate(
    request: FreezeAlphaCapitalRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    try:
        item, created = service.freeze_alpha_capital_route(
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            actor_id=_actor_id(principal),
            expected_evidence_sha256=request.evidence_sha256,
        )
        return {"created": created, "mandate": item}
    except AlphaCapitalConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Alpha 资本路线冻结失败："
                f"{sanitize_worker_error(error)}"
            ),
        ) from error


@router.get("/mandates")
def get_alpha_capital_mandates(
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthPrincipal = Depends(principal_from_request),
):
    return {
        "schema_version": "alpha_capital_mandate_list.v1",
        "items": service.list_mandates(
            tenant_id=_tenant_id(principal),
            user_id=_subject_id(principal),
            limit=limit,
        ),
    }


@router.get("/mandates/{mandate_id}")
def get_alpha_capital_mandate(
    mandate_id: str,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    item = repository.get_mandate(
        mandate_id,
        tenant_id=_tenant_id(principal),
        user_id=_subject_id(principal),
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Alpha 资本路由指令不存在",
        )
    return item
