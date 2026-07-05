# -*- coding: utf-8 -*-
"""
个股 vs 大盘 对比分析
=====================
把个股放进市场背景里看,让判断更可信:
  - 相对强弱:个股 vs 指数 在 1月/3月/6月/1年 的涨跌,以及【超额收益】
  - Beta:个股相对大盘的波动敏感度(>1 比大盘更猛,<1 更稳)
  - 相关性:个股与大盘同向程度
  - 再基准化对比序列(都从 100 起步)供画图

基准指数:A股=沪深300,港股=恒生指数(盈富基金02800),美股=标普500(SPY)。
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
    "港股": ("恒生指数(盈富基金)", "hk:02800"),
    "美股": ("标普500(SPY)", "us:SPY"),
}

_INDEX_CACHE_TTL = 600
_index_cache = {}


def _index_a(months):
    """A股指数:用腾讯前复权/指数日线取沪深300,带 timeout,比 BaoStock 指数接口更稳。"""
    import akshare as ak
    end = datetime.date.today()
    start = end - datetime.timedelta(days=months * 31)

    key = ("A_INDEX_000300", months)
    cached = _index_cache.get(key)
    if cached and time.time() - cached[0] < _INDEX_CACHE_TTL:
        return cached[1].copy()

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
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[(df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))]
    df = df.dropna().reset_index(drop=True)
    _index_cache[key] = (time.time(), df)
    return df.copy()


def _get_benchmark(market, months):
    name, spec = BENCHMARKS[market]
    kind, code = spec.split(":")
    if kind == "bs":
        return name, _index_a(months)
    # 港股/美股用现有管道抓 ETF 代理(盈富/SPY)
    mk = "港股" if kind == "hk" else "美股"
    df = data_fetch.get_history_months(mk, code, months, fetch_months=months)
    return name, df[["date", "close"]]


def _period_return(closes, n):
    if len(closes) <= n:
        return None
    return round((closes[-1] / closes[-1 - n] - 1) * 100, 2)


def compare(market: str, symbol: str, months: int = 12) -> dict:
    if market not in BENCHMARKS:
        raise ValueError(f"不支持的市场:{market}")

    stock = data_fetch.get_history_months(market, symbol, months, fetch_months=months)
    bench_name, bench = _get_benchmark(market, months)

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

    # Beta / 相关性(用日收益,近 1 年或全部)
    win = min(252, len(m) - 1)
    sret = pd.Series(sc).pct_change().dropna().values[-win:]
    bret = pd.Series(bc).pct_change().dropna().values[-win:]
    L = min(len(sret), len(bret))
    sret, bret = sret[-L:], bret[-L:]
    var_b = float(np.var(bret))
    beta = round(float(np.cov(sret, bret)[0, 1] / var_b), 2) if var_b > 0 else None
    corr = round(float(np.corrcoef(sret, bret)[0, 1]), 2) if L > 2 else None

    # 再基准化(都从 100 起),控制点数(最多 ~250)
    step = max(1, len(m) // 250)
    md = m.iloc[::step]
    rebased = [{
        "date": d.strftime("%Y-%m-%d"),
        "stock": round(sv / sc[0] * 100, 2),
        "index": round(bv / bc[0] * 100, 2),
    } for d, sv, bv in zip(md["date"], md["s"], md["b"])]

    # 综合判定(以最长可得周期的超额收益为主)
    last_excess = rows[-1]["excess"] if rows else 0
    if last_excess > 3:
        verdict = "跑赢大盘(相对强势)"
    elif last_excess < -3:
        verdict = "跑输大盘(相对弱势)"
    else:
        verdict = "与大盘基本同步"

    return {
        "market": market, "symbol": symbol,
        "benchmark": bench_name,
        "periods": rows,
        "beta": beta, "correlation": corr,
        "verdict": verdict,
        "rebased": rebased,
    }
