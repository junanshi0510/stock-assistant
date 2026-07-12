# -*- coding: utf-8 -*-
"""FastAPI application bootstrap for the investment assistant.

Route implementations live in ``routers/`` and domain calculations remain in
the existing service modules. Keeping this file small makes startup, CORS, and
background jobs easy to audit without mixing them with business endpoints.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import monitor
from agent.worker import start_worker
from routers import agent, funds, market, portfolio


app = FastAPI(title="金融投资助手 API", version="2.2")

_allowed_origins = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(funds.router)
app.include_router(portfolio.router)
app.include_router(agent.router)

# Each process owns one daemon monitor. It only evaluates user-confirmed watchlist data.
monitor.start_monitor(interval_seconds=3600)
start_worker()
