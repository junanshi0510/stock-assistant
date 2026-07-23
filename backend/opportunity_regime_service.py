# -*- coding: utf-8 -*-
"""Market-regime, strategy-suitability, and risk-budget decision layer.

This module does not forecast an absolute price direction.  It turns verified
opportunity-run snapshots into a candidate-pool regime, evaluates each
strategy only against independent forward cohorts frozen in the same regime,
and supplies a no-leverage risk cap to downstream portfolio construction.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any

from opportunity_regime_repository import (
    RESULT_SCHEMA_VERSION,
    OpportunityRegimeRepository,
    repository as regime_repository,
    sha256_payload,
)
from opportunity_repository import (
    OpportunityRepository,
    repository as opportunity_repository,
)


ENGINE_VERSION = "market_regime_strategy_fit@1.0.0"
EVIDENCE_SCHEMA_VERSION = "opportunity_regime_evidence.v1"
MARKETS = ("A股", "港股", "美股")
MAX_RUN_AGE_DAYS = 14
MAX_SOURCES_PER_MARKET = 5
MIN_REGIME_FIT_COHORTS = 4
FULL_RELIABILITY_COHORTS = 8
REGIME_SCORE = {
    "risk_on": 1.0,
    "mixed": 0.0,
    "defensive": -1.0,
}
REGIME_LABEL = {
    "risk_on": "偏强",
    "mixed": "震荡 / 分歧",
    "defensive": "防守",
    "insufficient": "证据不足",
}
BASE_RISK_MULTIPLIER = {
    "risk_on": 1.0,
    "mixed": 0.85,
    "defensive": 0.60,
    "insufficient": 0.50,
}


def _now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _now(value).isoformat(timespec="milliseconds")


def _parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _round(value: Any, digits: int = 3) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _weighted_mean(
    values: list[tuple[float, float]],
) -> float | None:
    total = sum(weight for _, weight in values if weight > 0)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in values) / total


def _status_from_score(score: float, *, coverage: float = 1.0) -> str:
    if coverage < 0.5:
        return "insufficient"
    if score >= 0.35:
        return "risk_on"
    if score <= -0.35:
        return "defensive"
    return "mixed"


def _volatility_multiplier(value: float | None) -> float:
    if value is None or value <= 25:
        return 1.0
    if value <= 35:
        return 0.90
    if value <= 50:
        return 0.75
    return 0.60


def _position_weight(item: dict[str, Any]) -> float:
    for key in (
        "source_weight_pct",
        "normalised_weight_pct",
        "weight_pct",
        "target_weight_pct",
    ):
        value = _number(item.get(key))
        if value is not None and value > 0:
            return value
    return 0.0


def _run_market_observations(
    run_records: list[dict[str, Any]],
    *,
    now: dt.datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    stale = {market: 0 for market in MARKETS}
    for run in run_records:
        result = run.get("result") or {}
        if (
            run.get("status") not in {"succeeded", "partial"}
            or not run.get("result_verified")
            or not isinstance(result, dict)
        ):
            continue
        observed_at = _parse_datetime(
            result.get("generated_at")
            or run.get("completed_at")
            or run.get("created_at")
        )
        if observed_at is None:
            continue
        # Freeze freshness to a UTC calendar-day cut so repeated reads within
        # one decision day produce the same evidence hash.  The state can
        # still roll forward naturally on the next day.
        age_days = float(
            max(0, (now.date() - observed_at.date()).days)
        )
        strategy = result.get("strategy") or {}
        strategy_version_id = str(
            strategy.get("version_id")
            or run.get("strategy_version_id")
            or run.get("id")
            or ""
        )
        for regime in result.get("market_regimes") or []:
            market = str(regime.get("market") or "")
            status = str(regime.get("status") or "")
            if market not in MARKETS or status not in REGIME_SCORE:
                continue
            if age_days > MAX_RUN_AGE_DAYS:
                stale[market] += 1
                continue
            sample_count = max(
                0, int(_number(regime.get("sample_count"), 0) or 0)
            )
            freshness = max(
                0.15, 1 - age_days / MAX_RUN_AGE_DAYS
            )
            sample_reliability = min(1.0, sample_count / 8)
            weight = max(0.05, freshness * sample_reliability)
            item = {
                "market": market,
                "status": status,
                "run_id": run.get("id"),
                "result_sha256": run.get("result_sha256"),
                "strategy_id": strategy.get("id")
                or run.get("strategy_id"),
                "strategy_version_id": strategy_version_id,
                "observed_at": observed_at.isoformat(
                    timespec="seconds"
                ),
                "age_days": round(age_days, 3),
                "sample_count": sample_count,
                "median_return_3m": _round(
                    regime.get("median_return_3m"), 3
                ),
                "positive_breadth_pct": _round(
                    regime.get("positive_breadth_pct"), 3
                ),
                "median_annual_vol": _round(
                    regime.get("median_annual_vol"), 3
                ),
                "evidence_weight": round(weight, 6),
                "scope": "candidate_pool",
            }
            key = (market, strategy_version_id)
            existing = latest.get(key)
            if (
                existing is None
                or str(item["observed_at"])
                > str(existing["observed_at"])
                or (
                    item["observed_at"] == existing["observed_at"]
                    and str(item.get("run_id") or "")
                    > str(existing.get("run_id") or "")
                )
            ):
                latest[key] = item
    selected: list[dict[str, Any]] = []
    for market in MARKETS:
        market_rows = sorted(
            (
                item
                for (item_market, _), item in latest.items()
                if item_market == market
            ),
            key=lambda item: (
                str(item.get("observed_at") or ""),
                str(item.get("run_id") or ""),
            ),
            reverse=True,
        )[:MAX_SOURCES_PER_MARKET]
        selected.extend(market_rows)
    return selected, stale


def _market_state(
    market: str,
    observations: list[dict[str, Any]],
    *,
    stale_count: int,
) -> dict[str, Any]:
    rows = [
        item for item in observations if item.get("market") == market
    ]
    if not rows:
        return {
            "market": market,
            "status": "insufficient",
            "label": REGIME_LABEL["insufficient"],
            "source_count": 0,
            "candidate_sample_count": 0,
            "agreement_pct": None,
            "weighted_regime_score": None,
            "median_return_3m": None,
            "positive_breadth_pct": None,
            "median_annual_vol": None,
            "latest_observed_at": None,
            "latest_age_days": None,
            "stale_source_count": stale_count,
            "evidence_grade": "insufficient",
            "base_risk_multiplier": 0.50,
            "volatility_multiplier": 1.0,
            "risk_budget_multiplier": 0.50,
            "risk_budget_pct": 50.0,
            "sources": [],
            "scope_notice": (
                "状态只覆盖策略候选池，不代表交易所全市场；"
                "当前没有 14 天内可验证来源"
            ),
        }
    score = _weighted_mean(
        [
            (
                REGIME_SCORE[str(item["status"])],
                float(item["evidence_weight"]),
            )
            for item in rows
        ]
    )
    status = _status_from_score(score or 0.0)
    total_weight = sum(float(item["evidence_weight"]) for item in rows)
    status_weights = {
        candidate: sum(
            float(item["evidence_weight"])
            for item in rows
            if item["status"] == candidate
        )
        for candidate in REGIME_SCORE
    }
    agreement = (
        max(status_weights.values()) / total_weight * 100
        if total_weight > 0
        else None
    )

    def metric(key: str) -> float | None:
        return _weighted_mean(
            [
                (
                    float(value),
                    float(item["evidence_weight"]),
                )
                for item in rows
                if (value := _number(item.get(key))) is not None
            ]
        )

    annual_vol = metric("median_annual_vol")
    base_multiplier = BASE_RISK_MULTIPLIER[status]
    vol_multiplier = _volatility_multiplier(annual_vol)
    source_multiplier = 0.85 if len(rows) == 1 else 1.0
    risk_multiplier = min(
        1.0,
        base_multiplier,
        vol_multiplier,
        source_multiplier,
    )
    latest_age = min(float(item["age_days"]) for item in rows)
    if len(rows) >= 3 and (agreement or 0) >= 60 and latest_age <= 7:
        grade = "strong"
    elif len(rows) >= 2:
        grade = "usable"
    else:
        grade = "thin"
    return {
        "market": market,
        "status": status,
        "label": REGIME_LABEL[status],
        "source_count": len(rows),
        "candidate_sample_count": sum(
            int(item["sample_count"]) for item in rows
        ),
        "agreement_pct": _round(agreement, 2),
        "weighted_regime_score": _round(score, 4),
        "median_return_3m": _round(
            metric("median_return_3m"), 2
        ),
        "positive_breadth_pct": _round(
            metric("positive_breadth_pct"), 2
        ),
        "median_annual_vol": _round(annual_vol, 2),
        "latest_observed_at": max(
            str(item["observed_at"]) for item in rows
        ),
        "latest_age_days": round(latest_age, 2),
        "stale_source_count": stale_count,
        "evidence_grade": grade,
        "base_risk_multiplier": round(base_multiplier, 4),
        "volatility_multiplier": round(vol_multiplier, 4),
        "risk_budget_multiplier": round(risk_multiplier, 4),
        "risk_budget_pct": round(risk_multiplier * 100, 2),
        "sources": rows,
        "scope_notice": (
            "由最多 5 个策略版本的最新候选池状态按样本与新鲜度"
            "加权；不是交易所全市场状态，也不是涨跌概率"
        ),
    }


def build_market_states(
    run_records: list[dict[str, Any]],
    *,
    now: dt.datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current = _now(now)
    observations, stale = _run_market_observations(
        run_records, now=current
    )
    states = [
        _market_state(
            market,
            observations,
            stale_count=stale.get(market, 0),
        )
        for market in MARKETS
    ]
    return states, observations


def _weighted_position_regime(
    positions: list[dict[str, Any]],
    state_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    market_weights: dict[str, float] = {}
    for position in positions:
        market = str(position.get("market") or "")
        weight = _position_weight(position)
        if market and weight > 0:
            market_weights[market] = (
                market_weights.get(market, 0.0) + weight
            )
    total = sum(market_weights.values())
    if total <= 0:
        return {
            "status": "insufficient",
            "label": REGIME_LABEL["insufficient"],
            "weighted_regime_score": None,
            "coverage_pct": 0.0,
            "market_weights_pct": {},
            "risk_budget_multiplier": 0.50,
        }
    covered = 0.0
    score_sum = 0.0
    risk_sum = 0.0
    normalized = {}
    for market, raw_weight in market_weights.items():
        weight = raw_weight / total
        normalized[market] = round(weight * 100, 2)
        state = state_map.get(market) or {}
        status = str(state.get("status") or "insufficient")
        risk_multiplier = _number(
            state.get("risk_budget_multiplier"),
            BASE_RISK_MULTIPLIER.get(status, 0.50),
        ) or 0.50
        risk_sum += weight * min(1.0, risk_multiplier)
        if status in REGIME_SCORE:
            covered += weight
            score_sum += weight * REGIME_SCORE[status]
    score = score_sum / covered if covered > 0 else 0.0
    status = _status_from_score(score, coverage=covered)
    if covered < 0.8:
        risk_sum = min(risk_sum, 0.75)
    return {
        "status": status,
        "label": REGIME_LABEL[status],
        "weighted_regime_score": (
            round(score, 4) if covered > 0 else None
        ),
        "coverage_pct": round(covered * 100, 2),
        "market_weights_pct": normalized,
        "risk_budget_multiplier": round(
            min(1.0, max(0.0, risk_sum)), 4
        ),
    }


def classify_frozen_basket(
    basket: dict[str, Any],
    *,
    bound_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = basket.get("snapshot") or {}
    if not basket.get("snapshot_verified"):
        return {
            "status": "insufficient",
            "label": REGIME_LABEL["insufficient"],
            "coverage_pct": 0.0,
            "source": "invalid_basket_snapshot",
            "source_sha256": basket.get("snapshot_sha256"),
            "market_weights_pct": {},
            "market_states": [],
        }
    regimes = snapshot.get("market_regimes")
    source = "basket_snapshot"
    source_sha = basket.get("snapshot_sha256")
    if not isinstance(regimes, list):
        run_result = (bound_run or {}).get("result") or {}
        exact_binding = bool(
            bound_run
            and bound_run.get("result_verified")
            and snapshot.get("run_result_sha256")
            and snapshot.get("run_result_sha256")
            == bound_run.get("result_sha256")
        )
        if exact_binding:
            regimes = run_result.get("market_regimes") or []
            source = "bound_run_result"
            source_sha = bound_run.get("result_sha256")
        else:
            regimes = []
            source = "missing_verified_regime_source"
    state_map = {
        str(item.get("market") or ""): {
            **item,
            "risk_budget_multiplier": BASE_RISK_MULTIPLIER.get(
                str(item.get("status") or "insufficient"), 0.50
            ),
        }
        for item in regimes
        if str(item.get("market") or "") in MARKETS
    }
    composite = _weighted_position_regime(
        snapshot.get("positions") or [], state_map
    )
    return {
        **composite,
        "basket_id": basket.get("id"),
        "run_id": basket.get("run_id"),
        "frozen_at": snapshot.get("frozen_at")
        or basket.get("created_at"),
        "source": source,
        "source_sha256": source_sha,
        "market_states": [
            {
                key: item.get(key)
                for key in (
                    "market",
                    "status",
                    "label",
                    "sample_count",
                    "median_return_3m",
                    "positive_breadth_pct",
                    "median_annual_vol",
                )
            }
            for item in regimes
            if isinstance(item, dict)
        ],
    }


def _t_critical_95(sample_count: int) -> float | None:
    values = {
        2: 12.706,
        3: 4.303,
        4: 3.182,
        5: 2.776,
        6: 2.571,
        7: 2.447,
        8: 2.365,
        9: 2.306,
        10: 2.262,
        11: 2.228,
        12: 2.201,
        13: 2.179,
        14: 2.160,
        15: 2.145,
        16: 2.131,
        17: 2.120,
        18: 2.110,
        19: 2.101,
        20: 2.093,
        21: 2.086,
        22: 2.080,
        23: 2.074,
        24: 2.069,
        25: 2.064,
        26: 2.060,
        27: 2.056,
        28: 2.052,
        29: 2.048,
        30: 2.045,
    }
    if sample_count < 2:
        return None
    return values.get(min(sample_count, 30), 1.96)


def _mean_ci95(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"lower": None, "upper": None}
    mean = statistics.fmean(values)
    critical = _t_critical_95(len(values))
    if critical is None:
        return {"lower": None, "upper": None}
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return {
        "lower": round(mean - critical * standard_error, 3),
        "upper": round(mean + critical * standard_error, 3),
    }


def _strategy_regime_fit(
    row: dict[str, Any],
    *,
    market_state_map: dict[str, dict[str, Any]],
    basket_contexts: dict[str, dict[str, Any]],
    fallback_basket: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_positions = (
        (row.get("live_capital_plan") or {}).get("positions") or []
    )
    if live_positions:
        positions = live_positions
        positions_source = "live_capital_plan"
        positions_basket_id = row.get("basket_id")
    else:
        positions = (
            (fallback_basket or {}).get("snapshot") or {}
        ).get("positions") or []
        positions_source = (
            "latest_verified_paper_basket"
            if positions
            else "unavailable"
        )
        positions_basket_id = (fallback_basket or {}).get("id")
    current_regime = _weighted_position_regime(
        positions, market_state_map
    )
    current_regime["positions_source"] = positions_source
    current_regime["positions_basket_id"] = positions_basket_id
    cohort_evidence = []
    for cohort in row.get("primary_cohorts") or []:
        if _number(cohort.get("net_excess_return_pct")) is None:
            continue
        basket_id = str(cohort.get("basket_id") or "")
        frozen = basket_contexts.get(basket_id) or {
            "status": "insufficient",
            "source": "basket_not_available",
            "coverage_pct": 0,
        }
        cohort_evidence.append(
            {
                "basket_id": basket_id,
                "run_id": cohort.get("run_id"),
                "frozen_at": cohort.get("frozen_at"),
                "outcome_date_max": cohort.get("outcome_date_max"),
                "net_excess_return_pct": _round(
                    cohort.get("net_excess_return_pct"), 3
                ),
                "frozen_regime_status": frozen.get("status"),
                "frozen_regime_coverage_pct": frozen.get(
                    "coverage_pct"
                ),
                "regime_source": frozen.get("source"),
                "regime_source_sha256": frozen.get(
                    "source_sha256"
                ),
            }
        )
    cohort_evidence.sort(
        key=lambda item: (
            str(item.get("frozen_at") or ""),
            str(item.get("basket_id") or ""),
        )
    )
    current_status = str(
        current_regime.get("status") or "insufficient"
    )
    matched = [
        item
        for item in cohort_evidence
        if current_status in REGIME_SCORE
        and item.get("frozen_regime_status") == current_status
        and float(item.get("frozen_regime_coverage_pct") or 0) >= 70
    ]
    values = [
        float(item["net_excess_return_pct"]) for item in matched
    ]
    sample_count = len(values)
    mean = statistics.fmean(values) if values else None
    positive_rate = (
        sum(1 for value in values if value > 0)
        / sample_count
        * 100
        if values
        else None
    )
    ci95 = _mean_ci95(values)
    recent = values[-3:]
    recent_failure = bool(
        sample_count >= MIN_REGIME_FIT_COHORTS
        and len(recent) == 3
        and all(value <= 0 for value in recent)
    )
    reasons = []
    if current_status == "insufficient":
        fit_status = "unavailable"
        raw_tilt = 1.0
        reasons.append("当前候选池市场状态证据不足，不做适配倾斜")
    elif sample_count < MIN_REGIME_FIT_COHORTS:
        fit_status = "collecting"
        raw_tilt = 1.0
        reasons.append(
            f"同类环境只有 {sample_count} 个独立前瞻批次，"
            f"少于 {MIN_REGIME_FIT_COHORTS} 个，不做收益倾斜"
        )
    elif recent_failure:
        fit_status = "avoid"
        raw_tilt = 0.0
        reasons.append(
            "最近 3 个同类环境独立前瞻批次均未跑赢基准，"
            "触发环境失配熔断"
        )
    elif (mean or 0) <= 0 or (positive_rate or 0) < 50:
        fit_status = "underweight"
        raw_tilt = 0.75
        reasons.append(
            "同类环境的平均超额不为正或跑赢比例低于 50%，"
            "策略自动降权"
        )
    elif (
        mean is not None
        and mean > 0
        and (positive_rate or 0) >= 60
        and _number(ci95.get("lower"), -1) > 0
    ):
        fit_status = "preferred"
        raw_tilt = 1.10
        reasons.append(
            "同类环境平均超额、跑赢比例与 95% 区间下界均为正，"
            "只允许窄幅优先"
        )
    else:
        fit_status = "neutral"
        raw_tilt = 1.0
        reasons.append("同类环境证据可用，但不足以升权或降权")
    reliability = min(
        1.0, sample_count / FULL_RELIABILITY_COHORTS
    )
    allocation_tilt = (
        0.0
        if fit_status == "avoid"
        else 1 + (raw_tilt - 1) * reliability
    )
    public = {
        "strategy_id": row.get("strategy_id"),
        "strategy_name": row.get("strategy_name"),
        "strategy_version_id": row.get("strategy_version_id"),
        "scorecard_id": row.get("scorecard_id"),
        "current_regime": current_regime,
        "fit_status": fit_status,
        "matched_regime": current_status,
        "matched_cohort_count": sample_count,
        "minimum_cohort_count": MIN_REGIME_FIT_COHORTS,
        "mean_net_excess_return_pct": _round(mean, 3),
        "positive_excess_rate_pct": _round(positive_rate, 2),
        "mean_excess_ci95": ci95,
        "recent_same_regime_returns_pct": [
            round(value, 3) for value in recent
        ],
        "recent_three_nonpositive": recent_failure,
        "reliability_pct": round(reliability * 100, 2),
        "raw_allocation_tilt": round(raw_tilt, 4),
        "allocation_tilt": round(allocation_tilt, 4),
        "market_risk_budget_multiplier": current_regime.get(
            "risk_budget_multiplier"
        ),
        "reasons": reasons,
    }
    evidence = {
        "strategy_id": row.get("strategy_id"),
        "strategy_version_id": row.get("strategy_version_id"),
        "scorecard_id": row.get("scorecard_id"),
        "scorecard_sha256": row.get("scorecard_sha256"),
        "basket_id": row.get("basket_id"),
        "positions_source": positions_source,
        "positions_basket_id": positions_basket_id,
        "current_positions": [
            {
                "market": item.get("market"),
                "symbol": item.get("symbol"),
                "weight_pct": _round(_position_weight(item), 4),
            }
            for item in positions
            if _position_weight(item) > 0
        ],
        "current_regime": current_regime,
        "cohorts": cohort_evidence,
        "fit": {
            key: public.get(key)
            for key in (
                "fit_status",
                "matched_regime",
                "matched_cohort_count",
                "mean_net_excess_return_pct",
                "positive_excess_rate_pct",
                "mean_excess_ci95",
                "recent_same_regime_returns_pct",
                "reliability_pct",
                "raw_allocation_tilt",
                "allocation_tilt",
                "market_risk_budget_multiplier",
            )
        },
    }
    return public, evidence


def compose_regime_hub(
    run_records: list[dict[str, Any]],
    strategy_rows: list[dict[str, Any]],
    *,
    baskets: list[dict[str, Any]] | None = None,
    run_lookup: dict[str, dict[str, Any]] | None = None,
    previous_result: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = _now(now)
    market_states, observations = build_market_states(
        run_records, now=current
    )
    market_state_map = {
        str(item["market"]): item for item in market_states
    }
    run_map = dict(run_lookup or {})
    run_map.update(
        {
            str(item.get("id") or ""): item
            for item in run_records
            if item.get("id")
        }
    )
    relevant_basket_ids = {
        str(cohort.get("basket_id") or "")
        for row in strategy_rows
        for cohort in row.get("primary_cohorts") or []
        if cohort.get("basket_id")
    }
    basket_contexts = {}
    basket_bindings = []
    for basket in baskets or []:
        basket_id = str(basket.get("id") or "")
        if basket_id not in relevant_basket_ids:
            continue
        frozen = classify_frozen_basket(
            basket,
            bound_run=run_map.get(str(basket.get("run_id") or "")),
        )
        basket_contexts[basket_id] = frozen
        basket_bindings.append(
            {
                "basket_id": basket_id,
                "snapshot_sha256": basket.get("snapshot_sha256"),
                "run_id": basket.get("run_id"),
                "regime_status": frozen.get("status"),
                "regime_source": frozen.get("source"),
                "regime_source_sha256": frozen.get(
                    "source_sha256"
                ),
            }
        )
    latest_basket_by_version: dict[str, dict[str, Any]] = {}
    latest_basket_by_strategy: dict[str, dict[str, Any]] = {}
    for basket in baskets or []:
        if not basket.get("snapshot_verified"):
            continue
        snapshot = basket.get("snapshot") or {}
        snapshot_strategy = snapshot.get("strategy") or {}
        version_id = str(snapshot_strategy.get("version_id") or "")
        strategy_id = str(snapshot_strategy.get("id") or "")
        frozen_at = str(
            snapshot.get("frozen_at")
            or basket.get("created_at")
            or ""
        )
        for key, target in (
            (version_id, latest_basket_by_version),
            (strategy_id, latest_basket_by_strategy),
        ):
            if not key:
                continue
            existing = target.get(key)
            existing_at = str(
                ((existing or {}).get("snapshot") or {}).get(
                    "frozen_at"
                )
                or (existing or {}).get("created_at")
                or ""
            )
            if existing is None or frozen_at > existing_at:
                target[key] = basket
    strategy_fits = []
    strategy_evidence = []
    for row in strategy_rows:
        fallback_basket = latest_basket_by_version.get(
            str(row.get("strategy_version_id") or "")
        ) or latest_basket_by_strategy.get(
            str(row.get("strategy_id") or "")
        )
        public, evidence_row = _strategy_regime_fit(
            row,
            market_state_map=market_state_map,
            basket_contexts=basket_contexts,
            fallback_basket=fallback_basket,
        )
        strategy_fits.append(public)
        strategy_evidence.append(evidence_row)
    strategy_fits.sort(
        key=lambda item: str(item.get("strategy_id") or "")
    )
    strategy_evidence.sort(
        key=lambda item: str(item.get("strategy_id") or "")
    )

    available_states = [
        item
        for item in market_states
        if item.get("status") in REGIME_SCORE
    ]
    if available_states:
        overall_score = statistics.fmean(
            float(item["weighted_regime_score"] or 0)
            for item in available_states
        )
        overall_status = _status_from_score(overall_score)
    else:
        overall_score = None
        overall_status = "insufficient"
    fit_risk_multipliers = [
        float(item["market_risk_budget_multiplier"])
        for item in strategy_fits
        if _number(item.get("market_risk_budget_multiplier"))
        is not None
        and (item.get("current_regime") or {}).get(
            "market_weights_pct"
        )
    ]
    if fit_risk_multipliers:
        portfolio_risk_multiplier = statistics.fmean(
            fit_risk_multipliers
        )
        risk_basis = "current_strategy_market_exposure"
    elif market_states:
        portfolio_risk_multiplier = statistics.fmean(
            float(item["risk_budget_multiplier"])
            for item in market_states
        )
        risk_basis = "equal_market_fallback"
    else:
        portfolio_risk_multiplier = 0.50
        risk_basis = "insufficient_fallback"
    portfolio_risk_multiplier = min(
        1.0, max(0.0, portfolio_risk_multiplier)
    )

    evidence_cutoffs = [
        str(item.get("observed_at") or "") for item in observations
    ] + [
        str(item.get("evidence_cutoff_at") or "")
        for item in strategy_rows
    ]
    evidence_cutoff = (
        max(value for value in evidence_cutoffs if value)
        if any(evidence_cutoffs)
        else None
    )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "evidence_cutoff_at": evidence_cutoff,
        "bindings": {
            "run_ids": sorted(
                str(item.get("run_id"))
                for item in observations
                if item.get("run_id")
            ),
            "run_result_sha256s": sorted(
                str(item.get("result_sha256"))
                for item in observations
                if item.get("result_sha256")
            ),
            "scorecard_ids": sorted(
                str(item.get("scorecard_id"))
                for item in strategy_rows
                if item.get("scorecard_id")
            ),
            "scorecard_sha256s": sorted(
                str(item.get("scorecard_sha256"))
                for item in strategy_rows
                if item.get("scorecard_sha256")
            ),
            "basket_ids": sorted(relevant_basket_ids),
            "basket_snapshot_sha256s": sorted(
                str(item.get("snapshot_sha256"))
                for item in basket_bindings
                if item.get("snapshot_sha256")
            ),
        },
        "market_observations": observations,
        "basket_regime_bindings": sorted(
            basket_bindings,
            key=lambda item: str(item.get("basket_id") or ""),
        ),
        "strategies": strategy_evidence,
    }
    evidence_sha = sha256_payload(evidence)

    previous_states = {
        str(item.get("market") or ""): str(
            item.get("status") or ""
        )
        for item in (previous_result or {}).get("market_states") or []
    }
    changes = [
        {
            "market": item["market"],
            "from": previous_states.get(item["market"]),
            "to": item["status"],
        }
        for item in market_states
        if previous_states.get(item["market"])
        and previous_states.get(item["market"]) != item["status"]
    ]
    previous_risk = _number(
        ((previous_result or {}).get("portfolio_risk_budget") or {}).get(
            "multiplier"
        )
    )
    if not previous_result:
        transition_state = "initial"
    elif changes or (
        previous_risk is not None
        and abs(previous_risk - portfolio_risk_multiplier) >= 0.05
    ):
        transition_state = "changed"
    else:
        transition_state = "stable"

    preferred_count = sum(
        1
        for item in strategy_fits
        if item.get("fit_status") == "preferred"
    )
    avoid_count = sum(
        1
        for item in strategy_fits
        if item.get("fit_status") == "avoid"
    )
    if overall_status == "risk_on":
        headline = (
            "候选池环境偏强；总风险仍不超过原委员会上限，"
            "只在策略之间做证据支持的窄幅倾斜"
        )
    elif overall_status == "defensive":
        headline = (
            "候选池进入防守环境，风险预算已主动收缩并增加现金"
        )
    elif overall_status == "mixed":
        headline = (
            "市场候选池处于震荡或分歧状态，风险预算保持折扣"
        )
    else:
        headline = (
            "缺少 14 天内可验证的候选池状态，风险层使用保守预算"
        )
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(current),
        "evidence_cutoff_at": evidence_cutoff,
        "evidence_sha256": evidence_sha,
        "status": overall_status,
        "label": REGIME_LABEL[overall_status],
        "headline": headline,
        "summary": {
            "market_count": len(MARKETS),
            "current_market_count": len(available_states),
            "source_count": len(observations),
            "candidate_sample_count": sum(
                int(item.get("sample_count") or 0)
                for item in observations
            ),
            "strategy_count": len(strategy_fits),
            "preferred_strategy_count": preferred_count,
            "avoid_strategy_count": avoid_count,
            "matched_regime_cohort_count": sum(
                int(item.get("matched_cohort_count") or 0)
                for item in strategy_fits
            ),
        },
        "portfolio_risk_budget": {
            "multiplier": round(portfolio_risk_multiplier, 4),
            "budget_pct_of_committee_limit": round(
                portfolio_risk_multiplier * 100, 2
            ),
            "minimum_cash_added_pct_of_committee_limit": round(
                (1 - portfolio_risk_multiplier) * 100, 2
            ),
            "basis": risk_basis,
            "leverage_allowed": False,
            "can_increase_baseline_risk": False,
        },
        "market_states": market_states,
        "strategy_fits": strategy_fits,
        "transition": {
            "state": transition_state,
            "market_changes": changes,
            "previous_risk_budget_multiplier": _round(
                previous_risk, 4
            ),
            "current_risk_budget_multiplier": round(
                portfolio_risk_multiplier, 4
            ),
            "previous_snapshot_available": bool(previous_result),
        },
        "policy": {
            "maximum_source_age_days": MAX_RUN_AGE_DAYS,
            "maximum_sources_per_market": MAX_SOURCES_PER_MARKET,
            "minimum_same_regime_cohorts": MIN_REGIME_FIT_COHORTS,
            "full_reliability_cohorts": FULL_RELIABILITY_COHORTS,
            "risk_on_base_multiplier": 1.0,
            "mixed_base_multiplier": 0.85,
            "defensive_base_multiplier": 0.60,
            "insufficient_base_multiplier": 0.50,
            "preferred_raw_tilt": 1.10,
            "underweight_raw_tilt": 0.75,
        },
        "methodology": {
            "market_regime": (
                "每个市场只取最多 5 个策略版本的最新不可变扫描，"
                "按候选样本数与 14 天内新鲜度加权；状态仍只代表"
                "候选池，不外推为交易所全市场。"
            ),
            "frozen_regime": (
                "新纸面组合直接冻结当时状态；旧组合只从其绑定且"
                "哈希通过的原扫描结果临时还原，不改写历史记录。"
            ),
            "strategy_fit": (
                "只比较当前环境与冻结时环境相同的独立前瞻批次；"
                "少于 4 个不倾斜，最近 3 个同类环境连续未跑赢基准"
                "则停用，升权最高仅 10% 且按 8 个样本向中性收缩。"
            ),
            "risk_budget": (
                "趋势状态、候选池波动和来源厚度只会维持或降低"
                "委员会原始风险上限；不允许杠杆，也不会因偏强状态"
                "把总风险提高到原上限以上。"
            ),
        },
        "boundaries": {
            "candidate_pool_scope_only": True,
            "calibrated_probability_provided": False,
            "absolute_price_direction_claimed": False,
            "execution_authorized": False,
            "automatic_order_creation": False,
            "return_guaranteed": False,
            "interpretation": (
                "本中枢回答“当前证据环境下哪些已验证策略更适合、"
                "总风险应缩到多少”，不回答某只股票必涨或必跌。"
            ),
        },
    }
    return result, evidence


def _load_current_inputs(
    *,
    user_id: str,
    strategy_rows: list[dict[str, Any]],
    opp_repo: OpportunityRepository,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    run_records = []
    run_lookup: dict[str, dict[str, Any]] = {}
    for summary in opp_repo.list_runs(user_id=user_id, limit=200):
        if summary.get("status") not in {"succeeded", "partial"}:
            continue
        run = opp_repo.get_run(
            str(summary["id"]),
            user_id=user_id,
            include_events=False,
        )
        if run is not None:
            run_records.append(run)
            run_lookup[str(run["id"])] = run
    baskets = opp_repo.list_paper_baskets(
        user_id=user_id, limit=200
    )
    needed = {
        str(cohort.get("basket_id") or "")
        for row in strategy_rows
        for cohort in row.get("primary_cohorts") or []
        if cohort.get("basket_id")
    }
    available = {
        str(item.get("id") or "") for item in baskets
    }
    for basket_id in sorted(needed - available):
        basket = opp_repo.get_paper_basket(
            basket_id, user_id=user_id
        )
        if basket is not None:
            baskets.append(basket)
    for basket in baskets:
        run_id = str(basket.get("run_id") or "")
        if not run_id or run_id in run_lookup:
            continue
        run = opp_repo.get_run(
            run_id, user_id=user_id, include_events=False
        )
        if run is not None:
            run_lookup[run_id] = run
    return run_records, baskets, run_lookup


def current_regime_context(
    *,
    user_id: str,
    strategy_rows: list[dict[str, Any]],
    now: dt.datetime | None = None,
    opp_repo: OpportunityRepository = opportunity_repository,
    snapshot_repo: OpportunityRegimeRepository = regime_repository,
) -> dict[str, Any]:
    run_records, baskets, run_lookup = _load_current_inputs(
        user_id=user_id,
        strategy_rows=strategy_rows,
        opp_repo=opp_repo,
    )
    latest = snapshot_repo.latest_snapshot(user_id=user_id)
    previous = (
        latest.get("result")
        if latest and (latest.get("integrity") or {}).get("verified")
        else None
    )
    result, evidence = compose_regime_hub(
        run_records,
        strategy_rows,
        baskets=baskets,
        run_lookup=run_lookup,
        previous_result=previous,
        now=now,
    )
    evidence_sha = sha256_payload(evidence)
    return {
        **result,
        "persistence": {
            "latest_snapshot": (
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


def _current_strategy_rows(
    *,
    user_id: str,
    profit_lab_loader=None,
    profit_repo=None,
) -> list[dict[str, Any]]:
    import opportunity_committee_service
    import opportunity_profit_service
    from opportunity_profit_repository import (
        repository as default_profit_repository,
    )

    loader = profit_lab_loader or (
        lambda: opportunity_profit_service.profit_lab_overview(
            user_id=user_id
        )
    )
    return opportunity_committee_service.strategy_evidence_rows(
        loader(),
        profit_repo=profit_repo or default_profit_repository,
        user_id=user_id,
    )


def current_regime_hub(
    *,
    user_id: str,
    now: dt.datetime | None = None,
    profit_lab_loader=None,
    profit_repo=None,
    opp_repo: OpportunityRepository = opportunity_repository,
    snapshot_repo: OpportunityRegimeRepository = regime_repository,
) -> dict[str, Any]:
    rows = _current_strategy_rows(
        user_id=user_id,
        profit_lab_loader=profit_lab_loader,
        profit_repo=profit_repo,
    )
    return current_regime_context(
        user_id=user_id,
        strategy_rows=rows,
        now=now,
        opp_repo=opp_repo,
        snapshot_repo=snapshot_repo,
    )


def freeze_regime_hub(
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
    profit_lab_loader=None,
    profit_repo=None,
    opp_repo: OpportunityRepository = opportunity_repository,
    snapshot_repo: OpportunityRegimeRepository = regime_repository,
) -> tuple[dict[str, Any], bool]:
    rows = _current_strategy_rows(
        user_id=user_id,
        profit_lab_loader=profit_lab_loader,
        profit_repo=profit_repo,
    )
    run_records, baskets, run_lookup = _load_current_inputs(
        user_id=user_id,
        strategy_rows=rows,
        opp_repo=opp_repo,
    )
    latest = snapshot_repo.latest_snapshot(user_id=user_id)
    previous = (
        latest.get("result")
        if latest and (latest.get("integrity") or {}).get("verified")
        else None
    )
    result, evidence = compose_regime_hub(
        run_records,
        rows,
        baskets=baskets,
        run_lookup=run_lookup,
        previous_result=previous,
        now=now,
    )
    return snapshot_repo.create_snapshot(
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        status=str(result["status"]),
        evidence_cutoff_at=result.get("evidence_cutoff_at"),
        evidence=evidence,
        result=result,
    )


def regime_snapshot_history(
    *,
    user_id: str,
    limit: int = 30,
    snapshot_repo: OpportunityRegimeRepository = regime_repository,
) -> dict[str, Any]:
    items = snapshot_repo.list_snapshots(
        user_id=user_id, limit=limit
    )
    return {
        "items": items,
        "count": len(items),
        "engine_version": ENGINE_VERSION,
    }
