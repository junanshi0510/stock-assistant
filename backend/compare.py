# -*- coding: utf-8 -*-
"""
个股 vs 大盘 对比分析
=====================
把个股放进市场背景里看,让判断更可信:
  - 相对强弱:个股 vs 指数 在 1月/3月/6月/1年 的涨跌,以及【超额收益】
  - Beta:个股相对大盘的波动敏感度(>1 比大盘更猛,<1 更稳)
  - 相关性:个股与大盘同向程度
  - 再基准化对比序列(都从 100 起步)供画图

基准指数:A股=沪深300,港股=恒生指数(^HSI),美股=标普500(^GSPC)。
"""

import datetime
import contextlib
import io
import time
import numpy as np
import pandas as pd

import data_fetch

# market -> (指数显示名, 取数方式)
BENCHMARKS = {
    "A股": ("沪深300", "bs:sh.000300"),
    "港股": ("恒生指数(^HSI)", "yahoo:^HSI"),
    "美股": ("标普500(^GSPC)", "yahoo:^GSPC"),
}

_INDEX_CACHE_TTL = 600
_index_cache = {}


def _index_a(months):
    """A股指数:优先 BaoStock 专业免费源，失败时回退腾讯指数日线。"""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=months * 31)

    key = ("A_INDEX_000300", months)
    cached = _index_cache.get(key)
    if cached and time.time() - cached[0] < _INDEX_CACHE_TTL:
        return cached[1].copy(), cached[2]

    try:
        with data_fetch._bs_lock:  # 复用项目内 BaoStock 单连接锁
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                bs = data_fetch._bs()
                result = bs.query_history_k_data_plus(
                    "sh.000300",
                    "date,close",
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="3",
                )
            rows = []
            while result.error_code == "0" and result.next():
                rows.append(result.get_row_data())
        if result.error_code != "0" or not rows:
            raise RuntimeError(result.error_msg or "返回空数据")
        df = pd.DataFrame(rows, columns=["date", "close"])
        source = "BaoStock"
    except Exception:
        import akshare as ak
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            raw = ak.stock_zh_a_hist_tx(
                symbol="sh000300",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
                timeout=10,
            )
        if raw is None or raw.empty:
            raise RuntimeError("未取到沪深300指数")
        df = raw[["date", "close"]].copy()
        source = "腾讯证券(备用)"

    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[(df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))]
    df = df.dropna().reset_index(drop=True)
    _index_cache[key] = (time.time(), df, source)
    return df.copy(), source


def _get_benchmark(market, months):
    name, spec = BENCHMARKS[market]
    kind, code = spec.split(":")
    if kind == "bs":
        frame, source = _index_a(months)
        return name, frame, source, False
    if kind == "yahoo":
        return name, _yahoo_history(code, months), "Yahoo Finance", False
    raise ValueError(f"未知基准数据源:{kind}")


def _yahoo_history(symbol: str, months: int) -> pd.DataFrame:
    """复用核心行情层中带硬超时的 Yahoo 日线，并缓存对比序列。"""
    key = ("YAHOO", symbol.upper(), months)
    cached = _index_cache.get(key)
    if cached and time.time() - cached[0] < _INDEX_CACHE_TTL:
        return cached[1].copy()

    end = datetime.date.today()
    start = end - datetime.timedelta(days=months * 31)
    raw = data_fetch._src_yahoo(
        symbol,
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
    )
    frame = data_fetch._normalize(raw)[["date", "close"]]
    if len(frame) < 30:
        raise RuntimeError(f"Yahoo {symbol} 可用日线不足")
    _index_cache[key] = (time.time(), frame)
    return frame.copy()


def _get_stock(market: str, symbol: str, months: int):
    """对比模块使用明确、可追溯且带超时的数据源，不静默切换到新浪。"""
    if market == "A股":
        end = datetime.date.today()
        start = end - datetime.timedelta(days=months * 31)
        raw = data_fetch._src_a_baostock(
            symbol,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )
        return data_fetch._normalize(raw), "BaoStock"
    if market == "港股":
        yahoo_symbol = f"{str(int(symbol)):0>4}.HK"
        return _yahoo_history(yahoo_symbol, months), "Yahoo Finance"
    if market == "美股":
        return _yahoo_history(symbol.upper(), months), "Yahoo Finance"
    raise ValueError(f"不支持的市场:{market}")


def _period_return(closes, n):
    if len(closes) <= n:
        return None
    return round((closes[-1] / closes[-1 - n] - 1) * 100, 2)


def _max_drawdown(closes) -> float | None:
    if len(closes) < 2:
        return None
    series = pd.Series(closes, dtype="float64")
    drawdowns = series / series.cummax() - 1
    return round(float(drawdowns.min()) * 100, 2)


def compare(market: str, symbol: str, months: int = 12) -> dict:
    if market not in BENCHMARKS:
        raise ValueError(f"不支持的市场:{market}")

    stock, stock_source = _get_stock(market, symbol, months)
    bench_name, bench, bench_source, benchmark_is_proxy = _get_benchmark(market, months)

    # 按日期对齐
    s = stock[["date", "close"]].rename(columns={"close": "s"})
    b = bench.rename(columns={"close": "b"})
    m = pd.merge(s, b, on="date", how="inner").sort_values("date").reset_index(drop=True)
    if len(m) < 30:
        raise ValueError("个股与指数可对齐的交易日太少,无法对比。")

    sc = m["s"].values
    bc = m["b"].values

    # 多周期相对强弱
    periods = [("1月", 21), ("3月", 63), ("6月", 126), ("1年", 252)]
    rows = []
    for label, n in periods:
        sr = _period_return(sc, n)
        br = _period_return(bc, n)
        if sr is None or br is None:
            continue
        rows.append({"period": label, "stock": sr, "index": br,
                     "excess": round(sr - br, 2)})

    # Beta / 相关性(用日收益,近 1 年或全部)。协方差和方差统一使用样本口径(ddof=1)。
    win = min(252, len(m) - 1)
    sret = pd.Series(sc).pct_change().dropna().values[-win:]
    bret = pd.Series(bc).pct_change().dropna().values[-win:]
    L = min(len(sret), len(bret))
    sret, bret = sret[-L:], bret[-L:]
    var_b = float(np.var(bret, ddof=1)) if L > 1 else 0
    beta = round(float(np.cov(sret, bret, ddof=1)[0, 1] / var_b), 2) if var_b > 0 else None
    corr = (
        round(float(np.corrcoef(sret, bret)[0, 1]), 2)
        if L > 2 and np.std(sret) > 0 and np.std(bret) > 0
        else None
    )
    stock_volatility = round(float(np.std(sret, ddof=1) * np.sqrt(252) * 100), 2) if L > 1 else None
    benchmark_volatility = round(float(np.std(bret, ddof=1) * np.sqrt(252) * 100), 2) if L > 1 else None
    active_returns = sret - bret
    tracking_error = round(float(np.std(active_returns, ddof=1) * np.sqrt(252) * 100), 2) if L > 1 else None
    active_std = float(np.std(active_returns, ddof=1)) if L > 1 else 0
    information_ratio = (
        round(float(np.mean(active_returns) / active_std * np.sqrt(252)), 2)
        if active_std > 0
        else None
    )

    # 再基准化(都从 100 起),控制点数(最多 ~250)
    step = max(1, len(m) // 250)
    sample_positions = list(range(0, len(m), step))
    if sample_positions[-1] != len(m) - 1:
        sample_positions.append(len(m) - 1)
    md = m.iloc[sample_positions]
    rebased = [{
        "date": d.strftime("%Y-%m-%d"),
        "stock": round(sv / sc[0] * 100, 2),
        "index": round(bv / bc[0] * 100, 2),
    } for d, sv, bv in zip(md["date"], md["s"], md["b"])]

    # 综合判定使用多周期加权超额收益，避免只看某一个起点造成结论跳变。
    weights = {"1月": 0.15, "3月": 0.25, "6月": 0.25, "1年": 0.35}
    available_weight = sum(weights[row["period"]] for row in rows)
    weighted_excess = (
        round(sum(row["excess"] * weights[row["period"]] for row in rows) / available_weight, 2)
        if available_weight
        else 0
    )
    periods_outperformed = sum(1 for row in rows if row["excess"] > 0)
    if weighted_excess > 3:
        verdict = "跑赢大盘(相对强势)"
    elif weighted_excess < -3:
        verdict = "跑输大盘(相对弱势)"
    else:
        verdict = "与大盘基本同步"

    return {
        "market": market, "symbol": symbol,
        "benchmark": bench_name,
        "benchmark_source": bench_source,
        "stock_source": stock_source,
        "benchmark_is_proxy": benchmark_is_proxy,
        "as_of": m.iloc[-1]["date"].strftime("%Y-%m-%d"),
        "sample_days": int(L),
        "periods": rows,
        "beta": beta, "correlation": corr,
        "weighted_excess": weighted_excess,
        "periods_outperformed": periods_outperformed,
        "periods_available": len(rows),
        "stock_volatility": stock_volatility,
        "benchmark_volatility": benchmark_volatility,
        "stock_max_drawdown": _max_drawdown(sc[-win - 1:]),
        "benchmark_max_drawdown": _max_drawdown(bc[-win - 1:]),
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "verdict": verdict,
        "rebased": rebased,
    }
