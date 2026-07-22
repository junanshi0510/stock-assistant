# -*- coding: utf-8 -*-
"""Professional-first hot-stock rankings with explicit degraded fallbacks.

The ranking router deliberately separates licensed/professional APIs from public
web endpoints:

* A-share / Hong Kong: Tushare Pro latest completed trading day.
* US: Alpha Vantage ``TOP_GAINERS_LOSERS``.
* Degraded fallback only: Eastmoney (A/HK) and Yahoo Finance (US).

Sina is never used. Provider failures are circuit-broken, secrets are redacted,
and callers receive one actionable failure per market instead of one failure per
ranking kind.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import datetime as dt
import re
import threading
import time
from typing import Any, Callable

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
_POLICY_VERSION = "hot_stock_provider_router@1.0.0"

_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_name_cache: dict[str, tuple[float, dict[str, str]]] = {}
_name_cache_lock = threading.Lock()
_provider_runtime: dict[str, dict[str, Any]] = {}
_provider_runtime_lock = threading.Lock()

_MARKET_FS = {
    "A股": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    "港股": "m:116,m:113,m:114,m:115",
    "美股": "m:105,m:106,m:107",
}
_PERIOD_DAYS = {"7d": 7, "30d": 30}
_VALID_TYPES = ("gainers", "losers", "active")
_PROFESSIONAL_PROVIDER = {"A股": "tushare_pro_a", "港股": "tushare_pro_hk", "美股": "alpha_vantage"}
_PUBLIC_PROVIDER = {"A股": "eastmoney_public_a", "港股": "eastmoney_public_hk", "美股": "yahoo_public"}
_PROVIDER_LABELS = {
    "tushare_pro_a": "Tushare Pro",
    "tushare_pro_hk": "Tushare Pro",
    "alpha_vantage": "Alpha Vantage",
    "eastmoney_public_a": "东方财富公开接口",
    "eastmoney_public_hk": "东方财富公开接口",
    "yahoo_public": "Yahoo Finance 公开接口",
}
_REQUIRED_ENV = {"A股": "TUSHARE_TOKEN", "港股": "TUSHARE_TOKEN", "美股": "ALPHAVANTAGE_API_KEY"}
_DOC_URLS = {
    "A股": "https://tushare.pro/document/1?doc_id=27",
    "港股": "https://tushare.pro/document/2?doc_id=190",
    "美股": "https://www.alphavantage.co/documentation/",
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
        else:
            candidates = bundle["rankings"]["active"]
            items = _enrich_multiday(market, candidates, type_, _PERIOD_DAYS[period], limit)
            scope = "成交活跃候选池内"
            methodology = (
                f"{bundle['source']} 成交活跃候选池内，按真实日 K 计算区间涨跌；"
                "不是交易所全量多日排名"
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
        "stale": False,
        "degraded": bundle["degraded"],
        "warning": bundle.get("warning"),
        "provider_attempts": copy.deepcopy(bundle["provider_attempts"]),
        "provider_policy_version": _POLICY_VERSION,
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
) -> dict[str, Any]:
    """Fetch all requested ranking kinds through one market-level provider route."""
    if market not in _MARKET_FS:
        raise ValueError(f"不支持的市场：{market}")
    normalized_types = tuple(dict.fromkeys(str(item) for item in types))
    if not normalized_types or any(item not in _VALID_TYPES for item in normalized_types):
        raise ValueError("榜单类型必须是 gainers、losers 或 active")
    limit = max(5, min(50, int(limit)))
    attempts: list[dict[str, Any]] = []

    professional_id = _PROFESSIONAL_PROVIDER[market]
    professional = _attempt_provider(
        professional_id,
        "professional",
        lambda: _professional_bundle(market, normalized_types, limit),
    )
    attempts.append(professional["attempt"])
    if professional["value"] is not None:
        return _complete_bundle(professional["value"], attempts, degraded=False)

    if config.HOT_STOCK_PUBLIC_FALLBACK_ENABLED:
        public_id = _PUBLIC_PROVIDER[market]
        public = _attempt_provider(
            public_id,
            "public_fallback",
            lambda: _public_bundle(market, normalized_types, limit),
        )
        attempts.append(public["attempt"])
        if public["value"] is not None:
            professional_reason = professional["attempt"].get("message") or "专业源不可用"
            warning = (
                f"{market}专业源未生效（{professional_reason}），当前使用{_PROVIDER_LABELS[public_id]}"
                "临时降级数据；覆盖范围、稳定性和时效性不作生产保证。"
            )
            value = dict(public["value"])
            value["warning"] = warning
            return _complete_bundle(value, attempts, degraded=True)

    raise HotStockProviderUnavailable(_unavailable_message(market, attempts), attempts)


def get_provider_status() -> dict[str, Any]:
    """Expose configuration/runtime health without probing or returning secrets."""
    generated_at = _now_iso()
    markets = []
    for market in _MARKET_FS:
        professional_id = _PROFESSIONAL_PROVIDER[market]
        public_id = _PUBLIC_PROVIDER[market]
        configured, config_message = _professional_configuration(market)
        runtime = _runtime_snapshot(professional_id)
        remaining = _circuit_remaining(professional_id)
        if not configured:
            state = "configuration_required" if config_message == "未配置" else "configuration_invalid"
        elif remaining > 0:
            state = "circuit_open"
        elif runtime.get("last_success_at") and (
            not runtime.get("last_failure_at")
            or runtime["last_success_at"] >= runtime["last_failure_at"]
        ):
            state = "ready"
        else:
            state = "configured_unverified"
        freshness = "latest_completed_eod"
        if market == "美股":
            entitlement = config.ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT
            freshness = entitlement if entitlement in {"delayed", "realtime"} else "end_of_day"
        markets.append(
            {
                "market": market,
                "provider": professional_id,
                "provider_label": _PROVIDER_LABELS[professional_id],
                "provider_tier": "professional",
                "required_env": _REQUIRED_ENV[market],
                "configured": configured,
                "configuration_message": config_message,
                "state": state,
                "expected_freshness": freshness,
                "documentation_url": _DOC_URLS[market],
                "runtime": runtime,
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


def _validate_request(market: str, period: str, type_: str) -> None:
    if market not in _MARKET_FS:
        raise ValueError(f"不支持的市场：{market}")
    if type_ not in _VALID_TYPES:
        raise ValueError(f"不支持的榜单类型：{type_}")
    if period not in ("1d", "7d", "30d"):
        raise ValueError(f"不支持的周期：{period}")


def _professional_configuration(market: str) -> tuple[bool, str]:
    if market in {"A股", "港股"}:
        return (True, "已配置") if str(config.TUSHARE_TOKEN).strip() else (False, "未配置")
    if not str(config.ALPHAVANTAGE_API_KEY).strip():
        return False, "未配置"
    entitlement = str(config.ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT).strip().lower()
    if entitlement and entitlement not in {"delayed", "realtime"}:
        return False, "ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT 只能留空、delayed 或 realtime"
    return True, "已配置"


def _professional_bundle(market: str, types: tuple[str, ...], limit: int) -> dict[str, Any]:
    if market in {"A股", "港股"}:
        if not str(config.TUSHARE_TOKEN).strip():
            raise ProviderNotConfigured(f"未配置 {_REQUIRED_ENV[market]}")
        return _tushare_bundle(market, types, limit)
    configured, message = _professional_configuration(market)
    if not configured:
        if message == "未配置":
            raise ProviderNotConfigured(f"未配置 {_REQUIRED_ENV[market]}")
        raise ProviderNotConfigured(message)
    return _alpha_vantage_bundle(types, limit)


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
        "provider": _PROFESSIONAL_PROVIDER[market],
        "provider_tier": "professional",
        "data_freshness": "latest_completed_eod",
        "as_of": formatted_as_of,
        "scope": "全市场·最近完整交易日",
        "methodology": "Tushare Pro 全市场日线快照本地统一排序",
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
            "retrieved_at": _now_iso(),
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
    secrets = [str(config.TUSHARE_TOKEN), str(config.ALPHAVANTAGE_API_KEY), str(config.POLYGON_API_KEY)]
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    message = re.sub(r"(?i)(apikey|api_key|token)=([^&\s]+)", r"\1=***", message)
    return " ".join(message.split())[:240]


def _unavailable_message(market: str, attempts: list[dict[str, Any]]) -> str:
    details = "；".join(
        f"{attempt['provider_label']}：{attempt.get('message') or attempt['status']}"
        for attempt in attempts
    )
    fallback_note = "；公开降级已关闭" if not config.HOT_STOCK_PUBLIC_FALLBACK_ENABLED else ""
    return (
        f"{market}热门榜不可用。{details}{fallback_note}。"
        f"生产环境请配置 {_REQUIRED_ENV[market]}；系统不会回退新浪。"
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
