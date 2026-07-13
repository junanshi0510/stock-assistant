# -*- coding: utf-8 -*-
"""Deterministic portfolio-aware decision policy for a researched public fund."""

from __future__ import annotations

import math
from typing import Any


STRATEGY_ID = "personalized_fund_decision"
STRATEGY_VERSION = "1.0.0"


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


def evaluate_personalized_fund_decision(
    analysis: dict[str, Any],
    context: dict[str, Any],
    *,
    planned_amount: float | None = None,
) -> dict[str, Any]:
    profile = context.get("profile") or {}
    portfolio = context.get("portfolio") or {}
    target = context.get("target_holding") or {}
    strategy = analysis.get("conditioned_forward") or {}
    metrics = analysis.get("metrics") or {}
    timing = analysis.get("timing") or {}

    configured = bool(profile.get("configured"))
    amount_complete = bool(portfolio.get("amount_complete"))
    total = _number(portfolio.get("total_amount"))
    current = _number(target.get("amount")) or 0
    current_ratio = _number(target.get("ratio"))
    max_ratio = _number(profile.get("max_single_ratio")) if configured else None
    budget = _number(planned_amount)
    budget_source = "planned_amount" if budget is not None and budget > 0 else "monthly_budget"
    if (budget is None or budget <= 0) and configured:
        budget = _number(profile.get("monthly_budget"))

    fund_risk = _fund_risk_level(analysis)
    user_risk = str(profile.get("risk") or "") if configured else ""
    horizon = str(profile.get("horizon") or "") if configured else ""
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

    over_limit = (
        configured
        and max_ratio is not None
        and current_ratio is not None
        and current_ratio > max_ratio
    )
    capacity = (
        _maximum_additional(total, current, max_ratio)
        if holdings_ready and configured and max_ratio is not None
        else None
    )
    gates.append({
        "code": "single_position_limit",
        "status": "block" if over_limit else "pass" if capacity is not None else "pending",
        "label": "单品上限",
        "detail": (
            f"当前 {current_ratio:.2f}%，超过上限 {max_ratio:.2f}%"
            if over_limit
            else f"当前 {current_ratio or 0:.2f}%，上限 {max_ratio:.2f}%"
            if capacity is not None
            else "尚无用户确认的单品仓位上限"
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

    candidate_amount = min(budget, capacity) if budget is not None and capacity is not None else None
    candidate_amount = max(0, candidate_amount) if candidate_amount is not None else None
    action = "hold_review"
    label = "持有并复核"
    rationale = "当前证据不足以支持新增投入，保持观察并等待下一次确认净值。"
    reduction = None
    if missing:
        action = "setup_required"
        label = "先补齐个人决策资料"
        rationale = "缺少真实投资约束或完整持仓金额，系统拒绝生成个性化金额。"
    elif over_limit:
        action = "reduce_exposure"
        label = "降低集中度"
        reduction = _required_reduction(total, current, max_ratio) if total is not None else None
        rationale = "目标基金已超过你设置的单品仓位上限，不应继续加仓。"
    elif risk_block or horizon_block:
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
        "status": "abstained" if missing else "evaluated",
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
            "maximum_additional_by_limit": _money(capacity),
            "allowed_full_amount": _money(allowed_amount),
            "tranche_count": tranche_count if first_tranche is not None else None,
            "first_tranche_amount": _money(first_tranche),
            "suggested_reduction_amount": _money(reduction),
        },
        "suitability": {
            "user_risk": user_risk or None,
            "user_horizon": horizon or None,
            "fund_risk": fund_risk,
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
        },
        "policy": "这是基于已确认持仓、用户约束和历史统计的决策检查，不保证收益，不自动下单；缺少关键个人数据时必须拒绝给金额。当前持仓存储仍是单用户迁移账本，多用户开放前必须完成登录、授权与数据隔离。",
    }
