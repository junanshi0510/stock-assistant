# -*- coding: utf-8 -*-
"""Deterministic pre-purchase review for one immutable fund batch allocation."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


STRATEGY_ID = "portfolio_batch_purchase_preflight"
STRATEGY_VERSION = "1.0.0"
SCHEMA_VERSION = f"{STRATEGY_ID}.v1"
QUOTE_VALID_HOURS = 24
MAX_FUTURE_SKEW_MINUTES = 5
MAX_CONFIRMATION_DAYS = 30
MONEY_TOLERANCE = 0.02


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: Any) -> float | None:
    number = _number(value)
    return round(number, 2) if number is not None and number >= 0 else None


def _datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo is not None else None


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _gate(code: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "pass" if passed else "block",
        "detail": detail,
    }


def _holding_amounts(holdings: list[dict[str, Any]]) -> tuple[float | None, bool]:
    if not holdings:
        return None, False
    total = 0.0
    complete = True
    for item in holdings:
        amount = _number(item.get("amount"))
        if amount is None or amount < 0:
            complete = False
            continue
        total += amount
    return (total if total > 0 else None), complete


def evaluate_portfolio_batch_purchase_preflight(
    allocation: dict[str, Any],
    quotes: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    market_profiles: dict[str, dict[str, Any]],
    projected_holdings: list[dict[str, Any]],
    projected_exposure: dict[str, Any],
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Review all proposed purchases together without authorizing an order."""
    generated = _datetime(generated_at)
    if generated is None:
        raise ValueError("执行前复核时间必须包含时区")
    generated_utc = generated.astimezone(dt.timezone.utc)

    allocation_items = [
        item
        for item in ((allocation.get("allocation") or {}).get("items") or [])
        if (_number(item.get("allocated_amount_yuan")) or 0) > MONEY_TOLERANCE
    ]
    allocation_by_code = {
        str(item.get("code") or ""): item for item in allocation_items
    }
    expected_codes = set(allocation_by_code)
    quote_codes = [str(item.get("code") or "") for item in quotes]
    duplicate_codes = sorted({code for code in quote_codes if quote_codes.count(code) > 1})
    unexpected_codes = sorted(set(quote_codes) - expected_codes)
    missing_codes = sorted(expected_codes - set(quote_codes))
    coverage_ready = bool(
        expected_codes
        and not duplicate_codes
        and not unexpected_codes
        and not missing_codes
    )

    profile_ready = bool(
        profile.get("configured")
        and profile.get("integrity_verified")
        and not profile.get("review_required")
        and (profile.get("governance_integrity") or {}).get("verified")
    )
    allowed_markets = set(profile.get("allowed_fund_markets") or []) if profile_ready else set()
    allocation_binding_ready = bool(
        bindings.get("allocation_integrity_verified")
        and bindings.get("holdings_binding_current")
        and bindings.get("profile_binding_current")
    )

    rows = []
    earliest_expiry: dt.datetime | None = None
    total_order = 0.0
    total_fee = 0.0
    quote_facts_complete = coverage_ready
    all_fresh = coverage_ready
    all_available = coverage_ready
    all_amounts = coverage_ready
    all_markets = coverage_ready

    for code in sorted(expected_codes):
        allocation_item = allocation_by_code[code]
        matches = [item for item in quotes if str(item.get("code") or "") == code]
        quote = matches[0] if len(matches) == 1 else {}
        allocated = _number(allocation_item.get("allocated_amount_yuan")) or 0.0
        order = _number(quote.get("order_amount_yuan"))
        fee = _number(quote.get("entry_fee_yuan"))
        limit = _number(quote.get("purchase_limit_yuan"))
        status = str(quote.get("purchase_status") or "unknown")
        currency = str(quote.get("currency") or "CNY").upper()
        quoted_at = _datetime(quote.get("quoted_at"))
        confirmation_date = _date(quote.get("expected_confirmation_date"))
        expires_at = (
            quoted_at.astimezone(dt.timezone.utc) + dt.timedelta(hours=QUOTE_VALID_HOURS)
            if quoted_at is not None else None
        )
        if expires_at is not None and (
            earliest_expiry is None or expires_at < earliest_expiry
        ):
            earliest_expiry = expires_at
        fresh = bool(
            quoted_at is not None
            and quoted_at.astimezone(dt.timezone.utc)
            <= generated_utc + dt.timedelta(minutes=MAX_FUTURE_SKEW_MINUTES)
            and expires_at is not None
            and expires_at >= generated_utc
        )
        fee_ready = bool(
            order is not None and order > 0
            and fee is not None and 0 <= fee < order
        )
        net = order - fee if fee_ready and order is not None and fee is not None else None
        amount_ready = bool(
            order is not None
            and order > 0
            and order <= allocated + MONEY_TOLERANCE
            and currency == "CNY"
        )
        availability_ready = bool(
            status == "available"
            or (
                status == "limited"
                and limit is not None
                and limit > 0
                and order is not None
                and order <= limit + MONEY_TOLERANCE
            )
        )
        confirmation_ready = bool(
            quoted_at is not None
            and confirmation_date is not None
            and quoted_at.date() <= confirmation_date
            <= quoted_at.date() + dt.timedelta(days=MAX_CONFIRMATION_DAYS)
        )
        platform_ready = bool(
            2 <= len(str(quote.get("platform_name") or "").strip()) <= 80
            and quote.get("acknowledged_platform_quote") is True
        )

        market_profile = market_profiles.get(code) or {}
        market = market_profile.get("market") or {}
        required_markets = set(market.get("required_permissions") or [])
        market_identified = bool(
            market_profile.get("resolution_status") == "identified"
            and required_markets
        )
        permission_ready = bool(
            market_identified
            and profile_ready
            and required_markets.issubset(allowed_markets)
        )
        fx_ready = bool(
            profile_ready
            and (not market.get("currency_risk") or profile.get("accept_fx_risk"))
        )
        market_ready = bool(market_identified and permission_ready and fx_ready)
        row_ready = bool(
            fresh
            and fee_ready
            and amount_ready
            and availability_ready
            and confirmation_ready
            and platform_ready
            and market_ready
        )
        reasons = []
        if not platform_ready:
            reasons.append("缺少销售平台本次申购页确认")
        if not fresh:
            reasons.append("平台报价缺少时区、已过期或晚于服务器时间")
        if not fee_ready:
            reasons.append("平台实际申购费缺失或金额无效")
        if not amount_ready:
            reasons.append("拟申购金额超过组合分配、不是人民币或金额无效")
        if not availability_ready:
            reasons.append("当前不可申购，或限购额度不足/缺失")
        if not confirmation_ready:
            reasons.append("平台预计确认日期缺失或超出 30 天")
        if not market_ready:
            reasons.append("基金市场、投资权限或汇率风险门禁未通过")
        if order is not None and order > 0:
            total_order += order
        if fee is not None and fee >= 0:
            total_fee += fee
        quote_facts_complete = bool(
            quote_facts_complete and platform_ready and fee_ready and confirmation_ready
        )
        all_fresh = bool(all_fresh and fresh)
        all_available = bool(all_available and availability_ready)
        all_amounts = bool(all_amounts and amount_ready)
        all_markets = bool(all_markets and market_ready)
        rows.append({
            "code": code,
            "name": allocation_item.get("name") or code,
            "allocated_amount_yuan": _money(allocated),
            "platform_name": str(quote.get("platform_name") or "").strip() or None,
            "quoted_at": quoted_at.isoformat(timespec="seconds") if quoted_at else None,
            "quote_expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None,
            "currency": currency,
            "purchase_status": status,
            "purchase_limit_yuan": _money(limit),
            "expected_confirmation_date": confirmation_date.isoformat() if confirmation_date else None,
            "order_amount_yuan": _money(order),
            "entry_fee_yuan": _money(fee),
            "net_asset_amount_yuan": _money(net),
            "fee_rate_pct": (
                round(fee / order * 100, 6)
                if fee_ready and order is not None and fee is not None else None
            ),
            "market": {
                "primary": market.get("primary"),
                "label": market.get("label"),
                "required_permissions": sorted(required_markets),
                "currency_risk": bool(market.get("currency_risk")),
                "fx_risk_acknowledged": bool(profile.get("accept_fx_risk")),
                "source": market_profile.get("source"),
                "source_url": market_profile.get("source_url"),
            },
            "ready": row_ready,
            "reasons": reasons,
        })

    allocated_total = _number((allocation.get("budget") or {}).get("allocated_total_yuan"))
    requested_total = _number((allocation.get("budget") or {}).get("requested_total_yuan"))
    cash_ready = bool(
        allocated_total is not None
        and allocated_total > 0
        and total_order > 0
        and total_order <= allocated_total + MONEY_TOLERANCE
        and requested_total is not None
        and total_order <= requested_total + MONEY_TOLERANCE
        and all_amounts
    )

    projected_total, projected_amounts_complete = _holding_amounts(projected_holdings)
    max_single = _number(profile.get("max_single_ratio")) if profile_ready else None
    position_rows = []
    single_ready = bool(projected_amounts_complete and projected_total and max_single is not None)
    for code in sorted(expected_codes):
        amount = sum(
            _number(item.get("amount")) or 0
            for item in projected_holdings
            if item.get("asset_type") == "fund"
            and str(item.get("code") or "") == code
        )
        ratio = amount / projected_total * 100 if projected_total else None
        within = bool(ratio is not None and max_single is not None and ratio <= max_single + 1e-8)
        single_ready = bool(single_ready and within)
        position_rows.append({
            "code": code,
            "amount_after_purchase_yuan": _money(amount),
            "ratio_after_purchase_pct": round(ratio, 6) if ratio is not None else None,
            "max_single_ratio_pct": round(max_single, 6) if max_single is not None else None,
            "within_limit": within,
        })

    quality = projected_exposure.get("quality") or {}
    summary = projected_exposure.get("summary") or {}
    exposure_ready = bool(
        quality.get("decision_eligible")
        and projected_exposure.get("holdings_sha256")
        == bindings.get("projected_holdings_sha256")
        and projected_exposure.get("profile_version_id")
        == profile.get("profile_version_id")
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
            "allocation_snapshot",
            "组合分配快照",
            allocation.get("status") == "ready" and allocation_binding_ready,
            "分配快照完整，且当前持仓与 IPS 仍匹配"
            if allocation_binding_ready else "分配快照、当前持仓或 IPS 绑定已变化",
        ),
        _gate(
            "quote_coverage",
            "逐只平台报价",
            coverage_ready,
            f"覆盖 {len(expected_codes)} 只已分配基金"
            if coverage_ready else (
                f"缺少 {','.join(missing_codes) or '-'}；重复 {','.join(duplicate_codes) or '-'}；"
                f"额外 {','.join(unexpected_codes) or '-'}"
            ),
        ),
        _gate(
            "quote_facts",
            "平台事实完整",
            quote_facts_complete,
            "每只基金均有真实费用、预计确认日和平台确认"
            if quote_facts_complete else "至少一只基金缺少真实费用、预计确认日或平台确认",
        ),
        _gate(
            "quote_freshness",
            "报价有效期",
            all_fresh,
            "全部报价仍在 24 小时有效期内"
            if all_fresh else "至少一项报价过期、缺少时区或时间异常",
        ),
        _gate(
            "purchase_availability",
            "可购与限购",
            all_available,
            "全部基金当前可购且拟申购金额不超过平台限额"
            if all_available else "至少一只基金暂停申购或限购额度不足/缺失",
        ),
        _gate(
            "one_cash_budget",
            "唯一现金预算",
            cash_ready,
            f"拟申购 {_money(total_order)} 元，不超过已分配 {_money(allocated_total)} 元"
            if cash_ready else "拟申购金额超过批次分配、存在重复预算或金额不完整",
        ),
        _gate(
            "investment_policy",
            "当前投资政策",
            profile_ready,
            f"已绑定 {profile.get('profile_version_id')}"
            if profile_ready else "IPS 未激活、待复核或治理完整性失败",
        ),
        _gate(
            "market_and_fx",
            "市场与汇率权限",
            all_markets,
            "全部基金市场已识别并通过用户权限与汇率风险确认"
            if all_markets else "至少一只基金的市场、投资权限或汇率风险未通过",
        ),
        _gate(
            "single_position_limit",
            "单品仓位上限",
            single_ready,
            "全部拟申购基金在组合投影中均低于单品上限"
            if single_ready else "至少一只基金申购后超过单品上限，或持仓金额不完整",
        ),
        _gate(
            "projected_exposure",
            "申购后组合穿透",
            exposure_ready,
            "当前组合与全部拟申购金额已用真实定期披露联合穿透"
            if exposure_ready else "基金披露缺失、过期、冲突或投影绑定失败",
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
    blockers = [gate["detail"] for gate in gates if gate["status"] == "block"]
    ready = not blockers
    status = "ready_for_manual_purchase_review" if ready else "purchase_preflight_blocked"
    net_total = max(0.0, total_order - total_fee)
    allocation_residual = (
        max(0.0, allocated_total - total_order)
        if allocated_total is not None else None
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": generated_utc.isoformat(timespec="seconds"),
        "status": status,
        "bindings": bindings,
        "quote_expires_at": (
            earliest_expiry.isoformat(timespec="seconds")
            if earliest_expiry is not None else None
        ),
        "cashflow": {
            "batch_requested_budget_yuan": _money(requested_total),
            "batch_allocated_budget_yuan": _money(allocated_total),
            "proposed_order_total_yuan": _money(total_order),
            "confirmed_entry_fee_total_yuan": _money(total_fee),
            "projected_net_asset_total_yuan": _money(net_total),
            "allocated_cash_retained_yuan": _money(allocation_residual),
            "currency": "CNY",
        },
        "quotes": rows,
        "position_projection": {
            "invested_total_after_purchase_yuan": _money(projected_total),
            "max_single_ratio_pct": _money(max_single),
            "items": position_rows,
        },
        "portfolio_projection": {
            "status": projected_exposure.get("status"),
            "evaluated_on": projected_exposure.get("evaluated_on"),
            "equity_upper_ratio_pct": _money(equity_upper),
            "max_equity_ratio_pct": _money(max_equity),
            "industry_max_upper_ratio_pct": _money(industry_upper),
            "max_industry_ratio_pct": _money(max_industry),
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
        "gates": gates,
        "blockers": blockers,
        "decision_gate": {
            "manual_purchase_review_ready": ready,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "order_submitted": False,
            "reason": "all_batch_purchase_gates_passed"
            if ready else next(
                (gate["code"] for gate in gates if gate["status"] == "block"),
                "unknown_blocker",
            ),
        },
        "sources": {
            "platform_quotes": "用户逐只从销售平台本次申购确认页录入",
            "allocation": "不可变组合资金分配快照",
            "investment_policy": "用户当前激活的版本化投资政策",
            "market_profiles": "东方财富基金代码搜索库 + 东方财富基金详情页",
            "portfolio_exposure": projected_exposure.get("source"),
        },
        "policy": (
            "该复核只证明当前平台报价和真实披露下的批量申购可以进入人工确认；"
            "它不预测收益，不保证盈利，不读取销售账户余额，也不授权或提交任何订单。"
            "报价、持仓或 IPS 变化后必须重新复核，最终份额、净值和费用以实际成交为准。"
        ),
    }
