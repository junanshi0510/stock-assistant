# -*- coding: utf-8 -*-
"""Historical forward-return evidence for the fund's current condition."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from bisect import bisect_left
from typing import Any, Iterable


STRATEGY_ID = "fund_conditioned_forward_return"
STRATEGY_VERSION = "1.0.0"
MIN_ANALOG_SAMPLES = 6
_MA_WINDOW = 60
_HORIZONS = (
    ("3m", 63),
    ("6m", 126),
    ("12m", 252),
)


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


def _parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _normalize_points(points: Iterable[Any]) -> list[tuple[dt.date, float]]:
    by_date: dict[dt.date, float] = {}
    for item in points:
        if isinstance(item, dict):
            date_value = item.get("date")
            nav_value = item.get("unit_nav")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            date_value, nav_value = item[0], item[1]
        else:
            continue
        parsed_date = _parse_date(date_value)
        parsed_nav = _number(nav_value)
        if parsed_date is not None and parsed_nav is not None:
            by_date[parsed_date] = parsed_nav
    return sorted(by_date.items())


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "sample_count": 0,
            "positive_rate": None,
            "average_return": None,
            "median_return": None,
            "p25_return": None,
            "p75_return": None,
            "best_return": None,
            "worst_return": None,
        }
    return {
        "sample_count": len(values),
        "positive_rate": _round(sum(value > 0 for value in values) / len(values) * 100),
        "average_return": _round(statistics.fmean(values)),
        "median_return": _round(statistics.median(values)),
        "p25_return": _round(_quantile(values, 0.25)),
        "p75_return": _round(_quantile(values, 0.75)),
        "best_return": _round(max(values)),
        "worst_return": _round(min(values)),
    }


def _classify_normalized(
    normalized: list[tuple[dt.date, float]],
    index: int | None = None,
) -> dict[str, Any] | None:
    position = len(normalized) - 1 if index is None else int(index)
    if position < _MA_WINDOW - 1 or position >= len(normalized):
        return None

    dates = [item[0] for item in normalized]
    nav = [item[1] for item in normalized]
    current = nav[position]
    ma60 = statistics.fmean(nav[position - _MA_WINDOW + 1: position + 1])
    peak = max(nav[: position + 1])
    drawdown = (current / peak - 1) * 100
    target_date = dates[position] - dt.timedelta(days=90)
    base_index = bisect_left(dates, target_date, 0, position + 1)
    return_3m = (
        (current / nav[base_index] - 1) * 100
        if base_index < position and nav[base_index] > 0
        else None
    )
    if drawdown <= -15:
        drawdown_band = "deep_drawdown"
    elif drawdown <= -5:
        drawdown_band = "normal_pullback"
    else:
        drawdown_band = "near_high"
    return {
        "as_of": dates[position].isoformat(),
        "latest_nav": _round(current, 4),
        "ma60": _round(ma60, 4),
        "trend": "above_ma60" if current >= ma60 else "below_ma60",
        "drawdown_band": drawdown_band,
        "current_drawdown": _round(drawdown),
        "return_3m": _round(return_3m),
    }


def classify_condition(points: Iterable[Any], index: int | None = None) -> dict[str, Any] | None:
    """Classify one point using only observations available at that point."""
    return _classify_normalized(_normalize_points(points), index)


def _monthly_endpoint_indices(points: list[tuple[dt.date, float]]) -> list[int]:
    endpoints: dict[tuple[int, int], int] = {}
    for index, (date_value, _) in enumerate(points):
        endpoints[(date_value.year, date_value.month)] = index
    return sorted(endpoints.values())


def _empty_result(points: list[tuple[dt.date, float]], reason: str) -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "status": "insufficient_data",
        "decision": "data_required",
        "signal": {"direction": "unavailable", "strength": None},
        "confidence": {"level": "unavailable", "reasons": [reason]},
        "suitability": {
            "status": "not_evaluated",
            "conflicts": ["user_profile_not_in_scope", "portfolio_exposure_not_in_scope"],
        },
        "condition": _classify_normalized(points),
        "primary_horizon": None,
        "horizons": [],
        "coverage": {
            "observation_count": len(points),
            "start_date": points[0][0].isoformat() if points else None,
            "end_date": points[-1][0].isoformat() if points else None,
        },
        "invalidation_conditions": [],
        "thesis": [],
        "counter_evidence": [],
        "risks": ["insufficient_historical_analogs"],
        "next_research_actions": ["collect_more_confirmed_nav"],
        "evidence_ids": [],
        "method": {
            "sampling": "calendar_month_last_observation",
            "matching_fields": ["trend", "drawdown_band"],
            "minimum_analog_samples": MIN_ANALOG_SAMPLES,
        },
        "limitations": [
            "historical_results_are_not_forecasts",
            "single_fund_history_may_include_regime_changes",
            "forward_windows_can_overlap",
        ],
    }


def unavailable_conditioned_forward(reason: str) -> dict[str, Any]:
    """Return an explicit unavailable result without fabricating historical samples."""
    result = _empty_result([], reason)
    result["status"] = "unavailable"
    result["reason"] = reason
    return result


def evaluate_conditioned_forward_strategy(points: Iterable[Any]) -> dict[str, Any]:
    """Compare forward returns after historical month-ends matching today's condition."""
    normalized = _normalize_points(points)
    if len(normalized) < _MA_WINDOW:
        return _empty_result(normalized, "history_shorter_than_ma_window")

    current_condition = _classify_normalized(normalized)
    if current_condition is None:
        return _empty_result(normalized, "current_condition_unavailable")

    nav = [item[1] for item in normalized]
    monthly_indices = [
        index
        for index in _monthly_endpoint_indices(normalized)
        if index >= _MA_WINDOW - 1
    ]
    horizons = []
    for horizon, observation_days in _HORIZONS:
        baseline_returns = []
        analog_returns = []
        analog_dates = []
        for index in monthly_indices:
            future_index = index + observation_days
            if future_index >= len(normalized):
                continue
            forward_return = (nav[future_index] / nav[index] - 1) * 100
            baseline_returns.append(forward_return)
            historical_condition = _classify_normalized(normalized, index)
            if (
                historical_condition
                and historical_condition["trend"] == current_condition["trend"]
                and historical_condition["drawdown_band"] == current_condition["drawdown_band"]
            ):
                analog_returns.append(forward_return)
                analog_dates.append(normalized[index][0])

        analog = _distribution(analog_returns)
        baseline = _distribution(baseline_returns)
        analog["sample_start"] = min(analog_dates).isoformat() if analog_dates else None
        analog["sample_end"] = max(analog_dates).isoformat() if analog_dates else None
        sufficient = analog["sample_count"] >= MIN_ANALOG_SAMPLES
        horizons.append({
            "horizon": horizon,
            "observation_days": observation_days,
            "status": "available" if sufficient else "insufficient_samples",
            "analog": analog,
            "baseline": baseline,
            "edge": {
                "positive_rate": _round(
                    analog["positive_rate"] - baseline["positive_rate"]
                    if analog["positive_rate"] is not None and baseline["positive_rate"] is not None
                    else None
                ),
                "median_return": _round(
                    analog["median_return"] - baseline["median_return"]
                    if analog["median_return"] is not None and baseline["median_return"] is not None
                    else None
                ),
            },
        })

    by_horizon = {item["horizon"]: item for item in horizons}
    primary = next(
        (
            by_horizon[key]
            for key in ("6m", "3m", "12m")
            if by_horizon[key]["status"] == "available"
        ),
        None,
    )
    if primary is None:
        result = _empty_result(normalized, "analog_samples_below_minimum")
        result["condition"] = current_condition
        result["horizons"] = horizons
        result["coverage"]["monthly_candidate_count"] = len(monthly_indices)
        return result

    analog = primary["analog"]
    positive_rate = float(analog["positive_rate"])
    median_return = float(analog["median_return"])
    if positive_rate >= 55 and median_return > 0:
        direction = "positive"
        decision = "research"
    elif positive_rate <= 45 and median_return < 0:
        direction = "negative"
        decision = "avoid_for_now"
    else:
        direction = "mixed"
        decision = "hold_review"
    signal_strength = int(max(0, min(100, round(abs(positive_rate - 50) * 2))))

    history_years = (normalized[-1][0] - normalized[0][0]).days / 365.25
    if analog["sample_count"] >= 18 and history_years >= 5:
        confidence_level = "medium"
    else:
        confidence_level = "low"
    confidence_reasons = [
        f"analog_sample_count:{analog['sample_count']}",
        f"history_years:{history_years:.1f}",
        "monthly_sampling_with_overlapping_forward_windows",
    ]
    thesis = [
        {"code": "analog_positive_rate", "value": analog["positive_rate"], "unit": "%"},
        {"code": "analog_median_return", "value": analog["median_return"], "unit": "%"},
    ]
    counter_evidence = []
    if primary["edge"]["positive_rate"] is not None and primary["edge"]["positive_rate"] < 0:
        counter_evidence.append({
            "code": "positive_rate_below_unconditional_baseline",
            "value": primary["edge"]["positive_rate"],
            "unit": "%",
        })
    if analog["p25_return"] is not None and analog["p25_return"] < 0:
        counter_evidence.append({
            "code": "lower_quartile_is_negative",
            "value": analog["p25_return"],
            "unit": "%",
        })

    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "status": "evaluated",
        "decision": decision,
        "signal": {
            "direction": direction,
            "strength": signal_strength,
            "strength_method": "absolute_distance_of_positive_rate_from_50_percent",
        },
        "confidence": {
            "level": confidence_level,
            "reasons": confidence_reasons,
        },
        "suitability": {
            "status": "not_evaluated",
            "conflicts": ["user_profile_not_in_scope", "portfolio_exposure_not_in_scope"],
        },
        "condition": current_condition,
        "primary_horizon": primary["horizon"],
        "horizons": horizons,
        "coverage": {
            "observation_count": len(normalized),
            "start_date": normalized[0][0].isoformat(),
            "end_date": normalized[-1][0].isoformat(),
            "monthly_candidate_count": len(monthly_indices),
        },
        "invalidation_conditions": [
            {"field": "trend", "current": current_condition["trend"], "invalid_when": "changes"},
            {
                "field": "drawdown_band",
                "current": current_condition["drawdown_band"],
                "invalid_when": "changes",
            },
            {"field": "as_of", "current": current_condition["as_of"], "invalid_when": "new_nav_published"},
        ],
        "thesis": thesis,
        "counter_evidence": counter_evidence,
        "risks": [
            "historical_tail_loss_remains_possible",
            "condition_match_does_not_measure_fundamental_change",
        ],
        "next_research_actions": [
            "re_evaluate_after_new_confirmed_nav",
            "verify_manager_and_portfolio_disclosure",
            "apply_user_risk_and_portfolio_constraints",
        ],
        "evidence_ids": [],
        "method": {
            "sampling": "calendar_month_last_observation",
            "matching_fields": ["trend", "drawdown_band"],
            "ma_window_observations": _MA_WINDOW,
            "minimum_analog_samples": MIN_ANALOG_SAMPLES,
            "horizon_observations": {key: days for key, days in _HORIZONS},
            "transaction_costs": "not_applicable_no_trading_simulation",
        },
        "limitations": [
            "historical_results_are_not_forecasts",
            "single_fund_history_may_include_regime_changes",
            "forward_windows_can_overlap",
            "fund_manager_or_mandate_changes_are_not_normalized",
        ],
    }
