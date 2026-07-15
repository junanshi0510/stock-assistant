# -*- coding: utf-8 -*-
"""Deterministic pre-trade review for a user-confirmed fund replacement.

The review never authorizes an order. It only decides whether the currently
bound redemption quote may enter manual review and always requires a fresh
purchase quote after redemption proceeds arrive.
"""

from __future__ import annotations

import math
from typing import Any

from investment_policy import payload_sha256


STRATEGY_ID = "fund_switch_execution_review"
STRATEGY_VERSION = "1.0.0"
QUOTE_SCHEMA_VERSION = "fund_switch_platform_quote.v2"
CASHFLOW_TOLERANCE_YUAN = 0.02


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _gate(code: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "pass" if passed else "block",
        "detail": detail,
    }


def _holding_amounts(items: list[dict[str, Any]]) -> tuple[float | None, bool]:
    values = [_number(item.get("amount")) for item in items]
    complete = bool(items) and all(value is not None and value >= 0 for value in values)
    if not complete:
        return None, False
    return sum(value or 0.0 for value in values), True


def evaluate_fund_switch_execution(
    *,
    holding: dict[str, Any],
    cost_review: dict[str, Any],
    quote: dict[str, Any],
    profile: dict[str, Any],
    thesis: dict[str, Any] | None,
    market_profile: dict[str, Any],
    projected_holdings: list[dict[str, Any]],
    projected_exposure: dict[str, Any],
    bindings: dict[str, Any],
    acknowledged_holding_thesis: bool,
    generated_at: str,
) -> dict[str, Any]:
    """Build a fail-closed pre-trade review from already verified evidence."""
    quote_payload = quote.get("payload") or {}
    quote_gate = quote_payload.get("decision_gate") or {}
    quote_cashflow = quote_payload.get("cashflow") or {}
    quote_cost = quote_payload.get("confirmed_cost") or {}
    settlement = quote_payload.get("settlement") or {}
    platform_quote = quote_payload.get("platform_quote") or {}
    review_payload = cost_review.get("payload") or {}
    thesis_payload = (thesis or {}).get("payload") or {}
    market = market_profile.get("market") or {}
    exposure_quality = projected_exposure.get("quality") or {}
    exposure_summary = projected_exposure.get("summary") or {}

    selected_code = str(holding.get("code") or "")
    candidate_code = str(quote.get("candidate_code") or "")
    candidate_name = str(review_payload.get("candidate_name") or candidate_code)
    quote_schema_current = quote_payload.get("schema_version") == QUOTE_SCHEMA_VERSION
    quote_ready = bool(
        quote.get("status") == "confirmed_current"
        and (quote.get("integrity") or {}).get("verified")
        and quote_schema_current
        and quote_gate.get("executable_switch_cost_confirmed")
        and quote_gate.get("cashflow_amounts_confirmed")
        and quote_gate.get("settlement_risk_acknowledged")
    )

    profile_ready = bool(
        profile.get("configured")
        and profile.get("integrity_verified")
        and not profile.get("review_required")
        and (profile.get("governance_integrity") or {}).get("verified")
    )
    thesis_ready = bool(
        thesis
        and thesis.get("state") == "active"
        and thesis.get("integrity_verified")
        and int(thesis_payload.get("holding_id") or 0) == int(holding.get("id") or 0)
        and acknowledged_holding_thesis
    )

    required_markets = set(market.get("required_permissions") or [])
    allowed_markets = set(profile.get("allowed_fund_markets") or []) if profile_ready else set()
    market_identified = bool(
        market_profile.get("resolution_status") == "identified" and required_markets
    )
    market_permission = bool(
        profile_ready and market_identified and required_markets.issubset(allowed_markets)
    )
    fx_ready = bool(
        profile_ready
        and (not market.get("currency_risk") or profile.get("accept_fx_risk"))
    )

    redemption_gross = _number(quote_cashflow.get("redemption_gross_yuan"))
    redemption_fee = _number(quote_cost.get("redemption_fee_yuan"))
    net_proceeds = _number(quote_cashflow.get("redemption_net_proceeds_yuan"))
    order_amount = _number(quote_cashflow.get("candidate_order_amount_yuan"))
    entry_fee = _number(quote_cost.get("candidate_entry_fee_yuan"))
    candidate_net = _number(quote_cashflow.get("candidate_net_asset_amount_yuan"))
    residual_cash = _number(quote_cashflow.get("residual_cash_yuan"))
    cashflow_ready = bool(
        quote_ready
        and redemption_gross is not None
        and redemption_fee is not None
        and net_proceeds is not None
        and order_amount is not None
        and entry_fee is not None
        and candidate_net is not None
        and residual_cash is not None
        and redemption_gross > 0
        and 0 <= redemption_fee < redemption_gross
        and 0 < order_amount <= net_proceeds + CASHFLOW_TOLERANCE_YUAN
        and 0 <= entry_fee < order_amount
        and candidate_net > 0
        and residual_cash >= -CASHFLOW_TOLERANCE_YUAN
        and abs(net_proceeds - (redemption_gross - redemption_fee)) <= CASHFLOW_TOLERANCE_YUAN
        and abs(candidate_net - (order_amount - entry_fee)) <= CASHFLOW_TOLERANCE_YUAN
        and abs(residual_cash - (net_proceeds - order_amount)) <= CASHFLOW_TOLERANCE_YUAN
        and settlement.get("candidate_purchase_available") is True
    )

    projected_total, projected_amounts_complete = _holding_amounts(projected_holdings)
    candidate_projected_amount = sum(
        _number(item.get("amount")) or 0.0
        for item in projected_holdings
        if item.get("asset_type") == "fund"
        and str(item.get("code") or "") == candidate_code
    )
    projected_candidate_ratio = (
        candidate_projected_amount / projected_total * 100
        if projected_total is not None and projected_total > 0 else None
    )
    max_single_ratio = _number(profile.get("max_single_ratio")) if profile_ready else None
    single_limit_ready = bool(
        projected_amounts_complete
        and projected_candidate_ratio is not None
        and max_single_ratio is not None
        and projected_candidate_ratio <= max_single_ratio + 1e-8
    )

    exposure_binding_ready = bool(
        exposure_quality.get("decision_eligible")
        and projected_exposure.get("holdings_sha256") == bindings.get("projected_holdings_sha256")
        and projected_exposure.get("profile_version_id") == profile.get("profile_version_id")
        and projected_exposure.get("target_code") == candidate_code
    )
    equity = exposure_summary.get("equity") or {}
    industry = exposure_summary.get("industry") or {}
    equity_upper = _number(equity.get("upper_ratio"))
    industry_upper = _number(industry.get("max_upper_ratio"))
    max_equity_ratio = _number(profile.get("max_equity_ratio")) if profile_ready else None
    max_industry_ratio = _number(profile.get("max_industry_ratio")) if profile_ready else None
    equity_limit_ready = bool(
        exposure_binding_ready
        and equity_upper is not None
        and max_equity_ratio is not None
        and equity_upper <= max_equity_ratio + 1e-8
    )
    industry_limit_ready = bool(
        exposure_binding_ready
        and industry_upper is not None
        and max_industry_ratio is not None
        and industry_upper <= max_industry_ratio + 1e-8
    )

    gates = [
        _gate(
            "platform_quote_v2",
            "平台报价与现金流",
            quote_ready,
            "真实赎回总额、费用、拟申购金额、到账日和在途风险已确认"
            if quote_ready else "报价已过期、证据已变化或缺少 v2 现金流字段",
        ),
        _gate(
            "investment_policy",
            "投资政策",
            profile_ready,
            f"已绑定 {profile.get('profile_version_id')}"
            if profile_ready else "投资政策未激活、已到期或审计完整性失败",
        ),
        _gate(
            "holding_thesis",
            "原持有逻辑",
            thesis_ready,
            "已阅读用户保存的退出条件；自由文本条件仍由用户人工判断"
            if thesis_ready else "缺少完整持有逻辑，或尚未确认已人工复核退出条件",
        ),
        _gate(
            "fund_market_identified",
            "候选投资市场",
            market_identified,
            market.get("label") or "真实基金元数据无法确认底层市场",
        ),
        _gate(
            "market_permission",
            "市场权限",
            market_permission,
            f"需要 {', '.join(sorted(required_markets)) or '-'}；允许 {', '.join(sorted(allowed_markets)) or '-'}",
        ),
        _gate(
            "foreign_exchange_risk",
            "汇率风险",
            fx_ready,
            "跨境汇率风险已确认或该候选不涉及跨境风险"
            if fx_ready else "候选涉及汇率风险，但投资政策未确认接受",
        ),
        _gate(
            "cashflow_no_prefunding",
            "到账后再申购",
            cashflow_ready,
            "拟申购金额不超过预计到账净额，且禁止垫资提前申购"
            if cashflow_ready else "平台金额无法闭合、候选不可申购或未确认在途风险",
        ),
        _gate(
            "single_position_limit",
            "单品仓位上限",
            single_limit_ready,
            (
                f"换仓后候选约 {projected_candidate_ratio:.2f}%，上限 {max_single_ratio:.2f}%"
                if projected_candidate_ratio is not None and max_single_ratio is not None
                else "缺少完整金额或单品上限"
            ),
        ),
        _gate(
            "projected_exposure",
            "换仓后组合穿透",
            exposure_binding_ready,
            "真实定期披露完整、新鲜且绑定预计换仓后持仓"
            if exposure_binding_ready else "真实披露缺失、过期、冲突或投影绑定失败",
        ),
        _gate(
            "equity_limit",
            "权益仓位上限",
            equity_limit_ready,
            (
                f"最坏上界 {equity_upper:.2f}%，上限 {max_equity_ratio:.2f}%"
                if equity_upper is not None and max_equity_ratio is not None
                else "无法验证权益暴露最坏上界"
            ),
        ),
        _gate(
            "industry_limit",
            "单行业上限",
            industry_limit_ready,
            (
                f"最坏上界 {industry_upper:.2f}%，上限 {max_industry_ratio:.2f}%"
                if industry_upper is not None and max_industry_ratio is not None
                else "无法验证行业暴露最坏上界"
            ),
        ),
    ]
    blockers = [item["code"] for item in gates if item["status"] == "block"]
    redemption_review_ready = not blockers
    if redemption_review_ready:
        status = "ready_for_redemption_review"
        label = "组合与现金流门禁通过，进入人工赎回复核"
    elif not quote_ready:
        status = "blocked_by_quote"
        label = "平台报价或现金流证据不可用"
    elif not profile_ready:
        status = "blocked_by_policy"
        label = "投资政策门禁未通过"
    elif not thesis_ready:
        status = "blocked_by_thesis"
        label = "原持有逻辑尚未完成复核"
    elif not market_identified or not market_permission or not fx_ready:
        status = "blocked_by_market"
        label = "候选市场权限门禁未通过"
    elif not cashflow_ready:
        status = "blocked_by_cashflow"
        label = "赎回与申购现金流无法闭合"
    elif not exposure_binding_ready:
        status = "blocked_by_exposure_evidence"
        label = "换仓后组合穿透证据不足"
    else:
        status = "blocked_by_portfolio_limit"
        label = "换仓后将触发组合上限"

    result = {
        "schema_version": f"{STRATEGY_ID}.v1",
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "status": status,
        "label": label,
        "generated_at": generated_at,
        "holding_id": int(holding.get("id") or 0),
        "selected_code": selected_code,
        "candidate_code": candidate_code,
        "candidate_name": candidate_name,
        "bindings": bindings,
        "cashflow": {
            "redemption_gross_yuan": _round(redemption_gross),
            "redemption_fee_yuan": _round(redemption_fee),
            "redemption_net_proceeds_yuan": _round(net_proceeds),
            "candidate_order_amount_yuan": _round(order_amount),
            "candidate_entry_fee_yuan": _round(entry_fee),
            "candidate_net_asset_amount_yuan": _round(candidate_net),
            "residual_cash_yuan": _round(max(0.0, residual_cash or 0.0)),
            "expected_arrival_date": settlement.get("expected_redemption_arrival_date"),
            "cash_gap_days": settlement.get("cash_gap_days"),
            "pre_funding_allowed": False,
        },
        "position_projection": {
            "invested_total_after_switch_yuan": _round(projected_total),
            "candidate_amount_after_switch_yuan": _round(candidate_projected_amount),
            "candidate_ratio_after_switch_pct": _round(projected_candidate_ratio, 4),
            "max_single_ratio_pct": _round(max_single_ratio, 4),
        },
        "portfolio_projection": {
            "status": projected_exposure.get("status"),
            "evaluated_on": projected_exposure.get("evaluated_on"),
            "equity_upper_ratio_pct": _round(equity_upper, 4),
            "max_equity_ratio_pct": _round(max_equity_ratio, 4),
            "industry_max_upper_ratio_pct": _round(industry_upper, 4),
            "max_industry_ratio_pct": _round(max_industry_ratio, 4),
            "quality_reasons": exposure_quality.get("reasons") or [],
            "failed_sources": projected_exposure.get("failed_sources") or [],
            "fund_disclosures": [
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "periods": item.get("periods") or {},
                    "equity_interval": item.get("equity_interval") or {},
                    "quality": item.get("quality") or {},
                    "linked_fund": item.get("linked_fund"),
                }
                for item in (projected_exposure.get("funds") or [])
            ],
            "target_evidence": {
                key: (projected_exposure.get("target") or {}).get(key)
                for key in ("status", "code", "name", "periods", "quality", "source", "source_url")
            },
        },
        "holding_thesis": {
            "version_id": (thesis or {}).get("id"),
            "payload_sha256": (thesis or {}).get("payload_sha256"),
            "exit_condition": thesis_payload.get("exit_condition"),
            "acknowledged_by_user": acknowledged_holding_thesis,
            "machine_verified": False,
        },
        "market": {
            "primary": market.get("primary"),
            "label": market.get("label"),
            "required_permissions": sorted(required_markets),
            "allowed_permissions": sorted(allowed_markets),
            "currency_risk": bool(market.get("currency_risk")),
            "fx_risk_acknowledged": bool(profile.get("accept_fx_risk")),
            "confirmed_nav_lag": (market_profile.get("valuation") or {}).get("confirmed_nav_lag"),
            "source": market_profile.get("source"),
            "source_url": market_profile.get("source_url"),
        },
        "gates": gates,
        "blockers": blockers,
        "manual_stages": [
            {
                "id": "redemption_submission",
                "state": "reviewable" if redemption_review_ready else "blocked",
                "label": "在报价有效期内人工复核赎回",
                "deadline": platform_quote.get("quote_expires_at"),
                "shares": (review_payload.get("coverage") or {}).get("confirmed_shares"),
                "quoted_gross_yuan": _round(redemption_gross),
            },
            {
                "id": "settlement_wait",
                "state": "required_after_redemption",
                "label": "等待真实赎回款到账，不垫资",
                "expected_arrival_date": settlement.get("expected_redemption_arrival_date"),
                "pre_funding_allowed": False,
            },
            {
                "id": "purchase_requote",
                "state": "blocked_until_settlement",
                "label": "到账后重新获取候选申购报价",
                "intended_order_ceiling_yuan": _round(order_amount),
                "reason": "当前候选申购费仅是提交前预览；到账后的价格、额度和费率必须重新确认。",
            },
            {
                "id": "ledger_reconciliation",
                "state": "required_after_manual_orders",
                "label": "导入实际赎回与申购成交，核对最终费用和份额",
            },
        ],
        "decision_gate": {
            "redemption_review_ready": redemption_review_ready,
            "candidate_purchase_ready": False,
            "full_switch_execution_ready": False,
            "execution_authorized": False,
            "manual_order_required": True,
            "automatic_redemption_allowed": False,
            "automatic_purchase_allowed": False,
            "reason": "all_pretrade_gates_passed_purchase_requote_pending"
            if redemption_review_ready else blockers[0] if blockers else "unknown_blocker",
        },
        "sources": {
            "platform_quote": "用户从销售平台本次交易确认页录入",
            "investment_policy": "用户激活的版本化投资政策",
            "holding_thesis": "用户保存的版本化持有逻辑",
            "market_profile": market_profile.get("source"),
            "portfolio_exposure": projected_exposure.get("source"),
        },
        "policy": (
            "该结果只是一份绑定真实报价、用户政策和定期披露的预交易复核。"
            "即使赎回复核门禁通过，系统也不会下单；候选申购必须等待真实到账并重新报价。"
        ),
    }
    evidence_payload = dict(result)
    result["evidence_sha256"] = payload_sha256(evidence_payload)
    return result
