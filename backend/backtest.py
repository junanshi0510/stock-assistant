# -*- coding: utf-8 -*-
"""
回测模块
========
衡量打分信号过去到底「准不准」—— 这是评估预测能力唯一诚实的方式。

做法:对历史上的每一个交易日,用当天(及之前)的数据算出打分信号,
再看它之后 N 个交易日的实际涨跌,统计:
    - 看涨信号的胜率(之后真的涨了的比例)
    - 看跌信号的胜率(之后真的跌了的比例)
    - 方向准确率、平均收益
    - 与「买入持有」基准的对比
    - 按打分分档的收益曲线(检验:分数越高,之后收益是否越高)

注意:回测不含交易成本/滑点,且历史表现不代表未来。
"""

import numpy as np
import pandas as pd

import analysis


def backtest(df: pd.DataFrame, horizon: int = 20) -> dict:
    """
    参数:
        df: 标准行情(date/open/high/low/close/volume)
        horizon: 前瞻交易日数(预测“之后 N 天”的涨跌)
    返回: 各项统计指标的字典。
    """
    if len(df) < 80 + horizon:
        raise ValueError("数据不足,无法回测(建议至少 1 年以上历史)。")

    df = analysis.add_indicators(df).reset_index(drop=True)
    closes = df["close"].values
    n = len(df)

    rows = []  # (score, signal, fwd_return_pct)
    for i in range(60, n - horizon):
        points, _ = analysis._evaluate(df.iloc[i], df.iloc[i - 1])
        score = float(np.clip(points, 0, 100))
        fwd = (closes[i + horizon] / closes[i] - 1) * 100
        if score >= 65:
            sig = "看涨"
        elif score <= 35:
            sig = "看跌"
        else:
            sig = "中性"
        rows.append((score, sig, fwd))

    if not rows:
        raise ValueError("可回测样本为 0。")

    res = pd.DataFrame(rows, columns=["score", "signal", "fwd"])

    # —— 基准:买入持有(所有交易日的平均前瞻收益)与上涨基础概率 ——
    base_up_rate = float((res["fwd"] > 0).mean() * 100)
    base_avg = float(res["fwd"].mean())

    # —— 按信号分组 ——
    by_signal = {}
    for sig in ["看涨", "看跌", "中性"]:
        g = res[res["signal"] == sig]
        if len(g) == 0:
            by_signal[sig] = {"count": 0}
            continue
        if sig == "看涨":
            win = float((g["fwd"] > 0).mean() * 100)
        elif sig == "看跌":
            win = float((g["fwd"] < 0).mean() * 100)
        else:
            win = float((g["fwd"] > 0).mean() * 100)
        by_signal[sig] = {
            "count": int(len(g)),
            "win_rate": round(win, 1),
            "avg_return": round(float(g["fwd"].mean()), 2),
            "median_return": round(float(g["fwd"].median()), 2),
        }

    # —— 方向准确率(只看明确的看涨/看跌信号)——
    directional = res[res["signal"].isin(["看涨", "看跌"])]
    if len(directional) > 0:
        correct = ((directional["signal"] == "看涨") & (directional["fwd"] > 0)) | \
                  ((directional["signal"] == "看跌") & (directional["fwd"] < 0))
        dir_acc = round(float(correct.mean() * 100), 1)
        dir_count = int(len(directional))
    else:
        dir_acc, dir_count = None, 0

    # —— 按打分分档(检验单调性:分越高,之后收益是否越高)——
    buckets = [(0, 35, "0-35 看跌区"), (35, 50, "35-50 偏弱"),
               (50, 65, "50-65 偏强"), (65, 100.01, "65-100 看涨区")]
    bucket_stats = []
    for lo, hi, label in buckets:
        g = res[(res["score"] >= lo) & (res["score"] < hi)]
        if len(g) == 0:
            bucket_stats.append({"range": label, "count": 0,
                                 "avg_return": None, "win_rate": None})
        else:
            bucket_stats.append({
                "range": label,
                "count": int(len(g)),
                "avg_return": round(float(g["fwd"].mean()), 2),
                "win_rate": round(float((g["fwd"] > 0).mean() * 100), 1),
            })

    return {
        "horizon": horizon,
        "samples": int(len(res)),
        "date_range": [df["date"].iloc[60].strftime("%Y-%m-%d"),
                       df["date"].iloc[n - 1].strftime("%Y-%m-%d")],
        "benchmark": {"up_rate": round(base_up_rate, 1), "avg_return": round(base_avg, 2)},
        "by_signal": by_signal,
        "directional_accuracy": dir_acc,
        "directional_count": dir_count,
        "buckets": bucket_stats,
    }
