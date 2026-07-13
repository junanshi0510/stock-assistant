# -*- coding: utf-8 -*-
"""Find the prior independent occurrence of each current rolling fund return."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any, Iterable


METRIC_ID = "fund_return_recurrence"
METRIC_VERSION = "1.0.0"
MIN_SEPARATION_OBSERVATIONS = 5
DEFAULT_WINDOWS = (
    ("1m", "近1月", 20),
    ("3m", "近3月", 60),
    ("6m", "近6月", 120),
    ("1y", "近1年", 250),
)


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _normalize_points(points: Iterable[Any]) -> list[tuple[dt.date, float]]:
    by_date: dict[dt.date, float] = {}
    for item in points:
        if isinstance(item, dict):
            date_value, nav_value = item.get("date"), item.get("unit_nav")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            date_value, nav_value = item[0], item[1]
        else:
            continue
        parsed_date = _date(date_value)
        parsed_nav = _number(nav_value)
        if parsed_date is not None and parsed_nav is not None:
            by_date[parsed_date] = parsed_nav
    return sorted(by_date.items())


def _tolerance(current_return: float) -> float:
    """Percentage-point band; wide enough for rounding but capped for large returns."""
    return round(max(0.20, min(2.0, abs(current_return) * 0.02)), 2)


def find_previous_return_occurrence(
    rows: list[dict[str, Any]],
    current_return: float,
    minimum_separation_observations: int = MIN_SEPARATION_OBSERVATIONS,
) -> dict[str, Any]:
    """Skip the current contiguous band, then locate the prior independent hit."""
    if not rows:
        return {
            "status": "no_prior_history",
            "tolerance_pp": _tolerance(current_return),
            "minimum_separation_observations": minimum_separation_observations,
            "current_episode": None,
            "previous": None,
        }
    tolerance = _tolerance(current_return)
    current_index = len(rows) - 1
    episode_start = current_index
    while (
        episode_start > 0
        and abs(float(rows[episode_start - 1]["return"]) - current_return) <= tolerance
    ):
        episode_start -= 1

    current_date = _date(rows[current_index].get("date"))
    episode_date = _date(rows[episode_start].get("date"))
    current_episode = {
        "start_date": episode_date.isoformat() if episode_date else None,
        "end_date": current_date.isoformat() if current_date else None,
        "observation_count": current_index - episode_start + 1,
    }
    search_end = episode_start - max(1, int(minimum_separation_observations)) - 1
    if search_end < 0:
        return {
            "status": "no_prior_history",
            "tolerance_pp": tolerance,
            "minimum_separation_observations": minimum_separation_observations,
            "current_episode": current_episode,
            "previous": None,
        }

    matched_index = next(
        (
            index
            for index in range(search_end, -1, -1)
            if abs(float(rows[index]["return"]) - current_return) <= tolerance
        ),
        None,
    )
    if matched_index is None:
        matched_index = min(
            range(search_end + 1),
            key=lambda index: abs(float(rows[index]["return"]) - current_return),
        )
        status = "nearest_only"
        method = "nearest_prior_independent_observation"
    else:
        status = "matched"
        method = "prior_independent_episode_within_tolerance"

    matched = rows[matched_index]
    matched_return = float(matched["return"])
    matched_date = _date(matched.get("date"))
    difference = matched_return - current_return
    return {
        "status": status,
        "tolerance_pp": tolerance,
        "minimum_separation_observations": minimum_separation_observations,
        "current_episode": current_episode,
        "previous": {
            "date": matched_date.isoformat() if matched_date else None,
            "return": _round(matched_return),
            "difference_pp": _round(difference),
            "absolute_difference_pp": _round(abs(difference)),
            "calendar_days_ago": (
                (current_date - matched_date).days
                if current_date is not None and matched_date is not None
                else None
            ),
            "observations_ago": current_index - matched_index,
            "method": method,
        },
    }


def _percentile_rank(values: list[float], current: float) -> float | None:
    if not values:
        return None
    return sum(value <= current for value in values) / len(values) * 100


def evaluate_fund_return_recurrence(
    points: Iterable[Any],
    windows: Iterable[tuple[str, str, int]] = DEFAULT_WINDOWS,
) -> dict[str, Any]:
    normalized = _normalize_points(points)
    dates = [item[0] for item in normalized]
    nav = [item[1] for item in normalized]
    items = []
    for key, label, observations in windows:
        if len(nav) <= observations:
            items.append({
                "key": key,
                "label": label,
                "observations": observations,
                "days": observations,
                "status": "insufficient_history",
                "current_return": None,
                "historical_percentile": None,
                "average_return": None,
                "avg_return": None,
                "positive_ratio": None,
                "sample_count": 0,
                "recurrence": None,
            })
            continue

        rows = []
        for index in range(observations, len(nav)):
            base = nav[index - observations]
            if base <= 0:
                continue
            rows.append({
                "date": dates[index].isoformat(),
                "return": (nav[index] / base - 1) * 100,
            })
        values = [float(row["return"]) for row in rows]
        current = values[-1]
        average_return = _round(statistics.fmean(values))
        items.append({
            "key": key,
            "label": label,
            "observations": observations,
            "days": observations,
            "status": "available",
            "current_return": _round(current),
            "historical_percentile": _round(_percentile_rank(values, current)),
            "average_return": average_return,
            "avg_return": average_return,
            "positive_ratio": _round(sum(value > 0 for value in values) / len(values) * 100),
            "sample_count": len(values),
            "recurrence": find_previous_return_occurrence(rows, current),
        })

    return {
        "metric_id": METRIC_ID,
        "metric_version": METRIC_VERSION,
        "as_of": dates[-1].isoformat() if dates else None,
        "coverage": {
            "observation_count": len(normalized),
            "start_date": dates[0].isoformat() if dates else None,
            "end_date": dates[-1].isoformat() if dates else None,
        },
        "items": items,
        "method": {
            "return_definition": "unit_nav_rolling_return_by_observation_count",
            "match": "skip_current_contiguous_tolerance_band_then_find_previous_independent_hit",
            "minimum_separation_observations": MIN_SEPARATION_OBSERVATIONS,
            "tolerance": "max(0.20pp, min(2.00pp, abs(current_return)*2%))",
            "fallback": "nearest_prior_independent_observation_is_labeled_nearest_only",
        },
        "policy": "历史重现只描述过去滚动收益率何时处于相同水平，不代表未来收益或买卖时点。",
    }
