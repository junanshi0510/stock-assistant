# -*- coding: utf-8 -*-
"""
机器学习历史验证模块
====================
用技术指标作为特征，检查它们在后段历史样本中是否曾经优于简单基准。

⚠️ 诚实声明(非常重要):
单只股票的价格序列噪声极大,机器学习很容易【过拟合】——对历史拟合得很好,
对未来照样不准。因此本模块:
  1) 用【时间序列切分】(前 70% 训练 / 后 30% 测试),绝不打乱,杜绝"偷看未来"。
  2) 把【样本外(测试集)准确率 / AUC】和【基准】一并显示出来。
     —— 只有样本外准确率明显高于基准,模型才算"学到了东西"。
  3) 不发布最新时点的上涨概率。单次时间切分既没有完成概率校准，也不是可重复使用
     的真正未来样本，只能作为实验性历史诊断。
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

import analysis

# 作为特征使用的指标列
_FEATURES = [
    "rsi", "macd", "macd_hist", "kdj_k", "kdj_d", "kdj_j",
    "boll_pctb", "adx", "plus_di", "minus_di", "atr_pct",
    "vol_ratio", "obv_slope", "mom5", "mom20", "ma20_slope", "pos_52w",
]


def _build_features(df: pd.DataFrame):
    df = analysis.add_indicators(df).copy()
    close = df["close"]
    # 价格相对均线的偏离(归一化特征)
    df["px_ma20"] = close / df["ma20"] - 1
    df["px_ma60"] = close / df["ma60"] - 1
    df["ma5_ma20"] = df["ma5"] / df["ma20"] - 1
    feats = _FEATURES + ["px_ma20", "px_ma60", "ma5_ma20"]
    return df, feats


def predict(df: pd.DataFrame, horizon: int = 10) -> dict:
    """
    训练并完成一次后段历史评估；不对最新一天发布可执行预测。
    """
    if len(df) < 250:
        raise ValueError("数据太少(建议至少 1.5 年),无法可靠训练模型。")

    df, feats = _build_features(df)
    df["future_ret"] = df["close"].shift(-horizon) / df["close"] - 1
    df["label"] = (df["future_ret"] > 0).astype(int)

    # 只使用未来结果已经发生的历史样本，最后 horizon 行不参与任何输出。
    known = df.dropna(subset=feats + ["future_ret"]).copy()
    if len(known) < 150:
        raise ValueError("有效样本不足,无法训练。")

    X = known[feats].values
    y = known["label"].values

    # 时间序列切分(不打乱)
    cut = int(len(known) * 0.7)
    X_tr, X_te = X[:cut], X[cut:]
    y_tr, y_te = y[:cut], y[cut:]

    model = HistGradientBoostingClassifier(
        max_depth=3, max_iter=200, learning_rate=0.05,
        l2_regularization=1.0, random_state=42)
    model.fit(X_tr, y_tr)

    # 样本外评估
    proba_te = model.predict_proba(X_te)[:, 1]
    pred_te = (proba_te >= 0.5).astype(int)
    test_acc = float((pred_te == y_te).mean() * 100)
    try:
        auc = float(roc_auc_score(y_te, proba_te) * 100)
    except ValueError:
        auc = None
    # 基准:测试集里"总是猜上涨"的准确率(= 实际上涨比例)
    base_rate = float(y_te.mean() * 100)
    baseline_acc = max(base_rate, 100 - base_rate)

    # 评价:模型相对基准的"超额准确率"，仍然只是一次历史切分。
    edge = round(test_acc - baseline_acc, 1)
    if edge >= 5:
        verdict = "样本外略有预测力(仍需谨慎)"
    elif edge >= -2:
        verdict = "与基准接近,预测力有限"
    else:
        verdict = "未跑赢基准,基本无预测力"

    return {
        "horizon": horizon,
        "samples_total": int(len(known)),
        "train_size": int(cut),
        "test_size": int(len(known) - cut),
        "test_accuracy": round(test_acc, 1),
        "auc": round(auc, 1) if auc is not None else None,
        "baseline_accuracy": round(baseline_acc, 1),
        "edge_vs_baseline": edge,
        "verdict": verdict,
        "research_status": "historical_validation_only",
        "decision_eligible": False,
        "calibrated_probability": False,
        "latest_forecast_available": False,
        "validation": {
            "method": "chronological_70_30_holdout",
            "reusable_out_of_sample": False,
            "reason": "用户看到后继续调参会污染后段样本，且本模型未完成概率校准、跨标的验证和前瞻纸面跟踪。",
        },
    }
