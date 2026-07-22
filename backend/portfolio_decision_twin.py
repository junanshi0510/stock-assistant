# -*- coding: utf-8 -*-
"""Deterministic portfolio what-if, reverse-stress and repair engine.

The model is deliberately first-order. It applies explicit market shocks and
industry overlays to user-confirmed amounts and disclosure-derived exposure
intervals. Missing disclosure widens the interval; it is never imputed as a
precise holding, beta or correlation.
"""

from __future__ import annotations

import copy
import math
from typing import Any

import portfolio_exposure


SCHEMA_VERSION = "portfolio_decision_twin.v1"
METHOD_VERSION = "first_order_exposure_interval.v1"
SUPPORTED_MARKETS = (
    "mainland",
    "hong_kong",
    "united_states",
    "global",
    "unknown",
)
MARKET_LABELS = {
    "mainland": "A股",
    "hong_kong": "港股",
    "united_states": "美股",
    "global": "全球",
    "unknown": "未识别权益",
}
MARKET_ALIASES = {
    "a股": "mainland",
    "A股": "mainland",
    "mainland": "mainland",
    "cn": "mainland",
    "港股": "hong_kong",
    "hong_kong": "hong_kong",
    "hk": "hong_kong",
    "美股": "united_states",
    "united_states": "united_states",
    "us": "united_states",
    "全球": "global",
    "global": "global",
    "unknown": "unknown",
    "未知": "unknown",
}
CASH_ASSET_TYPES = {"cash", "currency", "现金", "货币"}


_PRESETS = (
    {
        "id": "global_risk_off",
        "name": "全球风险偏好收缩",
        "description": "检验三地权益同步下跌时，组合亏损预算和披露盲区的承压能力。",
        "market_shocks": [
            {"market": "mainland", "shock_pct": -10},
            {"market": "hong_kong", "shock_pct": -15},
            {"market": "united_states", "shock_pct": -12},
            {"market": "global", "shock_pct": -12},
            {"market": "unknown", "shock_pct": -18},
        ],
        "industry_shocks": [],
        "loss_budget_pct": 15,
    },
    {
        "id": "china_equity_selloff",
        "name": "中国权益集中回撤",
        "description": "检验 A/H 股同时承压、海外市场轻度波动时的地域集中风险。",
        "market_shocks": [
            {"market": "mainland", "shock_pct": -18},
            {"market": "hong_kong", "shock_pct": -15},
            {"market": "united_states", "shock_pct": -4},
            {"market": "global", "shock_pct": -8},
            {"market": "unknown", "shock_pct": -20},
        ],
        "industry_shocks": [],
        "loss_budget_pct": 15,
    },
    {
        "id": "us_growth_repricing",
        "name": "美股成长估值重定价",
        "description": "检验美股大幅调整并向港股、全球资产传导的组合影响。",
        "market_shocks": [
            {"market": "mainland", "shock_pct": -5},
            {"market": "hong_kong", "shock_pct": -10},
            {"market": "united_states", "shock_pct": -22},
            {"market": "global", "shock_pct": -14},
            {"market": "unknown", "shock_pct": -22},
        ],
        "industry_shocks": [],
        "loss_budget_pct": 15,
    },
    {
        "id": "disclosure_blind_spot",
        "name": "披露盲区审计",
        "description": "已识别市场温和波动，但未分类权益受到更大冲击，用于衡量数据不确定性成本。",
        "market_shocks": [
            {"market": "mainland", "shock_pct": -6},
            {"market": "hong_kong", "shock_pct": -8},
            {"market": "united_states", "shock_pct": -8},
            {"market": "global", "shock_pct": -8},
            {"market": "unknown", "shock_pct": -30},
        ],
        "industry_shocks": [],
        "loss_budget_pct": 12,
    },
)


def scenario_presets() -> list[dict[str, Any]]:
    result = copy.deepcopy(list(_PRESETS))
    for item in result:
        item["assumption_type"] = "illustrative_user_editable"
        item["historical_calibration"] = False
    return result


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _canonical_market(value: Any) -> str:
    raw = str(value or "").strip()
    return MARKET_ALIASES.get(raw, MARKET_ALIASES.get(raw.lower(), "unknown"))


def _holding_id(value: Any) -> str:
    return str(value).strip()


def _is_cash(holding: dict[str, Any]) -> bool:
    return str(holding.get("asset_type") or "").strip().lower() in CASH_ASSET_TYPES


def normalize_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("情景参数必须是对象")
    name = str(payload.get("name") or "自定义组合压力情景").strip()[:80]
    if not name:
        raise ValueError("情景名称不能为空")

    market_shocks = {market: 0.0 for market in SUPPORTED_MARKETS}
    rows = payload.get("market_shocks") or []
    if isinstance(rows, dict):
        rows = [{"market": key, "shock_pct": value} for key, value in rows.items()]
    seen_markets: set[str] = set()
    for row in rows:
        market = _canonical_market((row or {}).get("market"))
        if market in seen_markets:
            raise ValueError(f"市场冲击重复：{MARKET_LABELS[market]}")
        shock = _number((row or {}).get("shock_pct"))
        if shock is None or shock < -80 or shock > 50:
            raise ValueError("市场冲击必须在 -80% 至 50% 之间")
        seen_markets.add(market)
        market_shocks[market] = round(shock, 4)
    if "unknown" not in seen_markets:
        market_shocks["unknown"] = min(market_shocks.values())

    industries: dict[str, float] = {}
    for row in payload.get("industry_shocks") or []:
        industry = str((row or {}).get("industry") or "").strip()[:80]
        shock = _number((row or {}).get("shock_pct"))
        if not industry:
            raise ValueError("行业冲击必须提供行业名称")
        if industry in industries:
            raise ValueError(f"行业冲击重复：{industry}")
        if shock is None or shock < -50 or shock > 50:
            raise ValueError("行业叠加冲击必须在 -50% 至 50% 之间")
        industries[industry] = round(shock, 4)
    if len(industries) > 12:
        raise ValueError("单次情景最多设置 12 个行业叠加冲击")

    overrides: dict[str, float] = {}
    for row in payload.get("position_shocks") or []:
        holding_id = _holding_id((row or {}).get("holding_id"))
        shock = _number((row or {}).get("shock_pct"))
        if not holding_id:
            raise ValueError("个券冲击缺少持仓 ID")
        if holding_id in overrides:
            raise ValueError(f"持仓 {holding_id} 的个券冲击重复")
        if shock is None or shock < -95 or shock > 100:
            raise ValueError("个券总冲击必须在 -95% 至 100% 之间")
        overrides[holding_id] = round(shock, 4)
    if len(overrides) > 30:
        raise ValueError("单次情景最多覆盖 30 项持仓")

    targets: dict[str, float] = {}
    for row in payload.get("hypothetical_positions") or []:
        holding_id = _holding_id((row or {}).get("holding_id"))
        amount = _number((row or {}).get("target_amount"))
        if not holding_id:
            raise ValueError("假设调仓缺少持仓 ID")
        if holding_id in targets:
            raise ValueError(f"持仓 {holding_id} 的目标金额重复")
        if amount is None or amount < 0 or amount > 1_000_000_000:
            raise ValueError("目标金额必须在 0 至 10 亿元之间")
        targets[holding_id] = round(amount, 2)
    if len(targets) > 100:
        raise ValueError("单次情景最多调整 100 项持仓")

    budget = _number(payload.get("loss_budget_pct"))
    if budget is None or budget < 1 or budget > 50:
        raise ValueError("亏损预算必须在 1% 至 50% 之间")
    minimum_trade = _number(payload.get("minimum_trade_amount"), 0.0) or 0.0
    if minimum_trade < 0 or minimum_trade > 10_000_000:
        raise ValueError("最小调整金额必须在 0 至 1000 万之间")
    if not any(abs(value) > 1e-9 for value in market_shocks.values()) and not industries and not overrides:
        raise ValueError("至少需要设置一个非零压力冲击")

    return {
        "schema_version": "portfolio_stress_scenario.v1",
        "name": name,
        "preset_id": str(payload.get("preset_id") or "custom")[:80],
        "market_shocks": [
            {
                "market": market,
                "label": MARKET_LABELS[market],
                "shock_pct": market_shocks[market],
            }
            for market in SUPPORTED_MARKETS
        ],
        "industry_shocks": [
            {"industry": name, "shock_pct": shock}
            for name, shock in sorted(industries.items())
        ],
        "position_shocks": [
            {"holding_id": holding_id, "shock_pct": shock}
            for holding_id, shock in sorted(overrides.items())
        ],
        "hypothetical_positions": [
            {"holding_id": holding_id, "target_amount": amount}
            for holding_id, amount in sorted(targets.items())
        ],
        "loss_budget_pct": round(budget, 4),
        "minimum_trade_amount": round(minimum_trade, 2),
        "assumption_type": "illustrative_user_editable",
        "historical_calibration": False,
    }


def _scenario_maps(scenario: dict[str, Any]) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    markets = {
        str(item["market"]): float(item["shock_pct"])
        for item in scenario.get("market_shocks") or []
    }
    industries = {
        str(item["industry"]): float(item["shock_pct"])
        for item in scenario.get("industry_shocks") or []
    }
    overrides = {
        _holding_id(item["holding_id"]): float(item["shock_pct"])
        for item in scenario.get("position_shocks") or []
    }
    return markets, industries, overrides


def _allocate_contributors(
    holdings: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    dimension_key: str,
) -> dict[str, dict[str, float]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for holding in holdings:
        if _is_cash(holding):
            continue
        amount = _number(holding.get("amount"), 0.0) or 0.0
        if amount <= 0:
            continue
        by_code.setdefault(str(holding.get("code") or "").strip(), []).append(holding)
    result: dict[str, dict[str, float]] = {}
    for row in rows or []:
        dimension = str(row.get(dimension_key) or "unknown")
        for contributor in row.get("contributors") or []:
            code = str(contributor.get("code") or "").strip()
            amount = max(0.0, _number(contributor.get("amount"), 0.0) or 0.0)
            matches = by_code.get(code) or []
            denominator = sum(max(0.0, _number(item.get("amount"), 0.0) or 0.0) for item in matches)
            if not matches or denominator <= 0 or amount <= 0:
                continue
            for holding in matches:
                holding_amount = max(0.0, _number(holding.get("amount"), 0.0) or 0.0)
                allocated = amount * holding_amount / denominator
                holding_id = _holding_id(holding.get("id"))
                bucket = result.setdefault(holding_id, {})
                bucket[dimension] = bucket.get(dimension, 0.0) + allocated
    return result


def _build_position_models(
    holdings: list[dict[str, Any]], exposure: dict[str, Any]
) -> tuple[list[dict[str, Any]], float, list[str]]:
    warnings: list[str] = []
    valid = []
    for index, raw in enumerate(holdings):
        item = dict(raw)
        if item.get("id") is None:
            item["id"] = f"row_{index + 1}"
        amount = _number(item.get("amount"))
        if amount is None or amount < 0:
            warnings.append(f"{item.get('name') or item.get('code') or item['id']} 缺少有效金额，未进入金额计算。")
            continue
        if amount == 0:
            continue
        item["amount"] = amount
        valid.append(item)
    total = sum(float(item["amount"]) for item in valid)
    if total <= 0:
        raise ValueError("没有可用于压力测试的用户确认持仓金额")

    market_known = _allocate_contributors(
        valid, exposure.get("markets") or [], dimension_key="market"
    )
    industry_known = _allocate_contributors(
        valid, exposure.get("industries") or [], dimension_key="name"
    )
    fund_rows = {
        str(item.get("code") or "").strip(): item
        for item in exposure.get("funds") or []
    }
    models: list[dict[str, Any]] = []
    cash_amount = 0.0
    for holding in valid:
        amount = float(holding["amount"])
        if _is_cash(holding):
            cash_amount += amount
            continue
        holding_id = _holding_id(holding["id"])
        asset_type = str(holding.get("asset_type") or "").strip().lower()
        code = str(holding.get("code") or "").strip()
        name = str(holding.get("name") or code or holding_id)
        known_markets = dict(market_known.get(holding_id) or {})
        known_industries = dict(industry_known.get(holding_id) or {})

        if asset_type == "stock":
            market = _canonical_market(holding.get("market"))
            known_markets = {} if market == "unknown" else {market: amount}
            equity_lower = equity_upper = amount
            unknown_industry = amount
        elif asset_type == "fund":
            fund = fund_rows.get(code) or {}
            interval = fund.get("equity_interval") or {}
            lower_ratio = _number(interval.get("lower_ratio"), 0.0) or 0.0
            upper_ratio = _number(interval.get("upper_ratio"), 100.0)
            upper_ratio = 100.0 if upper_ratio is None else upper_ratio
            equity_lower = amount * max(0.0, min(100.0, lower_ratio)) / 100
            equity_upper = amount * max(lower_ratio, min(100.0, upper_ratio)) / 100
            declared_unknown = amount * max(
                0.0, _number(fund.get("industry_unknown_ratio"), 100.0) or 0.0
            ) / 100
            unknown_industry = min(
                max(0.0, equity_upper - sum(known_industries.values())),
                declared_unknown,
            )
        else:
            equity_lower = 0.0
            equity_upper = amount
            unknown_industry = amount

        known_market_total = min(equity_upper, sum(max(0.0, value) for value in known_markets.values()))
        if sum(known_markets.values()) > known_market_total + 0.01:
            scale = known_market_total / sum(known_markets.values()) if sum(known_markets.values()) else 0
            known_markets = {key: value * scale for key, value in known_markets.items()}
        unknown_market_lower = max(0.0, equity_lower - known_market_total)
        unknown_market_upper = max(0.0, equity_upper - known_market_total)
        known_industry_total = sum(max(0.0, value) for value in known_industries.values())
        if known_industry_total > equity_upper + 0.01:
            scale = equity_upper / known_industry_total if known_industry_total else 0
            known_industries = {key: value * scale for key, value in known_industries.items()}
            unknown_industry = 0.0

        models.append(
            {
                "holding_id": holding_id,
                "asset_type": asset_type,
                "market": str(holding.get("market") or ""),
                "code": code,
                "name": name,
                "base_amount": amount,
                "equity_lower": equity_lower,
                "equity_upper": equity_upper,
                "known_markets": known_markets,
                "unknown_market_lower": unknown_market_lower,
                "unknown_market_upper": unknown_market_upper,
                "known_industries": known_industries,
                "unknown_industry": max(0.0, unknown_industry),
            }
        )
    models.append(
        {
            "holding_id": "cash_reserve",
            "asset_type": "cash",
            "market": "",
            "code": "CASH",
            "name": "现金储备",
            "base_amount": cash_amount,
            "equity_lower": 0.0,
            "equity_upper": 0.0,
            "known_markets": {},
            "unknown_market_lower": 0.0,
            "unknown_market_upper": 0.0,
            "known_industries": {},
            "unknown_industry": 0.0,
        }
    )
    return models, total, warnings


def _amounts(
    models: list[dict[str, Any]], total: float, targets: dict[str, float]
) -> tuple[dict[str, float], dict[str, float]]:
    current = {
        model["holding_id"]: float(model["base_amount"])
        for model in models
        if model["holding_id"] != "cash_reserve"
    }
    existing_cash = next(
        (float(model["base_amount"]) for model in models if model["holding_id"] == "cash_reserve"),
        0.0,
    )
    current["cash_reserve"] = existing_cash
    proposed = dict(current)
    available_ids = {model["holding_id"] for model in models if model["holding_id"] != "cash_reserve"}
    for holding_id, amount in targets.items():
        if holding_id not in available_ids:
            raise ValueError(f"假设调仓引用了不存在或不可调整的持仓：{holding_id}")
        proposed[holding_id] = amount
    risky_total = sum(value for key, value in proposed.items() if key != "cash_reserve")
    if risky_total > total + 0.01:
        raise ValueError("假设调仓后的非现金资产超过当前组合总金额；本模型不允许隐含杠杆或外部注资")
    proposed["cash_reserve"] = max(0.0, total - risky_total)
    return current, proposed


def _position_stress(
    model: dict[str, Any], amount: float, scenario: dict[str, Any]
) -> dict[str, Any]:
    if amount <= 0 or model["holding_id"] == "cash_reserve":
        return {
            "holding_id": model["holding_id"],
            "asset_type": model["asset_type"],
            "market": model["market"],
            "code": model["code"],
            "name": model["name"],
            "amount": _round(amount),
            "pnl_lower": 0.0,
            "pnl_upper": 0.0,
            "shock_lower_pct": 0.0,
            "shock_upper_pct": 0.0,
            "interval_width": 0.0,
            "driver": "cash" if model["holding_id"] == "cash_reserve" else "zero_amount",
        }
    markets, industries, overrides = _scenario_maps(scenario)
    scale = amount / float(model["base_amount"]) if model["base_amount"] > 0 else 0.0
    override = overrides.get(model["holding_id"])
    if override is not None:
        lower = upper = amount * override / 100
        driver = "position_override"
    else:
        known_market_pnl = sum(
            value * scale * markets.get(market, markets.get("unknown", 0.0)) / 100
            for market, value in model["known_markets"].items()
        )
        unknown_shock = markets.get("unknown", 0.0) / 100
        unknown_values = (
            model["unknown_market_lower"] * scale * unknown_shock,
            model["unknown_market_upper"] * scale * unknown_shock,
        )
        known_industry_pnl = sum(
            value * scale * industries.get(industry, 0.0) / 100
            for industry, value in model["known_industries"].items()
        )
        industry_extremes = [0.0, *[value / 100 for value in industries.values()]]
        unknown_industry_amount = model["unknown_industry"] * scale
        industry_unknown_values = (
            unknown_industry_amount * min(industry_extremes),
            unknown_industry_amount * max(industry_extremes),
        )
        lower = (
            known_market_pnl
            + min(unknown_values)
            + known_industry_pnl
            + min(industry_unknown_values)
        )
        upper = (
            known_market_pnl
            + max(unknown_values)
            + known_industry_pnl
            + max(industry_unknown_values)
        )
        driver = "market_plus_industry_overlay"
    lower = max(-0.95 * amount, min(amount, lower))
    upper = max(-0.95 * amount, min(amount, upper))
    if lower > upper:
        lower, upper = upper, lower
    return {
        "holding_id": model["holding_id"],
        "asset_type": model["asset_type"],
        "market": model["market"],
        "code": model["code"],
        "name": model["name"],
        "amount": _round(amount),
        "pnl_lower": _round(lower),
        "pnl_upper": _round(upper),
        "shock_lower_pct": _round(lower / amount * 100, 4),
        "shock_upper_pct": _round(upper / amount * 100, 4),
        "interval_width": _round(upper - lower),
        "driver": driver,
    }


def _allocation_summary(
    models: list[dict[str, Any]], amounts: dict[str, float], total: float
) -> dict[str, Any]:
    equity_lower = equity_upper = unknown_market = unknown_industry = 0.0
    markets: dict[str, float] = {}
    industries: dict[str, float] = {}
    positions = []
    for model in models:
        amount = amounts.get(model["holding_id"], 0.0)
        if amount <= 0:
            continue
        scale = amount / model["base_amount"] if model["base_amount"] > 0 else 0.0
        equity_lower += model["equity_lower"] * scale
        equity_upper += model["equity_upper"] * scale
        unknown_market += model["unknown_market_upper"] * scale
        unknown_industry += model["unknown_industry"] * scale
        for market, value in model["known_markets"].items():
            markets[market] = markets.get(market, 0.0) + value * scale
        for industry, value in model["known_industries"].items():
            industries[industry] = industries.get(industry, 0.0) + value * scale
        positions.append({"holding_id": model["holding_id"], "amount": amount})
    max_single = max([item["amount"] / total * 100 for item in positions] + [0.0])
    industry_lower = max(industries.values(), default=0.0)
    industry_upper = max(
        [value + unknown_industry for value in industries.values()] + [unknown_industry, 0.0]
    )
    market_rows = [
        {
            "market": market,
            "label": MARKET_LABELS.get(market, market),
            "known_amount": _round(value),
            "known_ratio": _round(value / total * 100 if total else 0, 4),
        }
        for market, value in sorted(markets.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "total_amount": _round(total),
        "cash_amount": _round(amounts.get("cash_reserve", 0.0)),
        "cash_ratio": _round(amounts.get("cash_reserve", 0.0) / total * 100, 4),
        "max_single_ratio": _round(max_single, 4),
        "equity_lower_ratio": _round(equity_lower / total * 100, 4),
        "equity_upper_ratio": _round(equity_upper / total * 100, 4),
        "industry_max_lower_ratio": _round(industry_lower / total * 100, 4),
        "industry_max_upper_ratio": _round(industry_upper / total * 100, 4),
        "unknown_market_ratio": _round(unknown_market / total * 100, 4),
        "unknown_industry_ratio": _round(unknown_industry / total * 100, 4),
        "markets": market_rows,
    }


def _evaluate_portfolio(
    models: list[dict[str, Any]],
    amounts: dict[str, float],
    total: float,
    scenario: dict[str, Any],
    loss_budget_pct: float,
) -> dict[str, Any]:
    rows = [
        _position_stress(model, amounts.get(model["holding_id"], 0.0), scenario)
        for model in models
        if amounts.get(model["holding_id"], 0.0) > 0
    ]
    rows.sort(key=lambda item: (item["pnl_lower"], -item["amount"]))
    lower = sum(float(item["pnl_lower"] or 0) for item in rows)
    upper = sum(float(item["pnl_upper"] or 0) for item in rows)
    budget_amount = total * loss_budget_pct / 100
    worst_loss = max(0.0, -lower)
    return {
        "pnl_interval": {
            "lower_amount": _round(lower),
            "upper_amount": _round(upper),
            "lower_pct": _round(lower / total * 100, 4),
            "upper_pct": _round(upper / total * 100, 4),
            "width_amount": _round(upper - lower),
        },
        "risk_budget": {
            "loss_budget_pct": _round(loss_budget_pct, 4),
            "loss_budget_amount": _round(budget_amount),
            "worst_loss_amount": _round(worst_loss),
            "utilization_pct": _round(worst_loss / budget_amount * 100 if budget_amount else None, 4),
            "breached": lower < -budget_amount - 0.005,
            "remaining_amount": _round(budget_amount - worst_loss),
        },
        "allocation": _allocation_summary(models, amounts, total),
        "positions": rows,
    }


def _policy_gates(portfolio: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    allocation = portfolio["allocation"]
    budget = portfolio["risk_budget"]
    configured = bool(profile.get("configured"))
    limits = {
        "max_single_ratio": _number(profile.get("max_single_ratio")) if configured else None,
        "max_equity_ratio": _number(profile.get("max_equity_ratio")) if configured else None,
        "max_industry_ratio": _number(profile.get("max_industry_ratio")) if configured else None,
    }
    gates = [
        {
            "code": "scenario_loss_budget",
            "label": "情景亏损预算",
            "status": "block" if budget["breached"] else "pass",
            "actual": budget["worst_loss_amount"],
            "limit": budget["loss_budget_amount"],
        }
    ]
    for code, label, actual_key in (
        ("max_single_ratio", "单项持仓上限", "max_single_ratio"),
        ("max_equity_ratio", "权益仓位上限（保守边界）", "equity_upper_ratio"),
        ("max_industry_ratio", "单一行业上限（保守边界）", "industry_max_upper_ratio"),
    ):
        limit = limits[code]
        actual = allocation[actual_key]
        gates.append(
            {
                "code": code,
                "label": label,
                "status": "unavailable" if limit is None else "block" if actual > limit + 1e-6 else "pass",
                "actual": actual,
                "limit": limit,
            }
        )
    return gates


def _scale_scenario(scenario: dict[str, Any], factor: float) -> dict[str, Any]:
    scaled = copy.deepcopy(scenario)
    for group in ("market_shocks", "industry_shocks", "position_shocks"):
        for row in scaled.get(group) or []:
            row["shock_pct"] = round(float(row["shock_pct"]) * factor, 8)
    return scaled


def _reverse_stress(
    models: list[dict[str, Any]],
    amounts: dict[str, float],
    total: float,
    scenario: dict[str, Any],
    loss_budget_pct: float,
) -> dict[str, Any]:
    directional_shocks = [
        float(row["shock_pct"])
        for group in ("market_shocks", "industry_shocks", "position_shocks")
        for row in scenario.get(group) or []
    ]
    if any(shock > 1e-9 for shock in directional_shocks):
        return {
            "status": "unsupported_mixed_direction",
            "reason": "情景包含正向冲击，统一倍增后的组合损益可能非单调；为避免伪精确，本次不求解单一破线倍数。",
        }

    def breached(factor: float) -> tuple[bool, dict[str, Any]]:
        value = _evaluate_portfolio(
            models, amounts, total, _scale_scenario(scenario, factor), loss_budget_pct
        )
        return bool(value["risk_budget"]["breached"]), value

    high = 1.0
    hit, value = breached(high)
    while not hit and high < 16:
        high *= 2
        hit, value = breached(high)
    if not hit:
        return {
            "status": "unreachable_within_model",
            "max_tested_multiplier": high,
            "reason": "该组冲击按同一比例放大至模型上限仍未触碰亏损预算。",
        }
    low = 0.0
    for _ in range(56):
        middle = (low + high) / 2
        if breached(middle)[0]:
            high = middle
        else:
            low = middle
    _, threshold = breached(high)
    return {
        "status": "already_breached" if high <= 1.000001 else "available",
        "breach_multiplier": _round(high, 4),
        "distance_from_current_scenario_pct": _round((high - 1) * 100, 2),
        "threshold_worst_loss_pct": threshold["pnl_interval"]["lower_pct"],
        "scaled_market_shocks": [
            {
                **row,
                "shock_pct": _round(max(-95.0, min(100.0, float(row["shock_pct"]) * high)), 4),
            }
            for row in scenario.get("market_shocks") or []
        ],
        "interpretation": "在当前一阶暴露与冲击比例结构不变的假设下，统一放大倍数首次触碰亏损预算。",
    }


def _repair_plan(
    models: list[dict[str, Any]],
    proposed_amounts: dict[str, float],
    total: float,
    scenario: dict[str, Any],
    loss_budget_pct: float,
    minimum_trade_amount: float,
) -> dict[str, Any]:
    before = _evaluate_portfolio(
        models, proposed_amounts, total, scenario, loss_budget_pct
    )
    if not before["risk_budget"]["breached"]:
        return {
            "status": "not_needed",
            "actions": [],
            "total_shift_to_cash": 0.0,
            "before": before["risk_budget"],
            "after": before["risk_budget"],
            "frontier": [],
        }
    required = max(
        0.0,
        before["risk_budget"]["worst_loss_amount"]
        - before["risk_budget"]["loss_budget_amount"],
    )
    repair_amounts = dict(proposed_amounts)
    actions = []
    candidates = [
        row for row in before["positions"]
        if row["holding_id"] != "cash_reserve" and (row["shock_lower_pct"] or 0) < 0
    ]
    candidates.sort(key=lambda row: row["shock_lower_pct"])
    for row in candidates:
        if required <= 0.005:
            break
        efficiency = -float(row["shock_lower_pct"]) / 100
        available = float(repair_amounts.get(row["holding_id"], 0.0))
        if efficiency <= 0 or available <= 0:
            continue
        shift = min(available, required / efficiency)
        if minimum_trade_amount > 0 and 0 < shift < minimum_trade_amount:
            shift = min(available, minimum_trade_amount)
        shift = round(shift + 0.005, 2)
        shift = min(available, shift)
        if shift <= 0:
            continue
        repair_amounts[row["holding_id"]] = available - shift
        repair_amounts["cash_reserve"] = repair_amounts.get("cash_reserve", 0.0) + shift
        improvement = shift * efficiency
        required -= improvement
        actions.append(
            {
                "action": "reduce_to_cash",
                "holding_id": row["holding_id"],
                "code": row["code"],
                "name": row["name"],
                "reduce_amount": _round(shift),
                "target_amount": _round(repair_amounts[row["holding_id"]]),
                "marginal_worst_loss_reduction": _round(improvement),
                "worst_shock_pct": row["shock_lower_pct"],
            }
        )
    after = _evaluate_portfolio(models, repair_amounts, total, scenario, loss_budget_pct)
    total_shift = sum(float(item["reduce_amount"]) for item in actions)
    frontier = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        trial = dict(proposed_amounts)
        shifted = 0.0
        for action in actions:
            delta = float(action["reduce_amount"]) * fraction
            trial[action["holding_id"]] -= delta
            trial["cash_reserve"] = trial.get("cash_reserve", 0.0) + delta
            shifted += delta
        evaluated = _evaluate_portfolio(models, trial, total, scenario, loss_budget_pct)
        frontier.append(
            {
                "repair_pct": int(fraction * 100),
                "shift_to_cash": _round(shifted),
                "worst_loss_pct": _round(max(0.0, -evaluated["pnl_interval"]["lower_pct"]), 4),
                "budget_utilization_pct": evaluated["risk_budget"]["utilization_pct"],
            }
        )
    return {
        "status": "available" if not after["risk_budget"]["breached"] else "insufficient",
        "actions": actions,
        "total_shift_to_cash": _round(total_shift),
        "before": before["risk_budget"],
        "after": after["risk_budget"],
        "frontier": frontier,
        "optimality_scope": "在线性最坏情景、仅允许减持并转为零冲击现金、忽略费用税费与最小持仓约束时，按边际损失率排序得到最小名义调整额。",
        "execution_policy": "仅为研究方案；不会自动下单，执行前需核对流动性、税费、汇率、申赎规则和最新价格。",
    }


def build_decision_twin(
    *,
    holdings: list[dict[str, Any]],
    exposure: dict[str, Any],
    profile: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_scenario(scenario)
    models, total, amount_warnings = _build_position_models(holdings, exposure)
    targets = {
        _holding_id(item["holding_id"]): float(item["target_amount"])
        for item in normalized["hypothetical_positions"]
    }
    current_amounts, proposed_amounts = _amounts(models, total, targets)
    requested_budget = float(normalized["loss_budget_pct"])
    policy_budget = _number(profile.get("max_drawdown_pct")) if profile.get("configured") else None
    effective_budget = min(requested_budget, policy_budget) if policy_budget is not None else requested_budget

    current = _evaluate_portfolio(
        models, current_amounts, total, normalized, effective_budget
    )
    proposed = _evaluate_portfolio(
        models, proposed_amounts, total, normalized, effective_budget
    )
    current["policy_gates"] = _policy_gates(current, profile)
    proposed["policy_gates"] = _policy_gates(proposed, profile)

    current_holdings_hash = portfolio_exposure.holdings_sha256(holdings)
    snapshot_hash = str(exposure.get("holdings_sha256") or "")
    integrity = exposure.get("integrity") or {}
    quality = exposure.get("quality") or {}
    evidence_checks = {
        "holdings_hash_matches": bool(snapshot_hash and snapshot_hash == current_holdings_hash),
        "exposure_snapshot_verified": bool(integrity.get("verified")),
        "exposure_decision_eligible": bool(quality.get("decision_eligible")),
        "investment_policy_active": bool(profile.get("configured")),
    }
    reasons = list(amount_warnings)
    if not evidence_checks["holdings_hash_matches"]:
        reasons.append("压力测试期间持仓与穿透快照哈希不一致，结果不得用于调仓决策。")
    if not evidence_checks["exposure_snapshot_verified"]:
        reasons.append("穿透快照完整性校验未通过。")
    if not evidence_checks["exposure_decision_eligible"]:
        reasons.extend(str(item) for item in quality.get("reasons") or [])
    if not evidence_checks["investment_policy_active"]:
        reasons.append("尚无有效投资政策，本次使用情景内显式亏损预算，但政策一致性门禁不可用。")
    decision_eligible = all(evidence_checks.values()) and not amount_warnings
    run_status = "complete" if decision_eligible else "partial"

    repair = _repair_plan(
        models,
        proposed_amounts,
        total,
        normalized,
        effective_budget,
        float(normalized["minimum_trade_amount"]),
    )
    comparison = {
        "what_if_changed": bool(targets),
        "worst_loss_improvement_amount": _round(
            proposed["pnl_interval"]["lower_amount"] - current["pnl_interval"]["lower_amount"]
        ),
        "worst_loss_improvement_pct": _round(
            proposed["pnl_interval"]["lower_pct"] - current["pnl_interval"]["lower_pct"], 4
        ),
        "risk_budget_utilization_change_pct": _round(
            proposed["risk_budget"]["utilization_pct"] - current["risk_budget"]["utilization_pct"], 4
        ),
        "cash_ratio_change_pct": _round(
            proposed["allocation"]["cash_ratio"] - current["allocation"]["cash_ratio"], 4
        ),
    }
    fragility = [
        {
            "rank": index,
            **row,
            "worst_loss_contribution_pct": _round(
                max(0.0, -float(row["pnl_lower"]))
                / max(0.01, proposed["risk_budget"]["worst_loss_amount"])
                * 100,
                4,
            ),
        }
        for index, row in enumerate(proposed["positions"], 1)
        if row["holding_id"] != "cash_reserve"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "method_version": METHOD_VERSION,
        "status": run_status,
        "scenario": normalized,
        "budget": {
            "requested_loss_budget_pct": requested_budget,
            "policy_max_drawdown_pct": policy_budget,
            "effective_loss_budget_pct": effective_budget,
            "source": "stricter_of_policy_and_scenario" if policy_budget is not None else "scenario_explicit",
        },
        "current": current,
        "proposed": proposed,
        "comparison": comparison,
        "reverse_stress": _reverse_stress(
            models, proposed_amounts, total, normalized, effective_budget
        ),
        "repair_plan": repair,
        "fragility_map": fragility,
        "decision_gate": {
            "decision_eligible": decision_eligible,
            "checks": evidence_checks,
            "reasons": list(dict.fromkeys(reasons)),
        },
        "data_lineage": {
            "holdings_sha256": current_holdings_hash,
            "exposure_holdings_sha256": snapshot_hash or None,
            "exposure_snapshot_id": (exposure.get("snapshot") or {}).get("id"),
            "exposure_payload_sha256": (exposure.get("snapshot") or {}).get("payload_sha256"),
            "profile_version_id": profile.get("profile_version_id"),
            "exposure_evaluated_on": exposure.get("evaluated_on"),
        },
        "methodology": {
            "model": "对金额暴露应用一阶线性市场冲击，并将行业冲击作为叠加项；单项总损失封顶为 95%。",
            "uncertainty": "基金未披露或未分类权益保留为上下界；行业未知部分在用户设置的行业叠加冲击中取最不利/最有利归属。",
            "not_modeled": [
                "相关性在危机中的动态变化",
                "期权与债券全价重估、久期和凸性",
                "汇率二阶传导、流动性滑点、税费与申赎限制",
                "未来收益概率或涨跌预测",
            ],
            "decision_boundary": "结果用于比较假设和识别脆弱点，不是收益保证、个性化买卖指令或自动交易授权。",
        },
    }
