# -*- coding: utf-8 -*-
"""Authentication and administrator-only user management endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, SecretStr

from auth import (
    AuthError,
    AuthPrincipal,
    ROLE_ADMIN,
    ROLE_USER,
    SESSION_COOKIE_NAME,
    USER_ACTIVE,
    USER_DISABLED,
    auth_service,
    principal_from_request,
    request_client_identifier,
    require_admin,
)


router = APIRouter(prefix="/api", tags=["身份与权限"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["admin", "user"] = ROLE_USER
    temporary_password: SecretStr = Field(min_length=12, max_length=128)


class AdminUpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: Literal["admin", "user"] | None = None
    status: Literal["active", "disabled"] | None = None


class AdminResetPasswordRequest(BaseModel):
    temporary_password: SecretStr = Field(min_length=12, max_length=128)


def _client_hash(request: Request) -> str:
    return auth_service.client_hash(request_client_identifier(request, auth_service))


def _raise(error: AuthError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"message": str(error), "code": error.code},
    ) from error


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=auth_service.settings.session_hours * 3600,
        httponly=True,
        secure=auth_service.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=auth_service.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/auth/session")
def get_session(request: Request, response: Response):
    _no_store(response)
    if not auth_service.settings.required:
        return {
            "authenticated": True,
            "auth_required": False,
            "user": {
                "id": "legacy-admin",
                "username": "legacy-admin",
                "display_name": "本地开发管理员",
                "role": ROLE_ADMIN,
                "must_change_password": False,
            },
            "csrf_token": None,
            "readiness": auth_service.readiness(),
        }
    token = request.cookies.get(SESSION_COOKIE_NAME)
    principal = auth_service.authenticate(token)
    if principal is None:
        if token:
            _clear_session_cookie(response)
        return {
            "authenticated": False,
            "auth_required": True,
            "user": None,
            "csrf_token": None,
            "readiness": auth_service.readiness(),
        }
    csrf_token = auth_service.rotate_csrf(str(principal.session_id))
    return {
        "authenticated": True,
        "auth_required": True,
        "user": principal.public_dict(),
        "csrf_token": csrf_token,
        "readiness": auth_service.readiness(),
    }


@router.post("/auth/login")
def login(request: Request, response: Response, payload: LoginRequest):
    _no_store(response)
    try:
        result = auth_service.login(
            payload.username,
            payload.password.get_secret_value(),
            client_hash=_client_hash(request),
        )
    except AuthError as error:
        _raise(error)
    _set_session_cookie(response, result["token"])
    return {
        "authenticated": True,
        "user": result["user"],
        "csrf_token": result["csrf_token"],
        "expires_at": result["expires_at"],
    }


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _no_store(response)
    if principal.session_id:
        auth_service.logout(
            principal.session_id,
            actor_user_id=principal.user_id,
            client_hash=_client_hash(request),
        )
    _clear_session_cookie(response)
    return {"authenticated": False}


@router.post("/auth/change-password")
def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    principal: AuthPrincipal = Depends(principal_from_request),
):
    _no_store(response)
    try:
        auth_service.change_password(
            principal,
            payload.current_password.get_secret_value(),
            payload.new_password.get_secret_value(),
            client_hash=_client_hash(request),
        )
    except AuthError as error:
        _raise(error)
    _clear_session_cookie(response)
    return {
        "changed": True,
        "reauthentication_required": True,
        "message": "密码已更新，所有会话均已退出，请重新登录。",
    }


@router.get("/admin/overview")
def get_admin_overview(
    principal: AuthPrincipal = Depends(require_admin),
):
    del principal
    return auth_service.overview()


@router.get("/admin/users")
def get_admin_users(
    principal: AuthPrincipal = Depends(require_admin),
):
    del principal
    items = auth_service.list_users()
    return {"items": items, "count": len(items)}


@router.post("/admin/users", status_code=201)
def create_admin_user(
    request: Request,
    payload: AdminCreateUserRequest,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        user = auth_service.create_user(
            username=payload.username,
            password=payload.temporary_password.get_secret_value(),
            display_name=payload.display_name,
            role=payload.role,
            actor_user_id=principal.user_id,
            client_hash=_client_hash(request),
        )
    except AuthError as error:
        _raise(error)
    return {"user": user}


@router.patch("/admin/users/{user_id}")
def update_admin_user(
    user_id: str,
    request: Request,
    payload: AdminUpdateUserRequest,
    principal: AuthPrincipal = Depends(require_admin),
):
    if payload.display_name is None and payload.role is None and payload.status is None:
        raise HTTPException(status_code=400, detail="至少需要修改一个用户字段")
    try:
        user = auth_service.update_user(
            user_id,
            actor_user_id=principal.user_id,
            display_name=payload.display_name,
            role=payload.role,
            status=payload.status,
            client_hash=_client_hash(request),
        )
    except AuthError as error:
        _raise(error)
    return {"user": user}


@router.post("/admin/users/{user_id}/reset-password")
def reset_admin_user_password(
    user_id: str,
    request: Request,
    payload: AdminResetPasswordRequest,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        user = auth_service.reset_password(
            user_id,
            payload.temporary_password.get_secret_value(),
            actor_user_id=principal.user_id,
            client_hash=_client_hash(request),
        )
    except AuthError as error:
        _raise(error)
    return {"user": user, "sessions_revoked": True, "must_change_password": True}


@router.get("/admin/auth-audit")
def get_admin_auth_audit(
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_admin),
):
    del principal
    items = auth_service.list_audit(limit)
    return {
        "items": items,
        "count": len(items),
        "verification": auth_service.verify_audit(),
    }


__all__ = [
    "ROLE_ADMIN",
    "ROLE_USER",
    "USER_ACTIVE",
    "USER_DISABLED",
    "router",
]
