# -*- coding: utf-8 -*-
"""Pure rules for an audited, post-redemption fund replacement lifecycle."""

from __future__ import annotations

from typing import Any

from investment_policy import payload_sha256


STRATEGY_ID = "fund_switch_lifecycle"
STRATEGY_VERSION = "1.0.0"
SCHEMA_VERSION = f"{STRATEGY_ID}.v1"
CASH_TOLERANCE_YUAN = 0.02


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _with_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["evidence_sha256"] = payload_sha256(payload)
    return result


def _gate(code: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "pass" if passed else "block",
        "detail": detail,
    }


def _holding_amounts(holdings: list[dict[str, Any]]) -> tuple[float | None, bool]:
    values = []
    complete = True
    for item in holdings:
        amount = _number(item.get("amount"))
        if amount is None or amount < 0:
            complete = False
            continue
        values.append(amount)
    return (sum(values) if values else None), complete


def evaluate_redemption_settlement(
    *,
    case_id: str,
    holding_id: int,
    selected_code: str,
    candidate_code: str,
    candidate_name: str,
    execution_review: dict[str, Any],
    redemption_transaction: dict[str, Any],
    settled_on: str,
    actual_received_yuan: float,
    acknowledged_quote_variance: bool,
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Bind one confirmed redemption and actual credited cash to a review."""
    review_payload = execution_review.get("payload") or {}
    quoted = review_payload.get("cashflow") or {}
    transaction_gross = (
        (_number(redemption_transaction.get("shares")) or 0)
        * (_number(redemption_transaction.get("unit_price")) or 0)
    )
    transaction_fee = _number(redemption_transaction.get("fee")) or 0
    transaction_net = transaction_gross - transaction_fee
    quoted_gross = _number(quoted.get("redemption_gross_yuan"))
    quoted_net = _number(quoted.get("redemption_net_proceeds_yuan"))
    gross_variance = (
        transaction_gross - quoted_gross if quoted_gross is not None else None
    )
    net_variance = (
        actual_received_yuan - quoted_net if quoted_net is not None else None
    )
    variance_threshold = max(10.0, abs(quoted_gross or 0) * 0.005)
    material_variance = bool(
        gross_variance is not None and abs(gross_variance) > variance_threshold
    )
    if material_variance and not acknowledged_quote_variance:
        raise ValueError("实际赎回总额明显偏离执行前报价，请复核后明确确认差异")
    if abs(actual_received_yuan - transaction_net) > CASH_TOLERANCE_YUAN:
        raise ValueError("实际到账金额与赎回流水净额不一致，请修正流水费用或到账金额")

    return _with_evidence({
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "event_type": "redemption_settled",
        "status": "settled_purchase_requote_required",
        "case_id": case_id,
        "holding_id": int(holding_id),
        "selected_code": selected_code,
        "candidate_code": candidate_code,
        "candidate_name": candidate_name,
        "generated_at": generated_at,
        "bindings": bindings,
        "redemption": {
            "transaction_id": redemption_transaction.get("id"),
            "trade_date": redemption_transaction.get("trade_date"),
            "settled_on": settled_on,
            "shares": _round(redemption_transaction.get("shares"), 8),
            "confirmed_unit_price": _round(redemption_transaction.get("unit_price"), 8),
            "actual_gross_yuan": _round(transaction_gross),
            "actual_fee_yuan": _round(transaction_fee),
            "actual_received_yuan": _round(actual_received_yuan),
            "quoted_gross_yuan": _round(quoted_gross),
            "quoted_net_yuan": _round(quoted_net),
            "gross_variance_yuan": _round(gross_variance),
            "net_variance_yuan": _round(net_variance),
            "material_quote_variance": material_variance,
            "acknowledged_quote_variance": bool(acknowledged_quote_variance),
            "expected_arrival_date": quoted.get("expected_arrival_date"),
        },
        "decision_gate": {
            "settlement_confirmed": True,
            "manual_purchase_review_ready": False,
            "purchase_recorded": False,
            "holdings_reconciled": False,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "reason": "actual_redemption_settled_purchase_requote_required",
        },
        "next_required_action": "到账后从销售平台重新确认候选申购金额、费用、可申购状态和确认日期",
        "sources": {
            "redemption_transaction": "用户确认交易流水",
            "settlement": "用户从销售平台或结算账户确认的真实到账",
        },
        "policy": "到账确认只证明赎回现金已落账，不授权候选申购。",
    })


def evaluate_purchase_requote(
    *,
    case_id: str,
    holding_id: int,
    selected_code: str,
    candidate_code: str,
    candidate_name: str,
    settlement_event: dict[str, Any],
    quoted_at: str,
    quote_expires_at: str,
    expected_confirmation_date: str,
    platform_name: str,
    order_amount_yuan: float,
    entry_fee_yuan: float,
    purchase_available: bool,
    platform_quote_acknowledged: bool,
    profile: dict[str, Any],
    market_profile: dict[str, Any],
    projected_holdings: list[dict[str, Any]],
    projected_exposure: dict[str, Any],
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Re-run purchase gates only after real redemption cash has settled."""
    settlement_payload = settlement_event.get("payload") or {}
    available_cash = _number(
        (settlement_payload.get("redemption") or {}).get("actual_received_yuan")
    )
    candidate_net = order_amount_yuan - entry_fee_yuan
    residual_cash = (
        available_cash - order_amount_yuan if available_cash is not None else None
    )
    profile_ready = bool(
        profile.get("configured")
        and profile.get("integrity_verified")
        and not profile.get("review_required")
        and (profile.get("governance_integrity") or {}).get("verified")
    )
    market = market_profile.get("market") or {}
    required_markets = set(market.get("required_permissions") or [])
    allowed_markets = set(profile.get("allowed_fund_markets") or []) if profile_ready else set()
    market_identified = bool(
        market_profile.get("resolution_status") == "identified" and required_markets
    )
    market_permission = bool(
        market_identified and profile_ready and required_markets.issubset(allowed_markets)
    )
    fx_ready = bool(
        profile_ready
        and (not market.get("currency_risk") or profile.get("accept_fx_risk"))
    )
    cash_ready = bool(
        platform_quote_acknowledged
        and purchase_available
        and available_cash is not None
        and available_cash > 0
        and 0 < order_amount_yuan <= available_cash + CASH_TOLERANCE_YUAN
        and 0 <= entry_fee_yuan < order_amount_yuan
        and candidate_net > 0
        and residual_cash is not None
        and residual_cash >= -CASH_TOLERANCE_YUAN
    )

    projected_total, projected_amounts_complete = _holding_amounts(projected_holdings)
    candidate_projected_amount = sum(
        _number(item.get("amount")) or 0
        for item in projected_holdings
        if item.get("asset_type") == "fund"
        and str(item.get("code") or "") == candidate_code
    )
    candidate_ratio = (
        candidate_projected_amount / projected_total * 100
        if projected_total is not None and projected_total > 0 else None
    )
    max_single = _number(profile.get("max_single_ratio")) if profile_ready else None
    single_ready = bool(
        projected_amounts_complete
        and candidate_ratio is not None
        and max_single is not None
        and candidate_ratio <= max_single + 1e-8
    )

    quality = projected_exposure.get("quality") or {}
    summary = projected_exposure.get("summary") or {}
    exposure_ready = bool(
        quality.get("decision_eligible")
        and projected_exposure.get("holdings_sha256")
        == bindings.get("projected_holdings_sha256")
        and projected_exposure.get("profile_version_id")
        == profile.get("profile_version_id")
        and projected_exposure.get("target_code") == candidate_code
    )
    equity_upper = _number((summary.get("equity") or {}).get("upper_ratio"))
    industry_upper = _number((summary.get("industry") or {}).get("max_upper_ratio"))
    max_equity = _number(profile.get("max_equity_ratio")) if profile_ready else None
    max_industry = _number(profile.get("max_industry_ratio")) if profile_ready else None
    equity_ready = bool(
        exposure_ready and equity_upper is not None and max_equity is not None
        and equity_upper <= max_equity + 1e-8
    )
    industry_ready = bool(
        exposure_ready and industry_upper is not None and max_industry is not None
        and industry_upper <= max_industry + 1e-8
    )

    gates = [
        _gate(
            "settled_cash",
            "真实到账资金",
            cash_ready,
            "申购金额不超过已到账现金，未使用预计资金垫资"
            if cash_ready else "申购金额、费用、可申购状态或平台确认无法闭合",
        ),
        _gate(
            "investment_policy",
            "当前投资政策",
            profile_ready,
            f"已绑定 {profile.get('profile_version_id')}"
            if profile_ready else "投资政策未激活、已过期或审计完整性失败",
        ),
        _gate(
            "fund_market",
            "候选市场与汇率权限",
            market_identified and market_permission and fx_ready,
            f"需要 {', '.join(sorted(required_markets)) or '-'}；允许 {', '.join(sorted(allowed_markets)) or '-'}"
            if market_identified else "真实基金元数据无法确认底层市场",
        ),
        _gate(
            "single_position_limit",
            "单品仓位上限",
            single_ready,
            f"申购后候选约 {candidate_ratio:.2f}%，上限 {max_single:.2f}%"
            if candidate_ratio is not None and max_single is not None
            else "缺少完整金额或单品上限",
        ),
        _gate(
            "projected_exposure",
            "申购后组合穿透",
            exposure_ready,
            "真实定期披露完整、新鲜且绑定当前申购投影"
            if exposure_ready else "真实披露缺失、过期、冲突或投影绑定失败",
        ),
        _gate(
            "equity_limit",
            "权益仓位上限",
            equity_ready,
            f"最坏上界 {equity_upper:.2f}%，上限 {max_equity:.2f}%"
            if equity_upper is not None and max_equity is not None
            else "无法验证权益暴露最坏上界",
        ),
        _gate(
            "industry_limit",
            "单行业上限",
            industry_ready,
            f"最坏上界 {industry_upper:.2f}%，上限 {max_industry:.2f}%"
            if industry_upper is not None and max_industry is not None
            else "无法验证行业暴露最坏上界",
        ),
    ]
    blockers = [item["code"] for item in gates if item["status"] == "block"]
    ready = not blockers
    status = "ready_for_manual_purchase_review" if ready else "purchase_requote_blocked"

    return _with_evidence({
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "event_type": "purchase_requoted",
        "status": status,
        "case_id": case_id,
        "holding_id": int(holding_id),
        "selected_code": selected_code,
        "candidate_code": candidate_code,
        "candidate_name": candidate_name,
        "generated_at": generated_at,
        "bindings": bindings,
        "purchase_quote": {
            "platform_name": platform_name,
            "quoted_at": quoted_at,
            "quote_expires_at": quote_expires_at,
            "expected_confirmation_date": expected_confirmation_date,
            "candidate_purchase_available": bool(purchase_available),
            "platform_quote_acknowledged": bool(platform_quote_acknowledged),
            "available_settled_cash_yuan": _round(available_cash),
            "order_amount_yuan": _round(order_amount_yuan),
            "entry_fee_yuan": _round(entry_fee_yuan),
            "candidate_net_asset_amount_yuan": _round(candidate_net),
            "residual_cash_after_order_yuan": _round(max(0.0, residual_cash or 0.0)),
        },
        "position_projection": {
            "invested_total_after_purchase_yuan": _round(projected_total),
            "candidate_amount_after_purchase_yuan": _round(candidate_projected_amount),
            "candidate_ratio_after_purchase_pct": _round(candidate_ratio, 4),
            "max_single_ratio_pct": _round(max_single, 4),
        },
        "portfolio_projection": {
            "status": projected_exposure.get("status"),
            "evaluated_on": projected_exposure.get("evaluated_on"),
            "equity_upper_ratio_pct": _round(equity_upper, 4),
            "max_equity_ratio_pct": _round(max_equity, 4),
            "industry_max_upper_ratio_pct": _round(industry_upper, 4),
            "max_industry_ratio_pct": _round(max_industry, 4),
            "quality_reasons": quality.get("reasons") or [],
            "failed_sources": projected_exposure.get("failed_sources") or [],
            "fund_disclosures": [
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "periods": item.get("periods") or {},
                    "quality": item.get("quality") or {},
                }
                for item in (projected_exposure.get("funds") or [])
            ],
        },
        "market": {
            "primary": market.get("primary"),
            "label": market.get("label"),
            "required_permissions": sorted(required_markets),
            "allowed_permissions": sorted(allowed_markets),
            "currency_risk": bool(market.get("currency_risk")),
            "fx_risk_acknowledged": bool(profile.get("accept_fx_risk")),
            "source": market_profile.get("source"),
            "source_url": market_profile.get("source_url"),
        },
        "gates": gates,
        "blockers": blockers,
        "decision_gate": {
            "settlement_confirmed": True,
            "manual_purchase_review_ready": ready,
            "purchase_recorded": False,
            "holdings_reconciled": False,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "reason": "all_post_settlement_purchase_gates_passed"
            if ready else blockers[0] if blockers else "unknown_blocker",
        },
        "sources": {
            "purchase_quote": "用户从销售平台到账后申购确认页录入",
            "investment_policy": "用户当前激活的版本化投资政策",
            "market_profile": market_profile.get("source"),
            "portfolio_exposure": projected_exposure.get("source"),
        },
        "policy": "该状态只允许用户人工复核并自行下单；最终份额、净值和费用必须由实际成交流水确认。",
    })


def evaluate_purchase_record(
    *,
    case_id: str,
    holding_id: int,
    selected_code: str,
    candidate_code: str,
    candidate_name: str,
    purchase_quote_event: dict[str, Any],
    purchase_transaction: dict[str, Any],
    submitted_at: str,
    acknowledged_order_variance: bool,
    settled_cash_yuan: float,
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    quote = (purchase_quote_event.get("payload") or {}).get("purchase_quote") or {}
    shares = _number(purchase_transaction.get("shares")) or 0
    unit_price = _number(purchase_transaction.get("unit_price")) or 0
    fee = _number(purchase_transaction.get("fee")) or 0
    gross = shares * unit_price
    cash_used = gross + fee
    residual_cash = settled_cash_yuan - cash_used
    quoted_order = _number(quote.get("order_amount_yuan"))
    order_variance = cash_used - quoted_order if quoted_order is not None else None
    variance_threshold = max(2.0, abs(quoted_order or 0) * 0.001)
    material_variance = bool(
        order_variance is not None and abs(order_variance) > variance_threshold
    )
    if material_variance and not acknowledged_order_variance:
        raise ValueError("实际申购使用金额明显偏离到账后报价，请复核后明确确认差异")
    if cash_used > settled_cash_yuan + CASH_TOLERANCE_YUAN:
        raise ValueError("实际申购使用金额超过已确认到账资金，不能记录为本次替换成交")

    return _with_evidence({
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "event_type": "purchase_recorded",
        "status": "purchase_recorded_reconciliation_pending",
        "case_id": case_id,
        "holding_id": int(holding_id),
        "selected_code": selected_code,
        "candidate_code": candidate_code,
        "candidate_name": candidate_name,
        "generated_at": generated_at,
        "bindings": bindings,
        "purchase": {
            "transaction_id": purchase_transaction.get("id"),
            "submitted_at": submitted_at,
            "confirmation_date": purchase_transaction.get("trade_date"),
            "shares": _round(shares, 8),
            "confirmed_unit_price": _round(unit_price, 8),
            "actual_asset_amount_yuan": _round(gross),
            "actual_fee_yuan": _round(fee),
            "actual_cash_used_yuan": _round(cash_used),
            "settled_cash_ceiling_yuan": _round(settled_cash_yuan),
            "residual_cash_yuan": _round(max(0.0, residual_cash)),
            "quoted_order_amount_yuan": _round(quoted_order),
            "order_variance_yuan": _round(order_variance),
            "material_order_variance": material_variance,
            "acknowledged_order_variance": bool(acknowledged_order_variance),
        },
        "decision_gate": {
            "settlement_confirmed": True,
            "manual_purchase_review_ready": False,
            "purchase_recorded": True,
            "holdings_reconciled": False,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "reason": "actual_purchase_recorded_holdings_reconciliation_required",
        },
        "next_required_action": "更新当前确认持仓并核对原基金剩余份额与候选基金账本份额",
        "sources": {"purchase_transaction": "用户确认交易流水"},
        "policy": "成交回填只记录用户已经完成的真实交易，不代表系统曾授权或执行该申购。",
    })


def evaluate_holdings_reconciliation(
    *,
    case_id: str,
    holding_id: int,
    selected_code: str,
    candidate_code: str,
    candidate_name: str,
    reconciliation: dict[str, Any],
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    if not reconciliation.get("ready"):
        raise ValueError("当前持仓与交易账本尚未完全一致，不能确认替换批次已对账")
    return _with_evidence({
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "event_type": "holdings_reconciled",
        "status": "completed_attribution_pending",
        "case_id": case_id,
        "holding_id": int(holding_id),
        "selected_code": selected_code,
        "candidate_code": candidate_code,
        "candidate_name": candidate_name,
        "generated_at": generated_at,
        "bindings": bindings,
        "reconciliation": reconciliation,
        "decision_gate": {
            "settlement_confirmed": True,
            "manual_purchase_review_ready": False,
            "purchase_recorded": True,
            "holdings_reconciled": True,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "reason": "actual_transactions_and_confirmed_holdings_reconciled",
        },
        "next_required_action": "按需刷新真实净值归因，比较替换路径与继续持有原基金的历史结果",
        "sources": {
            "holdings": "用户当前确认持仓",
            "ledger": "用户确认交易流水 / FIFO 剩余份额",
        },
        "policy": "对账完成只证明持仓事实闭合，不证明替换决策未来仍会盈利。",
    })


def evaluate_attribution_snapshot(
    *,
    case_id: str,
    holding_id: int,
    selected_code: str,
    candidate_code: str,
    candidate_name: str,
    redemption: dict[str, Any],
    purchase: dict[str, Any],
    source_history: dict[str, Any] | None,
    candidate_history: dict[str, Any] | None,
    source_distributions: dict[str, Any] | None,
    candidate_distributions: dict[str, Any] | None,
    source_errors: list[dict[str, str]],
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Compare the realized switch path with a no-switch path on one real NAV date."""
    reasons = []
    if source_errors:
        reasons.append("真实净值或分红送配数据源不可用")
    source_points = {
        str(item.get("date")): item
        for item in ((source_history or {}).get("points") or [])
        if item.get("date") and _number(item.get("unit_nav")) is not None
    }
    candidate_points = {
        str(item.get("date")): item
        for item in ((candidate_history or {}).get("points") or [])
        if item.get("date") and _number(item.get("unit_nav")) is not None
    }
    sale_date = str(redemption.get("trade_date") or "")
    purchase_date = str(purchase.get("confirmation_date") or "")
    common_dates = sorted(
        date for date in set(source_points) & set(candidate_points)
        if date >= max(sale_date, purchase_date)
    )
    as_of = common_dates[-1] if common_dates else None
    if not as_of:
        reasons.append("两只基金没有可用于同日比较的确认净值")
    if sale_date not in source_points:
        reasons.append("原基金赎回确认日没有对应的真实净值记录")
    if purchase_date not in candidate_points:
        reasons.append("候选基金申购确认日没有对应的真实净值记录")

    def corporate_actions(
        dataset: dict[str, Any] | None,
        start: str,
        end: str | None,
    ) -> list[dict[str, Any]]:
        if not end:
            return []
        dividends = [
            {"type": "dividend", **item}
            for item in ((dataset or {}).get("dividends") or [])
            if start <= str(item.get("ex_dividend_date") or "") <= end
        ]
        splits = [
            {"type": "split", **item}
            for item in ((dataset or {}).get("splits") or [])
            if start <= str(item.get("date") or "") <= end
        ]
        return dividends + splits

    source_actions = corporate_actions(source_distributions, sale_date, as_of)
    candidate_actions = corporate_actions(candidate_distributions, purchase_date, as_of)
    if source_actions or candidate_actions:
        reasons.append("比较区间存在未进入交易账本的分红或拆分，单位净值不能直接用于完整收益归因")

    source_trade_nav = _number(redemption.get("confirmed_unit_price"))
    candidate_trade_nav = _number(purchase.get("confirmed_unit_price"))
    source_provider_trade_nav = _number((source_points.get(sale_date) or {}).get("unit_nav"))
    candidate_provider_trade_nav = _number((candidate_points.get(purchase_date) or {}).get("unit_nav"))
    source_match = bool(
        source_trade_nav is not None
        and source_provider_trade_nav is not None
        and abs(source_trade_nav - source_provider_trade_nav)
        <= max(0.0001, abs(source_provider_trade_nav) * 0.002)
    )
    candidate_match = bool(
        candidate_trade_nav is not None
        and candidate_provider_trade_nav is not None
        and abs(candidate_trade_nav - candidate_provider_trade_nav)
        <= max(0.0001, abs(candidate_provider_trade_nav) * 0.002)
    )
    if not source_match:
        reasons.append("赎回流水确认净值与数据源同日净值不一致")
    if not candidate_match:
        reasons.append("申购流水确认净值与数据源同日净值不一致")

    status = "available" if not reasons else "unavailable"
    metrics: dict[str, Any] = {}
    if status == "available" and as_of:
        source_nav = _number(source_points[as_of].get("unit_nav")) or 0
        candidate_nav = _number(candidate_points[as_of].get("unit_nav")) or 0
        redeemed_shares = _number(redemption.get("shares")) or 0
        purchased_shares = _number(purchase.get("shares")) or 0
        residual_cash = _number(purchase.get("residual_cash_yuan")) or 0
        redemption_gross = _number(redemption.get("actual_gross_yuan")) or 0
        redemption_received = _number(redemption.get("actual_received_yuan")) or 0
        candidate_value = purchased_shares * candidate_nav
        actual_path_value = candidate_value + residual_cash
        no_switch_value = redeemed_shares * source_nav
        incremental = actual_path_value - no_switch_value
        metrics = {
            "as_of": as_of,
            "source_unit_nav": _round(source_nav, 8),
            "candidate_unit_nav": _round(candidate_nav, 8),
            "candidate_lot_value_yuan": _round(candidate_value),
            "residual_cash_yuan": _round(residual_cash),
            "actual_switch_path_value_yuan": _round(actual_path_value),
            "no_switch_counterfactual_value_yuan": _round(no_switch_value),
            "incremental_value_vs_hold_yuan": _round(incremental),
            "incremental_return_vs_hold_pct": _round(
                incremental / redemption_gross * 100 if redemption_gross else None,
                4,
            ),
            "post_settlement_profit_yuan": _round(actual_path_value - redemption_received),
            "end_to_end_profit_yuan": _round(actual_path_value - redemption_gross),
            "total_switch_fees_yuan": _round(
                (_number(redemption.get("actual_fee_yuan")) or 0)
                + (_number(purchase.get("actual_fee_yuan")) or 0)
            ),
        }

    return _with_evidence({
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "event_type": "attribution_snapshot",
        "status": "completed_attribution_available"
        if status == "available" else "completed_attribution_blocked",
        "case_id": case_id,
        "holding_id": int(holding_id),
        "selected_code": selected_code,
        "candidate_code": candidate_code,
        "candidate_name": candidate_name,
        "generated_at": generated_at,
        "bindings": bindings,
        "attribution": {
            "status": status,
            "metrics": metrics,
            "reasons": reasons,
            "source_errors": source_errors,
            "source_trade_nav_verified": source_match,
            "candidate_trade_nav_verified": candidate_match,
            "source_corporate_actions": source_actions,
            "candidate_corporate_actions": candidate_actions,
        },
        "decision_gate": {
            "historical_attribution_available": status == "available",
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "reason": "real_nav_attribution_available"
            if status == "available" else "real_nav_attribution_blocked",
        },
        "sources": {
            "source_nav": (source_history or {}).get("source"),
            "candidate_nav": (candidate_history or {}).get("source"),
            "source_distributions": (source_distributions or {}).get("source"),
            "candidate_distributions": (candidate_distributions or {}).get("source"),
        },
        "policy": (
            "增量收益只描述已发生替换相对继续持有原基金的历史结果；"
            "它不是未来收益预测，也不支持自动交易。"
        ),
    })
