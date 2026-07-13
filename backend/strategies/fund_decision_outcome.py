# -*- coding: utf-8 -*-
"""Evaluate a saved fund decision against later confirmed NAV observations."""

from __future__ import annotations

from typing import Any, Iterable

from .fund_outcome_common import (
    PEER_COMPARATOR_TYPE,
    comparison_points as _comparison_points,
    number as _number,
    parse_date as _date,
    peer_period as _peer_period,
    return_pct as _return_pct,
)


EVALUATOR_ID = "fund_decision_outcome"
EVALUATOR_VERSION = "1.1.0"
_MILESTONES = (5, 20, 60, 120)
ACTIONABLE_DECISION_ACTIONS = frozenset({
    "consider_tranche",
    "wait",
    "do_not_add",
    "hold_no_add",
    "reduce_exposure",
})


def _interpret(
    action: str,
    return_pct: float | None,
    sample_count: int,
    peer_comparison: dict[str, Any],
) -> dict[str, Any]:
    if action not in ACTIONABLE_DECISION_ACTIONS:
        return {
            "status": "not_scored",
            "label": "原决策不可执行或不包含方向",
            "reason": "补资料、仅研究或持有复核不构成可评分的新增/回避动作。",
        }
    if sample_count < 20 or return_pct is None:
        return {
            "status": "too_early",
            "label": "观察期不足",
            "reason": "至少需要 20 个后续确认净值样本，当前不提前宣布策略成功或失败。",
        }
    peer_available = peer_comparison.get("status") == "available"
    relative = _number(peer_comparison.get("relative_excess_return_pct"))
    comparable_return = (
        _number(peer_comparison.get("fund_return_pct"))
        if peer_available
        else return_pct
    )
    if action == "consider_tranche" and peer_available and relative is not None:
        if comparable_return is not None and comparable_return > 0 and relative > 0:
            status, label = "favorable_with_peer_edge", "上涨且暂时跑赢同类"
            reason = "标的绝对收益和相对同类收益均为正；这是单次观察，不证明策略长期有效。"
        elif comparable_return is not None and comparable_return > 0:
            status, label = "positive_but_lagging_peer", "上涨但暂时跑输同类"
            reason = "正收益主要不能解释为标的选择优势，同期同类平均表现更强。"
        elif relative > 0:
            status, label = "negative_but_peer_resilient", "下跌但暂时强于同类"
            reason = "绝对方向不利，但跌幅小于同类平均；不能把相对抗跌写成盈利。"
        else:
            status, label = "adverse_and_lagging_peer", "下跌且暂时跑输同类"
            reason = "绝对收益和相对同类收益均不利，需要进入策略复盘样本。"
    elif action == "consider_tranche":
        status = "favorable" if return_pct > 0 else "adverse"
        label = "方向暂时有利" if return_pct > 0 else "方向暂时不利"
        reason = "只完成绝对净值方向评价；同类基准不可用，因此不评价相对表现。"
    elif action in {"wait", "do_not_add", "hold_no_add"}:
        if peer_available and relative is not None and comparable_return is not None and comparable_return >= 0 and relative <= 0:
            status, label = "limited_opportunity_cost", "上涨但弱于同类"
            reason = "回避动作存在绝对机会成本，但标的同期没有跑赢同类；不推断用户真实收益。"
        elif peer_available and relative is not None and comparable_return is not None and comparable_return < 0 and relative < 0:
            status, label = "avoided_lagging_downside", "回避了下跌且弱于同类的标的"
            reason = "标的绝对下跌并跑输同类；只评价未新增暴露的后果，不推断用户执行。"
        else:
            status = "capital_preserved" if return_pct < 0 else "opportunity_cost"
            label = "回避动作暂时减少下行暴露" if return_pct < 0 else "回避动作产生机会成本"
            reason = "只描述未新增暴露的市场后果，不等于用户真实收益。"
    else:
        if peer_available and relative is not None and comparable_return is not None and comparable_return < 0 and relative < 0:
            status, label = "avoided_lagging_loss", "降低了下跌且弱于同类的暴露"
            reason = "标的绝对下跌并跑输同类；只评价被降低部分，不推断用户实际执行。"
        else:
            status = "avoided_loss" if return_pct < 0 else "opportunity_cost"
            label = "降低暴露后暂时避免损失" if return_pct < 0 else "降低暴露后产生机会成本"
            reason = "只评价被降低部分的市场方向，不推断用户是否实际执行。"
    return {"status": status, "label": label, "reason": reason}


def evaluate_fund_decision_outcome(
    *,
    code: str,
    baseline_as_of: str,
    baseline_nav: float,
    action: str,
    points: Iterable[dict[str, Any]],
    peer_series: dict[str, Any] | None = None,
    peer_unavailable_reason: str | None = None,
) -> dict[str, Any]:
    baseline_date = _date(baseline_as_of)
    baseline_value = _number(baseline_nav)
    if baseline_date is None or baseline_value is None or baseline_value <= 0:
        raise ValueError("原决策缺少有效的确认净值基线")

    observations = []
    for item in points:
        observed_date = _date(item.get("date"))
        value = _number(item.get("unit_nav"))
        if observed_date is None or observed_date <= baseline_date or value is None or value <= 0:
            continue
        observations.append({"date": observed_date.isoformat(), "unit_nav": value})
    observations.sort(key=lambda item: item["date"])
    deduplicated = {item["date"]: item for item in observations}
    observations = [deduplicated[key] for key in sorted(deduplicated)]

    latest = observations[-1] if observations else None
    latest_return = _return_pct(baseline_value, latest["unit_nav"]) if latest else None
    peer_lookup = _comparison_points(peer_series, "points")
    fund_lookup = _comparison_points(peer_series, "fund_points")
    peer_name = str((peer_series or {}).get("name") or "同类平均")
    peer_source = (peer_series or {}).get("source")
    peer_source_url = (peer_series or {}).get("source_url")
    peer_comparison = _peer_period(
        peer_lookup=peer_lookup,
        fund_lookup=fund_lookup,
        name=peer_name,
        source=str(peer_source or "") or None,
        source_url=str(peer_source_url or "") or None,
        baseline_as_of=baseline_date.isoformat(),
        observed_as_of=latest.get("date") if latest else None,
        fund_return_pct=latest_return,
        unavailable_reason=peer_unavailable_reason,
    )
    milestones = []
    for count in _MILESTONES:
        if len(observations) >= count:
            item = observations[count - 1]
            milestone_return = _return_pct(baseline_value, item["unit_nav"])
            milestone_peer = _peer_period(
                peer_lookup=peer_lookup,
                fund_lookup=fund_lookup,
                name=peer_name,
                source=str(peer_source or "") or None,
                source_url=str(peer_source_url or "") or None,
                baseline_as_of=baseline_date.isoformat(),
                observed_as_of=item["date"],
                fund_return_pct=milestone_return,
                unavailable_reason=peer_unavailable_reason,
            )
            milestones.append({
                "confirmed_nav_count": count,
                "status": "observed",
                "as_of": item["date"],
                "unit_nav": item["unit_nav"],
                "return_pct": milestone_return,
                "peer_return_pct": milestone_peer.get("period_return_pct"),
                "relative_excess_return_pct": milestone_peer.get("relative_excess_return_pct"),
                "peer_status": milestone_peer.get("status"),
            })
        else:
            milestones.append({
                "confirmed_nav_count": count,
                "status": "pending",
                "as_of": None,
                "unit_nav": None,
                "return_pct": None,
            })

    if not observations:
        evaluation_status = "pending"
    elif len(observations) < 20:
        evaluation_status = "observing"
    else:
        evaluation_status = "evaluable"
    interpretation = _interpret(
        str(action or ""), latest_return, len(observations), peer_comparison
    )
    peer_available = peer_comparison.get("status") in {"available", "pending"}
    limitations = [
        "fees_tax_fx_and_user_execution_are_not_included",
        "historical_outcome_does_not_prove_future_strategy_edge",
        "provider_peer_average_is_not_contractual_benchmark",
        "single_outcome_cannot_establish_strategy_edge",
    ]
    if not peer_available:
        limitations.append("absolute_nav_return_is_not_excess_return")

    return {
        "evaluator_id": EVALUATOR_ID,
        "evaluator_version": EVALUATOR_VERSION,
        "status": evaluation_status,
        "code": str(code),
        "decision": {
            "action": str(action or ""),
            "actionable": str(action or "") in ACTIONABLE_DECISION_ACTIONS,
        },
        "baseline": {
            "as_of": baseline_date.isoformat(),
            "unit_nav": round(baseline_value, 6),
            "source": "saved_agent_run",
        },
        "observed": {
            "as_of": latest.get("date") if latest else None,
            "unit_nav": latest.get("unit_nav") if latest else None,
            "confirmed_nav_count": len(observations),
            "calendar_days": ((_date(latest["date"]) - baseline_date).days if latest else 0),
            "return_pct": latest_return,
        },
        "peer_comparison": peer_comparison,
        "milestones": milestones,
        "interpretation": interpretation,
        "method": {
            "baseline": "immutable_confirmed_nav_saved_in_source_run",
            "observations": "confirmed_nav_dates_strictly_after_baseline",
            "minimum_directional_sample": 20,
            "costs": "not_included",
            "benchmark": PEER_COMPARATOR_TYPE,
            "benchmark_alignment": "exact_provider_date_only",
            "benchmark_return_formula": "(1 + observed_cumulative_return) / (1 + baseline_cumulative_return) - 1",
            "relative_return_formula": "(1 + provider_fund_period_return) / (1 + peer_period_return) - 1",
            "unit_nav_return": "reported_separately_and_not_mixed_with_comparable_return",
            "execution": "user_execution_not_inferred",
        },
        "limitations": limitations,
        "policy": "结果评估只使用原 Run 保存的确认净值基线、之后的真实确认净值和来源原生同类平均序列；同类日期必须精确对齐，缺失时不选择其他指数兜底。结果不回写原结论，也不把单次样本或用户未执行的动作包装成盈利证明。",
    }
