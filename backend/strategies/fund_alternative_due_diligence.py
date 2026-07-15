# -*- coding: utf-8 -*-
"""Versioned fee and disclosed-holding gate for fund replacement candidates."""

from __future__ import annotations

import math
from typing import Any


DIAGNOSTIC_ID = "fund_alternative_due_diligence"
DIAGNOSTIC_VERSION = "1.0.0"
MEDIUM_STOCK_OVERLAP_PCT = 8.0
HIGH_STOCK_OVERLAP_PCT = 20.0
MEDIUM_INDUSTRY_OVERLAP_PCT = 45.0
HIGH_INDUSTRY_OVERLAP_PCT = 70.0
MATERIAL_ANNUAL_COST_EDGE_PP = 0.10
MATERIAL_ANNUAL_COST_PREMIUM_PP = 0.30


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _normalized_name(value: Any) -> str:
    return "".join(str(value or "").split()).upper()


def _holding_map(rows: Any, *, key_field: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key = str(row.get(key_field) or "").strip()
        ratio = _number(row.get("nav_ratio"))
        if not key or ratio is None or ratio < 0:
            continue
        result[key] = {
            "key": key,
            "name": str(row.get("name") or key),
            "ratio": ratio,
        }
    return result


def _overlap_rows(
    selected: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    *,
    include_code: bool,
) -> tuple[float | None, list[dict[str, Any]]]:
    if not selected or not candidate:
        return None, []
    rows = []
    for key in set(selected).intersection(candidate):
        left = selected[key]
        right = candidate[key]
        contribution = min(float(left["ratio"]), float(right["ratio"]))
        row = {
            "name": left.get("name") or right.get("name") or key,
            "selected_ratio_pct": _round(left["ratio"]),
            "candidate_ratio_pct": _round(right["ratio"]),
            "overlap_contribution_pct": _round(contribution),
        }
        if include_code:
            row["code"] = key
        rows.append(row)
    rows.sort(key=lambda row: row["overlap_contribution_pct"] or 0, reverse=True)
    return _round(sum(row["overlap_contribution_pct"] or 0 for row in rows)), rows


def _overlap_level(stock_overlap: float | None, industry_overlap: float | None) -> str:
    if stock_overlap is not None and (
        stock_overlap >= HIGH_STOCK_OVERLAP_PCT
        or (
            stock_overlap >= MEDIUM_STOCK_OVERLAP_PCT
            and industry_overlap is not None
            and industry_overlap >= HIGH_INDUSTRY_OVERLAP_PCT
        )
    ):
        return "high"
    if (
        stock_overlap is None
        and industry_overlap is not None
        and industry_overlap >= HIGH_INDUSTRY_OVERLAP_PCT
    ):
        return "high"
    if (
        stock_overlap is not None and stock_overlap >= MEDIUM_STOCK_OVERLAP_PCT
    ) or (
        industry_overlap is not None and industry_overlap >= MEDIUM_INDUSTRY_OVERLAP_PCT
    ):
        return "medium"
    return "low"


def _operating_fee(payload: dict[str, Any]) -> float | None:
    operating = payload.get("operating") or {}
    explicit_total = _number(operating.get("declared_annual_total_rate_pct"))
    if explicit_total is not None:
        return explicit_total
    values = [
        _number(operating.get("management_rate_pct")),
        _number(operating.get("custodian_rate_pct")),
        _number(operating.get("sales_service_rate_pct")),
    ]
    return sum(values) if all(value is not None for value in values) else None


def _entry_fee(payload: dict[str, Any]) -> float | None:
    purchase = payload.get("purchase") or {}
    return _number(purchase.get("first_band_current_rate_pct"))


def _manager_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    managers = payload.get("managers") if isinstance(payload.get("managers"), list) else []
    manager = next((item for item in managers if isinstance(item, dict)), None)
    if not manager:
        return {
            "status": "unavailable",
            "id": None,
            "name": None,
            "work_time": None,
            "score": None,
            "excess_vs_peer_pct": None,
        }
    return {
        "status": "available",
        "id": str(manager.get("id") or ""),
        "name": str(manager.get("name") or ""),
        "work_time": manager.get("work_time"),
        "score": _round(manager.get("score")),
        "excess_vs_peer_pct": _round(manager.get("excess_vs_peer")),
    }


def _same_manager(selected: dict[str, Any], candidate: dict[str, Any]) -> bool | None:
    if selected.get("status") != "available" or candidate.get("status") != "available":
        return None
    if selected.get("id") and candidate.get("id"):
        return selected["id"] == candidate["id"]
    left = _normalized_name(selected.get("name"))
    right = _normalized_name(candidate.get("name"))
    return bool(left and right and left == right)


def _check(code: str, label: str, passed: bool | None, observed: Any) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "pass" if passed is True else "fail" if passed is False else "pending",
        "observed": observed,
    }


def _source_status(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "unavailable")


def _candidate_result(selected: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    durability = candidate.get("durability") or {}
    durability_eligible = bool(
        (durability.get("decision_gate") or {}).get("eligible_for_due_diligence")
    )
    selected_portfolio = selected.get("portfolio") or {}
    candidate_portfolio = candidate.get("portfolio") or {}
    source_gaps = []
    for owner, source_name, payload in (
        ("selected", "portfolio", selected_portfolio),
        ("candidate", "portfolio", candidate_portfolio),
        ("selected", "fees", selected.get("fees") or {}),
        ("candidate", "fees", candidate.get("fees") or {}),
    ):
        if str(payload.get("status") or "") == "unavailable" or payload.get("reason"):
            source_gaps.append({
                "owner": owner,
                "source": source_name,
                "reason": str(payload.get("reason") or "source_unavailable"),
            })
    selected_stocks = _holding_map(selected_portfolio.get("stocks"), key_field="code")
    candidate_stocks = _holding_map(candidate_portfolio.get("stocks"), key_field="code")
    selected_industries = _holding_map(selected_portfolio.get("industries"), key_field="name")
    candidate_industries = _holding_map(candidate_portfolio.get("industries"), key_field="name")
    stock_overlap, common_stocks = _overlap_rows(
        selected_stocks,
        candidate_stocks,
        include_code=True,
    )
    industry_overlap, common_industries = _overlap_rows(
        selected_industries,
        candidate_industries,
        include_code=False,
    )
    overlap_available = stock_overlap is not None or industry_overlap is not None
    overlap_level = _overlap_level(stock_overlap, industry_overlap) if overlap_available else "unavailable"

    selected_fees = selected.get("fees") or {}
    candidate_fees = candidate.get("fees") or {}
    selected_operating = _operating_fee(selected_fees)
    candidate_operating = _operating_fee(candidate_fees)
    annual_cost_delta = (
        candidate_operating - selected_operating
        if candidate_operating is not None and selected_operating is not None
        else None
    )
    selected_entry = _entry_fee(selected_fees)
    candidate_entry = _entry_fee(candidate_fees)
    entry_fee_delta = (
        candidate_entry - selected_entry
        if candidate_entry is not None and selected_entry is not None
        else None
    )
    selected_redemption = (selected_fees.get("redemption") or {}).get("bands") or []
    fee_evidence_complete = bool(
        _source_status(selected_fees) == "available"
        and _source_status(candidate_fees) == "available"
        and selected_operating is not None
        and candidate_operating is not None
        and candidate_entry is not None
        and selected_redemption
    )
    cost_edge = bool(
        annual_cost_delta is not None
        and annual_cost_delta <= -MATERIAL_ANNUAL_COST_EDGE_PP
    )
    cost_premium = bool(
        annual_cost_delta is not None
        and annual_cost_delta >= MATERIAL_ANNUAL_COST_PREMIUM_PP
    )

    selected_manager = _manager_snapshot(selected)
    candidate_manager = _manager_snapshot(candidate)
    manager_available = bool(
        selected_manager["status"] == "available"
        and candidate_manager["status"] == "available"
    )
    same_manager = _same_manager(selected_manager, candidate_manager)

    if not durability_eligible:
        status = "blocked_by_durability"
        label = "持续性未过门禁"
        rationale = "滚动持续性门禁未通过，不因费率或披露持仓差异升级为换仓候选。"
    elif not overlap_available:
        status = "insufficient_disclosure"
        label = "披露持仓不足"
        rationale = "当前基金与候选没有足够的共同定期报告持仓口径，不能判断替换后是否真的改变风险暴露。"
    elif not fee_evidence_complete:
        status = "incomplete_fee_evidence"
        label = "完整费率待补齐"
        rationale = "真实费率页没有形成完整的运作费、候选申购费和当前基金赎回费区间，暂不进入换仓成本核验。"
    elif overlap_level == "high" and not cost_edge:
        status = "duplicate_without_cost_edge"
        label = "高重合且无成本优势"
        rationale = "定期报告显示底层暴露高度重复，候选又没有明确的年度披露费率优势，当前没有足够替换价值。"
    elif overlap_level == "high":
        status = "duplicate_but_cost_edge"
        label = "同类暴露但费率更低"
        rationale = "底层披露持仓高度重复，但候选年度明确运作费率更低，可继续核对实际持有天数与赎回报价。"
    elif cost_premium:
        status = "distinct_but_costlier"
        label = "暴露不同但费率更高"
        rationale = "候选能改变部分底层暴露，但年度明确运作费率明显更高，需要证明差异化能力足以覆盖长期成本。"
    elif overlap_level == "medium":
        status = "partial_overlap_candidate"
        label = "部分重合可继续核验"
        rationale = "持续性已通过且披露持仓仅部分重合，可继续核对用户实际赎回费和平台成交成本。"
    else:
        status = "distinct_candidate"
        label = "差异化候选可继续核验"
        rationale = "持续性通过，披露持仓重合下界较低且年度费率未显著恶化，可进入用户级换仓成本核验。"

    review_ready = status in {
        "duplicate_but_cost_edge",
        "distinct_but_costlier",
        "partial_overlap_candidate",
        "distinct_candidate",
    }
    checks = [
        _check(
            "durability_gate",
            "滚动持续性允许进入尽调",
            durability_eligible,
            durability.get("status") or "unavailable",
        ),
        _check(
            "disclosed_overlap_available",
            "双方定期报告可计算重合下界",
            overlap_available,
            {
                "stock_overlap_lower_bound_pct": stock_overlap,
                "industry_overlap_lower_bound_pct": industry_overlap,
            },
        ),
        _check(
            "duplicate_exposure_control",
            "不存在无成本优势的高度重复暴露",
            None if not overlap_available else not (overlap_level == "high" and not cost_edge),
            overlap_level,
        ),
        _check(
            "declared_fee_evidence",
            "真实费率页关键字段完整",
            fee_evidence_complete,
            {
                "annual_cost_delta_pp": _round(annual_cost_delta),
                "candidate_entry_rate_pct": _round(candidate_entry),
                "selected_redemption_band_count": len(selected_redemption),
            },
        ),
        _check(
            "manager_evidence",
            "双方当前基金经理证据可用",
            manager_available if durability_eligible else None,
            {
                "selected": selected_manager.get("name"),
                "candidate": candidate_manager.get("name"),
                "same_manager": same_manager,
            },
        ),
        _check(
            "user_transaction_cost",
            "用户持有天数、份额批次和平台赎回报价已核验",
            None,
            "holding_lots_and_platform_quote_required",
        ),
    ]
    return {
        "code": str(candidate.get("code") or ""),
        "name": str(candidate.get("name") or ""),
        "status": status,
        "label": label,
        "rationale": rationale,
        "source_gaps": source_gaps,
        "overlap": {
            "status": "available" if overlap_available else "unavailable",
            "level": overlap_level,
            "stock_overlap_lower_bound_pct": stock_overlap,
            "industry_overlap_lower_bound_pct": industry_overlap,
            "common_stock_count": len(common_stocks),
            "common_industry_count": len(common_industries),
            "common_stocks": common_stocks[:6],
            "common_industries": common_industries[:5],
            "selected_stock_period": selected_portfolio.get("stock_period"),
            "candidate_stock_period": candidate_portfolio.get("stock_period"),
            "selected_industry_period": selected_portfolio.get("industry_period"),
            "candidate_industry_period": candidate_portfolio.get("industry_period"),
            "selected_stock_disclosed_pct": _round(sum(row["ratio"] for row in selected_stocks.values())),
            "candidate_stock_disclosed_pct": _round(sum(row["ratio"] for row in candidate_stocks.values())),
            "method": "共同披露持仓按双方占净值比例较小值求和，仅代表重合下界。",
        },
        "fees": {
            "status": "available" if fee_evidence_complete else "incomplete",
            "selected_declared_annual_rate_pct": _round(selected_operating),
            "candidate_declared_annual_rate_pct": _round(candidate_operating),
            "annual_rate_delta_pp": _round(annual_cost_delta),
            "selected_first_band_entry_rate_pct": _round(selected_entry),
            "candidate_first_band_entry_rate_pct": _round(candidate_entry),
            "entry_rate_delta_pp": _round(entry_fee_delta),
            "selected_redemption_bands": selected_redemption[:8],
            "actual_redemption_rate_pct": None,
            "actual_redemption_rate_reason": "holding_lots_and_platform_quote_required",
            "nav_already_net_of_operating_fees": True,
        },
        "manager": {
            "status": "available" if manager_available else "incomplete",
            "same_manager": same_manager,
            "selected": selected_manager,
            "candidate": candidate_manager,
        },
        "decision_gate": {
            "eligible_for_holding_period_cost_review": review_ready,
            "automatic_switch_allowed": False,
            "automatic_purchase_allowed": False,
            "automatic_redemption_allowed": False,
            "reason": status,
            "checks": checks,
            "remaining_requirements": [
                "用户每笔买入日期与剩余份额",
                "销售平台当日赎回费与申购费报价",
                "税费、到账时间和在途市场风险",
                "最新基金经理与投资合同是否发生实质变化",
            ],
        },
    }


def evaluate_alternative_due_diligence(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate real fee pages and periodic-report overlap after durability."""
    results = [_candidate_result(selected, item) for item in candidates]
    evaluated_count = sum(
        item["status"] not in {"insufficient_disclosure", "incomplete_fee_evidence"}
        for item in results
    )
    review_ready_count = sum(
        bool((item.get("decision_gate") or {}).get("eligible_for_holding_period_cost_review"))
        for item in results
    )
    status = "evaluated" if results and evaluated_count == len(results) else "partial" if results else "unavailable"
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "status": status,
        "selected": {
            "code": selected.get("code"),
            "name": selected.get("name"),
        },
        "candidates": results,
        "summary": {
            "candidate_count": len(results),
            "evaluated_count": evaluated_count,
            "holding_period_cost_review_count": review_ready_count,
            "duplicate_without_cost_edge_count": sum(
                item["status"] == "duplicate_without_cost_edge" for item in results
            ),
            "incomplete_disclosure_count": sum(
                item["status"] == "insufficient_disclosure" for item in results
            ),
            "incomplete_fee_count": sum(
                item["status"] == "incomplete_fee_evidence" for item in results
            ),
        },
        "thresholds": {
            "medium_stock_overlap_pct": MEDIUM_STOCK_OVERLAP_PCT,
            "high_stock_overlap_pct": HIGH_STOCK_OVERLAP_PCT,
            "medium_industry_overlap_pct": MEDIUM_INDUSTRY_OVERLAP_PCT,
            "high_industry_overlap_pct": HIGH_INDUSTRY_OVERLAP_PCT,
            "material_annual_cost_edge_pp": MATERIAL_ANNUAL_COST_EDGE_PP,
            "material_annual_cost_premium_pp": MATERIAL_ANNUAL_COST_PREMIUM_PP,
        },
        "method": {
            "fees": "管理费、托管费、销售服务费、申购费和赎回费区间来自基金费率档案页；净值历史已扣除运作费，不重复扣减。",
            "overlap": "只比较双方最新可得定期报告中的共同股票与行业，并按较小净值占比求和，因此结果是重合下界。",
            "manager": "基金经理信息来自基金详情页当前经理披露，只作为继续尽调证据。",
        },
        "limitations": [
            "定期报告持仓滞后，不代表实时组合。",
            "页面优惠申购费可能随销售平台和金额变化。",
            "没有用户逐笔持有天数时不能确定实际赎回费。",
            "历史净值超额不代表未来仍能覆盖成本。",
        ],
        "policy": "本门禁只决定是否继续用户级换仓成本核验；任何状态都禁止自动买入、赎回或切换。",
    }
