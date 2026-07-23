# -*- coding: utf-8 -*-
"""Deterministic cross-market opportunity research and paper tracking.

The engine ranks evidence; it does not claim to predict certain winners.  All
peer grades are calculated only from the exact, frozen run universe, and every
missing source remains visible in the result.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median
from typing import Any, Callable

import numpy as np
import pandas as pd

import analysis
import data_fetch
import fundamentals
import hot_stocks
import storage
from background_jobs import BackgroundJobRepository
from market_presets import PRESETS
from opportunity_repository import (
    OpportunityConflictError,
    OpportunityNotFoundError,
    OpportunityRepository,
    repository,
)
from task_queue import (
    QUEUE_MARKET,
    TaskQueueUnavailableError,
    enqueue_background_job,
    uses_celery_queue,
)


POLICY_VERSION = "cross_market_opportunity_factory@1.0.0"
MAX_UNIVERSE = 80
FACTOR_LABELS = {
    "momentum": "趋势动量",
    "value": "估值",
    "quality": "盈利质量",
    "growth": "成长",
    "risk": "风险韧性",
}
DEFAULT_FACTORS = {
    "momentum": 30.0,
    "value": 15.0,
    "quality": 20.0,
    "growth": 15.0,
    "risk": 20.0,
}
DEFAULT_GATES = {
    "min_history_days": 180,
    "max_data_age_days": 10,
    "min_technical_score": 45.0,
    "min_return_3m": -15.0,
    "max_annual_vol": 80.0,
    "max_drawdown_pct": 60.0,
    "min_factor_coverage": 0.4,
    "min_composite_score": 58.0,
    "require_fundamentals": False,
}
DEFAULT_PORTFOLIO = {
    "max_positions": 8,
    "max_position_pct": 20.0,
    "min_cash_pct": 10.0,
    "max_pair_correlation": 0.85,
    "defensive_cash_add_pct": 10.0,
    "weighting": "score_inverse_vol",
}
PAPER_VALIDATION_HORIZONS = (5, 20, 60)
PAPER_COST_SCENARIO_BPS = 30.0
PAPER_BENCHMARKS = {
    "A股": {"symbol": "510300", "name": "沪深300ETF"},
    "港股": {"symbol": "02800", "name": "盈富基金"},
    "美股": {"symbol": "SPY", "name": "标普500ETF"},
}

FACTOR_METRICS = {
    "momentum": (
        ("return_1m", "近 1 月收益", True),
        ("return_3m", "近 3 月收益", True),
        ("return_6m", "近 6 月收益", True),
        ("technical_score", "技术评分", True),
    ),
    "value": (
        ("pe", "市盈率", False),
        ("pb", "市净率", False),
        ("pe_percentile", "PE 历史分位", False),
        ("pb_percentile", "PB 历史分位", False),
    ),
    "quality": (
        ("roe", "ROE", True),
        ("gross_margin", "毛利率", True),
        ("net_margin", "净利率", True),
        ("debt_ratio", "资产负债率", False),
        ("cashflow_quality", "现金流质量", True),
    ),
    "growth": (
        ("revenue_growth", "营收同比", True),
        ("profit_growth", "净利润同比", True),
        ("revenue_streak", "营收连续增长年数", True),
        ("profit_streak", "利润连续增长年数", True),
    ),
    "risk": (
        ("annual_vol", "年化波动", False),
        ("downside_vol", "下行波动", False),
        ("max_drawdown_abs", "最大回撤", False),
    ),
}


def _template(
    template_id: str,
    name: str,
    description: str,
    *,
    markets: list[str],
    factors: dict[str, float] | None = None,
    gates: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    hot_lists: list[str] | None = None,
) -> dict[str, Any]:
    return normalize_definition(
        {
            "template_id": template_id,
            "name": name,
            "description": description,
            "markets": markets,
            "history_months": 18,
            "universe": {
                "include_presets": True,
                "include_watchlist": True,
                "hot_lists": hot_lists or [],
                "hot_limit_per_market": 8,
                "symbols": [],
            },
            "factors": {**DEFAULT_FACTORS, **(factors or {})},
            "gates": {**DEFAULT_GATES, **(gates or {})},
            "portfolio": {**DEFAULT_PORTFOLIO, **(portfolio or {})},
        }
    )


def strategy_templates() -> list[dict[str, Any]]:
    return [
        _template(
            "cross_market_balanced",
            "跨市场均衡雷达",
            "在 A 股、港股和美股种子池与自选股中，同时衡量趋势、估值、质量、成长和风险。",
            markets=["A股", "港股", "美股"],
        ),
        _template(
            "quality_momentum",
            "质量趋势共振",
            "基本面必须可用，只有质量与成长证据和趋势同时通过才进入候选。",
            markets=["A股", "美股"],
            factors={"momentum": 30, "value": 15, "quality": 25, "growth": 20, "risk": 10},
            gates={"require_fundamentals": True, "min_factor_coverage": 0.8, "min_composite_score": 62},
            portfolio={"max_positions": 6, "max_position_pct": 22, "min_cash_pct": 12},
        ),
        _template(
            "active_momentum",
            "活跃强势验证",
            "把成交活跃榜和涨幅榜作为发现入口，再用历史、波动、回撤和多因子门槛排除追高冲突。",
            markets=["A股", "港股", "美股"],
            factors={"momentum": 40, "value": 10, "quality": 15, "growth": 10, "risk": 25},
            gates={"min_technical_score": 52, "min_return_3m": -5, "min_composite_score": 62},
            portfolio={"max_positions": 6, "max_position_pct": 18, "min_cash_pct": 20},
            hot_lists=["active", "gainers"],
        ),
        _template(
            "defensive_resilience",
            "低波动防守池",
            "优先淘汰高波动和深回撤标的，并在弱势市场状态下自动提高纸面现金缓冲。",
            markets=["A股", "港股", "美股"],
            factors={"momentum": 15, "value": 15, "quality": 20, "growth": 10, "risk": 40},
            gates={"max_annual_vol": 48, "max_drawdown_pct": 38, "min_composite_score": 58},
            portfolio={"max_positions": 8, "max_position_pct": 16, "min_cash_pct": 20, "defensive_cash_add_pct": 15},
        ),
    ]


def _bounded(value: Any, lower: float, upper: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须是数字") from error
    if not math.isfinite(number) or number < lower or number > upper:
        raise ValueError(f"{name} 必须在 {lower:g}-{upper:g} 之间")
    return number


def _normalize_symbol(market: str, value: Any) -> str:
    symbol = re.sub(r"\s+", "", str(value or ""))
    if market == "美股":
        symbol = symbol.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError(f"美股代码格式无效:{symbol or '(空)'}")
    elif market == "港股":
        if not re.fullmatch(r"\d{1,5}", symbol):
            raise ValueError(f"港股代码格式无效:{symbol or '(空)'}")
        symbol = symbol.zfill(5)
    elif market == "A股":
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError(f"A股代码格式无效:{symbol or '(空)'}")
    else:
        raise ValueError(f"不支持的市场:{market}")
    return symbol


def normalize_definition(value: dict[str, Any]) -> dict[str, Any]:
    source = dict(value or {})
    name = re.sub(r"\s+", " ", str(source.get("name") or "")).strip()
    if len(name) < 2 or len(name) > 80:
        raise ValueError("策略名称需要 2-80 个字符")
    description = re.sub(r"\s+", " ", str(source.get("description") or "")).strip()
    if len(description) < 8 or len(description) > 300:
        raise ValueError("策略说明需要 8-300 个字符")
    markets = []
    for market in source.get("markets") or []:
        item = str(market)
        if item not in data_fetch.MARKETS:
            raise ValueError(f"不支持的市场:{item}")
        if item not in markets:
            markets.append(item)
    if not markets:
        raise ValueError("至少选择一个市场")

    universe_source = dict(source.get("universe") or {})
    symbols = []
    seen = set()
    for item in universe_source.get("symbols") or []:
        market = str((item or {}).get("market") or "")
        if market not in markets:
            raise ValueError("手工股票所属市场必须已勾选")
        symbol = _normalize_symbol(market, (item or {}).get("symbol"))
        key = (market, symbol)
        if key in seen:
            continue
        seen.add(key)
        symbols.append(
            {
                "market": market,
                "symbol": symbol,
                "name": re.sub(r"\s+", " ", str((item or {}).get("name") or "")).strip()[:80],
            }
        )
    if len(symbols) > MAX_UNIVERSE:
        raise ValueError(f"手工股票最多 {MAX_UNIVERSE} 只")
    hot_lists = []
    for kind in universe_source.get("hot_lists") or []:
        item = str(kind)
        if item not in {"active", "gainers", "losers"}:
            raise ValueError(f"不支持的热门池:{item}")
        if item not in hot_lists:
            hot_lists.append(item)
    include_presets = bool(universe_source.get("include_presets", True))
    include_watchlist = bool(universe_source.get("include_watchlist", True))
    if not (include_presets or include_watchlist or hot_lists or symbols):
        raise ValueError("候选池至少启用一个来源")
    universe = {
        "include_presets": include_presets,
        "include_watchlist": include_watchlist,
        "hot_lists": hot_lists,
        "hot_limit_per_market": int(
            _bounded(universe_source.get("hot_limit_per_market", 8), 5, 20, "每市场热门候选数")
        ),
        "symbols": symbols,
    }

    factor_source = {**DEFAULT_FACTORS, **dict(source.get("factors") or {})}
    factors = {
        key: _bounded(factor_source.get(key), 0, 100, f"{FACTOR_LABELS[key]}权重")
        for key in FACTOR_LABELS
    }
    if sum(factors.values()) <= 0:
        raise ValueError("至少一个因子权重必须大于 0")

    gate_source = {**DEFAULT_GATES, **dict(source.get("gates") or {})}
    gates = {
        "min_history_days": int(_bounded(gate_source["min_history_days"], 60, 1000, "最少历史交易日")),
        "max_data_age_days": int(_bounded(gate_source["max_data_age_days"], 3, 45, "行情最大陈旧天数")),
        "min_technical_score": _bounded(gate_source["min_technical_score"], 0, 100, "最低技术评分"),
        "min_return_3m": _bounded(gate_source["min_return_3m"], -100, 300, "最低三月收益"),
        "max_annual_vol": _bounded(gate_source["max_annual_vol"], 5, 300, "最大年化波动"),
        "max_drawdown_pct": _bounded(gate_source["max_drawdown_pct"], 5, 100, "最大历史回撤"),
        "min_factor_coverage": _bounded(gate_source["min_factor_coverage"], 0.2, 1, "最低因子覆盖"),
        "min_composite_score": _bounded(gate_source["min_composite_score"], 0, 100, "最低综合评分"),
        "require_fundamentals": bool(gate_source["require_fundamentals"]),
    }
    portfolio_source = {**DEFAULT_PORTFOLIO, **dict(source.get("portfolio") or {})}
    weighting = str(portfolio_source.get("weighting") or "score_inverse_vol")
    if weighting not in {"score_inverse_vol", "inverse_vol", "equal"}:
        raise ValueError("不支持的组合权重方法")
    portfolio = {
        "max_positions": int(_bounded(portfolio_source["max_positions"], 2, 12, "最大持仓数")),
        "max_position_pct": _bounded(portfolio_source["max_position_pct"], 5, 50, "单股仓位上限"),
        "min_cash_pct": _bounded(portfolio_source["min_cash_pct"], 0, 60, "最低现金比例"),
        "max_pair_correlation": _bounded(portfolio_source["max_pair_correlation"], 0, 1, "最大两两相关性"),
        "defensive_cash_add_pct": _bounded(portfolio_source["defensive_cash_add_pct"], 0, 30, "弱势现金增量"),
        "weighting": weighting,
    }
    return {
        "template_id": str(source.get("template_id") or "custom")[:60],
        "name": name,
        "description": description,
        "markets": markets,
        "history_months": int(_bounded(source.get("history_months", 18), 9, 60, "历史月数")),
        "universe": universe,
        "factors": factors,
        "gates": gates,
        "portfolio": portfolio,
    }


def _resolve_universe(definition: dict[str, Any], user_id: str) -> dict[str, Any]:
    universe = definition["universe"]
    markets = definition["markets"]
    collected: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}

    def add(market: str, symbol: Any, name: Any, source: str) -> None:
        if market not in markets:
            return
        try:
            normalized = _normalize_symbol(market, symbol)
        except ValueError as error:
            warnings.append({"source": source, "message": str(error)})
            return
        key = (market, normalized)
        if key not in collected:
            collected[key] = {
                "market": market,
                "symbol": normalized,
                "name": str(name or normalized)[:80],
                "universe_sources": [],
            }
        if source not in collected[key]["universe_sources"]:
            collected[key]["universe_sources"].append(source)
            source_counts[source] = source_counts.get(source, 0) + 1

    for item in universe["symbols"]:
        add(item["market"], item["symbol"], item.get("name"), "manual")
    if universe["include_presets"]:
        for market in markets:
            for item in PRESETS.get(market, []):
                add(market, item["symbol"], item.get("name"), "curated_seed")
    if universe["include_watchlist"]:
        for item in storage.list_watchlist(user_id=user_id):
            add(item.get("market"), item.get("symbol"), item.get("name"), "watchlist")
    for market in markets:
        kinds = universe["hot_lists"]
        if not kinds:
            continue
        try:
            bundle = hot_stocks.get_hot_stock_bundle(
                market,
                kinds,
                limit=universe["hot_limit_per_market"],
            )
            for kind in kinds:
                for item in (bundle.get("rankings") or {}).get(kind) or []:
                    add(market, item.get("symbol"), item.get("name"), f"hot_{kind}")
            if bundle.get("degraded"):
                warnings.append(
                    {
                        "source": f"{market}:hot_provider",
                        "message": bundle.get("warning") or "热门榜正在使用公开降级源",
                    }
                )
        except Exception as error:
            # One market-level warning replaces three repeated gainers/losers/active
            # failures and keeps the strategy run usable when other sources exist.
            warnings.append(
                {"source": f"{market}:hot_provider", "message": str(error)[:500]}
            )
    items = list(collected.values())
    truncated = max(0, len(items) - MAX_UNIVERSE)
    if truncated:
        items = items[:MAX_UNIVERSE]
        warnings.append(
            {"source": "universe_limit", "message": f"候选池超过 {MAX_UNIVERSE} 只，已按来源顺序截断 {truncated} 只"}
        )
    if not items:
        raise ValueError("候选池为空；请添加手工股票或启用预设、自选、热门池")
    return {
        "scope": "candidate_pool",
        "scope_label": "候选池（非交易所全量）",
        "licensed_full_market": False,
        "items": items,
        "count": len(items),
        "source_counts": source_counts,
        "warnings": warnings,
        "truncated_count": truncated,
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _period_return(close: pd.Series, trading_days: int) -> float | None:
    if len(close) <= trading_days:
        return None
    base = float(close.iloc[-trading_days - 1])
    return round((float(close.iloc[-1]) / base - 1) * 100, 3) if base else None


def _max_drawdown(close: pd.Series) -> float:
    drawdown = close / close.cummax() - 1
    return round(float(drawdown.min() * 100), 3)


def _fundamental_metrics(result: dict[str, Any]) -> dict[str, float | None]:
    metrics = result.get("metrics") or {}
    return {
        "pe": _number(metrics.get("市盈率PE(TTM)") or metrics.get("市盈率PE")),
        "pb": _number(metrics.get("市净率PB")),
        "pe_percentile": _number(metrics.get("PE近五年分位%")),
        "pb_percentile": _number(metrics.get("PB近五年分位%")),
        "roe": _number(metrics.get("净资产收益率ROE%")),
        "gross_margin": _number(metrics.get("毛利率%")),
        "net_margin": _number(metrics.get("净利率%")),
        "debt_ratio": _number(metrics.get("资产负债率%")),
        "cashflow_quality": _number(metrics.get("现金流质量")),
        "revenue_growth": _number(metrics.get("营收同比%")),
        "profit_growth": _number(metrics.get("净利润同比%")),
        "revenue_streak": _number(metrics.get("营收连续增长年数")),
        "profit_streak": _number(metrics.get("净利润连续增长年数")),
    }


def _gate(code: str, label: str, actual: Any, threshold: str) -> dict[str, Any]:
    return {"code": code, "label": label, "actual": actual, "threshold": threshold}


def _evaluate_candidate(item: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    market = item["market"]
    symbol = item["symbol"]
    try:
        frame = data_fetch.get_history_months(
            market,
            symbol,
            definition["history_months"],
            fetch_months=definition["history_months"],
        )
        if frame is None or frame.empty:
            raise ValueError("历史行情为空")
        close = frame["close"].astype(float).dropna()
        if len(close) < 30:
            raise ValueError("有效历史交易日少于 30")
        scored = analysis.score_only(frame)
        daily_returns = close.pct_change().dropna()
        annual_vol = float(daily_returns.std() * np.sqrt(252) * 100) if len(daily_returns) > 1 else None
        downside = daily_returns[daily_returns < 0]
        downside_vol = float(downside.std() * np.sqrt(252) * 100) if len(downside) > 1 else None
        last_date = pd.Timestamp(frame["date"].iloc[-1]).date()
        age_days = max(0, (dt.date.today() - last_date).days)
        raw_metrics: dict[str, float | None] = {
            "return_1m": _period_return(close, 21),
            "return_3m": _period_return(close, 63),
            "return_6m": _period_return(close, 126),
            "technical_score": _number(scored.get("score")),
            "annual_vol": round(annual_vol, 3) if annual_vol is not None else None,
            "downside_vol": round(downside_vol, 3) if downside_vol is not None else None,
            "max_drawdown_abs": abs(_max_drawdown(close)),
        }
        fundamental_result: dict[str, Any]
        fundamental_error = None
        if market == "港股":
            fundamental_result = {"available": False, "message": "港股专业基本面供应商尚未接入"}
        else:
            try:
                fundamental_result = fundamentals.get_fundamentals(market, symbol)
            except Exception as error:
                fundamental_result = {"available": False, "message": str(error)[:180]}
        if not fundamental_result.get("available"):
            fundamental_error = str(fundamental_result.get("message") or "基本面不可用")
        raw_metrics.update(_fundamental_metrics(fundamental_result))
        gates = definition["gates"]
        disqualifiers = []
        if len(close) < gates["min_history_days"]:
            disqualifiers.append(_gate("history_too_short", "历史样本不足", len(close), f">={gates['min_history_days']} 个交易日"))
        if age_days > gates["max_data_age_days"]:
            disqualifiers.append(_gate("data_stale", "行情过旧", age_days, f"<={gates['max_data_age_days']} 天"))
        if raw_metrics["technical_score"] is None or raw_metrics["technical_score"] < gates["min_technical_score"]:
            disqualifiers.append(_gate("technical_below_gate", "技术评分未过线", raw_metrics["technical_score"], f">={gates['min_technical_score']}"))
        if raw_metrics["return_3m"] is None or raw_metrics["return_3m"] < gates["min_return_3m"]:
            disqualifiers.append(_gate("momentum_below_gate", "三月趋势未过线", raw_metrics["return_3m"], f">={gates['min_return_3m']}%"))
        if raw_metrics["annual_vol"] is None or raw_metrics["annual_vol"] > gates["max_annual_vol"]:
            disqualifiers.append(_gate("volatility_above_gate", "波动超过上限", raw_metrics["annual_vol"], f"<={gates['max_annual_vol']}%"))
        if raw_metrics["max_drawdown_abs"] > gates["max_drawdown_pct"]:
            disqualifiers.append(_gate("drawdown_above_gate", "历史回撤超过上限", raw_metrics["max_drawdown_abs"], f"<={gates['max_drawdown_pct']}%"))
        if gates["require_fundamentals"] and not fundamental_result.get("available"):
            disqualifiers.append(_gate("fundamentals_required", "基本面证据缺失", fundamental_error, "必须可用"))
        indexed_returns = daily_returns.copy()
        indexed_returns.index = pd.to_datetime(frame.loc[daily_returns.index, "date"]).dt.normalize()
        return {
            **item,
            "status": "evaluated",
            "data": {
                "history_days": len(close),
                "first_date": pd.Timestamp(frame["date"].iloc[0]).strftime("%Y-%m-%d"),
                "last_date": last_date.isoformat(),
                "age_days": age_days,
                "source": str(frame.attrs.get("source") or "source_not_exposed"),
                "retrieved_at": frame.attrs.get("retrieved_at"),
                "last_close": round(float(close.iloc[-1]), 4),
            },
            "technical": {
                "score": scored.get("score"),
                "direction": scored.get("direction"),
                "signal_integrity": scored.get("signal_integrity"),
            },
            "fundamentals": {
                "available": bool(fundamental_result.get("available")),
                "as_of": fundamental_result.get("as_of"),
                "source_error": fundamental_error,
                "provider_score": fundamental_result.get("score"),
                "provider_rating": fundamental_result.get("rating"),
            },
            "metrics": raw_metrics,
            "disqualifiers": disqualifiers,
            "_returns": indexed_returns,
        }
    except Exception as error:
        return {
            **item,
            "status": "unavailable",
            "error": str(error)[:500],
            "disqualifiers": [
                _gate("market_data_unavailable", "真实行情不可用", str(error)[:180], "必须成功读取真实历史行情")
            ],
        }


def _percentile_scores(values: list[tuple[int, float]], higher_is_better: bool) -> dict[int, float]:
    if not values:
        return {}
    if len(values) == 1:
        return {values[0][0]: 50.0}
    ordered = sorted(values, key=lambda pair: pair[1])
    output: dict[int, float] = {}
    index = 0
    count = len(ordered)
    while index < count:
        end = index + 1
        while end < count and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = ((index + 1) + end) / 2
        score = (average_rank - 0.5) / count * 100
        if not higher_is_better:
            score = 100 - score
        for position in range(index, end):
            output[ordered[position][0]] = round(score, 1)
        index = end
    return output


def _grade_candidates(candidates: list[dict[str, Any]], definition: dict[str, Any]) -> None:
    weights = definition["factors"]
    for market in definition["markets"]:
        indexes = [
            index for index, item in enumerate(candidates)
            if item.get("market") == market and item.get("status") == "evaluated"
        ]
        for factor, metrics in FACTOR_METRICS.items():
            for metric_key, metric_label, higher_is_better in metrics:
                values = []
                for index in indexes:
                    value = _number((candidates[index].get("metrics") or {}).get(metric_key))
                    if value is None:
                        continue
                    rank_value = value
                    if metric_key in {"pe", "pb"} and value <= 0:
                        # Loss-making/negative-book-value companies must not turn
                        # into a favourable low-multiple rank.  Keep the actual
                        # value visible, but rank it behind positive multiples.
                        rank_value = 1_000_000 + abs(value)
                    values.append((index, rank_value))
                scores = _percentile_scores(values, higher_is_better)
                for index, score in scores.items():
                    factors = candidates[index].setdefault("_factor_parts", {})
                    factors.setdefault(factor, []).append(
                        {
                            "metric": metric_key,
                            "label": metric_label,
                            "value": candidates[index]["metrics"][metric_key],
                            "peer_grade": score,
                            "higher_is_better": higher_is_better,
                            "peer_count": len(values),
                        }
                    )

    total_weight = sum(weights.values())
    for item in candidates:
        if item.get("status") != "evaluated":
            continue
        factor_results = {}
        available_weight = 0.0
        composite_points = 0.0
        for factor, weight in weights.items():
            parts = (item.get("_factor_parts") or {}).get(factor, [])
            score = round(sum(part["peer_grade"] for part in parts) / len(parts), 1) if parts else None
            if score is not None:
                available_weight += weight
            # Missing factors are explicitly neutral in the composite and remain
            # visible through coverage; they are never silently reweighted away.
            composite_points += weight * (score if score is not None else 50.0)
            factor_results[factor] = {
                "label": FACTOR_LABELS[factor],
                "score": score,
                "available": score is not None,
                "weight": weight,
                "parts": parts,
            }
        coverage = available_weight / total_weight if total_weight else 0.0
        composite = round(composite_points / total_weight, 1) if total_weight else None
        item["factors"] = factor_results
        item["factor_coverage"] = round(coverage, 3)
        item["composite_score"] = composite
        gates = definition["gates"]
        if coverage < gates["min_factor_coverage"]:
            item["disqualifiers"].append(
                _gate("factor_coverage_below_gate", "因子证据覆盖不足", round(coverage * 100, 1), f">={gates['min_factor_coverage'] * 100:.0f}%")
            )
        hard_failed = bool(item["disqualifiers"])
        if hard_failed:
            item["status"] = "rejected"
        elif composite is None or composite < gates["min_composite_score"]:
            item["status"] = "watch"
            item["disqualifiers"].append(
                _gate("composite_below_gate", "综合评分未过线", composite, f">={gates['min_composite_score']}")
            )
        else:
            item["status"] = "qualified"
        available = [value for value in factor_results.values() if value["score"] is not None]
        strongest = sorted(available, key=lambda value: value["score"], reverse=True)[:2]
        weakest = sorted(available, key=lambda value: value["score"])[:2]
        item["strengths"] = [f"{value['label']}同市场分位 {value['score']:.1f}" for value in strongest]
        item["concerns"] = [f"{value['label']}同市场分位 {value['score']:.1f}" for value in weakest if value["score"] < 45]
        missing = [value["label"] for value in factor_results.values() if value["score"] is None]
        if missing:
            item["concerns"].append("缺少:" + "、".join(missing))
        item.pop("_factor_parts", None)

    ranked = sorted(
        [item for item in candidates if item.get("composite_score") is not None],
        key=lambda item: (-float(item["composite_score"]), -float(item.get("factor_coverage") or 0), item["market"], item["symbol"]),
    )
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank


def _market_regimes(candidates: list[dict[str, Any]], markets: list[str]) -> list[dict[str, Any]]:
    output = []
    for market in markets:
        rows = [item for item in candidates if item.get("market") == market and item.get("status") != "unavailable"]
        returns = [item["metrics"]["return_3m"] for item in rows if item.get("metrics", {}).get("return_3m") is not None]
        vols = [item["metrics"]["annual_vol"] for item in rows if item.get("metrics", {}).get("annual_vol") is not None]
        if len(returns) < 2:
            output.append({"market": market, "status": "insufficient", "label": "样本不足", "sample_count": len(returns)})
            continue
        median_return = float(median(returns))
        breadth = sum(1 for value in returns if value > 0) / len(returns) * 100
        if median_return > 5 and breadth >= 60:
            status, label = "risk_on", "偏强"
        elif median_return < -5 or breadth < 40:
            status, label = "defensive", "防守"
        else:
            status, label = "mixed", "震荡"
        output.append(
            {
                "market": market,
                "status": status,
                "label": label,
                "sample_count": len(returns),
                "median_return_3m": round(median_return, 2),
                "positive_breadth_pct": round(breadth, 1),
                "median_annual_vol": round(float(median(vols)), 2) if vols else None,
                "method": "候选池三月收益中位数与上涨广度；不是全市场状态",
            }
        )
    return output


def _pair_correlation(a: dict[str, Any], b: dict[str, Any]) -> tuple[float | None, int]:
    joined = pd.concat([a["_returns"].rename("a"), b["_returns"].rename("b")], axis=1, join="inner").dropna()
    if len(joined) < 40:
        return None, len(joined)
    value = float(joined["a"].corr(joined["b"]))
    return (round(value, 3), len(joined)) if math.isfinite(value) else (None, len(joined))


def _capped_weights(raw: list[float], target: float, cap: float) -> list[float]:
    weights = [0.0 for _ in raw]
    active = set(range(len(raw)))
    remaining = max(0.0, target)
    while active and remaining > 1e-10:
        total_raw = sum(max(0.0, raw[index]) for index in active)
        tentative = {
            index: remaining * (max(0.0, raw[index]) / total_raw if total_raw else 1 / len(active))
            for index in active
        }
        capped = [index for index, value in tentative.items() if value > cap + 1e-10]
        if not capped:
            for index, value in tentative.items():
                weights[index] += value
            break
        for index in capped:
            room = max(0.0, cap - weights[index])
            weights[index] += room
            remaining -= room
            active.remove(index)
        if remaining <= 1e-10:
            break
    return weights


def _portfolio_proposal(
    candidates: list[dict[str, Any]],
    definition: dict[str, Any],
    regimes: list[dict[str, Any]],
) -> dict[str, Any]:
    policy = definition["portfolio"]
    qualified = sorted(
        [item for item in candidates if item.get("status") == "qualified"],
        key=lambda item: item.get("rank") or 10_000,
    )
    selected: list[dict[str, Any]] = []
    correlation_exclusions = []
    correlation_rows = []
    for item in qualified:
        conflicts = []
        for current in selected:
            value, overlap = _pair_correlation(item, current)
            correlation_rows.append(
                {"a": current["market"] + ":" + current["symbol"], "b": item["market"] + ":" + item["symbol"], "correlation": value, "overlap_days": overlap}
            )
            if value is not None and value > policy["max_pair_correlation"]:
                conflicts.append({"with": current["market"] + ":" + current["symbol"], "correlation": value})
        if conflicts:
            correlation_exclusions.append(
                {"market": item["market"], "symbol": item["symbol"], "reason": "pair_correlation_above_limit", "conflicts": conflicts}
            )
            continue
        selected.append(item)
        if len(selected) >= policy["max_positions"]:
            break

    regime_map = {item["market"]: item for item in regimes}
    selected_markets = {item["market"] for item in selected}
    defensive_count = sum(1 for market in selected_markets if regime_map.get(market, {}).get("status") == "defensive")
    defensive_applied = bool(selected_markets and defensive_count / len(selected_markets) >= 0.5)
    cash_target = policy["min_cash_pct"] + (policy["defensive_cash_add_pct"] if defensive_applied else 0)
    cash_target = min(70.0, cash_target)
    target_invested = max(0.0, 100.0 - cash_target)
    raw = []
    for item in selected:
        vol = max(1.0, float(item["metrics"].get("annual_vol") or 100))
        if policy["weighting"] == "equal":
            value = 1.0
        elif policy["weighting"] == "inverse_vol":
            value = 1 / vol
        else:
            value = max(1.0, float(item.get("composite_score") or 0)) / vol
        raw.append(value)
    weights = _capped_weights(raw, target_invested, policy["max_position_pct"]) if selected else []
    invested = sum(weights)
    positions = []
    for item, weight in zip(selected, weights):
        positions.append(
            {
                "market": item["market"],
                "symbol": item["symbol"],
                "name": item.get("name") or item["symbol"],
                "weight_pct": round(weight, 2),
                "composite_score": item.get("composite_score"),
                "factor_coverage": item.get("factor_coverage"),
                "annual_vol": item["metrics"].get("annual_vol"),
                "entry_price": item["data"].get("last_close"),
                "entry_date": item["data"].get("last_date"),
                "price_source": item["data"].get("source"),
            }
        )
    estimated_vol = None
    aligned_days = 0
    if len(selected) >= 2 and weights:
        matrix = pd.concat(
            [item["_returns"].rename(f"p{index}") for index, item in enumerate(selected)],
            axis=1,
            join="inner",
        ).dropna()
        aligned_days = len(matrix)
        if aligned_days >= 40:
            vector = np.array(weights, dtype=float) / 100
            covariance = matrix.cov().to_numpy(dtype=float) * 252
            variance = float(vector @ covariance @ vector.T)
            if variance >= 0 and math.isfinite(variance):
                estimated_vol = round(math.sqrt(variance) * 100, 2)
    market_weights = {}
    for position in positions:
        market_weights[position["market"]] = round(
            market_weights.get(position["market"], 0) + position["weight_pct"], 2
        )
    warnings = [
        "跨市场纸面权重未换算汇率，组合收益只能解释为各本币收益的权重近似。",
        "未计入佣金、税费、整手、涨跌停、停牌、市场冲击和真实成交偏差。",
        "历史相关性与波动率会变化，不是未来风险上限。",
    ]
    if len(selected) < 2:
        warnings.insert(0, "通过门槛且满足相关性约束的股票少于 2 只，不能形成分散组合。")
    if invested + 1e-6 < target_invested:
        warnings.insert(0, "单股仓位上限限制了总投入，未分配部分继续保留为纸面现金。")
    return {
        "status": "ready" if len(selected) >= 2 else "insufficient_diversification",
        "method": policy["weighting"],
        "positions": positions,
        "position_count": len(positions),
        "cash_pct": round(100 - invested, 2),
        "base_cash_pct": policy["min_cash_pct"],
        "defensive_cash_applied": defensive_applied,
        "estimated_annual_vol_pct": estimated_vol,
        "covariance_aligned_days": aligned_days,
        "market_weights": market_weights,
        "correlations": correlation_rows,
        "correlation_exclusions": correlation_exclusions,
        "constraints": policy,
        "warnings": warnings,
    }


def _run_comparison(current: list[dict[str, Any]], prior_run: dict[str, Any] | None) -> dict[str, Any]:
    if not prior_run or not prior_run.get("result_verified") or not prior_run.get("result"):
        return {"available": False, "reason": "没有同策略的上一期完整结果"}
    previous = prior_run["result"].get("candidates") or []
    current_qualified = {f"{item['market']}:{item['symbol']}": item for item in current if item.get("status") == "qualified"}
    prior_qualified = {f"{item['market']}:{item['symbol']}": item for item in previous if item.get("status") == "qualified"}
    entered = sorted(set(current_qualified) - set(prior_qualified))
    exited = sorted(set(prior_qualified) - set(current_qualified))
    retained = sorted(set(current_qualified) & set(prior_qualified))
    rank_changes = []
    for key in retained:
        current_rank = current_qualified[key].get("rank")
        prior_rank = prior_qualified[key].get("rank")
        rank_changes.append(
            {"key": key, "current_rank": current_rank, "prior_rank": prior_rank, "change": (prior_rank - current_rank) if current_rank and prior_rank else None}
        )
    return {
        "available": True,
        "prior_run_id": prior_run["id"],
        "prior_completed_at": prior_run.get("completed_at"),
        "entered": entered,
        "exited": exited,
        "retained": retained,
        "rank_changes": sorted(rank_changes, key=lambda item: item["key"]),
    }


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def execute_run(
    run_id: str,
    *,
    user_id: str,
    repo: OpportunityRepository = repository,
    actor_id: str = "opportunity-worker",
) -> dict[str, Any]:
    run = repo.get_run(run_id, user_id=user_id, include_events=False)
    if run is None:
        raise OpportunityNotFoundError("机会扫描不存在")
    if run["status"] in {"succeeded", "partial"}:
        return run
    version = repo.get_strategy_version(run["strategy_version_id"], user_id=user_id)
    if version is None or not version.get("definition_verified"):
        raise OpportunityConflictError("扫描绑定的策略版本不存在或完整性校验失败")
    if version["definition_sha256"] != run["strategy_sha256"]:
        raise OpportunityConflictError("扫描绑定的策略摘要不一致")
    definition = normalize_definition(version["definition"])
    try:
        repo.mark_running(run_id, user_id=user_id, actor_id=actor_id)
        universe = _resolve_universe(definition, user_id)
        repo.update_progress(
            run_id,
            user_id=user_id,
            progress={"stage": "market_data", "completed": 0, "total": universe["count"], "message": "正在读取真实行情与基本面"},
        )
        candidates = []
        completed = 0
        with ThreadPoolExecutor(max_workers=min(8, universe["count"])) as pool:
            futures = {
                pool.submit(_evaluate_candidate, item, definition): item
                for item in universe["items"]
            }
            for future in as_completed(futures):
                candidates.append(future.result())
                completed += 1
                source_item = futures[future]
                repo.update_progress(
                    run_id,
                    user_id=user_id,
                    progress={
                        "stage": "market_data",
                        "completed": completed,
                        "total": universe["count"],
                        "message": f"已处理 {source_item['market']} {source_item['symbol']}",
                    },
                )
        candidates.sort(key=lambda item: (definition["markets"].index(item["market"]), item["symbol"]))
        repo.update_progress(
            run_id,
            user_id=user_id,
            progress={"stage": "ranking", "completed": completed, "total": universe["count"], "message": "正在计算同市场分位和组合约束"},
        )
        _grade_candidates(candidates, definition)
        regimes = _market_regimes(candidates, definition["markets"])
        portfolio = _portfolio_proposal(candidates, definition, regimes)
        prior = repo.get_prior_completed_run(
            strategy_id=run["strategy_id"], before_run_id=run_id, user_id=user_id
        )
        unavailable = sum(1 for item in candidates if item.get("status") == "unavailable")
        rejected = sum(1 for item in candidates if item.get("status") == "rejected")
        watch = sum(1 for item in candidates if item.get("status") == "watch")
        qualified = sum(1 for item in candidates if item.get("status") == "qualified")
        public_candidates = sorted(
            [_public_candidate(item) for item in candidates],
            key=lambda item: (item.get("rank") or 100_000, item["market"], item["symbol"]),
        )
        result = {
            "schema_version": "opportunity_run_result.v1",
            "policy_version": POLICY_VERSION,
            "run_id": run_id,
            "strategy": {
                "id": run["strategy_id"],
                "version_id": run["strategy_version_id"],
                "version_no": run["strategy_version_no"],
                "sha256": run["strategy_sha256"],
                "name": definition["name"],
                "definition": definition,
            },
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "universe": {key: value for key, value in universe.items() if key != "items"},
            "funnel": {
                "universe": universe["count"],
                "evaluated": universe["count"] - unavailable,
                "unavailable": unavailable,
                "hard_rejected": rejected,
                "watch": watch,
                "qualified": qualified,
                "portfolio": portfolio["position_count"],
            },
            "market_regimes": regimes,
            "candidates": public_candidates,
            "portfolio": portfolio,
            "comparison": _run_comparison(public_candidates, prior),
            "methodology": {
                "peer_scope": "每个市场内、仅本次候选池横向分位；不是行业中性或交易所全量分位",
                "missing_factor_treatment": "缺失因子按中性 50 分进入综合分，同时由覆盖率门槛单独否决",
                "ranking": "先通过数据新鲜度、历史长度、趋势、波动、回撤和基本面硬门槛，再按加权同市场因子分排序",
                "portfolio": "按综合分与历史波动形成原始权重，受现金、单股上限和两两相关性约束",
            },
            "limitations": [
                "候选池来自预设、自选、手工和可用热门榜，不代表交易所全量股票。",
                "没有有授权的历史成分股库，无法消除幸存者偏差。",
                "因子分和市场状态只描述本次可用历史证据，不保证未来涨跌。",
                "港股基本面在专业供应商接入前保持缺失，不使用估算或其他市场字段代替。",
                "纸面组合不连接券商、不提交订单，也不是收益承诺。",
            ],
        }
        final_status = "partial" if unavailable or universe["warnings"] else "succeeded"
        return repo.complete_run(
            run_id,
            user_id=user_id,
            result=result,
            status=final_status,
            actor_id=actor_id,
        )
    except Exception as error:
        try:
            repo.fail_run(
                run_id,
                user_id=user_id,
                error_code="OPPORTUNITY_RUN_FAILED",
                error_message=str(error),
                actor_id=actor_id,
            )
        except OpportunityConflictError:
            pass
        raise


def start_run(
    strategy_id: str,
    *,
    user_id: str,
    actor_id: str,
    tenant_id: str = "public",
    repo: OpportunityRepository = repository,
) -> dict[str, Any]:
    run = repo.create_run(strategy_id, user_id=user_id, actor_id=actor_id)
    if not uses_celery_queue():
        return execute_run(run["id"], user_id=user_id, repo=repo, actor_id="embedded-opportunity-worker")
    jobs = BackgroundJobRepository()
    try:
        job, _ = jobs.create_job(
            job_type="opportunity_scan",
            queue_name=QUEUE_MARKET,
            payload={"run_id": run["id"], "user_id": user_id},
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=run["id"],
            max_attempts=1,
        )
        repo.bind_job(run["id"], str(job["id"]), user_id=user_id)
        enqueue_background_job(job, jobs)
    except Exception as error:
        repo.fail_run(
            run["id"],
            user_id=user_id,
            error_code="OPPORTUNITY_QUEUE_UNAVAILABLE",
            error_message=str(error),
            actor_id="api",
        )
        if isinstance(error, TaskQueueUnavailableError):
            raise
        raise
    refreshed = repo.get_run(run["id"], user_id=user_id)
    return refreshed or run


def refresh_run_status(
    run_id: str, *, user_id: str, repo: OpportunityRepository = repository
) -> dict[str, Any] | None:
    run = repo.get_run(run_id, user_id=user_id)
    if not run or run["status"] not in {"queued", "running"} or not run.get("job_id"):
        return run
    job = BackgroundJobRepository().get_job(str(run["job_id"]), include_payload=False)
    if job and job.get("status") in {"failed", "cancelled"}:
        return repo.fail_run(
            run_id,
            user_id=user_id,
            error_code=str(job.get("error_code") or "OPPORTUNITY_JOB_FAILED"),
            error_message=str(job.get("error_message") or "机会扫描后台任务失败"),
            actor_id="api-reconciler",
        )
    return run


def create_paper_basket(
    run_id: str,
    *,
    user_id: str,
    repo: OpportunityRepository = repository,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    run = repo.get_run(run_id, user_id=user_id)
    if run is None:
        raise OpportunityNotFoundError("机会扫描不存在")
    if not run.get("result_verified") or not run.get("result"):
        raise OpportunityConflictError("扫描结果不可用或完整性校验失败")
    result = run["result"]
    portfolio = result.get("portfolio") or {}
    positions = portfolio.get("positions") or []
    if not positions:
        raise OpportunityConflictError("本次扫描没有可冻结的纸面持仓")
    frozen_at = now or dt.datetime.now(dt.timezone.utc)
    if frozen_at.tzinfo is None:
        frozen_at = frozen_at.replace(tzinfo=dt.timezone.utc)
    snapshot = {
        "schema_version": "opportunity_paper_basket.v1",
        "run_id": run_id,
        "run_result_sha256": run["result_sha256"],
        "strategy": result.get("strategy"),
        "frozen_at": frozen_at.astimezone(dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "market_regimes": result.get("market_regimes") or [],
        "positions": positions,
        "cash_pct": portfolio.get("cash_pct"),
        "data_basis": "复权日线最新收盘价；后续观察继续使用同口径真实历史接口",
        "regime_basis": (
            "冻结本次扫描的候选池市场状态；不是交易所全市场状态，"
            "后续不会用新行情改写"
        ),
        "limitations": portfolio.get("warnings") or [],
    }
    return repo.create_paper_basket(run_id=run_id, user_id=user_id, snapshot=snapshot)


def observe_paper_basket(
    basket_id: str,
    *,
    user_id: str,
    repo: OpportunityRepository = repository,
    history_loader: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, Any]:
    basket = repo.get_paper_basket(basket_id, user_id=user_id)
    if basket is None:
        raise OpportunityNotFoundError("纸面组合不存在")
    if not basket.get("snapshot_verified"):
        raise OpportunityConflictError("纸面组合快照完整性校验失败")
    positions = basket["snapshot"].get("positions") or []
    loader = history_loader or data_fetch.get_history_months

    def normalized_history(market: str, symbol: str, entry_date: str) -> pd.DataFrame:
        try:
            parsed_entry = dt.date.fromisoformat(str(entry_date)[:10])
            age_days = max(0, (dt.datetime.now(dt.timezone.utc).date() - parsed_entry).days)
        except ValueError:
            age_days = 180
        months = max(6, min(24, int(math.ceil(age_days / 30)) + 3))
        frame = loader(market, symbol, months, fetch_months=months).copy()
        if frame.empty:
            raise ValueError("真实历史行情为空")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["date", "close"]).sort_values("date")
        if frame.empty:
            raise ValueError("真实历史行情缺少有效日期或收盘价")
        return frame

    def observe(position: dict[str, Any]) -> dict[str, Any]:
        try:
            entry_date = str(position.get("entry_date") or "")[:10]
            frame = normalized_history(
                str(position["market"]), str(position["symbol"]), entry_date
            )
            entry_timestamp = pd.Timestamp(entry_date)
            current = frame.iloc[-1]
            current_price = float(current["close"])
            current_date = pd.Timestamp(current["date"]).strftime("%Y-%m-%d")
            entry = float(position["entry_price"])
            return_pct = (current_price / entry - 1) * 100 if entry else None
            weight = float(position["weight_pct"])
            future_rows = frame[frame["date"] > entry_timestamp].reset_index(
                drop=True
            )
            elapsed = len(future_rows)
            horizon_returns: dict[str, dict[str, Any]] = {}
            for horizon in PAPER_VALIDATION_HORIZONS:
                if elapsed < horizon:
                    horizon_returns[str(horizon)] = {
                        "trading_days": horizon,
                        "status": "pending",
                        "trading_days_observed": elapsed,
                    }
                    continue
                outcome = future_rows.iloc[horizon - 1]
                outcome_price = float(outcome["close"])
                outcome_return = (
                    (outcome_price / entry - 1) * 100 if entry else None
                )
                path_prices = [entry] + [
                    float(value)
                    for value in future_rows.iloc[:horizon]["close"].tolist()
                ]
                peak = path_prices[0] if path_prices else 0.0
                worst_drawdown = 0.0
                for price in path_prices:
                    peak = max(peak, price)
                    if peak > 0:
                        worst_drawdown = min(
                            worst_drawdown, price / peak - 1
                        )
                horizon_returns[str(horizon)] = {
                    "trading_days": horizon,
                    "status": "available",
                    "outcome_date": pd.Timestamp(outcome["date"]).strftime(
                        "%Y-%m-%d"
                    ),
                    "outcome_price": round(outcome_price, 4),
                    "return_pct": (
                        round(outcome_return, 3)
                        if outcome_return is not None
                        else None
                    ),
                    "contribution_pct": (
                        round(weight / 100 * outcome_return, 3)
                        if outcome_return is not None
                        else None
                    ),
                    "max_drawdown_pct": round(
                        abs(worst_drawdown) * 100, 3
                    ),
                }
            return {
                "market": position["market"],
                "symbol": position["symbol"],
                "name": position.get("name"),
                "weight_pct": weight,
                "entry_price": entry,
                "entry_date": position.get("entry_date"),
                "current_price": round(current_price, 4),
                "current_date": current_date,
                "trading_days_elapsed": elapsed,
                "horizon_returns": horizon_returns,
                "return_pct": round(return_pct, 3) if return_pct is not None else None,
                "contribution_pct": round(weight / 100 * return_pct, 3) if return_pct is not None else None,
                "source": str(frame.attrs.get("source") or "source_not_exposed"),
                "status": "available",
            }
        except Exception as error:
            return {
                "market": position["market"],
                "symbol": position["symbol"],
                "name": position.get("name"),
                "weight_pct": position.get("weight_pct"),
                "status": "unavailable",
                "error": str(error)[:300],
            }

    benchmark_markets = sorted(
        {
            str(position.get("market") or "")
            for position in positions
            if str(position.get("market") or "") in PAPER_BENCHMARKS
        }
    )

    def load_benchmark(market: str) -> tuple[str, dict[str, Any]]:
        benchmark = PAPER_BENCHMARKS[market]
        entry_dates = [
            str(item.get("entry_date") or "")[:10]
            for item in positions
            if item.get("market") == market
        ]
        earliest = min((value for value in entry_dates if value), default="")
        try:
            frame = normalized_history(market, benchmark["symbol"], earliest)
            return market, {
                **benchmark,
                "market": market,
                "frame": frame,
                "source": str(frame.attrs.get("source") or "source_not_exposed"),
                "status": "available",
            }
        except Exception as error:
            return market, {
                **benchmark,
                "market": market,
                "status": "unavailable",
                "error": str(error)[:300],
            }

    with ThreadPoolExecutor(
        max_workers=min(10, max(1, len(positions) + len(benchmark_markets)))
    ) as pool:
        position_futures = [pool.submit(observe, position) for position in positions]
        benchmark_futures = [
            pool.submit(load_benchmark, market) for market in benchmark_markets
        ]
        observations = [future.result() for future in position_futures]
        benchmarks = dict(future.result() for future in benchmark_futures)

    benchmark_contribution = 0.0
    benchmark_coverage = 0.0
    for item in observations:
        if item.get("status") != "available":
            continue
        benchmark = benchmarks.get(str(item.get("market") or "")) or {}
        frame = benchmark.get("frame")
        if benchmark.get("status") != "available" or not isinstance(frame, pd.DataFrame):
            item["benchmark"] = {
                key: value for key, value in benchmark.items() if key != "frame"
            }
            continue
        entry_timestamp = pd.Timestamp(str(item.get("entry_date") or "")[:10])
        current_timestamp = pd.Timestamp(str(item.get("current_date") or "")[:10])
        baseline_rows = frame[frame["date"] <= entry_timestamp]
        current_rows = frame[frame["date"] <= current_timestamp]
        if baseline_rows.empty or current_rows.empty:
            item["benchmark"] = {
                **{key: value for key, value in benchmark.items() if key != "frame"},
                "status": "unavailable",
                "error": "基准在冻结日之前没有可用收盘价",
            }
            continue
        baseline = baseline_rows.iloc[-1]
        current = current_rows.iloc[-1]
        baseline_price = float(baseline["close"])
        current_price = float(current["close"])
        benchmark_return = (
            (current_price / baseline_price - 1) * 100 if baseline_price else None
        )
        weight = float(item.get("weight_pct") or 0)
        contribution = (
            weight / 100 * benchmark_return
            if benchmark_return is not None
            else None
        )
        item["benchmark"] = {
            "market": benchmark["market"],
            "symbol": benchmark["symbol"],
            "name": benchmark["name"],
            "entry_date": pd.Timestamp(baseline["date"]).strftime("%Y-%m-%d"),
            "entry_price": round(baseline_price, 4),
            "current_date": pd.Timestamp(current["date"]).strftime("%Y-%m-%d"),
            "current_price": round(current_price, 4),
            "return_pct": round(benchmark_return, 3),
            "contribution_pct": round(contribution, 3),
            "source": benchmark["source"],
            "status": "available",
        }
        benchmark_coverage += weight
        benchmark_contribution += contribution or 0.0

        benchmark_future = frame[
            frame["date"] > pd.Timestamp(baseline["date"])
        ].reset_index(drop=True)
        for horizon in PAPER_VALIDATION_HORIZONS:
            position_horizon = (item.get("horizon_returns") or {}).get(
                str(horizon)
            ) or {}
            if (
                position_horizon.get("status") != "available"
                or len(benchmark_future) < horizon
            ):
                continue
            horizon_row = benchmark_future.iloc[horizon - 1]
            horizon_price = float(horizon_row["close"])
            horizon_return = (
                (horizon_price / baseline_price - 1) * 100
                if baseline_price
                else None
            )
            position_horizon["benchmark"] = {
                "status": (
                    "available" if horizon_return is not None else "unavailable"
                ),
                "outcome_date": pd.Timestamp(
                    horizon_row["date"]
                ).strftime("%Y-%m-%d"),
                "outcome_price": round(horizon_price, 4),
                "return_pct": (
                    round(horizon_return, 3)
                    if horizon_return is not None
                    else None
                ),
                "contribution_pct": (
                    round(weight / 100 * horizon_return, 3)
                    if horizon_return is not None
                    else None
                ),
            }

    available = [item for item in observations if item["status"] == "available"]
    coverage_weight = sum(float(item["weight_pct"]) for item in available)
    invested_weight = sum(float(item.get("weight_pct") or 0) for item in positions)
    weighted_return = sum(float(item["contribution_pct"]) for item in available)
    cost_drag = invested_weight / 100 * PAPER_COST_SCENARIO_BPS / 100
    net_return = weighted_return - cost_drag
    net_excess = net_return - benchmark_contribution
    elapsed_values = [
        int(item.get("trading_days_elapsed") or 0) for item in available
    ]
    elapsed_min = min(elapsed_values) if elapsed_values else 0
    elapsed_max = max(elapsed_values) if elapsed_values else 0
    horizon_status = []
    for horizon in PAPER_VALIDATION_HORIZONS:
        position_coverage = 0.0
        horizon_benchmark_coverage = 0.0
        gross_horizon_return = 0.0
        benchmark_horizon_return = 0.0
        conservative_drawdown = 0.0
        outcome_dates = []
        for item in available:
            metric = (item.get("horizon_returns") or {}).get(
                str(horizon)
            ) or {}
            if metric.get("status") != "available":
                continue
            weight = float(item.get("weight_pct") or 0)
            position_coverage += weight
            gross_horizon_return += float(
                metric.get("contribution_pct") or 0
            )
            conservative_drawdown += (
                weight / 100 * float(metric.get("max_drawdown_pct") or 0)
            )
            if metric.get("outcome_date"):
                outcome_dates.append(str(metric["outcome_date"]))
            benchmark_metric = metric.get("benchmark") or {}
            if benchmark_metric.get("status") == "available":
                horizon_benchmark_coverage += weight
                benchmark_horizon_return += float(
                    benchmark_metric.get("contribution_pct") or 0
                )
        horizon_cost_drag = (
            invested_weight / 100 * PAPER_COST_SCENARIO_BPS / 100
        )
        exact_complete = bool(
            position_coverage >= 90
            and horizon_benchmark_coverage >= 90
        )
        horizon_status.append(
            {
                "trading_days": horizon,
                "status": (
                    "complete"
                    if exact_complete
                    else "partial"
                    if position_coverage or horizon_benchmark_coverage
                    else "pending"
                ),
                "complete": exact_complete,
                "exact_horizon": True,
                "gross_weighted_return_pct": round(
                    gross_horizon_return, 3
                ),
                "round_trip_cost_scenario_bps": PAPER_COST_SCENARIO_BPS,
                "cost_drag_pct": round(horizon_cost_drag, 3),
                "net_return_after_cost_pct": round(
                    gross_horizon_return - horizon_cost_drag, 3
                ),
                "benchmark_return_pct": round(
                    benchmark_horizon_return, 3
                ),
                "net_excess_return_pct": round(
                    gross_horizon_return
                    - horizon_cost_drag
                    - benchmark_horizon_return,
                    3,
                ),
                "covered_position_weight_pct": round(
                    position_coverage, 2
                ),
                "benchmark_coverage_weight_pct": round(
                    horizon_benchmark_coverage, 2
                ),
                "invested_weight_pct": round(invested_weight, 2),
                "conservative_component_drawdown_pct": round(
                    conservative_drawdown, 3
                ),
                "outcome_date_min": min(outcome_dates)
                if outcome_dates
                else None,
                "outcome_date_max": max(outcome_dates)
                if outcome_dates
                else None,
            }
        )
    observed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    observation_key_basis = {
        "basket_id": basket_id,
        "snapshot_sha256": basket.get("snapshot_sha256"),
        "positions": sorted(
            [
                {
                    "market": item.get("market"),
                    "symbol": item.get("symbol"),
                    "current_date": item.get("current_date"),
                    "current_price": item.get("current_price"),
                    "benchmark_date": (item.get("benchmark") or {}).get(
                        "current_date"
                    ),
                    "horizon_outcomes": {
                        key: {
                            "date": value.get("outcome_date"),
                            "price": value.get("outcome_price"),
                            "benchmark_date": (
                                value.get("benchmark") or {}
                            ).get("outcome_date"),
                            "benchmark_price": (
                                value.get("benchmark") or {}
                            ).get("outcome_price"),
                        }
                        for key, value in (
                            item.get("horizon_returns") or {}
                        ).items()
                        if value.get("status") == "available"
                    },
                }
                for item in observations
            ],
            key=lambda item: (str(item["market"]), str(item["symbol"])),
        ),
    }
    idempotency_key = hashlib.sha256(
        json.dumps(
            observation_key_basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema_version": "opportunity_paper_observation.v2",
        "observed_at": observed_at,
        "observation_key": idempotency_key,
        "status": (
            "complete"
            if (
                len(available) == len(observations)
                and benchmark_coverage + 1e-6 >= invested_weight
            )
            else "partial"
        ),
        "gross_weighted_return_pct": round(weighted_return, 3),
        "weighted_return_pct": round(weighted_return, 3),
        "round_trip_cost_scenario_bps": PAPER_COST_SCENARIO_BPS,
        "cost_drag_pct": round(cost_drag, 3),
        "net_return_after_cost_pct": round(net_return, 3),
        "benchmark_return_pct": round(benchmark_contribution, 3),
        "net_excess_return_pct": round(net_excess, 3),
        "covered_position_weight_pct": round(coverage_weight, 2),
        "benchmark_coverage_weight_pct": round(benchmark_coverage, 2),
        "invested_weight_pct": round(invested_weight, 2),
        "cash_pct": basket["snapshot"].get("cash_pct"),
        "observed_trading_days_min": elapsed_min,
        "observed_trading_days_max": elapsed_max,
        "horizons": horizon_status,
        "max_horizon_complete": bool(
            horizon_status and horizon_status[-1]["complete"]
        ),
        "positions": observations,
        "benchmarks": [
            {key: value for key, value in item.items() if key != "frame"}
            for item in benchmarks.values()
        ],
        "failed_count": len(observations) - len(available),
        "method": (
            "各标的按冻结后第 5/20/60 个真实交易日取本币复权收盘价并乘冻结权重；"
            "市场基准从同一冻结基线按相同交易日序号计算；成本后结果扣除冻结投入仓位"
            "的往返成本情景，现金按 0 处理"
        ),
        "limitations": [
            "跨市场收益仍是各本币收益加权近似，未计入持有期汇率变化。",
            "30 bps 是统一往返成本压力情景，不代表任何券商、市场或账户的实际收费。",
            "未模拟整手、涨跌停、停牌、成交冲击、分红税和资金利息。",
            "数据源切换或复权口径修订可能影响历史可比性。",
        ],
    }
    return repo.append_paper_observation(
        basket_id,
        user_id=user_id,
        observed_at=observed_at,
        payload=payload,
        idempotency_key=idempotency_key,
    )
