# -*- coding: utf-8 -*-
"""Versioned rolling durability gate for real fund replacement candidates."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any


DIAGNOSTIC_ID = "fund_alternative_durability"
DIAGNOSTIC_VERSION = "1.0.0"
MIN_DAILY_OBSERVATIONS = 120
MIN_MONTHLY_ENDPOINTS = 25
MIN_6M_WINDOWS = 18
MIN_12M_WINDOWS = 12
WIN_RATE_THRESHOLD_PCT = 60.0
MAX_EXTRA_DRAWDOWN_PP = 5.0
HOT_RETURN_PERCENTILE = 90.0
HOT_RETURN_ABOVE_MEDIAN_PP = 2.0


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    number = float(value)
    return round(number, digits) if math.isfinite(number) else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _daily_return(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > -100 else None


def _total_return_index(payload: dict[str, Any]) -> tuple[dict[dt.date, float] | None, dict[str, Any]]:
    parsed = []
    seen_dates = set()
    duplicate_dates = []
    for item in payload.get("points") if isinstance(payload.get("points"), list) else []:
        if not isinstance(item, dict):
            continue
        observed_on = _date(item.get("date"))
        if observed_on is None:
            continue
        if observed_on in seen_dates:
            duplicate_dates.append(observed_on.isoformat())
            continue
        seen_dates.add(observed_on)
        parsed.append((observed_on, _daily_return(item.get("daily_return_pct"))))
    parsed.sort(key=lambda item: item[0])
    quality = {
        "observation_count": len(parsed),
        "start_date": parsed[0][0].isoformat() if parsed else None,
        "end_date": parsed[-1][0].isoformat() if parsed else None,
        "duplicate_dates": duplicate_dates[:5],
        "missing_return_dates": [],
        "method": "compound_provider_daily_return",
    }
    if duplicate_dates:
        quality["status"] = "invalid"
        quality["reason"] = "duplicate_provider_dates"
        return None, quality
    if len(parsed) < MIN_DAILY_OBSERVATIONS:
        quality["status"] = "insufficient"
        quality["reason"] = "daily_observations_below_minimum"
        return None, quality

    index_value = 1.0
    index_by_date: dict[dt.date, float] = {}
    for position, (observed_on, daily_return) in enumerate(parsed):
        if daily_return is None:
            if position == 0:
                index_by_date[observed_on] = index_value
                continue
            quality["missing_return_dates"].append(observed_on.isoformat())
            continue
        index_value *= 1 + daily_return / 100
        if not math.isfinite(index_value) or index_value <= 0:
            quality["status"] = "invalid"
            quality["reason"] = "invalid_compounded_total_return_index"
            return None, quality
        index_by_date[observed_on] = index_value
    if quality["missing_return_dates"]:
        quality["status"] = "invalid"
        quality["reason"] = "provider_daily_return_gap"
        quality["missing_return_dates"] = quality["missing_return_dates"][:10]
        return None, quality
    quality["status"] = "complete"
    quality["reason"] = None
    return index_by_date, quality


def _monthly_common_endpoints(
    selected: dict[dt.date, float],
    candidate: dict[dt.date, float],
) -> tuple[dict[int, dt.date], list[dt.date]]:
    common_dates = sorted(set(selected).intersection(candidate))
    month_endpoints = {}
    for observed_on in common_dates:
        month_endpoints[observed_on.year * 12 + observed_on.month - 1] = observed_on
    return month_endpoints, common_dates


def _rolling_windows(
    selected: dict[dt.date, float],
    candidate: dict[dt.date, float],
    endpoints: dict[int, dt.date],
    months: int,
) -> list[dict[str, Any]]:
    rows = []
    for end_month in sorted(endpoints):
        start_month = end_month - months
        if start_month not in endpoints:
            continue
        start_date = endpoints[start_month]
        end_date = endpoints[end_month]
        selected_return = (selected[end_date] / selected[start_date] - 1) * 100
        candidate_return = (candidate[end_date] / candidate[start_date] - 1) * 100
        rows.append({
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "selected_return_pct": _round(selected_return),
            "candidate_return_pct": _round(candidate_return),
            "excess_return_pp": _round(candidate_return - selected_return),
        })
    return rows


def _percentile_rank(values: list[float], current: float | None) -> float | None:
    if not values or current is None:
        return None
    return sum(value <= current for value in values) / len(values) * 100


def _window_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "sample_count": 0,
            "win_rate_pct": None,
            "median_excess_pp": None,
            "lower_quartile_excess_pp": None,
            "worst_excess_pp": None,
            "downside_protection_rate_pct": None,
            "selected_negative_window_count": 0,
            "candidate_return_percentile": None,
            "candidate_median_return_pct": None,
            "latest": None,
            "recent_windows": [],
        }
    excess = [float(row["excess_return_pp"]) for row in rows]
    candidate_returns = [float(row["candidate_return_pct"]) for row in rows]
    selected_negative = [row for row in rows if float(row["selected_return_pct"]) < 0]
    latest = rows[-1]
    lower_quartile = (
        statistics.quantiles(excess, n=4, method="inclusive")[0]
        if len(excess) >= 2
        else excess[0]
    )
    return {
        "sample_count": len(rows),
        "win_rate_pct": _round(sum(value > 0 for value in excess) / len(excess) * 100),
        "median_excess_pp": _round(statistics.median(excess)),
        "lower_quartile_excess_pp": _round(lower_quartile),
        "worst_excess_pp": _round(min(excess)),
        "downside_protection_rate_pct": (
            _round(
                sum(float(row["excess_return_pp"]) > 0 for row in selected_negative)
                / len(selected_negative)
                * 100
            )
            if selected_negative
            else None
        ),
        "selected_negative_window_count": len(selected_negative),
        "candidate_return_percentile": _round(
            _percentile_rank(candidate_returns, float(latest["candidate_return_pct"]))
        ),
        "candidate_median_return_pct": _round(statistics.median(candidate_returns)),
        "latest": latest,
        "recent_windows": rows[-6:],
    }


def _max_drawdown(
    index_by_date: dict[dt.date, float],
    start_date: dt.date,
    end_date: dt.date,
) -> float | None:
    values = [
        value
        for observed_on, value in sorted(index_by_date.items())
        if start_date <= observed_on <= end_date
    ]
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, (value / peak - 1) * 100)
    return _round(worst)


def _check(
    code: str,
    label: str,
    passed: bool | None,
    observed: Any,
    threshold: Any = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "pass" if passed is True else "fail" if passed is False else "pending",
        "observed": observed,
        "threshold": threshold,
    }


def _unavailable_candidate(candidate: dict[str, Any], reason: str, quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(candidate.get("code") or ""),
        "name": str(candidate.get("name") or ""),
        "status": "insufficient_data",
        "label": "持续性数据不足",
        "rationale": "真实日收益或共同日期不足，本轮不判断候选是否具备持续优势。",
        "as_of": None,
        "coverage": quality,
        "rolling": {"6m": _window_summary([]), "12m": _window_summary([])},
        "risk": {},
        "decision_gate": {
            "eligible_for_due_diligence": False,
            "automatic_purchase_allowed": False,
            "automatic_redemption_allowed": False,
            "reason": reason,
            "checks": [],
        },
    }


def _evaluate_candidate(
    selected_payload: dict[str, Any],
    selected_index: dict[dt.date, float],
    selected_quality: dict[str, Any],
    candidate_payload: dict[str, Any],
) -> dict[str, Any]:
    candidate_index, candidate_quality = _total_return_index(candidate_payload)
    if candidate_index is None:
        return _unavailable_candidate(
            candidate_payload,
            str(candidate_quality.get("reason") or "candidate_total_return_unavailable"),
            {"selected": selected_quality, "candidate": candidate_quality},
        )
    endpoints, common_dates = _monthly_common_endpoints(selected_index, candidate_index)
    coverage = {
        "selected": selected_quality,
        "candidate": candidate_quality,
        "common_date_count": len(common_dates),
        "monthly_endpoint_count": len(endpoints),
        "start_date": common_dates[0].isoformat() if common_dates else None,
        "end_date": common_dates[-1].isoformat() if common_dates else None,
        "alignment": "exact_common_dates_month_end",
    }
    windows_6m = _rolling_windows(selected_index, candidate_index, endpoints, 6)
    windows_12m = _rolling_windows(selected_index, candidate_index, endpoints, 12)
    rolling_6m = _window_summary(windows_6m)
    rolling_12m = _window_summary(windows_12m)
    coverage_ok = bool(
        len(endpoints) >= MIN_MONTHLY_ENDPOINTS
        and rolling_6m["sample_count"] >= MIN_6M_WINDOWS
        and rolling_12m["sample_count"] >= MIN_12M_WINDOWS
    )
    if not common_dates or not coverage_ok:
        result = _unavailable_candidate(
            candidate_payload,
            "rolling_windows_below_minimum",
            coverage,
        )
        result["rolling"] = {"6m": rolling_6m, "12m": rolling_12m}
        result["as_of"] = coverage.get("end_date")
        return result

    start_date, end_date = common_dates[0], common_dates[-1]
    selected_drawdown = _max_drawdown(selected_index, start_date, end_date)
    candidate_drawdown = _max_drawdown(candidate_index, start_date, end_date)
    drawdown_delta = (
        candidate_drawdown - selected_drawdown
        if candidate_drawdown is not None and selected_drawdown is not None
        else None
    )
    hot_entry = bool(
        rolling_6m["candidate_return_percentile"] is not None
        and rolling_6m["candidate_return_percentile"] >= HOT_RETURN_PERCENTILE
        and (rolling_6m.get("latest") or {}).get("candidate_return_pct") is not None
        and rolling_6m.get("candidate_median_return_pct") is not None
        and float(rolling_6m["latest"]["candidate_return_pct"])
        >= float(rolling_6m["candidate_median_return_pct"]) + HOT_RETURN_ABOVE_MEDIAN_PP
    )
    win_6m = float(rolling_6m["win_rate_pct"] or 0) >= WIN_RATE_THRESHOLD_PCT
    win_12m = float(rolling_12m["win_rate_pct"] or 0) >= WIN_RATE_THRESHOLD_PCT
    median_positive = bool(
        float(rolling_6m["median_excess_pp"] or 0) > 0
        and float(rolling_12m["median_excess_pp"] or 0) > 0
    )
    drawdown_ok = bool(
        drawdown_delta is not None and drawdown_delta >= -MAX_EXTRA_DRAWDOWN_PP
    )
    recent_advantage = bool(
        float((rolling_6m.get("latest") or {}).get("excess_return_pp") or 0) > 0
        and float((rolling_12m.get("latest") or {}).get("excess_return_pp") or 0) > 0
    )
    durable = win_6m and win_12m and median_positive and drawdown_ok and recent_advantage
    if durable and hot_entry:
        status = "advantage_but_hot"
        label = "持续占优但处于追涨区"
        rationale = "滚动胜率和回撤门禁通过，但当前六个月收益位于自身历史高分位，暂不进入换仓尽调。"
    elif durable:
        status = "durable_advantage"
        label = "持续优势待尽调"
        rationale = "六个月与十二个月滚动胜率、中位超额和回撤门禁同时通过，可继续核验费用与持仓重合。"
    elif recent_advantage and (not win_6m or not win_12m or not median_positive):
        status = "recent_leader_only"
        label = "近期领先但历史不稳定"
        rationale = "最新窗口领先，但长期滚动胜率或中位超额未通过，不能把近期榜单领先解释为稳定替代能力。"
    else:
        status = "mixed_evidence"
        label = "持续性证据分化"
        rationale = "滚动胜率、近期优势或回撤条件未同时通过，当前不进入换仓尽调。"

    checks = [
        _check(
            "rolling_6m_win_rate",
            "近六个月滚动窗口胜率",
            win_6m,
            rolling_6m["win_rate_pct"],
            WIN_RATE_THRESHOLD_PCT,
        ),
        _check(
            "rolling_12m_win_rate",
            "近十二个月滚动窗口胜率",
            win_12m,
            rolling_12m["win_rate_pct"],
            WIN_RATE_THRESHOLD_PCT,
        ),
        _check(
            "positive_median_excess",
            "六个月与十二个月中位超额均为正",
            median_positive,
            [rolling_6m["median_excess_pp"], rolling_12m["median_excess_pp"]],
            "> 0",
        ),
        _check(
            "drawdown_not_materially_worse",
            "最大回撤不比当前基金深 5 个百分点以上",
            drawdown_ok,
            _round(drawdown_delta),
            -MAX_EXTRA_DRAWDOWN_PP,
        ),
        _check(
            "recent_advantage",
            "最新六个月与十二个月均领先",
            recent_advantage,
            [
                (rolling_6m.get("latest") or {}).get("excess_return_pp"),
                (rolling_12m.get("latest") or {}).get("excess_return_pp"),
            ],
            "> 0",
        ),
        _check(
            "not_extreme_hot_zone",
            "当前六个月收益未处于自身历史极热区",
            not hot_entry,
            rolling_6m["candidate_return_percentile"],
            f"< {HOT_RETURN_PERCENTILE}",
        ),
        _check("fees_verified", "申赎费、销售服务费和份额类别已核验", None, None),
        _check("overlap_verified", "最新持仓重合与风格漂移已核验", None, None),
    ]
    return {
        "code": str(candidate_payload.get("code") or ""),
        "name": str(candidate_payload.get("name") or ""),
        "status": status,
        "label": label,
        "rationale": rationale,
        "as_of": end_date.isoformat(),
        "coverage": coverage,
        "rolling": {"6m": rolling_6m, "12m": rolling_12m},
        "risk": {
            "selected_max_drawdown_pct": selected_drawdown,
            "candidate_max_drawdown_pct": candidate_drawdown,
            "drawdown_delta_pp": _round(drawdown_delta),
            "hot_entry_risk": hot_entry,
            "hot_threshold_percentile": HOT_RETURN_PERCENTILE,
        },
        "decision_gate": {
            "eligible_for_due_diligence": status == "durable_advantage",
            "automatic_purchase_allowed": False,
            "automatic_redemption_allowed": False,
            "reason": status,
            "checks": checks,
        },
    }


def evaluate_alternative_durability(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare real provider daily returns across repeated common-date windows."""
    selected_index, selected_quality = _total_return_index(selected)
    if selected_index is None:
        return {
            "diagnostic_id": DIAGNOSTIC_ID,
            "diagnostic_version": DIAGNOSTIC_VERSION,
            "status": "unavailable",
            "reason": str(selected_quality.get("reason") or "selected_total_return_unavailable"),
            "selected": {"code": selected.get("code"), "name": selected.get("name")},
            "candidates": [],
            "summary": {"candidate_count": 0, "due_diligence_count": 0, "hot_count": 0},
            "policy": "当前基金真实总回报序列不可用时停止诊断，不使用单位净值涨跌替代。",
        }
    results = [
        _evaluate_candidate(selected, selected_index, selected_quality, candidate)
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    evaluated_count = sum(item["status"] != "insufficient_data" for item in results)
    status = "evaluated" if evaluated_count == len(results) and results else "partial" if results else "unavailable"
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "status": status,
        "reason": None if results else "candidate_series_missing",
        "selected": {"code": selected.get("code"), "name": selected.get("name")},
        "candidates": results,
        "summary": {
            "candidate_count": len(results),
            "evaluated_count": evaluated_count,
            "due_diligence_count": sum(
                bool((item.get("decision_gate") or {}).get("eligible_for_due_diligence"))
                for item in results
            ),
            "hot_count": sum(item.get("status") == "advantage_but_hot" for item in results),
            "recent_leader_only_count": sum(item.get("status") == "recent_leader_only" for item in results),
        },
        "method": {
            "return_index": "compound_provider_daily_return",
            "alignment": "exact_common_dates_month_end",
            "windows": ["rolling_6m", "rolling_12m"],
            "minimum_6m_windows": MIN_6M_WINDOWS,
            "minimum_12m_windows": MIN_12M_WINDOWS,
            "win_rate_threshold_pct": WIN_RATE_THRESHOLD_PCT,
            "maximum_extra_drawdown_pp": MAX_EXTRA_DRAWDOWN_PP,
            "hot_return_percentile": HOT_RETURN_PERCENTILE,
            "hot_return_above_median_pp": HOT_RETURN_ABOVE_MEDIAN_PP,
        },
        "limitations": [
            "overlapping_rolling_windows_are_not_independent_samples",
            "past_relative_performance_is_not_a_forecast",
            "manager_and_mandate_changes_are_not_normalized",
            "fees_taxes_and_redemption_costs_are_not_deducted",
            "portfolio_overlap_requires_separate_disclosure_review",
        ],
        "policy": "诊断只决定候选是否值得继续尽调；不允许自动买入、自动赎回或把历史胜率解释为未来收益概率。",
    }
