# -*- coding: utf-8 -*-
"""Shared exact-date benchmark calculations for immutable fund outcomes."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


PEER_COMPARATOR_TYPE = "provider_same_category_average"


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def return_pct(baseline: float, value: float) -> float:
    return round((value / baseline - 1) * 100, 4)


def comparison_points(
    peer_series: dict[str, Any] | None,
    key: str,
) -> dict[str, float]:
    points: dict[str, float] = {}
    for item in (peer_series or {}).get(key) or []:
        observed_date = parse_date(item.get("date"))
        value = number(item.get("cumulative_return_pct"))
        if observed_date is None or value is None or value <= -100:
            continue
        points[observed_date.isoformat()] = value
    return points


def relative_return(fund_return_pct: float, peer_return_pct: float) -> float | None:
    peer_gross = 1 + peer_return_pct / 100
    if peer_gross <= 0:
        return None
    return round(((1 + fund_return_pct / 100) / peer_gross - 1) * 100, 4)


def peer_period(
    *,
    peer_lookup: dict[str, float],
    fund_lookup: dict[str, float],
    name: str,
    source: str | None,
    source_url: str | None,
    baseline_as_of: str,
    observed_as_of: str | None,
    fund_return_pct: float | None,
    unavailable_reason: str | None,
) -> dict[str, Any]:
    base = {
        "type": PEER_COMPARATOR_TYPE,
        "name": name or "同类平均",
        "source": source,
        "source_url": source_url,
        "alignment": "exact_provider_date_only",
    }
    if not peer_lookup or not fund_lookup:
        return {
            **base,
            "status": "unavailable",
            "reason": unavailable_reason or "provider_comparable_series_missing",
        }
    baseline_peer_value = peer_lookup.get(baseline_as_of)
    baseline_fund_value = fund_lookup.get(baseline_as_of)
    if baseline_peer_value is None or baseline_fund_value is None:
        return {
            **base,
            "status": "unavailable",
            "reason": "baseline_date_not_in_provider_comparable_series",
        }
    baseline = {
        "as_of": baseline_as_of,
        "peer_cumulative_return_pct": round(baseline_peer_value, 4),
        "fund_cumulative_return_pct": round(baseline_fund_value, 4),
    }
    if observed_as_of is None or fund_return_pct is None:
        return {
            **base,
            "status": "pending",
            "reason": "waiting_for_target_confirmed_nav",
            "baseline": baseline,
        }
    observed_peer_value = peer_lookup.get(observed_as_of)
    observed_fund_value = fund_lookup.get(observed_as_of)
    if observed_peer_value is None or observed_fund_value is None:
        return {
            **base,
            "status": "unavailable",
            "reason": "observed_date_not_in_provider_comparable_series",
            "baseline": baseline,
        }
    peer_baseline_gross = 1 + baseline_peer_value / 100
    peer_observed_gross = 1 + observed_peer_value / 100
    fund_baseline_gross = 1 + baseline_fund_value / 100
    fund_observed_gross = 1 + observed_fund_value / 100
    if min(
        peer_baseline_gross,
        peer_observed_gross,
        fund_baseline_gross,
        fund_observed_gross,
    ) <= 0:
        return {
            **base,
            "status": "unavailable",
            "reason": "invalid_provider_comparable_cumulative_return",
            "baseline": baseline,
        }
    peer_return = round(
        (peer_observed_gross / peer_baseline_gross - 1) * 100,
        4,
    )
    provider_fund_return = round(
        (fund_observed_gross / fund_baseline_gross - 1) * 100,
        4,
    )
    return {
        **base,
        "status": "available",
        "reason": None,
        "baseline": baseline,
        "observed": {
            "as_of": observed_as_of,
            "peer_cumulative_return_pct": round(observed_peer_value, 4),
            "fund_cumulative_return_pct": round(observed_fund_value, 4),
        },
        "period_return_pct": peer_return,
        "fund_return_pct": provider_fund_return,
        "unit_nav_return_pct": round(fund_return_pct, 4),
        "return_spread_pp": round(provider_fund_return - peer_return, 4),
        "relative_excess_return_pct": relative_return(provider_fund_return, peer_return),
        "return_basis": "provider_comparable_cumulative_return_series",
    }
