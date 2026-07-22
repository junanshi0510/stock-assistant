# -*- coding: utf-8 -*-
"""Auditable cross-market portfolio valuation in a single base currency.

The service never invents a quote. It uses confirmed NAV/EOD observations,
persists their provenance, falls back to a recent user-confirmed amount when
possible, and makes freshness/coverage gaps explicit in the decision gate.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import requests

import config
import data_fetch
import funds
import storage
from portfolio_valuation_repository import (
    SNAPSHOT_SCHEMA_VERSION,
    PortfolioValuationRepository,
    canonical_json,
    sha256_text,
)


BASE_CURRENCY = "CNY"
METHOD_VERSION = "portfolio_valuation@1.0.0"
PRICE_CACHE_HOURS = 6
NAV_CACHE_HOURS = 12
FX_CACHE_HOURS = 12
MANUAL_AMOUNT_MAX_AGE_DAYS = 7

_MARKET_ALIASES = {
    "a": "A股",
    "a股": "A股",
    "cn": "A股",
    "china": "A股",
    "mainland": "A股",
    "大陆": "A股",
    "港股": "港股",
    "hk": "港股",
    "hongkong": "港股",
    "hong kong": "港股",
    "美股": "美股",
    "us": "美股",
    "usa": "美股",
    "基金": "基金",
    "fund": "基金",
}
_MARKET_CURRENCY = {"A股": "CNY", "港股": "HKD", "美股": "USD", "基金": "CNY"}
_OBSERVATION_MAX_AGE_DAYS = {"price": 5, "nav": 7, "fx": 5}


class PortfolioValuationError(RuntimeError):
    pass


def _safe_provider_error(error: Exception | str, limit: int = 300) -> str:
    """Keep provider diagnostics without persisting API credentials."""
    message = str(error)
    message = re.sub(
        r"(?i)((?:api[_-]?key|apikey|token|access[_-]?key(?:_id)?|secret)=)[^&\s;]+",
        r"\1[redacted]",
        message,
    )
    for secret in (
        config.TUSHARE_TOKEN,
        config.MASSIVE_API_KEY,
        config.POLYGON_API_KEY,
        config.ALPHAVANTAGE_API_KEY,
    ):
        secret = str(secret or "").strip()
        if len(secret) >= 4:
            message = message.replace(secret, "[redacted]")
    return message[: max(1, int(limit))]


def _now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return _now(value).isoformat(timespec="milliseconds")


def _parse_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = dt.datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _date(value: Any) -> dt.date | None:
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def normalize_market(asset_type: str, market: str, symbol: str) -> str:
    if str(asset_type or "").strip().lower() == "fund":
        return "基金"
    raw = str(market or "").strip()
    normalized = _MARKET_ALIASES.get(raw.lower())
    if normalized and normalized != "基金":
        return normalized
    code = str(symbol or "").strip()
    if re.fullmatch(r"\d{6}", code):
        return "A股"
    if re.fullmatch(r"\d{5}", code):
        return "港股"
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,14}", code):
        return "美股"
    raise ValueError(f"无法识别股票市场:{raw or code}")


def holdings_fingerprint(items: list[dict[str, Any]]) -> str:
    normalized = []
    for item in items:
        normalized.append({
            "id": item.get("id"),
            "asset_type": str(item.get("asset_type") or ""),
            "market": str(item.get("market") or ""),
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or ""),
            "shares": _round(item.get("shares"), 8),
            "amount": _round(item.get("amount"), 4),
            "updated_at": str(item.get("updated_at") or ""),
        })
    normalized.sort(key=lambda row: (
        row["asset_type"], row["market"], row["code"], str(row["id"])
    ))
    return sha256_text(canonical_json(normalized))


def _quality_for_stock_source(source: str) -> str:
    label = str(source or "").lower()
    if any(token in label for token in ("tushare", "polygon", "massive", "alpha vantage")):
        return "primary"
    return "fallback"


def _stock_observation(market: str, symbol: str, current: dt.datetime) -> dict[str, Any]:
    frame, source = data_fetch.get_price_level_history_months(market, symbol, months=6)
    if frame.empty:
        raise PortfolioValuationError("真实未复权日线为空")
    row = frame.iloc[-1]
    price = _number(row.get("close"))
    if price is None or price <= 0:
        raise PortfolioValuationError("真实未复权日线没有有效收盘价")
    as_of = str(row.get("date") or "")[:10]
    return {
        "kind": "price",
        "asset_type": "stock",
        "market": market,
        "symbol": symbol,
        "currency": _MARKET_CURRENCY[market],
        "value": price,
        "as_of": as_of,
        "source": source,
        "source_url": "",
        "quality_status": _quality_for_stock_source(source),
        "retrieved_at": _iso(current),
        "expires_at": _iso(current + dt.timedelta(hours=PRICE_CACHE_HOURS)),
        "payload": {
            "open": _round(row.get("open"), 8),
            "high": _round(row.get("high"), 8),
            "low": _round(row.get("low"), 8),
            "close": _round(price, 8),
            "volume": _round(row.get("volume"), 4),
            "adjustment": "unadjusted_eod",
        },
    }


def _fund_observation(code: str, current: dt.datetime) -> dict[str, Any]:
    data = funds.get_fund_nav_history(code, months=6)
    points = data.get("points") or []
    if not points:
        raise PortfolioValuationError("真实确认净值为空")
    latest = points[-1]
    nav = _number(latest.get("unit_nav"))
    if nav is None or nav <= 0:
        raise PortfolioValuationError("真实确认净值无效")
    return {
        "kind": "nav",
        "asset_type": "fund",
        "market": "基金",
        "symbol": code,
        "currency": "CNY",
        "value": nav,
        "as_of": str(latest.get("date") or data.get("as_of") or ""),
        "source": str(data.get("source") or "基金确认净值"),
        "source_url": str(data.get("source_url") or ""),
        "quality_status": "primary",
        "retrieved_at": _iso(current),
        "expires_at": _iso(current + dt.timedelta(hours=NAV_CACHE_HOURS)),
        "payload": {
            "unit_nav": _round(nav, 8),
            "acc_nav": _round(latest.get("acc_nav"), 8),
            "confirmed_nav_only": True,
            "observation_count": int(data.get("observation_count") or len(points)),
        },
    }


def _massive_fx(currency: str, current: dt.datetime) -> dict[str, Any]:
    api_key = str(config.MASSIVE_API_KEY or config.POLYGON_API_KEY or "").strip()
    if not api_key:
        raise PortfolioValuationError("Massive FX Key 未配置")
    base_url = (
        str(config.MASSIVE_API_BASE_URL).rstrip("/")
        if config.MASSIVE_API_KEY
        else "https://api.polygon.io"
    )
    ticker = f"C:{currency}{BASE_CURRENCY}"
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        f"{base_url}/v2/aggs/ticker/{ticker}/prev",
        params={"adjusted": "true", "apiKey": api_key},
        headers={"User-Agent": "stock-assistant/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    results = (response.json() or {}).get("results") or []
    if not results:
        raise PortfolioValuationError("Massive FX 未返回前一交易日汇率")
    row = results[-1]
    rate = _number(row.get("c"))
    if rate is None or rate <= 0:
        raise PortfolioValuationError("Massive FX 汇率无效")
    stamp = _number(row.get("t"))
    as_of = (
        dt.datetime.fromtimestamp(stamp / 1000, tz=dt.timezone.utc).date().isoformat()
        if stamp is not None
        else current.date().isoformat()
    )
    return {
        "kind": "fx",
        "asset_type": "currency",
        "market": "FX",
        "symbol": f"{currency}/{BASE_CURRENCY}",
        "currency": BASE_CURRENCY,
        "value": rate,
        "as_of": as_of,
        "source": "Massive Forex previous-day aggregate",
        "source_url": f"{base_url}/v2/aggs/ticker/{ticker}/prev",
        "quality_status": "primary",
        "retrieved_at": _iso(current),
        "expires_at": _iso(current + dt.timedelta(hours=FX_CACHE_HOURS)),
        "payload": {"base": currency, "quote": BASE_CURRENCY, "close": rate},
    }


def _frankfurter_fx(currency: str, current: dt.datetime) -> dict[str, Any]:
    url = f"https://api.frankfurter.dev/v2/rate/{currency}/{BASE_CURRENCY}"
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        url,
        headers={"Accept": "application/json", "User-Agent": "stock-assistant/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json() or {}
    rate = _number(data.get("rate"))
    if rate is None or rate <= 0:
        raise PortfolioValuationError("Frankfurter 未返回有效参考汇率")
    return {
        "kind": "fx",
        "asset_type": "currency",
        "market": "FX",
        "symbol": f"{currency}/{BASE_CURRENCY}",
        "currency": BASE_CURRENCY,
        "value": rate,
        "as_of": str(data.get("date") or current.date().isoformat()),
        "source": "Frankfurter central-bank reference rates",
        "source_url": url,
        "quality_status": "primary",
        "retrieved_at": _iso(current),
        "expires_at": _iso(current + dt.timedelta(hours=FX_CACHE_HOURS)),
        "payload": {
            "base": str(data.get("base") or currency),
            "quote": str(data.get("quote") or BASE_CURRENCY),
            "rate": rate,
            "providers": data.get("providers") or [],
        },
    }


def _fx_observation(currency: str, current: dt.datetime) -> dict[str, Any]:
    currency = str(currency or "").upper()
    if currency == BASE_CURRENCY:
        return {
            "kind": "fx",
            "asset_type": "currency",
            "market": "FX",
            "symbol": f"{BASE_CURRENCY}/{BASE_CURRENCY}",
            "currency": BASE_CURRENCY,
            "value": 1.0,
            "as_of": current.date().isoformat(),
            "source": "base-currency identity",
            "source_url": "",
            "quality_status": "identity",
            "retrieved_at": _iso(current),
            "expires_at": _iso(current + dt.timedelta(days=30)),
            "payload": {"base": BASE_CURRENCY, "quote": BASE_CURRENCY, "rate": 1.0},
        }
    massive_error = None
    try:
        return _massive_fx(currency, current)
    except Exception as error:
        massive_error = _safe_provider_error(error, 180)
    try:
        result = _frankfurter_fx(currency, current)
        result["payload"]["massive_fallback_reason"] = massive_error
        return result
    except Exception as error:
        raise PortfolioValuationError(
            f"{currency}/{BASE_CURRENCY} 汇率不可用: Massive={massive_error}; "
            f"Frankfurter={_safe_provider_error(error, 180)}"
        ) from error


def _observation_current(observation: dict[str, Any], current: dt.datetime) -> bool:
    if not (observation.get("integrity") or {}).get("verified", True):
        return False
    expires = _parse_datetime(observation.get("expires_at"))
    if expires is None or expires <= current:
        return False
    as_of = _date(observation.get("as_of"))
    if as_of is None:
        return False
    max_age = _OBSERVATION_MAX_AGE_DAYS.get(str(observation.get("kind")), 5)
    return (current.date() - as_of).days <= max_age


def _observation_fresh_until(observation: dict[str, Any]) -> dt.datetime | None:
    expires = _parse_datetime(observation.get("expires_at"))
    as_of = _date(observation.get("as_of"))
    if as_of is None:
        return expires
    max_age = _OBSERVATION_MAX_AGE_DAYS.get(str(observation.get("kind")), 5)
    age_limit = dt.datetime.combine(
        as_of + dt.timedelta(days=max_age + 1),
        dt.time.min,
        tzinfo=dt.timezone.utc,
    )
    return min(expires, age_limit) if expires else age_limit


def _load_observation(
    repository: PortfolioValuationRepository,
    *,
    kind: str,
    market: str,
    symbol: str,
    loader: Callable[[], dict[str, Any]],
    force: bool,
    current: dt.datetime,
) -> tuple[dict[str, Any] | None, str, str | None]:
    cached = repository.latest_observation(kind=kind, market=market, symbol=symbol)
    if cached and not force and _observation_current(cached, current):
        return cached, "cache_fresh", None
    try:
        saved = repository.save_observation(loader())
        return saved, "provider", None
    except Exception as error:
        message = _safe_provider_error(error, 300)
        if cached:
            cache_state = (
                "cache_fallback_current"
                if _observation_current(cached, current)
                else "cache_stale"
            )
            return cached, cache_state, message
        return None, "unavailable", message


def _manual_fresh_until(item: dict[str, Any], current: dt.datetime) -> dt.datetime:
    updated = _parse_datetime(item.get("updated_at")) or current
    return updated + dt.timedelta(days=MANUAL_AMOUNT_MAX_AGE_DAYS)


def _position_key(item: dict[str, Any]) -> tuple[str, str, str]:
    asset_type = str(item.get("asset_type") or "").strip().lower()
    code = str(item.get("code") or "").strip()
    market = normalize_market(asset_type, str(item.get("market") or ""), code)
    return asset_type, market, code


def _group_totals(positions: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    for position in positions:
        value = _number(position.get("base_value"))
        if value is None:
            continue
        label = str(position.get(key) or "未分类")
        totals[label] = totals.get(label, 0.0) + value
    grand = sum(totals.values())
    return [
        {
            "name": name,
            "value": round(value, 2),
            "ratio": round(value / grand * 100, 2) if grand else None,
        }
        for name, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def refresh_portfolio_valuation(
    *,
    user_id: str,
    tenant_id: str = "public",
    actor_id: str | None = None,
    force: bool = False,
    repository: PortfolioValuationRepository | None = None,
    holdings: list[dict[str, Any]] | None = None,
    stock_loader: Callable[[str, str, dt.datetime], dict[str, Any]] | None = None,
    fund_loader: Callable[[str, dt.datetime], dict[str, Any]] | None = None,
    fx_loader: Callable[[str, dt.datetime], dict[str, Any]] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    repo = repository or PortfolioValuationRepository()
    items = list(holdings if holdings is not None else storage.list_holdings(user_id=user_id))
    holdings_sha = holdings_fingerprint(items)
    stock_fetch = stock_loader or _stock_observation
    fund_fetch = fund_loader or _fund_observation
    fx_fetch = fx_loader or _fx_observation

    price_results: dict[tuple[str, str, str], tuple[dict | None, str, str | None]] = {}
    fetch_targets: dict[tuple[str, str, str], dict[str, Any]] = {}
    target_errors: dict[int, str] = {}
    for item in items:
        shares = _number(item.get("shares"))
        if shares is None or shares <= 0:
            continue
        try:
            key = _position_key(item)
            fetch_targets.setdefault(key, item)
        except ValueError as error:
            target_errors[int(item.get("id") or 0)] = str(error)

    def load_price(key: tuple[str, str, str]):
        asset_type, market, code = key
        kind = "nav" if asset_type == "fund" else "price"
        loader = (
            (lambda: fund_fetch(code, current))
            if asset_type == "fund"
            else (lambda: stock_fetch(market, code, current))
        )
        return key, _load_observation(
            repo,
            kind=kind,
            market=market,
            symbol=code,
            loader=loader,
            force=bool(force),
            current=current,
        )

    if fetch_targets:
        with ThreadPoolExecutor(max_workers=min(6, len(fetch_targets))) as pool:
            futures = [pool.submit(load_price, key) for key in fetch_targets]
            for future in as_completed(futures):
                key, result = future.result()
                price_results[key] = result

    required_currencies = {
        str(result[0].get("currency"))
        for result in price_results.values()
        if result[0] is not None
    }
    fx_results: dict[str, tuple[dict | None, str, str | None]] = {}

    def load_fx(currency: str):
        symbol = f"{currency}/{BASE_CURRENCY}"
        return currency, _load_observation(
            repo,
            kind="fx",
            market="FX",
            symbol=symbol,
            loader=lambda: fx_fetch(currency, current),
            force=bool(force and currency != BASE_CURRENCY),
            current=current,
        )

    if required_currencies:
        with ThreadPoolExecutor(max_workers=min(3, len(required_currencies))) as pool:
            futures = [pool.submit(load_fx, currency) for currency in required_currencies]
            for future in as_completed(futures):
                currency, result = future.result()
                fx_results[currency] = result

    positions: list[dict[str, Any]] = []
    fresh_limits: list[dt.datetime] = []
    for item in items:
        holding_id = int(item.get("id") or 0)
        asset_type = str(item.get("asset_type") or "").strip().lower()
        code = str(item.get("code") or "").strip()
        shares = _number(item.get("shares"))
        manual_amount = _number(item.get("amount"))
        manual_amount = manual_amount if manual_amount is not None and manual_amount > 0 else None
        issues: list[str] = []
        observation = None
        observation_cache = "not_requested"
        observation_error = target_errors.get(holding_id)
        market = "基金" if asset_type == "fund" else str(item.get("market") or "")
        try:
            key = _position_key(item)
            market = key[1]
            if shares is not None and shares > 0:
                observation, observation_cache, observation_error = price_results.get(
                    key, (None, "unavailable", "没有返回价格观察")
                )
        except ValueError as error:
            issues.append(str(error))

        method = "unavailable"
        native_value = None
        base_value = None
        unit_price = None
        currency = _MARKET_CURRENCY.get(market, BASE_CURRENCY)
        fx = None
        fx_cache = "not_requested"
        fx_error = None
        freshness = "unavailable"
        price_fresh = False
        fx_fresh = False

        if observation is not None and shares is not None and shares > 0:
            unit_price = _number(observation.get("value"))
            currency = str(observation.get("currency") or currency)
            native_value = shares * unit_price if unit_price is not None else None
            fx, fx_cache, fx_error = fx_results.get(
                currency, (None, "unavailable", "没有返回汇率观察")
            )
            price_fresh = observation_cache != "cache_stale" and _observation_current(
                observation, current
            )
            fx_fresh = bool(
                fx
                and fx_cache != "cache_stale"
                and _observation_current(fx, current)
            )
            if native_value is not None and fx is not None:
                fx_rate = _number(fx.get("value"))
                if fx_rate is not None:
                    base_value = native_value * fx_rate
                    method = "automatic_confirmed_price"
                    freshness = "current" if price_fresh and fx_fresh else "stale"
                    for source_observation in (observation, fx):
                        limit = _observation_fresh_until(source_observation)
                        if limit:
                            fresh_limits.append(limit)

        if method == "unavailable" and manual_amount is not None:
            method = "manual_confirmed_amount"
            base_value = manual_amount
            currency = BASE_CURRENCY
            manual_limit = _manual_fresh_until(item, current)
            fresh_limits.append(manual_limit)
            freshness = "current" if manual_limit > current else "stale"
            if shares is None or shares <= 0:
                issues.append("缺少有效份额，暂时使用用户确认金额")
            elif observation_error:
                issues.append(f"价格不可用，暂时使用用户确认金额:{observation_error}")
            elif fx_error:
                issues.append(f"汇率不可用，暂时使用用户确认金额:{fx_error}")

        if method == "unavailable":
            issues.append(observation_error or fx_error or "金额、份额或真实价格不足")
        else:
            if observation_cache == "cache_fallback_current":
                issues.append(f"上游刷新失败，使用仍有效的缓存价格观察:{observation_error or '-'}")
            if fx_cache == "cache_fallback_current":
                issues.append(f"汇率刷新失败，使用仍有效的缓存汇率观察:{fx_error or '-'}")
            if observation_cache == "cache_stale":
                issues.append(f"上游刷新失败，使用过期价格观察:{observation_error or '-'}")
            if fx_cache == "cache_stale":
                issues.append(f"汇率刷新失败，使用过期汇率观察:{fx_error or '-'}")

        fx_rate = _number((fx or {}).get("value")) if fx else (1.0 if currency == BASE_CURRENCY else None)
        positions.append({
            "holding_id": holding_id,
            "asset_type": asset_type,
            "market": market,
            "code": code,
            "name": str(item.get("name") or ""),
            "shares": _round(shares, 8),
            "manual_amount": _round(manual_amount),
            "valuation_method": method,
            "freshness": freshness,
            "currency": currency,
            "unit_price": _round(unit_price, 8),
            "price_as_of": observation.get("as_of") if observation else None,
            "price_source": observation.get("source") if observation else None,
            "price_quality": observation.get("quality_status") if observation else None,
            "price_observation_id": observation.get("id") if observation else None,
            "price_cache": observation_cache,
            "fx_rate_to_cny": _round(fx_rate, 8),
            "fx_as_of": fx.get("as_of") if fx else (current.date().isoformat() if currency == BASE_CURRENCY else None),
            "fx_source": fx.get("source") if fx else ("base-currency identity" if currency == BASE_CURRENCY else None),
            "fx_observation_id": fx.get("id") if fx else None,
            "fx_cache": fx_cache,
            "native_value": _round(native_value),
            "base_value": _round(base_value),
            "delta_vs_manual_amount": _round(
                base_value - manual_amount
                if base_value is not None and manual_amount is not None
                else None
            ),
            "holding_updated_at": item.get("updated_at"),
            "issues": issues,
        })

    valued_positions = [row for row in positions if _number(row.get("base_value")) is not None]
    total_value = sum(float(row["base_value"]) for row in valued_positions)
    for row in positions:
        value = _number(row.get("base_value"))
        row["ratio"] = round(value / total_value * 100, 2) if value is not None and total_value else None
    positions.sort(key=lambda row: _number(row.get("base_value")) or -1, reverse=True)

    automatic = [row for row in valued_positions if row["valuation_method"] == "automatic_confirmed_price"]
    manual = [row for row in valued_positions if row["valuation_method"] == "manual_confirmed_amount"]
    stale = [row for row in valued_positions if row["freshness"] != "current"]
    primary_value = sum(
        float(row["base_value"])
        for row in automatic
        if row.get("price_quality") in {"primary", "identity"}
    )
    automatic_value = sum(float(row["base_value"]) for row in automatic)
    count = len(positions)
    valued_count = len(valued_positions)
    count_coverage = valued_count / count * 100 if count else 0.0
    automatic_value_pct = automatic_value / total_value * 100 if total_value else 0.0
    professional_value_pct = primary_value / total_value * 100 if total_value else 0.0

    allocation_eligible = bool(count and valued_count == count and total_value > 0)
    risk_eligible = bool(allocation_eligible and not stale)
    trade_amount_eligible = bool(
        risk_eligible
        and automatic_value_pct >= 95
        and professional_value_pct >= 90
    )
    gate_reasons = []
    if not count:
        gate_reasons.append("尚未保存持仓")
    if valued_count < count:
        gate_reasons.append(f"{count - valued_count} 项持仓无法估值")
    if stale:
        gate_reasons.append(f"{len(stale)} 项估值已过期")
    if automatic_value_pct < 95 and total_value:
        gate_reasons.append(f"自动估值覆盖 {automatic_value_pct:.2f}%，不足 95%")
    if professional_value_pct < 90 and automatic_value:
        gate_reasons.append(f"专业/确认来源覆盖 {professional_value_pct:.2f}%，不足 90%")
    if not gate_reasons:
        gate_reasons.append("估值、汇率、覆盖率与时效门禁均通过")

    status = (
        "blocked"
        if not valued_positions
        else "complete"
        if risk_eligible
        else "partial"
    )
    fresh_until = min(fresh_limits).isoformat(timespec="milliseconds") if fresh_limits else None
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "method_version": METHOD_VERSION,
        "created_at": _iso(current),
        "base_currency": BASE_CURRENCY,
        "holdings_sha256": holdings_sha,
        "status": status,
        "fresh_until": fresh_until,
        "summary": {
            "holding_count": count,
            "valued_holding_count": valued_count,
            "total_value": round(total_value, 2) if total_value else None,
            "automatic_value": round(automatic_value, 2) if automatic_value else None,
            "manual_value": round(sum(float(row["base_value"]) for row in manual), 2) if manual else None,
            "top1_ratio": positions[0].get("ratio") if positions else None,
            "market_totals": _group_totals(positions, "market"),
            "asset_type_totals": _group_totals(positions, "asset_type"),
            "currency_totals": _group_totals(positions, "currency"),
        },
        "coverage": {
            "holding_count": count,
            "valued_count": valued_count,
            "automatic_count": len(automatic),
            "manual_count": len(manual),
            "unavailable_count": count - valued_count,
            "stale_count": len(stale),
            "count_coverage_pct": round(count_coverage, 2),
            "automatic_value_pct": round(automatic_value_pct, 2),
            "professional_value_pct": round(professional_value_pct, 2),
        },
        "decision_gate": {
            "allocation_eligible": allocation_eligible,
            "risk_analysis_eligible": risk_eligible,
            "trade_amount_eligible": trade_amount_eligible,
            "execution_authorized": False,
            "reasons": gate_reasons,
        },
        "positions": positions,
        "provider_policy": {
            "stocks": "优先使用已配置的 Tushare/Massive/Alpha Vantage 未复权日线，失败后才使用明确标注的公开源。",
            "funds": "只使用基金确认单位净值，不把盘中估值当成正式市值。",
            "fx": "优先 Massive 前日汇率；不可用时使用 Frankfurter 汇总的央行参考汇率。",
            "cache": "每条价格、净值和汇率均持久化为不可变观察；刷新失败时只允许显式使用仍可识别的旧观察。",
        },
        "limitations": [
            "估值用于组合风险和配置复盘，不等于券商可成交金额。",
            "未计入盘中滑点、税费、停牌、申赎确认和券商现金余额。",
            "系统不自动交易；交易金额门禁通过也仍需用户人工确认。",
        ],
    }
    return repo.create_snapshot(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id or user_id,
        holdings_sha256=holdings_sha,
        status=status,
        payload=payload,
    )


def latest_portfolio_valuation(
    *,
    user_id: str,
    tenant_id: str = "public",
    repository: PortfolioValuationRepository | None = None,
    holdings: list[dict[str, Any]] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    repo = repository or PortfolioValuationRepository()
    items = list(holdings if holdings is not None else storage.list_holdings(user_id=user_id))
    current_hash = holdings_fingerprint(items)
    snapshot = repo.latest_snapshot(tenant_id=tenant_id, user_id=user_id)
    if snapshot is None:
        return {
            "status": "empty",
            "snapshot": None,
            "binding": {"current": False, "reason": "尚未生成组合估值快照"},
            "runtime_gate": {
                "risk_analysis_eligible": False,
                "trade_amount_eligible": False,
                "execution_authorized": False,
            },
        }
    payload = snapshot.get("payload") or {}
    holdings_current = snapshot.get("holdings_sha256") == current_hash
    fresh_until = _parse_datetime(snapshot.get("fresh_until") or payload.get("fresh_until"))
    fresh = bool(fresh_until and fresh_until > current)
    integrity = bool((snapshot.get("integrity") or {}).get("verified"))
    stored_gate = payload.get("decision_gate") or {}
    runtime_eligible = bool(
        holdings_current
        and fresh
        and integrity
        and stored_gate.get("risk_analysis_eligible")
    )
    reasons = []
    if not holdings_current:
        reasons.append("持仓已变化，快照不再绑定当前组合")
    if not fresh:
        reasons.append("估值快照已过期，需要重新刷新")
    if not integrity:
        reasons.append("估值快照完整性校验失败")
    reasons.extend(str(item) for item in stored_gate.get("reasons") or [])
    return {
        "status": "available",
        "snapshot": snapshot,
        "binding": {
            "current": holdings_current,
            "current_holdings_sha256": current_hash,
            "snapshot_holdings_sha256": snapshot.get("holdings_sha256"),
        },
        "runtime_gate": {
            "risk_analysis_eligible": runtime_eligible,
            "trade_amount_eligible": bool(
                runtime_eligible and stored_gate.get("trade_amount_eligible")
            ),
            "execution_authorized": False,
            "fresh": fresh,
            "integrity_verified": integrity,
            "reasons": list(dict.fromkeys(reasons)),
        },
    }


def overlay_insights_with_valuation(
    insights: dict[str, Any],
    valuation: dict[str, Any],
) -> dict[str, Any]:
    """Use the current immutable valuation for allocation math, preserving research fields."""
    if valuation.get("status") != "available":
        return insights
    if not (valuation.get("binding") or {}).get("current"):
        return insights
    snapshot = valuation.get("snapshot") or {}
    payload = snapshot.get("payload") or {}
    gate = valuation.get("runtime_gate") or {}
    if not gate.get("risk_analysis_eligible"):
        return {**insights, "valuation": valuation}

    positions = payload.get("positions") or []
    existing = {
        (str(row.get("asset_type") or ""), str(row.get("code") or "")): row
        for row in insights.get("allocation") or []
    }
    allocation = []
    for position in positions:
        key = (str(position.get("asset_type") or ""), str(position.get("code") or ""))
        prior = existing.get(key) or {}
        allocation.append({
            **prior,
            "asset_type": position.get("asset_type"),
            "market": position.get("market"),
            "code": position.get("code"),
            "name": position.get("name") or prior.get("name") or "",
            "amount": position.get("base_value"),
            "ratio": position.get("ratio"),
            "valuation_method": position.get("valuation_method"),
            "price_as_of": position.get("price_as_of"),
            "price_source": position.get("price_source"),
        })
    allocation.sort(key=lambda row: _number(row.get("amount")) or -1, reverse=True)
    ratios = [float(row.get("ratio") or 0) for row in allocation]
    summary = {
        **(insights.get("summary") or {}),
        "holding_count": len(allocation),
        "total_amount": (payload.get("summary") or {}).get("total_value"),
        "top1_ratio": ratios[0] if ratios else None,
        "top3_ratio": round(sum(ratios[:3]), 2) if ratios else None,
        "hhi": round(sum((ratio / 100) ** 2 for ratio in ratios), 4) if ratios else None,
        "valuation_snapshot_id": snapshot.get("id"),
        "valuation_as_of": snapshot.get("created_at"),
    }
    return {
        **insights,
        "source": "不可变组合估值快照 / " + str(insights.get("source") or "用户持仓"),
        "summary": summary,
        "allocation": allocation,
        "valuation": valuation,
    }


def apply_valuation_to_holdings(
    holdings: list[dict[str, Any]],
    valuation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return copies with current CNY valuation amounts when the runtime gate passes."""
    if valuation.get("status") != "available":
        return [dict(item) for item in holdings]
    if not (valuation.get("binding") or {}).get("current"):
        return [dict(item) for item in holdings]
    if not (valuation.get("runtime_gate") or {}).get("risk_analysis_eligible"):
        return [dict(item) for item in holdings]
    positions = (((valuation.get("snapshot") or {}).get("payload") or {}).get("positions") or [])
    by_id = {int(item.get("holding_id") or 0): item for item in positions}
    result = []
    for holding in holdings:
        item = dict(holding)
        position = by_id.get(int(item.get("id") or 0))
        if position and _number(position.get("base_value")) is not None:
            item["amount"] = position.get("base_value")
            item["valuation_snapshot_id"] = (valuation.get("snapshot") or {}).get("id")
            item["valuation_method"] = position.get("valuation_method")
            item["valuation_price_as_of"] = position.get("price_as_of")
            item["valuation_currency"] = BASE_CURRENCY
        result.append(item)
    return result


def current_valued_holdings(
    *,
    user_id: str,
    tenant_id: str = "public",
    repository: PortfolioValuationRepository | None = None,
    holdings: list[dict[str, Any]] | None = None,
    now: dt.datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = list(holdings if holdings is not None else storage.list_holdings(user_id=user_id))
    valuation = latest_portfolio_valuation(
        user_id=user_id,
        tenant_id=tenant_id,
        repository=repository,
        holdings=items,
        now=now,
    )
    return apply_valuation_to_holdings(items, valuation), valuation
