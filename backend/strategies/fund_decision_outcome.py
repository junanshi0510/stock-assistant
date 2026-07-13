# -*- coding: utf-8 -*-
"""Evaluate a saved fund decision against later confirmed NAV observations."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable


EVALUATOR_ID = "fund_decision_outcome"
EVALUATOR_VERSION = "1.0.0"
_MILESTONES = (5, 20, 60, 120)
_ACTIONABLE = {
    "consider_tranche",
    "wait",
    "do_not_add",
    "hold_no_add",
    "reduce_exposure",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _return_pct(baseline: float, value: float) -> float:
    return round((value / baseline - 1) * 100, 4)


def _interpret(action: str, return_pct: float | None, sample_count: int) -> dict[str, Any]:
    if action not in _ACTIONABLE:
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
    if action == "consider_tranche":
        status = "favorable" if return_pct > 0 else "adverse"
        label = "方向暂时有利" if return_pct > 0 else "方向暂时不利"
        reason = "仅评价新增暴露后的绝对净值方向，尚未扣除费用或比较适配基准。"
    elif action in {"wait", "do_not_add", "hold_no_add"}:
        status = "capital_preserved" if return_pct < 0 else "opportunity_cost"
        label = "回避动作暂时减少下行暴露" if return_pct < 0 else "回避动作产生机会成本"
        reason = "只描述未新增暴露的市场后果，不等于用户真实收益。"
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
    milestones = []
    for count in _MILESTONES:
        if len(observations) >= count:
            item = observations[count - 1]
            milestones.append({
                "confirmed_nav_count": count,
                "status": "observed",
                "as_of": item["date"],
                "unit_nav": item["unit_nav"],
                "return_pct": _return_pct(baseline_value, item["unit_nav"]),
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
    interpretation = _interpret(str(action or ""), latest_return, len(observations))

    return {
        "evaluator_id": EVALUATOR_ID,
        "evaluator_version": EVALUATOR_VERSION,
        "status": evaluation_status,
        "code": str(code),
        "decision": {
            "action": str(action or ""),
            "actionable": str(action or "") in _ACTIONABLE,
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
        "milestones": milestones,
        "interpretation": interpretation,
        "method": {
            "baseline": "immutable_confirmed_nav_saved_in_source_run",
            "observations": "confirmed_nav_dates_strictly_after_baseline",
            "minimum_directional_sample": 20,
            "costs": "not_included",
            "benchmark": "not_included",
            "execution": "user_execution_not_inferred",
        },
        "limitations": [
            "absolute_nav_return_is_not_excess_return",
            "fees_tax_fx_and_user_execution_are_not_included",
            "historical_outcome_does_not_prove_future_strategy_edge",
        ],
        "policy": "结果评估只使用原 Run 保存的确认净值基线和之后的真实确认净值；不回写原结论，不把短样本或用户未执行的动作包装成盈利证明。",
    }
