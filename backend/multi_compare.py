# -*- coding: utf-8 -*-
"""
多股横向对比
============
把同一市场的多只股票放到同一时间轴上,输出:
  - 归一化走势:每只都从 100 起步,方便直接比较强弱
  - 指标表:区间收益、年化波动、最大回撤、当前技术评分
  - 相关性矩阵:看股票之间是否高度同涨同跌
"""

import datetime
import contextlib
import io
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import analysis
import data_fetch
import fundamentals

_RESULT_CACHE_TTL = 600
_result_cache = {}


def _a_symbol_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "sh."
    if symbol.startswith(("4", "8")):
        return "bj."
    return "sz."


def _a_tx_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "sh" + symbol
    if symbol.startswith(("4", "8")):
        return "bj" + symbol
    return "sz" + symbol


def _max_drawdown(close: pd.Series) -> float:
    peak = close.cummax()
    dd = close / peak - 1
    return round(float(dd.min() * 100), 2)


def _annual_vol(close: pd.Series) -> float:
    ret = close.pct_change().dropna()
    if ret.empty:
        return 0.0
    return round(float(ret.std() * np.sqrt(252) * 100), 2)


def _period_return(close: pd.Series, trading_days: int):
    if len(close) <= trading_days:
        return None
    return round(float((close.iloc[-1] / close.iloc[-trading_days - 1] - 1) * 100), 2)


def _fetch_one(market: str, symbol: str, months: int) -> dict:
    symbol = symbol.strip()
    if not symbol:
        return {"symbol": symbol, "error": "股票代码为空"}
    try:
        df = data_fetch.get_history_months(market, symbol, months, fetch_months=months)
        if len(df) < 30:
            raise ValueError("可用交易日太少,无法对比")

        close = df["close"].astype(float)
        score = analysis.score_only(df)
        total_return = round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2)
        return {
            "symbol": symbol,
            "df": df[["date", "close"]].copy(),
            "start": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "end": df["date"].iloc[-1].strftime("%Y-%m-%d"),
            "start_price": round(float(close.iloc[0]), 3),
            "end_price": round(float(close.iloc[-1]), 3),
            "return_pct": total_return,
            "return_1m": _period_return(close, 21),
            "return_3m": _period_return(close, 63),
            "return_6m": _period_return(close, 126),
            "annual_vol": _annual_vol(close),
            "max_drawdown": _max_drawdown(close),
            "score": score["score"],
            "direction": score["direction"],
            "signal_integrity": score["signal_integrity"],
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)[:120]}


def _build_row_from_df(symbol: str, df: pd.DataFrame) -> dict:
    if len(df) < 30:
        raise ValueError("可用交易日太少,无法对比")

    close = df["close"].astype(float)
    score = analysis.score_only(df)
    total_return = round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2)
    return {
        "symbol": symbol,
        "df": df[["date", "close"]].copy(),
        "start": df["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "start_price": round(float(close.iloc[0]), 3),
        "end_price": round(float(close.iloc[-1]), 3),
        "return_pct": total_return,
        "return_1m": _period_return(close, 21),
        "return_3m": _period_return(close, 63),
        "return_6m": _period_return(close, 126),
        "annual_vol": _annual_vol(close),
        "max_drawdown": _max_drawdown(close),
        "score": score["score"],
        "direction": score["direction"],
        "signal_integrity": score["signal_integrity"],
    }


def _fetch_a_tx_one(symbol: str, months: int) -> dict:
    """A股多股专用快路:腾讯前复权日线,可并行,比 BaoStock 多只串行快。"""
    try:
        import akshare as ak

        end = datetime.date.today()
        start = end - datetime.timedelta(days=months * 31)
        # akshare 这个接口内部带 tqdm,重定向掉进度条,避免污染后端日志。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zh_a_hist_tx(
                symbol=_a_tx_symbol(symbol),
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
                timeout=10,
            )
        if df is None or df.empty:
            raise ValueError("腾讯接口返回空数据")
        df = df.copy()
        if "amount" in df.columns and "volume" not in df.columns:
            # 腾讯源返回成交额,没有成交量。多股对比的主要指标不依赖成交量;
            # 技术评分里的量能因子用成交额做代理,作为快路的近似值。
            df["volume"] = df["amount"]
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df[["date", "open", "high", "low", "close", "volume"]].dropna().sort_values("date").reset_index(drop=True)
        return _build_row_from_df(symbol, df)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)[:120]}


def _fetch_a_fast(symbols: list[str], months: int) -> list[dict]:
    """A股多股快路：腾讯并行；个别失败再用专业源/BaoStock/东财兜底。"""
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        rows = list(pool.map(lambda s: _fetch_a_tx_one(s, months), symbols))

    failed = [r["symbol"] for r in rows if "error" in r]
    if failed:
        fallback = {r["symbol"]: r for r in rows}
        with ThreadPoolExecutor(max_workers=min(4, len(failed))) as pool:
            for row in pool.map(lambda s: _fetch_one("A股", s, months), failed):
                fallback[row["symbol"]] = row
        rows = [fallback[s] for s in symbols]
    return rows


def _fetch_fundamental_snapshot(symbol: str) -> dict:
    """A股多股对比用的基本面快照。所有字段都来自真实财务源,失败就标注错误。"""
    try:
        data = fundamentals.get_fundamentals("A股", symbol)
        if not data.get("available"):
            return {
                "symbol": symbol,
                "fundamental_available": False,
                "fundamental_error": data.get("message") or "未取到基本面数据",
            }
        metrics = data.get("metrics", {})
        return {
            "symbol": symbol,
            "fundamental_available": True,
            "fundamental_score": data.get("score"),
            "fundamental_rating": data.get("rating"),
            "pe": metrics.get("市盈率PE(TTM)"),
            "pb": metrics.get("市净率PB"),
            "pe_percentile": metrics.get("PE近五年分位%"),
            "pb_percentile": metrics.get("PB近五年分位%"),
            "roe": metrics.get("净资产收益率ROE%"),
            "gross_margin": metrics.get("毛利率%"),
            "net_margin": metrics.get("净利率%"),
            "debt_ratio": metrics.get("资产负债率%"),
            "cashflow_quality": metrics.get("现金流质量"),
            "revenue_growth_years": metrics.get("营收连续增长年数"),
            "profit_growth_years": metrics.get("净利润连续增长年数"),
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "fundamental_available": False,
            "fundamental_error": str(e)[:120],
        }


def _attach_fundamentals(metrics: list[dict]) -> tuple[list[dict], dict]:
    symbols = [m["symbol"] for m in metrics]
    fund_map = {}
    with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
        for row in pool.map(_fetch_fundamental_snapshot, symbols):
            fund_map[row["symbol"]] = row

    failed = []
    for item in metrics:
        snap = fund_map.get(item["symbol"])
        if snap:
            item.update({k: v for k, v in snap.items() if k != "symbol"})
            if not snap.get("fundamental_available"):
                failed.append({"symbol": item["symbol"], "error": snap.get("fundamental_error")})

    ok = [m for m in metrics if m.get("fundamental_available") and m.get("fundamental_score") is not None]
    summary = {}
    if ok:
        best_quality = max(ok, key=lambda x: x["fundamental_score"])
        low_pe = min(
            [m for m in ok if m.get("pe_percentile") is not None],
            key=lambda x: x["pe_percentile"],
            default=None,
        )
        summary = {
            "best_quality_symbol": best_quality["symbol"],
            "best_quality_score": best_quality["fundamental_score"],
            "best_quality_rating": best_quality.get("fundamental_rating"),
            "lowest_pe_percentile_symbol": low_pe["symbol"] if low_pe else None,
            "lowest_pe_percentile": low_pe.get("pe_percentile") if low_pe else None,
        }
    return metrics, {"failed": failed, "summary": summary}


def _composite_score(item: dict, include_fundamentals: bool) -> tuple[float, str]:
    technical = float(item.get("score") or 0)
    if include_fundamentals and item.get("fundamental_score") is not None:
        base = technical * 0.45 + float(item["fundamental_score"]) * 0.45
    else:
        base = technical * 0.8

    risk_penalty = 0.0
    annual_vol = item.get("annual_vol")
    max_drawdown = item.get("max_drawdown")
    if annual_vol is not None:
        if annual_vol >= 45:
            risk_penalty += 6
        elif annual_vol >= 30:
            risk_penalty += 3
    if max_drawdown is not None:
        dd = abs(float(max_drawdown))
        if dd >= 40:
            risk_penalty += 7
        elif dd >= 25:
            risk_penalty += 4

    valuation_bonus = 0.0
    if include_fundamentals:
        pe_pct = item.get("pe_percentile")
        pb_pct = item.get("pb_percentile")
        vals = [v for v in (pe_pct, pb_pct) if v is not None]
        if vals:
            avg_pct = sum(vals) / len(vals)
            if avg_pct <= 25:
                valuation_bonus += 6
            elif avg_pct >= 80:
                valuation_bonus -= 6
            elif avg_pct <= 45:
                valuation_bonus += 2

    score = max(0.0, min(100.0, base + valuation_bonus - risk_penalty + 10))
    if score >= 72:
        verdict = "优先观察"
    elif score >= 58:
        verdict = "可继续跟踪"
    elif score >= 45:
        verdict = "中性"
    else:
        verdict = "谨慎"
    return round(score, 1), verdict


def _normalize_weights(raw_weights: dict[str, float], symbols_ok: list[str]) -> dict[str, float]:
    cleaned = {s: max(0.0, float(raw_weights.get(s, 0) or 0)) for s in symbols_ok}
    total = sum(cleaned.values())
    if total <= 0:
        return {s: 1 / len(symbols_ok) for s in symbols_ok}
    return {s: cleaned[s] / total for s in symbols_ok}


def _portfolio_snapshot(
    merged: pd.DataFrame,
    rebased_mat: pd.DataFrame,
    symbols_ok: list[str],
    step: int,
    name: str,
    weights: dict[str, float],
) -> dict:
    """组合表现。基于已对齐的真实价格,不额外抓取数据。"""
    weights = _normalize_weights(weights, symbols_ok)
    portfolio = sum(rebased_mat[sym] * weights[sym] for sym in symbols_ok)
    ret = portfolio.pct_change().dropna()
    total_return = round(float(portfolio.iloc[-1] / portfolio.iloc[0] - 1) * 100, 2)
    annual_vol = round(float(ret.std() * np.sqrt(252) * 100), 2) if not ret.empty else 0.0
    max_drawdown = _max_drawdown(portfolio)
    risk_adjusted = round(total_return / annual_vol, 3) if annual_vol else None
    path = []
    for i in range(0, len(merged), step):
        path.append({
            "date": merged["date"].iloc[i].strftime("%Y-%m-%d"),
            "value": round(float(portfolio.iloc[i]), 2),
        })
    return {
        "type": name,
        "weights": {s: round(weights[s], 4) for s in symbols_ok},
        "symbols": symbols_ok,
        "return_pct": total_return,
        "annual_vol": annual_vol,
        "max_drawdown": max_drawdown,
        "risk_adjusted": risk_adjusted,
        "start": merged["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": merged["date"].iloc[-1].strftime("%Y-%m-%d"),
        "path": path,
    }


def _portfolio_modes(
    merged: pd.DataFrame,
    rebased_mat: pd.DataFrame,
    symbols_ok: list[str],
    metrics: list[dict],
    step: int,
    include_fundamentals: bool,
) -> list[dict]:
    metric_map = {m["symbol"]: m for m in metrics}
    modes = [
        ("等权组合", {s: 1 for s in symbols_ok}),
        ("技术评分加权", {s: metric_map[s].get("score") or 0 for s in symbols_ok}),
        ("低波动加权", {s: 1 / max(metric_map[s].get("annual_vol") or 0, 0.01) for s in symbols_ok}),
    ]
    if include_fundamentals:
        modes.append(("综合评分加权", {s: metric_map[s].get("composite_score") or 0 for s in symbols_ok}))
    return [
        _portfolio_snapshot(merged, rebased_mat, symbols_ok, step, name, weights)
        for name, weights in modes
    ]


def _correlation_summary(corr: pd.DataFrame, symbols_ok: list[str]) -> dict:
    pairs = []
    for i, a in enumerate(symbols_ok):
        for b in symbols_ok[i + 1:]:
            pairs.append({"a": a, "b": b, "value": float(corr.loc[a, b])})
    if not pairs:
        return {}
    highest = max(pairs, key=lambda x: x["value"])
    lowest = min(pairs, key=lambda x: x["value"])
    avg_abs = sum(abs(p["value"]) for p in pairs) / len(pairs)
    return {
        "highest_pair": highest,
        "lowest_pair": lowest,
        "average_abs": round(avg_abs, 3),
        "pair_count": len(pairs),
    }


def _period_strength_summary(metrics: list[dict]) -> dict:
    periods = [
        ("1月", "return_1m"),
        ("3月", "return_3m"),
        ("6月", "return_6m"),
        ("区间", "return_pct"),
    ]
    summary = {}
    for label, key in periods:
        valid = [m for m in metrics if m.get(key) is not None]
        if not valid:
            continue
        best = max(valid, key=lambda x: x[key])
        worst = min(valid, key=lambda x: x[key])
        summary[key] = {
            "label": label,
            "best_symbol": best["symbol"],
            "best_return": best[key],
            "worst_symbol": worst["symbol"],
            "worst_return": worst[key],
        }

    consistency = []
    for m in metrics:
        vals = [m.get("return_1m"), m.get("return_3m"), m.get("return_6m")]
        valid = [v for v in vals if v is not None]
        consistency.append({
            "symbol": m["symbol"],
            "positive_periods": sum(1 for v in valid if v > 0),
            "negative_periods": sum(1 for v in valid if v < 0),
            "valid_periods": len(valid),
        })
    strongest = max(consistency, key=lambda x: (x["positive_periods"], -x["negative_periods"], x["symbol"])) if consistency else None
    weakest = max(consistency, key=lambda x: (x["negative_periods"], -x["positive_periods"], x["symbol"])) if consistency else None
    summary["consistency"] = {"strongest": strongest, "weakest": weakest}
    return summary


def compare_many(market: str, symbols: list[str], months: int = 12, include_fundamentals: bool = False) -> dict:
    if market not in data_fetch.MARKETS:
        raise ValueError(f"不支持的市场:{market}")

    clean_symbols = []
    seen = set()
    for s in symbols:
        sym = s.strip().upper() if market == "美股" else s.strip()
        if sym and sym not in seen:
            clean_symbols.append(sym)
            seen.add(sym)
    clean_symbols = clean_symbols[:12]
    if len(clean_symbols) < 2:
        raise ValueError("多股对比至少需要 2 只股票")

    months = max(3, min(36, int(months)))
    include_fundamentals = bool(include_fundamentals and market == "A股")
    cache_key = (market, tuple(clean_symbols), months, include_fundamentals)
    cached = _result_cache.get(cache_key)
    if cached and time.time() - cached[0] < _RESULT_CACHE_TTL:
        return cached[1]

    if market == "A股" and len(clean_symbols) >= 3:
        rows = _fetch_a_fast(clean_symbols, months)
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(clean_symbols))) as pool:
            rows = list(pool.map(lambda s: _fetch_one(market, s, months), clean_symbols))

    ok = [r for r in rows if "error" not in r]
    failed = [{"symbol": r["symbol"], "error": r["error"]} for r in rows if "error" in r]
    if len(ok) < 2:
        raise ValueError("可成功对比的股票少于 2 只:" + "; ".join(
            f"{f['symbol']}: {f['error']}" for f in failed
        ))

    # 按日期内连接,保证每个点所有股票都有价格。
    merged = None
    for item in ok:
        s = item["df"].rename(columns={"close": item["symbol"]})
        merged = s if merged is None else pd.merge(merged, s, on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)
    if len(merged) < 20:
        raise ValueError("这些股票可对齐的交易日太少,请减少股票数量或换同一交易市场。")

    symbols_ok = [r["symbol"] for r in ok]
    close_mat = merged[symbols_ok].astype(float)

    # 归一化到 100。控制图表点数,避免前端过重。
    rebased_mat = close_mat / close_mat.iloc[0] * 100
    step = max(1, len(merged) // 260)
    rebased = []
    for i in range(0, len(merged), step):
        row = {"date": merged["date"].iloc[i].strftime("%Y-%m-%d")}
        for sym in symbols_ok:
            row[sym] = round(float(rebased_mat[sym].iloc[i]), 2)
        rebased.append(row)

    returns = close_mat.pct_change().dropna()
    corr = returns.corr().round(3).fillna(0)
    correlations = {
        a: {b: float(corr.loc[a, b]) for b in symbols_ok}
        for a in symbols_ok
    }
    correlation_summary = _correlation_summary(corr, symbols_ok)
    data_quality = {
        "aligned_days": int(len(merged)),
        "start": merged["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": merged["date"].iloc[-1].strftime("%Y-%m-%d"),
        "source_symbols": len(clean_symbols),
        "success_symbols": len(ok),
        "failed_symbols": len(failed),
    }

    # 风险调整收益:区间收益 / 年化波动,用于表格辅助排序。
    metrics = []
    for item in ok:
        risk_adj = None
        if item["annual_vol"]:
            risk_adj = round(item["return_pct"] / item["annual_vol"], 3)
        metrics.append({
            "symbol": item["symbol"],
            "start": item["start"],
            "end": item["end"],
            "start_price": item["start_price"],
            "end_price": item["end_price"],
            "return_pct": item["return_pct"],
            "return_1m": item.get("return_1m"),
            "return_3m": item.get("return_3m"),
            "return_6m": item.get("return_6m"),
            "annual_vol": item["annual_vol"],
            "max_drawdown": item["max_drawdown"],
            "risk_adjusted": risk_adj,
            "score": item["score"],
            "direction": item["direction"],
            "signal_integrity": item.get("signal_integrity") or {
                "kind": "rule_based_technical_state",
                "calibrated_probability": False,
                "decision_eligible": False,
                "validation_required": True,
            },
        })

    fundamental_info = {"failed": [], "summary": {}}
    if include_fundamentals:
        metrics, fundamental_info = _attach_fundamentals(metrics)

    for item in metrics:
        item["composite_score"], item["composite_verdict"] = _composite_score(item, include_fundamentals)

    portfolios = _portfolio_modes(merged, rebased_mat, symbols_ok, metrics, step, include_fundamentals)
    portfolio = portfolios[0]

    metrics.sort(key=lambda x: x["return_pct"], reverse=True)
    period_strength = _period_strength_summary(metrics)

    leader = metrics[0]["symbol"]
    laggard = metrics[-1]["symbol"]
    summary = (
        f"{months}个月区间内,{leader}收益最高({metrics[0]['return_pct']}%),"
        f"{laggard}收益最低({metrics[-1]['return_pct']}%)。"
    )
    best_composite = max(metrics, key=lambda x: x["composite_score"])

    result = {
        "market": market,
        "months": months,
        "symbols": symbols_ok,
        "count": len(metrics),
        "failed": failed,
        "failed_count": len(failed),
        "fundamentals_included": include_fundamentals,
        "fundamental_failed": fundamental_info["failed"],
        "fundamental_failed_count": len(fundamental_info["failed"]),
        "fundamental_summary": fundamental_info["summary"],
        "best_composite": {
            "symbol": best_composite["symbol"],
            "score": best_composite["composite_score"],
            "verdict": best_composite["composite_verdict"],
        },
        "summary": summary,
        "metrics": metrics,
        "rebased": rebased,
        "portfolio": portfolio,
        "portfolios": portfolios,
        "correlations": correlations,
        "correlation_summary": correlation_summary,
        "data_quality": data_quality,
        "period_strength": period_strength,
    }
    _result_cache[cache_key] = (time.time(), result)
    return result
