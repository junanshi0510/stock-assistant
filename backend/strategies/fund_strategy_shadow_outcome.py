# -*- coding: utf-8 -*-
"""Evaluate one frozen strategy signal at its exact confirmed-NAV horizon."""

from __future__ import annotations

from typing import Any, Iterable

from .fund_outcome_common import (
    PEER_COMPARATOR_TYPE,
    comparison_points,
    number,
    parse_date,
    peer_period,
    return_pct,
)


EVALUATOR_ID = "fund_strategy_shadow_outcome"
EVALUATOR_VERSION = "1.0.0"
SIGNAL_DIRECTIONS = frozenset({"positive", "negative"})


def _base_result(
    *,
    code: str,
    baseline_as_of: str,
    baseline_nav: float,
    signal_direction: str,
    horizon: str,
    observation_days: int,
) -> dict[str, Any]:
    return {
        "evaluator_id": EVALUATOR_ID,
        "evaluator_version": EVALUATOR_VERSION,
        "code": str(code),
        "signal": {
            "direction": signal_direction,
            "horizon": horizon,
            "confirmed_nav_observations": observation_days,
        },
        "baseline": {
            "as_of": baseline_as_of,
            "unit_nav": round(baseline_nav, 6),
            "source": "immutable_strategy_shadow_enrollment",
        },
    }


def evaluate_fund_strategy_shadow_outcome(
    *,
    code: str,
    baseline_as_of: str,
    baseline_nav: float,
    signal_direction: str,
    horizon: str,
    observation_days: int,
    points: Iterable[dict[str, Any]],
    peer_series: dict[str, Any] | None = None,
    peer_unavailable_reason: str | None = None,
) -> dict[str, Any]:
    baseline_date = parse_date(baseline_as_of)
    baseline_value = number(baseline_nav)
    direction = str(signal_direction or "")
    observations_required = int(observation_days)
    if baseline_date is None or baseline_value is None or baseline_value <= 0:
        raise ValueError("策略 Shadow 样本缺少有效的确认净值基线")
    if direction not in SIGNAL_DIRECTIONS:
        raise ValueError("只有 positive 或 negative 方向可进入 Shadow Outcome")
    if not str(horizon or "").strip() or observations_required < 1:
        raise ValueError("策略 Shadow 样本缺少有效预测窗口")

    normalized: dict[str, float] = {}
    for item in points:
        observed_date = parse_date(item.get("date"))
        value = number(item.get("unit_nav"))
        if observed_date is None or value is None or value <= 0:
            continue
        normalized[observed_date.isoformat()] = value
    ordered = sorted(normalized.items())
    latest_provider_as_of = ordered[-1][0] if ordered else None
    base = _base_result(
        code=code,
        baseline_as_of=baseline_date.isoformat(),
        baseline_nav=baseline_value,
        signal_direction=direction,
        horizon=str(horizon),
        observation_days=observations_required,
    )
    provider_baseline = normalized.get(baseline_date.isoformat())
    if provider_baseline is None:
        return {
            **base,
            "status": "blocked",
            "reason_code": "baseline_date_missing_from_provider_history",
            "reason": "来源历史中找不到入组时冻结的基线日，拒绝使用近似日期。",
            "provider_as_of": latest_provider_as_of,
        }
    tolerance = max(0.000001, abs(baseline_value) * 0.000001)
    if abs(provider_baseline - baseline_value) > tolerance:
        return {
            **base,
            "status": "blocked",
            "reason_code": "baseline_nav_provider_revision",
            "reason": "来源当前历史净值与入组时冻结基线不一致，拒绝静默改写基线。",
            "provider_as_of": latest_provider_as_of,
            "provider_baseline_nav": round(provider_baseline, 6),
            "baseline_difference": round(provider_baseline - baseline_value, 8),
        }

    later = [(date_value, value) for date_value, value in ordered if date_value > baseline_date.isoformat()]
    if len(later) < observations_required:
        return {
            **base,
            "status": "pending",
            "reason_code": "target_confirmed_nav_not_reached",
            "reason": "后续真实确认净值样本尚未达到预测窗口。",
            "provider_as_of": latest_provider_as_of,
            "progress": {
                "available_observations": len(later),
                "required_observations": observations_required,
            },
        }

    target_date, target_nav = later[observations_required - 1]
    unit_nav_return = return_pct(baseline_value, target_nav)
    peer_comparison = peer_period(
        peer_lookup=comparison_points(peer_series, "points"),
        fund_lookup=comparison_points(peer_series, "fund_points"),
        name=str((peer_series or {}).get("name") or "同类平均"),
        source=str((peer_series or {}).get("source") or "") or None,
        source_url=str((peer_series or {}).get("source_url") or "") or None,
        baseline_as_of=baseline_date.isoformat(),
        observed_as_of=target_date,
        fund_return_pct=unit_nav_return,
        unavailable_reason=peer_unavailable_reason,
    )
    relative = number(peer_comparison.get("relative_excess_return_pct"))
    directionally_correct = unit_nav_return > 0 if direction == "positive" else unit_nav_return < 0
    peer_edge_correct = None
    if relative is not None:
        peer_edge_correct = relative > 0 if direction == "positive" else relative < 0
    release_grade = peer_comparison.get("status") == "available"
    return {
        **base,
        "status": "observed",
        "reason_code": None,
        "reason": None,
        "provider_as_of": latest_provider_as_of,
        "observed": {
            "as_of": target_date,
            "unit_nav": round(target_nav, 6),
            "confirmed_nav_observation_number": observations_required,
            "calendar_days": (parse_date(target_date) - baseline_date).days,
            "unit_nav_return_pct": unit_nav_return,
        },
        "peer_comparison": peer_comparison,
        "score": {
            "directionally_correct": directionally_correct,
            "signed_unit_nav_return_pct": round(
                unit_nav_return if direction == "positive" else -unit_nav_return,
                4,
            ),
            "peer_edge_correct": peer_edge_correct,
            "release_grade": release_grade,
        },
        "method": {
            "target": "Nth_confirmed_nav_strictly_after_frozen_baseline",
            "target_observation_number": observations_required,
            "date_substitution": "forbidden",
            "baseline_revision": "blocked_not_overwritten",
            "direction_score_basis": "unit_nav_return_matching_strategy_input",
            "benchmark": PEER_COMPARATOR_TYPE,
            "benchmark_alignment": "exact_provider_date_only",
            "fees_tax_fx_and_user_execution": "not_included",
        },
        "limitations": [
            "single_shadow_outcome_does_not_establish_strategy_edge",
            "provider_peer_average_is_not_contractual_benchmark",
            "cross_fund_outcomes_can_share_market_regime",
            "fund_manager_or_mandate_changes_are_not_normalized",
            "fees_tax_fx_attribution_and_user_execution_are_not_included",
        ],
        "policy": "只评估入组时已冻结的策略方向和精确确认净值观测窗口；不回写原信号，不选择近似日期，不把单次样本包装成胜率或盈利证明。",
    }
