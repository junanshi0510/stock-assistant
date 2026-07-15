# -*- coding: utf-8 -*-
"""Deterministic aggregation for multi-fund Agent batches."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from .repository import RUN_TERMINAL_STATUSES


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _distribution(values: list[str]) -> list[dict[str, Any]]:
    counts = Counter(value for value in values if value)
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _batch_status(statuses: list[str]) -> str:
    if not statuses:
        return "queued"
    if not all(status in RUN_TERMINAL_STATUSES for status in statuses):
        return "running" if any(status != "queued" for status in statuses) else "queued"
    if all(status == "completed" for status in statuses):
        return "completed"
    if all(status == "failed" for status in statuses):
        return "failed"
    if all(status == "cancelled" for status in statuses):
        return "cancelled"
    return "partial"


def _fact_value(result: dict[str, Any], label: str) -> float | None:
    for item in result.get("facts") or []:
        if item.get("label") == label:
            return _number(item.get("value"))
    return None


def _holding_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pulse = ((result.get("market_intelligence") or {}).get("holding_pulse") or {})
    holdings: dict[str, dict[str, Any]] = {}
    for item in pulse.get("items") or []:
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        market = str(item.get("market") or "unknown").strip().lower()
        ratio = _number(item.get("nav_ratio"))
        if ratio is None or ratio <= 0:
            continue
        key = f"{market}:{code}"
        holdings[key] = {
            "code": code,
            "name": item.get("name") or code,
            "market": market,
            "nav_ratio": round(ratio, 4),
        }
    return holdings


def _run_row(item: dict[str, Any]) -> dict[str, Any]:
    run = item["run"]
    result = run.get("result") or {}
    fund = result.get("fund") or {}
    conclusion = result.get("conclusion") or {}
    market_profile = result.get("market_profile") or {}
    market = market_profile.get("market") or {}
    intelligence = result.get("market_intelligence") or {}
    synthesis = result.get("ai_synthesis") or {}
    synthesis_result = synthesis.get("synthesis") or {}
    strategy = result.get("strategy") or {}
    personalized = result.get("personalized_decision") or {}
    personal_decision = personalized.get("decision") or {}

    if synthesis.get("status") == "available" and synthesis_result.get("action"):
        action = synthesis_result.get("action")
        action_source = "model"
    elif personal_decision.get("action"):
        action = personal_decision.get("action")
        action_source = "deterministic_personal"
    else:
        action = strategy.get("decision")
        action_source = "deterministic_research" if action else None

    return {
        "sequence_no": item["sequence_no"],
        "code": item["code"],
        "run_id": run["id"],
        "status": run["status"],
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "error_code": run.get("error_code"),
        "error_message": run.get("error_message"),
        "fund": {
            "name": fund.get("name"),
            "as_of": fund.get("as_of"),
            "unit_nav": fund.get("unit_nav"),
            "trend_state": fund.get("trend_state"),
        },
        "market": {
            "primary": market.get("primary") or (intelligence.get("market") or {}).get("primary"),
            "label": market.get("label") or (intelligence.get("market") or {}).get("label"),
            "cross_border": market.get("cross_border"),
        },
        "decision": {
            "action": action,
            "source": action_source,
            "role": conclusion.get("role"),
            "risk_band": conclusion.get("risk_band"),
            "timing_label": conclusion.get("timing_label"),
            "strategy_confidence": (strategy.get("confidence") or {}).get("level"),
        },
        "metrics": {
            "return_3m": _fact_value(result, "近 3 月收益"),
            "return_1y": _fact_value(result, "近 1 年收益"),
            "annual_volatility": _fact_value(result, "年化波动"),
            "max_drawdown": _fact_value(result, "样本最大回撤"),
            "current_drawdown": _fact_value(result, "当前回撤"),
        },
        "coverage": {
            "market_intelligence": intelligence.get("status") or "not_requested",
            "news_count": ((intelligence.get("news") or {}).get("count") or 0),
            "model": synthesis.get("status") or "not_requested",
            "model_reason_code": synthesis.get("reason_code"),
            "personalized": bool(result.get("personalized_decision")),
        },
        "holding_count_used_for_overlap": len(_holding_map(result)),
    }


def _holding_overlap(items: list[dict[str, Any]]) -> dict[str, Any]:
    prepared = []
    for item in items:
        run = item["run"]
        result = run.get("result") or {}
        holdings = _holding_map(result)
        if holdings:
            prepared.append((item, holdings))

    pairs = []
    for (left_item, left), (right_item, right) in combinations(prepared, 2):
        common_keys = sorted(set(left) & set(right))
        if not common_keys:
            continue
        shared = []
        overlap = 0.0
        for key in common_keys:
            left_holding = left[key]
            right_holding = right[key]
            contribution = min(left_holding["nav_ratio"], right_holding["nav_ratio"])
            overlap += contribution
            shared.append({
                "code": left_holding["code"],
                "name": left_holding["name"],
                "market": left_holding["market"],
                "left_ratio": left_holding["nav_ratio"],
                "right_ratio": right_holding["nav_ratio"],
                "overlap_contribution": round(contribution, 4),
            })
        shared.sort(key=lambda row: row["overlap_contribution"], reverse=True)
        pairs.append({
            "left_code": left_item["code"],
            "right_code": right_item["code"],
            "overlap_lower_bound_pct": round(overlap, 4),
            "shared_holding_count": len(shared),
            "shared_holdings": shared[:8],
        })
    pairs.sort(key=lambda row: row["overlap_lower_bound_pct"], reverse=True)
    return {
        "status": "available" if len(prepared) >= 2 else "unavailable",
        "covered_fund_count": len(prepared),
        "covered_codes": [str(item[0]["code"]) for item in prepared],
        "total_fund_count": len(items),
        "pairs": pairs[:15],
        "policy": (
            "重合度是本批次成功获取的前 N 大披露持仓重合下界，按共同持仓在两只基金中的较小净值占比求和；"
            "未披露、未覆盖或披露日期不同的部分不推断，因此不能当作完整组合重合度。"
        ),
    }


def summarize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    items = sorted(batch.get("items") or [], key=lambda item: item["sequence_no"])
    rows = [_run_row(item) for item in items]
    statuses = [row["status"] for row in rows]
    terminal_count = sum(status in RUN_TERMINAL_STATUSES for status in statuses)
    completed_count = sum(status == "completed" for status in statuses)
    failed_count = sum(status == "failed" for status in statuses)
    partial_count = sum(status in {"partial", "abstained"} for status in statuses)
    cancelled_count = sum(status == "cancelled" for status in statuses)

    actions = [str((row.get("decision") or {}).get("action") or "") for row in rows]
    markets = [str((row.get("market") or {}).get("label") or "") for row in rows]
    risk_bands = [str((row.get("decision") or {}).get("risk_band") or "") for row in rows]
    intelligence_available = sum(
        (row.get("coverage") or {}).get("market_intelligence") in {"available", "partial"}
        for row in rows
    )
    model_available = sum((row.get("coverage") or {}).get("model") == "available" for row in rows)

    allocation_event = batch.get("allocation_event")
    allocation = None
    if allocation_event is not None:
        if allocation_event.get("integrity_verified"):
            allocation = {
                **(allocation_event.get("payload") or {}),
                "snapshot": {
                    "id": allocation_event.get("id"),
                    "event_hash": allocation_event.get("event_hash"),
                    "payload_sha256": allocation_event.get("payload_sha256"),
                    "created_at": allocation_event.get("created_at"),
                    "integrity_verified": True,
                },
            }
        else:
            allocation = {
                "status": "integrity_failed",
                "blockers": ["已保存的组合资金分配事件未通过内容哈希或事件哈希校验"],
                "snapshot": {
                    "id": allocation_event.get("id"),
                    "created_at": allocation_event.get("created_at"),
                    "integrity_verified": False,
                },
            }
    allocation_by_code = {
        str(item.get("code") or ""): item
        for item in (((allocation or {}).get("allocation") or {}).get("items") or [])
    }
    for row in rows:
        row["portfolio_allocation"] = allocation_by_code.get(row["code"])

    warnings = []
    if failed_count:
        warnings.append(f"{failed_count} 只基金未形成必需 Evidence，需要逐只查看失败原因。")
    if terminal_count and intelligence_available < terminal_count:
        warnings.append("部分终态基金没有形成市场/持仓情报，跨基金重合度覆盖不完整。")
    if terminal_count and model_available < terminal_count:
        warnings.append("部分终态基金没有通过真实模型研判；批次不会用模板补齐。")
    if allocation and allocation.get("status") == "integrity_failed":
        warnings.append("组合资金分配快照完整性失败，全部金额已停止展示。")

    status = _batch_status(statuses)
    return {
        "id": batch["id"],
        "intent": batch["intent"],
        "status": status,
        "input": batch.get("input") or {},
        "input_hash": batch.get("input_hash"),
        "created_at": batch.get("created_at"),
        "updated_at": max(
            [batch.get("updated_at") or ""] + [item["run"].get("updated_at") or "" for item in items]
        ),
        "completed_at": (
            max((item["run"].get("completed_at") or "" for item in items), default="") or None
            if terminal_count == len(items) and items
            else None
        ),
        "progress": {
            "total": len(items),
            "terminal": terminal_count,
            "completed": completed_count,
            "partial": partial_count,
            "failed": failed_count,
            "cancelled": cancelled_count,
            "percent": round(terminal_count / len(items) * 100, 2) if items else 0,
        },
        "summary": {
            "actions": _distribution(actions),
            "markets": _distribution(markets),
            "risk_bands": _distribution(risk_bands),
            "market_intelligence_available": intelligence_available,
            "model_available": model_available,
            "warnings": warnings,
        },
        "holding_overlap": _holding_overlap(items),
        "allocation": allocation,
        "items": rows,
        "policy": (
            "批次只编排并聚合相互独立的单基金 Evidence Run；每一行可进入原始 Run 查看证据和审计。"
            "跨基金摘要不构成收益排名，不自动下单，也不以缺失数据推断优劣。"
        ),
    }
