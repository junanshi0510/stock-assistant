# -*- coding: utf-8 -*-
"""
分析与打分模块
==============
1) 计算一整套常用技术指标(均线及斜率、RSI、MACD、KDJ、布林带、ATR、ADX、OBV、
   多周期动量、52周位置、量价关系)。
2) 用【加权多因子模型】把这些指标综合成 0-100 的「看涨打分」,并由打分映射出一个
   「模型估计上涨概率」。
3) 每个因子的贡献都透明列出(加了多少分、为什么)。

⚠️ 重要说明:这是基于历史价格的【量化信号】,不是预测,更不是投资建议。
没有任何模型能准确预测股市。想知道这套信号过去到底准不准,请看「回测」(backtest.py)。
"""

import numpy as np
import pandas as pd


# ============================================================
# 指标计算
# ============================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在行情 DataFrame 上追加全部技术指标列。"""
    df = df.copy()
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

    # —— 均线(趋势)——
    for n in (5, 10, 20, 60):
        df[f"ma{n}"] = close.rolling(n).mean()
    # MA20 斜率(用近 5 日变化率衡量趋势方向与力度,%)
    df["ma20_slope"] = (df["ma20"] - df["ma20"].shift(5)) / df["ma20"].shift(5) * 100

    # —— RSI(14)——
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # —— MACD(动量)——
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # —— KDJ(超买超卖 + 金叉死叉)——
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    # —— 布林带(20, 2σ)——
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["boll_mid"] = mid
    df["boll_up"] = mid + 2 * std
    df["boll_low"] = mid - 2 * std
    # %B:价格在布林带中的相对位置(0=下轨, 1=上轨)
    df["boll_pctb"] = (close - df["boll_low"]) / (df["boll_up"] - df["boll_low"]).replace(0, np.nan)

    # —— ATR(14)波动率(占价格百分比)——
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["atr_pct"] = atr / close * 100

    # —— ADX(14)趋势强度 ——
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr_w = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    # —— 量能 ——
    df["vol_ratio"] = vol.rolling(5).mean() / vol.rolling(20).mean()
    # OBV(能量潮)及其 10 日斜率
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    df["obv"] = obv
    df["obv_slope"] = obv.diff(10)

    # —— 多周期动量 ——
    df["mom5"] = close.pct_change(5) * 100
    df["mom20"] = close.pct_change(20) * 100

    # —— 52 周(约 250 交易日)高低位置 ——
    win = min(250, len(df))
    hh = close.rolling(win, min_periods=20).max()
    ll = close.rolling(win, min_periods=20).min()
    df["pos_52w"] = (close - ll) / (hh - ll).replace(0, np.nan) * 100

    return df


# ============================================================
# 加权多因子打分
# ============================================================

def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def _evaluate(last, prev):
    """对单根 K 线(及前一根,用于金叉死叉)按多因子规则打分。
    返回 (points, reasons)。被 score() 和回测复用,保证逻辑一致。"""
    points = 50.0
    reasons = []

    def add(name, delta, detail):
        nonlocal points
        points += delta
        reasons.append((name, round(delta, 1), detail))

    # 1) 均线多空排列(±18,趋势是核心)
    c, m5, m20, m60 = last["close"], last["ma5"], last["ma20"], last["ma60"]
    if c > m5 > m20 > m60:
        add("均线排列", 18, "完美多头排列(价>MA5>MA20>MA60)")
    elif c > m20 > m60:
        add("均线排列", 11, "多头排列(价>MA20>MA60)")
    elif c < m5 < m20 < m60:
        add("均线排列", -18, "完美空头排列(价<MA5<MA20<MA60)")
    elif c < m20 < m60:
        add("均线排列", -11, "空头排列(价<MA20<MA60)")
    else:
        add("均线排列", 0, "均线交织,趋势不明")

    # 2) MA20 斜率(±8,趋势力度)
    slope = last["ma20_slope"]
    if pd.notna(slope):
        s = _clip(slope * 2, -8, 8)
        add("趋势力度", s, f"MA20 斜率 {slope:+.1f}%({'走强' if slope > 0 else '走弱'})")

    # 3) ADX 趋势强度(±6,强趋势按方向加分)
    adx, pdi, mdi = last["adx"], last["plus_di"], last["minus_di"]
    if pd.notna(adx):
        if adx >= 25:
            d = 6 if pdi > mdi else -6
            add("ADX强度", d, f"ADX={adx:.0f} 强趋势,方向{'向上' if pdi > mdi else '向下'}")
        else:
            add("ADX强度", 0, f"ADX={adx:.0f} 趋势偏弱/震荡")

    # 4) MACD(±10)
    if last["macd_hist"] > 0:
        add("MACD", 10 if last["macd"] > 0 else 6, "MACD 红柱,动量向上")
    else:
        add("MACD", -10 if last["macd"] < 0 else -6, "MACD 绿柱,动量向下")

    # 5) RSI(±8)
    rsi = last["rsi"]
    if rsi > 75:
        add("RSI", -8, f"RSI={rsi:.0f} 严重超买,回调风险高")
    elif rsi > 65:
        add("RSI", 3, f"RSI={rsi:.0f} 偏强(注意过热)")
    elif rsi < 25:
        add("RSI", 6, f"RSI={rsi:.0f} 严重超卖,反弹概率大")
    elif rsi < 35:
        add("RSI", 3, f"RSI={rsi:.0f} 偏弱(或现超卖)")
    else:
        add("RSI", 2, f"RSI={rsi:.0f} 健康区间")

    # 6) KDJ(±8,金叉/死叉 + 超买超卖)
    k, d, j = last["kdj_k"], last["kdj_d"], last["kdj_j"]
    if pd.notna(k):
        if prev["kdj_k"] <= prev["kdj_d"] and k > d:
            add("KDJ", 8, "KDJ 金叉,短线转强")
        elif prev["kdj_k"] >= prev["kdj_d"] and k < d:
            add("KDJ", -8, "KDJ 死叉,短线转弱")
        elif j < 0:
            add("KDJ", 4, "J<0 超卖,易反弹")
        elif j > 100:
            add("KDJ", -4, "J>100 超买,易回落")
        else:
            add("KDJ", 0, f"KDJ 中性(K={k:.0f})")

    # 7) 布林带位置(±8)
    pctb = last["boll_pctb"]
    if pd.notna(pctb):
        if pctb > 1:
            add("布林带", -6, "突破上轨,短期过热")
        elif pctb > 0.8:
            add("布林带", 5, "贴近上轨,强势")
        elif pctb < 0:
            add("布林带", 6, "跌破下轨,超跌反弹概率大")
        elif pctb < 0.2:
            add("布林带", 3, "贴近下轨,偏弱")
        else:
            add("布林带", 0, f"%B={pctb:.2f} 居中")

    # 8) 多周期动量(±10)
    mom20, mom5 = last["mom20"], last["mom5"]
    m = _clip(mom20 * 0.4, -6, 6) + _clip(mom5 * 0.6, -4, 4)
    add("动量", m, f"20日 {mom20:+.1f}% / 5日 {mom5:+.1f}%")

    # 9) 量价配合(±8)
    vr = last["vol_ratio"]
    obv_up = last["obv_slope"] > 0
    if pd.notna(vr):
        if vr > 1.2 and c > m5 and obv_up:
            add("量价", 8, "放量上攻 + OBV走高,资金流入")
        elif vr > 1.2 and c < m5:
            add("量价", -5, "放量下跌,抛压重")
        elif vr < 0.7:
            add("量价", -2, "缩量,人气不足")
        else:
            add("量价", 2 if obv_up else -2, f"量比 {vr:.2f},OBV{'上行' if obv_up else '下行'}")

    # 10) 52周位置(±6,过高过低都给提示)
    pos = last["pos_52w"]
    if pd.notna(pos):
        if pos > 90:
            add("52周位置", -3, f"处于52周高位({pos:.0f}%),追高风险")
        elif pos < 15:
            add("52周位置", 4, f"处于52周低位({pos:.0f}%),低吸机会")
        else:
            add("52周位置", 0, f"52周位置 {pos:.0f}%")

    return points, reasons


def score_to_probability(total: float) -> float:
    """打分(0-100)-> 估计上涨概率(0-100)。logistic 映射,保守。"""
    prob = 1 / (1 + np.exp(-0.045 * (total - 50)))
    return round(float(prob) * 100, 1)


def score(df: pd.DataFrame) -> dict:
    """
    综合多因子打分(0-100,50 为中性),并映射出估计上涨概率。
    返回:score / probability / direction / reasons / indicators / df。
    """
    if len(df) < 60:
        raise ValueError("数据太少(不足 60 个交易日),无法可靠计算指标。")

    df = add_indicators(df)
    points, reasons = _evaluate(df.iloc[-1], df.iloc[-2])
    last = df.iloc[-1]

    total = float(np.clip(points, 0, 100))
    probability = score_to_probability(total)

    if total >= 65:
        direction = "看涨"
    elif total <= 35:
        direction = "看跌"
    else:
        direction = "中性/震荡"

    def r2(x):
        return round(float(x), 2) if pd.notna(x) else None

    return {
        "score": round(total, 1),
        "probability": probability,
        "direction": direction,
        "reasons": reasons,
        "indicators": {
            "收盘价": r2(last["close"]),
            "MA5": r2(last["ma5"]),
            "MA20": r2(last["ma20"]),
            "MA60": r2(last["ma60"]),
            "RSI": r2(last["rsi"]),
            "MACD柱": r2(last["macd_hist"]),
            "KDJ-K": r2(last["kdj_k"]),
            "布林%B": r2(last["boll_pctb"]),
            "ADX": r2(last["adx"]),
            "ATR波动%": r2(last["atr_pct"]),
            "量比": r2(last["vol_ratio"]),
            "20日动量%": r2(last["mom20"]),
            "52周位置%": r2(last["pos_52w"]),
        },
        "df": df,
    }


def score_only(df: pd.DataFrame) -> dict:
    """轻量版:只返回打分/概率/方向/收盘价(批量扫描用,不带 df)。"""
    r = score(df)
    return {
        "score": r["score"],
        "probability": r["probability"],
        "direction": r["direction"],
        "close": r["indicators"]["收盘价"],
    }
