# -*- coding: utf-8 -*-
"""Locate the last historical observation that reached a live price or NAV level."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any, Iterable


METRIC_ID = "asset_level_recurrence"
METRIC_VERSION = "1.0.0"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _date(value: Any) -> dt.date | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _days_ago(as_of: dt.date | None, occurrence: dt.date | None) -> int | None:
    if as_of is None or occurrence is None:
        return None
    return max(0, (as_of - occurrence).days)


def unavailable_level_recurrence(
    *,
    asset_type: str,
    reason: str,
    target_label: str,
    target_value: float | None = None,
    target_as_of: str | None = None,
    target_source: str | None = None,
) -> dict[str, Any]:
    return {
        "metric_id": METRIC_ID,
        "metric_version": METRIC_VERSION,
        "asset_type": asset_type,
        "status": "unavailable",
        "reason": reason,
        "target": {
            "label": target_label,
            "value": _round(_number(target_value)),
            "as_of": target_as_of,
            "source": target_source,
        },
        "history": None,
        "occurrence": None,
        "nearest": None,
    }


def evaluate_stock_level_recurrence(
    *,
    current_price: float,
    quote_as_of: str,
    quote_source: str,
    bars: Iterable[dict[str, Any]],
    history_source: str,
    market: str,
    symbol: str,
) -> dict[str, Any]:
    """Use unadjusted daily high/low ranges to find the prior trading day at a live price."""
    target = _number(current_price)
    if target is None:
        return unavailable_level_recurrence(
            asset_type="stock",
            reason="实时行情源没有返回有效成交价。",
            target_label="实时成交价",
            target_as_of=quote_as_of,
            target_source=quote_source,
        )

    as_of_date = _date(quote_as_of)
    normalized: list[dict[str, Any]] = []
    for item in bars:
        date = _date(item.get("date"))
        low = _number(item.get("low"))
        high = _number(item.get("high"))
        close = _number(item.get("close"))
        if date is None or low is None or high is None or close is None:
            continue
        if as_of_date is not None and date >= as_of_date:
            continue
        normalized.append({
            "date": date,
            "low": min(low, high),
            "high": max(low, high),
            "close": close,
        })
    normalized.sort(key=lambda item: item["date"])
    if not normalized:
        return unavailable_level_recurrence(
            asset_type="stock",
            reason="没有早于实时报价日期的未复权历史日线。",
            target_label="实时成交价",
            target_value=target,
            target_as_of=quote_as_of,
            target_source=quote_source,
        )

    matched = next(
        (item for item in reversed(normalized) if item["low"] <= target <= item["high"]),
        None,
    )
    nearest = min(
        normalized,
        key=lambda item: (
            (
                item["low"] - target
                if target < item["low"]
                else target - item["high"]
                if target > item["high"]
                else 0
            ),
            -item["date"].toordinal(),
        ),
    )
    nearest_value = (
        nearest["low"]
        if target < nearest["low"]
        else nearest["high"]
        if target > nearest["high"]
        else target
    )
    result = {
        "metric_id": METRIC_ID,
        "metric_version": METRIC_VERSION,
        "asset_type": "stock",
        "market": market,
        "symbol": symbol,
        "status": "reached" if matched else "not_found_in_coverage",
        "target": {
            "label": "实时成交价",
            "value": _round(target),
            "as_of": quote_as_of,
            "source": quote_source,
        },
        "history": {
            "source": history_source,
            "adjustment": "none",
            "granularity": "trading_day_high_low",
            "observation_count": len(normalized),
            "start_date": normalized[0]["date"].isoformat(),
            "end_date": normalized[-1]["date"].isoformat(),
        },
        "occurrence": None,
        "nearest": {
            "date": nearest["date"].isoformat(),
            "value": _round(nearest_value),
            "difference": _round(nearest_value - target),
            "low": _round(nearest["low"]),
            "high": _round(nearest["high"]),
            "close": _round(nearest["close"]),
        },
        "method": {
            "match": "current_price_inside_prior_unadjusted_daily_low_high_range",
            "current_day_excluded": True,
        },
        "policy": "历史日线只能确认某个交易日曾覆盖该价位，不能还原当日的精确分钟或成交时刻。",
    }
    if matched:
        result["occurrence"] = {
            "kind": "daily_range",
            "date": matched["date"].isoformat(),
            "low": _round(matched["low"]),
            "high": _round(matched["high"]),
            "close": _round(matched["close"]),
            "calendar_days_ago": _days_ago(as_of_date, matched["date"]),
        }
    return result


def evaluate_fund_level_recurrence(
    *,
    estimate_nav: float,
    estimate_as_of: str,
    estimate_source: str,
    points: Iterable[dict[str, Any]],
    history_source: str,
    code: str,
) -> dict[str, Any]:
    """Find an exact confirmed NAV or the latest confirmed-NAV interval crossing an estimate."""
    target = _number(estimate_nav)
    if target is None:
        return unavailable_level_recurrence(
            asset_type="fund",
            reason="估值源没有返回有效盘中估算净值。",
            target_label="盘中估算净值",
            target_as_of=estimate_as_of,
            target_source=estimate_source,
        )

    as_of_date = _date(estimate_as_of)
    by_date: dict[dt.date, float] = {}
    for item in points:
        date = _date(item.get("date"))
        value = _number(item.get("unit_nav"))
        if date is None or value is None:
            continue
        if as_of_date is not None and date >= as_of_date:
            continue
        by_date[date] = value
    normalized = sorted(by_date.items())
    if not normalized:
        return unavailable_level_recurrence(
            asset_type="fund",
            reason="没有早于盘中估值日期的确认净值。",
            target_label="盘中估算净值",
            target_value=target,
            target_as_of=estimate_as_of,
            target_source=estimate_source,
        )

    precision_tolerance = 0.00005
    exact = next(
        (
            (date, value)
            for date, value in reversed(normalized)
            if abs(value - target) <= precision_tolerance
        ),
        None,
    )
    crossing = None
    for index in range(len(normalized) - 1, 0, -1):
        from_date, from_value = normalized[index - 1]
        to_date, to_value = normalized[index]
        if min(from_value, to_value) < target < max(from_value, to_value):
            crossing = (from_date, from_value, to_date, to_value)
            break

    if exact is not None and crossing is not None and crossing[2] > exact[0]:
        exact = None
    elif exact is not None:
        crossing = None

    nearest_date, nearest_value = min(normalized, key=lambda item: abs(item[1] - target))
    status = "reached_exact" if exact else "crossed_between" if crossing else "not_found_in_coverage"
    occurrence = None
    if exact:
        occurrence = {
            "kind": "exact_observation",
            "date": exact[0].isoformat(),
            "value": _round(exact[1]),
            "calendar_days_ago": _days_ago(as_of_date, exact[0]),
        }
    elif crossing:
        occurrence = {
            "kind": "crossing_interval",
            "from_date": crossing[0].isoformat(),
            "from_value": _round(crossing[1]),
            "to_date": crossing[2].isoformat(),
            "to_value": _round(crossing[3]),
            "direction": "up" if crossing[3] > crossing[1] else "down",
            "calendar_days_ago": _days_ago(as_of_date, crossing[2]),
        }

    return {
        "metric_id": METRIC_ID,
        "metric_version": METRIC_VERSION,
        "asset_type": "fund",
        "code": code,
        "status": status,
        "target": {
            "label": "盘中估算净值",
            "value": _round(target),
            "as_of": estimate_as_of,
            "source": estimate_source,
        },
        "history": {
            "source": history_source,
            "adjustment": "confirmed_unit_nav",
            "granularity": "confirmed_nav_date",
            "observation_count": len(normalized),
            "start_date": normalized[0][0].isoformat(),
            "end_date": normalized[-1][0].isoformat(),
        },
        "occurrence": occurrence,
        "nearest": {
            "date": nearest_date.isoformat(),
            "value": _round(nearest_value),
            "difference": _round(nearest_value - target),
        },
        "method": {
            "exact_tolerance": precision_tolerance,
            "crossing": "target_strictly_between_two_consecutive_confirmed_nav_observations",
            "estimate_date_excluded": True,
        },
        "policy": "基金盘中估值不是确认净值；跨越结果只能定位到两个确认净值日期之间，不能伪造精确盘中时刻。",
    }
