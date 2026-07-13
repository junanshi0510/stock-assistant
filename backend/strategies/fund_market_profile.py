# -*- coding: utf-8 -*-
"""Classify a public fund's investment market from provider metadata."""

from __future__ import annotations

from typing import Any


STRATEGY_ID = "fund_market_profile"
STRATEGY_VERSION = "1.0.0"

_MARKET_RULES = {
    "hong_kong": (
        "恒生", "港股", "香港", "H股", "国企指数", "恒生科技",
        "HANG SENG", "HONG KONG",
    ),
    "united_states": (
        "纳斯达克", "标普", "美国", "美股", "道琼斯", "NASDAQ",
        "S&P 500", "S&P500", "DOW JONES", "MSCI USA",
    ),
    "global": (
        "全球", "世界", "亚太", "亚洲", "欧洲", "德国", "法国", "英国",
        "日本", "日经", "印度", "越南", "GLOBAL", "WORLD", "EUROPE",
        "JAPAN", "INDIA", "ASIA PACIFIC",
    ),
}

_MARKET_LABELS = {
    "mainland": "中国内地或未明确跨境",
    "hong_kong": "中国香港",
    "united_states": "美国",
    "global": "全球或其他海外市场",
    "cross_border_mixed": "跨境混合市场",
    "unknown_cross_border": "跨境市场待确认",
}


def _matches(text: str, keywords: tuple[str, ...]) -> list[str]:
    upper = str(text or "").upper()
    return [keyword for keyword in keywords if keyword.upper() in upper]


def build_fund_market_profile(
    *,
    code: str,
    name: str,
    fund_type: str,
    benchmark_names: list[str] | None = None,
) -> dict[str, Any]:
    benchmarks = [str(item or "").strip() for item in benchmark_names or [] if str(item or "").strip()]
    combined = " | ".join([str(name or ""), str(fund_type or ""), *benchmarks])
    qdii = "QDII" in str(fund_type or "").upper() or "QDII" in str(name or "").upper()
    signals = {
        market: _matches(combined, keywords)
        for market, keywords in _MARKET_RULES.items()
    }
    detected = [market for market, matches in signals.items() if matches]

    if len(detected) > 1:
        primary_market = "cross_border_mixed"
        required_permissions = sorted(set(detected))
        resolution_status = "identified"
    elif detected:
        primary_market = detected[0]
        required_permissions = [primary_market]
        resolution_status = "identified"
    elif qdii:
        primary_market = "unknown_cross_border"
        required_permissions = []
        resolution_status = "insufficient"
    else:
        primary_market = "mainland"
        required_permissions = ["mainland"]
        resolution_status = "identified"

    cross_border = qdii or primary_market != "mainland"
    currency_risk = cross_border
    nav_lag = "T+1/T+2 或更晚" if qdii else "以基金管理人确认净值日为准"
    estimate_policy = (
        "跨境基金的盘中估值可能受海外时区、休市和汇率影响，只能作参考，不进入金额门禁。"
        if cross_border
        else "盘中估值与确认净值分离，不用估值替代最终净值。"
    )
    risks = []
    if qdii:
        risks.extend(["overseas_market_session_mismatch", "confirmed_nav_publication_lag"])
    if currency_risk:
        risks.append("foreign_exchange_exposure")
    if resolution_status == "insufficient":
        risks.append("underlying_market_not_resolved")

    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "status": "available",
        "resolution_status": resolution_status,
        "fund": {
            "code": str(code),
            "name": str(name or ""),
            "fund_type": str(fund_type or ""),
            "is_qdii": qdii,
        },
        "market": {
            "primary": primary_market,
            "label": _MARKET_LABELS[primary_market],
            "detected_markets": detected,
            "required_permissions": required_permissions,
            "cross_border": cross_border,
            "currency_risk": currency_risk,
        },
        "valuation": {
            "confirmed_nav_lag": nav_lag,
            "intraday_estimate_policy": estimate_policy,
            "confirmed_nav_only_for_decision": True,
        },
        "benchmark_names": benchmarks,
        "signals": [
            {"market": market, "keywords": matches}
            for market, matches in signals.items()
            if matches
        ],
        "risks": risks,
        "method": {
            "classification": "provider_fund_type_plus_provider_detail_comparison_series_names",
            "unknown_policy": "unresolved_cross_border_market_blocks_personalized_amount",
            "no_holdings_inference": "fund_name_or_code_is_not_used_to_invent_undisclosed_positions",
        },
        "policy": "市场画像来自真实基金元数据和详情页累计收益比较序列；页面比较序列不等于基金合同业绩比较基准。定期报告持仓可能滞后，市场画像不代表实时底层仓位。",
    }
