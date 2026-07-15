# -*- coding: utf-8 -*-
"""Deterministic post-trade attribution for real batch fund purchases."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


STRATEGY_ID = "portfolio_batch_purchase_attribution"
STRATEGY_VERSION = "1.0.0"
SCHEMA_VERSION = f"{STRATEGY_ID}.v1"
NAV_MATCH_TOLERANCE_RATIO = 0.002
NAV_MATCH_TOLERANCE_ABSOLUTE = 0.0001
NAV_MAX_AGE_DAYS = 10
AGGREGATE_AS_OF_MAX_SKEW_DAYS = 7
DECISION_REVIEW_MIN_DAYS = 30
_EPSILON = 1e-8


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _nav_matches(recorded: float | None, provider: float | None) -> bool:
    if recorded is None or provider is None or recorded <= 0 or provider <= 0:
        return False
    return abs(recorded - provider) <= max(
        NAV_MATCH_TOLERANCE_ABSOLUTE,
        abs(provider) * NAV_MATCH_TOLERANCE_RATIO,
    )


def _drawdown(points: list[tuple[str, float]]) -> dict[str, Any]:
    if not points:
        return {
            "max_drawdown_pct": None,
            "current_drawdown_pct": None,
            "peak_date": None,
            "trough_date": None,
        }
    peak_value = points[0][1]
    peak_date = points[0][0]
    max_drawdown = 0.0
    max_peak_date = peak_date
    trough_date = peak_date
    for date_value, nav in points:
        if nav > peak_value:
            peak_value = nav
            peak_date = date_value
        drawdown = nav / peak_value - 1 if peak_value > 0 else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_peak_date = peak_date
            trough_date = date_value
    latest_nav = points[-1][1]
    current_drawdown = latest_nav / peak_value - 1 if peak_value > 0 else 0.0
    return {
        "max_drawdown_pct": _round(max_drawdown * 100, 4),
        "current_drawdown_pct": _round(current_drawdown * 100, 4),
        "peak_date": max_peak_date,
        "trough_date": trough_date,
    }


def _corporate_actions(
    dataset: dict[str, Any] | None,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    dividends = [
        {"type": "dividend", **item}
        for item in ((dataset or {}).get("dividends") or [])
        if start <= str(item.get("ex_dividend_date") or "") <= end
    ]
    splits = [
        {"type": "split", **item}
        for item in ((dataset or {}).get("splits") or [])
        if start <= str(item.get("date") or "") <= end
    ]
    return sorted(
        dividends + splits,
        key=lambda item: str(item.get("ex_dividend_date") or item.get("date") or ""),
    )


def evaluate_batch_purchase_attribution(
    purchase_payload: dict[str, Any],
    lot_snapshots: dict[int, dict[str, Any]],
    nav_histories: dict[str, dict[str, Any]],
    distributions: dict[str, dict[str, Any]],
    source_errors: list[dict[str, str]],
    *,
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Attribute only executed lots; never infer a return for an unexecuted order."""
    generated = _datetime(generated_at)
    if generated is None:
        raise ValueError("收益归因时间必须包含时区")
    generated_date = generated.astimezone(dt.timezone.utc).date()
    purchased = [
        item for item in purchase_payload.get("outcomes") or []
        if item.get("resolution") == "purchased" and item.get("transaction")
    ]
    if not purchased:
        raise ValueError("本批次没有可归因的真实申购成交")

    errors_by_code: dict[str, list[dict[str, str]]] = {}
    for error in source_errors:
        errors_by_code.setdefault(str(error.get("code") or ""), []).append(error)

    items: list[dict[str, Any]] = []
    for outcome in purchased:
        transaction = outcome.get("transaction") or {}
        transaction_id = int(transaction.get("id") or 0)
        code = str(outcome.get("code") or transaction.get("code") or "")
        lot_snapshot = lot_snapshots.get(transaction_id) or {}
        lot = lot_snapshot.get("lot") or {}
        holding_reconciliation = lot_snapshot.get("holding_reconciliation") or {}
        history = nav_histories.get(code)
        distribution = distributions.get(code)
        reasons: list[str] = []
        source_failures = errors_by_code.get(code) or []
        if source_failures:
            reasons.append("真实确认净值或分红拆分数据源不可用")
        if not lot.get("complete"):
            reasons.append("该笔买入流水无法在当前 FIFO 账本中完整追踪")
        if lot_snapshot.get("integrity_issues"):
            reasons.append("该基金交易账本存在卖出超录或方向异常")
        if not holding_reconciliation.get("shares_match"):
            reasons.append("当前确认持仓份额与该基金 FIFO 未平仓份额不一致")

        point_map: dict[str, float] = {}
        for point in (history or {}).get("points") or []:
            point_date = _date(point.get("date"))
            nav = _number(point.get("unit_nav"))
            if point_date is None or nav is None or nav <= 0 or point_date > generated_date:
                continue
            point_map[point_date.isoformat()] = nav
        trade_date = str(transaction.get("trade_date") or "")
        trade_day = _date(trade_date)
        if trade_day is None:
            reasons.append("真实买入流水缺少有效确认日期")
        provider_trade_nav = point_map.get(trade_date)
        entry_nav_verified = _nav_matches(
            _number(transaction.get("unit_price")),
            provider_trade_nav,
        )
        if not entry_nav_verified:
            reasons.append("买入流水确认净值与数据源同日单位净值不一致")

        realized_matches = lot.get("realized_matches") or []
        relevant_transactions = {
            int(item.get("id") or 0): item
            for item in lot_snapshot.get("relevant_transactions") or []
        }
        sale_nav_verified = True
        for sale_transaction_id in sorted({
            int(item.get("sale_transaction_id") or 0) for item in realized_matches
        }):
            sale = relevant_transactions.get(sale_transaction_id)
            sale_date = str((sale or {}).get("trade_date") or "")
            if not _nav_matches(
                _number((sale or {}).get("unit_price")),
                point_map.get(sale_date),
            ):
                sale_nav_verified = False
                break
        if realized_matches and not sale_nav_verified:
            reasons.append("后续卖出流水确认净值与数据源同日单位净值不一致")

        remaining_shares = _number(lot.get("remaining_shares")) or 0.0
        sale_dates = sorted(
            str(item.get("sale_date") or "")
            for item in realized_matches
            if item.get("sale_date")
        )
        latest_nav_date = max(point_map) if point_map else ""
        as_of = latest_nav_date if remaining_shares > _EPSILON else (
            sale_dates[-1] if sale_dates else trade_date
        )
        as_of_day = _date(as_of)
        if remaining_shares > _EPSILON:
            if not latest_nav_date:
                reasons.append("没有可用于当前剩余份额估值的真实确认净值")
            elif (generated_date - (_date(latest_nav_date) or generated_date)).days > NAV_MAX_AGE_DAYS:
                reasons.append(f"最新确认净值已超过 {NAV_MAX_AGE_DAYS} 天未更新")
        if as_of and as_of not in point_map:
            reasons.append("归因截止日没有对应的真实确认净值")

        actions = _corporate_actions(distribution, trade_date, as_of) if trade_date and as_of else []
        if actions:
            reasons.append("持有区间存在未进入交易账本的分红或拆分，单位净值不能直接用于完整收益归因")
        observation_days = (
            max(0, (as_of_day - trade_day).days)
            if as_of_day is not None and trade_day is not None else None
        )
        path_points = sorted(
            (date_value, nav)
            for date_value, nav in point_map.items()
            if trade_date <= date_value <= as_of
        ) if trade_date and as_of else []
        risk = _drawdown(path_points)

        status = "available" if not reasons else "unavailable"
        metrics: dict[str, Any] = {}
        if status == "available":
            original_cost = _number(lot.get("original_cost_yuan")) or 0.0
            realized_proceeds = _number(lot.get("realized_proceeds_yuan")) or 0.0
            realized_profit = _number(lot.get("realized_profit_yuan")) or 0.0
            remaining_cost = _number(lot.get("remaining_cost_yuan")) or 0.0
            current_nav = point_map.get(as_of) or 0.0
            current_value = remaining_shares * current_nav
            unrealized_profit = current_value - remaining_cost
            ending_value = realized_proceeds + current_value
            total_profit = ending_value - original_cost
            metrics = {
                "as_of": as_of,
                "observation_days": observation_days,
                "original_cost_yuan": _round(original_cost),
                "realized_proceeds_yuan": _round(realized_proceeds),
                "current_remaining_value_yuan": _round(current_value),
                "ending_value_yuan": _round(ending_value),
                "realized_profit_yuan": _round(realized_profit),
                "unrealized_profit_yuan": _round(unrealized_profit),
                "total_profit_yuan": _round(total_profit),
                "total_return_pct": _round(
                    total_profit / original_cost * 100 if original_cost > _EPSILON else None,
                    4,
                ),
                "current_unit_nav": _round(current_nav, 8),
                "result_class": (
                    "positive" if total_profit > 0.01
                    else "negative" if total_profit < -0.01
                    else "flat"
                ),
                **risk,
            }

        review_eligible = bool(
            status == "available"
            and observation_days is not None
            and observation_days >= DECISION_REVIEW_MIN_DAYS
        )
        days_until_review = (
            max(0, DECISION_REVIEW_MIN_DAYS - observation_days)
            if observation_days is not None else None
        )
        items.append({
            "code": code,
            "name": outcome.get("name") or transaction.get("name") or code,
            "transaction_id": transaction_id,
            "status": status,
            "trade_date": trade_date,
            "as_of": as_of or None,
            "lot": {
                "original_shares": lot.get("original_shares"),
                "realized_shares": lot.get("realized_shares"),
                "remaining_shares": lot.get("remaining_shares"),
                "complete": bool(lot.get("complete")),
            },
            "holding_reconciliation": holding_reconciliation,
            "metrics": metrics,
            "risk": risk,
            "reasons": reasons,
            "source_errors": source_failures,
            "entry_nav_verified": entry_nav_verified,
            "sale_nav_verified": sale_nav_verified,
            "corporate_actions": actions,
            "decision_review": {
                "eligible": review_eligible,
                "minimum_observation_days": DECISION_REVIEW_MIN_DAYS,
                "days_until_review": days_until_review,
                "reason": (
                    "minimum_observation_window_reached"
                    if review_eligible else "observation_window_or_evidence_incomplete"
                ),
            },
            "sources": {
                "nav": (history or {}).get("source"),
                "nav_source_url": (history or {}).get("source_url"),
                "distribution": (distribution or {}).get("source"),
            },
        })

    available_items = [item for item in items if item.get("status") == "available"]
    if len(available_items) == len(items):
        status = "available"
    elif available_items:
        status = "partial"
    else:
        status = "unavailable"
    aggregate_blockers: list[str] = []
    as_of_days = sorted(
        day for item in available_items if (day := _date(item.get("as_of"))) is not None
    )
    as_of_skew_days = (as_of_days[-1] - as_of_days[0]).days if as_of_days else None
    if as_of_skew_days is not None and as_of_skew_days > AGGREGATE_AS_OF_MAX_SKEW_DAYS:
        aggregate_blockers.append(
            f"各基金归因截止日相差超过 {AGGREGATE_AS_OF_MAX_SKEW_DAYS} 天，不能合并为同一时点收益"
        )
        status = "partial"
    if len(available_items) != len(items):
        aggregate_blockers.append("部分真实成交缺少完整净值、分红或账本证据")

    aggregate_metrics: dict[str, Any] = {}
    if status == "available" and not aggregate_blockers:
        original_cost = sum(
            _number((item.get("metrics") or {}).get("original_cost_yuan")) or 0.0
            for item in available_items
        )
        realized_proceeds = sum(
            _number((item.get("metrics") or {}).get("realized_proceeds_yuan")) or 0.0
            for item in available_items
        )
        current_value = sum(
            _number((item.get("metrics") or {}).get("current_remaining_value_yuan")) or 0.0
            for item in available_items
        )
        ending_value = realized_proceeds + current_value
        total_profit = ending_value - original_cost
        aggregate_metrics = {
            "as_of_start": as_of_days[0].isoformat() if as_of_days else None,
            "as_of_end": as_of_days[-1].isoformat() if as_of_days else None,
            "as_of_skew_days": as_of_skew_days,
            "original_cost_yuan": _round(original_cost),
            "realized_proceeds_yuan": _round(realized_proceeds),
            "current_remaining_value_yuan": _round(current_value),
            "ending_value_yuan": _round(ending_value),
            "total_profit_yuan": _round(total_profit),
            "total_return_pct": _round(
                total_profit / original_cost * 100 if original_cost > _EPSILON else None,
                4,
            ),
            "positive_count": sum(
                (item.get("metrics") or {}).get("result_class") == "positive"
                for item in available_items
            ),
            "negative_count": sum(
                (item.get("metrics") or {}).get("result_class") == "negative"
                for item in available_items
            ),
            "flat_count": sum(
                (item.get("metrics") or {}).get("result_class") == "flat"
                for item in available_items
            ),
        }

    decision_review_eligible = bool(
        status == "available"
        and items
        and all((item.get("decision_review") or {}).get("eligible") for item in items)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": generated.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "bindings": bindings,
        "coverage": {
            "purchased_fund_count": len(items),
            "available_fund_count": len(available_items),
            "unavailable_fund_count": len(items) - len(available_items),
            "source_error_count": len(source_errors),
        },
        "aggregate": {
            "status": "available" if aggregate_metrics else "unavailable",
            "metrics": aggregate_metrics,
            "blockers": aggregate_blockers,
        },
        "items": items,
        "decision_gate": {
            "historical_attribution_available": status == "available",
            "decision_review_eligible": decision_review_eligible,
            "minimum_observation_days": DECISION_REVIEW_MIN_DAYS,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "reason": (
                "real_lot_outcome_review_ready"
                if decision_review_eligible
                else "monitoring_only_or_evidence_incomplete"
            ),
        },
        "policy": (
            "收益只归因到本批次实际绑定的基金买入流水，包含 FIFO 已实现回款、剩余份额按真实确认净值的当前价值和已记录费用；"
            "未成交基金不生成收益。少于 30 天只展示监控结果，不评价策略有效性；历史正收益不代表未来收益，也不授权自动交易。"
        ),
    }
