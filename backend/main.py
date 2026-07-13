# -*- coding: utf-8 -*-
"""FastAPI application bootstrap for the investment assistant.

Route implementations live in ``routers/`` and domain calculations remain in
the existing service modules. Keeping this file small makes startup, CORS, and
background jobs easy to audit without mixing them with business endpoints.
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import monitor
from auth import (
    AuthPrincipal,
    LEGACY_PRINCIPAL,
    SESSION_COOKIE_NAME,
    auth_service,
)
from agent.worker import start_worker
from routers import agent, auth, funds, market, portfolio


app = FastAPI(title="金融投资助手 API", version="2.2")

_allowed_origins = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if item.strip()
]

_AUTH_PUBLIC_ROUTES = {
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/login"),
}
_PASSWORD_CHANGE_ROUTES = {
    "/api/auth/session",
    "/api/auth/logout",
    "/api/auth/change-password",
}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _secure_response(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.middleware("http")
async def authentication_boundary(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    is_api = path.startswith("/api/")
    is_public = (method, path) in _AUTH_PUBLIC_ROUTES

    if is_api and not is_public:
        if not auth_service.settings.required:
            request.state.principal = LEGACY_PRINCIPAL
        else:
            readiness = auth_service.readiness()
            if not readiness["ready"]:
                return _secure_response(JSONResponse(
                    status_code=503,
                    content={
                        "detail": "认证系统尚未完成安全初始化",
                        "code": (
                            "auth_configuration_incomplete"
                            if not readiness["configured"]
                            else "auth_bootstrap_required"
                        ),
                    },
                ))
            principal = auth_service.authenticate(
                request.cookies.get(SESSION_COOKIE_NAME)
            )
            if not isinstance(principal, AuthPrincipal):
                return _secure_response(JSONResponse(
                    status_code=401,
                    content={"detail": "登录已失效，请重新登录", "code": "authentication_required"},
                ))
            request.state.principal = principal
            if principal.must_change_password and path not in _PASSWORD_CHANGE_ROUTES:
                return _secure_response(JSONResponse(
                    status_code=403,
                    content={"detail": "首次登录必须先修改临时密码", "code": "password_change_required"},
                ))
            if method in _UNSAFE_METHODS and not auth_service.verify_csrf(
                principal.session_id,
                request.headers.get("x-csrf-token"),
            ):
                return _secure_response(JSONResponse(
                    status_code=403,
                    content={"detail": "请求安全令牌无效，请刷新页面后重试", "code": "csrf_failed"},
                ))

    response = await call_next(request)
    return _secure_response(response)


# CORS must wrap the authentication boundary so browser preflight requests and
# early 401/403 responses receive the required cross-origin headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(market.router)
app.include_router(funds.router)
app.include_router(portfolio.router)
app.include_router(agent.router)

# Each process owns one daemon monitor. It only evaluates user-confirmed watchlist data.
monitor.start_monitor(interval_seconds=3600)
start_worker()
