# -*- coding: utf-8 -*-
"""Walk-forward portfolio construction and paper-rebalance research.

The engine deliberately optimises risk, not historical return.  It freezes the
user's current direct-stock sleeve, estimates covariance only from each
trailing training window, applies the resulting weights to the following
holdout window, and charges turnover-dependent trading costs.  Results remain
research-only and never authorize broker execution.
"""

from __future__ import annotations

import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import NormalDist
from typing import Any, Callable

import numpy as np
import pandas as pd

import data_fetch
import portfolio_valuation
import storage
from background_jobs import BackgroundJobRepository
from portfolio_quant_repository import (
    PortfolioQuantConflictError,
    PortfolioQuantNotFoundError,
    PortfolioQuantRepository,
    repository,
    sha256_payload,
)
from task_queue import (
    QUEUE_MARKET,
    TaskQueueUnavailableError,
    enqueue_background_job,
    uses_celery_queue,
)


SCHEMA_VERSION = "portfolio_quant_research.v1"
ENGINE_VERSION = "portfolio_walk_forward_optimizer@1.0.0"
POLICY_SCHEMA_VERSION = "portfolio_quant_policy.v1"
SUPPORTED_METHODS = {
    "equal_weight",
    "inverse_volatility",
    "risk_parity",
    "minimum_variance",
}
METHOD_LABELS = {
    "current_weights": "当前权重再平衡",
    "equal_weight": "等权",
    "inverse_volatility": "逆波动",
    "risk_parity": "风险平价",
    "minimum_variance": "最小方差",
}
SUPPORTED_MARKETS = {"A股", "港股", "美股"}
MAX_ASSETS = 12
MIN_ASSETS = 2
TRADING_DAYS = 252
COVARIANCE_SHRINKAGE = 0.25
PROFESSIONAL_HISTORY_SOURCES = {
    "Tushare",
    "Polygon",
    "AlphaVantage",
}
MAX_HISTORY_STALENESS_DAYS = 7


class PortfolioQuantInputError(ValueError):
    pass


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _round(value: Any, digits: int = 3) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def normalize_policy(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    method = str(raw.get("construction_method") or "risk_parity").strip()
    if method not in SUPPORTED_METHODS:
        raise PortfolioQuantInputError("不支持的组合构建方法")
    lookback_days = int(raw.get("lookback_days") or 252)
    if lookback_days not in {126, 252, 504}:
        raise PortfolioQuantInputError(
            "估计窗口只能是 126、252 或 504 个共同交易日"
        )
    rebalance_days = int(raw.get("rebalance_days") or 21)
    if rebalance_days not in {21, 63}:
        raise PortfolioQuantInputError(
            "再平衡周期只能是 21 或 63 个共同交易日"
        )
    values = {
        "commission_bps": _number(raw.get("commission_bps"), 5.0),
        "slippage_bps": _number(raw.get("slippage_bps"), 10.0),
        "sell_tax_bps": _number(raw.get("sell_tax_bps"), 0.0),
        "max_turnover_pct": _number(
            raw.get("max_turnover_pct"), 35.0
        ),
        "max_position_pct": _number(
            raw.get("max_position_pct"), 30.0
        ),
        "minimum_trade_amount_cny": _number(
            raw.get("minimum_trade_amount_cny"), 1000.0
        ),
    }
    ranges = {
        "commission_bps": (0.0, 100.0),
        "slippage_bps": (0.0, 200.0),
        "sell_tax_bps": (0.0, 200.0),
        "max_turnover_pct": (5.0, 100.0),
        "max_position_pct": (5.0, 100.0),
        "minimum_trade_amount_cny": (0.0, 10_000_000.0),
    }
    for key, (minimum, maximum) in ranges.items():
        value = values[key]
        if value is None or value < minimum or value > maximum:
            raise PortfolioQuantInputError(
                f"{key} 必须在 {minimum:g} 至 {maximum:g} 之间"
            )
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "construction_method": method,
        "lookback_days": lookback_days,
        "rebalance_days": rebalance_days,
        "commission_bps": round(float(values["commission_bps"]), 4),
        "slippage_bps": round(float(values["slippage_bps"]), 4),
        "sell_tax_bps": round(float(values["sell_tax_bps"]), 4),
        "max_turnover_pct": round(
            float(values["max_turnover_pct"]), 4
        ),
        "max_position_pct": round(
            float(values["max_position_pct"]), 4
        ),
        "minimum_trade_amount_cny": round(
            float(values["minimum_trade_amount_cny"]), 2
        ),
        "covariance_estimator": "sample_covariance_25pct_diagonal_shrinkage",
        "expected_return_model": "none",
        "objective": "risk_only_no_historical_return_maximization",
    }


def _compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: profile.get(key)
        for key in (
            "configured",
            "profile_version_id",
            "version_no",
            "payload_sha256",
            "integrity_verified",
            "risk",
            "horizon",
            "experience_level",
            "primary_objective",
            "monthly_budget",
            "max_single_ratio",
            "max_equity_ratio",
            "max_industry_ratio",
            "max_drawdown_pct",
            "allowed_fund_markets",
            "accept_fx_risk",
            "review_required",
            "review_due_at",
        )
    }


def _valuation_positions(
    valuation: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    rows = (
        (((valuation.get("snapshot") or {}).get("payload") or {}).get(
            "positions"
        ))
        or []
    )
    return {
        int(item.get("holding_id") or 0): dict(item)
        for item in rows
        if int(item.get("holding_id") or 0) > 0
    }


def _prepare_evidence(
    *,
    user_id: str,
    tenant_id: str,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_holdings = storage.list_holdings(user_id=user_id)
    valued_holdings, valuation = portfolio_valuation.current_valued_holdings(
        user_id=user_id,
        tenant_id=tenant_id,
        holdings=raw_holdings,
    )
    runtime_gate = valuation.get("runtime_gate") or {}
    if not runtime_gate.get("risk_analysis_eligible"):
        reasons = "；".join(
            str(item) for item in runtime_gate.get("reasons") or []
        )
        raise PortfolioQuantInputError(
            "量化实验需要当前且完整的人民币组合估值，请先刷新估值"
            + (f"：{reasons}" if reasons else "")
        )
    snapshot = valuation.get("snapshot") or {}
    valuation_rows = _valuation_positions(valuation)
    profile = storage.get_investment_profile(user_id=user_id)
    holdings_sha256 = portfolio_valuation.holdings_fingerprint(
        raw_holdings
    )
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in valued_holdings:
        holding_id = int(item.get("id") or 0)
        market = str(item.get("market") or "").strip()
        asset_type = str(item.get("asset_type") or "").strip().lower()
        amount = _number(item.get("amount"), 0.0) or 0.0
        valuation_row = valuation_rows.get(holding_id) or {}
        base = {
            "holding_id": holding_id,
            "asset_type": asset_type,
            "market": market,
            "code": str(item.get("code") or "").strip(),
            "name": str(item.get("name") or item.get("code") or ""),
            "amount_cny": round(amount, 2),
            "shares": _round(item.get("shares"), 8),
            "valuation_method": item.get("valuation_method"),
            "valuation_price_as_of": item.get(
                "valuation_price_as_of"
            ),
            "unit_price": valuation_row.get("unit_price"),
            "fx_rate_to_cny": valuation_row.get("fx_rate_to_cny"),
            "price_source": valuation_row.get("price_source"),
            "updated_at": item.get("updated_at"),
        }
        reason = None
        if asset_type != "stock":
            reason = "not_direct_stock"
        elif market not in SUPPORTED_MARKETS:
            reason = "unsupported_market"
        elif not base["code"]:
            reason = "symbol_missing"
        elif amount <= 0:
            reason = "valuation_amount_missing"
        if reason:
            excluded.append({**base, "reason": reason})
        else:
            candidates.append(base)
    candidates.sort(
        key=lambda item: (
            -float(item.get("amount_cny") or 0),
            item.get("market") or "",
            item.get("code") or "",
        )
    )
    if len(candidates) > MAX_ASSETS:
        for item in candidates[MAX_ASSETS:]:
            excluded.append({**item, "reason": "asset_limit_exceeded"})
        candidates = candidates[:MAX_ASSETS]
    if len(candidates) < MIN_ASSETS:
        raise PortfolioQuantInputError(
            "量化组合实验至少需要 2 只具有当前人民币估值的直接股票持仓"
        )

    total_value = _number(
        (((snapshot.get("payload") or {}).get("summary") or {}).get(
            "total_value"
        )),
        0.0,
    ) or 0.0
    sleeve_value = sum(
        float(item["amount_cny"]) for item in candidates
    )
    sleeve_pct = (
        sleeve_value / total_value * 100 if total_value > 0 else 0.0
    )
    requested_max = float(policy["max_position_pct"])
    profile_max = _number(profile.get("max_single_ratio"))
    effective_total_cap = min(
        requested_max,
        profile_max if profile.get("configured") and profile_max else requested_max,
    )
    effective_sleeve_cap = min(
        100.0,
        effective_total_cap / sleeve_pct * 100
        if sleeve_pct > 0
        else effective_total_cap,
    )
    effective_policy = {
        **policy,
        "requested_max_position_pct": requested_max,
        "effective_total_portfolio_position_cap_pct": round(
            effective_total_cap, 4
        ),
        "effective_stock_sleeve_position_cap_pct": round(
            effective_sleeve_cap, 4
        ),
        "profile_cap_applied": bool(
            profile.get("configured")
            and profile_max is not None
            and profile_max < requested_max
        ),
    }
    evidence = {
        "schema_version": "portfolio_quant_evidence.v1",
        "captured_at": _iso(),
        "holdings_sha256": holdings_sha256,
        "eligible_holdings": candidates,
        "excluded_holdings": excluded,
        "stock_sleeve": {
            "value_cny": round(sleeve_value, 2),
            "portfolio_value_cny": round(total_value, 2),
            "portfolio_weight_pct": round(sleeve_pct, 4),
            "eligible_count": len(candidates),
            "excluded_count": len(excluded),
        },
        "profile": _compact_profile(profile),
        "valuation": {
            "snapshot_id": snapshot.get("id"),
            "payload_sha256": snapshot.get("payload_sha256"),
            "holdings_sha256": snapshot.get("holdings_sha256"),
            "created_at": snapshot.get("created_at"),
            "fresh_until": snapshot.get("fresh_until"),
            "binding_current": bool(
                (valuation.get("binding") or {}).get("current")
            ),
            "risk_analysis_eligible": bool(
                runtime_gate.get("risk_analysis_eligible")
            ),
            "trade_amount_eligible": bool(
                runtime_gate.get("trade_amount_eligible")
            ),
            "integrity_verified": bool(
                runtime_gate.get("integrity_verified")
            ),
        },
        "known_limitations": [
            "股票池是当前持仓，不是无幸存者偏差的历史成分股池。",
            "跨市场历史收益暂按各自本币计算；多市场组合不能获得纸面调仓准入。",
            "日线无法重建盘中订单队列、涨跌停排队、停牌成交和市场冲击。",
        ],
    }
    return effective_policy, evidence


def _project_capped(
    raw: np.ndarray,
    *,
    cap: float,
    target_sum: float | None = None,
) -> np.ndarray:
    values = np.asarray(raw, dtype=float).copy()
    values[~np.isfinite(values)] = 0.0
    values = np.maximum(values, 0.0)
    count = int(values.size)
    if count == 0:
        return values
    upper = max(1e-8, min(1.0, float(cap)))
    target = min(
        float(target_sum if target_sum is not None else 1.0),
        count * upper,
    )
    target = max(0.0, target)
    if target <= 0:
        return np.zeros(count, dtype=float)
    if values.sum() <= 0:
        values = np.ones(count, dtype=float)
    values = values / values.sum() * target
    for _ in range(100):
        values = np.minimum(np.maximum(values, 0.0), upper)
        deficit = target - float(values.sum())
        if abs(deficit) <= 1e-10:
            break
        if deficit > 0:
            free = values < upper - 1e-12
            if not free.any():
                break
            room = upper - values[free]
            seed = values[free]
            if seed.sum() <= 1e-12:
                seed = np.ones_like(seed)
            addition = deficit * seed / seed.sum()
            values[free] += np.minimum(addition, room)
        else:
            positive = values > 1e-12
            if not positive.any():
                break
            removal = min(-deficit, float(values[positive].sum()))
            values[positive] -= (
                removal * values[positive] / values[positive].sum()
            )
    correction = target - float(values.sum())
    if abs(correction) > 1e-8:
        free = np.where(
            values < upper - 1e-9
            if correction > 0
            else values > 1e-9
        )[0]
        if len(free):
            values[free] += correction / len(free)
            values = np.minimum(np.maximum(values, 0.0), upper)
    return values


def _covariance(returns: pd.DataFrame) -> np.ndarray:
    sample = returns.cov().to_numpy(dtype=float, copy=True)
    sample[~np.isfinite(sample)] = 0.0
    diagonal = np.diag(np.diag(sample))
    shrunk = (
        (1.0 - COVARIANCE_SHRINKAGE) * sample
        + COVARIANCE_SHRINKAGE * diagonal
    )
    scale = float(np.nanmean(np.diag(shrunk))) if len(shrunk) else 0.0
    jitter = max(1e-12, abs(scale) * 1e-6)
    return shrunk + np.eye(len(shrunk)) * jitter


def _weights_for(
    method: str,
    returns: pd.DataFrame,
    *,
    max_weight: float,
) -> np.ndarray:
    count = returns.shape[1]
    target_sum = min(1.0, count * max_weight)
    if method == "equal_weight":
        return _project_capped(
            np.ones(count),
            cap=max_weight,
            target_sum=target_sum,
        )
    covariance = _covariance(returns)
    volatility = np.sqrt(
        np.maximum(np.diag(covariance), 1e-16)
    )
    if method == "inverse_volatility":
        return _project_capped(
            1.0 / volatility,
            cap=max_weight,
            target_sum=target_sum,
        )
    if method == "minimum_variance":
        weights = _project_capped(
            1.0 / volatility,
            cap=max_weight,
            target_sum=target_sum,
        )
        eigenvalues = np.linalg.eigvalsh(covariance)
        lipschitz = max(float(np.max(eigenvalues)), 1e-10)
        step = 0.25 / lipschitz
        for _ in range(600):
            candidate = _project_capped(
                weights - step * (covariance @ weights),
                cap=max_weight,
                target_sum=target_sum,
            )
            if np.max(np.abs(candidate - weights)) < 1e-10:
                weights = candidate
                break
            weights = candidate
        return weights
    if method == "risk_parity":
        weights = _project_capped(
            1.0 / volatility,
            cap=max_weight,
            target_sum=target_sum,
        )
        for _ in range(1000):
            marginal = covariance @ weights
            contributions = weights * marginal
            total = float(weights @ marginal)
            if total <= 1e-18:
                break
            target = total / count
            multiplier = np.sqrt(
                np.maximum(target, 1e-18)
                / np.maximum(contributions, 1e-18)
            )
            candidate = _project_capped(
                weights * multiplier,
                cap=max_weight,
                target_sum=target_sum,
            )
            if np.max(np.abs(candidate - weights)) < 1e-9:
                weights = candidate
                break
            weights = 0.5 * weights + 0.5 * candidate
        return _project_capped(
            weights,
            cap=max_weight,
            target_sum=target_sum,
        )
    raise PortfolioQuantInputError("不支持的组合构建方法")


def _risk_snapshot(
    weights: np.ndarray,
    returns: pd.DataFrame,
) -> dict[str, Any]:
    covariance = _covariance(returns)
    marginal = covariance @ weights
    variance = max(0.0, float(weights @ marginal))
    contributions = weights * marginal
    if variance > 1e-18:
        contribution_pct = contributions / variance * 100
    else:
        contribution_pct = np.zeros_like(weights)
    return {
        "estimated_annual_vol_pct": round(
            math.sqrt(variance * TRADING_DAYS) * 100, 4
        ),
        "risk_contribution_pct": [
            round(float(item), 4) for item in contribution_pct
        ],
        "risk_concentration_hhi": round(
            float(np.sum(np.square(contribution_pct / 100))), 6
        ),
    }


def _simulate_window(
    test: pd.DataFrame,
    target_weights: np.ndarray,
    previous_weights: np.ndarray,
    *,
    commission_bps: float,
    slippage_bps: float,
    sell_tax_bps: float,
    initial_current: bool = False,
) -> tuple[list[float], np.ndarray, dict[str, float]]:
    if initial_current:
        buys = sells = cost = 0.0
    else:
        delta = target_weights - previous_weights
        buys = float(np.maximum(delta, 0).sum())
        sells = float(np.maximum(-delta, 0).sum())
        variable = (commission_bps + slippage_bps) / 10_000
        cost = buys * variable + sells * (
            variable + sell_tax_bps / 10_000
        )
    weights = target_weights.copy()
    daily_returns: list[float] = []
    for index, (_, row) in enumerate(test.iterrows()):
        vector = row.to_numpy(dtype=float)
        gross = float(weights @ vector)
        net = (
            (1.0 - cost) * (1.0 + gross) - 1.0
            if index == 0
            else gross
        )
        daily_returns.append(net)
        denominator = 1.0 + gross
        if denominator > 1e-12:
            weights = weights * (1.0 + vector) / denominator
    return daily_returns, weights, {
        "buy_turnover_pct": round(buys * 100, 6),
        "sell_turnover_pct": round(sells * 100, 6),
        "gross_turnover_pct": round((buys + sells) * 100, 6),
        "one_way_turnover_pct": round(
            (buys + sells) / 2 * 100, 6
        ),
        "estimated_cost_pct": round(cost * 100, 6),
    }


def _max_drawdown(values: np.ndarray) -> float:
    if not len(values):
        return 0.0
    equity = np.cumprod(1.0 + values)
    peaks = np.maximum.accumulate(equity)
    drawdowns = equity / np.maximum(peaks, 1e-12) - 1.0
    return abs(float(np.min(drawdowns))) * 100


def _probabilistic_sharpe(values: np.ndarray) -> float | None:
    if len(values) < 30:
        return None
    standard = float(np.std(values, ddof=1))
    if standard <= 1e-12:
        return None
    mean = float(np.mean(values))
    daily_sharpe = mean / standard
    centered = values - mean
    skew = float(np.mean(centered**3) / max(standard**3, 1e-18))
    kurtosis = float(
        np.mean(centered**4) / max(standard**4, 1e-18)
    )
    denominator = math.sqrt(
        max(
            1e-12,
            1
            - skew * daily_sharpe
            + ((kurtosis - 1) / 4) * daily_sharpe**2,
        )
    )
    score = (
        daily_sharpe * math.sqrt(max(1, len(values) - 1))
        / denominator
    )
    return NormalDist().cdf(score) * 100


def _performance(
    daily_returns: list[float],
    turnovers: list[dict[str, float]],
) -> dict[str, Any]:
    values = np.asarray(daily_returns, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {
            "observation_count": 0,
            "cumulative_return_pct": None,
            "annualized_return_pct": None,
            "annualized_volatility_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown_pct": None,
            "cvar_95_pct": None,
            "probabilistic_sharpe_pct": None,
            "average_one_way_turnover_pct": None,
            "maximum_one_way_turnover_pct": None,
            "estimated_cost_drag_pct": None,
        }
    equity = float(np.prod(1.0 + values))
    cumulative = equity - 1.0
    years = len(values) / TRADING_DAYS
    annualized = (
        equity ** (1 / years) - 1.0
        if equity > 0 and years > 0
        else -1.0
    )
    standard = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    annual_vol = standard * math.sqrt(TRADING_DAYS)
    sharpe = (
        float(np.mean(values)) / standard * math.sqrt(TRADING_DAYS)
        if standard > 1e-12
        else None
    )
    downside = values[values < 0]
    downside_dev = (
        float(np.sqrt(np.mean(np.square(downside))))
        if len(downside)
        else 0.0
    )
    sortino = (
        float(np.mean(values)) / downside_dev
        * math.sqrt(TRADING_DAYS)
        if downside_dev > 1e-12
        else None
    )
    tail_count = max(1, int(math.ceil(len(values) * 0.05)))
    cvar = float(np.mean(np.sort(values)[:tail_count])) * 100
    one_way = [
        float(item.get("one_way_turnover_pct") or 0)
        for item in turnovers
    ]
    costs = [
        float(item.get("estimated_cost_pct") or 0)
        for item in turnovers
    ]
    return {
        "observation_count": len(values),
        "cumulative_return_pct": round(cumulative * 100, 4),
        "annualized_return_pct": round(annualized * 100, 4),
        "annualized_volatility_pct": round(annual_vol * 100, 4),
        "sharpe_ratio": _round(sharpe, 4),
        "sortino_ratio": _round(sortino, 4),
        "max_drawdown_pct": round(_max_drawdown(values), 4),
        "cvar_95_pct": round(cvar, 4),
        "probabilistic_sharpe_pct": _round(
            _probabilistic_sharpe(values), 3
        ),
        "average_one_way_turnover_pct": (
            round(float(np.mean(one_way)), 4) if one_way else 0.0
        ),
        "maximum_one_way_turnover_pct": (
            round(max(one_way), 4) if one_way else 0.0
        ),
        "estimated_cost_drag_pct": round(sum(costs), 4),
    }


def _price_frame(
    market: str,
    code: str,
    months: int,
) -> tuple[pd.DataFrame, str]:
    frame = data_fetch.get_history_months(
        market,
        code,
        months,
        fetch_months=months,
    )
    return frame, str(frame.attrs.get("source") or "unknown")


def _load_returns(
    holdings: list[dict[str, Any]],
    *,
    months: int,
    price_loader: Callable[
        [str, str, int], tuple[pd.DataFrame, str]
    ],
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    loaded: dict[str, pd.Series] = {}
    metadata: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def load(item: dict[str, Any]):
        frame, source = price_loader(
            str(item["market"]),
            str(item["code"]),
            months,
        )
        required = {"date", "close"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            raise ValueError("真实复权日线为空或缺少 date/close")
        clean = frame[["date", "close"]].copy()
        clean["date"] = pd.to_datetime(
            clean["date"], errors="coerce"
        )
        clean["close"] = pd.to_numeric(
            clean["close"], errors="coerce"
        )
        clean = (
            clean.dropna()
            .loc[lambda value: value["close"] > 0]
            .drop_duplicates("date", keep="last")
            .sort_values("date")
        )
        if len(clean) < 150:
            raise ValueError("有效复权日线少于 150 条")
        series = clean.set_index("date")["close"].pct_change(
            fill_method=None
        ).dropna()
        digest_rows = [
            [index.strftime("%Y-%m-%d"), round(float(value), 10)]
            for index, value in clean.set_index("date")["close"].items()
        ]
        key = f"{item['market']}:{item['code']}"
        return key, series.rename(key), {
            "holding_id": item["holding_id"],
            "market": item["market"],
            "code": item["code"],
            "name": item.get("name") or item["code"],
            "source": source,
            "professional_for_paper": (
                source in PROFESSIONAL_HISTORY_SOURCES
            ),
            "first_date": clean.iloc[0]["date"].date().isoformat(),
            "last_date": clean.iloc[-1]["date"].date().isoformat(),
            "price_count": len(clean),
            "price_sha256": sha256_payload(digest_rows),
        }

    total = len(holdings)
    with ThreadPoolExecutor(max_workers=min(6, total)) as pool:
        futures = {pool.submit(load, item): item for item in holdings}
        completed = 0
        for future in as_completed(futures):
            item = futures[future]
            try:
                key, series, detail = future.result()
                loaded[key] = series
                metadata.append(detail)
            except Exception as error:
                failures.append(
                    {
                        "holding_id": item.get("holding_id"),
                        "market": item.get("market"),
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "error": str(error)[:200],
                    }
                )
            completed += 1
            if progress:
                progress(
                    completed,
                    total,
                    f"已读取 {completed}/{total} 只股票",
                )
    metadata.sort(key=lambda item: (item["market"], item["code"]))
    if len(loaded) < MIN_ASSETS:
        raise PortfolioQuantInputError(
            "可用真实历史行情的股票少于 2 只，无法构建组合协方差"
        )
    matrix = pd.concat(
        [loaded[key] for key in sorted(loaded)],
        axis=1,
        join="inner",
    ).dropna()
    matrix = matrix.loc[
        np.isfinite(matrix.to_numpy(dtype=float)).all(axis=1)
    ]
    return matrix, metadata, failures


def _build_target_actions(
    holdings: list[dict[str, Any]],
    keys: list[str],
    current_weights: np.ndarray,
    target_weights: np.ndarray,
    *,
    sleeve_value: float,
    portfolio_value: float,
    minimum_trade_amount: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    by_key = {
        f"{item['market']}:{item['code']}": item for item in holdings
    }
    actions = []
    buy_amount = 0.0
    sell_amount = 0.0
    for index, key in enumerate(keys):
        item = by_key[key]
        current_amount = float(item.get("amount_cny") or 0)
        target_amount = sleeve_value * float(target_weights[index])
        delta = target_amount - current_amount
        if abs(delta) < minimum_trade_amount:
            action = "hold_small_delta"
        elif delta > 0:
            action = "increase"
            buy_amount += delta
        else:
            action = "reduce"
            sell_amount += abs(delta)
        actions.append(
            {
                "holding_id": item.get("holding_id"),
                "market": item.get("market"),
                "code": item.get("code"),
                "name": item.get("name") or item.get("code"),
                "action": action,
                "current_amount_cny": round(current_amount, 2),
                "target_amount_cny": round(target_amount, 2),
                "delta_amount_cny": round(delta, 2),
                "current_stock_sleeve_weight_pct": round(
                    float(current_weights[index]) * 100, 4
                ),
                "target_stock_sleeve_weight_pct": round(
                    float(target_weights[index]) * 100, 4
                ),
                "target_total_portfolio_weight_pct": round(
                    target_amount / portfolio_value * 100
                    if portfolio_value > 0
                    else 0.0,
                    4,
                ),
                "quantity_generated": False,
                "quantity_reason": (
                    "跨市场整手、可卖零股和实时价格规则尚未接入，"
                    "本轮只冻结人民币目标金额"
                ),
            }
        )
    retained = sum(
        float(item["target_amount_cny"]) for item in actions
    )
    cash_release = max(0.0, sleeve_value - retained)
    variable_bps = (
        float(policy["commission_bps"])
        + float(policy["slippage_bps"])
    )
    estimated_cost = (
        buy_amount * variable_bps / 10_000
        + sell_amount
        * (variable_bps + float(policy["sell_tax_bps"]))
        / 10_000
    )
    gross_turnover = (
        (buy_amount + sell_amount) / sleeve_value * 100
        if sleeve_value > 0
        else 0.0
    )
    return {
        "schema_version": "portfolio_quant_target.v1",
        "base_currency": "CNY",
        "execution_authorized": False,
        "stock_sleeve_value_cny": round(sleeve_value, 2),
        "portfolio_value_cny": round(portfolio_value, 2),
        "buy_amount_cny": round(buy_amount, 2),
        "sell_amount_cny": round(sell_amount, 2),
        "cash_release_cny": round(cash_release, 2),
        "gross_turnover_pct": round(gross_turnover, 4),
        "one_way_turnover_pct": round(gross_turnover / 2, 4),
        "estimated_cost_cny": round(estimated_cost, 2),
        "minimum_trade_amount_cny": round(
            minimum_trade_amount, 2
        ),
        "actions": actions,
        "limitations": [
            "目标金额不是券商订单，生成时未读取实时可用现金、买卖冻结或保证金。",
            "没有生成股数；A/H/美股整手、零股、停牌和订单价格必须在执行前重新报价。",
            "任何真实成交仍需进入交易账本，并由资本计划执行学习链重新对账。",
        ],
    }


def _promotion_gate(
    *,
    policy: dict[str, Any],
    evidence: dict[str, Any],
    selected: dict[str, Any],
    current: dict[str, Any],
    selected_risk: dict[str, Any],
    current_risk: dict[str, Any],
    target: dict[str, Any],
    fold_count: int,
    data_quality: dict[str, Any],
    market_count: int,
) -> dict[str, Any]:
    profile = evidence.get("profile") or {}
    valuation = evidence.get("valuation") or {}
    selected_vol = _number(
        selected.get("annualized_volatility_pct")
    )
    current_vol = _number(
        current.get("annualized_volatility_pct")
    )
    selected_drawdown = _number(selected.get("max_drawdown_pct"))
    current_drawdown = _number(current.get("max_drawdown_pct"))
    psr = _number(selected.get("probabilistic_sharpe_pct"))
    turnover = _number(
        selected.get("average_one_way_turnover_pct")
    )
    maximum_turnover = _number(
        selected.get("maximum_one_way_turnover_pct")
    )
    target_turnover = _number(target.get("one_way_turnover_pct"))
    selected_hhi = _number(
        selected_risk.get("risk_concentration_hhi")
    )
    current_hhi = _number(
        current_risk.get("risk_concentration_hhi")
    )
    history_staleness = _number(
        data_quality.get("history_staleness_days")
    )
    risk_improved = bool(
        selected_vol is not None
        and current_vol is not None
        and selected_hhi is not None
        and current_hhi is not None
        and (
            selected_vol <= current_vol * 0.95
            or selected_hhi <= current_hhi * 0.90
        )
    )
    checks = [
        {
            "code": "active_investment_policy",
            "label": "有效投资政策",
            "passed": bool(profile.get("configured")),
            "detail": (
                "投资政策有效且治理链完整"
                if profile.get("configured")
                else "尚无有效投资政策，不能冻结纸面调仓指令"
            ),
        },
        {
            "code": "trusted_trade_amounts",
            "label": "可信人民币金额",
            "passed": bool(valuation.get("trade_amount_eligible")),
            "detail": (
                "自动估值、专业来源覆盖和时效门禁通过"
                if valuation.get("trade_amount_eligible")
                else "估值只够风险研究，不够生成调仓金额"
            ),
        },
        {
            "code": "single_market_fx_boundary",
            "label": "汇率历史边界",
            "passed": market_count == 1,
            "detail": (
                "股票袖套来自单一市场，本币收益口径一致"
                if market_count == 1
                else "跨市场未纳入历史汇率收益，只能研究不能冻结"
            ),
        },
        {
            "code": "minimum_walk_forward_folds",
            "label": "样本外窗口",
            "passed": fold_count >= 6,
            "detail": f"完整滚动样本外窗口 {fold_count} 个，最低 6 个",
        },
        {
            "code": "history_coverage",
            "label": "行情覆盖",
            "passed": (
                float(data_quality.get("asset_coverage_pct") or 0)
                >= 100
                and int(data_quality.get("eligible_asset_count") or 0)
                >= MIN_ASSETS
            ),
            "detail": (
                f"资产覆盖 {data_quality.get('asset_coverage_pct')}%，"
                f"冻结纸面目标要求 100%；共同收益 "
                f"{data_quality.get('aligned_return_days')} 天"
            ),
        },
        {
            "code": "professional_history_sources",
            "label": "专业历史行情源",
            "passed": (
                float(
                    data_quality.get(
                        "professional_source_coverage_pct"
                    )
                    or 0
                )
                >= 100
            ),
            "detail": (
                "专业源覆盖 "
                f"{data_quality.get('professional_source_coverage_pct')}%；"
                "免费备用源只允许研究"
            ),
        },
        {
            "code": "history_freshness",
            "label": "历史行情时效",
            "passed": bool(
                history_staleness is not None
                and history_staleness <= MAX_HISTORY_STALENESS_DAYS
            ),
            "detail": (
                f"共同收益最后日期 "
                f"{data_quality.get('last_aligned_date')}，"
                f"距运行日 "
                f"{data_quality.get('history_staleness_days')} 天；"
                f"上限 {MAX_HISTORY_STALENESS_DAYS} 天"
            ),
        },
        {
            "code": "turnover_budget",
            "label": "换手预算",
            "passed": bool(
                turnover is not None
                and turnover <= float(policy["max_turnover_pct"])
                and maximum_turnover is not None
                and maximum_turnover
                <= float(policy["max_turnover_pct"])
                and target_turnover is not None
                and target_turnover
                <= float(policy["max_turnover_pct"])
            ),
            "detail": (
                f"平均单边换手 {turnover if turnover is not None else '—'}%，"
                f"历史最高 "
                f"{maximum_turnover if maximum_turnover is not None else '—'}%，"
                f"当前目标单边换手 "
                f"{target_turnover if target_turnover is not None else '—'}%，"
                f"两者上限均为 {policy['max_turnover_pct']}%"
            ),
        },
        {
            "code": "oos_volatility_not_worse",
            "label": "样本外波动",
            "passed": bool(
                selected_vol is not None
                and current_vol is not None
                and selected_vol <= current_vol * 1.05
            ),
            "detail": (
                f"方案 {selected_vol if selected_vol is not None else '—'}%，"
                f"当前权重 {current_vol if current_vol is not None else '—'}%"
            ),
        },
        {
            "code": "oos_drawdown_not_worse",
            "label": "样本外回撤",
            "passed": bool(
                selected_drawdown is not None
                and current_drawdown is not None
                and selected_drawdown <= current_drawdown + 2
            ),
            "detail": (
                f"方案 {selected_drawdown if selected_drawdown is not None else '—'}%，"
                f"当前权重 {current_drawdown if current_drawdown is not None else '—'}%"
            ),
        },
        {
            "code": "positive_risk_adjusted_evidence",
            "label": "正风险调整证据",
            "passed": bool(psr is not None and psr >= 55),
            "detail": (
                f"成本后 PSR {psr if psr is not None else '—'}%，最低 55%"
            ),
        },
        {
            "code": "risk_improvement",
            "label": "风险改善",
            "passed": risk_improved,
            "detail": (
                "波动至少下降 5%，或风险贡献集中度至少下降 10%"
                if risk_improved
                else "没有观察到足够的波动或风险集中度改善"
            ),
        },
    ]
    ready = all(bool(item["passed"]) for item in checks)
    return {
        "status": "paper_ready" if ready else "research_only",
        "label": (
            "可冻结纸面调仓指令"
            if ready
            else "继续研究，暂不冻结调仓"
        ),
        "paper_mandate_eligible": ready,
        "execution_authorized": False,
        "checks": checks,
        "failed_codes": [
            item["code"] for item in checks if not item["passed"]
        ],
        "policy": (
            "准入只允许进入不可变纸面指令；真实下单仍被禁止。"
        ),
    }


def execute_run(
    run_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str = "quant-worker",
    repo: PortfolioQuantRepository = repository,
    price_loader: Callable[
        [str, str, int], tuple[pd.DataFrame, str]
    ] = _price_frame,
) -> dict[str, Any]:
    run = repo.get_run(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if run is None:
        raise PortfolioQuantNotFoundError("量化实验不存在")
    if run.get("status") in {"succeeded", "partial"}:
        return run
    if not (run.get("integrity") or {}).get("verified"):
        raise PortfolioQuantConflictError("量化实验输入完整性失败")
    policy = run.get("policy") or {}
    evidence = run.get("evidence") or {}
    holdings = list(evidence.get("eligible_holdings") or [])
    if len(holdings) < MIN_ASSETS:
        raise PortfolioQuantInputError("冻结股票池少于 2 只")
    repo.mark_running(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
    )

    def progress(completed: int, total: int, message: str) -> None:
        repo.update_progress(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            progress={
                "stage": "market_data",
                "completed": completed,
                "total": total,
                "message": message,
            },
        )

    try:
        lookback = int(policy["lookback_days"])
        rebalance = int(policy["rebalance_days"])
        required_days = lookback + rebalance * 6
        months = max(
            24,
            min(120, int(math.ceil(required_days / 20 * 1.7))),
        )
        matrix, market_metadata, failures = _load_returns(
            holdings,
            months=months,
            price_loader=price_loader,
            progress=progress,
        )
        if len(matrix) < required_days:
            raise PortfolioQuantInputError(
                f"共同交易日只有 {len(matrix)} 天，至少需要 {required_days} 天"
            )
        keys = list(matrix.columns)
        holdings_by_key = {
            f"{item['market']}:{item['code']}": item
            for item in holdings
        }
        aligned_holdings = [holdings_by_key[key] for key in keys]
        amounts = np.asarray(
            [
                float(item.get("amount_cny") or 0)
                for item in aligned_holdings
            ],
            dtype=float,
        )
        sleeve_value = float(amounts.sum())
        if sleeve_value <= 0:
            raise PortfolioQuantInputError("股票袖套人民币金额无效")
        current_weights = amounts / sleeve_value
        max_weight = (
            float(policy["effective_stock_sleeve_position_cap_pct"])
            / 100
        )
        methods = [
            "current_weights",
            "equal_weight",
            "inverse_volatility",
            "risk_parity",
            "minimum_variance",
        ]
        method_daily: dict[str, list[float]] = {
            method: [] for method in methods
        }
        method_turnovers: dict[str, list[dict[str, float]]] = {
            method: [] for method in methods
        }
        previous_weights = {
            method: current_weights.copy() for method in methods
        }
        folds: list[dict[str, Any]] = []
        fold_no = 0
        for test_start in range(
            lookback, len(matrix) - rebalance + 1, rebalance
        ):
            train = matrix.iloc[test_start - lookback : test_start]
            test = matrix.iloc[test_start : test_start + rebalance]
            if len(test) < rebalance:
                continue
            fold_no += 1
            targets = {
                "current_weights": current_weights.copy(),
                **{
                    method: _weights_for(
                        method,
                        train,
                        max_weight=max_weight,
                    )
                    for method in SUPPORTED_METHODS
                },
            }
            fold_result = {
                "fold_no": fold_no,
                "train_start": train.index[0].date().isoformat(),
                "train_end": train.index[-1].date().isoformat(),
                "test_start": test.index[0].date().isoformat(),
                "test_end": test.index[-1].date().isoformat(),
                "train_days": len(train),
                "test_days": len(test),
                "methods": {},
            }
            for method in methods:
                daily, ending, turnover = _simulate_window(
                    test,
                    targets[method],
                    previous_weights[method],
                    commission_bps=float(policy["commission_bps"]),
                    slippage_bps=float(policy["slippage_bps"]),
                    sell_tax_bps=float(policy["sell_tax_bps"]),
                    initial_current=(
                        fold_no == 1 and method == "current_weights"
                    ),
                )
                method_daily[method].extend(daily)
                method_turnovers[method].append(turnover)
                previous_weights[method] = ending
                fold_result["methods"][method] = {
                    "label": METHOD_LABELS[method],
                    "net_return_pct": round(
                        (float(np.prod(1 + np.asarray(daily))) - 1)
                        * 100,
                        4,
                    ),
                    **turnover,
                }
            folds.append(fold_result)
        if fold_no < 1:
            raise PortfolioQuantInputError("没有形成完整样本外窗口")

        latest_train = matrix.iloc[-lookback:]
        latest_targets = {
            "current_weights": current_weights.copy(),
            **{
                method: _weights_for(
                    method,
                    latest_train,
                    max_weight=max_weight,
                )
                for method in SUPPORTED_METHODS
            },
        }
        model_rows = []
        risk_rows: dict[str, dict[str, Any]] = {}
        for method in methods:
            performance = _performance(
                method_daily[method],
                method_turnovers[method],
            )
            risk = _risk_snapshot(
                latest_targets[method],
                latest_train,
            )
            risk_rows[method] = risk
            model_rows.append(
                {
                    "method": method,
                    "label": METHOD_LABELS[method],
                    "selected": (
                        method == policy["construction_method"]
                    ),
                    "latest_stock_sleeve_weights_pct": [
                        round(float(value) * 100, 4)
                        for value in latest_targets[method]
                    ],
                    "latest_cash_within_sleeve_pct": round(
                        max(
                            0.0,
                            1 - float(latest_targets[method].sum()),
                        )
                        * 100,
                        4,
                    ),
                    "risk": risk,
                    "performance": performance,
                }
            )
        selected_method = str(policy["construction_method"])
        performance_by_method = {
            item["method"]: item["performance"] for item in model_rows
        }
        selected_performance = performance_by_method[selected_method]
        current_performance = performance_by_method["current_weights"]
        requested_count = len(holdings)
        eligible_count = len(keys)
        professional_count = sum(
            1
            for item in market_metadata
            if item.get("professional_for_paper")
        )
        last_aligned_date = matrix.index[-1].date()
        history_staleness_days = max(
            0,
            (
                dt.datetime.now(dt.timezone.utc).date()
                - last_aligned_date
            ).days,
        )
        data_quality = {
            "requested_asset_count": requested_count,
            "eligible_asset_count": eligible_count,
            "failed_asset_count": len(failures),
            "asset_coverage_pct": round(
                (
                    eligible_count / requested_count * 100
                    if requested_count
                    else 0.0
                ),
                2,
            ),
            "aligned_return_days": len(matrix),
            "required_return_days": required_days,
            "first_aligned_date": matrix.index[0].date().isoformat(),
            "last_aligned_date": last_aligned_date.isoformat(),
            "history_staleness_days": history_staleness_days,
            "maximum_history_staleness_days": (
                MAX_HISTORY_STALENESS_DAYS
            ),
            "professional_source_count": professional_count,
            "professional_source_coverage_pct": round(
                (
                    professional_count / eligible_count * 100
                    if eligible_count
                    else 0.0
                ),
                2,
            ),
            "nonprofessional_sources": sorted(
                {
                    str(item.get("source") or "unknown")
                    for item in market_metadata
                    if not item.get("professional_for_paper")
                }
            ),
            "market_count": len(
                {item["market"] for item in aligned_holdings}
            ),
            "markets": sorted(
                {item["market"] for item in aligned_holdings}
            ),
            "failures": failures,
        }
        portfolio_value = float(
            (evidence.get("stock_sleeve") or {}).get(
                "portfolio_value_cny"
            )
            or sleeve_value
        )
        target = _build_target_actions(
            aligned_holdings,
            keys,
            current_weights,
            latest_targets[selected_method],
            sleeve_value=sleeve_value,
            portfolio_value=portfolio_value,
            minimum_trade_amount=float(
                policy["minimum_trade_amount_cny"]
            ),
            policy=policy,
        )
        promotion = _promotion_gate(
            policy=policy,
            evidence=evidence,
            selected=selected_performance,
            current=current_performance,
            selected_risk=risk_rows[selected_method],
            current_risk=risk_rows["current_weights"],
            target=target,
            fold_count=fold_no,
            data_quality=data_quality,
            market_count=int(data_quality["market_count"]),
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "run_id": run_id,
            "generated_at": _iso(),
            "selected_method": selected_method,
            "selected_method_label": METHOD_LABELS[selected_method],
            "bindings": {
                "holdings_sha256": run.get("holdings_sha256"),
                "profile_version_id": run.get("profile_version_id"),
                "valuation_snapshot_id": run.get(
                    "valuation_snapshot_id"
                ),
                "policy_sha256": run.get("policy_sha256"),
                "evidence_sha256": run.get("evidence_sha256"),
            },
            "data_quality": data_quality,
            "market_data": market_metadata,
            "universe": [
                {
                    "key": key,
                    **{
                        field: holdings_by_key[key].get(field)
                        for field in (
                            "holding_id",
                            "market",
                            "code",
                            "name",
                            "amount_cny",
                        )
                    },
                }
                for key in keys
            ],
            "walk_forward": {
                "training_window_days": lookback,
                "holdout_window_days": rebalance,
                "fold_count": fold_no,
                "parameter_timing": (
                    "每个测试窗口只使用其之前紧邻训练窗口的收益；"
                    "测试窗口数据不进入权重估计"
                ),
                "folds": folds,
            },
            "models": model_rows,
            "selected_comparison": {
                "current_weights": current_performance,
                "selected": selected_performance,
                "annualized_volatility_change_pct_points": _round(
                    (
                        float(
                            selected_performance[
                                "annualized_volatility_pct"
                            ]
                        )
                        - float(
                            current_performance[
                                "annualized_volatility_pct"
                            ]
                        )
                    ),
                    4,
                ),
                "max_drawdown_change_pct_points": _round(
                    (
                        float(
                            selected_performance[
                                "max_drawdown_pct"
                            ]
                        )
                        - float(
                            current_performance[
                                "max_drawdown_pct"
                            ]
                        )
                    ),
                    4,
                ),
                "risk_concentration_change": _round(
                    risk_rows[selected_method][
                        "risk_concentration_hhi"
                    ]
                    - risk_rows["current_weights"][
                        "risk_concentration_hhi"
                    ],
                    6,
                ),
            },
            "target": target,
            "promotion_gate": promotion,
            "methodology": {
                "universe": (
                    "只使用运行开始时冻结的当前直接股票持仓；"
                    "不会向历史添加后来才知道的股票"
                ),
                "optimization": (
                    "只优化协方差与风险贡献，不使用历史收益率预测，"
                    "也不按样本外结果自动挑选最佳模型"
                ),
                "covariance": (
                    "训练窗口样本协方差与其对角阵按 75%/25% 收缩"
                ),
                "costs": (
                    "每次再平衡按买入/卖出换手分别扣佣金、滑点和卖出税费"
                ),
                "cash": (
                    "单股上限无法容纳全部股票袖套时，未分配部分视为零收益现金"
                ),
            },
            "limitations": [
                "当前持仓股票池存在选择偏差与幸存者偏差，结果不是选股策略历史业绩。",
                "跨市场收益没有历史汇率换算；多市场运行固定为 research_only。",
                "协方差、相关性、波动和风险贡献会随市场状态变化。",
                "日线成本模型不包含订单簿冲击、延迟、排队、涨跌停和停牌。",
                "PSR 是成本后样本的统计诊断，不是未来赚钱概率。",
                "纸面指令不连接券商、不生成股数、不授权自动交易。",
            ],
        }
        status = (
            "partial"
            if failures or eligible_count < requested_count
            else "succeeded"
        )
        return repo.complete_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            result=result,
            status=status,
            actor_id=actor_id,
        )
    except Exception as error:
        try:
            repo.fail_run(
                run_id,
                tenant_id=tenant_id,
                user_id=user_id,
                error_code="PORTFOLIO_QUANT_RUN_FAILED",
                error_message=str(error),
                actor_id=actor_id,
            )
        except PortfolioQuantConflictError:
            pass
        raise


def start_run(
    policy_payload: dict[str, Any] | None,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: PortfolioQuantRepository = repository,
) -> dict[str, Any]:
    normalized = normalize_policy(policy_payload)
    policy, evidence = _prepare_evidence(
        user_id=user_id,
        tenant_id=tenant_id,
        policy=normalized,
    )
    profile = evidence.get("profile") or {}
    valuation = evidence.get("valuation") or {}
    run = repo.create_run(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        holdings_sha256=str(evidence["holdings_sha256"]),
        profile_version_id=profile.get("profile_version_id"),
        valuation_snapshot_id=valuation.get("snapshot_id"),
        policy=policy,
        evidence=evidence,
    )
    if not uses_celery_queue():
        return execute_run(
            str(run["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id="embedded-quant-worker",
            repo=repo,
        )
    jobs = BackgroundJobRepository()
    try:
        job, _ = jobs.create_job(
            job_type="portfolio_quant_run",
            queue_name=QUEUE_MARKET,
            payload={
                "run_id": run["id"],
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=str(run["id"]),
            max_attempts=1,
        )
        repo.bind_job(
            str(run["id"]),
            str(job["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        enqueue_background_job(job, jobs)
    except Exception as error:
        repo.fail_run(
            str(run["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
            error_code="PORTFOLIO_QUANT_QUEUE_UNAVAILABLE",
            error_message=str(error),
            actor_id="api",
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


def refresh_run_status(
    run_id: str,
    *,
    tenant_id: str,
    user_id: str,
    repo: PortfolioQuantRepository = repository,
) -> dict[str, Any] | None:
    run = repo.get_run(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if (
        not run
        or run.get("status") not in {"queued", "running"}
        or not run.get("job_id")
    ):
        return run
    job = BackgroundJobRepository().get_job(
        str(run["job_id"]),
        include_payload=False,
    )
    if job and job.get("status") in {"failed", "cancelled"}:
        return repo.fail_run(
            run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            error_code=str(
                job.get("error_code") or "PORTFOLIO_QUANT_JOB_FAILED"
            ),
            error_message=str(
                job.get("error_message") or "量化实验后台任务失败"
            ),
            actor_id="api-reconciler",
        )
    return run


def overview(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 30,
    repo: PortfolioQuantRepository = repository,
) -> dict[str, Any]:
    runs = repo.list_runs(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    latest = (
        repo.get_run(
            str(runs[0]["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if runs
        else None
    )
    mandates = repo.list_mandates(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    holdings = storage.list_holdings(user_id=user_id)
    direct_stocks = [
        item
        for item in holdings
        if str(item.get("asset_type") or "").lower() == "stock"
        and str(item.get("market") or "") in SUPPORTED_MARKETS
    ]
    return {
        "schema_version": "portfolio_quant_lab_overview.v1",
        "engine_version": ENGINE_VERSION,
        "latest_run": latest,
        "runs": runs,
        "mandates": mandates,
        "summary": {
            "run_count": len(runs),
            "completed_run_count": sum(
                1
                for item in runs
                if item.get("status") in {"succeeded", "partial"}
            ),
            "mandate_count": len(mandates),
            "direct_stock_holding_count": len(direct_stocks),
        },
        "defaults": normalize_policy({}),
        "methods": [
            {
                "id": method,
                "label": METHOD_LABELS[method],
            }
            for method in (
                "risk_parity",
                "minimum_variance",
                "inverse_volatility",
                "equal_weight",
            )
        ],
        "boundary": {
            "execution_authorized": False,
            "minimum_direct_stocks": MIN_ASSETS,
            "maximum_assets": MAX_ASSETS,
            "supported_markets": sorted(SUPPORTED_MARKETS),
            "message": (
                "量化实验只生成可审计纸面目标；任何真实成交仍需人工确认并写入交易账本。"
            ),
        },
    }


def freeze_mandate(
    run_id: str,
    *,
    acknowledged: bool,
    expected_result_sha256: str,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: PortfolioQuantRepository = repository,
) -> tuple[dict[str, Any], bool]:
    if not acknowledged:
        raise PortfolioQuantInputError(
            "必须确认这只是纸面调仓研究，不会自动下单"
        )
    run = repo.get_run(
        run_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if run is None:
        raise PortfolioQuantNotFoundError("量化实验不存在")
    if (
        not run.get("result_verified")
        or str(run.get("result_sha256") or "")
        != str(expected_result_sha256 or "")
    ):
        raise PortfolioQuantConflictError(
            "量化实验结果哈希已变化，请重新读取后确认"
        )
    result = run.get("result") or {}
    gate = result.get("promotion_gate") or {}
    if not gate.get("paper_mandate_eligible"):
        raise PortfolioQuantConflictError(
            "本次实验未通过纸面调仓准入门禁"
        )
    current_holdings = storage.list_holdings(user_id=user_id)
    current_hash = portfolio_valuation.holdings_fingerprint(
        current_holdings
    )
    if current_hash != run.get("holdings_sha256"):
        raise PortfolioQuantConflictError(
            "持仓已变化，必须重新运行量化实验"
        )
    valuation = portfolio_valuation.latest_portfolio_valuation(
        user_id=user_id,
        tenant_id=tenant_id,
        holdings=current_holdings,
    )
    valuation_snapshot = valuation.get("snapshot") or {}
    if (
        valuation_snapshot.get("id")
        != run.get("valuation_snapshot_id")
        or not (valuation.get("runtime_gate") or {}).get(
            "trade_amount_eligible"
        )
    ):
        raise PortfolioQuantConflictError(
            "估值快照已变化或不再满足调仓金额门禁"
        )
    profile = storage.get_investment_profile(user_id=user_id)
    if (
        not profile.get("configured")
        or profile.get("profile_version_id")
        != run.get("profile_version_id")
    ):
        raise PortfolioQuantConflictError(
            "投资政策已变化或失效，必须重新运行量化实验"
        )
    target = result.get("target") or {}
    evidence = {
        "schema_version": "portfolio_quant_mandate_evidence.v1",
        "run_id": run_id,
        "result_sha256": run.get("result_sha256"),
        "holdings_sha256": current_hash,
        "profile_version_id": profile.get("profile_version_id"),
        "valuation_snapshot_id": valuation_snapshot.get("id"),
        "selected_method": result.get("selected_method"),
        "promotion_gate": gate,
        "acknowledged_research_only": True,
        "execution_authorized": False,
        "frozen_at": _iso(),
    }
    return repo.create_mandate(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        evidence=evidence,
        target={
            **target,
            "source_run_id": run_id,
            "source_result_sha256": run.get("result_sha256"),
            "execution_authorized": False,
        },
    )
