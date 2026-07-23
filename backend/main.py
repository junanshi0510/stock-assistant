# -*- coding: utf-8 -*-
"""FastAPI application bootstrap for the investment assistant.

Route implementations live in ``routers/`` and domain calculations remain in
the existing service modules. Keeping this file small makes startup, CORS, and
background jobs easy to audit without mixing them with business endpoints.
"""

import os

from observability import configure_logging

configure_logging()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

import health
import monitor
from decision_check_worker import (
    start_worker as start_decision_check_worker,
    stop_worker as stop_decision_check_worker,
)
from auth import (
    AuthPrincipal,
    LEGACY_PRINCIPAL,
    SESSION_COOKIE_NAME,
    auth_service,
)
from agent.worker import start_worker
from routers import agent, auth, availability, funds, market, opportunities, portfolio
from task_queue import uses_celery_queue
from observability import observe_http_request
from runtime_identity import api_replica_identity


app = FastAPI(title="金融投资助手 API", version="3.0")

_allowed_origins = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if item.strip()
]

_AUTH_PUBLIC_ROUTES = {
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"),
}
_PASSWORD_CHANGE_ROUTES = {
    "/api/auth/session",
    "/api/auth/logout",
    "/api/auth/change-password",
}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _secure_response(response):
    identity = api_replica_identity()
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Stock-Assistant-Replica", identity["replica_id"])
    response.headers.setdefault("X-Stock-Assistant-Release", identity["release_id"])
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
app.middleware("http")(observe_http_request)


@app.get("/health/live", include_in_schema=False)
def liveness():
    return {"status": "alive", "api_replica": api_replica_identity()}


@app.get("/health/ready", include_in_schema=False)
def dependency_readiness():
    result = health.readiness()
    return JSONResponse(
        status_code=200 if result["ready"] else 503,
        content={**result, "api_replica": api_replica_identity()},
    )


@app.get("/health/edge", include_in_schema=False)
def edge_readiness():
    """Expose only load-balancer-safe readiness and release identity."""
    result = health.readiness()
    return JSONResponse(
        status_code=200 if result["ready"] else 503,
        content={
            "schema_version": "edge_readiness.v1",
            "ready": bool(result["ready"]),
            "status": "operational" if result["ready"] else "unavailable",
            "api_replica": api_replica_identity(),
        },
    )


@app.get("/health/full", include_in_schema=False)
def full_service_readiness():
    result = health.readiness()
    return JSONResponse(
        status_code=200 if result["full_service_ready"] else 503,
        content={**result, "api_replica": api_replica_identity()},
    )


app.mount("/internal/metrics", make_asgi_app())


app.include_router(auth.router)
app.include_router(availability.router)
app.include_router(market.router)
app.include_router(funds.router)
app.include_router(portfolio.router)
app.include_router(agent.router)
app.include_router(opportunities.router)


@app.on_event("startup")
def start_decision_check_scheduler():
    if not uses_celery_queue():
        start_decision_check_worker()


@app.on_event("shutdown")
def stop_decision_check_scheduler():
    if not uses_celery_queue():
        stop_decision_check_worker()

# SQLite development mode retains the existing in-process workers. PostgreSQL
# production mode is fail-closed and runs all long work in dedicated Celery services.
if not uses_celery_queue():
    monitor.start_monitor(interval_seconds=3600)
    start_worker()
