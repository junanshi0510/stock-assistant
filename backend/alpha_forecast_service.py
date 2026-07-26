# -*- coding: utf-8 -*-
"""Application service for calibrated multi-horizon Alpha research."""

from __future__ import annotations

import datetime as dt
import math
import re
import uuid
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

import data_fetch
import funds
from background_jobs import sanitize_worker_error
from alpha_forecast_engine import (
    ENGINE_VERSION,
    FEATURE_SET_VERSION,
    MODEL_FAMILY,
    run_alpha_forecast_research,
)
from alpha_forecast_repository import (
    AlphaForecastConflict,
    AlphaForecastNotFound,
    AlphaForecastRepository,
    TERMINAL_RUN_STATUSES,
    repository,
)
from task_queue import (
    TaskQueueUnavailableError,
    enqueue_alpha_forecast_run,
    enqueue_alpha_forecast_settlement,
    uses_celery_queue,
)


POLICY_VERSION = "alpha_forecast_policy@1.0.0"
DEFAULT_BENCHMARKS = {
    "A股": "000300.SH",
    "港股": "02800",
    "美股": "SPY",
}
SOURCE_TIER_RANK = {
    "unknown": 0,
    "public_fallback": 1,
    "research_grade": 2,
    "professional": 3,
}


def _today() -> dt.date:
    return dt.date.today()


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _classify_source(
    source: Any,
    *,
    confirmed_nav_only: bool = False,
) -> str:
    normalized = str(source or "").strip().lower()
    if any(
        token in normalized
        for token in (
            "tushare",
            "polygon",
            "massive",
            "alpha vantage",
            "alphavantage",
            "futu",
            "富途",
        )
    ):
        return "professional"
    if "baostock" in normalized or confirmed_nav_only:
        return "research_grade"
    if any(
        token in normalized
        for token in (
            "yahoo",
            "腾讯",
            "东方财富",
            "天天基金",
            "eastmoney",
        )
    ):
        return "public_fallback"
    return "unknown"


def _source_release_gate(
    *,
    policy: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    loaded_symbols: set[str],
) -> dict[str, Any]:
    requested = {
        str(item["symbol"]) for item in (policy.get("symbols") or [])
    }
    asset_rows = []
    for symbol in sorted(loaded_symbols):
        evidence = sources.get(symbol) or {}
        tier = str(evidence.get("provider_tier") or "unknown")
        asset_rows.append(
            {
                "symbol": symbol,
                "source": evidence.get("source"),
                "provider_tier": tier,
            }
        )
    minimum_rank = min(
        (
            SOURCE_TIER_RANK.get(str(item["provider_tier"]), 0)
            for item in asset_rows
        ),
        default=0,
    )
    benchmark = sources.get("__benchmark__") or {}
    benchmark_tier = str(
        benchmark.get("provider_tier")
        or ("research_grade" if policy["asset_type"] == "fund" else "unknown")
    )
    benchmark_rank = SOURCE_TIER_RANK.get(benchmark_tier, 0)
    full_coverage = bool(requested and loaded_symbols == requested)
    shadow_ready = bool(
        full_coverage
        and minimum_rank >= SOURCE_TIER_RANK["research_grade"]
        and (
            policy["asset_type"] == "fund"
            or benchmark_rank >= SOURCE_TIER_RANK["research_grade"]
        )
    )
    decision_ready = bool(
        shadow_ready
        and (
            policy["asset_type"] == "fund"
            or (
                minimum_rank >= SOURCE_TIER_RANK["professional"]
                and benchmark_rank >= SOURCE_TIER_RANK["professional"]
            )
        )
    )
    return {
        "schema_version": "alpha_source_release_gate.v1",
        "status": "passed" if shadow_ready else "abstain",
        "shadow_release_eligible": shadow_ready,
        "decision_source_eligible": decision_ready,
        "requested_assets": len(requested),
        "loaded_assets": len(loaded_symbols),
        "minimum_asset_tier": next(
            (
                tier
                for tier, rank in SOURCE_TIER_RANK.items()
                if rank == minimum_rank
            ),
            "unknown",
        ),
        "benchmark_tier": benchmark_tier,
        "assets": asset_rows,
        "checks": [
            {
                "code": "frozen_universe_coverage",
                "label": "冻结资产池完整覆盖",
                "value": f"{len(loaded_symbols)}/{len(requested)}",
                "threshold": "100%",
                "passed": full_coverage,
            },
            {
                "code": "source_release_quality",
                "label": "行情/确认净值来源等级",
                "value": next(
                    (
                        tier
                        for tier, rank in SOURCE_TIER_RANK.items()
                        if rank == minimum_rank
                    ),
                    "unknown",
                ),
                "threshold": ">=research_grade",
                "passed": bool(
                    minimum_rank >= SOURCE_TIER_RANK["research_grade"]
                    and (
                        policy["asset_type"] == "fund"
                        or benchmark_rank
                        >= SOURCE_TIER_RANK["research_grade"]
                    )
                ),
            },
        ],
        "notice": (
            "冻结池完整且来源至少为研究级，可以发布历史通过后的 shadow 概率。"
            if shadow_ready
            else "冻结池缺失或使用公共降级/未知来源，本次明确弃权。"
        ),
        "decision_notice": (
            "股票进入决策层还要求资产与基准均为专业源；"
            "基金要求确认单位净值且来源至少为研究级。"
        ),
    }


def _apply_source_release_gate(
    result: dict[str, Any],
    gate: dict[str, Any],
) -> None:
    shadow_ready = bool(gate["shadow_release_eligible"])
    decision_ready = bool(gate["decision_source_eligible"])
    result["source_release_gate"] = gate
    result.setdefault("data_quality", {}).update(
        {
            "minimum_source_tier": gate["minimum_asset_tier"],
            "benchmark_source_tier": gate["benchmark_tier"],
            "full_frozen_universe_coverage": bool(
                gate["checks"][0]["passed"]
            ),
        }
    )
    for horizon in result.get("horizons") or []:
        historical_gate = horizon.get("historical_gate") or {}
        statistical_passed = bool(historical_gate.get("passed"))
        check = {
            "code": "data_release_boundary",
            "label": "冻结池与来源发布边界",
            "value": (
                f"{gate['loaded_assets']}/{gate['requested_assets']}"
                f" · {gate['minimum_asset_tier']}"
            ),
            "threshold": "100% · >=research_grade",
            "passed": shadow_ready,
        }
        checks = [
            item
            for item in (historical_gate.get("checks") or [])
            if item.get("code") != check["code"]
        ]
        checks.append(check)
        combined = bool(statistical_passed and shadow_ready)
        historical_gate.update(
            {
                "statistical_passed": statistical_passed,
                "passed": combined,
                "status": "passed" if combined else "abstain",
                "passed_count": sum(
                    bool(item.get("passed")) for item in checks
                ),
                "total_count": len(checks),
                "checks": checks,
                "notice": (
                    "固定统计门槛与数据发布边界全部通过；当前仍只是 shadow。"
                    if combined
                    else (
                        gate["notice"]
                        if statistical_passed
                        else historical_gate.get("notice")
                    )
                ),
            }
        )
        horizon["status"] = (
            "historical_gate_passed"
            if combined
            else "historical_gate_failed"
        )
        for forecast in horizon.get("forecasts") or []:
            forecast["statistical_gate_passed"] = statistical_passed
            forecast["data_release_gate_passed"] = shadow_ready
            forecast["decision_source_eligible"] = decision_ready
            forecast["historical_gate_passed"] = combined
            if not combined:
                forecast["published_probability"] = None
                forecast["stance"] = "证据不足·弃权"
                forecast["release_state"] = "data_gate_abstained"
    if not shadow_ready:
        for consensus in result.get("consensus") or []:
            consensus.update(
                {
                    "state": "abstain",
                    "label": "数据发布边界未通过",
                    "tactical_probability": None,
                    "strategic_probability": None,
                    "published_horizons": 0,
                    "decision_eligible": False,
                }
            )


def _normalize_symbol(
    value: str,
    *,
    asset_type: str,
    market: str,
) -> str:
    symbol = str(value or "").strip().upper()
    if asset_type == "fund":
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("基金代码必须是 6 位数字")
        return symbol
    if market == "A股":
        if re.fullmatch(r"\d{6}\.(SH|SZ)", symbol):
            symbol = symbol[:6]
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError(f"A 股代码格式无效: {value}")
    elif market == "港股":
        if not symbol.isdigit() or len(symbol) > 5:
            raise ValueError(f"港股代码格式无效: {value}")
        symbol = symbol.zfill(5)
    elif market == "美股":
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
            raise ValueError(f"美股代码格式无效: {value}")
    return symbol


def normalize_policy(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("Alpha 概率项目必须是对象")
    asset_type = str(payload.get("asset_type") or "stock").strip().lower()
    if asset_type not in {"stock", "fund"}:
        raise ValueError("资产类型只支持 stock 或 fund")
    market = str(
        payload.get("market") or ("基金" if asset_type == "fund" else "A股")
    ).strip()
    if asset_type == "stock" and market not in DEFAULT_BENCHMARKS:
        raise ValueError("股票市场只支持 A股、港股、美股")
    if asset_type == "fund" and market != "基金":
        raise ValueError("基金项目的 market 必须是 基金")

    name = str(payload.get("name") or "").strip()
    if not 2 <= len(name) <= 80:
        raise ValueError("项目名称需要 2–80 个字符")
    raw_symbols = payload.get("symbols") or []
    if not isinstance(raw_symbols, list):
        raise ValueError("symbols 必须是数组")
    symbols: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_symbols:
        source = raw if isinstance(raw, dict) else {"symbol": raw}
        symbol = _normalize_symbol(
            str(source.get("symbol") or source.get("code") or ""),
            asset_type=asset_type,
            market=market,
        )
        if symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(
            {
                "symbol": symbol,
                "name": str(source.get("name") or symbol).strip()[:80],
            }
        )
    if not 4 <= len(symbols) <= 12:
        raise ValueError("冻结资产池必须包含 4–12 个不重复资产")

    default_history = 60 if asset_type == "stock" else 120
    history_months = int(payload.get("history_months") or default_history)
    minimum_history = 36 if asset_type == "stock" else 60
    if not minimum_history <= history_months <= 120:
        raise ValueError(
            f"历史窗口必须在 {minimum_history}–120 个月之间"
        )
    cadence_days = int(
        payload.get("cadence_days") or (7 if asset_type == "stock" else 30)
    )
    if cadence_days not in {7, 14, 30}:
        raise ValueError("运行频率只允许 7、14 或 30 天")
    default_cost = 30.0 if asset_type == "stock" else 50.0
    round_trip_cost_bps = float(
        payload.get("round_trip_cost_bps", default_cost)
    )
    if not 0 <= round_trip_cost_bps <= 300:
        raise ValueError("往返成本必须在 0–300 bps 之间")

    benchmark_symbol = ""
    if asset_type == "stock":
        raw_benchmark = str(
            payload.get("benchmark_symbol")
            or DEFAULT_BENCHMARKS[market]
        ).strip()
        if market == "A股":
            normalized = raw_benchmark.upper()
            if normalized not in {"000300.SH", "000905.SH", "000852.SH"}:
                raise ValueError("A 股基准只允许沪深300、中证500或中证1000")
            benchmark_symbol = normalized
        else:
            benchmark_symbol = _normalize_symbol(
                raw_benchmark,
                asset_type="stock",
                market=market,
            )

    created_on = _today()
    training_start = created_on - dt.timedelta(days=history_months * 31)
    return {
        "schema_version": "alpha_forecast_policy.v1",
        "policy_version": POLICY_VERSION,
        "name": name,
        "asset_type": asset_type,
        "market": market,
        "symbols": symbols,
        "benchmark_symbol": benchmark_symbol,
        "history_months": history_months,
        "training_start_date": training_start.isoformat(),
        "initial_as_of_date": created_on.isoformat(),
        "cadence_days": cadence_days,
        "horizons": [5, 20, 60] if asset_type == "stock" else [20, 60, 120],
        "objective": (
            "benchmark_excess_after_cost"
            if asset_type == "stock"
            else "positive_return_after_cost"
        ),
        "round_trip_cost_bps": round(round_trip_cost_bps, 3),
        "feature_set_version": FEATURE_SET_VERSION,
        "model_family": MODEL_FAMILY,
        "parameter_search": False,
        "universe_mode": "frozen_at_program_creation",
        "data_rules": {
            "stock_price": (
                "tiered_real_source_cascade_no_synthetic_fallback"
            ),
            "fund_nav": "confirmed_unit_nav_only",
            "signal_timing": "after_confirmed_close_or_nav",
            "costs_in_target": True,
        },
        "forward_release_gate": {
            "minimum_outcomes": 30,
            "minimum_run_dates": 6,
            "minimum_symbols": 4,
            "brier_skill_score": ">0",
            "expected_calibration_error": "<=0.12",
            "high_low_return_spread_pct": ">0",
            "positive_run_rate": ">=0.50",
        },
    }


def presets() -> list[dict[str, Any]]:
    return [
        {
            "id": "a_share_core",
            "label": "A 股核心资产",
            "asset_type": "stock",
            "market": "A股",
            "benchmark_symbol": "000300.SH",
            "history_months": 60,
            "cadence_days": 7,
            "round_trip_cost_bps": 30,
            "horizons": [5, 20, 60],
        },
        {
            "id": "hk_liquid",
            "label": "港股高流动性池",
            "asset_type": "stock",
            "market": "港股",
            "benchmark_symbol": "02800",
            "history_months": 60,
            "cadence_days": 7,
            "round_trip_cost_bps": 40,
            "horizons": [5, 20, 60],
        },
        {
            "id": "us_quality",
            "label": "美股质量成长池",
            "asset_type": "stock",
            "market": "美股",
            "benchmark_symbol": "SPY",
            "history_months": 60,
            "cadence_days": 7,
            "round_trip_cost_bps": 20,
            "horizons": [5, 20, 60],
        },
        {
            "id": "fund_long_cycle",
            "label": "基金中长期配置池",
            "asset_type": "fund",
            "market": "基金",
            "benchmark_symbol": "",
            "history_months": 120,
            "cadence_days": 30,
            "round_trip_cost_bps": 50,
            "horizons": [20, 60, 120],
        },
    ]


def _forward_scorecard(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    horizons = sorted(
        {int(item["horizon_sessions"]) for item in rows}
    )
    horizon_rows: list[dict[str, Any]] = []
    for horizon in horizons:
        candidates = [
            item
            for item in rows
            if int(item["horizon_sessions"]) == horizon
            and item.get("outcome_id")
            and bool(item.get("historical_gate_passed"))
            and item.get("calibrated_probability") is not None
        ]
        evidence = [
            item
            for item in candidates
            if bool(item.get("decision_source_eligible"))
        ]
        source_excluded = len(candidates) - len(evidence)
        if not evidence:
            horizon_rows.append(
                {
                    "horizon_sessions": horizon,
                    "status": "collecting",
                    "decision_eligible": False,
                    "outcome_count": 0,
                    "run_date_count": 0,
                    "source_excluded_outcome_count": source_excluded,
                    "checks": [],
                }
            )
            continue
        probability = np.array(
            [float(item["calibrated_probability"]) for item in evidence]
        )
        labels = np.array(
            [int(item["realized_label"]) for item in evidence],
            dtype=int,
        )
        target_returns = np.array(
            [float(item["target_return_pct"]) for item in evidence]
        )
        baseline_probability = np.array(
            [
                min(
                    1 - 1e-6,
                    max(1e-6, float(item.get("base_rate") or 0.5)),
                )
                for item in evidence
            ]
        )
        brier = float(np.mean((probability - labels) ** 2))
        baseline_brier = float(
            np.mean((baseline_probability - labels) ** 2)
        )
        brier_skill = (
            1 - brier / baseline_brier
            if baseline_brier > 1e-12
            else None
        )
        ece = 0.0
        for lower in np.linspace(0, 0.9, 10):
            upper = lower + 0.1
            mask = (
                (probability >= lower)
                & (
                    (probability <= upper)
                    if upper >= 1
                    else (probability < upper)
                )
            )
            if mask.any():
                ece += (
                    mask.mean()
                    * abs(probability[mask].mean() - labels[mask].mean())
                )
        spread = None
        if len(evidence) >= 12:
            low_cut, high_cut = np.quantile(probability, [0.25, 0.75])
            low = target_returns[probability <= low_cut]
            high = target_returns[probability >= high_cut]
            if len(low) and len(high):
                spread = float(high.mean() - low.mean())
        by_run: list[bool] = []
        for as_of in sorted({str(item["as_of_date"]) for item in evidence}):
            group = [
                item for item in evidence if str(item["as_of_date"]) == as_of
            ]
            if not group:
                continue
            group_p = np.array(
                [float(item["calibrated_probability"]) for item in group]
            )
            group_y = np.array(
                [int(item["realized_label"]) for item in group]
            )
            group_b = np.array(
                [float(item.get("base_rate") or 0.5) for item in group]
            )
            by_run.append(
                float(np.mean((group_p - group_y) ** 2))
                < float(np.mean((group_b - group_y) ** 2))
            )
        positive_run_rate = (
            sum(by_run) / len(by_run) if by_run else None
        )
        run_dates = len({str(item["as_of_date"]) for item in evidence})
        symbols = len({str(item["symbol"]) for item in evidence})
        checks = [
            {
                "code": "outcomes",
                "label": "独立前瞻结果",
                "value": len(evidence),
                "threshold": ">=30",
                "passed": len(evidence) >= 30,
            },
            {
                "code": "run_dates",
                "label": "独立运行日期",
                "value": run_dates,
                "threshold": ">=6",
                "passed": run_dates >= 6,
            },
            {
                "code": "symbols",
                "label": "资产覆盖",
                "value": symbols,
                "threshold": ">=4",
                "passed": symbols >= 4,
            },
            {
                "code": "brier_skill",
                "label": "前瞻 Brier Skill",
                "value": _round(brier_skill),
                "threshold": ">0",
                "passed": brier_skill is not None and brier_skill > 0,
            },
            {
                "code": "calibration",
                "label": "前瞻校准误差",
                "value": _round(ece),
                "threshold": "<=0.12",
                "passed": ece <= 0.12,
            },
            {
                "code": "economic_spread",
                "label": "前瞻高低组收益差",
                "value": _round(spread),
                "threshold": ">0%",
                "passed": spread is not None and spread > 0,
            },
            {
                "code": "run_stability",
                "label": "逐批优于基准比例",
                "value": _round(positive_run_rate),
                "threshold": ">=0.50",
                "passed": (
                    positive_run_rate is not None
                    and positive_run_rate >= 0.5
                ),
            },
        ]
        qualified = all(item["passed"] for item in checks)
        horizon_rows.append(
            {
                "horizon_sessions": horizon,
                "status": "qualified" if qualified else "collecting",
                "decision_eligible": qualified,
                "outcome_count": len(evidence),
                "source_excluded_outcome_count": source_excluded,
                "run_date_count": run_dates,
                "symbol_count": symbols,
                "brier_score": _round(brier),
                "baseline_brier_score": _round(baseline_brier),
                "brier_skill_score": _round(brier_skill),
                "expected_calibration_error": _round(ece),
                "high_low_return_spread_pct": _round(spread),
                "positive_run_rate": _round(positive_run_rate),
                "checks": checks,
            }
        )
    return {
        "schema_version": "alpha_forward_scorecard.v1",
        "status": (
            "qualified"
            if horizon_rows
            and all(item["decision_eligible"] for item in horizon_rows)
            else "collecting"
        ),
        "horizons": horizon_rows,
        "notice": (
            "前瞻门禁只使用预测冻结后追加的真实结果；不足 6 个独立运行日或 "
            "30 个结果时不会进入决策层。"
        ),
    }


def forward_scorecard(
    program_id: str,
    *,
    tenant_id: str,
    user_id: str,
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    return _forward_scorecard(
        repo.list_forward_evidence(
            program_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )


def _load_stock_series(
    market: str,
    symbol: str,
    *,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    profile = "a_share_research" if market == "A股" else "default"
    frame = data_fetch.get_history(
        market,
        symbol,
        start_date.replace("-", ""),
        end_date.replace("-", ""),
        source_profile=profile,
    )
    evidence = {
        "source": str(frame.attrs.get("source") or "unknown"),
        "retrieved_at": str(frame.attrs.get("retrieved_at") or ""),
        "observation_count": len(frame),
        "start": (
            pd.to_datetime(frame["date"]).min().date().isoformat()
            if not frame.empty
            else None
        ),
        "end": (
            pd.to_datetime(frame["date"]).max().date().isoformat()
            if not frame.empty
            else None
        ),
        "source_profile": profile,
        "synthetic_fallback": False,
    }
    evidence["provider_tier"] = _classify_source(evidence["source"])
    return frame, evidence


def _load_fund_series(
    code: str,
    *,
    history_months: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    payload = funds.get_fund_nav_history(code, months=history_months)
    frame = pd.DataFrame(payload["points"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["unit_nav"], errors="coerce")
    frame["volume"] = np.nan
    evidence = {
        "source": payload.get("source"),
        "source_url": payload.get("source_url"),
        "as_of": payload.get("as_of"),
        "observation_count": payload.get("observation_count"),
        "confirmed_nav_only": True,
        "synthetic_fallback": False,
    }
    evidence["provider_tier"] = _classify_source(
        evidence["source"],
        confirmed_nav_only=True,
    )
    return frame, evidence


def execute_run(
    run_id: str,
    *,
    actor_id: str = "alpha-market-worker",
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    run = repo.get_run_unscoped(run_id)
    if run is None:
        raise AlphaForecastNotFound("概率运行不存在")
    if run["status"] in TERMINAL_RUN_STATUSES:
        return run
    tenant_id = str(run["tenant_id"])
    user_id = str(run["user_id"])
    program = repo.get_program(
        str(run["program_id"]),
        tenant_id=tenant_id,
        user_id=user_id,
        include_events=False,
    )
    if program is None:
        raise AlphaForecastNotFound("概率研究项目不存在")
    policy = program["policy"]
    repo.mark_running(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
    )
    frames: dict[str, pd.DataFrame] = {}
    names: dict[str, str] = {}
    sources: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    symbols = policy.get("symbols") or []
    try:
        for index, item in enumerate(symbols, start=1):
            symbol = str(item["symbol"])
            names[symbol] = str(item.get("name") or symbol)
            repo.update_progress(
                run_id,
                tenant_id=tenant_id,
                user_id=user_id,
                progress={
                    "stage": "fetching",
                    "completed": index - 1,
                    "total": len(symbols),
                    "message": f"正在读取 {symbol} 的真实历史序列",
                },
            )
            try:
                if policy["asset_type"] == "stock":
                    frame, evidence = _load_stock_series(
                        policy["market"],
                        symbol,
                        start_date=policy["training_start_date"],
                        end_date=str(run["as_of_date"]),
                    )
                else:
                    frame, evidence = _load_fund_series(
                        symbol,
                        history_months=int(policy["history_months"]),
                    )
                    frame = frame[
                        frame["date"]
                        >= pd.Timestamp(policy["training_start_date"])
                    ].reset_index(drop=True)
                frames[symbol] = frame
                sources[symbol] = evidence
            except Exception as error:
                failures.append(
                    {
                        "symbol": symbol,
                        "error": sanitize_worker_error(error)[:500],
                    }
                )
        if len(frames) < 4:
            raise RuntimeError(
                f"真实有效序列只有 {len(frames)} 个，至少需要 4 个；"
                f"失败: {failures[:4]}"
            )

        benchmark_frame = None
        if policy["asset_type"] == "stock":
            benchmark_symbol = str(policy["benchmark_symbol"])
            benchmark_frame, benchmark_evidence = _load_stock_series(
                policy["market"],
                benchmark_symbol,
                start_date=policy["training_start_date"],
                end_date=str(run["as_of_date"]),
            )
            sources["__benchmark__"] = {
                **benchmark_evidence,
                "symbol": benchmark_symbol,
            }
        repo.update_progress(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            progress={
                "stage": "walk_forward",
                "completed": len(frames),
                "total": len(symbols),
                "message": "正在执行逐日期净化、滚动样本外训练与独立概率校准",
            },
        )
        scorecard_before = forward_scorecard(
            program["id"],
            tenant_id=tenant_id,
            user_id=user_id,
            repo=repo,
        )
        eligible_horizons = {
            int(item["horizon_sessions"])
            for item in scorecard_before["horizons"]
            if item.get("decision_eligible")
        }
        result = run_alpha_forecast_research(
            frames=frames,
            names=names,
            benchmark_frame=benchmark_frame,
            policy=policy,
            source_evidence=sources,
        )
        source_gate = _source_release_gate(
            policy=policy,
            sources=sources,
            loaded_symbols=set(frames),
        )
        _apply_source_release_gate(result, source_gate)
        result["run_id"] = run_id
        result["program_id"] = program["id"]
        result["input_sha256"] = run["input_sha256"]
        result["fetch_failures"] = failures
        result["forward_scorecard_before_run"] = scorecard_before
        for forecast in result.get("forecasts") or []:
            decision_eligible = bool(
                forecast.get("historical_gate_passed")
                and int(forecast["horizon_sessions"]) in eligible_horizons
                and forecast.get("decision_source_eligible")
            )
            forecast["decision_eligible"] = decision_eligible
            if decision_eligible:
                forecast["release_state"] = "forward_qualified"
            forecast.update(
                {
                    "schema_version": "alpha_forecast_payload.v1",
                    "run_id": run_id,
                    "program_id": program["id"],
                    "asset_type": policy["asset_type"],
                    "market": policy["market"],
                    "objective": policy["objective"],
                    "benchmark_symbol": policy["benchmark_symbol"],
                    "round_trip_cost_bps": policy[
                        "round_trip_cost_bps"
                    ],
                    "source_evidence": sources.get(
                        str(forecast["symbol"]), {}
                    ),
                }
            )
        for consensus in result.get("consensus") or []:
            symbol_forecasts = [
                item
                for item in result.get("forecasts") or []
                if item["symbol"] == consensus["symbol"]
                and item.get("published_probability") is not None
            ]
            consensus["decision_eligible"] = bool(
                symbol_forecasts
                and all(
                    item.get("decision_eligible")
                    for item in symbol_forecasts
                )
            )
        partial = bool(failures)
        return repo.complete_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            status="partial" if partial else "succeeded",
            result=result,
            forecasts=result.get("forecasts") or [],
        )
    except Exception as error:
        try:
            repo.fail_run(
                run_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                error_code="ALPHA_FORECAST_RUN_FAILED",
                error_message=sanitize_worker_error(error),
            )
        except AlphaForecastConflict:
            pass
        raise


def start_program_run(
    program_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    request_key: str | None = None,
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    key = str(request_key or f"manual:{uuid.uuid4().hex}")
    run, created = repo.create_run(
        program_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        request_key=key,
        as_of_date=_today().isoformat(),
    )
    if not created:
        return run
    if not uses_celery_queue():
        return execute_run(
            run["id"],
            actor_id="embedded-alpha-market-worker",
            repo=repo,
        )
    try:
        task_id = enqueue_alpha_forecast_run(str(run["id"]))
        repo.bind_task(
            str(run["id"]),
            task_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except Exception as error:
        repo.fail_run(
            str(run["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id="api",
            error_code="ALPHA_FORECAST_QUEUE_UNAVAILABLE",
            error_message=sanitize_worker_error(error),
        )
        if isinstance(error, TaskQueueUnavailableError):
            raise
        raise
    return (
        repo.get_run(
            str(run["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        or run
    )


def create_program(
    payload: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    policy = normalize_policy(payload)
    program = repo.create_program(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        policy=policy,
    )
    run = start_program_run(
        program["id"],
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        request_key=f"initial:{policy['initial_as_of_date']}",
        repo=repo,
    )
    return {
        "schema_version": "alpha_forecast_program_creation.v1",
        "program": (
            repo.get_program(
                program["id"],
                tenant_id=tenant_id,
                user_id=user_id,
            )
            or program
        ),
        "initial_run": run,
    }


def transition_program(
    program_id: str,
    action: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    return repo.transition_program(
        program_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        action=action,
    )


def _normalize_observation_frame(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    work = frame.copy()
    if "close" not in work.columns and "unit_nav" in work.columns:
        work["close"] = work["unit_nav"]
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    return (
        work.dropna(subset=["date", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def settle_mature_outcomes(
    *,
    limit: int = 200,
    tenant_id: str | None = None,
    user_id: str | None = None,
    program_id: str | None = None,
    actor_id: str = "system:alpha-outcome-observer",
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    pending = repo.list_pending_forecasts(
        limit=limit,
        tenant_id=tenant_id,
        user_id=user_id,
        program_id=program_id,
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in pending:
        groups[
            (
                str(item["asset_type"]),
                str(item["market"]),
                str(item["symbol"]),
            )
        ].append(item)
    created = 0
    still_waiting = 0
    failures: list[dict[str, str]] = []
    benchmark_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    for (asset_type, market, symbol), forecasts_group in groups.items():
        earliest = min(
            dt.date.fromisoformat(str(item["as_of_date"]))
            for item in forecasts_group
        )
        try:
            if asset_type == "stock":
                frame, source = _load_stock_series(
                    market,
                    symbol,
                    start_date=(
                        earliest - dt.timedelta(days=14)
                    ).isoformat(),
                    end_date=_today().isoformat(),
                )
            else:
                frame, source = _load_fund_series(
                    symbol, history_months=120
                )
            observed = _normalize_observation_frame(frame)
        except Exception as error:
            failures.append(
                {
                    "symbol": symbol,
                    "error": sanitize_worker_error(error)[:400],
                }
            )
            continue
        for forecast in forecasts_group:
            payload = forecast.get("payload") or {}
            as_of = pd.Timestamp(forecast["as_of_date"])
            positions = np.flatnonzero(observed["date"].to_numpy() == as_of)
            horizon = int(forecast["horizon_sessions"])
            if not len(positions) or int(positions[-1]) + horizon >= len(observed):
                still_waiting += 1
                continue
            position = int(positions[-1])
            observed_start_value = float(observed.iloc[position]["close"])
            start_value = float(payload.get("start_value") or 0)
            if not math.isfinite(start_value) or start_value <= 0:
                failures.append(
                    {
                        "symbol": symbol,
                        "error": (
                            f"{forecast['id']} 缺少冻结信号起点，"
                            "拒绝用后来重载的历史值补写"
                        ),
                    }
                )
                continue
            end_row = observed.iloc[position + horizon]
            gross_return = float(end_row["close"]) / start_value - 1
            benchmark_return = None
            benchmark_symbol = str(payload.get("benchmark_symbol") or "")
            if asset_type == "stock":
                cache_key = (
                    market,
                    benchmark_symbol,
                    earliest.isoformat(),
                )
                if cache_key not in benchmark_cache:
                    benchmark_frame, _ = _load_stock_series(
                        market,
                        benchmark_symbol,
                        start_date=(
                            earliest - dt.timedelta(days=14)
                        ).isoformat(),
                        end_date=_today().isoformat(),
                    )
                    benchmark_cache[cache_key] = _normalize_observation_frame(
                        benchmark_frame
                    )
                benchmark = benchmark_cache[cache_key]
                aligned = pd.merge_asof(
                    observed[["date"]].sort_values("date"),
                    benchmark[["date", "close"]].sort_values("date"),
                    on="date",
                    direction="backward",
                    tolerance=pd.Timedelta(days=7),
                )
                start_benchmark = float(
                    payload.get("benchmark_start_value") or 0
                )
                end_benchmark = float(
                    aligned.iloc[position + horizon]["close"]
                )
                if (
                    not math.isfinite(start_benchmark)
                    or start_benchmark <= 0
                    or not math.isfinite(end_benchmark)
                    or end_benchmark <= 0
                ):
                    failures.append(
                        {
                            "symbol": symbol,
                            "error": (
                                f"{forecast['id']} 缺少冻结基准起点"
                                "或精确终点，保持待结算"
                            ),
                        }
                    )
                    continue
                benchmark_return = end_benchmark / start_benchmark - 1
            cost_rate = (
                float(payload.get("round_trip_cost_bps") or 0) / 10_000
            )
            if str(payload.get("objective")) == (
                "benchmark_excess_after_cost"
            ):
                target_return = (
                    gross_return - float(benchmark_return) - cost_rate
                )
            else:
                target_return = gross_return - cost_rate
            outcome_payload = {
                "schema_version": "alpha_forecast_outcome_payload.v1",
                "forecast_id": forecast["id"],
                "program_id": forecast["program_id"],
                "run_id": forecast["run_id"],
                "symbol": symbol,
                "horizon_sessions": horizon,
                "as_of_date": str(forecast["as_of_date"]),
                "observed_date": pd.Timestamp(end_row["date"])
                .date()
                .isoformat(),
                "start_value": _round(start_value, 8),
                "observed_start_value": _round(
                    observed_start_value, 8
                ),
                "start_value_revision_pct": _round(
                    (
                        observed_start_value / start_value - 1
                    )
                    * 100
                ),
                "end_value": _round(float(end_row["close"]), 8),
                "gross_return_pct": _round(gross_return * 100),
                "benchmark_return_pct": _round(
                    benchmark_return * 100
                    if benchmark_return is not None
                    else None
                ),
                "round_trip_cost_bps": _round(cost_rate * 10_000, 3),
                "target_return_pct": _round(target_return * 100),
                "realized_label": int(target_return > 0),
                "exact_confirmed_observations": True,
                "source_evidence": source,
                "actor_id": actor_id,
            }
            _, was_created = repo.record_outcome(
                str(forecast["id"]),
                payload=outcome_payload,
            )
            created += int(was_created)
    return {
        "schema_version": "alpha_outcome_settlement.v1",
        "pending_considered": len(pending),
        "created_outcomes": created,
        "still_waiting": still_waiting,
        "failures": failures,
    }


def settle_program_outcomes(
    program_id: str,
    *,
    actor_id: str = "system:alpha-outcome-observer",
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    program = repo.get_program_unscoped(program_id)
    if program is None:
        raise AlphaForecastNotFound("概率研究项目不存在")
    return settle_mature_outcomes(
        limit=200,
        tenant_id=str(program["tenant_id"]),
        user_id=str(program["user_id"]),
        program_id=str(program["id"]),
        actor_id=actor_id,
        repo=repo,
    )


def request_program_settlement(
    program_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    program = repo.get_program(
        program_id,
        tenant_id=tenant_id,
        user_id=user_id,
        include_events=False,
    )
    if program is None:
        raise AlphaForecastNotFound("概率研究项目不存在")
    if not uses_celery_queue():
        return {
            "scheduled": False,
            "mode": "embedded",
            "settlement": settle_program_outcomes(
                program_id,
                actor_id=f"{actor_id}:embedded-observer",
                repo=repo,
            ),
        }
    task_id = enqueue_alpha_forecast_settlement(program_id)
    return {
        "scheduled": True,
        "mode": "celery",
        "task_id": task_id,
        "program_id": program_id,
    }


def maintain_programs(
    *,
    limit: int = 5,
    actor_id: str = "system:alpha-maintenance",
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    settlement = settle_mature_outcomes(
        limit=max(50, limit * 20),
        actor_id=actor_id,
        repo=repo,
    )
    dispatched: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for program in repo.list_due_programs(limit=limit):
        try:
            run = start_program_run(
                str(program["id"]),
                tenant_id=str(program["tenant_id"]),
                user_id=str(program["user_id"]),
                actor_id=actor_id,
                request_key=f"scheduled:{_today().isoformat()}",
                repo=repo,
            )
            dispatched.append(
                {
                    "program_id": program["id"],
                    "run_id": run["id"],
                    "status": run["status"],
                }
            )
        except AlphaForecastConflict as error:
            failures.append(
                {"program_id": program["id"], "error": str(error)}
            )
    return {
        "schema_version": "alpha_forecast_maintenance.v1",
        "settlement": settlement,
        "dispatched": dispatched,
        "failures": failures,
    }


def overview(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 30,
    repo: AlphaForecastRepository = repository,
) -> dict[str, Any]:
    programs = repo.list_programs(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    runs = repo.list_runs(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    run_by_program: dict[str, dict[str, Any]] = {}
    for run in runs:
        run_by_program.setdefault(str(run["program_id"]), run)
    for program in programs:
        program["latest_run"] = run_by_program.get(str(program["id"]))
        program["forward_scorecard"] = forward_scorecard(
            str(program["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
            repo=repo,
        )
    active_runs = sum(
        item["status"] in {"queued", "running"} for item in runs
    )
    published = sum(
        1
        for run in runs
        for forecast in ((run.get("result") or {}).get("forecasts") or [])
        if forecast.get("published_probability") is not None
    )
    decision_eligible = sum(
        1
        for program in programs
        for item in (program["forward_scorecard"].get("horizons") or [])
        if item.get("decision_eligible")
    )
    return {
        "schema_version": "alpha_forecast_overview.v1",
        "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "presets": presets(),
        "programs": programs,
        "runs": runs,
        "summary": {
            "program_count": len(programs),
            "active_program_count": sum(
                item["status"] == "active" for item in programs
            ),
            "active_run_count": active_runs,
            "published_shadow_forecast_count": published,
            "decision_eligible_horizon_count": decision_eligible,
        },
        "workflow": [
            "冻结资产池、基准、成本、周期和模型族",
            "按日期滚动训练并严格净化未成熟标签",
            "用早期样本外预测校准、后期样本外预测评分",
            "历史门槛通过才发布 shadow 概率，否则弃权",
            "真实前瞻结果达到 6 批/30 条并再次过门槛才进入决策层",
        ],
        "notice": (
            "这是概率校准与模型治理工具，不是收益承诺。系统不会自动连接券商，"
            "也不会把历史回测概率冒充真实前瞻胜率。"
        ),
    }
