# -*- coding: utf-8 -*-
"""Leakage-aware, multi-horizon probability research for stocks and funds.

The engine deliberately uses one fixed model family and chronological
walk-forward folds.  It is a research component, not an order generator.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ENGINE_VERSION = "calibrated_multi_horizon_alpha@1.0.0"
FEATURE_SET_VERSION = "price_risk_relative_features@1.0.0"
MODEL_FAMILY = "fixed_logistic_sigmoid_calibration"

FEATURE_COLUMNS = [
    "return_5",
    "return_20",
    "return_60",
    "trend_20",
    "trend_60",
    "volatility_20",
    "volatility_60",
    "downside_20",
    "drawdown_60",
    "relative_return_20",
    "relative_return_60",
    "benchmark_return_20",
    "benchmark_volatility_20",
    "volume_z_20",
    "xs_momentum_rank",
    "xs_low_vol_rank",
]


class AlphaForecastEngineError(RuntimeError):
    """Raised when the frozen research design cannot be evaluated."""


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _normalize_price_frame(frame: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise AlphaForecastEngineError(f"{symbol} 没有可用的真实历史数据")
    work = frame.copy()
    if "close" not in work.columns and "unit_nav" in work.columns:
        work["close"] = work["unit_nav"]
    missing = {"date", "close"} - set(work.columns)
    if missing:
        raise AlphaForecastEngineError(
            f"{symbol} 历史数据缺少字段: {','.join(sorted(missing))}"
        )
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    if "volume" not in work.columns:
        work["volume"] = np.nan
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce")
    work = (
        work.dropna(subset=["date", "close"])
        .loc[lambda value: value["close"] > 0]
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )
    if len(work) < 180:
        raise AlphaForecastEngineError(
            f"{symbol} 有效历史只有 {len(work)} 条，至少需要 180 条"
        )
    return work[["date", "close", "volume"]]


def _attach_benchmark(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None,
) -> pd.DataFrame:
    work = frame.copy()
    if benchmark is None or benchmark.empty:
        work["benchmark_close"] = np.nan
        return work
    bench = benchmark[["date", "close"]].rename(
        columns={"close": "benchmark_close"}
    )
    return pd.merge_asof(
        work.sort_values("date"),
        bench.sort_values("date"),
        on="date",
        direction="backward",
        tolerance=pd.Timedelta(days=7),
    )


def _build_symbol_panel(
    symbol: str,
    name: str,
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    horizons: list[int],
    *,
    cost_rate: float,
    objective: str,
) -> pd.DataFrame:
    work = _attach_benchmark(frame, benchmark)
    returns = work["close"].pct_change()
    work["return_5"] = work["close"].pct_change(5)
    work["return_20"] = work["close"].pct_change(20)
    work["return_60"] = work["close"].pct_change(60)
    work["trend_20"] = work["close"] / work["close"].rolling(20).mean() - 1
    work["trend_60"] = work["close"] / work["close"].rolling(60).mean() - 1
    work["volatility_20"] = returns.rolling(20).std() * math.sqrt(252)
    work["volatility_60"] = returns.rolling(60).std() * math.sqrt(252)
    work["downside_20"] = (
        returns.where(returns < 0, 0).rolling(20).std() * math.sqrt(252)
    )
    work["drawdown_60"] = (
        work["close"] / work["close"].rolling(60).max() - 1
    )

    benchmark_returns = work["benchmark_close"].pct_change()
    work["benchmark_return_20"] = work["benchmark_close"].pct_change(20)
    work["benchmark_volatility_20"] = (
        benchmark_returns.rolling(20).std() * math.sqrt(252)
    )
    work["relative_return_20"] = (
        work["return_20"] - work["benchmark_return_20"]
    )
    work["relative_return_60"] = (
        work["return_60"] - work["benchmark_close"].pct_change(60)
    )
    log_volume = np.log1p(work["volume"].where(work["volume"] > 0))
    volume_mean = log_volume.rolling(20).mean()
    volume_std = log_volume.rolling(20).std().replace(0, np.nan)
    work["volume_z_20"] = (log_volume - volume_mean) / volume_std

    for horizon in horizons:
        suffix = str(horizon)
        work[f"label_end_date_{suffix}"] = work["date"].shift(-horizon)
        gross = work["close"].shift(-horizon) / work["close"] - 1
        work[f"gross_return_{suffix}"] = gross
        benchmark_forward = (
            work["benchmark_close"].shift(-horizon)
            / work["benchmark_close"]
            - 1
        )
        work[f"benchmark_return_{suffix}"] = benchmark_forward
        if objective == "benchmark_excess_after_cost":
            target = gross - benchmark_forward - cost_rate
        else:
            target = gross - cost_rate
        work[f"target_return_{suffix}"] = target
        work[f"label_{suffix}"] = np.where(
            target.notna(),
            (target > 0).astype(int),
            np.nan,
        )

    work["symbol"] = symbol
    work["name"] = name or symbol
    return work


def _make_model() -> Pipeline:
    return Pipeline(
        [
            (
                "imputer",
                SimpleImputer(
                    strategy="median",
                    keep_empty_features=True,
                ),
            ),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=1.0,
                    class_weight="balanced",
                    max_iter=1000,
                    random_state=19,
                    solver="lbfgs",
                ),
            ),
        ]
    )


def _raw_logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1 - clipped)).reshape(-1, 1)


def _fit_calibrator(
    probability: np.ndarray,
    labels: np.ndarray,
) -> LogisticRegression | None:
    labels = np.asarray(labels, dtype=int)
    if len(labels) < 12 or len(np.unique(labels)) < 2:
        return None
    calibrator = LogisticRegression(
        C=1.0,
        max_iter=1000,
        random_state=23,
        solver="lbfgs",
    )
    calibrator.fit(_raw_logit(probability), labels)
    return calibrator


def _apply_calibrator(
    calibrator: LogisticRegression | None,
    probability: np.ndarray,
) -> np.ndarray:
    raw = np.asarray(probability, dtype=float)
    if calibrator is None:
        return np.clip(raw, 1e-6, 1 - 1e-6)
    return calibrator.predict_proba(_raw_logit(raw))[:, 1]


def _expected_calibration_error(
    labels: np.ndarray,
    probability: np.ndarray,
    bins: int = 10,
) -> tuple[float, list[dict[str, Any]]]:
    labels = np.asarray(labels, dtype=float)
    probability = np.asarray(probability, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    rows: list[dict[str, Any]] = []
    error = 0.0
    for index in range(bins):
        lower, upper = float(edges[index]), float(edges[index + 1])
        if index == bins - 1:
            mask = (probability >= lower) & (probability <= upper)
        else:
            mask = (probability >= lower) & (probability < upper)
        count = int(mask.sum())
        if not count:
            continue
        mean_probability = float(probability[mask].mean())
        hit_rate = float(labels[mask].mean())
        error += count / max(1, len(labels)) * abs(mean_probability - hit_rate)
        rows.append(
            {
                "lower": _round(lower, 3),
                "upper": _round(upper, 3),
                "count": count,
                "mean_probability": _round(mean_probability),
                "observed_hit_rate": _round(hit_rate),
            }
        )
    return float(error), rows


def _wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _bucket_spread(
    returns: np.ndarray,
    probability: np.ndarray,
) -> tuple[float | None, float | None, float | None]:
    if len(returns) < 12:
        return None, None, None
    low_cut, high_cut = np.quantile(probability, [0.25, 0.75])
    low = returns[probability <= low_cut]
    high = returns[probability >= high_cut]
    if not len(low) or not len(high):
        return None, None, None
    low_mean = float(np.mean(low))
    high_mean = float(np.mean(high))
    return high_mean - low_mean, high_mean, low_mean


def _metric_payload(
    frame: pd.DataFrame,
    probability: np.ndarray,
    *,
    baseline_probability: float,
) -> dict[str, Any]:
    labels = frame["label"].to_numpy(dtype=int)
    returns = frame["target_return"].to_numpy(dtype=float)
    probability = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    baseline = np.full(len(labels), np.clip(baseline_probability, 1e-6, 1 - 1e-6))
    brier = float(np.mean((probability - labels) ** 2))
    baseline_brier = float(np.mean((baseline - labels) ** 2))
    brier_skill = (
        1 - brier / baseline_brier if baseline_brier > 1e-12 else float("nan")
    )
    model_log_loss = float(log_loss(labels, probability, labels=[0, 1]))
    baseline_log_loss = float(log_loss(labels, baseline, labels=[0, 1]))
    auc = (
        float(roc_auc_score(labels, probability))
        if len(np.unique(labels)) == 2
        else float("nan")
    )
    ece, reliability = _expected_calibration_error(labels, probability)
    spread, high_mean, low_mean = _bucket_spread(returns, probability)
    return {
        "sample_count": len(labels),
        "date_count": int(frame["date"].nunique()),
        "symbol_count": int(frame["symbol"].nunique()),
        "observed_base_rate": _round(float(labels.mean())),
        "brier_score": _round(brier),
        "baseline_brier_score": _round(baseline_brier),
        "brier_skill_score": _round(brier_skill),
        "log_loss": _round(model_log_loss),
        "baseline_log_loss": _round(baseline_log_loss),
        "log_loss_improvement": _round(baseline_log_loss - model_log_loss),
        "roc_auc": _round(auc),
        "expected_calibration_error": _round(ece),
        "high_low_return_spread_pct": _round(
            spread * 100 if spread is not None else None
        ),
        "high_probability_bucket_return_pct": _round(
            high_mean * 100 if high_mean is not None else None
        ),
        "low_probability_bucket_return_pct": _round(
            low_mean * 100 if low_mean is not None else None
        ),
        "reliability_bins": reliability,
    }


def _gate(
    metrics: dict[str, Any],
    *,
    calibration_count: int,
    fold_stability: float | None,
    asset_type: str,
) -> dict[str, Any]:
    minimum_samples = 30 if asset_type == "stock" else 20
    minimum_dates = 5 if asset_type == "stock" else 4
    checks = [
        (
            "evaluation_samples",
            int(metrics.get("sample_count") or 0) >= minimum_samples,
            metrics.get("sample_count"),
            f">={minimum_samples}",
            "独立最终评估样本",
        ),
        (
            "evaluation_dates",
            int(metrics.get("date_count") or 0) >= minimum_dates,
            metrics.get("date_count"),
            f">={minimum_dates}",
            "最终评估日期",
        ),
        (
            "symbol_coverage",
            int(metrics.get("symbol_count") or 0) >= 4,
            metrics.get("symbol_count"),
            ">=4",
            "横截面资产覆盖",
        ),
        (
            "calibration_samples",
            int(calibration_count) >= minimum_samples,
            calibration_count,
            f">={minimum_samples}",
            "独立概率校准样本",
        ),
        (
            "brier_skill",
            (metrics.get("brier_skill_score") or -1) > 0,
            metrics.get("brier_skill_score"),
            ">0",
            "Brier Skill 优于常数基准",
        ),
        (
            "log_loss",
            (metrics.get("log_loss_improvement") or -1) > 0,
            metrics.get("log_loss_improvement"),
            ">0",
            "Log Loss 优于常数基准",
        ),
        (
            "discrimination",
            (metrics.get("roc_auc") or 0) >= 0.52,
            metrics.get("roc_auc"),
            ">=0.52",
            "样本外区分能力",
        ),
        (
            "calibration_error",
            metrics.get("expected_calibration_error") is not None
            and metrics["expected_calibration_error"] <= 0.12,
            metrics.get("expected_calibration_error"),
            "<=0.12",
            "期望校准误差",
        ),
        (
            "economic_spread",
            (metrics.get("high_low_return_spread_pct") or -1) > 0,
            metrics.get("high_low_return_spread_pct"),
            ">0%",
            "高低概率组成本后收益差",
        ),
        (
            "fold_stability",
            fold_stability is not None and fold_stability >= 0.5,
            _round(fold_stability),
            ">=0.50",
            "跨折方向稳定率",
        ),
    ]
    rows = [
        {
            "code": code,
            "passed": bool(passed),
            "value": value,
            "threshold": threshold,
            "label": label,
        }
        for code, passed, value, threshold, label in checks
    ]
    return {
        "status": "passed" if all(item["passed"] for item in rows) else "abstain",
        "passed": all(item["passed"] for item in rows),
        "passed_count": sum(item["passed"] for item in rows),
        "total_count": len(rows),
        "checks": rows,
        "notice": (
            "全部固定门槛通过，仅表示该冻结方法在滚动样本外证据中具有可发布资格；"
            "仍需真实前瞻批次通过后才能进入决策层。"
            if all(item["passed"] for item in rows)
            else "至少一个固定门槛未通过，本周期明确弃权，不发布上涨或跑赢概率。"
        ),
    }


def _walk_forward_predictions(
    training: pd.DataFrame,
    *,
    horizon: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    dates = np.array(sorted(training["date"].dropna().unique()))
    minimum_training_dates = max(180, horizon * 3)
    if len(dates) < minimum_training_dates + horizon * 2:
        return pd.DataFrame(), []
    remaining = dates[minimum_training_dates:]
    chunks = [
        chunk
        for chunk in np.array_split(remaining, min(5, max(2, len(remaining) // horizon)))
        if len(chunk)
    ]
    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for fold_index, chunk in enumerate(chunks, start=1):
        test_start = pd.Timestamp(chunk[0])
        test_dates = [pd.Timestamp(value) for value in chunk[::horizon]]
        if not test_dates:
            test_dates = [pd.Timestamp(chunk[-1])]
        train_rows = training[
            training["label_end_date"] < test_start
        ].copy()
        test_rows = training[training["date"].isin(test_dates)].copy()
        if (
            len(train_rows) < 120
            or train_rows["symbol"].nunique() < 4
            or train_rows["label"].nunique() < 2
            or test_rows.empty
        ):
            continue
        model = _make_model()
        model.fit(train_rows[FEATURE_COLUMNS], train_rows["label"].astype(int))
        test_rows["raw_probability"] = model.predict_proba(
            test_rows[FEATURE_COLUMNS]
        )[:, 1]
        test_rows["fold"] = fold_index
        predictions.append(test_rows)
        folds.append(
            {
                "fold": fold_index,
                "train_start": train_rows["date"].min().date().isoformat(),
                "train_label_end": (
                    train_rows["label_end_date"].max().date().isoformat()
                ),
                "test_start": test_start.date().isoformat(),
                "test_end": pd.Timestamp(chunk[-1]).date().isoformat(),
                "train_samples": len(train_rows),
                "test_samples": len(test_rows),
                "test_dates": len(test_dates),
                "purged": bool(
                    train_rows["label_end_date"].max() < test_start
                ),
            }
        )
    if not predictions:
        return pd.DataFrame(), folds
    return pd.concat(predictions, ignore_index=True), folds


def _fold_stability(
    frame: pd.DataFrame,
    probability: np.ndarray,
) -> tuple[float | None, list[dict[str, Any]]]:
    work = frame.copy()
    work["probability"] = probability
    rows: list[dict[str, Any]] = []
    for fold, group in work.groupby("fold"):
        labels = group["label"].to_numpy(dtype=int)
        probs = group["probability"].to_numpy(dtype=float)
        returns = group["target_return"].to_numpy(dtype=float)
        auc = (
            float(roc_auc_score(labels, probs))
            if len(np.unique(labels)) == 2
            else None
        )
        spread, _, _ = _bucket_spread(returns, probs)
        positive = bool(
            (auc is None or auc >= 0.5)
            and spread is not None
            and spread > 0
        )
        rows.append(
            {
                "fold": int(fold),
                "samples": len(group),
                "roc_auc": _round(auc),
                "high_low_return_spread_pct": _round(
                    spread * 100 if spread is not None else None
                ),
                "positive": positive,
            }
        )
    evaluable = [item for item in rows if item["high_low_return_spread_pct"] is not None]
    if not evaluable:
        return None, rows
    return (
        sum(item["positive"] for item in evaluable) / len(evaluable),
        rows,
    )


def _neighborhood(
    evaluated: pd.DataFrame,
    probability: float,
) -> dict[str, Any]:
    if evaluated.empty:
        return {
            "sample_count": 0,
            "hit_rate": None,
            "wilson_low": None,
            "wilson_high": None,
            "median_target_return_pct": None,
            "q25_target_return_pct": None,
            "q75_target_return_pct": None,
        }
    work = evaluated.copy()
    work["distance"] = (work["calibrated_probability"] - probability).abs()
    nearby = work[work["distance"] <= 0.075].sort_values("distance")
    if len(nearby) < min(20, len(work)):
        nearby = work.sort_values("distance").head(min(30, len(work)))
    labels = nearby["label"].astype(int)
    returns = nearby["target_return"].astype(float)
    low, high = _wilson_interval(int(labels.sum()), len(labels))
    return {
        "sample_count": len(nearby),
        "hit_rate": _round(float(labels.mean())),
        "wilson_low": _round(low),
        "wilson_high": _round(high),
        "median_target_return_pct": _round(float(returns.median()) * 100),
        "q25_target_return_pct": _round(float(returns.quantile(0.25)) * 100),
        "q75_target_return_pct": _round(float(returns.quantile(0.75)) * 100),
    }


def _latest_forecasts(
    training: pd.DataFrame,
    latest: pd.DataFrame,
    oos: pd.DataFrame,
    *,
    horizon: int,
    gate: dict[str, Any],
    base_rate: float,
) -> list[dict[str, Any]]:
    if (
        training.empty
        or latest.empty
        or training["label"].nunique() < 2
        or oos.empty
    ):
        return []
    model = _make_model()
    model.fit(training[FEATURE_COLUMNS], training["label"].astype(int))
    raw = model.predict_proba(latest[FEATURE_COLUMNS])[:, 1]
    final_calibrator = _fit_calibrator(
        oos["raw_probability"].to_numpy(dtype=float),
        oos["label"].to_numpy(dtype=int),
    )
    calibrated = _apply_calibrator(final_calibrator, raw)
    evaluated = oos.copy()
    evaluated["calibrated_probability"] = _apply_calibrator(
        final_calibrator,
        evaluated["raw_probability"].to_numpy(dtype=float),
    )
    rows: list[dict[str, Any]] = []
    calendar_days = max(1, math.ceil(horizon * 7 / 5) + 3)
    for (_, item), raw_probability, probability in zip(
        latest.iterrows(), raw, calibrated
    ):
        as_of = pd.Timestamp(item["date"]).date()
        if gate.get("passed"):
            if probability >= max(0.57, base_rate + 0.07):
                stance = "看多候选"
            elif probability <= min(0.43, base_rate - 0.07):
                stance = "回避候选"
            else:
                stance = "中性观察"
        else:
            stance = "证据不足·弃权"
        rows.append(
            {
                "symbol": str(item["symbol"]),
                "name": str(item["name"]),
                "horizon_sessions": horizon,
                "as_of_date": as_of.isoformat(),
                "eligible_after": (
                    as_of + dt.timedelta(days=calendar_days)
                ).isoformat(),
                "start_value": _round(item["close"], 8),
                "benchmark_start_value": _round(
                    item.get("benchmark_close"), 8
                ),
                "raw_probability": _round(raw_probability),
                "shadow_calibrated_probability": _round(probability),
                "published_probability": (
                    _round(probability) if gate.get("passed") else None
                ),
                "base_rate": _round(base_rate),
                "stance": stance,
                "historical_gate_passed": bool(gate.get("passed")),
                "decision_eligible": False,
                "release_state": (
                    "historical_validated_shadow"
                    if gate.get("passed")
                    else "abstained"
                ),
                "neighborhood": _neighborhood(evaluated, float(probability)),
            }
        )
    return rows


def _consensus(
    forecasts_by_horizon: dict[int, list[dict[str, Any]]],
    *,
    asset_type: str,
) -> list[dict[str, Any]]:
    symbols = sorted(
        {
            item["symbol"]
            for rows in forecasts_by_horizon.values()
            for item in rows
        }
    )
    tactical = {5, 20} if asset_type == "stock" else {20}
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        forecasts = [
            item
            for rows in forecasts_by_horizon.values()
            for item in rows
            if item["symbol"] == symbol
        ]
        publishable = [
            item for item in forecasts if item.get("published_probability") is not None
        ]
        if not publishable:
            state = "abstain"
            label = "全部周期弃权"
        else:
            bullish = [
                item
                for item in publishable
                if float(item["published_probability"]) >= 0.57
            ]
            bearish = [
                item
                for item in publishable
                if float(item["published_probability"]) <= 0.43
            ]
            if bullish and bearish:
                state, label = "conflict", "长短周期冲突"
            elif len(bullish) >= max(1, math.ceil(len(publishable) / 2)):
                state, label = "supportive", "多周期证据偏正"
            elif len(bearish) >= max(1, math.ceil(len(publishable) / 2)):
                state, label = "defensive", "多周期证据偏弱"
            else:
                state, label = "neutral", "多周期证据中性"
        tactical_probs = [
            float(item["published_probability"])
            for item in publishable
            if item["horizon_sessions"] in tactical
        ]
        strategic_probs = [
            float(item["published_probability"])
            for item in publishable
            if item["horizon_sessions"] not in tactical
        ]
        rows.append(
            {
                "symbol": symbol,
                "name": forecasts[0]["name"] if forecasts else symbol,
                "state": state,
                "label": label,
                "tactical_probability": _round(
                    float(np.mean(tactical_probs)) if tactical_probs else None
                ),
                "strategic_probability": _round(
                    float(np.mean(strategic_probs)) if strategic_probs else None
                ),
                "published_horizons": len(publishable),
                "decision_eligible": bool(
                    publishable
                    and all(item.get("decision_eligible") for item in publishable)
                ),
            }
        )
    return rows


def run_alpha_forecast_research(
    *,
    frames: dict[str, pd.DataFrame],
    names: dict[str, str],
    benchmark_frame: pd.DataFrame | None,
    policy: dict[str, Any],
    source_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the frozen multi-horizon design and return auditable research facts."""
    asset_type = str(policy.get("asset_type") or "")
    if asset_type not in {"stock", "fund"}:
        raise AlphaForecastEngineError("asset_type 必须是 stock 或 fund")
    horizons = [int(value) for value in policy.get("horizons") or []]
    expected = [5, 20, 60] if asset_type == "stock" else [20, 60, 120]
    if horizons != expected:
        raise AlphaForecastEngineError(
            f"冻结周期必须是 {expected}，不允许运行后挑选周期"
        )
    objective = str(policy.get("objective") or "")
    expected_objective = (
        "benchmark_excess_after_cost"
        if asset_type == "stock"
        else "positive_return_after_cost"
    )
    if objective != expected_objective:
        raise AlphaForecastEngineError("预测目标与资产类型不一致")
    if len(frames) < 4:
        raise AlphaForecastEngineError("至少需要 4 个真实资产序列形成横截面")

    normalized_benchmark = (
        _normalize_price_frame(benchmark_frame, symbol="benchmark")
        if benchmark_frame is not None and not benchmark_frame.empty
        else None
    )
    if asset_type == "stock" and normalized_benchmark is None:
        raise AlphaForecastEngineError("股票概率研究必须有真实基准序列")

    cost_bps = float(policy.get("round_trip_cost_bps") or 0)
    cost_rate = cost_bps / 10_000
    panels: list[pd.DataFrame] = []
    rejected: list[dict[str, str]] = []
    for symbol, frame in frames.items():
        try:
            normalized = _normalize_price_frame(frame, symbol=symbol)
            panels.append(
                _build_symbol_panel(
                    str(symbol),
                    str(names.get(symbol) or symbol),
                    normalized,
                    normalized_benchmark,
                    horizons,
                    cost_rate=cost_rate,
                    objective=objective,
                )
            )
        except Exception as error:
            rejected.append(
                {"symbol": str(symbol), "error": str(error)[:300]}
            )
    if len(panels) < 4:
        raise AlphaForecastEngineError(
            f"有效资产序列只有 {len(panels)} 个，至少需要 4 个"
        )
    panel = pd.concat(panels, ignore_index=True)
    panel["xs_momentum_rank"] = panel.groupby("date")["return_20"].rank(
        pct=True, method="average"
    )
    panel["xs_low_vol_rank"] = 1 - panel.groupby("date")[
        "volatility_20"
    ].rank(pct=True, method="average")
    panel = panel.replace([np.inf, -np.inf], np.nan)
    core_features = ["return_20", "trend_20", "volatility_20"]
    feature_ready = panel[core_features].notna().all(axis=1)

    horizon_results: list[dict[str, Any]] = []
    forecasts_by_horizon: dict[int, list[dict[str, Any]]] = {}
    for horizon in horizons:
        suffix = str(horizon)
        work = panel[feature_ready].copy()
        work["label_end_date"] = work[f"label_end_date_{suffix}"]
        work["label"] = work[f"label_{suffix}"]
        work["target_return"] = work[f"target_return_{suffix}"]
        work["gross_return"] = work[f"gross_return_{suffix}"]
        work["benchmark_forward_return"] = work[
            f"benchmark_return_{suffix}"
        ]
        training = work.dropna(
            subset=["label_end_date", "label", "target_return"]
        ).copy()
        training["label"] = training["label"].astype(int)
        latest = (
            work.sort_values("date")
            .groupby("symbol", as_index=False)
            .tail(1)
            .copy()
        )
        oos, folds = _walk_forward_predictions(
            training,
            horizon=horizon,
        )
        if oos.empty or oos["date"].nunique() < 4:
            gate = _gate(
                {},
                calibration_count=0,
                fold_stability=None,
                asset_type=asset_type,
            )
            forecasts: list[dict[str, Any]] = []
            horizon_results.append(
                {
                    "horizon_sessions": horizon,
                    "status": "insufficient_oos_history",
                    "training_samples": len(training),
                    "walk_forward_folds": folds,
                    "calibration": {"sample_count": 0},
                    "evaluation": {"sample_count": 0},
                    "historical_gate": gate,
                    "forecasts": forecasts,
                }
            )
            forecasts_by_horizon[horizon] = forecasts
            continue

        oos_dates = sorted(oos["date"].unique())
        split_index = max(2, min(len(oos_dates) - 2, math.ceil(len(oos_dates) * 0.55)))
        calibration_dates = set(oos_dates[:split_index])
        calibration_rows = oos[oos["date"].isin(calibration_dates)].copy()
        evaluation_rows = oos[~oos["date"].isin(calibration_dates)].copy()
        calibrator = _fit_calibrator(
            calibration_rows["raw_probability"].to_numpy(dtype=float),
            calibration_rows["label"].to_numpy(dtype=int),
        )
        calibrated_eval = _apply_calibrator(
            calibrator,
            evaluation_rows["raw_probability"].to_numpy(dtype=float),
        )
        baseline_probability = float(calibration_rows["label"].mean())
        metrics = _metric_payload(
            evaluation_rows,
            calibrated_eval,
            baseline_probability=baseline_probability,
        )
        stability, fold_metrics = _fold_stability(
            evaluation_rows,
            calibrated_eval,
        )
        gate = _gate(
            metrics,
            calibration_count=len(calibration_rows),
            fold_stability=stability,
            asset_type=asset_type,
        )
        forecasts = _latest_forecasts(
            training,
            latest,
            oos,
            horizon=horizon,
            gate=gate,
            base_rate=baseline_probability,
        )
        forecasts_by_horizon[horizon] = forecasts
        horizon_results.append(
            {
                "horizon_sessions": horizon,
                "status": (
                    "historical_gate_passed"
                    if gate["passed"]
                    else "historical_gate_failed"
                ),
                "training_samples": len(training),
                "training_dates": int(training["date"].nunique()),
                "walk_forward_folds": folds,
                "calibration": {
                    "sample_count": len(calibration_rows),
                    "date_count": int(calibration_rows["date"].nunique()),
                    "start": calibration_rows["date"].min().date().isoformat(),
                    "end": calibration_rows["date"].max().date().isoformat(),
                    "base_rate": _round(baseline_probability),
                    "method": "sigmoid_on_prior_walk_forward_predictions",
                    "fitted": calibrator is not None,
                },
                "evaluation": {
                    **metrics,
                    "start": evaluation_rows["date"].min().date().isoformat(),
                    "end": evaluation_rows["date"].max().date().isoformat(),
                    "fold_stability": _round(stability),
                    "folds": fold_metrics,
                },
                "historical_gate": gate,
                "forecasts": forecasts,
            }
        )

    flattened_forecasts = [
        item
        for rows in forecasts_by_horizon.values()
        for item in rows
    ]
    return {
        "schema_version": "alpha_forecast_research_result.v1",
        "engine_version": ENGINE_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "model_family": MODEL_FAMILY,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "policy": policy,
        "methodology": {
            "split": "chronological_grouped_walk_forward",
            "purging": "train label_end_date strictly before test_start",
            "probability_calibration": (
                "sigmoid fitted on prior out-of-sample predictions; "
                "final metrics use later untouched out-of-sample dates"
            ),
            "parameter_search": False,
            "prediction_target": objective,
            "costs_in_label": True,
            "automatic_trading": False,
        },
        "data_quality": {
            "requested_assets": len(frames),
            "loaded_assets": len(panels),
            "rejected_assets": rejected,
            "panel_rows": len(panel),
            "panel_start": panel["date"].min().date().isoformat(),
            "panel_end": panel["date"].max().date().isoformat(),
            "source_evidence": source_evidence or {},
        },
        "horizons": horizon_results,
        "forecasts": flattened_forecasts,
        "consensus": _consensus(
            forecasts_by_horizon,
            asset_type=asset_type,
        ),
        "decision_notice": (
            "概率只在固定历史样本外门槛全部通过时发布；首次发布仍是 shadow。"
            "同一预登记项目必须积累独立真实前瞻结果并再次通过 Brier、校准和"
            "经济性门槛，才可标记为 decision_eligible。系统不承诺收益，也不自动下单。"
        ),
    }
