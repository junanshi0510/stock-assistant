# -*- coding: utf-8 -*-
"""Read a minimal, user-confirmed portfolio snapshot for Agent decisions."""

from __future__ import annotations

from typing import Any

import storage
import fund_switch_execution_service
import fund_switch_quote_service
from portfolio_exposure import holdings_sha256


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_portfolio_context(payload: dict[str, Any]) -> dict[str, Any]:
    code = str(payload.get("code") or "").strip()
    user_id = str(payload.get("user_id") or "default")
    items = storage.list_holdings(user_id=user_id)
    requested_profile_version_id = str(payload.get("profile_version_id") or "").strip() or None
    if requested_profile_version_id:
        profile = storage.get_investment_profile_version(
            requested_profile_version_id,
            user_id=user_id,
        )
        profile_version_available = profile is not None
        if profile is None:
            profile = storage.get_investment_profile(user_id=user_id)
            profile = {**profile, "configured": False}
    else:
        profile = storage.get_investment_profile(user_id=user_id)
        profile_version_available = True
    profile_configured = bool(profile.get("configured"))
    amounts = [_number(item.get("amount")) for item in items]
    amount_complete = all(value is not None and value >= 0 for value in amounts)
    total_amount = sum(value or 0 for value in amounts)
    target = next(
        (
            item for item in items
            if item.get("asset_type") == "fund" and str(item.get("code") or "") == code
        ),
        None,
    )
    target_amount = _number((target or {}).get("amount")) or 0
    target_ratio = target_amount / total_amount * 100 if total_amount > 0 else None
    as_of_values = [str(item.get("updated_at") or "") for item in items if item.get("updated_at")]
    if profile.get("updated_at"):
        as_of_values.append(str(profile["updated_at"]))
    switch_quotes = fund_switch_quote_service.agent_quote_summary(
        user_id,
        target_code=code,
    )
    switch_execution_reviews = fund_switch_execution_service.agent_execution_summary(
        user_id,
        target_code=code,
    )

    gaps = []
    if not profile.get("configured"):
        gaps.append("investment_profile_not_configured")
    if requested_profile_version_id and not profile_version_available:
        gaps.append("pinned_profile_version_unavailable")
    if not items:
        gaps.append("holdings_not_imported")
    elif not amount_complete:
        gaps.append("holding_amount_incomplete")

    return {
        "status": "available",
        "source": "用户已确认持仓与投资约束",
        "as_of": max(as_of_values) if as_of_values else None,
        "data_classification": "private_financial",
        "profile": {
            "configured": profile_configured,
            "risk": profile.get("risk") if profile_configured else None,
            "horizon": profile.get("horizon") if profile_configured else None,
            "monthly_budget": _number(profile.get("monthly_budget")) if profile_configured else None,
            "max_single_ratio": _number(profile.get("max_single_ratio")) if profile_configured else None,
            "allowed_fund_markets": (
                list(profile.get("allowed_fund_markets") or [])
                if profile_configured else []
            ),
            "accept_fx_risk": bool(profile.get("accept_fx_risk")) if profile_configured else False,
            "max_equity_ratio": _number(profile.get("max_equity_ratio")) if profile_configured else None,
            "max_industry_ratio": _number(profile.get("max_industry_ratio")) if profile_configured else None,
            "max_drawdown_pct": _number(profile.get("max_drawdown_pct")) if profile_configured else None,
            "liquidity_reserve_months": _number(profile.get("liquidity_reserve_months")) if profile_configured else None,
            "experience_level": profile.get("experience_level") if profile_configured else None,
            "primary_objective": profile.get("primary_objective") if profile_configured else None,
            "profile_version_id": profile.get("profile_version_id") if profile_configured else requested_profile_version_id,
            "profile_version_no": profile.get("version_no") if profile_configured else None,
            "profile_payload_sha256": profile.get("payload_sha256") if profile_configured else None,
            "activated_at": profile.get("activated_at") if profile_configured else None,
            "review_due_at": profile.get("review_due_at") if profile_configured else None,
            "consent_version": profile.get("consent_version") if profile_configured else None,
            "updated_at": profile.get("updated_at"),
        },
        "portfolio": {
            "holding_count": len(items),
            "amount_complete": amount_complete,
            "total_amount": round(total_amount, 2) if total_amount > 0 else None,
            "holdings_sha256": holdings_sha256(items),
        },
        "target_holding": {
            "exists": target is not None,
            "code": code,
            "name": (target or {}).get("name"),
            "amount": round(target_amount, 2) if target is not None else 0,
            "ratio": round(target_ratio, 2) if target_ratio is not None else None,
            "profit": _number((target or {}).get("profit")),
            "profit_rate": _number((target or {}).get("profit_rate")),
            "updated_at": (target or {}).get("updated_at"),
        },
        "fund_switch_quotes": switch_quotes,
        "fund_switch_execution_reviews": switch_execution_reviews,
        "holdings": [
            {
                "asset_type": item.get("asset_type"),
                "market": item.get("market"),
                "code": item.get("code"),
                "name": item.get("name"),
                "amount": _number(item.get("amount")),
                "profit": _number(item.get("profit")),
                "profit_rate": _number(item.get("profit_rate")),
                "source": item.get("source"),
                "updated_at": item.get("updated_at"),
            }
            for item in items
        ],
        "data_gaps": gaps,
        "method": {
            "scope": "single_user_migration_storage",
            "profile_binding": "exact_version_id_from_agent_run" if requested_profile_version_id else "active_version_at_tool_call",
            "amounts": "only_user_confirmed_holding_amounts",
            "target_match": "asset_type_fund_and_exact_six_digit_code",
            "switch_quote_scope": "latest_user_confirmed_quote_per_candidate_with_audit_status",
            "switch_execution_scope": "latest_immutable_pretrade_review_with_current_binding_status",
        },
        "policy": "组合上下文只读取用户已确认持仓和已保存约束，不推断现金、负债或未导入资产。",
    }
