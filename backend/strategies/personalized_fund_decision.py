# -*- coding: utf-8 -*-
"""Deterministic portfolio-aware decision policy for a researched public fund."""

from __future__ import annotations

import math
from typing import Any


STRATEGY_ID = "personalized_fund_decision"
STRATEGY_VERSION = "1.2.0"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _money(value: float | None) -> float | None:
    return round(value, 2) if value is not None and value >= 0 else None


def _fund_risk_level(analysis: dict[str, Any]) -> str:
    role = ((analysis.get("playbook") or {}).get("role") or {})
    label = str(role.get("risk_band") or "")
    volatility = _number((analysis.get("metrics") or {}).get("annual_volatility"))
    if "进攻" in label or (volatility is not None and volatility >= 25):
        return "aggressive"
    if "稳健" in label or (volatility is not None and volatility <= 10):
        return "stable"
    return "balanced"


def _maximum_additional(total: float, current: float, ratio: float) -> float:
    limit = max(0.01, min(0.95, ratio / 100))
    return max(0, (limit * total - current) / (1 - limit))


def _required_reduction(total: float, current: float, ratio: float) -> float:
    limit = max(0.01, min(0.95, ratio / 100))
    return max(0, (current - limit * total) / (1 - limit))


def _maximum_additional_by_exposure(
    total: float,
    current_upper_amount: float,
    target_upper_ratio: float,
    limit_ratio: float,
) -> float | None:
    """Return a conservative capacity, or None when the target cannot increase risk."""
    target = max(0.0, min(100.0, target_upper_ratio)) / 100
    limit = max(0.0, min(100.0, limit_ratio)) / 100
    if target <= limit:
        return None
    numerator = limit * total - current_upper_amount
    return max(0.0, numerator / (target - limit))


def _projected_ratio(total: float, current_amount: float, added: float, target_ratio: float) -> float:
    denominator = total + added
    if denominator <= 0:
        return 0.0
    return (current_amount + added * target_ratio / 100) / denominator * 100


def evaluate_personalized_fund_decision(
    analysis: dict[str, Any],
    context: dict[str, Any],
    market_profile: dict[str, Any] | None = None,
    exposure: dict[str, Any] | None = None,
    *,
    planned_amount: float | None = None,
) -> dict[str, Any]:
    profile = context.get("profile") or {}
    portfolio = context.get("portfolio") or {}
    target = context.get("target_holding") or {}
    strategy = analysis.get("conditioned_forward") or {}
    market_profile = market_profile or {}
    exposure = exposure or {}
    market = market_profile.get("market") or {}
    metrics = analysis.get("metrics") or {}
    timing = analysis.get("timing") or {}

    configured = bool(profile.get("configured"))
    amount_complete = bool(portfolio.get("amount_complete"))
    total = _number(portfolio.get("total_amount"))
    current = _number(target.get("amount")) or 0
    current_ratio = _number(target.get("ratio"))
    max_ratio = _number(profile.get("max_single_ratio")) if configured else None
    max_drawdown_tolerance = _number(profile.get("max_drawdown_pct")) if configured else None
    budget = _number(planned_amount)
    budget_source = "planned_amount" if budget is not None and budget > 0 else "monthly_budget"
    if (budget is None or budget <= 0) and configured:
        budget = _number(profile.get("monthly_budget"))

    fund_risk = _fund_risk_level(analysis)
    user_risk = str(profile.get("risk") or "") if configured else ""
    horizon = str(profile.get("horizon") or "") if configured else ""
    allowed_markets = set(profile.get("allowed_fund_markets") or []) if configured else set()
    accept_fx_risk = bool(profile.get("accept_fx_risk")) if configured else False
    strategy_decision = str(strategy.get("decision") or "data_required")
    confidence = str((strategy.get("confidence") or {}).get("level") or "unavailable")
    timing_score = _number(timing.get("score"))

    gates = []
    missing = []
    gates.append({
        "code": "profile_configured",
        "status": "pass" if configured else "block",
        "label": "投资约束",
        "detail": "已保存风险偏好、期限和单品上限" if configured else "尚未保存你的风险偏好、期限和单品上限",
    })
    if not configured:
        missing.append("investment_profile")
    holdings_ready = amount_complete and total is not None and total > 0
    gates.append({
        "code": "portfolio_amounts",
        "status": "pass" if holdings_ready else "block",
        "label": "组合金额",
        "detail": "持仓金额可用于计算真实仓位" if holdings_ready else "持仓为空或金额不完整，无法计算真实仓位",
    })
    if not holdings_ready:
        missing.append("confirmed_holding_amounts")

    market_resolution = str(market_profile.get("resolution_status") or "insufficient")
    market_primary = str(market.get("primary") or "unknown_cross_border")
    required_markets = set(market.get("required_permissions") or [])
    market_unresolved = market_resolution != "identified" or not required_markets
    missing_market_permissions = sorted(required_markets - allowed_markets) if configured else []
    market_permission_block = configured and bool(missing_market_permissions)
    currency_risk = bool(market.get("currency_risk"))
    fx_block = configured and currency_risk and not accept_fx_risk
    gates.append({
        "code": "fund_market_identified",
        "status": "block" if market_unresolved else "pass",
        "label": "投资市场识别",
        "detail": (
            "真实基金元数据尚不能确认底层市场"
            if market_unresolved else
            f"已识别为 {market.get('label') or market_primary}"
        ),
    })
    if market_unresolved:
        missing.append("fund_market_identification")
    gates.append({
        "code": "fund_market_permission",
        "status": (
            "pending" if not configured or market_unresolved
            else "block" if market_permission_block
            else "pass"
        ),
        "label": "市场投资权限",
        "detail": (
            "配置投资档案后确认是否允许该基金市场"
            if not configured else
            f"未允许市场：{', '.join(missing_market_permissions)}"
            if market_permission_block else
            f"允许市场：{', '.join(sorted(allowed_markets)) or '-'}"
        ),
    })
    gates.append({
        "code": "foreign_exchange_risk",
        "status": (
            "pending" if not configured
            else "block" if fx_block
            else "pass"
        ),
        "label": "汇率风险",
        "detail": (
            "待在投资档案中明确确认是否接受汇率风险"
            if not configured and currency_risk else
            "配置投资档案后确认汇率风险"
            if not configured else
            "该基金存在跨境汇率风险，但用户尚未确认接受"
            if fx_block else
            "已确认接受汇率风险" if currency_risk and accept_fx_risk else
            "未识别到必须单独确认的汇率风险"
        ),
    })

    risk_block = (
        configured
        and ((user_risk == "stable" and fund_risk == "aggressive")
             or (user_risk == "balanced" and fund_risk == "aggressive" and (timing_score or 0) < 55))
    )
    gates.append({
        "code": "risk_suitability",
        "status": "block" if risk_block else "pass" if configured else "pending",
        "label": "风险适配",
        "detail": f"用户风险 {user_risk or '-'}，基金风险 {fund_risk}",
    })
    horizon_block = configured and horizon in {"short", "short_term"} and fund_risk != "stable"
    gates.append({
        "code": "horizon_suitability",
        "status": "block" if horizon_block else "pass" if configured else "pending",
        "label": "期限适配",
        "detail": "短期资金不用于承担中高波动基金" if horizon_block else f"投资期限 {horizon or '-'}",
    })
    historical_max_drawdown = _number(metrics.get("max_drawdown"))
    drawdown_block = bool(
        configured
        and max_drawdown_tolerance is not None
        and historical_max_drawdown is not None
        and abs(historical_max_drawdown) > max_drawdown_tolerance
    )
    gates.append({
        "code": "drawdown_capacity",
        "status": "block" if drawdown_block else "pass" if configured else "pending",
        "label": "回撤承受能力",
        "detail": (
            f"基金历史最大回撤 {historical_max_drawdown:.2f}%，超过 IPS 上限 {max_drawdown_tolerance:.2f}%"
            if drawdown_block else
            f"基金历史最大回撤 {historical_max_drawdown:.2f}%，IPS 上限 {max_drawdown_tolerance:.2f}%"
            if configured and historical_max_drawdown is not None and max_drawdown_tolerance is not None else
            "缺少已激活 IPS 或基金历史最大回撤"
        ),
    })

    over_limit = (
        configured
        and max_ratio is not None
        and current_ratio is not None
        and current_ratio > max_ratio
    )
    single_capacity = (
        _maximum_additional(total, current, max_ratio)
        if holdings_ready and configured and max_ratio is not None
        else None
    )
    gates.append({
        "code": "single_position_limit",
        "status": "block" if over_limit else "pass" if single_capacity is not None else "pending",
        "label": "单品上限",
        "detail": (
            f"当前 {current_ratio:.2f}%，超过上限 {max_ratio:.2f}%"
            if over_limit
            else f"当前 {current_ratio or 0:.2f}%，上限 {max_ratio:.2f}%"
            if single_capacity is not None
            else "尚无用户确认的单品仓位上限"
        ),
    })

    exposure_quality = exposure.get("quality") or {}
    exposure_summary = exposure.get("summary") or {}
    exposure_target = exposure.get("target") or {}
    exposure_snapshot = exposure.get("snapshot") or {}
    exposure_integrity = exposure.get("integrity") or {}
    context_holdings_hash = str(portfolio.get("holdings_sha256") or "")
    expected_profile_version = profile.get("profile_version_id") if configured else None
    binding_valid = bool(
        exposure_snapshot.get("id")
        and exposure_integrity.get("verified")
        and exposure.get("holdings_sha256") == context_holdings_hash
        and exposure.get("profile_version_id") == expected_profile_version
        and str(exposure.get("target_code") or "") == str(analysis.get("code") or "")
    )
    exposure_eligible = bool(exposure_quality.get("decision_eligible") and binding_valid)
    gates.append({
        "code": "portfolio_exposure_snapshot",
        "status": "pass" if exposure_eligible else "block",
        "label": "组合穿透证据",
        "detail": (
            f"已验证快照 {exposure_snapshot.get('id')}，持仓与 IPS 版本绑定一致"
            if exposure_eligible else
            "缺少完整、新鲜且哈希验证通过的组合穿透快照"
        ),
    })
    if not exposure_eligible:
        missing.append("portfolio_exposure_snapshot")

    max_equity_ratio = _number(profile.get("max_equity_ratio")) if configured else None
    equity = exposure_summary.get("equity") or {}
    current_equity_lower = _number(equity.get("lower_ratio"))
    current_equity_upper = _number(equity.get("upper_ratio"))
    current_equity_upper_amount = _number(equity.get("upper_amount"))
    target_equity = exposure_target.get("equity_interval") or {}
    target_equity_lower = _number(target_equity.get("lower_ratio"))
    target_equity_upper = _number(target_equity.get("upper_ratio"))
    equity_definite_breach = bool(
        exposure_eligible
        and max_equity_ratio is not None
        and current_equity_lower is not None
        and current_equity_lower > max_equity_ratio
    )
    equity_uncertain = bool(
        exposure_eligible
        and max_equity_ratio is not None
        and current_equity_lower is not None
        and current_equity_upper is not None
        and current_equity_lower <= max_equity_ratio < current_equity_upper
    )
    equity_capacity = None
    if (
        exposure_eligible
        and total is not None
        and current_equity_upper_amount is not None
        and target_equity_upper is not None
        and max_equity_ratio is not None
        and not equity_definite_breach
        and not equity_uncertain
    ):
        equity_capacity = _maximum_additional_by_exposure(
            total,
            current_equity_upper_amount,
            target_equity_upper,
            max_equity_ratio,
        )
    gates.append({
        "code": "equity_exposure_limit",
        "status": (
            "block" if equity_definite_breach else
            "warn" if equity_uncertain else
            "pass" if exposure_eligible and max_equity_ratio is not None else
            "pending"
        ),
        "label": "权益总仓位上限",
        "detail": (
            f"组合权益可证明区间 {current_equity_lower:.2f}% - {current_equity_upper:.2f}%，IPS 上限 {max_equity_ratio:.2f}%"
            if current_equity_lower is not None and current_equity_upper is not None and max_equity_ratio is not None else
            "缺少可验证的权益暴露区间或 IPS 上限"
        ),
    })

    max_industry_ratio = _number(profile.get("max_industry_ratio")) if configured else None
    industry_summary = exposure_summary.get("industry") or {}
    current_industry_max_lower = _number(industry_summary.get("max_lower_ratio"))
    current_industry_max_upper = _number(industry_summary.get("max_upper_ratio"))
    current_industry_unknown = _number(industry_summary.get("unknown_equity_amount"))
    industry_definite_breach = bool(
        exposure_eligible
        and max_industry_ratio is not None
        and current_industry_max_lower is not None
        and current_industry_max_lower > max_industry_ratio
    )
    industry_uncertain = bool(
        exposure_eligible
        and max_industry_ratio is not None
        and current_industry_max_lower is not None
        and current_industry_max_upper is not None
        and current_industry_max_lower <= max_industry_ratio < current_industry_max_upper
    )
    industry_capacity = None
    if (
        exposure_eligible
        and total is not None
        and current_industry_unknown is not None
        and max_industry_ratio is not None
        and not industry_definite_breach
        and not industry_uncertain
    ):
        current_by_name = {
            str(row.get("name")): _number(row.get("lower_amount")) or 0.0
            for row in exposure.get("industries") or []
            if row.get("name")
        }
        target_by_name = {
            str(row.get("name")): _number(row.get("lower_ratio")) or 0.0
            for row in exposure_target.get("industries") or []
            if row.get("name")
        }
        target_unknown = _number(exposure_target.get("industry_unknown_ratio")) or 0.0
        capacities = []
        for industry_name in set(current_by_name) | set(target_by_name) | {"__unclassified__"}:
            current_upper_amount = current_industry_unknown + current_by_name.get(industry_name, 0.0)
            target_upper_ratio_for_industry = min(
                100.0,
                target_unknown + target_by_name.get(industry_name, 0.0),
            )
            candidate = _maximum_additional_by_exposure(
                total,
                current_upper_amount,
                target_upper_ratio_for_industry,
                max_industry_ratio,
            )
            if candidate is not None:
                capacities.append(candidate)
        if capacities:
            industry_capacity = min(capacities)
    gates.append({
        "code": "industry_exposure_limit",
        "status": (
            "block" if industry_definite_breach else
            "warn" if industry_uncertain else
            "pass" if exposure_eligible and max_industry_ratio is not None else
            "pending"
        ),
        "label": "单行业仓位上限",
        "detail": (
            f"组合行业集中度可证明区间 {current_industry_max_lower:.2f}% - {current_industry_max_upper:.2f}%，IPS 上限 {max_industry_ratio:.2f}%"
            if current_industry_max_lower is not None and current_industry_max_upper is not None and max_industry_ratio is not None else
            "缺少可验证的行业暴露区间或 IPS 上限"
        ),
    })

    edge_positive = strategy_decision == "research"
    edge_negative = strategy_decision == "avoid_for_now"
    gates.append({
        "code": "historical_edge",
        "status": "pass" if edge_positive else "block" if edge_negative else "warn",
        "label": "历史条件优势",
        "detail": f"{strategy.get('strategy_id') or '历史策略'} 判断 {strategy_decision}，置信度 {confidence}",
    })

    capacities = [value for value in (single_capacity, equity_capacity, industry_capacity) if value is not None]
    aggregate_capacity = min(capacities) if capacities else single_capacity
    candidate_amount = (
        min(budget, aggregate_capacity)
        if budget is not None and aggregate_capacity is not None
        else None
    )
    candidate_amount = max(0, candidate_amount) if candidate_amount is not None else None
    action = "hold_review"
    label = "持有并复核"
    rationale = "当前证据不足以支持新增投入，保持观察并等待下一次确认净值。"
    reduction = None
    setup_missing = [
        item for item in missing
        if item not in {"portfolio_exposure_snapshot"}
    ]
    if "fund_market_identification" in missing:
        action = "market_data_required"
        label = "等待确认基金投资市场"
        rationale = "真实元数据只能确认该基金属于跨境产品，但无法确认主要投资市场，系统拒绝生成金额。"
    elif setup_missing:
        action = "setup_required"
        label = "先补齐个人决策资料"
        rationale = "缺少真实投资约束或完整持仓金额，系统拒绝生成个性化金额。"
    elif "portfolio_exposure_snapshot" in missing:
        action = "exposure_data_required"
        label = "等待组合穿透证据"
        rationale = "真实披露缺失、过期、冲突或快照绑定校验失败，系统拒绝生成新增金额。"
    elif over_limit:
        action = "reduce_exposure"
        label = "降低集中度"
        reduction = _required_reduction(total, current, max_ratio) if total is not None else None
        rationale = "目标基金已超过你设置的单品仓位上限，不应继续加仓。"
    elif equity_definite_breach or industry_definite_breach:
        action = "reduce_exposure"
        label = "降低组合集中暴露"
        rationale = "真实披露下界已经证明组合权益或单行业仓位超过 IPS 上限，不应继续新增风险。"
    elif equity_uncertain or industry_uncertain:
        action = "exposure_data_required"
        label = "等待更完整披露"
        rationale = "已披露下界未超限，但未披露仓位的最坏上界可能超限，系统不会用乐观假设放行金额。"
    elif market_permission_block or fx_block:
        action = "do_not_add"
        label = "不新增投入"
        rationale = "基金投资市场不在你允许的范围内，或跨境汇率风险尚未得到你的明确确认。"
    elif risk_block or horizon_block or drawdown_block:
        action = "do_not_add"
        label = "不新增投入"
        rationale = "基金风险或建议持有期限与你保存的投资约束冲突。"
    elif edge_negative:
        action = "wait"
        label = "等待条件改善"
        rationale = "当前历史相似条件偏弱，不因短期回撤或亏损自动补仓。"
    elif strategy_decision in {"data_required", "hold_review"}:
        action = "hold_review" if target.get("exists") else "research_only"
        label = "持有复核" if target.get("exists") else "仅保留研究候选"
        rationale = "历史条件方向不一致或样本不足，暂不形成新增投入依据。"
    elif budget is None or budget <= 0:
        action = "budget_required"
        label = "先填写计划投入金额"
        rationale = "风险和历史条件允许继续研究，但没有预算，系统不猜测投入金额。"
        missing.append("planned_or_monthly_budget")
    elif candidate_amount is not None and candidate_amount <= 0:
        action = "hold_no_add"
        label = "持有但不加仓"
        rationale = "单品仓位上限已没有新增空间。"
    elif edge_positive:
        action = "consider_tranche"
        label = "可考虑小额分批"
        rationale = "历史条件偏正面且未触发个人风险门禁，但历史统计不等于未来收益。"

    allowed_amount = candidate_amount if action == "consider_tranche" else None
    tranche_count = 5 if confidence != "medium" else 4
    first_tranche = (
        allowed_amount / tranche_count
        if action == "consider_tranche" and allowed_amount is not None
        else None
    )
    projected_ratio = (
        (current + allowed_amount) / (total + allowed_amount) * 100
        if allowed_amount is not None and total is not None and total + allowed_amount > 0
        else None
    )
    primary = next(
        (
            item for item in (strategy.get("horizons") or [])
            if item.get("horizon") == strategy.get("primary_horizon")
        ),
        {},
    )
    analog = primary.get("analog") or {}

    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "status": (
            "abstained"
            if missing or action in {"market_data_required", "exposure_data_required", "setup_required"}
            else "evaluated"
        ),
        "decision": {
            "action": action,
            "label": label,
            "rationale": rationale,
        },
        "portfolio": {
            "total_amount": _money(total),
            "target_exists": bool(target.get("exists")),
            "target_amount": _money(current),
            "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
            "max_single_ratio": round(max_ratio, 2) if max_ratio is not None else None,
            "projected_ratio_after_full_amount": round(projected_ratio, 2) if projected_ratio is not None else None,
        },
        "budget": {
            "source": budget_source if budget is not None else None,
            "requested_or_monthly_amount": _money(budget),
            "maximum_additional_by_limit": _money(aggregate_capacity),
            "maximum_additional_by_single_limit": _money(single_capacity),
            "maximum_additional_by_equity_limit": _money(equity_capacity),
            "maximum_additional_by_industry_limit": _money(industry_capacity),
            "allowed_full_amount": _money(allowed_amount),
            "tranche_count": tranche_count if first_tranche is not None else None,
            "first_tranche_amount": _money(first_tranche),
            "suggested_reduction_amount": _money(reduction),
        },
        "suitability": {
            "user_risk": user_risk or None,
            "user_horizon": horizon or None,
            "fund_risk": fund_risk,
            "experience_level": profile.get("experience_level") if configured else None,
            "primary_objective": profile.get("primary_objective") if configured else None,
            "max_drawdown_pct": max_drawdown_tolerance,
            "profile_version_id": profile.get("profile_version_id") if configured else None,
            "profile_payload_sha256": profile.get("profile_payload_sha256") if configured else None,
        },
        "portfolio_exposure": {
            "snapshot_id": exposure_snapshot.get("id"),
            "snapshot_payload_sha256": exposure_snapshot.get("payload_sha256"),
            "integrity_verified": bool(exposure_integrity.get("verified")),
            "holdings_sha256": exposure.get("holdings_sha256"),
            "status": exposure.get("status"),
            "decision_eligible": exposure_eligible,
            "equity": {
                "current_lower_ratio": current_equity_lower,
                "current_upper_ratio": current_equity_upper,
                "target_lower_ratio": target_equity_lower,
                "target_upper_ratio": target_equity_upper,
                "limit_ratio": max_equity_ratio,
            },
            "industry": {
                "current_max_lower_ratio": current_industry_max_lower,
                "current_max_upper_ratio": current_industry_max_upper,
                "limit_ratio": max_industry_ratio,
                "unknown_equity_amount": current_industry_unknown,
            },
            "quality_reasons": exposure_quality.get("reasons") or [],
        },
        "market_context": {
            "resolution_status": market_resolution,
            "primary": market_primary,
            "label": market.get("label"),
            "is_qdii": bool((market_profile.get("fund") or {}).get("is_qdii")),
            "cross_border": bool(market.get("cross_border")),
            "currency_risk": currency_risk,
            "required_permissions": sorted(required_markets),
            "allowed_markets": sorted(allowed_markets),
            "accept_fx_risk": accept_fx_risk,
            "benchmark_names": market_profile.get("benchmark_names") or [],
            "confirmed_nav_lag": (market_profile.get("valuation") or {}).get("confirmed_nav_lag"),
            "estimate_policy": (market_profile.get("valuation") or {}).get("intraday_estimate_policy"),
        },
        "historical_context": {
            "decision": strategy_decision,
            "confidence": confidence,
            "primary_horizon": strategy.get("primary_horizon"),
            "positive_rate": _number(analog.get("positive_rate")),
            "median_return": _number(analog.get("median_return")),
            "worst_return": _number(analog.get("worst_return")),
        },
        "gates": gates,
        "missing_requirements": missing,
        "monitoring": {
            "invalidation_conditions": strategy.get("invalidation_conditions") or [],
            "next_review_as_of": (strategy.get("condition") or {}).get("as_of"),
        },
        "method": {
            "position_limit": "post_investment_target_ratio_must_not_exceed_user_maximum",
            "amount": "min(planned_or_monthly_budget, maximum_additional_by_position_limit)",
            "tranche": "four_tranches_for_medium_confidence_otherwise_five",
            "loss_handling": "never_average_down_only_because_current_profit_is_negative",
            "cross_market": "market_permission_and_fx_acknowledgement_are_required_before_amount",
            "drawdown_capacity": "historical_fund_max_drawdown_must_not_exceed_user_confirmed_ips_limit",
            "portfolio_exposure": "new_money_is_allowed_only_when_an_immutable_fresh_snapshot_proves_equity_and_industry_limits",
        },
        "policy": "这是基于已确认持仓、真实基金市场画像、不可变组合穿透快照、用户约束和历史统计的决策检查，不保证收益，不自动下单；未披露仓位按最坏上界处理，跨境市场或组合约束无法验证时不得给新增金额。当前持仓存储仍是单用户迁移账本，多用户开放前必须完成登录、授权与数据隔离。",
    }
