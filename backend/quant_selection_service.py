# -*- coding: utf-8 -*-
"""Application service for the point-in-time quant selection lab."""

from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

import config
import data_fetch
from background_jobs import BackgroundJobRepository
from quant_selection_engine import (
    ENGINE_VERSION,
    QuantSelectionInputError,
    run_selection_research,
)
from quant_selection_repository import (
    QuantSelectionConflictError,
    QuantSelectionNotFoundError,
    QuantSelectionRepository,
    repository,
    sha256_payload,
)
from task_queue import (
    QUEUE_MARKET,
    TaskQueueUnavailableError,
    enqueue_background_job,
    uses_celery_queue,
)


POLICY_VERSION = "quant_selection_policy@1.0.0"
MAX_MANUAL_SYMBOLS = 40
MAX_INDEX_UNION_SYMBOLS = 80
INDEX_OPTIONS = {
    "000300.SH": {
        "name": "沪深300",
        "benchmark_symbol": "510300",
    },
    "000905.SH": {
        "name": "中证500",
        "benchmark_symbol": "510500",
    },
    "000852.SH": {
        "name": "中证1000",
        "benchmark_symbol": "512100",
    },
}
DEFAULT_BENCHMARKS = {
    "A股": "510300",
    "港股": "02800",
    "美股": "SPY",
}
DEFAULT_FACTOR_WEIGHTS = {
    "momentum": 35.0,
    "trend_quality": 25.0,
    "low_volatility": 25.0,
    "liquidity": 15.0,
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _bounded(
    value: Any,
    lower: float,
    upper: float,
    name: str,
) -> float:
    number = _number(value)
    if number is None or number < lower or number > upper:
        raise ValueError(f"{name} 必须在 {lower:g}-{upper:g} 之间")
    return number


def _normalize_symbol(market: str, value: Any) -> str:
    symbol = re.sub(r"\s+", "", str(value or ""))
    if market == "A股":
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError(f"A股代码格式无效:{symbol or '(空)'}")
    elif market == "港股":
        if not re.fullmatch(r"\d{1,5}", symbol):
            raise ValueError(f"港股代码格式无效:{symbol or '(空)'}")
        symbol = symbol.zfill(5)
    elif market == "美股":
        symbol = symbol.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError(f"美股代码格式无效:{symbol or '(空)'}")
    else:
        raise ValueError(f"不支持的市场:{market}")
    return symbol


def normalize_policy(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(payload or {})
    market = str(source.get("market") or "A股")
    if market not in data_fetch.MARKETS:
        raise ValueError(f"不支持的市场:{market}")
    universe_mode = str(
        source.get("universe_mode") or "frozen_symbols"
    )
    if universe_mode not in {"frozen_symbols", "tushare_index"}:
        raise ValueError("股票池模式只能是 frozen_symbols 或 tushare_index")
    if universe_mode == "tushare_index" and market != "A股":
        raise ValueError("Tushare 历史指数成分模式当前只支持 A股")

    index_code = str(source.get("index_code") or "000300.SH").upper()
    if universe_mode == "tushare_index" and index_code not in INDEX_OPTIONS:
        raise ValueError("历史指数只支持沪深300、中证500或中证1000")

    symbols = []
    seen = set()
    for item in source.get("symbols") or []:
        symbol = _normalize_symbol(
            market,
            (item or {}).get("symbol")
            if isinstance(item, dict)
            else item,
        )
        if symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(
            {
                "symbol": symbol,
                "name": str(
                    (item or {}).get("name") or symbol
                    if isinstance(item, dict)
                    else symbol
                )[:80],
            }
        )
    if universe_mode == "frozen_symbols":
        if len(symbols) < 6:
            raise ValueError("冻结自定义股票池至少需要 6 只股票")
        if len(symbols) > MAX_MANUAL_SYMBOLS:
            raise ValueError(
                f"冻结自定义股票池最多 {MAX_MANUAL_SYMBOLS} 只股票"
            )

    history_months = int(
        _bounded(
            source.get("history_months", 60),
            36,
            120,
            "历史月数",
        )
    )
    lookback_days = int(source.get("lookback_days", 252))
    if lookback_days not in {126, 252}:
        raise ValueError("因子回看期只能是 126 或 252 个交易日")
    rebalance_days = int(source.get("rebalance_days", 21))
    if rebalance_days not in {21, 63}:
        raise ValueError("调仓周期只能是 21 或 63 个交易日")
    segment_days = int(source.get("oos_segment_days", 126))
    if segment_days not in {126, 252}:
        raise ValueError("样本外窗口只能是 126 或 252 个交易日")

    factor_source = {
        **DEFAULT_FACTOR_WEIGHTS,
        **dict(source.get("factor_weights") or {}),
    }
    factor_weights = {
        key: _bounded(
            factor_source.get(key),
            0,
            100,
            f"{key} 因子权重",
        )
        for key in DEFAULT_FACTOR_WEIGHTS
    }
    if sum(factor_weights.values()) <= 0:
        raise ValueError("至少一个因子权重必须大于 0")

    max_positions = int(
        _bounded(source.get("max_positions", 6), 2, 12, "最大持仓数")
    )
    max_position_pct = _bounded(
        source.get("max_position_pct", 20),
        5,
        50,
        "单股仓位上限",
    )
    minimum_cash_pct = _bounded(
        source.get("minimum_cash_pct", 10),
        0,
        60,
        "最低现金比例",
    )
    construction = str(
        source.get("construction_method")
        or "score_inverse_volatility"
    )
    if construction not in {
        "equal_weight",
        "inverse_volatility",
        "score_inverse_volatility",
    }:
        raise ValueError("不支持的组合加权方法")
    benchmark = _normalize_symbol(
        market,
        source.get("benchmark_symbol")
        or (
            INDEX_OPTIONS[index_code]["benchmark_symbol"]
            if universe_mode == "tushare_index"
            else DEFAULT_BENCHMARKS[market]
        ),
    )
    attestation = str(
        source.get("universe_attestation") or "current_snapshot"
    )
    if attestation not in {"current_snapshot", "historical_membership"}:
        raise ValueError("股票池声明无效")

    return {
        "schema_version": "quant_selection_policy.v1",
        "policy_version": POLICY_VERSION,
        "name": str(
            source.get("name")
            or (
                f"{INDEX_OPTIONS[index_code]['name']}历史多因子"
                if universe_mode == "tushare_index"
                else f"{market}冻结股票池多因子"
            )
        )[:80],
        "market": market,
        "universe_mode": universe_mode,
        "universe_attestation": attestation,
        "index_code": (
            index_code if universe_mode == "tushare_index" else None
        ),
        "index_member_limit": int(
            _bounded(
                source.get("index_member_limit", 12),
                8,
                24,
                "每期指数候选数",
            )
        ),
        "symbols": symbols,
        "benchmark_symbol": benchmark,
        "history_months": history_months,
        "lookback_days": lookback_days,
        "minimum_history_days": int(
            _bounded(
                source.get("minimum_history_days", lookback_days),
                126,
                504,
                "最少历史交易日",
            )
        ),
        "rebalance_days": rebalance_days,
        "oos_segment_days": segment_days,
        "factor_weights": factor_weights,
        "minimum_composite_score": _bounded(
            source.get("minimum_composite_score", 55),
            0,
            100,
            "最低综合分",
        ),
        "minimum_price": _bounded(
            source.get("minimum_price", 1),
            0.01,
            10_000,
            "最低价格",
        ),
        "minimum_average_turnover": _bounded(
            source.get("minimum_average_turnover", 1_000_000),
            0,
            1_000_000_000_000,
            "最低平均成交额",
        ),
        "max_price_staleness_days": int(
            _bounded(
                source.get("max_price_staleness_days", 7),
                3,
                30,
                "最大行情陈旧天数",
            )
        ),
        "construction_method": construction,
        "max_positions": max_positions,
        "max_position_pct": max_position_pct,
        "minimum_cash_pct": minimum_cash_pct,
        "initial_capital": _bounded(
            source.get("initial_capital", 1_000_000),
            100_000,
            100_000_000,
            "模拟初始资金",
        ),
        "minimum_order_notional": _bounded(
            source.get("minimum_order_notional", 1_000),
            0,
            10_000_000,
            "最小订单金额",
        ),
        "commission_bps": _bounded(
            source.get("commission_bps", 5),
            0,
            100,
            "单边佣金",
        ),
        "slippage_bps": _bounded(
            source.get("slippage_bps", 8),
            0,
            200,
            "基础滑点",
        ),
        "impact_bps": _bounded(
            source.get("impact_bps", 20),
            0,
            500,
            "满容量冲击",
        ),
        "sell_tax_bps": _bounded(
            source.get("sell_tax_bps", 0),
            0,
            200,
            "卖出税费",
        ),
        "max_volume_participation_pct": _bounded(
            source.get("max_volume_participation_pct", 2.5),
            0.1,
            20,
            "最大成交量参与率",
        ),
        "max_order_age_sessions": int(
            _bounded(
                source.get("max_order_age_sessions", 3),
                1,
                10,
                "订单最长等待交易日",
            )
        ),
        "maximum_drawdown_pct": _bounded(
            source.get("maximum_drawdown_pct", 25),
            5,
            60,
            "最大回撤预算",
        ),
    }


def presets() -> list[dict[str, Any]]:
    return [
        {
            "id": "a_csi300_point_in_time",
            "label": "沪深300历史成分",
            "description": (
                "每月读取 Tushare 历史指数权重，逐期选择权重靠前的"
                "成分股，适合验证 A股多因子轮动。"
            ),
            "promotion_capable": True,
            "policy": normalize_policy(
                {
                    "name": "沪深300历史成分多因子",
                    "market": "A股",
                    "universe_mode": "tushare_index",
                    "index_code": "000300.SH",
                    "index_member_limit": 12,
                    "history_months": 60,
                    "minimum_average_turnover": 50_000_000,
                    "sell_tax_bps": 10,
                }
            ),
        },
        {
            "id": "us_liquid_research_pool",
            "label": "美股高流动性研究池",
            "description": (
                "冻结示例股票池用于研究执行链路；它不是历史指数成分，"
                "因此不能通过幸存者偏差门槛。"
            ),
            "promotion_capable": False,
            "policy": normalize_policy(
                {
                    "name": "美股高流动性冻结池",
                    "market": "美股",
                    "universe_mode": "frozen_symbols",
                    "symbols": [
                        {"symbol": symbol, "name": symbol}
                        for symbol in (
                            "AAPL",
                            "MSFT",
                            "NVDA",
                            "AMZN",
                            "GOOGL",
                            "META",
                            "BRK.B",
                            "JPM",
                            "LLY",
                            "AVGO",
                            "XOM",
                            "UNH",
                        )
                    ],
                    "benchmark_symbol": "SPY",
                    "history_months": 60,
                    "minimum_average_turnover": 10_000_000,
                }
            ),
        },
        {
            "id": "hk_liquid_research_pool",
            "label": "港股高流动性研究池",
            "description": (
                "冻结港股大盘样本用于多因子与容量研究；缺少授权历史"
                "成分序列时只保留研究资格。"
            ),
            "promotion_capable": False,
            "policy": normalize_policy(
                {
                    "name": "港股高流动性冻结池",
                    "market": "港股",
                    "universe_mode": "frozen_symbols",
                    "symbols": [
                        {"symbol": symbol, "name": symbol}
                        for symbol in (
                            "00700",
                            "09988",
                            "03690",
                            "00941",
                            "01299",
                            "02318",
                            "00005",
                            "00883",
                            "01810",
                            "09618",
                        )
                    ],
                    "benchmark_symbol": "02800",
                    "history_months": 60,
                    "minimum_average_turnover": 5_000_000,
                }
            ),
        },
    ]


def _month_ranges(
    start: dt.date,
    end: dt.date,
) -> list[tuple[dt.date, dt.date]]:
    cursor = start.replace(day=1)
    output = []
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = min(
            end,
            dt.date(cursor.year, cursor.month, last_day),
        )
        # Query the whole boundary month as index weights are usually
        # published on one effective date that may precede ``start``.
        output.append((cursor, month_end))
        cursor = (
            dt.date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else dt.date(cursor.year, cursor.month + 1, 1)
        )
    return output


def _tushare_pro():
    if not str(config.TUSHARE_TOKEN or "").strip():
        raise ValueError(
            "历史指数成分模式需要 TUSHARE_TOKEN，且账号需有 index_weight 权限"
        )
    import tushare as ts

    return ts.pro_api(str(config.TUSHARE_TOKEN).strip())


def _a_stock_names(pro) -> dict[str, str]:
    output: dict[str, str] = {}
    for status in ("L", "D", "P"):
        try:
            frame = pro.stock_basic(
                exchange="",
                list_status=status,
                fields="ts_code,symbol,name,list_date,delist_date",
            )
        except Exception:
            continue
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            symbol = str(row.get("symbol") or "").strip()
            if re.fullmatch(r"\d{6}", symbol):
                output[symbol] = str(row.get("name") or symbol)
    return output


def _load_tushare_index_universe(
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pro = _tushare_pro()
    end = dt.date.today()
    start = end - dt.timedelta(days=int(policy["history_months"]) * 31)
    rows = []
    failed_months = []
    for month_start, month_end in _month_ranges(start, end):
        try:
            frame = pro.index_weight(
                index_code=policy["index_code"],
                start_date=month_start.strftime("%Y%m%d"),
                end_date=month_end.strftime("%Y%m%d"),
            )
            if frame is not None and not frame.empty:
                rows.append(frame)
            else:
                failed_months.append(month_start.strftime("%Y-%m"))
        except Exception as error:
            failed_months.append(
                f"{month_start.strftime('%Y-%m')}:{str(error)[:120]}"
            )
    if not rows:
        raise ValueError(
            "Tushare index_weight 未返回任何历史成分；请检查 Token 积分和接口权限"
        )
    combined = pd.concat(rows, ignore_index=True)
    required = {"trade_date", "con_code", "weight"}
    if not required.issubset(combined.columns):
        raise ValueError("Tushare index_weight 返回字段不完整")
    combined["trade_date"] = pd.to_datetime(
        combined["trade_date"].astype(str),
        format="%Y%m%d",
        errors="coerce",
    )
    combined["weight"] = pd.to_numeric(
        combined["weight"], errors="coerce"
    )
    combined = combined.dropna(
        subset=["trade_date", "con_code", "weight"]
    )
    names = _a_stock_names(pro)
    snapshots = []
    for trade_date, group in combined.groupby("trade_date"):
        members = []
        seen = set()
        for _, row in group.sort_values(
            ["weight", "con_code"], ascending=[False, True]
        ).iterrows():
            symbol = str(row["con_code"]).split(".")[0]
            if not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
                continue
            seen.add(symbol)
            members.append(
                {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol),
                    "parent_weight_pct": round(float(row["weight"]), 6),
                }
            )
            if len(members) >= int(policy["index_member_limit"]):
                break
        if members:
            snapshots.append(
                {
                    "as_of": pd.Timestamp(trade_date).strftime(
                        "%Y-%m-%d"
                    ),
                    "source": (
                        f"Tushare index_weight:{policy['index_code']}"
                    ),
                    "members": members,
                }
            )
    snapshots.sort(key=lambda item: item["as_of"])
    # Some index endpoints may return duplicate effective dates across calls.
    deduplicated = {
        item["as_of"]: item for item in snapshots
    }
    snapshots = [
        deduplicated[key] for key in sorted(deduplicated)
    ]
    union = {
        member["symbol"]
        for snapshot in snapshots
        for member in snapshot["members"]
    }
    if len(union) > MAX_INDEX_UNION_SYMBOLS:
        raise ValueError(
            f"历史指数成分并集达到 {len(union)} 只，超过安全上限 "
            f"{MAX_INDEX_UNION_SYMBOLS}；请缩短历史或减少每期候选数"
        )
    dates = [
        pd.Timestamp(item["as_of"]).normalize()
        for item in snapshots
    ]
    gaps = [
        int((right - left).days)
        for left, right in zip(dates, dates[1:])
    ]
    maximum_gap = max(gaps) if gaps else None
    verified = (
        len(snapshots) >= 12
        and (maximum_gap is None or maximum_gap <= 70)
        and not failed_months
    )
    evidence = {
        "mode": "tushare_index",
        "label": (
            f"{INDEX_OPTIONS[policy['index_code']]['name']}历史指数权重"
        ),
        "source": "Tushare Pro index_weight",
        "point_in_time_verified": verified,
        "verification_detail": (
            "逐月专业历史成分连续且无缺口"
            if verified
            else (
                f"历史成分存在 {len(failed_months)} 个缺失月份或"
                f"最大快照间隔 {maximum_gap} 天，阻止升级"
            )
        ),
        "maximum_member_count": max(
            len(item["members"]) for item in snapshots
        ),
        "unique_symbol_count": len(union),
        "maximum_snapshot_gap_days": maximum_gap,
        "failed_months": failed_months,
        "warning": (
            None
            if verified
            else "历史成分序列不连续，结果只能用于研究"
        ),
    }
    return snapshots, evidence


def _manual_universe(
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    end = dt.date.today()
    start = end - dt.timedelta(days=int(policy["history_months"]) * 31)
    members = [
        {
            "symbol": item["symbol"],
            "name": item.get("name") or item["symbol"],
            "parent_weight_pct": None,
        }
        for item in policy["symbols"]
    ]
    attested = policy["universe_attestation"] == "historical_membership"
    return [
        {
            "as_of": start.isoformat(),
            "source": "user_frozen_symbols",
            "members": members,
        }
    ], {
        "mode": "frozen_symbols",
        "label": "用户冻结研究股票池",
        "source": "user_input",
        "point_in_time_verified": False,
        "verification_detail": (
            "用户声明包含历史成员，但平台没有授权成分序列可独立核验"
            if attested
            else "使用当前冻结名单回看历史，存在幸存者偏差"
        ),
        "maximum_member_count": len(members),
        "unique_symbol_count": len(members),
        "maximum_snapshot_gap_days": None,
        "failed_months": [],
        "warning": "冻结自定义名单不是专业历史成分序列，不能升级为纸面策略",
    }


def _load_universe(
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if policy["universe_mode"] == "tushare_index":
        return _load_tushare_index_universe(policy)
    return _manual_universe(policy)


def _combine_price_modes(
    adjusted: pd.DataFrame,
    raw: pd.DataFrame | None,
) -> pd.DataFrame:
    adjusted_frame = adjusted[
        ["date", "open", "high", "low", "close", "volume"]
    ].copy()
    adjusted_frame["date"] = pd.to_datetime(
        adjusted_frame["date"]
    ).dt.normalize()
    if raw is None or raw.empty:
        adjusted_frame["execution_open"] = adjusted_frame["open"]
        adjusted_frame["raw_turnover"] = (
            adjusted_frame["open"] * adjusted_frame["volume"]
        )
        return adjusted_frame
    raw_frame = raw[["date", "open", "close", "volume"]].copy()
    raw_frame["date"] = pd.to_datetime(raw_frame["date"]).dt.normalize()
    raw_frame = raw_frame.rename(
        columns={
            "open": "raw_open",
            "close": "raw_close",
            "volume": "raw_volume",
        }
    )
    merged = adjusted_frame.merge(
        raw_frame,
        on="date",
        how="left",
        validate="one_to_one",
    )
    ratio = merged["close"] / merged["raw_close"]
    ratio = ratio.where(
        (merged["raw_close"] > 0)
        & ratio.map(lambda value: bool(math.isfinite(value)))
    )
    merged["execution_open"] = (
        merged["raw_open"] * ratio
    ).where(merged["raw_open"] > 0, merged["open"])
    merged["execution_open"] = merged["execution_open"].fillna(
        merged["open"]
    )
    merged["raw_turnover"] = (
        merged["raw_open"] * merged["raw_volume"]
    )
    merged["raw_turnover"] = merged["raw_turnover"].fillna(
        merged["open"] * merged["volume"]
    )
    return merged[
        [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "execution_open",
            "raw_turnover",
        ]
    ]


def _load_asset(
    market: str,
    symbol: str,
    history_months: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    adjusted = data_fetch.get_history_months(
        market,
        symbol,
        history_months,
        fetch_months=history_months,
    )
    adjusted_source = str(
        adjusted.attrs.get("source") or "source_not_exposed"
    )
    raw = None
    raw_source = None
    raw_error = None
    try:
        raw, raw_source = data_fetch.get_price_level_history_months(
            market,
            symbol,
            history_months,
        )
    except Exception as error:
        raw_error = str(error)[:300]
        raw_source = "adjusted_price_fallback"
    return _combine_price_modes(adjusted, raw), {
        "adjusted_source": adjusted_source,
        "raw_source": raw_source,
        "raw_error": raw_error,
        "retrieved_at": adjusted.attrs.get("retrieved_at"),
    }


def execute_run(
    run_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str = "quant-selection-worker",
    repo: QuantSelectionRepository = repository,
) -> dict[str, Any]:
    run = repo.get_run(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        include_events=False,
    )
    if run is None:
        raise QuantSelectionNotFoundError("组合选股实验不存在")
    if run["status"] in {"succeeded", "partial"}:
        return run
    if not (run.get("integrity") or {}).get("input_verified"):
        raise QuantSelectionConflictError("组合选股实验输入完整性校验失败")
    policy = normalize_policy(run["policy"])
    try:
        repo.mark_running(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
        )
        snapshots, universe_evidence = _load_universe(policy)
        symbols = sorted(
            {
                member["symbol"]
                for snapshot in snapshots
                for member in snapshot["members"]
            }
        )
        benchmark = policy["benchmark_symbol"]
        fetch_symbols = symbols + (
            [benchmark] if benchmark not in symbols else []
        )
        repo.update_progress(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            progress={
                "stage": "market_data",
                "completed": 0,
                "total": len(fetch_symbols),
                "message": "正在读取复权与未复权专业日线",
            },
        )
        frames: dict[str, pd.DataFrame] = {}
        sources: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        completed = 0
        with ThreadPoolExecutor(
            max_workers=min(6, len(fetch_symbols))
        ) as pool:
            futures = {
                pool.submit(
                    _load_asset,
                    policy["market"],
                    symbol,
                    int(policy["history_months"]),
                ): symbol
                for symbol in fetch_symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    frame, evidence = future.result()
                    frames[symbol] = frame
                    sources[symbol] = evidence
                except Exception as error:
                    failures.append(
                        {
                            "symbol": symbol,
                            "error": str(error)[:500],
                        }
                    )
                    sources[symbol] = {
                        "adjusted_source": None,
                        "raw_source": None,
                        "error": str(error)[:500],
                    }
                completed += 1
                repo.update_progress(
                    run_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    progress={
                        "stage": "market_data",
                        "completed": completed,
                        "total": len(fetch_symbols),
                        "message": f"已处理 {symbol}",
                    },
                )
        if benchmark not in frames:
            detail = next(
                (
                    item["error"]
                    for item in failures
                    if item["symbol"] == benchmark
                ),
                "基准行情不可用",
            )
            raise QuantSelectionInputError(
                f"基准 {benchmark} 行情失败:{detail}"
            )
        loaded_candidates = sum(symbol in frames for symbol in symbols)
        if loaded_candidates < 4:
            raise QuantSelectionInputError(
                f"只有 {loaded_candidates} 只候选股行情可用，至少需要 4 只"
            )
        repo.update_progress(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            progress={
                "stage": "simulation",
                "completed": completed,
                "total": len(fetch_symbols),
                "message": "正在运行逐日撮合、样本外分段和成本压力测试",
            },
        )
        result = run_selection_research(
            frames=frames,
            benchmark_symbol=benchmark,
            universe_snapshots=snapshots,
            policy=policy,
            universe_evidence=universe_evidence,
            source_evidence=sources,
        )
        result["run_id"] = run_id
        result["generated_at"] = dt.datetime.now(
            dt.timezone.utc
        ).isoformat(timespec="seconds")
        result["input_policy_sha256"] = run.get("policy_sha256")
        result["fetch_failures"] = failures
        result["data_quality"]["requested_asset_count"] = len(symbols)
        result["data_quality"]["loaded_asset_count"] = loaded_candidates
        partial = bool(
            failures
            or universe_evidence.get("warning")
            or any(
                (item or {}).get("raw_error")
                for item in sources.values()
            )
        )
        return repo.complete_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            status="partial" if partial else "succeeded",
            result=result,
        )
    except Exception as error:
        try:
            repo.fail_run(
                run_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                error_code="QUANT_SELECTION_RUN_FAILED",
                error_message=str(error),
            )
        except QuantSelectionConflictError:
            pass
        raise


def start_run(
    payload: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: QuantSelectionRepository = repository,
) -> dict[str, Any]:
    policy = normalize_policy(payload)
    run = repo.create_run(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        policy=policy,
    )
    if not uses_celery_queue():
        return execute_run(
            run["id"],
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id="embedded-quant-selection-worker",
            repo=repo,
        )
    jobs = BackgroundJobRepository()
    try:
        job, _ = jobs.create_job(
            job_type="quant_selection_run",
            queue_name=QUEUE_MARKET,
            payload={
                "run_id": run["id"],
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=run["id"],
            max_attempts=1,
        )
        repo.bind_job(
            run["id"],
            str(job["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        enqueue_background_job(job, jobs)
    except Exception as error:
        repo.fail_run(
            run["id"],
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id="api",
            error_code="QUANT_SELECTION_QUEUE_UNAVAILABLE",
            error_message=str(error),
        )
        if isinstance(error, TaskQueueUnavailableError):
            raise
        raise
    return (
        repo.get_run(
            run["id"],
            tenant_id=tenant_id,
            user_id=user_id,
        )
        or run
    )


def refresh_run_status(
    run_id: str,
    *,
    tenant_id: str,
    user_id: str,
    repo: QuantSelectionRepository = repository,
) -> dict[str, Any] | None:
    run = repo.get_run(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if (
        not run
        or run["status"] not in {"queued", "running"}
        or not run.get("job_id")
    ):
        return run
    job = BackgroundJobRepository().get_job(
        str(run["job_id"]), include_payload=False
    )
    if job and job.get("status") in {"failed", "cancelled"}:
        return repo.fail_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id="api-reconciler",
            error_code=str(
                job.get("error_code") or "QUANT_SELECTION_JOB_FAILED"
            ),
            error_message=str(
                job.get("error_message")
                or "组合选股后台任务失败"
            ),
        )
    return run


def overview(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 30,
    repo: QuantSelectionRepository = repository,
) -> dict[str, Any]:
    runs = repo.list_runs(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    mandates = repo.list_shadow_mandates(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    return {
        "schema_version": "quant_selection_overview.v1",
        "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "presets": presets(),
        "runs": runs,
        "shadow_mandates": mandates,
        "summary": {
            "run_count": len(runs),
            "active_run_count": sum(
                item["status"] in {"queued", "running"}
                for item in runs
            ),
            "shadow_mandate_count": len(mandates),
        },
        "scope_notice": (
            "历史指数模式可核验逐期成分；冻结自定义名单只用于研究，"
            "不会伪装成无幸存者偏差回测。"
        ),
    }


def freeze_shadow_mandate(
    run_id: str,
    *,
    acknowledged: bool,
    expected_result_sha256: str,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: QuantSelectionRepository = repository,
) -> tuple[dict[str, Any], bool]:
    if not acknowledged:
        raise ValueError("必须确认该指令仅用于前向纸面验证")
    run = repo.get_run(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if run is None:
        raise QuantSelectionNotFoundError("组合选股实验不存在")
    if not (run.get("integrity") or {}).get("verified"):
        raise QuantSelectionConflictError("组合选股实验完整性校验失败")
    if run.get("result_sha256") != expected_result_sha256:
        raise QuantSelectionConflictError("实验结果摘要已经变化")
    result = run.get("result") or {}
    gate = result.get("promotion_gate") or {}
    if not gate.get("paper_shadow_eligible"):
        raise QuantSelectionConflictError(
            "研究门槛未全部通过，不能冻结为前向纸面策略"
        )
    latest = result.get("latest_signal") or {}
    targets = latest.get("targets") or []
    if len(targets) < 2:
        raise QuantSelectionConflictError("最新目标不足两只股票")
    snapshot = {
        "schema_version": "quant_selection_shadow_snapshot.v1",
        "run_id": run_id,
        "result_sha256": run["result_sha256"],
        "engine_version": run["engine_version"],
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "policy": result.get("policy"),
        "universe_evidence": {
            key: value
            for key, value in (result.get("universe") or {}).items()
            if key != "snapshots"
        },
        "data_quality": result.get("data_quality"),
        "promotion_gate": gate,
        "latest_signal": latest,
        "forward_rules": {
            "mode": "paper_only",
            "signal_timing": "after_close",
            "earliest_fill": "next_trading_day_open",
            "rebalance_days": (
                (result.get("policy") or {}).get("rebalance_days")
            ),
            "broker_order_submission": False,
        },
        "acknowledgement": (
            "我理解历史结果不保证未来收益；本策略仅用于前向纸面验证，"
            "不会自动连接券商或提交订单。"
        ),
    }
    snapshot["evidence_sha256"] = sha256_payload(
        {
            "result_sha256": run["result_sha256"],
            "latest_signal": latest,
            "promotion_gate": gate,
        }
    )
    return repo.create_shadow_mandate(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        snapshot=snapshot,
    )
