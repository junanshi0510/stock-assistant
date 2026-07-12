# -*- coding: utf-8 -*-
"""Deterministic comparison of two persisted Agent research results."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


_DIMENSIONS = (
    ("trend_state", "趋势状态", ("fund", "trend_state")),
    ("role", "组合角色", ("conclusion", "role")),
    ("risk_band", "风险带", ("conclusion", "risk_band")),
    ("timing_label", "投入节奏", ("conclusion", "timing_label")),
    ("timing_score", "投入节奏评分", ("conclusion", "timing_score")),
    ("minimum_holding_period", "最低观察周期", ("conclusion", "minimum_holding_period")),
    ("strategy_decision", "历史条件策略判断", ("strategy", "decision")),
    ("strategy_direction", "历史条件策略方向", ("strategy", "signal", "direction")),
    ("strategy_confidence", "历史条件策略置信度", ("strategy", "confidence", "level")),
    ("strategy_horizon", "历史条件策略主窗口", ("strategy", "primary_horizon")),
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nested(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _fact_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("label") or "").strip(), str(item.get("unit") or "").strip()


def compare_run_results(current_run: dict[str, Any], parent_run: dict[str, Any]) -> dict[str, Any]:
    """Compare saved result snapshots without calling providers or recalculating facts."""
    current = current_run.get("result") or {}
    parent = parent_run.get("result") or {}
    if not current or not parent:
        raise ValueError("父子任务必须都形成研究结果后才能比较")

    current_fund = current.get("fund") or {}
    parent_fund = parent.get("fund") or {}
    current_code = str(current_fund.get("code") or (current_run.get("input") or {}).get("code") or "")
    parent_code = str(parent_fund.get("code") or (parent_run.get("input") or {}).get("code") or "")
    if not current_code or current_code != parent_code:
        raise ValueError("父子任务研究标的不同，不能直接比较")

    parent_facts = {_fact_key(item): item for item in (parent.get("facts") or []) if _fact_key(item)[0]}
    current_facts = {_fact_key(item): item for item in (current.get("facts") or []) if _fact_key(item)[0]}
    ordered_keys = list(current_facts)
    ordered_keys.extend(key for key in parent_facts if key not in current_facts)

    metrics = []
    for label, unit in ordered_keys:
        previous_item = parent_facts.get((label, unit)) or {}
        current_item = current_facts.get((label, unit)) or {}
        previous_value = _number(previous_item.get("value"))
        current_value = _number(current_item.get("value"))
        delta = (
            round(current_value - previous_value, 6)
            if previous_value is not None and current_value is not None
            else None
        )
        if previous_value is None and current_value is not None:
            direction = "added"
        elif previous_value is not None and current_value is None:
            direction = "removed"
        elif delta is None or math.isclose(delta, 0.0, abs_tol=1e-9):
            direction = "unchanged"
        else:
            direction = "up" if delta > 0 else "down"
        metrics.append(
            {
                "label": label,
                "unit": unit,
                "previous": previous_value,
                "current": current_value,
                "delta": delta,
                "direction": direction,
                "changed": direction != "unchanged",
                "previous_evidence_id": previous_item.get("evidence_id"),
                "current_evidence_id": current_item.get("evidence_id"),
            }
        )

    dimensions = []
    for key, label, path in _DIMENSIONS:
        previous_value = _nested(parent, path)
        current_value = _nested(current, path)
        dimensions.append(
            {
                "key": key,
                "label": label,
                "previous": previous_value,
                "current": current_value,
                "changed": previous_value != current_value,
            }
        )

    previous_as_of = parent_fund.get("as_of")
    current_as_of = current_fund.get("as_of")
    metric_changed_count = sum(1 for item in metrics if item["changed"])
    dimension_changed_count = sum(1 for item in dimensions if item["changed"])
    data_date_changed = previous_as_of != current_as_of
    return {
        "schema_version": "fund_run_comparison.v1",
        "generated_at": _now(),
        "run_id": current_run["id"],
        "parent_run_id": parent_run["id"],
        "fund": {
            "code": current_code,
            "name": current_fund.get("name") or parent_fund.get("name"),
        },
        "period": {
            "previous_as_of": previous_as_of,
            "current_as_of": current_as_of,
            "data_date_changed": data_date_changed,
        },
        "summary": {
            "metric_changed_count": metric_changed_count,
            "dimension_changed_count": dimension_changed_count,
            "stable": not metric_changed_count and not dimension_changed_count and not data_date_changed,
        },
        "metrics": metrics,
        "dimensions": dimensions,
        "policy": "对比只使用两个 Run 已保存且通过完整性校验的结果，不重新抓取数据，也不构成买卖建议。",
    }
