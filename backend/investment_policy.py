# -*- coding: utf-8 -*-
"""Versioned Investment Policy Statement validation and consent contract."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


POLICY_SCHEMA_VERSION = "investment_policy.v1"
QUESTIONNAIRE_VERSION = "investment_suitability.v1"
CONSENT_VERSION = "investment_policy_consent.v1"
CONSENT_TEXT = (
    "我确认本投资政策书中的风险承受能力、投资期限、预算、流动性和市场权限由我本人提供；"
    "系统只据此执行研究与风险门禁，不保证收益、不推断未提供资产，也不会自动下单。"
)
CONSENT_TEXT_SHA256 = hashlib.sha256(CONSENT_TEXT.encode("utf-8")).hexdigest()

RISK_LEVELS = {"stable", "balanced", "aggressive"}
HORIZONS = {"short", "mid_long", "long"}
EXPERIENCE_LEVELS = {"beginner", "intermediate", "experienced"}
OBJECTIVES = {"capital_preservation", "balanced_growth", "long_term_growth"}
FUND_MARKETS = {"mainland", "hong_kong", "united_states", "global"}
MARKET_ORDER = ("mainland", "hong_kong", "united_states", "global")

RISK_LIMITS = {
    "stable": {"max_single_ratio": 30.0, "max_equity_ratio": 40.0, "max_drawdown_pct": 15.0},
    "balanced": {"max_single_ratio": 45.0, "max_equity_ratio": 75.0, "max_drawdown_pct": 30.0},
    "aggressive": {"max_single_ratio": 60.0, "max_equity_ratio": 100.0, "max_drawdown_pct": 50.0},
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_investment_policy(payload: dict[str, Any]) -> dict[str, Any]:
    selected = {str(item) for item in payload.get("allowed_fund_markets") or []}
    markets = [item for item in MARKET_ORDER if item in selected]
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "risk": str(payload.get("risk") or ""),
        "horizon": str(payload.get("horizon") or ""),
        "experience_level": str(payload.get("experience_level") or ""),
        "primary_objective": str(payload.get("primary_objective") or ""),
        "monthly_budget": _number(payload.get("monthly_budget")),
        "max_single_ratio": _number(payload.get("max_single_ratio")),
        "max_equity_ratio": _number(payload.get("max_equity_ratio")),
        "max_industry_ratio": _number(payload.get("max_industry_ratio")),
        "max_drawdown_pct": _number(payload.get("max_drawdown_pct")),
        "liquidity_reserve_months": _number(payload.get("liquidity_reserve_months")),
        "allowed_fund_markets": markets,
        "accept_fx_risk": bool(payload.get("accept_fx_risk")),
        "emergency_fund_confirmed": bool(payload.get("emergency_fund_confirmed")),
        "review_cycle_months": _integer(payload.get("review_cycle_months")),
    }


def validate_investment_policy(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_investment_policy(payload)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(field: str, code: str, message: str) -> None:
        errors.append({"field": field, "code": code, "message": message})

    def warning(field: str, code: str, message: str) -> None:
        warnings.append({"field": field, "code": code, "message": message})

    risk = normalized["risk"]
    horizon = normalized["horizon"]
    experience = normalized["experience_level"]
    if risk not in RISK_LEVELS:
        error("risk", "unsupported_risk", "请选择有效的风险承受等级")
    if horizon not in HORIZONS:
        error("horizon", "unsupported_horizon", "请选择有效的资金使用期限")
    if experience not in EXPERIENCE_LEVELS:
        error("experience_level", "unsupported_experience", "请选择真实投资经验")
    if normalized["primary_objective"] not in OBJECTIVES:
        error("primary_objective", "unsupported_objective", "请选择主要投资目标")

    ranges = {
        "monthly_budget": (0, 10_000_000),
        "max_single_ratio": (5, 60),
        "max_equity_ratio": (0, 100),
        "max_industry_ratio": (5, 50),
        "max_drawdown_pct": (5, 50),
        "liquidity_reserve_months": (0, 36),
    }
    for field, (minimum, maximum) in ranges.items():
        value = normalized[field]
        if value is None:
            error(field, "required_number", "该项必须由用户明确填写")
        elif value < minimum or value > maximum:
            error(field, "out_of_range", f"该项必须在 {minimum} 至 {maximum} 之间")

    markets = normalized["allowed_fund_markets"]
    if not markets:
        error("allowed_fund_markets", "market_required", "至少选择一个允许投资的基金市场")
    unknown_markets = {
        str(item) for item in payload.get("allowed_fund_markets") or []
    } - FUND_MARKETS
    if unknown_markets:
        error("allowed_fund_markets", "unsupported_market", "包含不支持的基金市场")
    cross_border = any(item != "mainland" for item in markets)
    if cross_border and not normalized["accept_fx_risk"]:
        error("accept_fx_risk", "fx_consent_required", "允许跨境基金前必须确认汇率和净值时差风险")
    if not cross_border and normalized["accept_fx_risk"]:
        warning("accept_fx_risk", "fx_consent_not_used", "当前仅允许内地市场，汇率风险确认暂不参与决策")

    reserve = normalized["liquidity_reserve_months"]
    if reserve is not None and reserve < 3:
        error("liquidity_reserve_months", "liquidity_reserve_too_low", "激活前至少确认 3 个月流动性储备")
    if not normalized["emergency_fund_confirmed"]:
        error("emergency_fund_confirmed", "emergency_fund_not_confirmed", "必须确认投资预算不占用应急资金")

    if risk in RISK_LIMITS:
        limits = RISK_LIMITS[risk]
        for field, maximum in limits.items():
            value = normalized[field]
            if value is not None and value > maximum:
                error(field, "risk_limit_conflict", f"该数值超过当前风险等级上限 {maximum:g}%")
    if experience == "beginner" and risk == "aggressive":
        error("risk", "experience_risk_conflict", "初学投资经验不能直接激活进取型风险政策")
    if horizon == "short" and risk == "aggressive":
        error("horizon", "horizon_risk_conflict", "短期资金不能激活进取型风险政策")

    industry = normalized["max_industry_ratio"]
    equity = normalized["max_equity_ratio"]
    if industry is not None and equity is not None and industry > equity and equity > 0:
        error("max_industry_ratio", "industry_exceeds_equity", "单行业上限不能高于权益资产总上限")
    if normalized["review_cycle_months"] not in {6, 12}:
        error("review_cycle_months", "unsupported_review_cycle", "复核周期只能是 6 或 12 个月")
    if "global" in markets:
        warning("allowed_fund_markets", "global_market_scope", "全球基金可能包含多币种、额度和不同交易日历")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized": normalized,
        "payload_sha256": payload_sha256(normalized),
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "consent": {
            "version": CONSENT_VERSION,
            "text": CONSENT_TEXT,
            "text_sha256": CONSENT_TEXT_SHA256,
        },
        "policy": "只有通过确定性适当性校验并由用户确认哈希的版本才能激活；校验通过不代表保证收益。",
    }
