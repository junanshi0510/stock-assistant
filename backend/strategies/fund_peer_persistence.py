# -*- coding: utf-8 -*-
"""Versioned relative-performance diagnostics against a provider-native peer series."""

from __future__ import annotations

import calendar
import datetime as dt
import math
from bisect import bisect_right
from typing import Any


DIAGNOSTIC_ID = "fund_peer_relative_persistence"
DIAGNOSTIC_VERSION = "1.0.0"
MIN_ALIGNED_OBSERVATIONS = 8
MAX_ENDPOINT_GAP_DAYS = 10
MATERIAL_12M_LAG_PP = -3.0
STAGE_CROSSCHECK_TOLERANCE_PP = 0.08
_HORIZONS = (("3m", 3), ("6m", 6), ("12m", 12))


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    number = float(value)
    return round(number, digits) if math.isfinite(number) else None


def _parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _cumulative_return(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > -100 else None


def _normalize(points: Any) -> dict[dt.date, float]:
    by_date: dict[dt.date, float] = {}
    for item in points if isinstance(points, list) else []:
        if not isinstance(item, dict):
            continue
        observed_on = _parse_date(item.get("date"))
        value = _cumulative_return(item.get("cumulative_return_pct"))
        if observed_on is not None and value is not None:
            by_date[observed_on] = value
    return by_date


def _subtract_months(value: dt.date, months: int) -> dt.date:
    month_index = value.year * 12 + value.month - 1 - int(months)
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def _endpoint_on_or_before(
    dates: list[dt.date],
    target: dt.date,
) -> dt.date | None:
    index = bisect_right(dates, target) - 1
    if index < 0:
        return None
    endpoint = dates[index]
    return endpoint if (target - endpoint).days <= MAX_ENDPOINT_GAP_DAYS else None


def _window(
    label: str,
    start: dt.date | None,
    end: dt.date | None,
    aligned: dict[dt.date, tuple[float, float]],
) -> dict[str, Any]:
    if start is None or end is None or start >= end:
        return {
            "window": label,
            "status": "insufficient_coverage",
            "reason": "aligned_endpoint_unavailable",
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
        }
    fund_start, peer_start = aligned[start]
    fund_end, peer_end = aligned[end]
    fund_index_start = 1 + fund_start / 100
    peer_index_start = 1 + peer_start / 100
    fund_index_end = 1 + fund_end / 100
    peer_index_end = 1 + peer_end / 100
    if min(fund_index_start, peer_index_start, fund_index_end, peer_index_end) <= 0:
        return {
            "window": label,
            "status": "insufficient_coverage",
            "reason": "invalid_total_return_index",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
    fund_return = (fund_index_end / fund_index_start - 1) * 100
    peer_return = (peer_index_end / peer_index_start - 1) * 100
    relative_return = (
        ((1 + fund_return / 100) / (1 + peer_return / 100) - 1) * 100
        if peer_return > -100
        else None
    )
    return {
        "window": label,
        "status": "available",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "calendar_days": (end - start).days,
        "fund_return_pct": _round(fund_return),
        "peer_return_pct": _round(peer_return),
        "excess_return_pp": _round(fund_return - peer_return),
        "relative_excess_return_pct": _round(relative_return),
    }


def _verified_stage_annual_window(
    stage_comparison: Any,
    horizons: list[dict[str, Any]],
    as_of: dt.date,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not isinstance(stage_comparison, dict) or stage_comparison.get("status") not in {
        "available",
        "partial",
    }:
        return None, {
            "status": "unavailable",
            "reason": str(
                (stage_comparison or {}).get("reason")
                or "provider_stage_comparison_missing"
                if isinstance(stage_comparison, dict)
                else "provider_stage_comparison_missing"
            ),
            "crosschecks": [],
        }
    periods = stage_comparison.get("periods") or {}
    horizon_map = {item.get("window"): item for item in horizons}
    crosschecks = []
    for label in ("3m", "6m"):
        aligned = horizon_map.get(label) or {}
        staged = periods.get(label) or {}
        fund_difference = (
            abs(float(aligned["fund_return_pct"]) - float(staged["fund_return_pct"]))
            if aligned.get("status") == "available"
            and aligned.get("fund_return_pct") is not None
            and staged.get("fund_return_pct") is not None
            else None
        )
        peer_difference = (
            abs(float(aligned["peer_return_pct"]) - float(staged["peer_return_pct"]))
            if aligned.get("status") == "available"
            and aligned.get("peer_return_pct") is not None
            and staged.get("peer_return_pct") is not None
            else None
        )
        matched = bool(
            fund_difference is not None
            and peer_difference is not None
            and fund_difference <= STAGE_CROSSCHECK_TOLERANCE_PP
            and peer_difference <= STAGE_CROSSCHECK_TOLERANCE_PP
        )
        crosschecks.append({
            "window": label,
            "status": "matched" if matched else "mismatch",
            "fund_difference_pp": _round(fund_difference, 4),
            "peer_difference_pp": _round(peer_difference, 4),
        })
    if len(crosschecks) != 2 or not all(
        item["status"] == "matched" for item in crosschecks
    ):
        return None, {
            "status": "rejected",
            "reason": "provider_stage_periods_failed_3m_6m_crosscheck",
            "tolerance_pp": STAGE_CROSSCHECK_TOLERANCE_PP,
            "crosschecks": crosschecks,
        }

    annual = periods.get("12m") or {}
    fund_return = _cumulative_return(annual.get("fund_return_pct"))
    peer_return = _cumulative_return(annual.get("peer_return_pct"))
    if fund_return is None or peer_return is None:
        return None, {
            "status": "verified_without_12m",
            "reason": "provider_stage_12m_missing",
            "tolerance_pp": STAGE_CROSSCHECK_TOLERANCE_PP,
            "crosschecks": crosschecks,
        }
    relative_return = (
        ((1 + fund_return / 100) / (1 + peer_return / 100) - 1) * 100
        if peer_return > -100
        else None
    )
    return {
        "window": "12m",
        "status": "available",
        "start_date": None,
        "end_date": as_of.isoformat(),
        "calendar_days": None,
        "fund_return_pct": _round(fund_return),
        "peer_return_pct": _round(peer_return),
        "excess_return_pp": _round(fund_return - peer_return),
        "relative_excess_return_pct": _round(relative_return),
        "period_basis": "provider_defined_trailing_period",
        "source": stage_comparison.get("source"),
        "source_url": stage_comparison.get("source_url"),
    }, {
        "status": "verified",
        "reason": None,
        "tolerance_pp": STAGE_CROSSCHECK_TOLERANCE_PP,
        "crosschecks": crosschecks,
    }


def _empty_result(
    reason: str,
    *,
    status: str = "insufficient_data",
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "status": status,
        "reason": reason,
        "as_of": None,
        "diagnosis": {
            "status": "data_required",
            "label": "同类诊断数据不足",
            "rationale": "没有形成可比较的真实同类持续性证据。",
        },
        "horizons": [],
        "quarters": [],
        "replacement_review": {
            "status": "not_evaluated",
            "triggered": False,
            "permitted_next_step": "collect_peer_history",
            "automatic_redemption_allowed": False,
            "checks": [],
        },
        "confidence": {"level": "unavailable", "reasons": [reason]},
        "coverage": coverage or {
            "aligned_observation_count": 0,
            "start_date": None,
            "end_date": None,
        },
        "method": {
            "alignment": "exact_common_provider_dates",
            "endpoint_sampling": "latest_common_date_on_or_before_target",
            "maximum_endpoint_gap_days": MAX_ENDPOINT_GAP_DAYS,
            "material_12m_lag_threshold_pp": MATERIAL_12M_LAG_PP,
        },
        "limitations": [
            "same_category_average_is_not_an_investable_benchmark",
            "past_relative_performance_is_not_a_forecast",
            "fees_taxes_and_redemption_costs_not_evaluated",
            "portfolio_overlap_and_manager_changes_not_evaluated",
        ],
    }


def unavailable_peer_persistence(reason: str) -> dict[str, Any]:
    """Return an explicit provider failure without creating proxy observations."""
    return _empty_result(reason, status="unavailable")


def evaluate_peer_persistence(peer_series: dict[str, Any]) -> dict[str, Any]:
    """Diagnose persistent peer lag without producing a sell instruction."""
    fund = _normalize(peer_series.get("fund_points"))
    peer = _normalize(peer_series.get("points"))
    common_dates = sorted(set(fund).intersection(peer))
    aligned = {date_value: (fund[date_value], peer[date_value]) for date_value in common_dates}
    coverage = {
        "aligned_observation_count": len(common_dates),
        "fund_observation_count": len(fund),
        "peer_observation_count": len(peer),
        "start_date": common_dates[0].isoformat() if common_dates else None,
        "end_date": common_dates[-1].isoformat() if common_dates else None,
    }
    if len(common_dates) < MIN_ALIGNED_OBSERVATIONS:
        return _empty_result("aligned_observations_below_minimum", coverage=coverage)

    as_of = common_dates[-1]
    endpoints = {0: as_of}
    for months in (3, 6, 12):
        endpoints[months] = _endpoint_on_or_before(
            common_dates,
            _subtract_months(as_of, months),
        )

    horizons = [
        _window(label, endpoints[months], as_of, aligned)
        for label, months in _HORIZONS
    ]
    exact_annual = next(item for item in horizons if item["window"] == "12m")
    if exact_annual["status"] == "available":
        stage_validation = {
            "status": "not_required",
            "reason": "exact_common_date_12m_window_available",
            "crosschecks": [],
        }
    else:
        stage_annual, stage_validation = _verified_stage_annual_window(
            peer_series.get("stage_comparison"),
            horizons,
            as_of,
        )
        if stage_annual is not None:
            horizons = [
                stage_annual if item["window"] == "12m" else item
                for item in horizons
            ]
    quarters = [
        _window("latest_3m", endpoints[3], as_of, aligned),
        _window("previous_3m", endpoints[6], endpoints[3], aligned),
    ]
    available_horizons = [item for item in horizons if item["status"] == "available"]
    available_quarters = [item for item in quarters if item["status"] == "available"]
    if len(available_horizons) < 2 or len(available_quarters) < 2:
        result = _empty_result("comparable_windows_below_minimum", coverage=coverage)
        result["as_of"] = as_of.isoformat()
        result["horizons"] = horizons
        result["quarters"] = quarters
        result["stage_validation"] = stage_validation
        return result

    horizon_map = {item["window"]: item for item in available_horizons}
    latest_quarter, previous_quarter = quarters
    two_quarter_lag = all(
        float(item.get("excess_return_pp") or 0) < 0
        for item in (latest_quarter, previous_quarter)
    )
    annual = horizon_map.get("12m")
    material_annual_lag = bool(
        annual
        and annual.get("excess_return_pp") is not None
        and float(annual["excess_return_pp"]) <= MATERIAL_12M_LAG_PP
    )
    review_triggered = two_quarter_lag and material_annual_lag
    negative_horizons = sum(
        float(item.get("excess_return_pp") or 0) < 0 for item in available_horizons
    )
    positive_horizons = sum(
        float(item.get("excess_return_pp") or 0) > 0 for item in available_horizons
    )

    if review_triggered:
        diagnosis_status = "replacement_review"
        label = "进入替代审查"
        rationale = "连续两个互不重叠的近三个月窗口跑输同类，且近一年落后达到审查阈值。"
    elif two_quarter_lag or negative_horizons >= 2:
        diagnosis_status = "underperformance_watch"
        label = "持续性偏弱，继续复核"
        rationale = "相对同类的多个窗口偏弱，但尚未同时满足完整替代审查门槛。"
    elif positive_horizons >= 2 and float(latest_quarter.get("excess_return_pp") or 0) > 0:
        diagnosis_status = "relative_strength"
        label = "相对同类保持优势"
        rationale = "多数可比窗口及最近三个月相对同类为正，暂未触发替代审查。"
    else:
        diagnosis_status = "mixed"
        label = "相对表现分化"
        rationale = "不同窗口方向不一致，当前不能把短期结果解释为稳定选基能力。"

    history_years = (common_dates[-1] - common_dates[0]).days / 365.25
    confidence_level = (
        "medium"
        if len(common_dates) >= 500 and history_years >= 3 and len(available_horizons) == 3
        else "low"
    )
    checks = [
        {
            "code": "two_consecutive_non_overlapping_quarters",
            "status": "pass" if two_quarter_lag else "fail",
            "label": "连续两个三个月窗口跑输同类",
            "observed": [
                latest_quarter.get("excess_return_pp"),
                previous_quarter.get("excess_return_pp"),
            ],
            "unit": "percentage_points",
        },
        {
            "code": "material_trailing_12m_lag",
            "status": (
                "pass" if material_annual_lag else "fail" if annual else "unavailable"
            ),
            "label": "近一年显著跑输同类",
            "observed": annual.get("excess_return_pp") if annual else None,
            "threshold": MATERIAL_12M_LAG_PP,
            "unit": "percentage_points",
        },
        {
            "code": "investable_alternative_verified",
            "status": "pending",
            "label": "可投资替代品收益、风险与重合度已核验",
        },
        {
            "code": "switching_costs_verified",
            "status": "pending",
            "label": "申赎费、税费、份额类别与机会成本已核验",
        },
    ]
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "status": "evaluated",
        "reason": None,
        "as_of": as_of.isoformat(),
        "diagnosis": {
            "status": diagnosis_status,
            "label": label,
            "rationale": rationale,
        },
        "horizons": horizons,
        "quarters": quarters,
        "replacement_review": {
            "status": "triggered" if review_triggered else "not_triggered",
            "triggered": review_triggered,
            "permitted_next_step": (
                "compare_real_alternatives" if review_triggered else "continue_monitoring"
            ),
            "automatic_redemption_allowed": False,
            "checks": checks,
        },
        "confidence": {
            "level": confidence_level,
            "reasons": [
                f"aligned_observation_count:{len(common_dates)}",
                f"history_years:{history_years:.1f}",
                "provider_native_same_category_average",
            ],
        },
        "coverage": coverage,
        "stage_validation": stage_validation,
        "method": {
            "alignment": "exact_common_provider_dates",
            "endpoint_sampling": "latest_common_date_on_or_before_target",
            "maximum_endpoint_gap_days": MAX_ENDPOINT_GAP_DAYS,
            "period_return": "ratio_of_provider_cumulative_total_return_indices",
            "quarter_persistence": "two_non_overlapping_trailing_three_month_windows",
            "annual_stage_supplement": "accepted_only_after_3m_and_6m_provider_crosscheck",
            "stage_crosscheck_tolerance_pp": STAGE_CROSSCHECK_TOLERANCE_PP,
            "material_12m_lag_threshold_pp": MATERIAL_12M_LAG_PP,
        },
        "limitations": [
            "same_category_average_is_not_an_investable_benchmark",
            "past_relative_performance_is_not_a_forecast",
            "fees_taxes_and_redemption_costs_not_evaluated",
            "portfolio_overlap_and_manager_changes_not_evaluated",
        ],
    }
