# -*- coding: utf-8 -*-
"""Deterministic portfolio-level allocation for one completed Agent fund batch."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


STRATEGY_ID = "portfolio_batch_allocation"
STRATEGY_VERSION = "1.0.0"
SCHEMA_VERSION = f"{STRATEGY_ID}.v1"
TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "abstained"}
MONEY_TOLERANCE = 0.02


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: Any) -> float | None:
    number = _number(value)
    return round(number, 2) if number is not None and number >= 0 else None


def _ratio(value: Any) -> float | None:
    number = _number(value)
    return round(number, 6) if number is not None and 0 <= number <= 100 else None


def _gate(code: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "pass" if passed else "block",
        "detail": detail,
    }


def _fact_value(result: dict[str, Any], label: str) -> float | None:
    for item in result.get("facts") or []:
        if item.get("label") == label:
            return _number(item.get("value"))
    return None


def build_run_set_binding(batch: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for item in sorted(batch.get("items") or [], key=lambda row: int(row.get("sequence_no") or 0)):
        run = item.get("run") or {}
        result = run.get("result")
        rows.append({
            "sequence_no": int(item.get("sequence_no") or 0),
            "code": str(item.get("code") or ""),
            "run_id": str(run.get("id") or ""),
            "status": str(run.get("status") or ""),
            "result_sha256": _sha256(result) if isinstance(result, dict) else "",
        })
    return rows, _sha256(rows)


def _same_number(values: list[float | None], tolerance: float = 1e-6) -> bool:
    if not values or any(value is None for value in values):
        return False
    first = float(values[0])
    return all(abs(float(value) - first) <= tolerance for value in values[1:])


def _weighted_capped_allocation(
    budget: float,
    candidates: list[dict[str, Any]],
) -> dict[str, float]:
    allocations = {item["code"]: 0.0 for item in candidates}
    active = {item["code"] for item in candidates}
    remaining = max(0.0, budget)
    by_code = {item["code"]: item for item in candidates}
    while active and remaining > MONEY_TOLERANCE:
        weight_total = sum(by_code[code]["raw_weight"] for code in active)
        if weight_total <= 0:
            break
        capped_codes = []
        proposals = {}
        for code in active:
            candidate = by_code[code]
            room = max(0.0, candidate["capacity_yuan"] - allocations[code])
            proposal = remaining * candidate["raw_weight"] / weight_total
            proposals[code] = proposal
            if proposal >= room - 1e-9:
                capped_codes.append(code)
        if not capped_codes:
            for code, proposal in proposals.items():
                allocations[code] += proposal
            remaining = 0.0
            break
        progressed = 0.0
        for code in capped_codes:
            room = max(0.0, by_code[code]["capacity_yuan"] - allocations[code])
            allocations[code] += room
            progressed += room
            active.remove(code)
        remaining = max(0.0, remaining - progressed)
        if progressed <= 1e-9 and not active:
            break
    return allocations


def _constraint_scale(
    desired: dict[str, float],
    *,
    current_amount: float,
    base_total: float,
    limit_ratio_pct: float,
    target_ratios: dict[str, float],
) -> tuple[float, dict[str, Any]]:
    limit = limit_ratio_pct / 100
    headroom = limit * base_total - current_amount
    effect = sum(
        (target_ratios.get(code, 0.0) / 100 - limit) * amount
        for code, amount in desired.items()
    )
    if headroom < -MONEY_TOLERANCE:
        scale = 0.0
    elif effect > 0:
        scale = max(0.0, min(1.0, headroom / effect))
    else:
        scale = 1.0
    return scale, {
        "current_amount_yuan": _money(current_amount),
        "limit_ratio_pct": round(limit_ratio_pct, 6),
        "headroom_yuan": _money(max(0.0, headroom)),
        "desired_constraint_effect_yuan": round(effect, 6),
        "scale": round(scale, 8),
    }


def evaluate_portfolio_batch_allocation(
    batch: dict[str, Any],
    holding_overlap: dict[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Allocate one explicitly confirmed batch budget without changing child Evidence."""
    items = sorted(batch.get("items") or [], key=lambda row: int(row.get("sequence_no") or 0))
    batch_input = batch.get("input") or {}
    run_set, run_set_sha256 = build_run_set_binding(batch)
    total_budget = _number(batch_input.get("planned_amount"))
    cash_acknowledged = bool(batch_input.get("acknowledged_available_cash"))
    terminal = bool(items) and all(
        str((item.get("run") or {}).get("status") or "") in TERMINAL_STATUSES
        for item in items
    )

    candidates: list[dict[str, Any]] = []
    evidence_complete = True
    basis_rows: list[dict[str, Any]] = []
    for item in items:
        run = item.get("run") or {}
        result = run.get("result") or {}
        personalized = result.get("personalized_decision") or {}
        allocation = personalized.get("batch_allocation") or {}
        basis = allocation.get("basis") or {}
        code = str(item.get("code") or "")
        candidate_reasons = []
        if allocation.get("scope") != "portfolio_batch":
            candidate_reasons.append("该子 Run 不是组合级预算模式，不能复用历史单基金金额")
        if str(basis.get("target_code") or "") != code:
            candidate_reasons.append("子 Run 的目标基金与批次代码不一致")
        if not basis.get("portfolio_holdings_sha256"):
            candidate_reasons.append("缺少绑定的真实持仓哈希")
        if not basis.get("profile_version_id") or not basis.get("profile_payload_sha256"):
            candidate_reasons.append("缺少绑定的投资政策版本或哈希")
        if not basis.get("exposure_snapshot_id") or not basis.get("exposure_snapshot_sha256"):
            candidate_reasons.append("缺少不可变组合穿透快照")
        volatility = _fact_value(result, "年化波动")
        capacity = _number(basis.get("aggregate_candidate_capacity_yuan"))
        individually_eligible = bool(allocation.get("eligible"))
        if individually_eligible and (volatility is None or volatility <= 0):
            candidate_reasons.append("缺少真实年化波动，不能执行逆波动分配")
        if individually_eligible and (capacity is None or capacity <= 0):
            candidate_reasons.append("单基金或组合约束下没有可验证新增容量")
        if candidate_reasons:
            evidence_complete = False
        candidates.append({
            "sequence_no": int(item.get("sequence_no") or 0),
            "code": code,
            "name": ((result.get("fund") or {}).get("name") or code),
            "run_id": str(run.get("id") or ""),
            "run_status": str(run.get("status") or ""),
            "individual_action": (personalized.get("decision") or {}).get("action"),
            "pre_allocation_action": allocation.get("pre_allocation_action"),
            "eligible": bool(individually_eligible and not candidate_reasons),
            "reasons": candidate_reasons or (
                [] if individually_eligible else [
                    str((personalized.get("decision") or {}).get("rationale") or "单基金门禁未放行新增投入")
                ]
            ),
            "annual_volatility_pct": round(volatility, 6) if volatility is not None else None,
            "capacity_yuan": _money(capacity),
            "tranche_count": allocation.get("tranche_count"),
            "basis": basis,
        })
        if basis:
            basis_rows.append(basis)

    eligible = [item for item in candidates if item["eligible"]]
    covered_codes = set(holding_overlap.get("covered_codes") or [])
    overlap_complete = bool(eligible) and all(item["code"] in covered_codes for item in eligible)
    overlap_sums = {item["code"]: 0.0 for item in eligible}
    high_overlap_pairs = []
    for pair in holding_overlap.get("pairs") or []:
        left = str(pair.get("left_code") or "")
        right = str(pair.get("right_code") or "")
        ratio = _number(pair.get("overlap_lower_bound_pct")) or 0.0
        if left in overlap_sums and right in overlap_sums:
            overlap_sums[left] += ratio
            overlap_sums[right] += ratio
            if ratio >= 20:
                high_overlap_pairs.append({
                    "left_code": left,
                    "right_code": right,
                    "overlap_lower_bound_pct": round(ratio, 4),
                })

    common_total_values = [_number(row.get("portfolio_total_amount")) for row in basis_rows]
    holdings_hashes = {str(row.get("portfolio_holdings_sha256") or "") for row in basis_rows}
    profile_versions = {str(row.get("profile_version_id") or "") for row in basis_rows}
    profile_hashes = {str(row.get("profile_payload_sha256") or "") for row in basis_rows}
    binding_consistent = bool(
        len(basis_rows) == len(items)
        and _same_number(common_total_values, tolerance=0.02)
        and len(holdings_hashes) == 1 and "" not in holdings_hashes
        and len(profile_versions) == 1 and "" not in profile_versions
        and len(profile_hashes) == 1 and "" not in profile_hashes
        and (
            not batch.get("input", {}).get("profile_version_id")
            or next(iter(profile_versions), "") == str(batch["input"]["profile_version_id"])
        )
    )
    base_total = common_total_values[0] if binding_consistent else None

    equity_current_values = [
        _number(((row.get("equity") or {}).get("current_upper_amount_yuan")))
        for row in basis_rows
    ]
    equity_limit_values = [
        _number(((row.get("equity") or {}).get("limit_ratio_pct")))
        for row in basis_rows
    ]
    industry_unknown_values = [
        _number(((row.get("industry") or {}).get("current_unknown_equity_amount_yuan")))
        for row in basis_rows
    ]
    industry_limit_values = [
        _number(((row.get("industry") or {}).get("limit_ratio_pct")))
        for row in basis_rows
    ]
    current_industry_maps = [
        (row.get("industry") or {}).get("current_known_lower_amounts_yuan") or {}
        for row in basis_rows
    ]
    current_industries_consistent = bool(
        current_industry_maps
        and all(_canonical(value) == _canonical(current_industry_maps[0]) for value in current_industry_maps[1:])
    )
    aggregate_basis_complete = bool(
        binding_consistent
        and _same_number(equity_current_values, tolerance=0.02)
        and _same_number(equity_limit_values)
        and _same_number(industry_unknown_values, tolerance=0.02)
        and _same_number(industry_limit_values)
        and current_industries_consistent
        and all(
            _ratio(((item["basis"].get("equity") or {}).get("target_upper_ratio_pct"))) is not None
            and _ratio(((item["basis"].get("industry") or {}).get("target_unknown_ratio_pct"))) is not None
            for item in eligible
        )
    )

    gates = [
        _gate("batch_terminal", "批次状态", terminal, "全部子 Run 已到终态" if terminal else "仍有子 Run 未完成"),
        _gate(
            "explicit_total_budget",
            "批次总预算",
            total_budget is not None and total_budget > 0,
            f"本批次唯一总预算 {_money(total_budget)} 元" if total_budget and total_budget > 0 else "未填写本批次唯一总预算",
        ),
        _gate(
            "available_cash_acknowledged",
            "可用资金确认",
            cash_acknowledged,
            "用户已确认预算尚未投入且不占用应急资金" if cash_acknowledged else "尚未确认预算是未占用的真实可用资金",
        ),
        _gate(
            "portfolio_context_enabled",
            "组合上下文",
            bool(batch_input.get("include_portfolio_context")),
            "全部子 Run 应用真实持仓与投资政策" if batch_input.get("include_portfolio_context") else "批次未启用真实组合上下文",
        ),
        _gate(
            "child_evidence_complete",
            "子 Run 证据",
            evidence_complete,
            "每只基金均绑定持仓、政策和穿透快照" if evidence_complete else "至少一只基金缺少组合级分配所需 Evidence",
        ),
        _gate(
            "portfolio_bindings_consistent",
            "组合版本一致",
            binding_consistent,
            "持仓哈希、IPS 版本和组合金额一致" if binding_consistent else "子 Run 绑定了不同持仓、IPS 或组合金额",
        ),
        _gate(
            "overlap_coverage_complete",
            "重合数据覆盖",
            overlap_complete,
            "全部可分配候选都有真实披露持仓" if overlap_complete else "至少一只可分配候选缺少真实披露持仓，不能假设其与其他基金不重合",
        ),
        _gate(
            "aggregate_exposure_basis",
            "联合暴露基座",
            aggregate_basis_complete,
            "权益与行业最坏上界参数完整且版本一致" if aggregate_basis_complete else "缺少联合计算权益或行业上界的参数",
        ),
        _gate(
            "eligible_candidates",
            "可分配候选",
            bool(eligible),
            f"{len(eligible)} 只基金通过单基金门禁" if eligible else "没有基金通过单基金新增投入门禁",
        ),
    ]

    blockers = [gate["detail"] for gate in gates if gate["status"] == "block"]
    allocations = {item["code"]: 0.0 for item in eligible}
    desired: dict[str, float] = {}
    constraint_scale = 0.0
    equity_constraint: dict[str, Any] = {}
    industry_constraints: list[dict[str, Any]] = []

    if not blockers and total_budget is not None and base_total is not None:
        for item in eligible:
            overlap_penalty = 1 / (1 + overlap_sums[item["code"]] / 100)
            item["known_overlap_sum_pct"] = round(overlap_sums[item["code"]], 6)
            item["overlap_penalty"] = round(overlap_penalty, 8)
            item["raw_weight"] = overlap_penalty / float(item["annual_volatility_pct"])
        desired = _weighted_capped_allocation(total_budget, eligible)

        equity_current = float(equity_current_values[0])
        equity_limit = float(equity_limit_values[0])
        equity_targets = {
            item["code"]: float((item["basis"].get("equity") or {})["target_upper_ratio_pct"])
            for item in eligible
        }
        equity_scale, equity_constraint = _constraint_scale(
            desired,
            current_amount=equity_current,
            base_total=base_total,
            limit_ratio_pct=equity_limit,
            target_ratios=equity_targets,
        )

        current_unknown = float(industry_unknown_values[0])
        industry_limit = float(industry_limit_values[0])
        current_industries = {
            str(name): float(_number(amount) or 0)
            for name, amount in current_industry_maps[0].items()
        }
        industry_names = set(current_industries) | {"__unclassified__"}
        for item in eligible:
            industry_names.update(
                str(name)
                for name in ((item["basis"].get("industry") or {}).get("target_known_lower_ratios_pct") or {})
            )
        industry_scales = []
        for industry_name in sorted(industry_names):
            current_amount = current_unknown + current_industries.get(industry_name, 0.0)
            target_ratios = {}
            for item in eligible:
                industry = item["basis"].get("industry") or {}
                known = industry.get("target_known_lower_ratios_pct") or {}
                target_ratios[item["code"]] = min(
                    100.0,
                    float(_number(industry.get("target_unknown_ratio_pct")) or 0)
                    + float(_number(known.get(industry_name)) or 0),
                )
            scale, detail = _constraint_scale(
                desired,
                current_amount=current_amount,
                base_total=base_total,
                limit_ratio_pct=industry_limit,
                target_ratios=target_ratios,
            )
            detail["industry"] = industry_name
            detail["target_ratios_pct"] = {
                code: round(value, 6) for code, value in target_ratios.items()
            }
            industry_constraints.append(detail)
            industry_scales.append(scale)
        constraint_scale = min([1.0, equity_scale, *industry_scales])
        allocations = {
            code: math.floor(amount * constraint_scale * 100 + 1e-7) / 100
            for code, amount in desired.items()
        }

    allocated_total = round(sum(allocations.values()), 2)
    remaining_budget = (
        round(max(0.0, total_budget - allocated_total), 2)
        if total_budget is not None else None
    )
    for item in candidates:
        amount = allocations.get(item["code"], 0.0)
        item["allocated_amount_yuan"] = _money(amount) if item["eligible"] else None
        item["normalized_risk_weight_pct"] = (
            round(amount / allocated_total * 100, 4) if allocated_total > 0 and item["eligible"] else None
        )
        tranche_count = int(item.get("tranche_count") or 0)
        item["first_tranche_amount_yuan"] = (
            _money(amount / tranche_count) if amount > 0 and tranche_count > 0 else None
        )
        item.pop("basis", None)
        item.pop("raw_weight", None)

    status = "ready" if not blockers and allocated_total > 0 else "blocked"
    if not blockers and allocated_total <= 0:
        blockers.append("联合风险约束下没有可分配金额")
    warnings = []
    if high_overlap_pairs:
        warnings.append("至少一组基金的已披露持仓重合下界达到 20%，分配权重已按已知重合度降权。")
    if remaining_budget is not None and remaining_budget > MONEY_TOLERANCE:
        warnings.append("部分预算因单品容量或组合权益/行业上界约束而保留，不会强行分配。")
    if status == "ready":
        warnings.append("重合度只覆盖已披露持仓下界，未知部分不能被解释为已经分散。")

    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": generated_at,
        "status": status,
        "bindings": {
            "batch_id": str(batch.get("id") or ""),
            "batch_input_sha256": str(batch.get("input_hash") or ""),
            "run_set_sha256": run_set_sha256,
            "run_set": run_set,
            "profile_version_id": next(iter(profile_versions), None) if binding_consistent else None,
            "profile_payload_sha256": next(iter(profile_hashes), None) if binding_consistent else None,
            "portfolio_holdings_sha256": next(iter(holdings_hashes), None) if binding_consistent else None,
        },
        "gates": gates,
        "blockers": blockers,
        "budget": {
            "requested_total_yuan": _money(total_budget),
            "allocated_total_yuan": _money(allocated_total),
            "unallocated_total_yuan": remaining_budget,
            "available_cash_acknowledged": cash_acknowledged,
            "scope": "one_total_for_the_entire_batch",
        },
        "allocation": {
            "method": "inverse_realized_volatility_with_disclosed_overlap_penalty",
            "constraint_scale": round(constraint_scale, 8),
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "items": candidates,
        },
        "aggregate_constraints": {
            "base_portfolio_total_yuan": _money(base_total),
            "equity": equity_constraint,
            "industries": industry_constraints,
            "high_overlap_pairs": high_overlap_pairs,
        },
        "decision_gate": {
            "manual_allocation_review_ready": status == "ready",
            "execution_authorized": False,
            "automatic_order_allowed": False,
        },
        "warnings": warnings,
        "method": {
            "return_forecast_used": False,
            "model_amount_used": False,
            "risk_weight": "inverse_of_each_funds_real_annualized_volatility",
            "overlap": "penalize_only_observed_disclosed_holding_overlap_lower_bounds",
            "capacity": "each_candidate_is_capped_by_its_deterministic_single_equity_and_industry_capacity",
            "joint_constraints": "scale_the_whole_risk_weighted_vector_until_equity_and_every_industry_worst_case_bound_passes",
            "rounding": "amounts_are_rounded_down_to_cents_and_residual_cash_is_not_redistributed",
        },
        "policy": (
            "该快照只把一笔已确认的批次研究预算分配到通过门禁的基金，金额来自确定性风险规则，"
            "不使用大模型预测收益，不保证盈利；未分配资金保持未投入，也不授权自动申购或任何订单。"
        ),
    }
