"""Authenticated availability status and administrator control-plane APIs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

import availability_service
from auth import AuthPrincipal, principal_from_request, require_admin
from observability import sanitize_log_value


router = APIRouter(prefix="/api", tags=["平台可用性"])


class AvailabilityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["standard", "deep"] = "standard"


@router.get("/platform/availability")
def get_platform_availability(
    principal: AuthPrincipal = Depends(principal_from_request),
):
    del principal
    try:
        return availability_service.public_summary()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "可用性控制面暂时不可读取",
                "code": "availability_control_unavailable",
                "error_type": type(error).__name__,
            },
        ) from error


@router.get("/admin/availability")
def get_admin_availability(
    history_limit: int = Query(default=288, ge=12, le=1000),
    principal: AuthPrincipal = Depends(require_admin),
):
    del principal
    try:
        return availability_service.admin_dashboard(history_limit=history_limit)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "高可用控制面读取失败",
                "code": "availability_dashboard_unavailable",
                "error_type": type(error).__name__,
            },
        ) from error


@router.post("/admin/availability/probes")
def create_admin_availability_probe(
    payload: AvailabilityProbeRequest,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        probe = availability_service.run_probe(
            trigger_type="manual",
            actor_id=principal.user_id,
            deep=payload.mode == "deep",
        )
        return {
            "probe": probe,
            "summary": availability_service.public_summary(),
        }
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "主动可用性探测失败",
                "code": "availability_probe_failed",
                "error_type": type(error).__name__,
                "reason": sanitize_log_value(error, limit=200),
            },
        ) from error


__all__ = ["router"]
