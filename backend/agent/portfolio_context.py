# -*- coding: utf-8 -*-
"""Read a minimal, user-confirmed portfolio snapshot for Agent decisions."""

from __future__ import annotations

from typing import Any

import storage


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_portfolio_context(payload: dict[str, Any]) -> dict[str, Any]:
    code = str(payload.get("code") or "").strip()
    items = storage.list_holdings()
    profile = storage.get_investment_profile()
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

    gaps = []
    if not profile.get("configured"):
        gaps.append("investment_profile_not_configured")
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
            "updated_at": profile.get("updated_at"),
        },
        "portfolio": {
            "holding_count": len(items),
            "amount_complete": amount_complete,
            "total_amount": round(total_amount, 2) if total_amount > 0 else None,
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
            "amounts": "only_user_confirmed_holding_amounts",
            "target_match": "asset_type_fund_and_exact_six_digit_code",
        },
        "policy": "组合上下文只读取用户已确认持仓和已保存约束，不推断现金、负债或未导入资产。",
    }
