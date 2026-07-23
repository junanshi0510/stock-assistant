# -*- coding: utf-8 -*-
"""Adaptive strategy committee built only from frozen forward evidence.

The committee is a portfolio-construction layer, not a price oracle.  It
combines strategies that already passed the forward profit gate, penalises
duplicated exposures, suspends recent three-cohort failures, and converts the
surviving sleeves into an auditable candidate model portfolio.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any

from opportunity_committee_repository import (
    RESULT_SCHEMA_VERSION,
    OpportunityCommitteeRepository,
    repository as committee_repository,
    sha256_payload,
)
from opportunity_profit_repository import (
    OpportunityProfitRepository,
    repository as profit_repository,
)
import opportunity_profit_service


ENGINE_VERSION = "adaptive_strategy_committee@1.0.0"
EVIDENCE_SCHEMA_VERSION = "opportunity_committee_evidence.v1"
MAX_SELECTED_STRATEGIES = 3
MAX_STRATEGY_WEIGHT_PCT = 50.0
MAX_CANDIDATE_MODEL_WEIGHT_PCT = 25.0
RECENT_DECAY_COHORTS = 3
REBALANCE_DRIFT_THRESHOLD_PCT = 10.0


def _now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _now(value).isoformat(timespec="milliseconds")


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _round(value: Any, digits: int = 3) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _primary_horizon(scorecard: dict[str, Any]) -> dict[str, Any]:
    primary = int(
        ((scorecard.get("policy") or {}).get("values") or {}).get(
            "primary_horizon"
        )
        or 20
    )
    return next(
        (
            item
            for item in scorecard.get("horizons") or []
            if int(item.get("horizon_trading_days") or 0) == primary
        ),
        {},
    )


def _primary_cohorts(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    horizon = int(
        ((scorecard.get("policy") or {}).get("values") or {}).get(
            "primary_horizon"
        )
        or 20
    )
    result = []
    for item in (scorecard.get("cohorts") or {}).get(str(horizon), []):
        if item.get("status") != "mature" or not item.get("independent", True):
            continue
        result.append(
            {
                key: item.get(key)
                for key in (
                    "basket_id",
                    "run_id",
                    "frozen_at",
                    "outcome_date_max",
                    "net_return_pct",
                    "benchmark_return_pct",
                    "net_excess_return_pct",
                    "cohort_max_drawdown_pct",
                    "position_coverage_pct",
                )
            }
        )
    result.sort(
        key=lambda item: (
            str(item.get("frozen_at") or ""),
            str(item.get("basket_id") or ""),
        )
    )
    return result


def strategy_evidence_rows(
    profit_lab: dict[str, Any],
    *,
    profit_repo: OpportunityProfitRepository,
    user_id: str,
) -> list[dict[str, Any]]:
    """Normalise profit-lab rows into committee inputs.

    A live score alone is never sufficient: capital eligibility also requires
    the exact current scorecard to exist in immutable storage and pass its hash
    verification.
    """

    evidence: list[dict[str, Any]] = []
    for scorecard in profit_lab.get("items") or []:
        strategy = scorecard.get("strategy") or {}
        gate = scorecard.get("capital_gate") or {}
        live_plan = scorecard.get("capital_plan") or {}
        persisted_meta = scorecard.get("latest_persisted") or {}
        persisted = None
        if persisted_meta.get("id"):
            persisted = profit_repo.get_scorecard(
                str(persisted_meta["id"]),
                user_id=user_id,
            )
        persisted_current = bool(
            persisted_meta.get("binding_current")
            and persisted
            and persisted.get("integrity_verified")
        )
        primary = _primary_horizon(scorecard)
        family_ci = primary.get("mean_excess_familywise_ci95") or {}
        ci95 = primary.get("mean_excess_ci95") or {}
        policy = (scorecard.get("policy") or {}).get("values") or {}
        evidence.append(
            {
                "strategy_id": strategy.get("id"),
                "strategy_name": strategy.get("name"),
                "strategy_version_id": strategy.get("version_id"),
                "strategy_version_no": strategy.get("version_no"),
                "definition_sha256": strategy.get("definition_sha256"),
                "profit_policy_id": (
                    scorecard.get("policy") or {}
                ).get("id"),
                "profit_policy_version_no": (
                    scorecard.get("policy") or {}
                ).get("version_no"),
                "scorecard_id": persisted_meta.get("id"),
                "scorecard_sha256": persisted_meta.get(
                    "payload_sha256"
                ),
                "scorecard_current": persisted_current,
                "evidence_cutoff_at": scorecard.get(
                    "evidence_cutoff_at"
                ),
                "capital_gate_status": gate.get("status"),
                "capital_eligible": bool(
                    gate.get("capital_eligible")
                ),
                "capital_plan_status": live_plan.get("status"),
                "basket_id": live_plan.get("basket_id"),
                "primary_horizon_trading_days": primary.get(
                    "horizon_trading_days"
                ),
                "mature_cohort_count": primary.get("mature_count"),
                "mean_net_excess_return_pct": primary.get(
                    "mean_net_excess_return_pct"
                ),
                "positive_excess_rate_pct": primary.get(
                    "positive_excess_rate_pct"
                ),
                "mean_excess_ci95": {
                    "lower": ci95.get("lower"),
                    "upper": ci95.get("upper"),
                },
                "familywise_ci95": {
                    "lower": family_ci.get("lower"),
                    "upper": family_ci.get("upper"),
                    "strategy_family_size": family_ci.get(
                        "strategy_family_size"
                    ),
                },
                "worst_cohort_drawdown_pct": primary.get(
                    "worst_cohort_drawdown_pct"
                ),
                "maximum_manual_pilot_pct": gate.get(
                    "maximum_manual_pilot_pct"
                ),
                "minimum_mature_baskets": policy.get(
                    "minimum_mature_baskets"
                ),
                "primary_cohorts": _primary_cohorts(scorecard),
                "live_capital_plan": {
                    key: live_plan.get(key)
                    for key in (
                        "status",
                        "basket_id",
                        "valuation_snapshot_id",
                        "profile_version_id",
                        "pilot_cap_pct",
                        "pilot_cap_cny",
                        "planned_budget_cny",
                        "positions",
                        "reasons",
                    )
                },
                "reasons": _unique(
                    [
                        *(
                            str(item)
                            for item in gate.get("reasons") or []
                        ),
                        *(
                            str(item)
                            for item in live_plan.get("reasons") or []
                        ),
                    ]
                ),
            }
        )
    evidence.sort(key=lambda item: str(item.get("strategy_id") or ""))
    return evidence


def _normalised_positions(row: dict[str, Any]) -> list[dict[str, Any]]:
    positions = []
    for item in (
        (row.get("live_capital_plan") or {}).get("positions") or []
    ):
        weight = _number(item.get("source_weight_pct"), 0.0) or 0.0
        market = str(item.get("market") or "")
        symbol = str(item.get("symbol") or "")
        if weight <= 0 or not market or not symbol:
            continue
        positions.append(
            {
                "market": market,
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "source_weight_pct": weight,
            }
        )
    denominator = sum(
        float(item["source_weight_pct"]) for item in positions
    )
    if denominator <= 0:
        return []
    return [
        {
            **item,
            "normalised_weight_pct": round(
                float(item["source_weight_pct"])
                / denominator
                * 100,
                6,
            ),
        }
        for item in positions
    ]


def _position_overlap(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
) -> float:
    left = {
        (str(item["market"]), str(item["symbol"])): (
            float(item["normalised_weight_pct"]) / 100
        )
        for item in first
    }
    right = {
        (str(item["market"]), str(item["symbol"])): (
            float(item["normalised_weight_pct"]) / 100
        )
        for item in second
    }
    return sum(
        min(left.get(key, 0.0), right.get(key, 0.0))
        for key in set(left) | set(right)
    )


def _monthly_cohort_returns(
    cohorts: list[dict[str, Any]],
) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for item in cohorts:
        value = _number(item.get("net_excess_return_pct"))
        period = str(
            item.get("outcome_date_max") or item.get("frozen_at") or ""
        )[:7]
        if value is None or len(period) != 7:
            continue
        buckets.setdefault(period, []).append(value)
    return {
        period: statistics.fmean(values)
        for period, values in buckets.items()
        if values
    }


def _pearson(
    first: list[float], second: list[float]
) -> float | None:
    if len(first) < 4 or len(first) != len(second):
        return None
    left_mean = statistics.fmean(first)
    right_mean = statistics.fmean(second)
    numerator = sum(
        (left - left_mean) * (right - right_mean)
        for left, right in zip(first, second)
    )
    left_scale = math.sqrt(
        sum((value - left_mean) ** 2 for value in first)
    )
    right_scale = math.sqrt(
        sum((value - right_mean) ** 2 for value in second)
    )
    if left_scale <= 0 or right_scale <= 0:
        return None
    return numerator / (left_scale * right_scale)


def _cohort_correlation(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
) -> tuple[float | None, int]:
    left = _monthly_cohort_returns(first)
    right = _monthly_cohort_returns(second)
    periods = sorted(set(left) & set(right))
    if len(periods) < 4:
        return None, len(periods)
    return (
        _pearson(
            [left[period] for period in periods],
            [right[period] for period in periods],
        ),
        len(periods),
    )


def _base_state(row: dict[str, Any]) -> dict[str, Any]:
    cohorts = list(row.get("primary_cohorts") or [])
    recent = cohorts[-RECENT_DECAY_COHORTS:]
    recent_values = [
        float(item["net_excess_return_pct"])
        for item in recent
        if _number(item.get("net_excess_return_pct")) is not None
    ]
    recent_mean = (
        statistics.fmean(recent_values) if recent_values else None
    )
    recent_positive_rate = (
        sum(1 for value in recent_values if value > 0)
        / len(recent_values)
        * 100
        if recent_values
        else None
    )
    three_loss_switch = bool(
        len(recent_values) == RECENT_DECAY_COHORTS
        and all(value <= 0 for value in recent_values)
    )
    decay_warning = bool(
        len(recent_values) == RECENT_DECAY_COHORTS
        and not three_loss_switch
        and (
            (recent_mean is not None and recent_mean <= 0)
            or (
                recent_positive_rate is not None
                and recent_positive_rate < 50
            )
        )
    )
    base_eligible = bool(
        row.get("capital_eligible")
        and row.get("capital_plan_status") == "available"
        and row.get("scorecard_current")
        and row.get("scorecard_id")
        and row.get("basket_id")
        and _normalised_positions(row)
    )
    reasons = list(row.get("reasons") or [])
    if not row.get("scorecard_current"):
        reasons.append("当前前瞻证据尚未冻结为完整性通过的记分卡")
    if row.get("capital_plan_status") != "available":
        reasons.append("策略没有当前可用的受限人工试运行组合")
    if three_loss_switch:
        reasons.append("最近 3 个独立前瞻批次连续未跑赢基准，触发熔断")
    if decay_warning:
        reasons.append("最近 3 个独立前瞻批次出现衰减，委员会自动降权")
    if three_loss_switch:
        state = "suspended"
    elif base_eligible:
        state = "approved"
    elif row.get("capital_gate_status") == "suspended":
        state = "suspended"
    else:
        state = "collecting"
    return {
        **row,
        "committee_state": state,
        "committee_reasons": _unique([str(item) for item in reasons]),
        "positions_normalised": _normalised_positions(row),
        "recent_decay": {
            "window_cohort_count": len(recent_values),
            "mean_net_excess_return_pct": _round(recent_mean),
            "positive_excess_rate_pct": _round(
                recent_positive_rate, 2
            ),
            "three_consecutive_nonpositive": three_loss_switch,
            "warning": decay_warning,
        },
    }


def _capped_weights(
    raw: dict[str, float],
    *,
    target_pct: float,
    cap_pct: float,
) -> dict[str, float]:
    weights = {key: 0.0 for key in raw}
    remaining = {
        key for key, value in raw.items() if float(value) > 0
    }
    remaining_target = max(0.0, float(target_pct))
    while remaining and remaining_target > 1e-9:
        denominator = sum(float(raw[key]) for key in remaining)
        if denominator <= 0:
            equal = remaining_target / len(remaining)
            proposed = {key: equal for key in remaining}
        else:
            proposed = {
                key: remaining_target * float(raw[key]) / denominator
                for key in remaining
            }
        capped = [
            key
            for key, value in proposed.items()
            if value > cap_pct + 1e-9
        ]
        if not capped:
            for key, value in proposed.items():
                weights[key] += value
            break
        for key in capped:
            room = max(0.0, cap_pct - weights[key])
            weights[key] += room
            remaining_target -= room
            remaining.remove(key)
    return {key: round(value, 6) for key, value in weights.items()}


def _turnover(
    current: dict[str, float], previous: dict[str, float]
) -> float:
    keys = set(current) | set(previous) | {"CASH"}
    current_cash = max(0.0, 100 - sum(current.values()))
    previous_cash = max(0.0, 100 - sum(previous.values()))
    left = {**current, "CASH": current_cash}
    right = {**previous, "CASH": previous_cash}
    return 0.5 * sum(
        abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0)))
        for key in keys
    )


def compose_committee(
    strategy_rows: list[dict[str, Any]],
    *,
    previous_result: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create one deterministic committee result from point-in-time evidence."""

    current = _now(now)
    rows = [_base_state(dict(item)) for item in strategy_rows]
    approved = [
        item for item in rows if item["committee_state"] == "approved"
    ]

    pair_rows: list[dict[str, Any]] = []
    pair_lookup: dict[frozenset[str], float] = {}
    for index, first in enumerate(approved):
        for second in approved[index + 1 :]:
            overlap = _position_overlap(
                first["positions_normalised"],
                second["positions_normalised"],
            )
            correlation, aligned_periods = _cohort_correlation(
                first.get("primary_cohorts") or [],
                second.get("primary_cohorts") or [],
            )
            redundancy = max(
                overlap,
                max(0.0, correlation)
                if correlation is not None
                else 0.0,
            )
            first_id = str(first.get("strategy_id") or "")
            second_id = str(second.get("strategy_id") or "")
            pair_lookup[frozenset((first_id, second_id))] = redundancy
            pair_rows.append(
                {
                    "first_strategy_id": first_id,
                    "first_strategy_name": first.get("strategy_name"),
                    "second_strategy_id": second_id,
                    "second_strategy_name": second.get("strategy_name"),
                    "current_position_overlap_pct": round(
                        overlap * 100, 2
                    ),
                    "cohort_excess_correlation": _round(
                        correlation, 4
                    ),
                    "aligned_cohort_months": aligned_periods,
                    "redundancy_pct": round(redundancy * 100, 2),
                    "correlation_decision_eligible": aligned_periods >= 4,
                }
            )

    evidence_order = sorted(
        approved,
        key=lambda item: (
            _number(
                (item.get("familywise_ci95") or {}).get("lower"),
                -1_000_000,
            )
            or -1_000_000,
            _number(item.get("positive_excess_rate_pct"), 0) or 0,
            int(item.get("mature_cohort_count") or 0),
            -(
                _number(item.get("worst_cohort_drawdown_pct"), 1_000_000)
                or 1_000_000
            ),
            str(item.get("strategy_id") or ""),
        ),
    )
    evidence_rank = {
        str(item.get("strategy_id") or ""): rank
        for rank, item in enumerate(evidence_order)
    }
    for item in approved:
        strategy_id = str(item.get("strategy_id") or "")
        peers = [
            pair_lookup.get(
                frozenset(
                    (strategy_id, str(other.get("strategy_id") or ""))
                ),
                0.0,
            )
            for other in approved
            if other is not item
        ]
        average_redundancy = (
            statistics.fmean(peers) if peers else 0.0
        )
        uniqueness = max(0.0, 1 - average_redundancy)
        if len(approved) <= 1:
            evidence_tilt = 1.0
        else:
            percentile = evidence_rank[strategy_id] / (
                len(approved) - 1
            )
            evidence_tilt = 0.9 + 0.2 * percentile
        uniqueness_tilt = 0.7 + 0.3 * uniqueness
        decay_tilt = (
            0.55
            if (item.get("recent_decay") or {}).get("warning")
            else 1.0
        )
        item["unique_contribution_pct"] = round(
            uniqueness * 100, 2
        )
        item["evidence_tilt"] = round(evidence_tilt, 4)
        item["allocation_utility"] = (
            evidence_tilt * uniqueness_tilt * decay_tilt
        )

    selected: list[dict[str, Any]] = []
    candidates = list(approved)
    while candidates and len(selected) < MAX_SELECTED_STRATEGIES:
        def selection_score(item: dict[str, Any]) -> tuple[float, str]:
            item_id = str(item.get("strategy_id") or "")
            selected_redundancy = max(
                [
                    pair_lookup.get(
                        frozenset(
                            (
                                item_id,
                                str(
                                    other.get("strategy_id") or ""
                                ),
                            )
                        ),
                        0.0,
                    )
                    for other in selected
                ]
                or [0.0]
            )
            return (
                float(item["allocation_utility"])
                * (1 - 0.5 * selected_redundancy),
                item_id,
            )

        chosen = max(candidates, key=selection_score)
        selected.append(chosen)
        candidates.remove(chosen)

    selected_pairs = [
        pair_lookup.get(
            frozenset(
                (
                    str(first.get("strategy_id") or ""),
                    str(second.get("strategy_id") or ""),
                )
            ),
            0.0,
        )
        for index, first in enumerate(selected)
        for second in selected[index + 1 :]
    ]
    average_selected_redundancy = (
        statistics.fmean(selected_pairs) if selected_pairs else 0.0
    )
    if not selected:
        investable_pct = 0.0
    elif len(selected) == 1:
        investable_pct = 50.0
    elif average_selected_redundancy >= 0.8:
        investable_pct = 70.0
    elif average_selected_redundancy >= 0.65:
        investable_pct = 85.0
    else:
        investable_pct = 100.0

    selected_raw = {
        str(item.get("strategy_id") or ""): float(
            item["allocation_utility"]
        )
        for item in selected
    }
    selected_weights = _capped_weights(
        selected_raw,
        target_pct=investable_pct,
        cap_pct=MAX_STRATEGY_WEIGHT_PCT,
    )
    selected_ids = set(selected_weights)
    for item in rows:
        strategy_id = str(item.get("strategy_id") or "")
        if (
            item["committee_state"] == "approved"
            and strategy_id not in selected_ids
        ):
            item["committee_state"] = "reserve"
            item["committee_reasons"] = _unique(
                [
                    *item["committee_reasons"],
                    (
                        f"委员会最多启用 {MAX_SELECTED_STRATEGIES} 个"
                        "策略袖套，本策略保留为替补"
                    ),
                ]
            )
        item["committee_weight_pct"] = round(
            selected_weights.get(strategy_id, 0.0), 2
        )
        item["selected"] = strategy_id in selected_ids

    candidate_map: dict[tuple[str, str], dict[str, Any]] = {}
    for item in rows:
        strategy_weight = float(
            item.get("committee_weight_pct") or 0
        )
        if strategy_weight <= 0:
            continue
        for position in item["positions_normalised"]:
            key = (
                str(position["market"]),
                str(position["symbol"]),
            )
            target = (
                strategy_weight
                * float(position["normalised_weight_pct"])
                / 100
            )
            candidate = candidate_map.setdefault(
                key,
                {
                    "market": key[0],
                    "symbol": key[1],
                    "name": position.get("name") or key[1],
                    "raw_model_weight_pct": 0.0,
                    "supporting_strategy_weight_pct": 0.0,
                    "sources": [],
                },
            )
            candidate["raw_model_weight_pct"] += target
            candidate["supporting_strategy_weight_pct"] += (
                strategy_weight
            )
            candidate["sources"].append(
                {
                    "strategy_id": item.get("strategy_id"),
                    "strategy_name": item.get("strategy_name"),
                    "committee_weight_pct": round(
                        strategy_weight, 2
                    ),
                    "within_strategy_weight_pct": round(
                        float(position["normalised_weight_pct"]), 2
                    ),
                    "scorecard_id": item.get("scorecard_id"),
                    "basket_id": item.get("basket_id"),
                }
            )

    candidate_rows = []
    for item in candidate_map.values():
        raw_weight = float(item["raw_model_weight_pct"])
        target_weight = min(
            raw_weight, MAX_CANDIDATE_MODEL_WEIGHT_PCT
        )
        support_count = len(item["sources"])
        agreement = (
            float(item["supporting_strategy_weight_pct"])
            / investable_pct
            * 100
            if investable_pct > 0
            else 0.0
        )
        if support_count >= 2 and agreement >= 50:
            relative_view = "committee_consensus"
            view_label = "多策略共识优先"
        elif support_count >= 2:
            relative_view = "diversified_support"
            view_label = "多策略分散支持"
        else:
            relative_view = "single_strategy_candidate"
            view_label = "单策略候选，需更谨慎"
        candidate_rows.append(
            {
                **item,
                "raw_model_weight_pct": round(raw_weight, 3),
                "model_target_weight_pct": round(
                    target_weight, 3
                ),
                "candidate_cap_applied": (
                    raw_weight
                    > MAX_CANDIDATE_MODEL_WEIGHT_PCT + 1e-9
                ),
                "support_count": support_count,
                "agreement_pct": round(agreement, 2),
                "relative_view": relative_view,
                "view_label": view_label,
                "calibrated_probability": False,
                "execution_authorized": False,
            }
        )
    candidate_rows.sort(
        key=lambda item: (
            -float(item["model_target_weight_pct"]),
            -int(item["support_count"]),
            str(item["market"]),
            str(item["symbol"]),
        )
    )
    for rank, item in enumerate(candidate_rows, start=1):
        item["committee_rank"] = rank

    candidate_invested_pct = sum(
        float(item["model_target_weight_pct"])
        for item in candidate_rows
    )
    cash_reserve_pct = max(0.0, 100 - candidate_invested_pct)

    current_strategy_map = {
        str(item.get("strategy_id") or ""): float(
            item.get("committee_weight_pct") or 0
        )
        for item in rows
        if float(item.get("committee_weight_pct") or 0) > 0
    }
    current_candidate_map = {
        f"{item['market']}:{item['symbol']}": float(
            item["model_target_weight_pct"]
        )
        for item in candidate_rows
    }
    previous_strategies = {
        str(item.get("strategy_id") or ""): float(
            item.get("committee_weight_pct") or 0
        )
        for item in (previous_result or {}).get("strategies") or []
        if float(item.get("committee_weight_pct") or 0) > 0
    }
    previous_candidates = {
        f"{item.get('market')}:{item.get('symbol')}": float(
            item.get("model_target_weight_pct") or 0
        )
        for item in (previous_result or {}).get("candidate_consensus")
        or []
    }
    if previous_result:
        strategy_turnover = _turnover(
            current_strategy_map, previous_strategies
        )
        candidate_turnover = _turnover(
            current_candidate_map, previous_candidates
        )
        entered = sorted(
            set(current_strategy_map) - set(previous_strategies)
        )
        exited = sorted(
            set(previous_strategies) - set(current_strategy_map)
        )
        rebalance_required = bool(
            entered
            or exited
            or candidate_turnover
            >= REBALANCE_DRIFT_THRESHOLD_PCT
        )
        drift_state = (
            "rebalance_required"
            if rebalance_required
            else "within_band"
        )
    else:
        strategy_turnover = None
        candidate_turnover = None
        entered = sorted(current_strategy_map)
        exited = []
        rebalance_required = bool(current_strategy_map)
        drift_state = "initial_mandate"

    suspended_count = sum(
        1
        for item in rows
        if item.get("committee_state") == "suspended"
    )
    if not selected:
        status = (
            "degraded" if suspended_count else "collecting"
        )
    elif suspended_count:
        status = "degraded"
    elif len(selected) == 1 or average_selected_redundancy >= 0.65:
        status = "concentrated"
    else:
        status = "active"

    cutoff_values = [
        str(item.get("evidence_cutoff_at") or "") for item in rows
    ]
    evidence_cutoff = (
        max(cutoff_values) if any(cutoff_values) else None
    )
    evidence_strategies = []
    for item in rows:
        evidence_strategies.append(
            {
                key: item.get(key)
                for key in (
                    "strategy_id",
                    "strategy_name",
                    "strategy_version_id",
                    "strategy_version_no",
                    "definition_sha256",
                    "profit_policy_id",
                    "profit_policy_version_no",
                    "scorecard_id",
                    "scorecard_sha256",
                    "scorecard_current",
                    "evidence_cutoff_at",
                    "capital_gate_status",
                    "capital_eligible",
                    "capital_plan_status",
                    "basket_id",
                    "primary_horizon_trading_days",
                    "mature_cohort_count",
                    "mean_net_excess_return_pct",
                    "positive_excess_rate_pct",
                    "mean_excess_ci95",
                    "familywise_ci95",
                    "worst_cohort_drawdown_pct",
                    "maximum_manual_pilot_pct",
                    "minimum_mature_baskets",
                    "primary_cohorts",
                    "live_capital_plan",
                )
            }
        )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "evidence_cutoff_at": evidence_cutoff,
        "bindings": {
            "scorecard_ids": sorted(
                str(item.get("scorecard_id"))
                for item in rows
                if item.get("scorecard_current")
                and item.get("scorecard_id")
            ),
            "scorecard_sha256s": sorted(
                str(item.get("scorecard_sha256"))
                for item in rows
                if item.get("scorecard_current")
                and item.get("scorecard_sha256")
            ),
            "basket_ids": sorted(
                str(item.get("basket_id"))
                for item in rows
                if item.get("basket_id")
            ),
            "strategy_version_ids": sorted(
                str(item.get("strategy_version_id"))
                for item in rows
                if item.get("strategy_version_id")
            ),
        },
        "strategies": evidence_strategies,
    }
    evidence_sha = sha256_payload(evidence)

    public_strategies = []
    for item in rows:
        public_strategies.append(
            {
                key: item.get(key)
                for key in (
                    "strategy_id",
                    "strategy_name",
                    "strategy_version_id",
                    "scorecard_id",
                    "basket_id",
                    "capital_gate_status",
                    "committee_state",
                    "committee_reasons",
                    "selected",
                    "committee_weight_pct",
                    "mature_cohort_count",
                    "mean_net_excess_return_pct",
                    "positive_excess_rate_pct",
                    "familywise_ci95",
                    "worst_cohort_drawdown_pct",
                    "recent_decay",
                    "unique_contribution_pct",
                    "evidence_tilt",
                    "allocation_utility",
                )
            }
        )
    public_strategies.sort(
        key=lambda item: (
            -float(item.get("committee_weight_pct") or 0),
            str(item.get("strategy_name") or ""),
        )
    )

    if status == "active":
        headline = (
            f"{len(selected)} 个互补策略组成当前模型组合，"
            f"候选现金保留 {cash_reserve_pct:.1f}%"
        )
    elif status == "concentrated":
        headline = (
            "合格策略数量或独立性不足，委员会主动保留更多现金"
        )
    elif status == "degraded":
        headline = "至少一个策略触发失效检查，已停用或降权"
    else:
        headline = "前瞻证据尚不足，委员会不分配研究资金"

    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(current),
        "evidence_cutoff_at": evidence_cutoff,
        "evidence_sha256": evidence_sha,
        "status": status,
        "headline": headline,
        "summary": {
            "strategy_count": len(rows),
            "forward_eligible_count": sum(
                1 for item in rows if item.get("capital_eligible")
            ),
            "selected_strategy_count": len(selected),
            "suspended_strategy_count": suspended_count,
            "candidate_count": len(candidate_rows),
            "committee_investable_pct": round(
                investable_pct, 2
            ),
            "candidate_model_invested_pct": round(
                candidate_invested_pct, 2
            ),
            "cash_reserve_pct": round(cash_reserve_pct, 2),
            "average_selected_redundancy_pct": round(
                average_selected_redundancy * 100, 2
            ),
        },
        "strategies": public_strategies,
        "redundancy_matrix": pair_rows,
        "candidate_consensus": candidate_rows,
        "drift": {
            "state": drift_state,
            "rebalance_required": rebalance_required,
            "threshold_pct": REBALANCE_DRIFT_THRESHOLD_PCT,
            "strategy_one_way_turnover_pct": _round(
                strategy_turnover, 2
            ),
            "candidate_one_way_turnover_pct": _round(
                candidate_turnover, 2
            ),
            "entered_strategy_ids": entered,
            "exited_strategy_ids": exited,
            "previous_mandate_available": bool(previous_result),
        },
        "policy": {
            "maximum_selected_strategies": MAX_SELECTED_STRATEGIES,
            "maximum_strategy_weight_pct": MAX_STRATEGY_WEIGHT_PCT,
            "maximum_candidate_model_weight_pct": (
                MAX_CANDIDATE_MODEL_WEIGHT_PCT
            ),
            "recent_decay_cohorts": RECENT_DECAY_COHORTS,
            "rebalance_drift_threshold_pct": (
                REBALANCE_DRIFT_THRESHOLD_PCT
            ),
            "single_strategy_investable_pct": 50.0,
            "high_redundancy_cash_reserve": (
                "入选策略平均冗余达到 65%/80% 时，模型最多投入 "
                "85%/70%，其余保留现金"
            ),
        },
        "methodology": {
            "admission": (
                "只接纳通过独立前瞻批次、成本后相对基准、"
                "回撤、置信区间和多重检验门禁，且当前不可变"
                "记分卡完整性通过的策略。"
            ),
            "decay_kill_switch": (
                "最近 3 个独立前瞻批次连续未跑赢基准时停用；"
                "出现衰减但未连续失败时自动降权。"
            ),
            "unique_contribution": (
                "按当前候选权重重叠和至少 4 个共同月份的前瞻"
                "超额相关性衡量冗余；相关样本不足时只使用可验证"
                "的持仓重叠，不伪造相关系数。"
            ),
            "allocation": (
                "以等权为锚，仅在窄幅内按保守证据和独立贡献"
                "倾斜；单策略最高 50%，最多启用 3 个策略。"
            ),
            "candidate_consensus": (
                "候选权重来自策略袖套权重乘以冻结组合内权重；"
                "单一候选最高占模型 25%，超出部分保留现金。"
            ),
            "drift": (
                "与上一份不可变指令比较策略和候选单边换手；"
                "超过 10% 或策略进出时才要求再平衡，避免追涨"
                "杀跌式频繁切换。"
            ),
        },
        "boundaries": {
            "calibrated_probability_provided": False,
            "absolute_price_direction_claimed": False,
            "execution_authorized": False,
            "automatic_order_creation": False,
            "return_guaranteed": False,
            "interpretation": (
                "委员会输出的是有前瞻证据支持的相对优先级、"
                "组合权重和回避/停用理由，不是上涨概率或收益承诺。"
            ),
        },
    }
    return result, evidence


def current_committee(
    *,
    user_id: str,
    now: dt.datetime | None = None,
    profit_lab_loader=None,
    profit_repo: OpportunityProfitRepository = profit_repository,
    mandate_repo: OpportunityCommitteeRepository = committee_repository,
) -> dict[str, Any]:
    loader = profit_lab_loader or (
        lambda: opportunity_profit_service.profit_lab_overview(
            user_id=user_id
        )
    )
    rows = strategy_evidence_rows(
        loader(),
        profit_repo=profit_repo,
        user_id=user_id,
    )
    latest = mandate_repo.latest_mandate(user_id=user_id)
    previous = (
        latest.get("result")
        if latest and (latest.get("integrity") or {}).get("verified")
        else None
    )
    result, evidence = compose_committee(
        rows, previous_result=previous, now=now
    )
    evidence_sha = sha256_payload(evidence)
    return {
        **result,
        "persistence": {
            "latest_mandate": (
                {
                    key: latest.get(key)
                    for key in (
                        "id",
                        "status",
                        "engine_version",
                        "evidence_cutoff_at",
                        "evidence_sha256",
                        "result_sha256",
                        "created_at",
                    )
                }
                if latest
                else None
            ),
            "binding_current": bool(
                latest
                and (latest.get("integrity") or {}).get("verified")
                and latest.get("engine_version") == ENGINE_VERSION
                and latest.get("evidence_sha256") == evidence_sha
            ),
        },
    }


def freeze_committee(
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
    profit_lab_loader=None,
    profit_repo: OpportunityProfitRepository = profit_repository,
    mandate_repo: OpportunityCommitteeRepository = committee_repository,
) -> tuple[dict[str, Any], bool]:
    loader = profit_lab_loader or (
        lambda: opportunity_profit_service.profit_lab_overview(
            user_id=user_id
        )
    )
    rows = strategy_evidence_rows(
        loader(),
        profit_repo=profit_repo,
        user_id=user_id,
    )
    latest = mandate_repo.latest_mandate(user_id=user_id)
    previous = (
        latest.get("result")
        if latest and (latest.get("integrity") or {}).get("verified")
        else None
    )
    result, evidence = compose_committee(
        rows, previous_result=previous, now=now
    )
    return mandate_repo.create_mandate(
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        status=str(result["status"]),
        evidence_cutoff_at=result.get("evidence_cutoff_at"),
        evidence=evidence,
        result=result,
    )


def mandate_history(
    *,
    user_id: str,
    limit: int = 30,
    mandate_repo: OpportunityCommitteeRepository = committee_repository,
) -> dict[str, Any]:
    items = mandate_repo.list_mandates(
        user_id=user_id, limit=limit
    )
    return {
        "items": items,
        "count": len(items),
        "engine_version": ENGINE_VERSION,
    }
