# -*- coding: utf-8 -*-
"""Forward, cost-aware profit validation for Opportunity Factory strategies.

The service deliberately separates candidate generation from capital
eligibility. A strategy can receive a non-zero manual pilot budget only after
independent frozen baskets have accumulated enough forward observations and
passed benchmark, cost, coverage and drawdown gates.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any, Callable

import portfolio_valuation
import storage
from background_jobs import BackgroundJobRepository
from opportunity_profit_repository import (
    POLICY_SCHEMA_VERSION,
    SCORECARD_SCHEMA_VERSION,
    OpportunityProfitRepository,
    repository as profit_repository,
)
from opportunity_repository import (
    OpportunityConflictError,
    OpportunityNotFoundError,
    OpportunityRepository,
    repository as opportunity_repository,
)
from task_queue import QUEUE_MARKET, enqueue_background_job, uses_celery_queue


ENGINE_VERSION = "opportunity_profit_engine@1.0.0"
DEFAULT_POLICY = {
    "schema_version": POLICY_SCHEMA_VERSION,
    "evaluation_horizons": [5, 20, 60],
    "primary_horizon": 20,
    "round_trip_cost_bps": 30.0,
    "minimum_coverage_pct": 90.0,
    "minimum_mature_baskets": 6,
    "minimum_mean_excess_return_pct": 0.5,
    "minimum_positive_excess_rate_pct": 55.0,
    "maximum_cohort_drawdown_pct": 15.0,
    "maximum_manual_pilot_pct": 5.0,
    "latest_basket_max_age_days": 14,
}


def _now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _now(value).isoformat(timespec="milliseconds")


def _parse_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 3) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def normalize_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = {**DEFAULT_POLICY, **dict(value or {})}
    horizons = sorted(
        {
            int(item)
            for item in raw.get("evaluation_horizons") or []
            if str(item).strip()
        }
    )
    if not horizons or len(horizons) > 5 or any(item < 3 or item > 252 for item in horizons):
        raise ValueError("观察窗口必须包含 1-5 个 3 至 252 交易日的唯一整数")
    primary = int(raw.get("primary_horizon") or 0)
    if primary not in horizons:
        raise ValueError("主验证窗口必须属于观察窗口")

    numeric_ranges = {
        "round_trip_cost_bps": (10.0, 500.0),
        "minimum_coverage_pct": (80.0, 100.0),
        "minimum_mean_excess_return_pct": (0.0, 20.0),
        "minimum_positive_excess_rate_pct": (50.0, 100.0),
        "maximum_cohort_drawdown_pct": (3.0, 25.0),
        "maximum_manual_pilot_pct": (0.5, 5.0),
    }
    normalized: dict[str, Any] = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "evaluation_horizons": horizons,
        "primary_horizon": primary,
    }
    for field, (minimum, maximum) in numeric_ranges.items():
        number = _number(raw.get(field))
        if number is None or number < minimum or number > maximum:
            raise ValueError(f"{field} 必须在 {minimum:g} 至 {maximum:g} 之间")
        normalized[field] = round(number, 4)

    minimum_baskets = int(raw.get("minimum_mature_baskets") or 0)
    if minimum_baskets < 6 or minimum_baskets > 100:
        raise ValueError("minimum_mature_baskets 必须在 6 至 100 之间")
    max_age = int(raw.get("latest_basket_max_age_days") or 0)
    if max_age < 3 or max_age > 30:
        raise ValueError("latest_basket_max_age_days 必须在 3 至 30 之间")
    normalized["minimum_mature_baskets"] = minimum_baskets
    normalized["latest_basket_max_age_days"] = max_age
    return normalized


def get_policy(
    strategy_id: str,
    *,
    user_id: str,
    opp_repo: OpportunityRepository = opportunity_repository,
    profit_repo: OpportunityProfitRepository = profit_repository,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = strategy or opp_repo.get_strategy(strategy_id, user_id=user_id)
    if strategy is None:
        raise OpportunityNotFoundError("机会策略不存在")
    persisted = profit_repo.latest_policy(
        strategy_id,
        user_id=user_id,
        strategy_version_id=strategy["version_id"],
    )
    if persisted is not None:
        if not persisted.get("integrity_verified"):
            raise OpportunityConflictError("收益验证政策完整性校验失败")
        return persisted
    return {
        "id": None,
        "user_id": user_id,
        "strategy_id": strategy_id,
        "strategy_version_id": strategy["version_id"],
        "version_no": 0,
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy": normalize_policy(None),
        "payload_sha256": None,
        "actor_id": "system-default",
        "created_at": None,
        "integrity_verified": True,
        "persisted": False,
    }


def save_policy(
    strategy_id: str,
    value: dict[str, Any],
    *,
    user_id: str,
    actor_id: str,
    opp_repo: OpportunityRepository = opportunity_repository,
    profit_repo: OpportunityProfitRepository = profit_repository,
) -> dict[str, Any]:
    strategy = opp_repo.get_strategy(strategy_id, user_id=user_id)
    if strategy is None:
        raise OpportunityNotFoundError("机会策略不存在")
    if strategy.get("status") != "active":
        raise OpportunityConflictError("已归档策略不能创建新的收益验证政策")
    if not strategy.get("definition_verified"):
        raise OpportunityConflictError("机会策略版本完整性校验失败")
    return profit_repo.create_policy(
        user_id=user_id,
        strategy_id=strategy_id,
        strategy_version_id=strategy["version_id"],
        policy=normalize_policy(value),
        actor_id=actor_id,
    )


def _t_critical_95(sample_count: int) -> float | None:
    if sample_count < 2:
        return None
    degrees = sample_count - 1
    table = {
        1: 12.706,
        2: 4.303,
        3: 3.182,
        4: 2.776,
        5: 2.571,
        6: 2.447,
        7: 2.365,
        8: 2.306,
        9: 2.262,
        10: 2.228,
        12: 2.179,
        15: 2.131,
        20: 2.086,
        25: 2.060,
        30: 2.042,
    }
    for boundary in sorted(table):
        if degrees <= boundary:
            return table[boundary]
    return 1.96


def _mean_ci95(values: list[float]) -> dict[str, float | None]:
    return _mean_ci(values, two_sided_alpha=0.05)


def _mean_ci(
    values: list[float],
    *,
    two_sided_alpha: float,
) -> dict[str, float | None]:
    if not values:
        return {"lower": None, "upper": None, "half_width": None}
    mean = statistics.fmean(values)
    if abs(two_sided_alpha - 0.05) < 1e-12:
        critical = _t_critical_95(len(values))
    elif len(values) < 2:
        critical = None
    else:
        degrees = len(values) - 1
        z_value = statistics.NormalDist().inv_cdf(
            1 - two_sided_alpha / 2
        )
        z2 = z_value * z_value
        critical = (
            z_value
            + (z_value**3 + z_value) / (4 * degrees)
            + (
                5 * z_value**5
                + 16 * z_value**3
                + 3 * z_value
            )
            / (96 * degrees**2)
            + (
                3 * z_value**7
                + 19 * z_value**5
                + 17 * z_value**3
                - 15 * z_value
            )
            / (384 * degrees**3)
        )
    if critical is None:
        return {"lower": None, "upper": None, "half_width": None}
    half_width = critical * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "lower": round(mean - half_width, 3),
        "upper": round(mean + half_width, 3),
        "half_width": round(half_width, 3),
    }


def _max_drawdown(levels: list[float]) -> float:
    peak = 1.0
    worst = 0.0
    for level in levels:
        peak = max(peak, level)
        if peak > 0:
            worst = min(worst, level / peak - 1)
    return round(abs(worst) * 100, 3)


def _valid_observations(basket: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for observation in reversed(basket.get("observations") or []):
        payload = observation.get("payload") or {}
        if (
            observation.get("payload_verified")
            and payload.get("schema_version") == "opportunity_paper_observation.v2"
            and _number(payload.get("observed_trading_days_min")) is not None
        ):
            result.append(observation)
    return result


def _cohort_at_horizon(
    basket: dict[str, Any],
    horizon: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    observations = _valid_observations(basket)
    threshold = float(policy["minimum_coverage_pct"])
    selected = None
    selected_metric: dict[str, Any] | None = None
    excluded_reasons: list[str] = []
    if not basket.get("snapshot_verified"):
        excluded_reasons.append("纸面组合快照完整性失败")
    for observation in observations:
        payload = observation["payload"]
        exact_metric = next(
            (
                item
                for item in payload.get("horizons") or []
                if int(item.get("trading_days") or 0) == horizon
                and item.get("exact_horizon")
            ),
            None,
        )
        if exact_metric is not None:
            if (
                float(
                    exact_metric.get("covered_position_weight_pct") or 0
                )
                < threshold
            ):
                excluded_reasons.append("精确窗口股票观察覆盖不足")
                continue
            if (
                float(
                    exact_metric.get("benchmark_coverage_weight_pct") or 0
                )
                < threshold
            ):
                excluded_reasons.append("精确窗口基准观察覆盖不足")
                continue
            selected = observation
            selected_metric = exact_metric
            break
        elapsed = int(payload.get("observed_trading_days_min") or 0)
        if elapsed < horizon:
            continue
        if float(payload.get("covered_position_weight_pct") or 0) < threshold:
            excluded_reasons.append("股票观察覆盖不足")
            continue
        if float(payload.get("benchmark_coverage_weight_pct") or 0) < threshold:
            excluded_reasons.append("基准观察覆盖不足")
            continue
        selected = observation
        break

    strategy = (basket.get("snapshot") or {}).get("strategy") or {}
    base = {
        "basket_id": basket.get("id"),
        "run_id": basket.get("run_id"),
        "strategy_id": strategy.get("id"),
        "strategy_version_id": strategy.get("version_id"),
        "frozen_at": (basket.get("snapshot") or {}).get("frozen_at")
        or basket.get("created_at"),
        "horizon": horizon,
    }
    if selected is None or not basket.get("snapshot_verified"):
        latest = observations[-1]["payload"] if observations else {}
        return {
            **base,
            "status": "pending" if basket.get("snapshot_verified") else "excluded",
            "trading_days_observed": int(
                latest.get("observed_trading_days_min") or 0
            ),
            "reasons": list(dict.fromkeys(excluded_reasons))
            or [f"尚未积累 {horizon} 个交易日"],
        }

    payload = selected["payload"]
    metric = selected_metric or payload
    gross = float(metric.get("gross_weighted_return_pct") or 0)
    invested = float(metric.get("invested_weight_pct") or 0)
    benchmark = float(metric.get("benchmark_return_pct") or 0)
    cost_drag = invested / 100 * float(policy["round_trip_cost_bps"]) / 100
    net = gross - cost_drag
    excess = net - benchmark
    levels = []
    for observation in observations:
        point = observation["payload"]
        if int(point.get("observed_trading_days_min") or 0) > int(
            payload.get("observed_trading_days_min") or 0
        ):
            break
        point_gross = float(point.get("gross_weighted_return_pct") or 0)
        point_invested = float(point.get("invested_weight_pct") or 0)
        point_cost = point_invested / 100 * float(
            policy["round_trip_cost_bps"]
        ) / 100
        levels.append(1 + (point_gross - point_cost) / 100)
    exact_drawdown = _number(
        metric.get("conservative_component_drawdown_pct")
    )
    cohort_drawdown = (
        round(exact_drawdown, 3)
        if exact_drawdown is not None
        else _max_drawdown(levels)
    )
    return {
        **base,
        "status": "mature",
        "observation_id": selected.get("id"),
        "observed_at": selected.get("observed_at"),
        "trading_days_observed": (
            horizon
            if selected_metric is not None
            else int(payload.get("observed_trading_days_min") or 0)
        ),
        "horizon_measurement": (
            "exact_trading_day"
            if selected_metric is not None
            else "legacy_observation_point"
        ),
        "outcome_date_min": metric.get("outcome_date_min"),
        "outcome_date_max": metric.get("outcome_date_max"),
        "gross_return_pct": round(gross, 3),
        "cost_drag_pct": round(cost_drag, 3),
        "net_return_pct": round(net, 3),
        "benchmark_return_pct": round(benchmark, 3),
        "net_excess_return_pct": round(excess, 3),
        "positive_net_return": net > 0,
        "positive_excess_return": excess > 0,
        "cohort_max_drawdown_pct": cohort_drawdown,
        "position_coverage_pct": _round(
            metric.get("covered_position_weight_pct"), 2
        ),
        "benchmark_coverage_pct": _round(
            metric.get("benchmark_coverage_weight_pct"), 2
        ),
        "round_trip_cost_bps": policy["round_trip_cost_bps"],
        "reasons": [],
    }


def _independent_cohorts(
    cohorts: list[dict[str, Any]], horizon: int
) -> list[dict[str, Any]]:
    """Deterministically exclude overlapping cohort starts before outcomes."""
    spacing_days = max(1, int(math.ceil(horizon * 7 / 5)))
    ordered = sorted(
        cohorts,
        key=lambda item: (
            _parse_datetime(item.get("frozen_at"))
            or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
            str(item.get("basket_id") or ""),
        ),
    )
    accepted_at: dt.datetime | None = None
    normalized: dict[str, dict[str, Any]] = {}
    for cohort in ordered:
        basket_id = str(cohort.get("basket_id") or "")
        frozen_at = _parse_datetime(cohort.get("frozen_at"))
        if frozen_at is None:
            normalized[basket_id] = {
                **cohort,
                "original_status": cohort.get("status"),
                "status": "excluded",
                "independent": False,
                "independence_spacing_days": spacing_days,
                "reasons": list(
                    dict.fromkeys(
                        [
                            *(cohort.get("reasons") or []),
                            "冻结时间缺失，不能验证批次独立性",
                        ]
                    )
                ),
            }
            continue
        if accepted_at is None or (frozen_at - accepted_at).days >= spacing_days:
            accepted_at = frozen_at
            normalized[basket_id] = {
                **cohort,
                "independent": True,
                "independence_spacing_days": spacing_days,
            }
            continue
        normalized[basket_id] = {
            **cohort,
            "original_status": cohort.get("status"),
            "status": "excluded",
            "independent": False,
            "independence_spacing_days": spacing_days,
            "reasons": [
                (
                    f"与已计入批次的冻结起点间隔少于 {spacing_days} 个自然日，"
                    "为防止重叠样本刷高胜率而排除"
                )
            ],
        }
    return [
        normalized.get(str(item.get("basket_id") or ""), item)
        for item in cohorts
    ]


def _horizon_summary(
    horizon: int,
    cohorts: list[dict[str, Any]],
    *,
    strategy_family_size: int,
) -> dict[str, Any]:
    mature = [item for item in cohorts if item.get("status") == "mature"]
    pending = [item for item in cohorts if item.get("status") == "pending"]
    excluded = [item for item in cohorts if item.get("status") == "excluded"]
    net_values = [float(item["net_return_pct"]) for item in mature]
    excess_values = [float(item["net_excess_return_pct"]) for item in mature]
    positive_excess = sum(1 for value in excess_values if value > 0)
    positive_net = sum(1 for value in net_values if value > 0)
    family_size = max(1, int(strategy_family_size))
    family_alpha = 0.05 / family_size
    return {
        "horizon_trading_days": horizon,
        "independence_spacing_days": max(
            (
                int(item.get("independence_spacing_days") or 0)
                for item in cohorts
            ),
            default=max(1, int(math.ceil(horizon * 7 / 5))),
        ),
        "mature_count": len(mature),
        "pending_count": len(pending),
        "excluded_count": len(excluded),
        "overlap_excluded_count": sum(
            1
            for item in excluded
            if item.get("original_status") is not None
        ),
        "mean_net_return_pct": (
            round(statistics.fmean(net_values), 3) if net_values else None
        ),
        "median_net_return_pct": (
            round(statistics.median(net_values), 3) if net_values else None
        ),
        "mean_net_excess_return_pct": (
            round(statistics.fmean(excess_values), 3) if excess_values else None
        ),
        "median_net_excess_return_pct": (
            round(statistics.median(excess_values), 3) if excess_values else None
        ),
        "positive_net_rate_pct": (
            round(positive_net / len(mature) * 100, 2) if mature else None
        ),
        "positive_excess_rate_pct": (
            round(positive_excess / len(mature) * 100, 2) if mature else None
        ),
        "mean_excess_ci95": _mean_ci95(excess_values),
        "mean_excess_familywise_ci95": {
            **_mean_ci(
                excess_values,
                two_sided_alpha=family_alpha,
            ),
            "strategy_family_size": family_size,
            "familywise_confidence_pct": 95.0,
            "per_strategy_confidence_pct": round(
                (1 - family_alpha) * 100, 4
            ),
            "method": "Bonferroni family-wise error control",
        },
        "best_excess_return_pct": max(excess_values) if excess_values else None,
        "worst_excess_return_pct": min(excess_values) if excess_values else None,
        "worst_cohort_drawdown_pct": (
            max(float(item["cohort_max_drawdown_pct"]) for item in mature)
            if mature
            else None
        ),
    }


def _capital_gate(
    primary: dict[str, Any],
    policy: dict[str, Any],
    *,
    basket_count: int,
) -> dict[str, Any]:
    mature = int(primary.get("mature_count") or 0)
    required = int(policy["minimum_mature_baskets"])
    reasons: list[str] = []
    checks = {
        "minimum_mature_baskets": mature >= required,
        "positive_mean_net_excess": bool(
            _number(primary.get("mean_net_excess_return_pct")) is not None
            and float(primary["mean_net_excess_return_pct"])
            >= float(policy["minimum_mean_excess_return_pct"])
        ),
        "positive_excess_rate": bool(
            _number(primary.get("positive_excess_rate_pct")) is not None
            and float(primary["positive_excess_rate_pct"])
            >= float(policy["minimum_positive_excess_rate_pct"])
        ),
        "drawdown_within_limit": bool(
            _number(primary.get("worst_cohort_drawdown_pct")) is not None
            and float(primary["worst_cohort_drawdown_pct"])
            <= float(policy["maximum_cohort_drawdown_pct"])
        ),
        "confidence_interval_above_zero": bool(
            _number((primary.get("mean_excess_ci95") or {}).get("lower")) is not None
            and float(primary["mean_excess_ci95"]["lower"]) > 0
        ),
        "multiple_testing_guard": bool(
            _number(
                (
                    primary.get("mean_excess_familywise_ci95") or {}
                ).get("lower")
            )
            is not None
            and float(
                primary["mean_excess_familywise_ci95"]["lower"]
            )
            > 0
        ),
    }
    if basket_count == 0:
        status = "empty"
        reasons.append("尚未冻结任何前瞻纸面组合")
    elif not checks["minimum_mature_baskets"]:
        status = "collecting"
        reasons.append(f"主窗口只有 {mature}/{required} 个独立成熟批次")
    elif not checks["drawdown_within_limit"]:
        status = "suspended"
        reasons.append("前瞻批次最大回撤超过收益验证政策上限")
    elif not checks["positive_mean_net_excess"] or not checks["positive_excess_rate"]:
        status = "suspended"
        reasons.append("成本后平均超额或胜过基准的批次比例未通过")
    elif (
        checks["confidence_interval_above_zero"]
        and checks["multiple_testing_guard"]
    ):
        status = "limited_manual_pilot"
        reasons.append(
            "前瞻成本后超额、命中率、回撤与跨策略校正置信区间全部通过"
        )
    else:
        status = "watch"
        reasons.append(
            (
                "平均结果通过，但跨策略多重检验校正后仍覆盖零超额"
                if checks["confidence_interval_above_zero"]
                else "平均结果通过，但样本不确定性仍覆盖零超额"
            )
        )
    return {
        "status": status,
        "capital_eligible": status == "limited_manual_pilot",
        "maximum_manual_pilot_pct": (
            policy["maximum_manual_pilot_pct"]
            if status == "limited_manual_pilot"
            else 0.0
        ),
        "checks": checks,
        "reasons": reasons,
        "execution_authorized": False,
        "boundary": (
            "资金资格只允许生成受限人工试运行预算，不创建订单、不连接券商、"
            "不把统计结果解释为未来收益承诺。"
        ),
    }


def _allowed_stock_markets(profile: dict[str, Any]) -> set[str]:
    mapping = {
        "mainland": "A股",
        "hong_kong": "港股",
        "united_states": "美股",
    }
    return {
        mapping[item]
        for item in profile.get("allowed_fund_markets") or []
        if item in mapping
    }


def _capital_plan(
    *,
    user_id: str,
    gate: dict[str, Any],
    policy: dict[str, Any],
    baskets: list[dict[str, Any]],
    current: dt.datetime,
) -> dict[str, Any]:
    blocked = {
        "status": "blocked",
        "execution_authorized": False,
        "positions": [],
        "planned_budget_cny": 0.0,
        "unallocated_cash_cny": 0.0,
        "reasons": [],
    }
    if not gate.get("capital_eligible"):
        blocked["reasons"] = list(gate.get("reasons") or [])
        return blocked

    profile = storage.get_investment_profile(user_id=user_id)
    if not profile.get("configured") or not (
        profile.get("governance_integrity") or {}
    ).get("verified"):
        blocked["reasons"] = ["需要先激活完整且可核验的个人投资政策"]
        return blocked
    monthly_budget = float(profile.get("monthly_budget") or 0)
    if monthly_budget <= 0:
        blocked["reasons"] = ["个人投资政策中的月度预算为 0"]
        return blocked
    valuation = portfolio_valuation.latest_portfolio_valuation(user_id=user_id)
    valuation_gate = valuation.get("runtime_gate") or {}
    if not valuation_gate.get("trade_amount_eligible"):
        blocked["reasons"] = list(valuation_gate.get("reasons") or []) or [
            "当前组合估值未通过交易金额门禁"
        ]
        blocked["valuation_snapshot_id"] = (
            valuation.get("snapshot") or {}
        ).get("id")
        return blocked

    fresh_baskets = []
    max_age = int(policy["latest_basket_max_age_days"])
    for basket in baskets:
        frozen = _parse_datetime(
            (basket.get("snapshot") or {}).get("frozen_at")
            or basket.get("created_at")
        )
        if basket.get("snapshot_verified") and frozen and (
            current - frozen
        ).days <= max_age:
            fresh_baskets.append(basket)
    if not fresh_baskets:
        blocked["reasons"] = [
            f"没有 {max_age} 天内、完整性通过的最新纸面组合；请先重跑已验证策略"
        ]
        return blocked
    basket = max(
        fresh_baskets,
        key=lambda item: str(
            (item.get("snapshot") or {}).get("frozen_at")
            or item.get("created_at")
            or ""
        ),
    )
    snapshot = basket.get("snapshot") or {}
    positions = snapshot.get("positions") or []
    if not positions:
        blocked["reasons"] = ["最新纸面组合没有可用持仓"]
        return blocked

    valuation_snapshot = valuation.get("snapshot") or {}
    valuation_payload = valuation_snapshot.get("payload") or {}
    total_value = float((valuation_payload.get("summary") or {}).get("total_value") or 0)
    if total_value <= 0:
        blocked["reasons"] = ["当前组合总估值不可用"]
        return blocked
    pilot_cap = total_value * float(policy["maximum_manual_pilot_pct"]) / 100
    planned_budget = min(monthly_budget, pilot_cap)
    if planned_budget <= 0:
        blocked["reasons"] = ["个人预算与试运行仓位上限没有可用空间"]
        return blocked

    allowed_markets = _allowed_stock_markets(profile)
    existing = {
        (str(item.get("market") or ""), str(item.get("code") or "")): float(
            item.get("base_value") or 0
        )
        for item in valuation_payload.get("positions") or []
    }
    post_total = total_value + planned_budget
    single_cap = post_total * float(profile.get("max_single_ratio") or 0) / 100
    rows = []
    allocated = 0.0
    reasons = []
    for position in positions:
        market = str(position.get("market") or "")
        symbol = str(position.get("symbol") or "")
        target = planned_budget * float(position.get("weight_pct") or 0) / 100
        if market not in allowed_markets:
            reasons.append(f"{market} {symbol} 不在个人投资政策允许市场内")
            amount = 0.0
        else:
            room = max(0.0, single_cap - existing.get((market, symbol), 0.0))
            amount = min(target, room)
            if amount + 0.01 < target:
                reasons.append(f"{market} {symbol} 受个人单品仓位上限约束")
        allocated += amount
        rows.append(
            {
                "market": market,
                "symbol": symbol,
                "name": position.get("name") or symbol,
                "source_weight_pct": _round(position.get("weight_pct"), 2),
                "planned_amount_cny": round(amount, 2),
                "target_amount_before_policy_cny": round(target, 2),
                "existing_amount_cny": round(
                    existing.get((market, symbol), 0.0), 2
                ),
                "manual_review_required": True,
            }
        )
    return {
        "status": "available",
        "schema_version": "opportunity_limited_manual_pilot.v1",
        "basket_id": basket.get("id"),
        "valuation_snapshot_id": valuation_snapshot.get("id"),
        "profile_version_id": profile.get("profile_version_id"),
        "base_currency": "CNY",
        "portfolio_value_cny": round(total_value, 2),
        "monthly_budget_cny": round(monthly_budget, 2),
        "pilot_cap_pct": policy["maximum_manual_pilot_pct"],
        "pilot_cap_cny": round(pilot_cap, 2),
        "planned_budget_cny": round(planned_budget, 2),
        "allocated_amount_cny": round(allocated, 2),
        "unallocated_cash_cny": round(max(0.0, planned_budget - allocated), 2),
        "positions": rows,
        "reasons": list(dict.fromkeys(reasons)),
        "execution_authorized": False,
        "notice": (
            "金额只使用已确认月度预算和当前可信估值形成研究上限；"
            "不代表券商现金余额，不给出股数或订单。"
        ),
    }


def build_scorecard(
    strategy_id: str,
    *,
    user_id: str,
    now: dt.datetime | None = None,
    opp_repo: OpportunityRepository = opportunity_repository,
    profit_repo: OpportunityProfitRepository = profit_repository,
    strategy_record: dict[str, Any] | None = None,
    strategy_baskets: list[dict[str, Any]] | None = None,
    tested_strategy_count: int | None = None,
) -> dict[str, Any]:
    current = _now(now)
    strategy = strategy_record or opp_repo.get_strategy(
        strategy_id, user_id=user_id
    )
    if strategy is None:
        raise OpportunityNotFoundError("机会策略不存在")
    policy_record = get_policy(
        strategy_id,
        user_id=user_id,
        opp_repo=opp_repo,
        profit_repo=profit_repo,
        strategy=strategy,
    )
    policy = normalize_policy(policy_record["policy"])
    baskets = []
    source_baskets = (
        strategy_baskets
        if strategy_baskets is not None
        else opp_repo.list_paper_baskets(user_id=user_id, limit=200)
    )
    strategy_family_size = max(
        1,
        int(tested_strategy_count)
        if tested_strategy_count is not None
        else opp_repo.count_tested_strategy_versions(user_id=user_id),
    )
    for basket in source_baskets:
        snapshot_strategy = (basket.get("snapshot") or {}).get("strategy") or {}
        if (
            str(snapshot_strategy.get("id") or "") == strategy_id
            and str(snapshot_strategy.get("version_id") or "")
            == str(strategy["version_id"])
        ):
            baskets.append(basket)

    cohorts_by_horizon: dict[int, list[dict[str, Any]]] = {}
    summaries = []
    for horizon in policy["evaluation_horizons"]:
        cohorts = _independent_cohorts(
            [
                _cohort_at_horizon(basket, int(horizon), policy)
                for basket in baskets
            ],
            int(horizon),
        )
        cohorts_by_horizon[int(horizon)] = cohorts
        summaries.append(
            _horizon_summary(
                int(horizon),
                cohorts,
                strategy_family_size=strategy_family_size,
            )
        )
    primary = next(
        item
        for item in summaries
        if item["horizon_trading_days"] == policy["primary_horizon"]
    )
    gate = _capital_gate(primary, policy, basket_count=len(baskets))
    capital_plan = _capital_plan(
        user_id=user_id,
        gate=gate,
        policy=policy,
        baskets=baskets,
        current=current,
    )
    all_observations = [
        observation
        for basket in baskets
        for observation in basket.get("observations") or []
    ]
    default_cutoff = max(
        [
            str(
                (basket.get("snapshot") or {}).get("frozen_at")
                or basket.get("created_at")
                or ""
            )
            for basket in baskets
        ]
        + [
            str(
                strategy.get("version_created_at")
                or strategy.get("updated_at")
                or strategy.get("created_at")
                or _iso(current)
            )
        ]
    )
    cutoff = max(
        (
            str(observation.get("created_at") or observation.get("observed_at") or "")
            for observation in all_observations
        ),
        default=default_cutoff,
    )
    valid_v2_count = sum(
        1
        for basket in baskets
        for observation in _valid_observations(basket)
    )
    max_horizon = max(policy["evaluation_horizons"])
    completed_baskets = sum(
        1
        for basket in baskets
        if any(
            int((observation.get("payload") or {}).get("observed_trading_days_min") or 0)
            >= max_horizon
            and float(
                (observation.get("payload") or {}).get(
                    "covered_position_weight_pct"
                )
                or 0
            )
            >= float(policy["minimum_coverage_pct"])
            for observation in _valid_observations(basket)
        )
    )
    return {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(current),
        "evidence_cutoff_at": cutoff,
        "strategy": {
            "id": strategy_id,
            "name": (strategy.get("definition") or {}).get("name") or strategy_id,
            "version_id": strategy["version_id"],
            "version_no": strategy["current_version_no"],
            "definition_sha256": strategy.get("definition_sha256"),
        },
        "policy": {
            "id": policy_record.get("id"),
            "version_no": policy_record.get("version_no"),
            "persisted": policy_record.get("persisted"),
            "integrity_verified": policy_record.get("integrity_verified"),
            "values": policy,
        },
        "automation": {
            "status": "active",
            "observation_interval": (
                "每个纸面组合最多每日一次；相同行情截面幂等去重，"
                "补跑仍按冻结后的精确第 N 个交易日重建窗口"
            ),
            "basket_count": len(baskets),
            "completed_basket_count": completed_baskets,
            "collecting_basket_count": max(0, len(baskets) - completed_baskets),
            "valid_observation_count": valid_v2_count,
            "maximum_horizon_trading_days": max_horizon,
        },
        "horizons": summaries,
        "cohorts": {
            str(horizon): cohorts
            for horizon, cohorts in cohorts_by_horizon.items()
        },
        "capital_gate": gate,
        "capital_plan": capital_plan,
        "methodology": {
            "cohort": "每个冻结纸面组合先形成前瞻批次；每个窗口只计入按冻结时间预先确定、起点不重叠的代表批次，重叠运行保留审计但不增加样本量，策略新版本也不混入旧版本样本。",
            "return": "冻结权重在第 5/20/60 个真实交易日的本币复权收益，扣除用户收益验证政策中的统一往返成本压力情景；补跑不会把更晚日期冒充目标窗口。",
            "benchmark": "A股 510300、港股 02800、美股 SPY 按每只股票冻结权重和同日起点合成。",
            "drawdown": "窗口内逐标的计算峰谷回撤，再按冻结权重加总为保守成分回撤；它不会低估为只有补跑当天一个观测点。",
            "confidence": "平均超额使用双侧 95% t 区间；小样本区间较宽，不以点估计冒充确定收益。",
            "selection_bias": f"历史上已有 {strategy_family_size} 个策略版本冻结过纸面组合并构成同一研究族；归档或升级不会抹掉试验次数，资金门禁额外使用 Bonferroni 家族错误率校正，避免从大量策略中挑出随机赢家。",
            "capital": "只有主窗口全部门禁通过才允许非零人工试运行上限；金额继续受 IPS、月度预算、可信估值和单品上限约束。",
        },
        "limitations": [
            "候选池仍可能存在历史成分和幸存者偏差，前瞻批次只能减少、不能消除该问题。",
            "跨市场收益尚未纳入持有期汇率变化，基准是可交易 ETF 近似而非机构总收益指数。",
            "统一成本情景不等于用户真实券商费用；实际执行还会受到整手、停牌、涨跌停和冲击成本影响。",
            "策略通过只代表当前冻结样本的证据门禁，不保证下一批次或真实账户盈利。",
        ],
    }


def persist_scorecard(
    strategy_id: str,
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
    opp_repo: OpportunityRepository = opportunity_repository,
    profit_repo: OpportunityProfitRepository = profit_repository,
) -> tuple[dict[str, Any], bool]:
    policy = get_policy(
        strategy_id, user_id=user_id, opp_repo=opp_repo, profit_repo=profit_repo
    )
    if not policy.get("persisted"):
        policy = save_policy(
            strategy_id,
            policy["policy"],
            user_id=user_id,
            actor_id=actor_id,
            opp_repo=opp_repo,
            profit_repo=profit_repo,
        )
    scorecard = build_scorecard(
        strategy_id,
        user_id=user_id,
        now=now,
        opp_repo=opp_repo,
        profit_repo=profit_repo,
    )
    frozen_scorecard = {
        **scorecard,
        # Persisted scorecards are content-addressed. Wall-clock calculation
        # time must not create a duplicate when every bound input is unchanged.
        "generated_at": scorecard["evidence_cutoff_at"],
    }
    saved, created = profit_repo.save_scorecard(
        user_id=user_id,
        strategy_id=strategy_id,
        strategy_version_id=scorecard["strategy"]["version_id"],
        policy_id=str(policy["id"]),
        evidence_cutoff_at=scorecard["evidence_cutoff_at"],
        payload=frozen_scorecard,
    )
    return saved, created


def profit_lab_overview(
    *,
    user_id: str,
    opp_repo: OpportunityRepository = opportunity_repository,
    profit_repo: OpportunityProfitRepository = profit_repository,
) -> dict[str, Any]:
    strategies = opp_repo.list_strategies(user_id=user_id, limit=100)
    tested_strategy_count = opp_repo.count_tested_strategy_versions(
        user_id=user_id
    )
    all_baskets = opp_repo.list_paper_baskets(user_id=user_id, limit=200)
    baskets_by_strategy: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for basket in all_baskets:
        snapshot_strategy = (basket.get("snapshot") or {}).get("strategy") or {}
        key = (
            str(snapshot_strategy.get("id") or ""),
            str(snapshot_strategy.get("version_id") or ""),
        )
        baskets_by_strategy.setdefault(key, []).append(basket)

    scorecards = []
    for strategy in strategies:
        scorecard = build_scorecard(
            strategy["id"],
            user_id=user_id,
            opp_repo=opp_repo,
            profit_repo=profit_repo,
            strategy_record=strategy,
            strategy_baskets=baskets_by_strategy.get(
                (str(strategy["id"]), str(strategy["version_id"])),
                [],
            ),
            tested_strategy_count=tested_strategy_count,
        )
        persisted = profit_repo.latest_scorecard(strategy["id"], user_id=user_id)
        persisted_current = bool(
            persisted
            and persisted.get("integrity_verified")
            and str(persisted.get("strategy_version_id") or "")
            == str(scorecard["strategy"]["version_id"])
            and str(persisted.get("policy_id") or "")
            == str(scorecard["policy"].get("id") or "")
            and str(persisted.get("evidence_cutoff_at") or "")
            == str(scorecard.get("evidence_cutoff_at") or "")
        )
        scorecards.append(
            {
                **scorecard,
                "latest_persisted": (
                    {
                        key: persisted.get(key)
                        for key in (
                            "id",
                            "payload_sha256",
                            "evidence_cutoff_at",
                            "created_at",
                            "integrity_verified",
                            "strategy_version_id",
                            "policy_id",
                        )
                    }
                    | {"binding_current": persisted_current}
                    if persisted
                    else None
                ),
            }
        )
    status_counts: dict[str, int] = {}
    for scorecard in scorecards:
        status = str((scorecard.get("capital_gate") or {}).get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": "opportunity_profit_lab.v1",
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(),
        "items": scorecards,
        "summary": {
            "strategy_count": len(scorecards),
            "tested_strategy_version_count": tested_strategy_count,
            "status_counts": status_counts,
            "capital_eligible_count": sum(
                1
                for item in scorecards
                if (item.get("capital_gate") or {}).get("capital_eligible")
            ),
            "collecting_basket_count": sum(
                int((item.get("automation") or {}).get("collecting_basket_count") or 0)
                for item in scorecards
            ),
            "valid_observation_count": sum(
                int((item.get("automation") or {}).get("valid_observation_count") or 0)
                for item in scorecards
            ),
        },
        "boundary": "收益实验室只决定研究和人工试运行资格；不自动下单，不承诺收益。",
    }


def dispatch_due_observations(
    *,
    now: dt.datetime | None = None,
    limit: int = 50,
    minimum_interval_hours: float = 18.0,
    opp_repo: OpportunityRepository = opportunity_repository,
    jobs: BackgroundJobRepository | None = None,
    enqueue: Callable[[dict[str, Any], BackgroundJobRepository], str]
    | None = None,
) -> dict[str, Any]:
    current = _now(now)
    if not uses_celery_queue() and jobs is None:
        return {
            "status": "embedded",
            "eligible": 0,
            "created": 0,
            "deduplicated": 0,
            "skipped": 0,
        }
    job_repo = jobs or BackgroundJobRepository()
    dispatch = enqueue or enqueue_background_job
    created_count = 0
    deduplicated = 0
    skipped = 0
    eligible = 0
    errors = []
    job_ids = []
    for basket in opp_repo.list_paper_basket_scopes(limit=max(limit * 5, 100)):
        if created_count >= limit:
            break
        if not basket.get("snapshot_verified"):
            skipped += 1
            continue
        latest = basket.get("latest_observation") or {}
        payload = latest.get("payload") or {}
        if latest and not latest.get("payload_verified"):
            skipped += 1
            continue
        if payload.get("max_horizon_complete"):
            skipped += 1
            continue
        last_time = _parse_datetime(
            latest.get("created_at") or latest.get("observed_at")
        )
        if last_time and (
            current - last_time
        ).total_seconds() < minimum_interval_hours * 3600:
            skipped += 1
            continue
        eligible += 1
        user_id = str(basket.get("user_id") or "")
        basket_id = str(basket.get("id") or "")
        operation_payload = {
            "operation": "opportunity.observe",
            "input": {"basket_id": basket_id, "user_id": user_id},
        }
        try:
            job, created = job_repo.create_job(
                job_type="market_data_operation",
                queue_name=QUEUE_MARKET,
                payload=operation_payload,
                tenant_id="public",
                user_id=user_id,
                idempotency_key=(
                    f"opportunity-auto-observe:{basket_id}:{current.date().isoformat()}"
                ),
                max_attempts=2,
            )
            if created:
                dispatch(job, job_repo)
                created_count += 1
                job_ids.append(str(job["id"]))
            else:
                deduplicated += 1
        except Exception as error:
            errors.append(
                {
                    "basket_id": basket_id,
                    "error": str(error)[:240],
                }
            )
    return {
        "status": "partial" if errors else "succeeded",
        "eligible": eligible,
        "created": created_count,
        "deduplicated": deduplicated,
        "skipped": skipped,
        "job_ids": job_ids,
        "errors": errors,
    }
