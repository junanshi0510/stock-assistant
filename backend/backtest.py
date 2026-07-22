# -*- coding: utf-8 -*-
"""
股票信号历史验证。

同一份结果包含两种不可混用的口径：
1. 方向统计：每个历史交易日的打分与未来 N 日涨跌，样本会重叠。
2. 执行仿真：信号日不成交，下一交易日开盘进场，只做多，不重叠持仓，
   按信号日 ATR 设置止损/止盈，并扣除用户给定的佣金、滑点和卖出税费场景值。

执行仿真仍是基于日线 OHLC 的假设性结果，不包含停牌、涨跌停无法成交、
整手限制、分红拆股、汇率和实际券商规则，不代表未来表现。
"""

import numpy as np
import pandas as pd

import analysis


EXECUTION_POLICY_VERSION = "stock_signal_execution_backtest@1.0.0"
ROBUSTNESS_POLICY_VERSION = "stock_signal_robustness@1.0.0"

PARAMETER_MIN_TRADES = 20
HOLDOUT_MIN_TRADES = 10
PERIOD_MIN_TRADES = 5
MIN_PARAMETER_COVERAGE_PCT = 60.0
MIN_PARAMETER_PASS_RATE_PCT = 70.0
MIN_EVALUABLE_PERIODS = 3
MIN_PERIOD_PASS_RATE_PCT = 50.0


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _rounded(value, digits: int = 2):
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _validate_execution_inputs(
    *,
    horizon: int,
    entry_score: float,
    stop_atr: float,
    target_atr: float,
    commission_bps: float,
    slippage_bps: float,
    sell_tax_bps: float,
    risk_per_trade_pct: float,
    max_position_pct: float,
) -> None:
    bounds = {
        "horizon": (horizon, 3, 60),
        "entry_score": (entry_score, 50, 90),
        "stop_atr": (stop_atr, 0.5, 6),
        "target_atr": (target_atr, 0.5, 12),
        "commission_bps": (commission_bps, 0, 100),
        "slippage_bps": (slippage_bps, 0, 100),
        "sell_tax_bps": (sell_tax_bps, 0, 200),
        "risk_per_trade_pct": (risk_per_trade_pct, 0.1, 5),
        "max_position_pct": (max_position_pct, 1, 100),
    }
    for name, (value, lower, upper) in bounds.items():
        number = _finite(value)
        if number is None or number < lower or number > upper:
            raise ValueError(f"{name} 必须在 {lower} 到 {upper} 之间。")


def _research_gate(trade_count: int, expectancy: float, profit_factor: float | None) -> dict:
    """Classify historical evidence without turning it into a trade instruction."""
    if trade_count < 20:
        return {
            "status": "insufficient_samples",
            "label": "样本不足",
            "historically_positive": False,
            "detail": f"只有 {trade_count} 笔非重叠交易，未达到 20 笔最低研究门槛。",
        }
    if expectancy <= 0 or (profit_factor is not None and profit_factor <= 1):
        return {
            "status": "non_positive_expectancy",
            "label": "历史净期望未通过",
            "historically_positive": False,
            "detail": "在当前成本和风控假设下，历史单笔净期望不为正或盈利因子不高于 1。",
        }
    return {
        "status": "historically_positive",
        "label": "历史成本后期望为正",
        "historically_positive": True,
        "detail": "该结论仅针对当前标的、参数和历史样本，不是买入信号或收益承诺。",
    }


def simulate_long_execution(
    df: pd.DataFrame,
    scores: dict[int, float],
    *,
    horizon: int = 20,
    entry_score: float = 65,
    stop_atr: float = 2.0,
    target_atr: float = 3.0,
    commission_bps: float = 5.0,
    slippage_bps: float = 5.0,
    sell_tax_bps: float = 0.0,
    risk_per_trade_pct: float = 1.0,
    max_position_pct: float = 30.0,
) -> dict:
    """Simulate non-overlapping long trades from close-of-day scores.

    The signal is only known after the signal day's close, so entry always uses
    the next available daily open. When a daily bar touches both stop and target,
    the stop is assumed first because OHLC cannot reveal the intraday path.
    """
    _validate_execution_inputs(
        horizon=horizon,
        entry_score=entry_score,
        stop_atr=stop_atr,
        target_atr=target_atr,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        sell_tax_bps=sell_tax_bps,
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
    )
    required = {"date", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"执行回测缺少字段:{', '.join(sorted(missing))}")

    buy_friction = (float(commission_bps) + float(slippage_bps)) / 10_000
    sell_friction = (
        float(commission_bps) + float(slippage_bps) + float(sell_tax_bps)
    ) / 10_000
    trades: list[dict] = []
    ambiguous_bars = 0
    skipped_invalid = 0
    index = min(scores) if scores else 0
    row_count = len(df)

    while index < row_count - 1:
        score = _finite(scores.get(index))
        if score is None or score < float(entry_score):
            index += 1
            continue

        signal_row = df.iloc[index]
        entry_index = index + 1
        entry_row = df.iloc[entry_index]
        signal_close = _finite(signal_row.get("close"))
        atr_value = _finite(signal_row.get("atr"))
        if atr_value is None:
            atr_pct = _finite(signal_row.get("atr_pct"))
            if atr_pct is not None and signal_close:
                atr_value = signal_close * atr_pct / 100
        entry_price = _finite(entry_row.get("open"))
        if not entry_price or not atr_value or atr_value <= 0:
            skipped_invalid += 1
            index += 1
            continue

        stop_price = entry_price - float(stop_atr) * atr_value
        target_price = entry_price + float(target_atr) * atr_value
        if stop_price <= 0 or target_price <= entry_price:
            skipped_invalid += 1
            index += 1
            continue

        planned_entry_cost = entry_price * (1 + buy_friction)
        planned_stop_proceeds = stop_price * (1 - sell_friction)
        planned_loss_pct = max(
            0.000001, (planned_entry_cost - planned_stop_proceeds) / planned_entry_cost * 100
        )
        position_pct = min(
            float(max_position_pct),
            float(risk_per_trade_pct) / planned_loss_pct * 100,
        )

        end_index = min(row_count - 1, entry_index + int(horizon) - 1)
        exit_index = end_index
        exit_price = _finite(df.iloc[end_index].get("close"))
        exit_reason = "time_exit"
        ambiguous = False
        observed_high = entry_price
        observed_low = entry_price

        for cursor in range(entry_index, end_index + 1):
            row = df.iloc[cursor]
            day_open = _finite(row.get("open"))
            day_high = _finite(row.get("high"))
            day_low = _finite(row.get("low"))
            if not day_open or day_high is None or day_low is None:
                continue
            observed_high = max(observed_high, day_high)
            observed_low = min(observed_low, day_low)

            # Only later sessions can gap through a level that was set at entry.
            if cursor > entry_index and day_open <= stop_price:
                exit_index, exit_price, exit_reason = cursor, day_open, "gap_stop"
                break
            if cursor > entry_index and day_open >= target_price:
                exit_index, exit_price, exit_reason = cursor, day_open, "gap_target"
                break

            hit_stop = day_low <= stop_price
            hit_target = day_high >= target_price
            if hit_stop and hit_target:
                ambiguous = True
                ambiguous_bars += 1
                exit_index, exit_price, exit_reason = cursor, stop_price, "stop_first_ambiguous"
                break
            if hit_stop:
                exit_index, exit_price, exit_reason = cursor, stop_price, "stop"
                break
            if hit_target:
                exit_index, exit_price, exit_reason = cursor, target_price, "target"
                break

        if not exit_price or exit_price <= 0:
            skipped_invalid += 1
            index += 1
            continue

        effective_entry = entry_price * (1 + buy_friction)
        effective_exit = exit_price * (1 - sell_friction)
        gross_return_pct = (exit_price / entry_price - 1) * 100
        net_return_pct = (effective_exit / effective_entry - 1) * 100
        account_return_pct = net_return_pct * position_pct / 100
        trade = {
            "signal_date": str(signal_row["date"])[:10],
            "entry_date": str(entry_row["date"])[:10],
            "exit_date": str(df.iloc[exit_index]["date"])[:10],
            "signal_score": round(score, 1),
            "entry_price": round(entry_price, 4),
            "stop_price": round(stop_price, 4),
            "target_price": round(target_price, 4),
            "exit_price": round(exit_price, 4),
            "exit_reason": exit_reason,
            "holding_days": int(exit_index - entry_index + 1),
            "gross_return_pct": round(gross_return_pct, 3),
            "net_return_pct": round(net_return_pct, 3),
            "cost_drag_pct": round(gross_return_pct - net_return_pct, 3),
            "planned_loss_pct": round(planned_loss_pct, 3),
            "position_pct": round(position_pct, 2),
            "account_return_pct": round(account_return_pct, 3),
            "mfe_pct": round((observed_high / entry_price - 1) * 100, 3),
            "mae_pct": round((observed_low / entry_price - 1) * 100, 3),
            "risk_budget_breached": account_return_pct < -float(risk_per_trade_pct) - 0.01,
            "same_bar_path_ambiguous": ambiguous,
        }
        trades.append(trade)

        # A signal observed at the exit close may enter on the following open.
        index = exit_index

    net_returns = [float(item["net_return_pct"]) for item in trades]
    gross_returns = [float(item["gross_return_pct"]) for item in trades]
    account_returns = [float(item["account_return_pct"]) for item in trades]
    winners = [value for value in net_returns if value > 0]
    losers = [value for value in net_returns if value < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    payoff_ratio = (
        float(np.mean(winners)) / abs(float(np.mean(losers)))
        if winners and losers
        else None
    )

    equity = 100.0
    peak = 100.0
    max_drawdown = 0.0
    equity_curve = []
    for trade, account_return in zip(trades, account_returns):
        equity *= max(0.0, 1 + account_return / 100)
        peak = max(peak, equity)
        drawdown = (equity / peak - 1) * 100 if peak else -100.0
        max_drawdown = min(max_drawdown, drawdown)
        equity_curve.append(
            {
                "date": trade["exit_date"],
                "equity": round(equity, 4),
                "drawdown_pct": round(drawdown, 3),
            }
        )

    trade_count = len(trades)
    expectancy = float(np.mean(net_returns)) if net_returns else 0.0
    exit_reasons = {
        "target": sum(item["exit_reason"] in {"target", "gap_target"} for item in trades),
        "stop": sum(item["exit_reason"] in {"stop", "gap_stop", "stop_first_ambiguous"} for item in trades),
        "time": sum(item["exit_reason"] == "time_exit" for item in trades),
    }
    risk_breaches = sum(bool(item["risk_budget_breached"]) for item in trades)
    research_gate = _research_gate(trade_count, expectancy, profit_factor)

    warnings = [
        "仅模拟多头交易；看跌信号不会被当作可做空许可。",
        "佣金、滑点和卖出税费是用户场景假设，不代表任何券商的实际收费。",
        "未模拟停牌、涨跌停排队、整手限制、分红拆股、汇率、融资成本和市场冲击。",
        "单组执行结果不能单独证明策略稳健；必须同时查看参数邻域、后段时间、成本和分阶段压力结果。",
    ]
    if ambiguous_bars:
        warnings.append(
            f"{ambiguous_bars} 笔交易的同一日 K 线同时触及止损和止盈，已保守按止损先发生处理。"
        )
    if risk_breaches:
        warnings.append(
            f"{risk_breaches} 笔交易因跳空或成本超过单笔风险预算，证明止损不能保证亏损上限。"
        )

    return {
        "policy_version": EXECUTION_POLICY_VERSION,
        "mode": "next_open_non_overlapping_long_only",
        "assumptions": {
            "entry_timing": "next_trading_day_open",
            "entry_score": float(entry_score),
            "max_holding_days": int(horizon),
            "stop_atr": float(stop_atr),
            "target_atr": float(target_atr),
            "reward_risk_atr_ratio": round(float(target_atr) / float(stop_atr), 3),
            "commission_bps_per_side": float(commission_bps),
            "slippage_bps_per_side": float(slippage_bps),
            "sell_tax_bps": float(sell_tax_bps),
            "risk_per_trade_pct": float(risk_per_trade_pct),
            "max_position_pct": float(max_position_pct),
        },
        "trade_count": trade_count,
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": _rounded(len(winners) / trade_count * 100 if trade_count else None, 1),
        "gross_expectancy_pct": _rounded(np.mean(gross_returns) if gross_returns else None, 3),
        "net_expectancy_pct": _rounded(np.mean(net_returns) if net_returns else None, 3),
        "median_net_return_pct": _rounded(np.median(net_returns) if net_returns else None, 3),
        "profit_factor": _rounded(profit_factor, 3),
        "payoff_ratio": _rounded(payoff_ratio, 3),
        "average_cost_drag_pct": _rounded(
            np.mean([item["cost_drag_pct"] for item in trades]) if trades else None, 3
        ),
        "strategy_return_pct": round(equity - 100, 3),
        "max_drawdown_pct": round(max_drawdown, 3),
        "average_holding_days": _rounded(
            np.mean([item["holding_days"] for item in trades]) if trades else None, 1
        ),
        "average_position_pct": _rounded(
            np.mean([item["position_pct"] for item in trades]) if trades else None, 2
        ),
        "risk_budget_breach_count": risk_breaches,
        "same_bar_ambiguous_count": ambiguous_bars,
        "skipped_invalid_count": skipped_invalid,
        "exit_reasons": exit_reasons,
        "research_gate": research_gate,
        "equity_curve": equity_curve,
        "trades": trades[-30:],
        "trade_history_truncated": trade_count > 30,
        "warnings": warnings,
    }


def _execution_snapshot(result: dict, *, min_trades: int) -> dict:
    """Return the compact, comparable part of one execution simulation."""
    trade_count = int(result.get("trade_count") or 0)
    expectancy = _finite(result.get("net_expectancy_pct"))
    profit_factor = _finite(result.get("profit_factor"))
    evaluable = trade_count >= int(min_trades)
    historically_positive = bool(
        evaluable
        and expectancy is not None
        and expectancy > 0
        and (profit_factor is None or profit_factor > 1)
    )
    return {
        "trade_count": trade_count,
        "win_rate": _rounded(result.get("win_rate"), 1),
        "net_expectancy_pct": _rounded(expectancy, 3),
        "profit_factor": _rounded(profit_factor, 3),
        "strategy_return_pct": _rounded(result.get("strategy_return_pct"), 3),
        "max_drawdown_pct": _rounded(result.get("max_drawdown_pct"), 3),
        "evaluable": evaluable,
        "historically_positive": historically_positive,
        "minimum_trades": int(min_trades),
    }


def _score_window(
    scores: dict[int, float],
    *,
    start_index: int,
    end_index: int,
    horizon: int,
) -> dict[int, float]:
    """Keep only signals whose full maximum holding period stays in [start, end)."""
    latest_signal_index = int(end_index) - int(horizon) - 1
    if latest_signal_index < int(start_index):
        return {}
    return {
        int(index): float(score)
        for index, score in scores.items()
        if int(start_index) <= int(index) <= latest_signal_index
    }


def _date_text(df: pd.DataFrame, index: int) -> str:
    return str(df.iloc[int(index)]["date"])[:10]


def _simulate_window(
    df: pd.DataFrame,
    scores: dict[int, float],
    *,
    window_id: str,
    label: str,
    start_index: int,
    end_index: int,
    horizon: int,
    min_trades: int,
    execution_kwargs: dict,
) -> dict:
    window_scores = _score_window(
        scores,
        start_index=start_index,
        end_index=end_index,
        horizon=horizon,
    )
    result = simulate_long_execution(df, window_scores, horizon=horizon, **execution_kwargs)
    return {
        "id": window_id,
        "label": label,
        "date_range": [_date_text(df, start_index), _date_text(df, end_index - 1)],
        "eligible_signal_count": len(window_scores),
        **_execution_snapshot(result, min_trades=min_trades),
    }


def _grid_values(
    center: float,
    offsets: tuple[float, ...],
    *,
    lower: float,
    upper: float,
) -> list[float]:
    values = {
        round(min(upper, max(lower, float(center) + float(offset))), 6)
        for offset in offsets
    }
    return sorted(values)


def _stress_cost(value: float, upper: float) -> float:
    """Raise a cost assumption while respecting the public API bounds."""
    return round(min(float(upper), max(float(value) * 2, float(value) + 5)), 2)


def _robustness_gate(
    baseline_execution: dict,
    parameter_summary: dict,
    holdout: dict,
    cost_stress: dict,
    time_consistency: dict,
) -> dict:
    baseline_positive = bool(
        baseline_execution.get("research_gate", {}).get("historically_positive")
    )
    if not baseline_positive:
        return {
            "status": "baseline_not_positive",
            "label": "基线尚未通过",
            "historically_robust": False,
            "detail": "当前单组参数在完整历史样本上的成本后净期望尚未通过，稳健性检查不应覆盖这一否决结果。",
        }

    evidence_gaps = []
    if parameter_summary["evaluable_count"] < parameter_summary["minimum_evaluable_count"]:
        evidence_gaps.append(
            f"参数邻域仅 {parameter_summary['evaluable_count']}/{parameter_summary['scenario_count']} 组达到样本门槛"
        )
    if holdout["trade_count"] < HOLDOUT_MIN_TRADES:
        evidence_gaps.append(
            f"较晚时间段只有 {holdout['trade_count']} 笔交易（至少需 {HOLDOUT_MIN_TRADES} 笔）"
        )
    if cost_stress["trade_count"] < PARAMETER_MIN_TRADES:
        evidence_gaps.append(
            f"高成本场景只有 {cost_stress['trade_count']} 笔交易（至少需 {PARAMETER_MIN_TRADES} 笔）"
        )
    if time_consistency["evaluable_count"] < MIN_EVALUABLE_PERIODS:
        evidence_gaps.append(
            f"仅 {time_consistency['evaluable_count']}/4 个时间段达到样本门槛"
        )
    if evidence_gaps:
        return {
            "status": "insufficient_evidence",
            "label": "稳健性证据不足",
            "historically_robust": False,
            "detail": "；".join(evidence_gaps) + "。扩大历史区间后再判断，不应把缺少样本视作通过。",
        }

    if not holdout["historically_positive"]:
        return {
            "status": "chronological_holdout_failed",
            "label": "后段时间未通过",
            "historically_robust": False,
            "detail": "同一组参数在较晚 40% 历史时间段的成本后净期望或盈利因子未通过，存在时间失效风险。",
        }
    if not cost_stress["historically_positive"]:
        return {
            "status": "cost_stress_failed",
            "label": "高成本压力未通过",
            "historically_robust": False,
            "detail": "提高佣金、滑点和卖出税费后，历史净期望或盈利因子未通过，策略对成交成本较敏感。",
        }
    if (parameter_summary.get("positive_rate_pct") or 0) < MIN_PARAMETER_PASS_RATE_PCT:
        return {
            "status": "parameter_fragile",
            "label": "参数邻域脆弱",
            "historically_robust": False,
            "detail": f"只有 {parameter_summary.get('positive_rate_pct') or 0:.1f}% 的可评估邻近参数保持历史正期望，低于 {MIN_PARAMETER_PASS_RATE_PCT:.0f}% 门槛。",
        }
    if (time_consistency.get("positive_rate_pct") or 0) < MIN_PERIOD_PASS_RATE_PCT:
        return {
            "status": "time_inconsistent",
            "label": "分阶段表现不一致",
            "historically_robust": False,
            "detail": f"只有 {time_consistency.get('positive_rate_pct') or 0:.1f}% 的可评估时间段保持历史正期望，低于 {MIN_PERIOD_PASS_RATE_PCT:.0f}% 门槛。",
        }
    return {
        "status": "historically_robust",
        "label": "通过历史稳健性门槛",
        "historically_robust": True,
        "detail": "基线、较晚时间段、邻近参数、高成本和分阶段检查均达到预设历史门槛；这仍不是买入指令或未来收益承诺。",
    }


def build_execution_robustness(
    df: pd.DataFrame,
    scores: dict[int, float],
    baseline_execution: dict,
    *,
    horizon: int,
    entry_score: float,
    stop_atr: float,
    target_atr: float,
    commission_bps: float,
    slippage_bps: float,
    sell_tax_bps: float,
    risk_per_trade_pct: float,
    max_position_pct: float,
) -> dict:
    """Stress one user-selected strategy without selecting a historical winner."""
    row_count = len(df)
    score_start = min(scores) if scores else 60
    complete_scores = _score_window(
        scores,
        start_index=score_start,
        end_index=row_count,
        horizon=horizon,
    )
    execution_kwargs = {
        "entry_score": float(entry_score),
        "stop_atr": float(stop_atr),
        "target_atr": float(target_atr),
        "commission_bps": float(commission_bps),
        "slippage_bps": float(slippage_bps),
        "sell_tax_bps": float(sell_tax_bps),
        "risk_per_trade_pct": float(risk_per_trade_pct),
        "max_position_pct": float(max_position_pct),
    }

    entry_values = _grid_values(entry_score, (-5, 0, 5), lower=50, upper=90)
    stop_values = _grid_values(stop_atr, (-0.5, 0, 0.5), lower=0.5, upper=6)
    target_values = _grid_values(target_atr, (-0.5, 0, 0.5), lower=0.5, upper=12)
    parameter_scenarios = []
    for scenario_entry in entry_values:
        for scenario_stop in stop_values:
            for scenario_target in target_values:
                is_baseline = bool(
                    abs(scenario_entry - float(entry_score)) < 1e-9
                    and abs(scenario_stop - float(stop_atr)) < 1e-9
                    and abs(scenario_target - float(target_atr)) < 1e-9
                )
                scenario_result = baseline_execution if is_baseline else simulate_long_execution(
                    df,
                    complete_scores,
                    horizon=horizon,
                    entry_score=scenario_entry,
                    stop_atr=scenario_stop,
                    target_atr=scenario_target,
                    commission_bps=commission_bps,
                    slippage_bps=slippage_bps,
                    sell_tax_bps=sell_tax_bps,
                    risk_per_trade_pct=risk_per_trade_pct,
                    max_position_pct=max_position_pct,
                )
                parameter_scenarios.append({
                    "entry_score": scenario_entry,
                    "stop_atr": scenario_stop,
                    "target_atr": scenario_target,
                    "is_baseline": is_baseline,
                    **_execution_snapshot(scenario_result, min_trades=PARAMETER_MIN_TRADES),
                })

    evaluable_scenarios = [item for item in parameter_scenarios if item["evaluable"]]
    positive_scenarios = [item for item in evaluable_scenarios if item["historically_positive"]]
    evaluable_expectancies = [
        item["net_expectancy_pct"]
        for item in evaluable_scenarios
        if item["net_expectancy_pct"] is not None
    ]
    scenario_count = len(parameter_scenarios)
    minimum_evaluable_count = int(np.ceil(scenario_count * MIN_PARAMETER_COVERAGE_PCT / 100))
    parameter_summary = {
        "scenario_count": scenario_count,
        "evaluable_count": len(evaluable_scenarios),
        "minimum_evaluable_count": minimum_evaluable_count,
        "coverage_pct": _rounded(len(evaluable_scenarios) / scenario_count * 100, 1),
        "positive_count": len(positive_scenarios),
        "positive_rate_pct": _rounded(
            len(positive_scenarios) / len(evaluable_scenarios) * 100
            if evaluable_scenarios else None,
            1,
        ),
        "net_expectancy_distribution_pct": {
            "minimum": _rounded(min(evaluable_expectancies), 3) if evaluable_expectancies else None,
            "median": _rounded(np.median(evaluable_expectancies), 3) if evaluable_expectancies else None,
            "maximum": _rounded(max(evaluable_expectancies), 3) if evaluable_expectancies else None,
        },
    }

    split_index = score_start + int((row_count - score_start) * 0.60)
    development = _simulate_window(
        df,
        scores,
        window_id="development",
        label="较早 60%（开发段）",
        start_index=score_start,
        end_index=split_index,
        horizon=horizon,
        min_trades=HOLDOUT_MIN_TRADES,
        execution_kwargs=execution_kwargs,
    )
    holdout = _simulate_window(
        df,
        scores,
        window_id="later_holdout",
        label="较晚 40%（时间留出段）",
        start_index=split_index,
        end_index=row_count,
        horizon=horizon,
        min_trades=HOLDOUT_MIN_TRADES,
        execution_kwargs=execution_kwargs,
    )

    boundaries = np.linspace(score_start, row_count, 5, dtype=int).tolist()
    periods = []
    for index in range(4):
        periods.append(_simulate_window(
            df,
            scores,
            window_id=f"period_{index + 1}",
            label=f"时间段 {index + 1}",
            start_index=boundaries[index],
            end_index=boundaries[index + 1],
            horizon=horizon,
            min_trades=PERIOD_MIN_TRADES,
            execution_kwargs=execution_kwargs,
        ))
    evaluable_periods = [item for item in periods if item["evaluable"]]
    positive_periods = [item for item in evaluable_periods if item["historically_positive"]]
    time_consistency = {
        "period_count": 4,
        "evaluable_count": len(evaluable_periods),
        "positive_count": len(positive_periods),
        "positive_rate_pct": _rounded(
            len(positive_periods) / len(evaluable_periods) * 100
            if evaluable_periods else None,
            1,
        ),
        "periods": periods,
    }

    stressed_costs = {
        "commission_bps_per_side": _stress_cost(commission_bps, 100),
        "slippage_bps_per_side": _stress_cost(slippage_bps, 100),
        "sell_tax_bps": _stress_cost(sell_tax_bps, 200),
    }
    stressed_result = simulate_long_execution(
        df,
        complete_scores,
        horizon=horizon,
        entry_score=entry_score,
        stop_atr=stop_atr,
        target_atr=target_atr,
        commission_bps=stressed_costs["commission_bps_per_side"],
        slippage_bps=stressed_costs["slippage_bps_per_side"],
        sell_tax_bps=stressed_costs["sell_tax_bps"],
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
    )
    cost_stress = {
        "assumptions": stressed_costs,
        **_execution_snapshot(stressed_result, min_trades=PARAMETER_MIN_TRADES),
    }

    gate = _robustness_gate(
        baseline_execution,
        parameter_summary,
        holdout,
        cost_stress,
        time_consistency,
    )
    return {
        "policy_version": ROBUSTNESS_POLICY_VERSION,
        "gate": gate,
        "criteria": {
            "parameter_min_trades": PARAMETER_MIN_TRADES,
            "parameter_min_coverage_pct": MIN_PARAMETER_COVERAGE_PCT,
            "parameter_min_positive_rate_pct": MIN_PARAMETER_PASS_RATE_PCT,
            "holdout_min_trades": HOLDOUT_MIN_TRADES,
            "period_min_trades": PERIOD_MIN_TRADES,
            "minimum_evaluable_periods": MIN_EVALUABLE_PERIODS,
            "period_min_positive_rate_pct": MIN_PERIOD_PASS_RATE_PCT,
        },
        "parameter_neighborhood": {
            "axes": {
                "entry_score": entry_values,
                "stop_atr": stop_values,
                "target_atr": target_values,
            },
            "summary": parameter_summary,
            "scenarios": parameter_scenarios,
        },
        "chronological_holdout": {
            "method": "fixed_parameter_60_40_chronological_holdout",
            "split_date": _date_text(df, split_index),
            "development": development,
            "holdout": holdout,
        },
        "cost_stress": cost_stress,
        "time_consistency": time_consistency,
        "warnings": [
            "时间留出段使用的是当前用户参数；若反复查看结果后继续调参，该后段也会被人为污染，不能再视为真正未见样本。",
            "参数邻域只检查入场分数 ±5、止损 ATR ±0.5、止盈 ATR ±0.5，不代表穷举了所有策略，也不挑选历史最佳组合。",
            "当前没有进行多重检验校正、幸存者偏差校正或按牛熊/流动性状态重新训练。",
            "所有交易均要求在各自时间窗口内走完最长持有期，避免跨越分割边界或被数据末端提前截断。",
            "通过历史稳健性门槛只说明这组假设较不容易被简单压力击穿，不代表未来会盈利。",
        ],
    }


def backtest(
    df: pd.DataFrame,
    horizon: int = 20,
    *,
    entry_score: float = 65,
    stop_atr: float = 2.0,
    target_atr: float = 3.0,
    commission_bps: float = 5.0,
    slippage_bps: float = 5.0,
    sell_tax_bps: float = 0.0,
    risk_per_trade_pct: float = 1.0,
    max_position_pct: float = 30.0,
) -> dict:
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
    scores: dict[int, float] = {}
    for i in range(60, n):
        points, _ = analysis._evaluate(df.iloc[i], df.iloc[i - 1])
        score = float(np.clip(points, 0, 100))
        scores[i] = score
        if i >= n - horizon:
            continue
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

    complete_execution_scores = _score_window(
        scores,
        start_index=60,
        end_index=n,
        horizon=horizon,
    )
    execution = simulate_long_execution(
        df,
        complete_execution_scores,
        horizon=horizon,
        entry_score=entry_score,
        stop_atr=stop_atr,
        target_atr=target_atr,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        sell_tax_bps=sell_tax_bps,
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
    )
    robustness = build_execution_robustness(
        df,
        scores,
        execution,
        horizon=horizon,
        entry_score=entry_score,
        stop_atr=stop_atr,
        target_atr=target_atr,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        sell_tax_bps=sell_tax_bps,
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
    )

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
        "execution": execution,
        "robustness": robustness,
        "methodology": {
            "direction_samples_overlap": True,
            "execution_trades_overlap": False,
            "signal_known_at_close": True,
            "execution_entry": "next_trading_day_open",
            "execution_requires_full_horizon": True,
            "robustness_selection": "fixed_user_parameters_no_best_scenario_selection",
        },
    }
