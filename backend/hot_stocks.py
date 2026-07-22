# -*- coding: utf-8 -*-
"""Professional multi-provider hot-stock rankings with explicit provenance.

The ranking router deliberately separates licensed/professional APIs from public
web endpoints:

* A-share / Hong Kong: Futu OpenD snapshot, then Tushare Pro completed EOD.
* US: Futu OpenD snapshot, Massive full-market EOD, then Alpha Vantage.
* Degraded fallback only: Eastmoney (A/HK) and Yahoo Finance (US).

Sina is never used. Provider failures are circuit-broken, secrets are redacted,
and callers receive one actionable failure per market instead of one failure per
ranking kind.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import datetime as dt
import importlib.util
import re
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

import config
import data_fetch


_TIMEOUT = 10
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_YAHOO_SCREENER_URL = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
_YAHOO_SPARK_URL = "https://query2.finance.yahoo.com/v7/finance/spark"
_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
_CACHE_TTL = {"1d": 60, "7d": 300, "30d": 600}
_POLICY_VERSION = "hot_stock_provider_router@2.0.0"
_MASSIVE_EOD_READY_HOUR_ET = 18

_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_name_cache: dict[str, tuple[float, dict[str, str]]] = {}
_name_cache_lock = threading.Lock()
_provider_runtime: dict[str, dict[str, Any]] = {}
_provider_runtime_lock = threading.Lock()
_provider_bundle_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_provider_bundle_cache_lock = threading.Lock()
_massive_day_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_massive_day_cache_lock = threading.Lock()
_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_probe_cache_lock = threading.Lock()
_futu_lock = threading.Lock()

_MARKET_FS = {
    "A股": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    "港股": "m:116,m:113,m:114,m:115",
    "美股": "m:105,m:106,m:107",
}
_PERIOD_DAYS = {"7d": 7, "30d": 30}
_VALID_TYPES = ("gainers", "losers", "active")
_PROVIDER_ROUTES = {
    "A股": ("futu_opend_a", "tushare_pro_a"),
    "港股": ("futu_opend_hk", "tushare_pro_hk"),
    "美股": ("futu_opend_us", "massive_eod_us", "alpha_vantage"),
}
_TUSHARE_PROVIDER = {"A股": "tushare_pro_a", "港股": "tushare_pro_hk"}
_FUTU_PROVIDER = {"A股": "futu_opend_a", "港股": "futu_opend_hk", "美股": "futu_opend_us"}
_PUBLIC_PROVIDER = {"A股": "eastmoney_public_a", "港股": "eastmoney_public_hk", "美股": "yahoo_public"}
_PROVIDER_LABELS = {
    "futu_opend_a": "富途 OpenAPI",
    "futu_opend_hk": "富途 OpenAPI",
    "futu_opend_us": "富途 OpenAPI",
    "tushare_pro_a": "Tushare Pro",
    "tushare_pro_hk": "Tushare Pro",
    "massive_eod_us": "Massive（原 Polygon.io）",
    "alpha_vantage": "Alpha Vantage",
    "eastmoney_public_a": "东方财富公开接口",
    "eastmoney_public_hk": "东方财富公开接口",
    "yahoo_public": "Yahoo Finance 公开接口",
}
_PROVIDER_MARKETS = {
    "futu_opend_a": "A股",
    "futu_opend_hk": "港股",
    "futu_opend_us": "美股",
    "tushare_pro_a": "A股",
    "tushare_pro_hk": "港股",
    "massive_eod_us": "美股",
    "alpha_vantage": "美股",
}
_REQUIRED_ENV = {
    "futu_opend_a": "FUTU_OPEND_HOST + FUTU_OPEND_MARKETS=A",
    "futu_opend_hk": "FUTU_OPEND_HOST + FUTU_OPEND_MARKETS=H",
    "futu_opend_us": "FUTU_OPEND_HOST + FUTU_OPEND_MARKETS=US",
    "tushare_pro_a": "TUSHARE_TOKEN",
    "tushare_pro_hk": "TUSHARE_TOKEN（含 hk_daily 权限）",
    "massive_eod_us": "MASSIVE_API_KEY（兼容 POLYGON_API_KEY）",
    "alpha_vantage": "ALPHAVANTAGE_API_KEY",
}
_DOC_URLS = {
    "futu_opend_a": "https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html",
    "futu_opend_hk": "https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html",
    "futu_opend_us": "https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html",
    "tushare_pro_a": "https://tushare.pro/document/1?doc_id=27",
    "tushare_pro_hk": "https://tushare.pro/document/2?doc_id=190",
    "massive_eod_us": "https://massive.com/docs/rest/stocks/aggregates/daily-market-summary",
    "alpha_vantage": "https://www.alphavantage.co/documentation/",
}
_EXPECTED_FRESHNESS = {
    "futu_opend_a": "realtime",
    "futu_opend_hk": "realtime",
    "futu_opend_us": "realtime",
    "tushare_pro_a": "latest_completed_eod",
    "tushare_pro_hk": "latest_completed_eod",
    "massive_eod_us": "latest_completed_eod",
    "alpha_vantage": "end_of_day",
}
_PROVIDER_CACHE_TTL = {
    "futu_opend_a": 30,
    "futu_opend_hk": 30,
    "futu_opend_us": 30,
    "tushare_pro_a": 3600,
    "tushare_pro_hk": 3600,
    "massive_eod_us": 3600,
    "alpha_vantage": 600,
}


class ProviderNotConfigured(RuntimeError):
    """A professional provider has no usable server-side credential/config."""


class HotStockProviderUnavailable(RuntimeError):
    """No provider could produce a real ranking for one market."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = copy.deepcopy(attempts)


def get_hot_stocks(
    market: str,
    period: str = "1d",
    type_: str = "gainers",
    limit: int = 20,
) -> dict[str, Any]:
    """Return one hot-stock ranking with provider provenance and freshness."""
    _validate_request(market, period, type_)
    limit = max(5, min(50, int(limit)))
    cache_key = (market, period, type_, limit)
    with _cache_lock:
        cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL[period]:
        return copy.deepcopy(cached[1])

    try:
        period_attempt = None
        full_market_multiday = False
        if market == "美股" and period != "1d":
            period_attempt = _attempt_provider(
                "massive_eod_us",
                "professional",
                lambda: _massive_multiday_bundle(_PERIOD_DAYS[period], type_, limit),
            )
            if period_attempt["value"] is not None:
                bundle = _complete_bundle(
                    period_attempt["value"], [period_attempt["attempt"]], degraded=False
                )
                items = bundle["rankings"][type_][:limit]
                scope = bundle["scope"]
                methodology = bundle["methodology"]
                full_market_multiday = True

        if not full_market_multiday:
            candidate_limit = limit if period == "1d" else max(20, min(50, limit * 3))
            bundle = get_hot_stock_bundle(
                market,
                [type_] if period == "1d" else ["active"],
                candidate_limit,
            )
        if period == "1d":
            items = bundle["rankings"][type_][:limit]
            scope = bundle["scope"]
            methodology = bundle["methodology"]
        elif not full_market_multiday:
            candidates = bundle["rankings"]["active"]
            items = _enrich_multiday(market, candidates, type_, _PERIOD_DAYS[period], limit)
            scope = "成交活跃候选池内"
            methodology = (
                f"{bundle['source']} 成交活跃候选池内，按真实日 K 计算区间涨跌；"
                "不是交易所全量多日排名"
            )
            if market == "美股" and period_attempt is not None:
                bundle = dict(bundle)
                bundle["provider_attempts"] = [period_attempt["attempt"]] + list(
                    bundle.get("provider_attempts") or []
                )
                bundle["degraded"] = True
                bundle["provider_tier"] = "mixed_fallback"
                bundle["warning"] = (
                    "Massive 全市场多日榜未生效，当前只在成交活跃候选池内计算区间收益；"
                    "该结果不是全市场排名。"
                )
    except Exception as error:
        if cached:
            stale = copy.deepcopy(cached[1])
            stale["stale"] = True
            stale["degraded"] = True
            stale["warning"] = "当前行情源不可用，展示最近一次成功缓存；请勿按陈旧榜单直接交易。"
            if isinstance(error, HotStockProviderUnavailable):
                stale["provider_attempts"] = copy.deepcopy(error.attempts)
            return stale
        raise

    result = {
        "market": market,
        "period": period,
        "type": type_,
        "scope": scope,
        "source": bundle["source"],
        "provider": bundle["provider"],
        "provider_tier": bundle["provider_tier"],
        "data_freshness": bundle["data_freshness"],
        "as_of": bundle.get("as_of"),
        "retrieved_at": bundle["retrieved_at"],
        "served_at": bundle.get("served_at"),
        "source_cache_hit": bool(bundle.get("source_cache_hit")),
        "stale": False,
        "degraded": bundle["degraded"],
        "warning": bundle.get("warning"),
        "provider_attempts": copy.deepcopy(bundle["provider_attempts"]),
        "provider_policy_version": _POLICY_VERSION,
        "data_quality": copy.deepcopy(bundle.get("data_quality") or {}),
        "full_market_multiday": bool(period != "1d" and full_market_multiday),
        "methodology": methodology,
        "items": items,
        "count": len(items),
    }
    with _cache_lock:
        _cache[cache_key] = (time.time(), copy.deepcopy(result))
    return result


def get_hot_stock_bundle(
    market: str,
    types: list[str] | tuple[str, ...],
    limit: int = 20,
    *,
    allow_public_fallback: bool | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch all requested ranking kinds through one market-level provider route."""
    if market not in _MARKET_FS:
        raise ValueError(f"不支持的市场：{market}")
    normalized_types = tuple(dict.fromkeys(str(item) for item in types))
    if not normalized_types or any(item not in _VALID_TYPES for item in normalized_types):
        raise ValueError("榜单类型必须是 gainers、losers 或 active")
    limit = max(5, min(50, int(limit)))
    attempts: list[dict[str, Any]] = []

    for professional_id in _PROVIDER_ROUTES[market]:
        professional = _attempt_provider(
            professional_id,
            "professional",
            lambda provider_id=professional_id: _professional_bundle(
                provider_id,
                market,
                normalized_types,
                limit,
                force_refresh=force_refresh,
            ),
        )
        attempts.append(professional["attempt"])
        if professional["value"] is not None:
            return _complete_bundle(professional["value"], attempts, degraded=False)

    public_enabled = (
        bool(config.HOT_STOCK_PUBLIC_FALLBACK_ENABLED)
        if allow_public_fallback is None
        else bool(allow_public_fallback)
    )
    if public_enabled:
        public_id = _PUBLIC_PROVIDER[market]
        public = _attempt_provider(
            public_id,
            "public_fallback",
            lambda: _public_bundle(market, normalized_types, limit),
        )
        attempts.append(public["attempt"])
        if public["value"] is not None:
            professional_reason = "；".join(
                str(attempt.get("message") or attempt.get("status") or "专业源不可用")
                for attempt in attempts
                if attempt.get("tier") == "professional"
            )
            warning = (
                f"{market}专业源未生效（{professional_reason}），当前使用{_PROVIDER_LABELS[public_id]}"
                "临时降级数据；覆盖范围、稳定性和时效性不作生产保证。"
            )
            value = dict(public["value"])
            value["warning"] = warning
            return _complete_bundle(value, attempts, degraded=True)

    raise HotStockProviderUnavailable(
        _unavailable_message(market, attempts, public_enabled=public_enabled), attempts
    )


def get_provider_status() -> dict[str, Any]:
    """Expose configuration/runtime health without probing or returning secrets."""
    generated_at = _now_iso()
    markets = []
    for market in _MARKET_FS:
        public_id = _PUBLIC_PROVIDER[market]
        providers = [_provider_status(provider_id) for provider_id in _PROVIDER_ROUTES[market]]
        configured_providers = [item for item in providers if item["configured"]]
        selected = next((item for item in providers if item["state"] == "ready"), None)
        if selected is None and configured_providers:
            selected = configured_providers[0]
        if selected is None:
            selected = providers[0]
        if any(item["state"] == "ready" for item in providers):
            state = "ready"
        elif any(item["state"] == "configured_unverified" for item in providers):
            state = "configured_unverified"
        elif configured_providers and all(item["state"] == "circuit_open" for item in configured_providers):
            state = "circuit_open"
        elif any(item["state"] == "configuration_invalid" for item in providers):
            state = "configuration_invalid"
        else:
            state = "configuration_required"
        markets.append(
            {
                "market": market,
                "provider": selected["provider"],
                "provider_label": selected["provider_label"],
                "provider_tier": "professional",
                "required_env": selected["required_env"],
                "configured": bool(configured_providers),
                "configuration_message": (
                    f"已配置 {len(configured_providers)}/{len(providers)} 个专业源"
                    if configured_providers else "未配置"
                ),
                "state": state,
                "expected_freshness": selected["expected_freshness"],
                "documentation_url": selected["documentation_url"],
                "runtime": selected["runtime"],
                "available_provider_count": len(configured_providers),
                "provider_count": len(providers),
                "providers": providers,
                "public_fallback": {
                    "enabled": bool(config.HOT_STOCK_PUBLIC_FALLBACK_ENABLED),
                    "provider": public_id,
                    "provider_label": _PROVIDER_LABELS[public_id],
                    "runtime": _runtime_snapshot(public_id),
                },
            }
        )
    return {
        "policy_version": _POLICY_VERSION,
        "generated_at": generated_at,
        "active_probe": False,
        "secrets_exposed": False,
        "sina_fallback": False,
        "markets": markets,
    }


def probe_provider(market: str) -> dict[str, Any]:
    """Actively verify one market's professional route without using public fallbacks."""
    if market not in _MARKET_FS:
        raise ValueError(f"不支持的市场：{market}")
    with _probe_cache_lock:
        cached = _probe_cache.get(market)
    if cached and time.time() - cached[0] < 30:
        result = copy.deepcopy(cached[1])
        result["probe_cache_hit"] = True
        return result
    started = time.perf_counter()
    try:
        bundle = get_hot_stock_bundle(
            market,
            _VALID_TYPES,
            10,
            allow_public_fallback=False,
            force_refresh=True,
        )
    except HotStockProviderUnavailable as error:
        result = {
            "market": market,
            "available": False,
            "professional": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "message": _safe_error(error),
            "attempts": copy.deepcopy(error.attempts),
            "probed_at": _now_iso(),
        }
        with _probe_cache_lock:
            _probe_cache[market] = (time.time(), copy.deepcopy(result))
        return result
    result = {
        "market": market,
        "available": True,
        "professional": bundle.get("provider_tier") == "professional",
        "provider": bundle.get("provider"),
        "provider_label": _PROVIDER_LABELS.get(str(bundle.get("provider")), bundle.get("source")),
        "data_freshness": bundle.get("data_freshness"),
        "as_of": bundle.get("as_of"),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "data_quality": copy.deepcopy(bundle.get("data_quality") or {}),
        "counts": {
            kind: len((bundle.get("rankings") or {}).get(kind) or []) for kind in _VALID_TYPES
        },
        "attempts": copy.deepcopy(bundle.get("provider_attempts") or []),
        "probed_at": _now_iso(),
    }
    with _probe_cache_lock:
        _probe_cache[market] = (time.time(), copy.deepcopy(result))
    return result


def _validate_request(market: str, period: str, type_: str) -> None:
    if market not in _MARKET_FS:
        raise ValueError(f"不支持的市场：{market}")
    if type_ not in _VALID_TYPES:
        raise ValueError(f"不支持的榜单类型：{type_}")
    if period not in ("1d", "7d", "30d"):
        raise ValueError(f"不支持的周期：{period}")


def _provider_configuration(provider_id: str) -> tuple[bool, str]:
    if provider_id.startswith("futu_opend_"):
        if not str(config.FUTU_OPEND_HOST).strip():
            return False, "未配置 FUTU_OPEND_HOST"
        market = _PROVIDER_MARKETS[provider_id]
        if market not in _configured_futu_markets():
            return False, f"{market}未在 FUTU_OPEND_MARKETS 启用"
        if importlib.util.find_spec("futu") is None:
            return False, "缺少 futu-api Python SDK"
        return True, "已配置"
    if provider_id.startswith("tushare_pro_"):
        return (True, "已配置") if str(config.TUSHARE_TOKEN).strip() else (False, "未配置 TUSHARE_TOKEN")
    if provider_id == "massive_eod_us":
        return (True, "已配置") if _massive_api_key() else (False, "未配置 MASSIVE_API_KEY 或 POLYGON_API_KEY")
    if provider_id == "alpha_vantage":
        if not str(config.ALPHAVANTAGE_API_KEY).strip():
            return False, "未配置 ALPHAVANTAGE_API_KEY"
        entitlement = str(config.ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT).strip().lower()
        if entitlement and entitlement not in {"delayed", "realtime"}:
            return False, "ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT 只能留空、delayed 或 realtime"
        return True, "已配置"
    return False, "未知供应商"


def _provider_status(provider_id: str) -> dict[str, Any]:
    configured, config_message = _provider_configuration(provider_id)
    runtime = _runtime_snapshot(provider_id)
    remaining = _circuit_remaining(provider_id)
    if not configured:
        state = (
            "configuration_invalid"
            if config_message.startswith("缺少") or config_message.startswith("未知")
            else "configuration_required"
        )
    elif remaining > 0:
        state = "circuit_open"
    elif runtime.get("last_success_at") and (
        not runtime.get("last_failure_at")
        or runtime["last_success_at"] >= runtime["last_failure_at"]
    ):
        state = "ready"
    else:
        state = "configured_unverified"
    freshness = _EXPECTED_FRESHNESS[provider_id]
    if provider_id == "alpha_vantage":
        entitlement = str(config.ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT).strip().lower()
        freshness = entitlement if entitlement in {"delayed", "realtime"} else "end_of_day"
    return {
        "provider": provider_id,
        "provider_label": _PROVIDER_LABELS[provider_id],
        "configured": configured,
        "configuration_message": config_message,
        "state": state,
        "required_env": _REQUIRED_ENV[provider_id],
        "expected_freshness": freshness,
        "documentation_url": _DOC_URLS[provider_id],
        "runtime": runtime,
    }


def _professional_bundle(
    provider_id: str,
    market: str,
    types: tuple[str, ...],
    limit: int,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    configured, message = _provider_configuration(provider_id)
    if not configured:
        raise ProviderNotConfigured(message)
    cache_key = (provider_id, market, types, limit)
    ttl = _PROVIDER_CACHE_TTL[provider_id]
    if not force_refresh:
        with _provider_bundle_cache_lock:
            cached = _provider_bundle_cache.get(cache_key)
        if cached and time.time() - cached[0] < ttl:
            value = copy.deepcopy(cached[1])
            value["source_cache_hit"] = True
            return value

    if provider_id.startswith("futu_opend_"):
        value = _futu_bundle(market, types, limit)
    elif provider_id.startswith("tushare_pro_"):
        value = _tushare_bundle(market, types, limit)
    elif provider_id == "massive_eod_us":
        value = _massive_bundle(types, limit, force_refresh=force_refresh)
    elif provider_id == "alpha_vantage":
        value = _alpha_vantage_bundle(types, limit)
    else:  # pragma: no cover - registry and dispatcher are defined together.
        raise RuntimeError(f"未知专业行情源：{provider_id}")
    value = dict(value)
    value["source_cache_hit"] = False
    value.setdefault("retrieved_at", _now_iso())
    with _provider_bundle_cache_lock:
        _provider_bundle_cache[cache_key] = (time.time(), copy.deepcopy(value))
    return value


def _public_bundle(market: str, types: tuple[str, ...], limit: int) -> dict[str, Any]:
    rankings: dict[str, list[dict[str, Any]]] = {}
    if market == "美股":
        for kind in types:
            rankings[kind] = _yahoo_us_1d(kind, limit)
        return {
            "source": "Yahoo Finance",
            "provider": "yahoo_public",
            "provider_tier": "public_fallback",
            "data_freshness": "intraday_best_effort",
            "as_of": None,
            "scope": "Yahoo 预定义美股榜单",
            "methodology": "Yahoo Finance 公开网页预定义筛选器（非 SLA 数据源）",
            "rankings": rankings,
        }
    field_order = {"gainers": ("f3", 1), "losers": ("f3", 0), "active": ("f6", 1)}
    for kind in types:
        field, direction = field_order[kind]
        rankings[kind] = _clist(market, field, direction, limit)
    return {
        "source": "东方财富",
        "provider": _PUBLIC_PROVIDER[market],
        "provider_tier": "public_fallback",
        "data_freshness": "intraday_best_effort",
        "as_of": None,
        "scope": "全市场公开榜单",
        "methodology": "东方财富公开网页榜单服务端排序（非 SLA 数据源）",
        "rankings": rankings,
    }


def _configured_futu_markets() -> set[str]:
    aliases = {
        "A": "A股", "CN": "A股", "A股": "A股",
        "H": "港股", "HK": "港股", "港股": "港股",
        "US": "美股", "USA": "美股", "美股": "美股",
    }
    raw = re.split(r"[,，;；\s]+", str(config.FUTU_OPEND_MARKETS or ""))
    return {aliases[item.strip().upper()] for item in raw if item.strip().upper() in aliases}


def _massive_api_key() -> str:
    return str(config.MASSIVE_API_KEY or config.POLYGON_API_KEY or "").strip()


def _massive_api_base() -> str:
    if str(config.MASSIVE_API_KEY or "").strip():
        return str(config.MASSIVE_API_BASE_URL or "https://api.massive.com").rstrip("/")
    return "https://api.polygon.io"


def _massive_candidate_dates(now: dt.datetime | None = None) -> list[str]:
    eastern = ZoneInfo("America/New_York")
    current = now or dt.datetime.now(eastern)
    current = current.replace(tzinfo=eastern) if current.tzinfo is None else current.astimezone(eastern)
    cursor = current.date()
    # Grouped daily can be partial while the US session is still open. Use a
    # conservative post-market cutoff, but do not force an unnecessary extra
    # trading-day lag once the complete EOD file should be available.
    if cursor.weekday() < 5 and current.hour < _MASSIVE_EOD_READY_HOUR_ET:
        cursor -= dt.timedelta(days=1)
    dates = []
    while len(dates) < 5:
        if cursor.weekday() < 5:
            dates.append(cursor.isoformat())
        cursor -= dt.timedelta(days=1)
    return dates


def _massive_grouped_day(trade_date: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    if not force_refresh:
        with _massive_day_cache_lock:
            cached = _massive_day_cache.get(trade_date)
        if cached and time.time() - cached[0] < 21600:
            return copy.deepcopy(cached[1])
    response = requests.get(
        f"{_massive_api_base()}/v2/aggs/grouped/locale/us/market/stocks/{trade_date}",
        params={"adjusted": "true", "include_otc": "false", "apiKey": _massive_api_key()},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status") or "").upper() in {"ERROR", "NOT_AUTHORIZED"}:
        raise RuntimeError(payload.get("error") or payload.get("message") or "Massive 请求失败")
    rows = [dict(row) for row in (payload.get("results") or []) if isinstance(row, dict)]
    if rows:
        with _massive_day_cache_lock:
            _massive_day_cache[trade_date] = (time.time(), copy.deepcopy(rows))
    return rows


def _massive_bundle(
    types: tuple[str, ...], limit: int, *, force_refresh: bool = False
) -> dict[str, Any]:
    snapshots: list[tuple[str, list[dict[str, Any]]]] = []
    for trade_date in _massive_candidate_dates():
        rows = _massive_grouped_day(trade_date, force_refresh=force_refresh)
        if rows:
            snapshots.append((trade_date, rows))
        if len(snapshots) == 2:
            break
    if len(snapshots) < 2:
        raise RuntimeError("Massive 最近交易日全市场日线不足两期")

    current_date, current_rows = snapshots[0]
    previous_date, previous_rows = snapshots[1]
    previous_close = {
        str(row.get("T") or "").upper(): _num(row.get("c")) for row in previous_rows
    }
    normalized = []
    matched = 0
    excluded = 0
    for row in current_rows:
        symbol = str(row.get("T") or "").upper().strip()
        price = _num(row.get("c"))
        prior = previous_close.get(symbol)
        volume = _num(row.get("v")) or 0.0
        if not symbol or not price or price <= 0 or not prior or prior <= 0:
            excluded += 1
            continue
        matched += 1
        if price < float(config.HOT_STOCK_US_MIN_PRICE) or volume < int(config.HOT_STOCK_US_MIN_VOLUME):
            excluded += 1
            continue
        vwap = _num(row.get("vw")) or price
        normalized.append(
            {
                "symbol": symbol,
                "name": symbol,
                "price": price,
                "change_pct": round((price / prior - 1) * 100, 4),
                "volume": volume,
                "turnover": round(volume * vwap, 2),
                "trade_count": _num(row.get("n")),
                "secid": symbol,
            }
        )
    if not normalized:
        raise RuntimeError("Massive 全市场日线通过流动性与前收盘校验的股票为零")
    coverage = matched / len(current_rows) if current_rows else 0.0
    quality_status = "pass" if coverage >= 0.8 and len(normalized) >= max(50, limit) else "warning"
    return {
        "source": "Massive（原 Polygon.io）",
        "provider": "massive_eod_us",
        "provider_tier": "professional",
        "data_freshness": "latest_completed_eod",
        "as_of": current_date,
        "scope": "全美股·最近完整交易日·流动性过滤",
        "methodology": (
            "Massive 两期全市场日线本地计算涨跌幅，活跃榜按估算成交额排序；"
            "过滤 OTC、极低价和极低成交标的"
        ),
        "data_quality": {
            "status": quality_status,
            "rows_received": len(current_rows),
            "prior_rows_received": len(previous_rows),
            "prior_close_matched": matched,
            "prior_close_coverage": round(coverage, 4),
            "eligible_rows": len(normalized),
            "excluded_rows": excluded,
            "baseline_as_of": previous_date,
            "filters": {
                "include_otc": False,
                "min_price": float(config.HOT_STOCK_US_MIN_PRICE),
                "min_volume": int(config.HOT_STOCK_US_MIN_VOLUME),
            },
        },
        "rankings": _rank_normalized_rows(normalized, types, limit),
    }


def _massive_session_dates(end_date: str, days: int) -> list[str]:
    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=max(45, days * 3))
    response = requests.get(
        f"{_massive_api_base()}/v2/aggs/ticker/SPY/range/1/day/{start.isoformat()}/{end.isoformat()}",
        params={"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": _massive_api_key()},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("status") or "").upper() in {"ERROR", "NOT_AUTHORIZED"}:
        raise RuntimeError(payload.get("error") or payload.get("message") or "Massive 交易日历请求失败")
    dates = []
    for row in payload.get("results") or []:
        timestamp = _num(row.get("t"))
        if timestamp is None:
            continue
        value = dt.datetime.fromtimestamp(timestamp / 1000, tz=dt.timezone.utc).date().isoformat()
        if value <= end_date:
            dates.append(value)
    dates = sorted(set(dates))
    if len(dates) < days + 1:
        raise RuntimeError(f"Massive SPY 交易日序列不足 {days + 1} 期")
    return dates


def _massive_multiday_bundle(days: int, type_: str, limit: int) -> dict[str, Any]:
    configured, message = _provider_configuration("massive_eod_us")
    if not configured:
        raise ProviderNotConfigured(message)
    current_date = ""
    current_rows: list[dict[str, Any]] = []
    for trade_date in _massive_candidate_dates():
        current_rows = _massive_grouped_day(trade_date)
        if current_rows:
            current_date = trade_date
            break
    if not current_rows:
        raise RuntimeError("Massive 最近完整交易日未返回全市场日线")
    sessions = _massive_session_dates(current_date, days)
    eligible_sessions = [value for value in sessions if value <= current_date]
    if not eligible_sessions or eligible_sessions[-1] != current_date:
        raise RuntimeError("Massive 全市场日线与交易日序列截止日不一致")
    baseline_date = eligible_sessions[-(days + 1)]
    baseline_rows = _massive_grouped_day(baseline_date)
    baseline_close = {
        str(row.get("T") or "").upper(): _num(row.get("c")) for row in baseline_rows
    }
    normalized = []
    matched = 0
    excluded = 0
    for row in current_rows:
        symbol = str(row.get("T") or "").upper().strip()
        price = _num(row.get("c"))
        baseline = baseline_close.get(symbol)
        volume = _num(row.get("v")) or 0.0
        if not symbol or not price or price <= 0 or not baseline or baseline <= 0:
            excluded += 1
            continue
        matched += 1
        if price < float(config.HOT_STOCK_US_MIN_PRICE) or volume < int(config.HOT_STOCK_US_MIN_VOLUME):
            excluded += 1
            continue
        vwap = _num(row.get("vw")) or price
        normalized.append(
            {
                "symbol": symbol,
                "name": symbol,
                "price": price,
                "change_pct": round((price / baseline - 1) * 100, 4),
                "volume": volume,
                "turnover": round(volume * vwap, 2),
                "trade_count": _num(row.get("n")),
                "secid": symbol,
            }
        )
    if not normalized:
        raise RuntimeError("Massive 多日全市场榜没有通过质量门槛的股票")
    coverage = matched / len(current_rows) if current_rows else 0.0
    rankings = _rank_normalized_rows(normalized, (type_,), limit)
    return {
        "source": "Massive（原 Polygon.io）",
        "provider": "massive_eod_us",
        "provider_tier": "professional",
        "data_freshness": "latest_completed_eod",
        "as_of": current_date,
        "scope": f"全美股·{days} 个真实交易日·流动性过滤",
        "methodology": (
            f"Massive 以 SPY 真实交易日序列定位 {days} 个交易日前基准日，"
            "用两份全市场日线计算区间收益；活跃榜按截止日估算成交额排序"
        ),
        "data_quality": {
            "status": "pass" if coverage >= 0.8 and len(normalized) >= max(50, limit) else "warning",
            "rows_received": len(current_rows),
            "baseline_rows_received": len(baseline_rows),
            "baseline_matched": matched,
            "baseline_coverage": round(coverage, 4),
            "eligible_rows": len(normalized),
            "excluded_rows": excluded,
            "baseline_as_of": baseline_date,
            "trading_sessions": days,
            "filters": {
                "include_otc": False,
                "min_price": float(config.HOT_STOCK_US_MIN_PRICE),
                "min_volume": int(config.HOT_STOCK_US_MIN_VOLUME),
            },
        },
        "rankings": rankings,
        "source_cache_hit": False,
        "retrieved_at": _now_iso(),
    }


def _futu_market_enums(futu: Any, market: str) -> list[Any]:
    if market == "A股":
        return [futu.Market.SH, futu.Market.SZ]
    if market == "港股":
        return [futu.Market.HK]
    return [futu.Market.US]


def _futu_symbol(market: str, code: Any) -> str:
    raw = str(code or "").strip().upper()
    value = raw.split(".", 1)[-1] if "." in raw else raw
    if market == "A股":
        digits = "".join(character for character in value if character.isdigit())
        return digits.zfill(6)[-6:] if digits else ""
    if market == "港股":
        digits = "".join(character for character in value if character.isdigit())
        return digits.zfill(5)[-5:] if digits else ""
    return value


def _futu_bundle(market: str, types: tuple[str, ...], limit: int) -> dict[str, Any]:
    import futu

    with _futu_lock:
        quote_ctx = futu.OpenQuoteContext(
            host=str(config.FUTU_OPEND_HOST).strip(),
            port=int(config.FUTU_OPEND_PORT),
        )
        try:
            securities: dict[str, str] = {}
            for market_enum in _futu_market_enums(futu, market):
                ret, frame = quote_ctx.get_stock_basicinfo(market_enum, futu.SecurityType.STOCK)
                if ret != futu.RET_OK:
                    raise RuntimeError(f"FutuOpenD 股票列表失败：{frame}")
                for row in _records(frame):
                    code = str(row.get("code") or "").strip().upper()
                    if code:
                        securities[code] = str(row.get("name") or code)
            if not securities:
                raise RuntimeError("FutuOpenD 未返回股票列表")

            snapshots = []
            codes = list(securities)
            batch_size = int(config.FUTU_SNAPSHOT_BATCH_SIZE)
            for offset in range(0, len(codes), batch_size):
                batch = codes[offset:offset + batch_size]
                ret, frame = quote_ctx.get_market_snapshot(batch)
                if ret != futu.RET_OK:
                    message = str(frame)
                    if market == "港股" and "BMP" in message.upper():
                        raise RuntimeError("Futu 港股 BMP 权限不支持全市场快照，请开通 LV1/LV2 行情")
                    raise RuntimeError(f"FutuOpenD 市场快照失败：{message}")
                snapshots.extend(_records(frame))
        finally:
            quote_ctx.close()

    normalized = []
    update_times = []
    excluded = 0
    for row in snapshots:
        suspended = str(row.get("suspension") or "").strip().lower() in {"true", "1"}
        price = _num(row.get("last_price"))
        previous = _num(row.get("prev_close_price"))
        volume = _num(row.get("volume")) or 0.0
        if suspended or not price or price <= 0 or not previous or previous <= 0:
            excluded += 1
            continue
        if market == "美股" and (
            price < float(config.HOT_STOCK_US_MIN_PRICE)
            or volume < int(config.HOT_STOCK_US_MIN_VOLUME)
        ):
            excluded += 1
            continue
        raw_code = str(row.get("code") or "").strip().upper()
        symbol = _futu_symbol(market, raw_code)
        if not symbol:
            excluded += 1
            continue
        update_time = str(row.get("update_time") or "").strip()
        if update_time:
            update_times.append(update_time)
        normalized.append(
            {
                "symbol": symbol,
                "name": str(row.get("name") or securities.get(raw_code) or symbol),
                "price": price,
                "change_pct": round((price / previous - 1) * 100, 4),
                "volume": volume,
                "turnover": _num(row.get("turnover")) or round(price * volume, 2),
                "turnover_rate": _num(row.get("turnover_rate")),
                "secid": raw_code,
            }
        )
    if not normalized:
        raise RuntimeError("FutuOpenD 快照通过价格、昨收和停牌校验的股票为零")
    coverage = len(normalized) / len(snapshots) if snapshots else 0.0
    return {
        "source": "富途 OpenAPI",
        "provider": _FUTU_PROVIDER[market],
        "provider_tier": "professional",
        "data_freshness": "realtime",
        "as_of": max(update_times) if update_times else None,
        "scope": "全市场股票快照·流动性与有效价格校验",
        "methodology": "FutuOpenD 全市场股票列表分批快照，本地统一计算三榜",
        "data_quality": {
            "status": "pass" if coverage >= 0.8 and len(normalized) >= limit else "warning",
            "rows_received": len(snapshots),
            "eligible_rows": len(normalized),
            "excluded_rows": excluded,
            "eligible_coverage": round(coverage, 4),
            "snapshot_batch_size": int(config.FUTU_SNAPSHOT_BATCH_SIZE),
        },
        "rankings": _rank_normalized_rows(normalized, types, limit),
    }


def _tushare_client() -> Any:
    import tushare as ts

    return ts.pro_api(str(config.TUSHARE_TOKEN).strip())


def _tushare_bundle(market: str, types: tuple[str, ...], limit: int) -> dict[str, Any]:
    pro = _tushare_client()
    rows: list[dict[str, Any]] = []
    as_of = ""
    errors: list[str] = []
    for trade_date in _tushare_trade_dates(pro, market):
        try:
            frame = pro.daily(trade_date=trade_date) if market == "A股" else pro.hk_daily(trade_date=trade_date)
            rows = _records(frame)
        except Exception as error:
            errors.append(_safe_error(error))
            continue
        if rows:
            as_of = trade_date
            break
    if not rows:
        suffix = f"：{errors[-1]}" if errors else ""
        raise RuntimeError(f"最近完整交易日未返回行情{suffix}")

    names = _tushare_names(pro, market)
    normalized = []
    for row in rows:
        item = _tushare_item(market, row, names)
        if item is not None:
            normalized.append(item)
    if not normalized:
        raise RuntimeError("Tushare Pro 行情字段为空或格式不兼容")

    rankings = _rank_normalized_rows(normalized, types, limit)
    formatted_as_of = f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:8]}" if len(as_of) == 8 else as_of
    return {
        "source": "Tushare Pro",
        "provider": _TUSHARE_PROVIDER[market],
        "provider_tier": "professional",
        "data_freshness": "latest_completed_eod",
        "as_of": formatted_as_of,
        "scope": "全市场·最近完整交易日",
        "methodology": "Tushare Pro 全市场日线快照本地统一排序",
        "data_quality": {
            "status": "pass" if len(normalized) >= max(50, limit) else "warning",
            "rows_received": len(rows),
            "eligible_rows": len(normalized),
            "excluded_rows": len(rows) - len(normalized),
            "name_coverage": round(
                sum(1 for item in normalized if item.get("name") != item.get("symbol")) / len(normalized),
                4,
            ),
        },
        "rankings": rankings,
    }


def _tushare_trade_dates(pro: Any, market: str) -> list[str]:
    today = dt.datetime.now().astimezone().date()
    start = today - dt.timedelta(days=16)
    kwargs = {"start_date": start.strftime("%Y%m%d"), "end_date": today.strftime("%Y%m%d"), "is_open": "1"}
    try:
        calendar = pro.trade_cal(exchange="", **kwargs) if market == "A股" else pro.hk_tradecal(**kwargs)
        dates = [str(row.get("cal_date") or row.get("trade_date") or "") for row in _records(calendar)]
        dates = sorted({value for value in dates if len(value) == 8}, reverse=True)
        if dates:
            return dates[:6]
    except Exception:
        pass
    fallback = []
    cursor = today
    while len(fallback) < 6:
        if cursor.weekday() < 5:
            fallback.append(cursor.strftime("%Y%m%d"))
        cursor -= dt.timedelta(days=1)
    return fallback


def _tushare_names(pro: Any, market: str) -> dict[str, str]:
    with _name_cache_lock:
        cached = _name_cache.get(market)
    if cached and time.time() - cached[0] < 21600:
        return dict(cached[1])
    try:
        if market == "A股":
            frame = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
        else:
            frame = pro.hk_basic(list_status="L", fields="ts_code,symbol,name")
        result: dict[str, str] = {}
        for row in _records(frame):
            code = _normalize_tushare_symbol(market, row.get("symbol") or row.get("ts_code"))
            if code:
                result[code] = str(row.get("name") or code)
    except Exception:
        result = {}
    with _name_cache_lock:
        _name_cache[market] = (time.time(), dict(result))
    return result


def _tushare_item(market: str, row: dict[str, Any], names: dict[str, str]) -> dict[str, Any] | None:
    ts_code = str(row.get("ts_code") or row.get("symbol") or "")
    symbol = _normalize_tushare_symbol(market, ts_code)
    price = _num(row.get("close"))
    if not symbol or price is None:
        return None
    change_pct = _num(row.get("pct_chg"))
    pre_close = _num(row.get("pre_close"))
    if change_pct is None and pre_close:
        change_pct = round((price / pre_close - 1) * 100, 4)
    volume = _num(row.get("vol") if row.get("vol") is not None else row.get("volume"))
    amount = _num(row.get("amount") if row.get("amount") is not None else row.get("turnover"))
    if market == "A股":
        secid = f"{1 if ts_code.upper().endswith('.SH') else 0}.{symbol}"
    else:
        secid = f"116.{symbol}"
    return {
        "symbol": symbol,
        "name": names.get(symbol, symbol),
        "price": price,
        "change_pct": change_pct,
        "volume": volume,
        "turnover": amount,
        "secid": secid,
    }


def _alpha_vantage_bundle(types: tuple[str, ...], limit: int) -> dict[str, Any]:
    params = {
        "function": "TOP_GAINERS_LOSERS",
        "apikey": str(config.ALPHAVANTAGE_API_KEY).strip(),
    }
    entitlement = str(config.ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT).strip().lower()
    if entitlement:
        params["entitlement"] = entitlement
    response = requests.get(_ALPHA_VANTAGE_URL, params=params, timeout=_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    error_message = payload.get("Error Message") or payload.get("Information") or payload.get("Note")
    if error_message:
        raise RuntimeError(str(error_message))
    source_keys = {
        "gainers": "top_gainers",
        "losers": "top_losers",
        "active": "most_actively_traded",
    }
    rankings = {}
    for kind in types:
        raw_items = payload.get(source_keys[kind]) or []
        rankings[kind] = [_alpha_item(item) for item in raw_items[:limit] if item.get("ticker")]
        if not rankings[kind]:
            raise RuntimeError(f"Alpha Vantage 未返回 {kind} 榜单")
    freshness = entitlement if entitlement in {"delayed", "realtime"} else "end_of_day"
    scope = "美股市场 Top 20 官方榜单"
    return {
        "source": "Alpha Vantage",
        "provider": "alpha_vantage",
        "provider_tier": "professional",
        "data_freshness": freshness,
        "as_of": payload.get("last_updated"),
        "scope": scope,
        "methodology": "Alpha Vantage TOP_GAINERS_LOSERS 官方市场榜单",
        "data_quality": {
            "status": "pass",
            "eligible_rows": sum(len(items) for items in rankings.values()),
            "ranking_kinds": len(rankings),
        },
        "rankings": rankings,
    }


def _alpha_item(item: dict[str, Any]) -> dict[str, Any]:
    symbol = str(item.get("ticker") or "").upper()
    return {
        "symbol": symbol,
        "name": symbol,
        "price": _num(item.get("price")),
        "change_pct": _num_percent(item.get("change_percentage")),
        "volume": _num(item.get("volume")),
        "secid": symbol,
    }


def _rank_normalized_rows(
    rows: list[dict[str, Any]], types: tuple[str, ...], limit: int
) -> dict[str, list[dict[str, Any]]]:
    rankings = {}
    for kind in types:
        candidates = list(rows)
        if kind == "gainers":
            candidates = [row for row in candidates if row.get("change_pct") is not None]
            candidates.sort(key=lambda row: float(row["change_pct"]), reverse=True)
        elif kind == "losers":
            candidates = [row for row in candidates if row.get("change_pct") is not None]
            candidates.sort(key=lambda row: float(row["change_pct"]))
        else:
            candidates.sort(
                key=lambda row: float(row.get("turnover") or row.get("volume") or 0),
                reverse=True,
            )
        rankings[kind] = candidates[:limit]
    return rankings


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    empty = getattr(value, "empty", False)
    if empty:
        return []
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        records = to_dict(orient="records")
        return [dict(row) for row in records]
    return []


def _normalize_tushare_symbol(market: str, value: Any) -> str:
    raw = str(value or "").strip().upper().split(".")[0]
    digits = "".join(character for character in raw if character.isdigit())
    if market == "A股":
        return digits.zfill(6)[-6:] if digits else ""
    return digits.zfill(5)[-5:] if digits else ""


def _complete_bundle(value: dict[str, Any], attempts: list[dict[str, Any]], degraded: bool) -> dict[str, Any]:
    result = dict(value)
    result.update(
        {
            "retrieved_at": value.get("retrieved_at") or _now_iso(),
            "served_at": _now_iso(),
            "degraded": bool(degraded),
            "provider_attempts": copy.deepcopy(attempts),
            "provider_policy_version": _POLICY_VERSION,
        }
    )
    return result


def _attempt_provider(
    provider_id: str,
    tier: str,
    function: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    remaining = _circuit_remaining(provider_id)
    if remaining > 0:
        return {
            "value": None,
            "attempt": {
                "provider": provider_id,
                "provider_label": _PROVIDER_LABELS[provider_id],
                "tier": tier,
                "status": "circuit_open",
                "message": f"熔断中，约 {remaining} 秒后重试",
            },
        }
    try:
        value = function()
    except ProviderNotConfigured as error:
        return {
            "value": None,
            "attempt": {
                "provider": provider_id,
                "provider_label": _PROVIDER_LABELS[provider_id],
                "tier": tier,
                "status": "not_configured",
                "message": _safe_error(error),
            },
        }
    except Exception as error:
        message = _safe_error(error)
        _record_provider_failure(provider_id, message)
        return {
            "value": None,
            "attempt": {
                "provider": provider_id,
                "provider_label": _PROVIDER_LABELS[provider_id],
                "tier": tier,
                "status": "failed",
                "message": message,
            },
        }
    _record_provider_success(provider_id)
    return {
        "value": value,
        "attempt": {
            "provider": provider_id,
            "provider_label": _PROVIDER_LABELS[provider_id],
            "tier": tier,
            "status": "success",
        },
    }


def _empty_runtime() -> dict[str, Any]:
    return {
        "success_count": 0,
        "consecutive_failures": 0,
        "last_success_at": None,
        "last_failure_at": None,
        "last_error": None,
        "circuit_open_until": None,
    }


def _record_provider_success(provider_id: str) -> None:
    with _provider_runtime_lock:
        runtime = _provider_runtime.setdefault(provider_id, _empty_runtime())
        runtime["success_count"] += 1
        runtime["consecutive_failures"] = 0
        runtime["last_success_at"] = _now_iso()
        runtime["last_error"] = None
        runtime["circuit_open_until"] = None


def _record_provider_failure(provider_id: str, message: str) -> None:
    with _provider_runtime_lock:
        runtime = _provider_runtime.setdefault(provider_id, _empty_runtime())
        runtime["consecutive_failures"] += 1
        runtime["last_failure_at"] = _now_iso()
        runtime["last_error"] = message[:240]
        if runtime["consecutive_failures"] >= config.HOT_STOCK_PROVIDER_FAILURE_THRESHOLD:
            open_until = time.time() + config.HOT_STOCK_PROVIDER_CIRCUIT_SECONDS
            runtime["circuit_open_until"] = dt.datetime.fromtimestamp(open_until).astimezone().isoformat(timespec="seconds")


def _runtime_snapshot(provider_id: str) -> dict[str, Any]:
    with _provider_runtime_lock:
        runtime = copy.deepcopy(_provider_runtime.get(provider_id, _empty_runtime()))
    runtime["circuit_remaining_seconds"] = _circuit_remaining(provider_id)
    return runtime


def _circuit_remaining(provider_id: str) -> int:
    with _provider_runtime_lock:
        runtime = _provider_runtime.get(provider_id) or {}
        raw = runtime.get("circuit_open_until")
    if not raw:
        return 0
    try:
        deadline = dt.datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0
    return max(0, int(deadline - time.time() + 0.999))


def _safe_error(error: Exception) -> str:
    if isinstance(error, requests.Timeout):
        message = "请求超时"
    elif isinstance(error, requests.HTTPError) and getattr(error, "response", None) is not None:
        response = error.response
        message = f"HTTP {response.status_code} {response.reason or '请求被拒绝'}"
    elif isinstance(error, (requests.ConnectionError, ConnectionError)):
        message = "连接被远端关闭、代理不可达或网络受限"
    else:
        message = str(error).strip() or error.__class__.__name__
    secrets = [
        str(config.TUSHARE_TOKEN),
        str(config.ALPHAVANTAGE_API_KEY),
        str(config.MASSIVE_API_KEY),
        str(config.POLYGON_API_KEY),
    ]
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    message = re.sub(r"(?i)(apikey|api_key|token)=([^&\s]+)", r"\1=***", message)
    message = re.sub(r"(?i)(authorization:\s*bearer\s+)([^\s]+)", r"\1***", message)
    return " ".join(message.split())[:240]


def _unavailable_message(
    market: str,
    attempts: list[dict[str, Any]],
    *,
    public_enabled: bool,
) -> str:
    details = "；".join(
        f"{attempt['provider_label']}：{attempt.get('message') or attempt['status']}"
        for attempt in attempts
    )
    fallback_note = "；公开降级已关闭" if not public_enabled else ""
    required = " 或 ".join(_REQUIRED_ENV[item] for item in _PROVIDER_ROUTES[market])
    return (
        f"{market}热门榜不可用。{details}{fallback_note}。"
        f"生产环境请配置 {required}；系统不会回退新浪。"
    )


def _clist(market: str, fid: str, po: int, limit: int) -> list[dict[str, Any]]:
    params = {
        "pn": 1,
        "pz": limit,
        "po": po,
        "np": 1,
        "ut": _UT,
        "fltt": 2,
        "invt": 2,
        "fid": fid,
        "fs": _MARKET_FS[market],
        "fields": "f2,f3,f5,f6,f12,f13,f14",
    }
    response = requests.get(_CLIST_URL, params=params, timeout=_TIMEOUT)
    response.raise_for_status()
    diff = ((response.json().get("data") or {}).get("diff") or [])
    if not diff:
        raise RuntimeError("东方财富榜单返回空数据")
    return [
        {
            "symbol": item.get("f12", ""),
            "name": item.get("f14", ""),
            "price": _num(item.get("f2")),
            "change_pct": _num(item.get("f3")),
            "volume": _num(item.get("f5")),
            "secid": f"{item.get('f13', '')}.{item.get('f12', '')}",
        }
        for item in diff[:limit]
    ]


def _hot_1d(market: str, type_: str, limit: int) -> list[dict[str, Any]]:
    """Compatibility helper used by multiday tests and older internal callers."""
    return get_hot_stock_bundle(market, [type_], limit)["rankings"][type_]


def _hot_multiday(market: str, type_: str, days: int, limit: int) -> list[dict[str, Any]]:
    candidates = _hot_1d(market, "active", max(20, min(50, limit * 3)))
    return _enrich_multiday(market, candidates, type_, days, limit)


def _enrich_multiday(
    market: str,
    candidates: list[dict[str, Any]],
    type_: str,
    days: int,
    limit: int,
) -> list[dict[str, Any]]:
    if market == "美股":
        enriched = _yahoo_us_period_returns(candidates, days)
    else:
        def enrich(item: dict[str, Any]) -> dict[str, Any] | None:
            try:
                result = dict(item)
                result["change_pct"] = _n_day_return(market, str(item["symbol"]), days)
                return result
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            enriched = [item for item in pool.map(enrich, candidates) if item is not None]
    if type_ == "active":
        return enriched[:limit]
    enriched.sort(key=lambda item: float(item["change_pct"]), reverse=type_ == "gainers")
    return enriched[:limit]


def _yahoo_us_1d(type_: str, limit: int) -> list[dict[str, Any]]:
    screener = {"gainers": "day_gainers", "losers": "day_losers", "active": "most_actives"}[type_]
    response = requests.get(
        _YAHOO_SCREENER_URL,
        params={"formatted": "false", "scrIds": screener, "count": limit, "start": 0},
        headers=_YAHOO_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    results = (response.json().get("finance") or {}).get("result") or []
    quotes = (results[0].get("quotes") or []) if results else []
    if not quotes:
        raise RuntimeError("Yahoo Finance 美股榜单返回空数据")
    return [
        {
            "symbol": str(item.get("symbol") or "").upper(),
            "name": item.get("shortName") or item.get("longName") or "",
            "price": _num(item.get("regularMarketPrice")),
            "change_pct": _num(item.get("regularMarketChangePercent")),
            "volume": _num(item.get("regularMarketVolume")),
            "secid": str(item.get("symbol") or "").upper(),
        }
        for item in quotes[:limit]
        if item.get("symbol")
    ]


def _yahoo_us_period_returns(candidates: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    symbols = [str(item.get("symbol") or "").upper() for item in candidates]
    results = _yahoo_spark_results(symbols, days)
    returns: dict[str, float] = {}
    for result in results:
        symbol = str(result.get("symbol") or "").upper()
        responses = result.get("response") or []
        quotes = (((responses[0].get("indicators") or {}).get("quote") or [{}]) if responses else [{}])
        closes = [value for value in (quotes[0].get("close") or []) if value is not None]
        if len(closes) >= 2:
            window = closes[-(days + 1):]
            returns[symbol] = round((window[-1] / window[0] - 1) * 100, 2)
    enriched = []
    for item in candidates:
        symbol = str(item.get("symbol") or "").upper()
        if symbol in returns:
            row = dict(item)
            row["change_pct"] = returns[symbol]
            enriched.append(row)
    return enriched


def _yahoo_spark_results(symbols: list[str], days: int) -> list[dict[str, Any]]:
    if not symbols:
        return []
    response = requests.get(
        _YAHOO_SPARK_URL,
        params={"symbols": ",".join(symbols), "range": "1mo" if days <= 7 else "3mo", "interval": "1d"},
        headers=_YAHOO_HEADERS,
        timeout=_TIMEOUT,
    )
    if response.ok:
        return (response.json().get("spark") or {}).get("result") or []
    if len(symbols) == 1:
        return []
    middle = len(symbols) // 2
    return _yahoo_spark_results(symbols[:middle], days) + _yahoo_spark_results(symbols[middle:], days)


def _n_day_return(market: str, symbol: str, days: int) -> float:
    frame = data_fetch.get_history_months(market, symbol, 3, fetch_months=3)
    closes = frame["close"].astype(float).dropna().tail(days + 1).tolist()
    if len(closes) < 2:
        raise RuntimeError("K 线数据不足")
    return round((closes[-1] / closes[0] - 1) * 100, 2)


def _num(value: Any) -> float | None:
    if value is None or value == "-":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _num_percent(value: Any) -> float | None:
    return _num(str(value).strip().rstrip("%")) if value is not None else None


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")
