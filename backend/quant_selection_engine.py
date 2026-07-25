# -*- coding: utf-8 -*-
"""Point-in-time multi-factor portfolio simulation.

The module is intentionally pure: callers provide frozen universe snapshots
and daily bars, and the engine returns deterministic research evidence.  It
never fetches data, submits broker orders, or calibrates factor weights on the
same sample it evaluates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


ENGINE_VERSION = "point_in_time_quant_selection@1.0.0"
RESULT_SCHEMA_VERSION = "quant_selection_research.v1"
FACTOR_LABELS = {
    "momentum": "中期动量（跳过最近一月）",
    "trend_quality": "趋势质量",
    "low_volatility": "低波动",
    "liquidity": "流动性",
}
PROFESSIONAL_SOURCE_PREFIXES = (
    "Tushare",
    "Polygon",
    "Massive",
)


class QuantSelectionInputError(ValueError):
    """Raised when frozen research inputs cannot produce honest evidence."""


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _rounded(value: Any, digits: int = 3) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _normalize_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = {
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise QuantSelectionInputError(
            f"{symbol} 行情缺少字段:{', '.join(sorted(missing))}"
        )
    output = frame.copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "execution_open",
        "raw_turnover",
    ]
    for column in numeric:
        if column in output.columns:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    output = (
        output.dropna(subset=["date", "close"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    output = output[
        (output["close"] > 0)
        & output["close"].map(lambda value: math.isfinite(float(value)))
    ].copy()
    if "execution_open" not in output:
        output["execution_open"] = output["open"]
    if "raw_turnover" not in output:
        output["raw_turnover"] = output["open"] * output["volume"]
    output["execution_open"] = output["execution_open"].where(
        output["execution_open"] > 0
    )
    output["raw_turnover"] = output["raw_turnover"].clip(lower=0)
    if output.empty:
        raise QuantSelectionInputError(f"{symbol} 没有有效日线")
    return output.set_index("date", drop=False)


def _normalize_snapshots(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for snapshot in snapshots or []:
        as_of = pd.Timestamp(snapshot.get("as_of")).normalize()
        members = []
        seen = set()
        for item in snapshot.get("members") or []:
            symbol = str((item or {}).get("symbol") or "").strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            members.append(
                {
                    "symbol": symbol,
                    "name": str((item or {}).get("name") or symbol)[:80],
                    "parent_weight_pct": _rounded(
                        (item or {}).get("parent_weight_pct"), 6
                    ),
                }
            )
        if members:
            output.append(
                {
                    "as_of": as_of,
                    "as_of_text": as_of.strftime("%Y-%m-%d"),
                    "members": members,
                    "source": str(snapshot.get("source") or "frozen_input"),
                }
            )
    output.sort(key=lambda item: item["as_of"])
    if not output:
        raise QuantSelectionInputError("历史股票池快照为空")
    return output


def _members_for(
    snapshots: list[dict[str, Any]],
    signal_date: pd.Timestamp,
) -> list[dict[str, Any]]:
    eligible = [
        snapshot for snapshot in snapshots if snapshot["as_of"] <= signal_date
    ]
    return list(eligible[-1]["members"]) if eligible else []


def _rank_percentiles(
    values: dict[str, float],
    *,
    higher_is_better: bool = True,
) -> dict[str, float]:
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 50.0}
    series = pd.Series(values, dtype=float)
    lower = float(series.quantile(0.05))
    upper = float(series.quantile(0.95))
    clipped = series.clip(lower=lower, upper=upper)
    ranks = clipped.rank(method="average", pct=True)
    if not higher_is_better:
        ranks = 1.0 - ranks + (1.0 / len(ranks))
    return {
        str(symbol): round(float(score) * 100, 3)
        for symbol, score in ranks.items()
    }


def _trend_quality(close: pd.Series, window: int = 126) -> float | None:
    values = close.tail(window).astype(float)
    if len(values) < window or (values <= 0).any():
        return None
    y = np.log(values.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    total = float(np.sum((y - np.mean(y)) ** 2))
    residual = float(np.sum((y - fitted) ** 2))
    r_squared = max(0.0, min(1.0, 1 - residual / total)) if total > 0 else 0.0
    annualized_slope = math.exp(float(slope) * 252) - 1
    return annualized_slope * r_squared


def _factor_snapshot(
    *,
    signal_date: pd.Timestamp,
    members: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    policy: dict[str, Any],
) -> dict[str, Any]:
    weights = policy["factor_weights"]
    raw_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    lookback = int(policy["lookback_days"])
    skip = 21
    volatility_window = min(63, lookback)
    liquidity_window = min(63, lookback)

    for member in members:
        symbol = member["symbol"]
        frame = frames.get(symbol)
        if frame is None:
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "history_unavailable",
                    "detail": "冻结股票池成员没有可用行情",
                }
            )
            continue
        history = frame.loc[frame.index <= signal_date]
        if history.empty:
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "not_listed_as_of",
                    "detail": "信号日尚无可用行情",
                }
            )
            continue
        last_date = pd.Timestamp(history.index[-1]).normalize()
        stale_days = int((signal_date - last_date).days)
        if stale_days > int(policy["max_price_staleness_days"]):
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "price_stale",
                    "detail": f"最近行情距信号日 {stale_days} 天",
                }
            )
            continue
        close = history["close"].astype(float)
        if len(close) < max(int(policy["minimum_history_days"]), lookback + 1):
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "insufficient_history",
                    "detail": (
                        f"只有 {len(close)} 个交易日，"
                        f"至少需要 {max(int(policy['minimum_history_days']), lookback + 1)}"
                    ),
                }
            )
            continue
        last_price = float(close.iloc[-1])
        if last_price < float(policy["minimum_price"]):
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "price_below_floor",
                    "detail": f"信号日价格 {last_price:.4f} 低于门槛",
                }
            )
            continue
        turnover = (
            history["raw_turnover"]
            .tail(liquidity_window)
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        average_turnover = (
            float(turnover.mean()) if len(turnover) >= liquidity_window // 2 else None
        )
        if (
            average_turnover is None
            or average_turnover < float(policy["minimum_average_turnover"])
        ):
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "liquidity_below_floor",
                    "detail": (
                        "近三月平均成交额不足"
                        if average_turnover is None
                        else f"近三月平均成交额 {average_turnover:,.0f} 低于门槛"
                    ),
                }
            )
            continue
        returns = close.pct_change().dropna()
        annual_volatility = (
            float(returns.tail(volatility_window).std(ddof=1) * math.sqrt(252))
            if len(returns) >= volatility_window
            else None
        )
        base_index = -(lookback + 1)
        skip_index = -(skip + 1)
        base = float(close.iloc[base_index])
        skip_close = float(close.iloc[skip_index])
        momentum = skip_close / base - 1 if base > 0 else None
        trend = _trend_quality(close, min(126, lookback))
        values = {
            "momentum": momentum,
            "trend_quality": trend,
            "low_volatility": (
                -annual_volatility if annual_volatility is not None else None
            ),
            "liquidity": (
                math.log10(average_turnover)
                if average_turnover and average_turnover > 0
                else None
            ),
        }
        if any(_number(value) is None for value in values.values()):
            exclusions.append(
                {
                    "symbol": symbol,
                    "name": member.get("name") or symbol,
                    "code": "factor_incomplete",
                    "detail": "至少一个价格或流动性因子无法计算",
                }
            )
            continue
        raw_rows.append(
            {
                "symbol": symbol,
                "name": member.get("name") or symbol,
                "parent_weight_pct": member.get("parent_weight_pct"),
                "last_price": last_price,
                "last_date": last_date.strftime("%Y-%m-%d"),
                "average_turnover": average_turnover,
                "annual_volatility": annual_volatility,
                "raw_factors": values,
            }
        )

    grades: dict[str, dict[str, float]] = {
        factor: _rank_percentiles(
            {
                item["symbol"]: float(item["raw_factors"][factor])
                for item in raw_rows
            }
        )
        for factor in FACTOR_LABELS
    }
    total_weight = float(sum(float(value) for value in weights.values()))
    ranked: list[dict[str, Any]] = []
    for item in raw_rows:
        factor_rows = {}
        composite = 0.0
        for factor, label in FACTOR_LABELS.items():
            grade = float(grades[factor][item["symbol"]])
            factor_weight = float(weights[factor])
            composite += factor_weight * grade
            factor_rows[factor] = {
                "label": label,
                "raw_value": _rounded(item["raw_factors"][factor], 6),
                "percentile": round(grade, 2),
                "weight": factor_weight,
                "peer_count": len(raw_rows),
            }
        ranked.append(
            {
                "symbol": item["symbol"],
                "name": item["name"],
                "parent_weight_pct": item.get("parent_weight_pct"),
                "last_price": round(float(item["last_price"]), 4),
                "last_date": item["last_date"],
                "average_turnover": round(float(item["average_turnover"]), 2),
                "annual_volatility_pct": round(
                    float(item["annual_volatility"]) * 100, 3
                ),
                "composite_score": round(composite / total_weight, 3),
                "factors": factor_rows,
            }
        )
    ranked.sort(key=lambda item: (-item["composite_score"], item["symbol"]))
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    selected = [
        item
        for item in ranked
        if item["composite_score"] >= float(policy["minimum_composite_score"])
    ][: int(policy["max_positions"])]
    return {
        "signal_date": signal_date.strftime("%Y-%m-%d"),
        "member_count": len(members),
        "eligible_count": len(ranked),
        "selected_count": len(selected),
        "ranked": ranked,
        "selected": selected,
        "exclusions": exclusions,
    }


def _capped_weights(
    raw: list[float],
    *,
    target: float,
    cap: float,
) -> list[float]:
    if not raw:
        return []
    weights = [0.0 for _ in raw]
    active = set(range(len(raw)))
    remaining = max(0.0, float(target))
    while active and remaining > 1e-10:
        denominator = sum(max(0.0, raw[index]) for index in active)
        proposed = {
            index: remaining
            * (
                max(0.0, raw[index]) / denominator
                if denominator > 0
                else 1 / len(active)
            )
            for index in active
        }
        capped = [
            index
            for index, value in proposed.items()
            if weights[index] + value > cap + 1e-10
        ]
        if not capped:
            for index, value in proposed.items():
                weights[index] += value
            break
        for index in capped:
            room = max(0.0, cap - weights[index])
            weights[index] += room
            remaining -= room
            active.remove(index)
    return weights


def _target_weights(
    selected: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    construction = policy["construction_method"]
    raw = []
    for item in selected:
        volatility = max(0.0001, float(item["annual_volatility_pct"]) / 100)
        score_edge = max(1.0, float(item["composite_score"]) - 45)
        if construction == "equal_weight":
            value = 1.0
        elif construction == "inverse_volatility":
            value = 1 / volatility
        else:
            value = score_edge / volatility
        raw.append(value)
    target_invested = 100 - float(policy["minimum_cash_pct"])
    weights = _capped_weights(
        raw,
        target=target_invested,
        cap=float(policy["max_position_pct"]),
    )
    return [
        {
            **item,
            "target_weight_pct": round(weight, 4),
        }
        for item, weight in zip(selected, weights)
        if weight > 0
    ]


def _last_value(
    frame: pd.DataFrame,
    date: pd.Timestamp,
    column: str,
) -> float | None:
    values = frame.loc[frame.index <= date, column].dropna()
    return float(values.iloc[-1]) if not values.empty else None


def _portfolio_value(
    *,
    date: pd.Timestamp,
    cash: float,
    shares: dict[str, float],
    frames: dict[str, pd.DataFrame],
) -> tuple[float, dict[str, float], list[str]]:
    total = float(cash)
    values = {}
    stale = []
    for symbol, quantity in shares.items():
        if abs(quantity) <= 1e-12:
            continue
        frame = frames[symbol]
        available = frame.loc[frame.index <= date]
        if available.empty:
            stale.append(symbol)
            continue
        row = available.iloc[-1]
        price = float(row["close"])
        value = float(quantity) * price
        values[symbol] = value
        total += value
        if int((date - pd.Timestamp(row["date"]).normalize()).days) > 10:
            stale.append(symbol)
    return total, values, stale


def _drawdown(values: pd.Series) -> pd.Series:
    return values / values.cummax() - 1


def _performance(
    equity_rows: list[dict[str, Any]],
    benchmark: pd.DataFrame,
    *,
    initial_capital: float,
) -> dict[str, Any]:
    if len(equity_rows) < 2:
        return {
            "trading_days": len(equity_rows),
            "total_return_pct": None,
            "annualized_return_pct": None,
            "annualized_volatility_pct": None,
            "sharpe": None,
            "sortino": None,
            "max_drawdown_pct": None,
            "benchmark_return_pct": None,
            "net_excess_return_pct": None,
            "information_ratio": None,
        }
    equity = pd.DataFrame(equity_rows)
    equity["date"] = pd.to_datetime(equity["date"])
    equity = equity.set_index("date")["equity"].astype(float)
    returns = equity.pct_change().dropna()
    years = max(len(returns) / 252, 1 / 252)
    total_return = float(equity.iloc[-1] / initial_capital - 1)
    annualized = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1
    volatility = float(returns.std(ddof=1) * math.sqrt(252)) if len(returns) > 1 else None
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * math.sqrt(252))
        if len(returns) > 1 and float(returns.std(ddof=1)) > 0
        else None
    )
    downside = returns[returns < 0]
    sortino = (
        float(returns.mean() / downside.std(ddof=1) * math.sqrt(252))
        if len(downside) > 1 and float(downside.std(ddof=1)) > 0
        else None
    )
    max_drawdown = float(_drawdown(equity).min())

    benchmark_series = (
        benchmark.set_index("date")["close"].astype(float).sort_index()
    )
    aligned = pd.concat(
        [equity.rename("strategy"), benchmark_series.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    benchmark_return = None
    excess = None
    information_ratio = None
    tracking_error = None
    if len(aligned) >= 2:
        benchmark_return = float(
            aligned["benchmark"].iloc[-1] / aligned["benchmark"].iloc[0] - 1
        )
        excess = total_return - benchmark_return
        paired_returns = aligned.pct_change().dropna()
        active_returns = paired_returns["strategy"] - paired_returns["benchmark"]
        if len(active_returns) > 1:
            tracking_error = float(active_returns.std(ddof=1) * math.sqrt(252))
            if tracking_error > 0:
                information_ratio = float(
                    active_returns.mean()
                    / active_returns.std(ddof=1)
                    * math.sqrt(252)
                )
    return {
        "trading_days": int(len(equity_rows)),
        "total_return_pct": round(total_return * 100, 3),
        "annualized_return_pct": round(annualized * 100, 3),
        "annualized_volatility_pct": _rounded(
            volatility * 100 if volatility is not None else None, 3
        ),
        "sharpe": _rounded(sharpe, 3),
        "sortino": _rounded(sortino, 3),
        "max_drawdown_pct": round(max_drawdown * 100, 3),
        "benchmark_return_pct": _rounded(
            benchmark_return * 100 if benchmark_return is not None else None, 3
        ),
        "net_excess_return_pct": _rounded(
            excess * 100 if excess is not None else None, 3
        ),
        "tracking_error_pct": _rounded(
            tracking_error * 100 if tracking_error is not None else None, 3
        ),
        "information_ratio": _rounded(information_ratio, 3),
    }


@dataclass
class _Order:
    order_id: str
    symbol: str
    side: str
    signal_date: pd.Timestamp
    remaining_notional: float
    requested_notional: float
    target_weight_pct: float
    rank: int | None
    composite_score: float | None
    attempts: int = 0


def _simulate_core(
    *,
    frames: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    snapshots: list[dict[str, Any]],
    policy: dict[str, Any],
    cost_multiplier: float,
    include_details: bool,
) -> dict[str, Any]:
    benchmark_dates = pd.DatetimeIndex(benchmark.index).sort_values().unique()
    if len(benchmark_dates) < int(policy["lookback_days"]) + 3:
        raise QuantSelectionInputError("基准历史不足，无法形成样本外区间")
    start_cursor = max(
        int(policy["lookback_days"]),
        int(policy["minimum_history_days"]),
    )
    signal_dates = list(
        benchmark_dates[
            start_cursor : -1 : int(policy["rebalance_days"])
        ]
    )
    if not signal_dates:
        raise QuantSelectionInputError("没有可用调仓日")
    simulation_dates = benchmark_dates[
        benchmark_dates >= signal_dates[0]
    ]
    initial_capital = float(policy["initial_capital"])
    cash = initial_capital
    shares: dict[str, float] = {}
    pending: list[_Order] = []
    order_sequence = 0
    fill_sequence = 0
    order_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    total_commission = 0.0
    total_slippage = 0.0
    total_tax = 0.0
    total_filled_notional = 0.0
    total_requested_notional = 0.0
    cancelled_notional = 0.0
    partial_fill_count = 0
    zero_volume_rejections = 0
    stale_holding_days = 0

    signal_set = {pd.Timestamp(value).normalize() for value in signal_dates}

    def cancel_order(order: _Order, reason: str, date: pd.Timestamp) -> None:
        nonlocal cancelled_notional
        cancelled_notional += max(0.0, order.remaining_notional)
        order_rows.append(
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side,
                "signal_date": order.signal_date.strftime("%Y-%m-%d"),
                "event_date": date.strftime("%Y-%m-%d"),
                "event": "cancelled",
                "reason": reason,
                "remaining_notional": round(order.remaining_notional, 2),
            }
        )

    for date_value in simulation_dates:
        date = pd.Timestamp(date_value).normalize()
        # Orders are generated after the signal close and can only trade later.
        active = [
            order for order in pending if order.signal_date < date
        ]
        waiting = [
            order for order in pending if order.signal_date >= date
        ]
        next_pending: list[_Order] = list(waiting)

        for side in ("sell", "buy"):
            for order in [item for item in active if item.side == side]:
                frame = frames.get(order.symbol)
                if frame is None or date not in frame.index:
                    order.attempts += 1
                    if order.attempts >= int(policy["max_order_age_sessions"]):
                        cancel_order(order, "no_tradable_bar", date)
                    else:
                        next_pending.append(order)
                    continue
                row = frame.loc[date]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
                execution_open = _number(row.get("execution_open"))
                turnover = _number(row.get("raw_turnover"), 0.0) or 0.0
                if execution_open is None or execution_open <= 0 or turnover <= 0:
                    zero_volume_rejections += 1
                    order.attempts += 1
                    if order.attempts >= int(policy["max_order_age_sessions"]):
                        cancel_order(order, "suspended_or_zero_volume", date)
                    else:
                        next_pending.append(order)
                    continue
                participation_limit = (
                    float(policy["max_volume_participation_pct"]) / 100
                )
                capacity = turnover * participation_limit
                fill_notional = min(order.remaining_notional, capacity)
                if fill_notional <= 0:
                    order.attempts += 1
                    next_pending.append(order)
                    continue
                utilization = min(1.0, fill_notional / max(capacity, 1e-12))
                slippage_bps = (
                    float(policy["slippage_bps"])
                    + float(policy["impact_bps"]) * utilization**2
                ) * cost_multiplier
                commission_bps = (
                    float(policy["commission_bps"]) * cost_multiplier
                )
                tax_bps = (
                    float(policy["sell_tax_bps"]) * cost_multiplier
                    if side == "sell"
                    else 0.0
                )
                effective_price = execution_open * (
                    1 + slippage_bps / 10_000
                    if side == "buy"
                    else 1 - slippage_bps / 10_000
                )
                if side == "buy":
                    maximum_cash_notional = cash / (
                        1 + commission_bps / 10_000
                    )
                    fill_notional = min(fill_notional, maximum_cash_notional)
                    if fill_notional < float(policy["minimum_order_notional"]):
                        order.attempts += 1
                        if order.attempts >= int(policy["max_order_age_sessions"]):
                            cancel_order(order, "insufficient_cash", date)
                        else:
                            next_pending.append(order)
                        continue
                else:
                    owned = max(0.0, shares.get(order.symbol, 0.0))
                    fill_notional = min(
                        fill_notional,
                        owned * effective_price,
                    )
                    if fill_notional <= 1e-8:
                        cancel_order(order, "position_already_closed", date)
                        continue

                quantity = fill_notional / effective_price
                commission = fill_notional * commission_bps / 10_000
                tax = fill_notional * tax_bps / 10_000
                slippage_cost = (
                    abs(effective_price - execution_open) * quantity
                )
                if side == "buy":
                    cash -= fill_notional + commission
                    shares[order.symbol] = (
                        shares.get(order.symbol, 0.0) + quantity
                    )
                else:
                    shares[order.symbol] = max(
                        0.0, shares.get(order.symbol, 0.0) - quantity
                    )
                    cash += fill_notional - commission - tax
                fill_sequence += 1
                total_filled_notional += fill_notional
                total_commission += commission
                total_tax += tax
                total_slippage += slippage_cost
                order.remaining_notional = max(
                    0.0, order.remaining_notional - fill_notional
                )
                partial = order.remaining_notional >= float(
                    policy["minimum_order_notional"]
                )
                if partial:
                    partial_fill_count += 1
                fill_rows.append(
                    {
                        "fill_id": f"qfill_{fill_sequence:06d}",
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "side": side,
                        "signal_date": order.signal_date.strftime("%Y-%m-%d"),
                        "fill_date": date.strftime("%Y-%m-%d"),
                        "reference_open": round(execution_open, 6),
                        "effective_price": round(effective_price, 6),
                        "quantity": round(quantity, 8),
                        "notional": round(fill_notional, 2),
                        "commission": round(commission, 2),
                        "sell_tax": round(tax, 2),
                        "slippage_cost": round(slippage_cost, 2),
                        "slippage_bps": round(slippage_bps, 3),
                        "capacity_notional": round(capacity, 2),
                        "capacity_utilization_pct": round(
                            utilization * 100, 3
                        ),
                        "partial": partial,
                    }
                )
                if partial:
                    order.attempts += 1
                    if order.attempts >= int(policy["max_order_age_sessions"]):
                        cancel_order(order, "partial_fill_expired", date)
                    else:
                        next_pending.append(order)
                else:
                    order_rows.append(
                        {
                            "order_id": order.order_id,
                            "symbol": order.symbol,
                            "side": side,
                            "signal_date": order.signal_date.strftime("%Y-%m-%d"),
                            "event_date": date.strftime("%Y-%m-%d"),
                            "event": "filled",
                            "reason": "completed",
                            "remaining_notional": 0.0,
                        }
                    )
        pending = next_pending

        equity, position_values, stale = _portfolio_value(
            date=date,
            cash=cash,
            shares=shares,
            frames=frames,
        )
        stale_holding_days += len(stale)
        equity_rows.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "equity": round(equity, 4),
                "cash": round(cash, 4),
                "cash_pct": round(cash / equity * 100, 3) if equity > 0 else None,
                "position_count": sum(
                    1 for value in position_values.values() if value > 1e-8
                ),
                "stale_holding_count": len(stale),
            }
        )

        if date not in signal_set:
            continue
        for order in pending:
            cancel_order(order, "replaced_by_new_rebalance", date)
        pending = []
        members = _members_for(snapshots, date)
        factor_snapshot = _factor_snapshot(
            signal_date=date,
            members=members,
            frames=frames,
            policy=policy,
        )
        targets = _target_weights(factor_snapshot["selected"], policy)
        factor_snapshot["targets"] = [
            {
                key: value
                for key, value in target.items()
                if key not in {"factors"}
            }
            for target in targets
        ]
        factor_snapshot["target_cash_pct"] = round(
            100
            - sum(float(item["target_weight_pct"]) for item in targets),
            4,
        )
        signal_rows.append(factor_snapshot)
        target_map = {
            item["symbol"]: item for item in targets
        }
        symbols = set(target_map) | {
            symbol
            for symbol, quantity in shares.items()
            if quantity > 1e-12
        }
        proposed: list[_Order] = []
        for symbol in sorted(symbols):
            target = target_map.get(symbol)
            target_weight = (
                float(target["target_weight_pct"]) / 100 if target else 0.0
            )
            current_value = float(position_values.get(symbol, 0.0))
            desired_value = equity * target_weight
            delta = desired_value - current_value
            if abs(delta) < float(policy["minimum_order_notional"]):
                continue
            order_sequence += 1
            order = _Order(
                order_id=f"qorder_{order_sequence:06d}",
                symbol=symbol,
                side="buy" if delta > 0 else "sell",
                signal_date=date,
                remaining_notional=abs(delta),
                requested_notional=abs(delta),
                target_weight_pct=target_weight * 100,
                rank=int(target["rank"]) if target else None,
                composite_score=(
                    float(target["composite_score"]) if target else None
                ),
            )
            proposed.append(order)
            total_requested_notional += abs(delta)
            order_rows.append(
                {
                    "order_id": order.order_id,
                    "symbol": symbol,
                    "side": order.side,
                    "signal_date": date.strftime("%Y-%m-%d"),
                    "event_date": date.strftime("%Y-%m-%d"),
                    "event": "submitted",
                    "reason": "rebalance_after_close",
                    "requested_notional": round(abs(delta), 2),
                    "target_weight_pct": round(target_weight * 100, 4),
                    "rank": order.rank,
                    "composite_score": _rounded(
                        order.composite_score, 3
                    ),
                }
            )
        pending = proposed

    final_date = pd.Timestamp(simulation_dates[-1]).normalize()
    for order in pending:
        cancel_order(order, "simulation_ended", final_date)
    performance = _performance(
        equity_rows,
        benchmark.reset_index(drop=True),
        initial_capital=initial_capital,
    )
    benchmark_series = benchmark["close"].astype(float).sort_index()
    benchmark_curve = []
    if equity_rows:
        first_equity_date = pd.Timestamp(
            equity_rows[0]["date"]
        ).normalize()
        selected_benchmark = benchmark_series.loc[
            benchmark_series.index >= first_equity_date
        ]
        if not selected_benchmark.empty:
            benchmark_base = float(selected_benchmark.iloc[0])
            benchmark_curve = [
                {
                    "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                    "equity": round(
                        initial_capital
                        * float(value)
                        / benchmark_base,
                        4,
                    ),
                }
                for date, value in selected_benchmark.items()
            ]
    average_equity = (
        float(np.mean([item["equity"] for item in equity_rows]))
        if equity_rows
        else initial_capital
    )
    average_fill_utilization = (
        float(
            np.mean(
                [item["capacity_utilization_pct"] for item in fill_rows]
            )
        )
        if fill_rows
        else None
    )
    output = {
        "performance": performance,
        "execution": {
            "order_count": sum(
                item.get("event") == "submitted" for item in order_rows
            ),
            "fill_count": len(fill_rows),
            "partial_fill_count": partial_fill_count,
            "zero_volume_rejection_count": zero_volume_rejections,
            "total_requested_notional": round(total_requested_notional, 2),
            "total_filled_notional": round(total_filled_notional, 2),
            "cancelled_notional": round(cancelled_notional, 2),
            "unfilled_requested_pct": round(
                cancelled_notional
                / max(total_requested_notional, 1e-12)
                * 100,
                3,
            ),
            "turnover_pct": round(
                total_filled_notional / max(average_equity, 1e-12) * 100,
                3,
            ),
            "commission": round(total_commission, 2),
            "sell_tax": round(total_tax, 2),
            "slippage_cost": round(total_slippage, 2),
            "total_cost": round(
                total_commission + total_tax + total_slippage, 2
            ),
            "cost_drag_initial_capital_pct": round(
                (total_commission + total_tax + total_slippage)
                / initial_capital
                * 100,
                3,
            ),
            "average_capacity_utilization_pct": _rounded(
                average_fill_utilization, 3
            ),
            "stale_holding_days": stale_holding_days,
        },
        "signals": signal_rows,
        "equity_curve": equity_rows,
        "benchmark_curve": benchmark_curve,
    }
    if include_details:
        output["orders"] = order_rows
        output["fills"] = fill_rows
    return output


def _rank_ic(
    signals: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    observations = []
    for index, signal in enumerate(signals[:-1]):
        start = pd.Timestamp(signal["signal_date"]).normalize()
        end = pd.Timestamp(signals[index + 1]["signal_date"]).normalize()
        scores = {}
        forwards = {}
        for item in signal.get("ranked") or []:
            symbol = item["symbol"]
            frame = frames.get(symbol)
            if frame is None:
                continue
            start_price = _last_value(frame, start, "close")
            end_price = _last_value(frame, end, "close")
            if (
                start_price is None
                or end_price is None
                or start_price <= 0
            ):
                continue
            scores[symbol] = float(item["composite_score"])
            forwards[symbol] = end_price / start_price - 1
        symbols = sorted(set(scores) & set(forwards))
        coefficient = None
        if len(symbols) >= 4:
            coefficient = pd.Series(
                [scores[symbol] for symbol in symbols]
            ).corr(
                pd.Series([forwards[symbol] for symbol in symbols]),
                method="spearman",
            )
            coefficient = _number(coefficient)
        observations.append(
            {
                "signal_date": start.strftime("%Y-%m-%d"),
                "forward_date": end.strftime("%Y-%m-%d"),
                "sample_count": len(symbols),
                "rank_ic": _rounded(coefficient, 4),
            }
        )
    valid = [
        float(item["rank_ic"])
        for item in observations
        if item.get("rank_ic") is not None
    ]
    mean_ic = float(np.mean(valid)) if valid else None
    std_ic = float(np.std(valid, ddof=1)) if len(valid) > 1 else None
    return {
        "method": "Spearman(信号日综合分, 下一调仓日前收盘收益)",
        "observation_count": len(valid),
        "mean_rank_ic": _rounded(mean_ic, 4),
        "rank_ic_information_ratio": _rounded(
            mean_ic / std_ic
            if mean_ic is not None and std_ic and std_ic > 0
            else None,
            3,
        ),
        "positive_rate_pct": _rounded(
            sum(value > 0 for value in valid) / len(valid) * 100
            if valid
            else None,
            1,
        ),
        "observations": observations,
    }


def _walk_forward_segments(
    equity_curve: list[dict[str, Any]],
    benchmark: pd.DataFrame,
    *,
    segment_days: int,
) -> dict[str, Any]:
    if len(equity_curve) < 2:
        return {
            "method": "non_overlapping_out_of_sample_segments",
            "segment_days": segment_days,
            "segments": [],
            "segment_count": 0,
            "positive_excess_rate_pct": None,
        }
    equity = pd.DataFrame(equity_curve)
    equity["date"] = pd.to_datetime(equity["date"])
    equity = equity.set_index("date")["equity"].astype(float)
    bench = benchmark.set_index("date")["close"].astype(float).sort_index()
    aligned = pd.concat(
        [equity.rename("equity"), bench.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    segments = []
    for start in range(0, len(aligned) - 1, int(segment_days)):
        window = aligned.iloc[start : start + int(segment_days) + 1]
        if len(window) < max(42, int(segment_days) // 2):
            continue
        strategy_return = float(
            window["equity"].iloc[-1] / window["equity"].iloc[0] - 1
        )
        benchmark_return = float(
            window["benchmark"].iloc[-1]
            / window["benchmark"].iloc[0]
            - 1
        )
        drawdown = float(_drawdown(window["equity"]).min())
        segments.append(
            {
                "segment_no": len(segments) + 1,
                "start_date": window.index[0].strftime("%Y-%m-%d"),
                "end_date": window.index[-1].strftime("%Y-%m-%d"),
                "trading_days": len(window),
                "strategy_return_pct": round(strategy_return * 100, 3),
                "benchmark_return_pct": round(
                    benchmark_return * 100, 3
                ),
                "net_excess_return_pct": round(
                    (strategy_return - benchmark_return) * 100, 3
                ),
                "max_drawdown_pct": round(drawdown * 100, 3),
                "positive_excess": strategy_return > benchmark_return,
            }
        )
    return {
        "method": "non_overlapping_out_of_sample_segments",
        "detail": (
            "因子和权重固定，不在测试窗口调参；每段仅汇总此前已按"
            "信号日可见数据产生的连续组合收益。"
        ),
        "segment_days": int(segment_days),
        "segment_count": len(segments),
        "positive_excess_rate_pct": _rounded(
            sum(item["positive_excess"] for item in segments)
            / len(segments)
            * 100
            if segments
            else None,
            1,
        ),
        "segments": segments,
    }


def _selection_stability(signals: list[dict[str, Any]]) -> dict[str, Any]:
    overlaps = []
    for prior, current in zip(signals, signals[1:]):
        left = {item["symbol"] for item in prior.get("targets") or []}
        right = {item["symbol"] for item in current.get("targets") or []}
        union = left | right
        overlaps.append(len(left & right) / len(union) if union else 1.0)
    return {
        "rebalance_count": len(signals),
        "average_jaccard_retention_pct": _rounded(
            float(np.mean(overlaps)) * 100 if overlaps else None, 1
        ),
        "minimum_jaccard_retention_pct": _rounded(
            min(overlaps) * 100 if overlaps else None, 1
        ),
    }


def _promotion_gate(
    *,
    policy: dict[str, Any],
    universe_evidence: dict[str, Any],
    data_quality: dict[str, Any],
    base: dict[str, Any],
    stress: dict[str, Any],
    walk_forward: dict[str, Any],
    rank_ic: dict[str, Any],
) -> dict[str, Any]:
    performance = base["performance"]
    execution = base["execution"]
    stress_performance = stress["performance"]
    checks = [
        {
            "code": "point_in_time_universe",
            "label": "历史时点股票池",
            "passed": bool(universe_evidence.get("point_in_time_verified")),
            "detail": str(
                universe_evidence.get("verification_detail")
                or "需要专业历史成分数据"
            ),
        },
        {
            "code": "minimum_universe",
            "label": "股票池宽度",
            "passed": int(universe_evidence.get("maximum_member_count") or 0)
            >= 8,
            "detail": (
                f"最大逐期成员数 {universe_evidence.get('maximum_member_count') or 0}，"
                "最低 8"
            ),
        },
        {
            "code": "professional_price_sources",
            "label": "专业行情覆盖",
            "passed": float(
                data_quality.get("professional_source_coverage_pct") or 0
            )
            >= 100,
            "detail": (
                f"专业调整/未复权行情双源覆盖 "
                f"{data_quality.get('professional_source_coverage_pct') or 0:.1f}%"
            ),
        },
        {
            "code": "minimum_oos_segments",
            "label": "样本外窗口",
            "passed": int(walk_forward.get("segment_count") or 0) >= 4,
            "detail": (
                f"非重叠窗口 {walk_forward.get('segment_count') or 0} 个，最低 4"
            ),
        },
        {
            "code": "segment_consistency",
            "label": "跨窗口一致性",
            "passed": float(
                walk_forward.get("positive_excess_rate_pct") or 0
            )
            >= 60,
            "detail": (
                f"跑赢基准窗口占比 "
                f"{walk_forward.get('positive_excess_rate_pct')}%，最低 60%"
            ),
        },
        {
            "code": "positive_net_excess",
            "label": "成本后超额",
            "passed": float(
                performance.get("net_excess_return_pct") or -1e9
            )
            > 0,
            "detail": (
                f"全期成本后超额 "
                f"{performance.get('net_excess_return_pct')}%"
            ),
        },
        {
            "code": "cost_stress",
            "label": "成本翻倍压力",
            "passed": float(
                stress_performance.get("net_excess_return_pct") or -1e9
            )
            > 0,
            "detail": (
                f"成本翻倍后超额 "
                f"{stress_performance.get('net_excess_return_pct')}%"
            ),
        },
        {
            "code": "drawdown_budget",
            "label": "回撤预算",
            "passed": abs(
                float(performance.get("max_drawdown_pct") or -1e9)
            )
            <= float(policy["maximum_drawdown_pct"]),
            "detail": (
                f"最大回撤 {performance.get('max_drawdown_pct')}%，"
                f"上限 {policy['maximum_drawdown_pct']}%"
            ),
        },
        {
            "code": "capacity",
            "label": "成交容量",
            "passed": float(execution.get("unfilled_requested_pct") or 0)
            <= 10
            and int(execution.get("stale_holding_days") or 0) == 0,
            "detail": (
                f"未成交 {execution.get('unfilled_requested_pct')}%，"
                f"陈旧持仓日 {execution.get('stale_holding_days')}"
            ),
        },
        {
            "code": "rank_ic",
            "label": "截面排序信息",
            "passed": int(rank_ic.get("observation_count") or 0) >= 6
            and float(rank_ic.get("mean_rank_ic") or -1e9) > 0,
            "detail": (
                f"Rank IC 均值 {rank_ic.get('mean_rank_ic')}，"
                f"有效观察 {rank_ic.get('observation_count') or 0}"
            ),
        },
    ]
    ready = all(item["passed"] for item in checks)
    return {
        "status": "paper_ready" if ready else "research_only",
        "paper_shadow_eligible": ready,
        "passed_count": sum(item["passed"] for item in checks),
        "total_count": len(checks),
        "checks": checks,
        "notice": (
            "仅允许冻结为前向纸面策略，不连接券商、不自动下单。"
            if ready
            else "至少一个研究或执行门槛未通过；结果不得升级为纸面策略。"
        ),
    }


def run_selection_research(
    *,
    frames: dict[str, pd.DataFrame],
    benchmark_symbol: str,
    universe_snapshots: list[dict[str, Any]],
    policy: dict[str, Any],
    universe_evidence: dict[str, Any],
    source_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Run deterministic point-in-time selection and execution research."""
    if benchmark_symbol not in frames:
        raise QuantSelectionInputError("基准行情缺失")
    normalized = {
        symbol: _normalize_frame(frame, symbol)
        for symbol, frame in frames.items()
    }
    benchmark = normalized[benchmark_symbol]
    snapshots = _normalize_snapshots(universe_snapshots)
    base = _simulate_core(
        frames=normalized,
        benchmark=benchmark,
        snapshots=snapshots,
        policy=policy,
        cost_multiplier=1.0,
        include_details=True,
    )
    stress = _simulate_core(
        frames=normalized,
        benchmark=benchmark,
        snapshots=snapshots,
        policy=policy,
        cost_multiplier=2.0,
        include_details=False,
    )
    walk_forward = _walk_forward_segments(
        base["equity_curve"],
        benchmark,
        segment_days=int(policy["oos_segment_days"]),
    )
    rank_ic = _rank_ic(base["signals"], normalized)
    stability = _selection_stability(base["signals"])
    candidate_symbols = sorted(
        {
            member["symbol"]
            for snapshot in snapshots
            for member in snapshot["members"]
        }
    )
    source_rows = []
    professional = 0
    professional_adjusted = 0
    professional_raw = 0
    independent_raw = 0
    for symbol in candidate_symbols:
        evidence = source_evidence.get(symbol) or {}
        adjusted = str(evidence.get("adjusted_source") or "")
        raw = str(evidence.get("raw_source") or "")
        has_independent_raw = bool(
            raw
            and raw
            not in {
                "adjusted_price_fallback",
                "adjusted_price_research_fallback",
            }
        )
        adjusted_professional = adjusted.startswith(
            PROFESSIONAL_SOURCE_PREFIXES
        )
        raw_professional = (
            has_independent_raw
            and raw.startswith(PROFESSIONAL_SOURCE_PREFIXES)
        )
        is_professional = adjusted_professional and raw_professional
        professional += int(is_professional)
        professional_adjusted += int(adjusted_professional)
        professional_raw += int(raw_professional)
        independent_raw += int(has_independent_raw)
        frame = normalized.get(symbol)
        source_rows.append(
            {
                "symbol": symbol,
                "adjusted_source": adjusted or None,
                "raw_source": raw or None,
                "raw_requested": bool(
                    evidence.get("raw_requested", True)
                ),
                "raw_note": evidence.get("raw_note"),
                "independent_raw_source": has_independent_raw,
                "adjusted_source_professional": adjusted_professional,
                "raw_source_professional": raw_professional,
                "professional_pair": is_professional,
                "row_count": len(frame) if frame is not None else 0,
                "first_date": (
                    frame.index[0].strftime("%Y-%m-%d")
                    if frame is not None and len(frame)
                    else None
                ),
                "last_date": (
                    frame.index[-1].strftime("%Y-%m-%d")
                    if frame is not None and len(frame)
                    else None
                ),
            }
        )
    data_quality = {
        "requested_asset_count": len(candidate_symbols),
        "loaded_asset_count": sum(
            symbol in normalized for symbol in candidate_symbols
        ),
        "professional_source_count": professional,
        "professional_source_coverage_pct": round(
            professional / max(len(candidate_symbols), 1) * 100, 1
        ),
        "professional_adjusted_source_count": professional_adjusted,
        "professional_adjusted_source_coverage_pct": round(
            professional_adjusted
            / max(len(candidate_symbols), 1)
            * 100,
            1,
        ),
        "professional_raw_source_count": professional_raw,
        "professional_raw_source_coverage_pct": round(
            professional_raw
            / max(len(candidate_symbols), 1)
            * 100,
            1,
        ),
        "independent_raw_source_count": independent_raw,
        "independent_raw_source_coverage_pct": round(
            independent_raw
            / max(len(candidate_symbols), 1)
            * 100,
            1,
        ),
        "benchmark_symbol": benchmark_symbol,
        "benchmark_source": source_evidence.get(benchmark_symbol) or {},
        "assets": source_rows,
        "price_modes": {
            "factor_signal": "供应商复权 OHLC",
            "execution": (
                (
                    "未复权开盘价映射到复权价格尺度；成交容量使用未复权"
                    "开盘价×原始成交量"
                )
                if independent_raw == len(candidate_symbols)
                else (
                    "缺少独立未复权日线的资产使用复权价研究回退；"
                    "该回退会如实降低数据覆盖并阻止策略升级"
                )
            ),
        },
    }
    latest_signal = base["signals"][-1] if base["signals"] else None
    promotion = _promotion_gate(
        policy=policy,
        universe_evidence=universe_evidence,
        data_quality=data_quality,
        base=base,
        stress=stress,
        walk_forward=walk_forward,
        rank_ic=rank_ic,
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "policy": policy,
        "universe": {
            **universe_evidence,
            "snapshot_count": len(snapshots),
            "first_snapshot_date": snapshots[0]["as_of_text"],
            "last_snapshot_date": snapshots[-1]["as_of_text"],
            "unique_symbol_count": len(candidate_symbols),
            "snapshots": [
                {
                    "as_of": snapshot["as_of_text"],
                    "source": snapshot["source"],
                    "member_count": len(snapshot["members"]),
                    "members": snapshot["members"],
                }
                for snapshot in snapshots
            ],
        },
        "data_quality": data_quality,
        "performance": base["performance"],
        "execution": base["execution"],
        "walk_forward": walk_forward,
        "rank_ic": rank_ic,
        "selection_stability": stability,
        "stress_test": {
            "scenario": "commission_slippage_tax_x2",
            "performance": stress["performance"],
            "execution": stress["execution"],
        },
        "latest_signal": latest_signal,
        "signals": base["signals"],
        "equity_curve": base["equity_curve"],
        "benchmark_curve": base["benchmark_curve"],
        "orders": base["orders"],
        "fills": base["fills"],
        "promotion_gate": promotion,
        "execution_authorized": False,
        "broker_connected": False,
        "quantity_generated": False,
        "methodology": {
            "universe": (
                "每个信号日只使用该日已生效的冻结股票池快照；"
                "当前名单不会回填到过去。"
            ),
            "factors": (
                "中期动量跳过最近 21 个交易日，另用趋势拟合质量、"
                "63 日波动与 63 日平均成交额做横截面百分位。"
            ),
            "timing": (
                "信号在收盘后生成，订单最早在下一交易日开盘处理，"
                "禁止同一根日线成交。"
            ),
            "execution": (
                "先卖后买；受成交量参与率限制，允许部分成交和超时撤单；"
                "滑点由基础值加容量利用率平方冲击组成。"
            ),
            "walk_forward": (
                "固定因子定义与权重，按非重叠窗口汇总连续样本外表现，"
                "不在测试窗口内选择最优参数。"
            ),
        },
        "limitations": [
            "这是日线级事件模拟，不含盘口队列、涨跌停封单、逐笔成交与券商实际拒单规则。",
            "港股每只证券的真实整手数量未纳入，结果只适合研究和前向纸面验证。",
            "专业历史指数成分可降低幸存者偏差，但不能消除数据修订、供应商回填和公司行动模型误差。",
            "因子历史有效不代表未来继续有效；任何纸面资格都不是买入建议或收益承诺。",
        ],
    }
