# -*- coding: utf-8 -*-
"""Build an immutable comparability cohort from already persisted Evidence."""

from __future__ import annotations

import re
from typing import Any


COHORT_SCHEMA_VERSION = "strategy_shadow_cohort.v1"
COHORT_TAXONOMY_ID = "fund_strategy_shadow_cohort"
COHORT_TAXONOMY_VERSION = "1.0.0"

_HORIZON_OBSERVATIONS = {"3m": 63, "6m": 126, "12m": 252}
_MARKETS = {
    "mainland",
    "hong_kong",
    "united_states",
    "global",
    "cross_border_mixed",
    "unknown_cross_border",
}
_MARKET_LABELS = {
    "mainland": "中国内地",
    "hong_kong": "中国香港",
    "united_states": "美国",
    "global": "全球或其他海外",
    "cross_border_mixed": "跨境混合市场",
    "unknown_cross_border": "跨境市场待确认",
}
_ASSET_LABELS = {
    "equity": "权益",
    "fixed_income": "固收",
    "mixed": "混合资产",
    "cash": "现金管理",
    "commodity": "商品",
    "real_estate": "不动产",
    "fund_of_funds": "基金中基金",
    "alternative": "另类策略",
    "unknown": "资产类别待确认",
}
_TREND_LABELS = {"above_ma60": "净值在 MA60 上方", "below_ma60": "净值在 MA60 下方"}
_DRAWDOWN_LABELS = {
    "near_high": "接近阶段高点",
    "normal_pullback": "常规回撤",
    "deep_drawdown": "深度回撤",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _evidence_payload(evidence: dict[str, Any], label: str) -> dict[str, Any]:
    if not evidence or not evidence.get("integrity_verified"):
        raise ValueError(f"{label} Evidence 完整性校验失败")
    evidence_id = _text(evidence.get("id"))
    payload_hash = _text(evidence.get("payload_sha256"))
    if not evidence_id or not re.fullmatch(r"[0-9a-f]{64}", payload_hash):
        raise ValueError(f"{label} Evidence 缺少稳定标识或载荷哈希")
    payload = evidence.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"{label} Evidence 缺少结构化载荷")
    return payload


def classify_fund_asset_class(fund_type: str) -> dict[str, str]:
    """Normalize provider-native fund types without guessing unknown categories."""
    normalized = _text(fund_type).upper().replace("（", "(").replace("）", ")")
    if any(keyword in normalized for keyword in ("货币", "现金")):
        asset_class = "cash"
    elif any(keyword in normalized for keyword in ("债券", "纯债", "固收", "可转债")):
        asset_class = "fixed_income"
    elif any(keyword in normalized for keyword in ("商品", "黄金", "白银", "原油", "期货")):
        asset_class = "commodity"
    elif any(keyword in normalized for keyword in ("REIT", "不动产")):
        asset_class = "real_estate"
    elif any(keyword in normalized for keyword in ("FOF", "基金中基金")):
        asset_class = "fund_of_funds"
    elif any(keyword in normalized for keyword in ("量化对冲", "绝对收益", "市场中性")):
        asset_class = "alternative"
    elif any(keyword in normalized for keyword in ("股票", "权益")):
        asset_class = "equity"
    elif any(keyword in normalized for keyword in ("混合", "灵活配置")):
        asset_class = "mixed"
    else:
        asset_class = "unknown"
    return {
        "primary": asset_class,
        "label": _ASSET_LABELS[asset_class],
        "provider_fund_type": _text(fund_type),
    }


def build_strategy_shadow_cohort(
    *,
    enrollment: dict[str, Any],
    market_profile_evidence: dict[str, Any],
    signal_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Bind a Shadow enrollment to comparable market, asset, horizon, and regime axes."""
    market_payload = _evidence_payload(market_profile_evidence, "市场画像")
    signal_payload = _evidence_payload(signal_evidence, "策略信号")
    run_id = _text(enrollment.get("run_id"))
    for evidence, label in (
        (market_profile_evidence, "市场画像"),
        (signal_evidence, "策略信号"),
    ):
        if _text(evidence.get("run_id")) != run_id:
            raise ValueError(f"{label} Evidence 与 Shadow Run 不一致")

    strategy = signal_payload.get("conditioned_forward") or signal_payload
    if not isinstance(strategy, dict):
        raise ValueError("策略信号 Evidence 缺少 conditioned_forward 结果")
    strategy_id = _text(enrollment.get("strategy_id"))
    strategy_version = _text(enrollment.get("strategy_version"))
    if (
        _text(strategy.get("strategy_id")) != strategy_id
        or _text(strategy.get("strategy_version")) != strategy_version
    ):
        raise ValueError("策略信号 Evidence 与入组策略版本不一致")
    signal = strategy.get("signal") or {}
    condition = strategy.get("condition") or {}
    horizon = _text(enrollment.get("horizon"))
    observation_days = int(enrollment.get("observation_days") or 0)
    expected_observations = _HORIZON_OBSERVATIONS.get(horizon)
    if expected_observations is None or observation_days != expected_observations:
        raise ValueError("Shadow 预测周期与确认净值观测数量不符合版本化口径")
    horizon_result = next(
        (
            item
            for item in strategy.get("horizons") or []
            if _text(item.get("horizon")) == horizon
        ),
        None,
    )
    if (
        _text(strategy.get("primary_horizon")) != horizon
        or not isinstance(horizon_result, dict)
        or int(horizon_result.get("observation_days") or 0) != observation_days
        or _text(signal.get("direction")) != _text(enrollment.get("signal_direction"))
        or _text(condition.get("as_of")) != _text(enrollment.get("baseline_as_of"))
    ):
        raise ValueError("策略信号 Evidence 与冻结方向、周期或基线不一致")

    market_fund = market_payload.get("fund") or {}
    market = market_payload.get("market") or {}
    fund_code = _text(enrollment.get("fund_code"))
    if _text(market_fund.get("code")) != fund_code:
        raise ValueError("市场画像 Evidence 与入组基金代码不一致")
    if (
        _text(market_payload.get("strategy_id")) != "fund_market_profile"
        or _text(market_payload.get("strategy_version")) != "1.0.0"
    ):
        raise ValueError("市场画像 Evidence 缺少受支持的分类器版本")
    primary_market = _text(market.get("primary"))
    if primary_market not in _MARKETS:
        raise ValueError("市场画像 Evidence 返回未知市场类别")
    snapshot_market = _text(
        (((enrollment.get("signal_snapshot") or {}).get("fund") or {}).get("market"))
    )
    if snapshot_market and snapshot_market != primary_market:
        raise ValueError("市场画像 Evidence 与原信号快照的市场不一致")

    detected_markets = sorted({
        _text(value)
        for value in market.get("detected_markets") or []
        if _text(value) in _MARKETS - {"cross_border_mixed", "unknown_cross_border"}
    })
    if primary_market == "cross_border_mixed" and len(detected_markets) < 2:
        raise ValueError("跨境混合市场缺少至少两个已识别市场")
    market_bucket = (
        f"cross_border_mixed[{'+'.join(detected_markets)}]"
        if primary_market == "cross_border_mixed"
        else primary_market
    )
    is_qdii = bool(market_fund.get("is_qdii"))
    cross_border = bool(market.get("cross_border"))
    vehicle = "qdii" if is_qdii else "cross_border_non_qdii" if cross_border else "domestic"
    asset = classify_fund_asset_class(_text(market_fund.get("fund_type")))
    trend = _text(condition.get("trend"))
    drawdown = _text(condition.get("drawdown_band"))

    release_reasons = []
    if _text(market_payload.get("resolution_status")) != "identified":
        release_reasons.append("market_resolution_insufficient")
    if primary_market == "unknown_cross_border":
        release_reasons.append("underlying_cross_border_market_unknown")
    if asset["primary"] == "unknown":
        release_reasons.append("asset_class_unknown")
    if trend not in _TREND_LABELS:
        release_reasons.append("signal_trend_regime_unknown")
    if drawdown not in _DRAWDOWN_LABELS:
        release_reasons.append("signal_drawdown_regime_unknown")
    release_reasons = sorted(set(release_reasons))
    release_eligible = not release_reasons
    release_cohort_key = (
        f"horizon={horizon}|market={market_bucket}|asset={asset['primary']}|vehicle={vehicle}"
    )
    regime_cohort_key = (
        f"{release_cohort_key}|trend={trend or 'unknown'}|drawdown={drawdown or 'unknown'}"
    )

    return {
        "schema_version": COHORT_SCHEMA_VERSION,
        "taxonomy": {
            "id": COHORT_TAXONOMY_ID,
            "version": COHORT_TAXONOMY_VERSION,
        },
        "enrollment_binding": {
            "enrollment_id": _text(enrollment.get("id")),
            "run_id": run_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "manifest_sha256": _text(enrollment.get("manifest_sha256")),
            "signal_snapshot_sha256": _text(enrollment.get("signal_snapshot_sha256")),
            "fund_code": fund_code,
            "baseline_as_of": _text(enrollment.get("baseline_as_of")),
            "signal_direction": _text(enrollment.get("signal_direction")),
        },
        "source_evidence": {
            "market_profile": {
                "evidence_id": _text(market_profile_evidence.get("id")),
                "payload_sha256": _text(market_profile_evidence.get("payload_sha256")),
                "classifier_id": _text(market_payload.get("strategy_id")),
                "classifier_version": _text(market_payload.get("strategy_version")),
            },
            "signal": {
                "evidence_id": _text(signal_evidence.get("id")),
                "payload_sha256": _text(signal_evidence.get("payload_sha256")),
                "strategy_id": _text(strategy.get("strategy_id")),
                "strategy_version": _text(strategy.get("strategy_version")),
            },
        },
        "dimensions": {
            "horizon": {
                "name": horizon,
                "confirmed_nav_observations": observation_days,
            },
            "market": {
                "primary": primary_market,
                "label": _MARKET_LABELS[primary_market],
                "bucket": market_bucket,
                "detected_markets": detected_markets,
                "resolution_status": _text(market_payload.get("resolution_status")),
                "cross_border": cross_border,
                "currency_risk": bool(market.get("currency_risk")),
            },
            "asset_class": asset,
            "vehicle": {
                "type": vehicle,
                "is_qdii": is_qdii,
            },
            "signal_regime": {
                "trend": trend,
                "trend_label": _TREND_LABELS.get(trend, "趋势状态待确认"),
                "drawdown_band": drawdown,
                "drawdown_label": _DRAWDOWN_LABELS.get(drawdown, "回撤状态待确认"),
            },
        },
        "keys": {
            "release_cohort": release_cohort_key,
            "regime_cohort": regime_cohort_key,
        },
        "release_classification": {
            "eligible": release_eligible,
            "status": "complete" if release_eligible else "insufficient",
            "reasons": release_reasons,
        },
        "method": {
            "source": "immutable_run_evidence_only",
            "asset_classification": "provider_fund_type_deterministic_rules",
            "market_classification": "bound_fund_market_profile_evidence",
            "regime_classification": "bound_strategy_input_condition",
            "pooled_cross_cohort_metrics": "forbidden",
            "unknown_values": "retained_but_not_release_eligible",
        },
        "policy": "Cohort 只用于判断策略结果是否可比较；未知市场、资产类别或信号状态不会被猜测，也不能进入策略绩效披露。",
    }
