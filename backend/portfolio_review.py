# -*- coding: utf-8 -*-
"""Auditable trade-ledger and allocation review built from user-entered facts only."""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any

import storage


_EPSILON = 1e-8
_TRADE_LABELS = {
    "buy": "买入",
    "sell": "卖出",
    "opening": "期初持仓",
}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _asset_key(item: dict) -> tuple[str, str]:
    return str(item.get("asset_type") or ""), str(item.get("code") or "").strip()


def _sort_transactions(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (str(row.get("trade_date") or ""), int(row.get("id") or 0)))


def _display_name(item: dict) -> str:
    return str(item.get("name") or item.get("code") or "未知资产")


def _date_or_none(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _calculate_fifo(
    rows: list[dict],
    *,
    include_realized_lots: bool = False,
) -> tuple[list[dict], list[dict]] | tuple[list[dict], list[dict], list[dict]]:
    """Return remaining lots, integrity issues, and optional sell-to-lot matches using FIFO."""
    states: dict[tuple[str, str], dict] = {}
    issues: list[dict] = []
    realized_lots: list[dict] = []

    for transaction_order, row in enumerate(_sort_transactions(rows), start=1):
        key = _asset_key(row)
        state = states.setdefault(key, {
            "asset_type": key[0],
            "code": key[1],
            "market": row.get("market") or "",
            "name": row.get("name") or "",
            "lots": [],
            "transaction_count": 0,
            "buy_amount": 0.0,
            "opening_amount": 0.0,
            "sell_proceeds": 0.0,
            "total_fee": 0.0,
            "realized_profit": 0.0,
        })
        if row.get("name"):
            state["name"] = row["name"]
        if row.get("market"):
            state["market"] = row["market"]

        shares = _number(row.get("shares")) or 0
        price = _number(row.get("unit_price")) or 0
        fee = _number(row.get("fee")) or 0
        gross = shares * price
        trade_type = row.get("trade_type")
        state["transaction_count"] += 1
        state["total_fee"] += fee

        if trade_type in ("buy", "opening"):
            total_cost = gross + fee
            state["lots"].append({
                "shares": shares,
                "cost_per_share": total_cost / shares if shares else 0,
                "trade_date": row.get("trade_date") or "",
                "transaction_id": row.get("id"),
                "transaction_order": transaction_order,
                "trade_type": trade_type,
            })
            if trade_type == "opening":
                state["opening_amount"] += gross
            else:
                state["buy_amount"] += gross
            continue

        if trade_type != "sell":
            issues.append({
                "type": "unsupported_trade_type",
                "asset_type": key[0],
                "code": key[1],
                "name": _display_name(state),
                "message": "交易方向无法参与成本计算。",
            })
            continue

        state["sell_proceeds"] += max(0, gross - fee)
        remaining_to_sell = shares
        cost_used = 0.0
        matched_shares = 0.0
        while remaining_to_sell > _EPSILON and state["lots"]:
            lot = state["lots"][0]
            matched = min(remaining_to_sell, lot["shares"])
            matched_cost = matched * lot["cost_per_share"]
            cost_used += matched_cost
            matched_shares += matched
            matched_proceeds = (gross - fee) * (matched / shares) if shares else 0
            buy_date = _date_or_none(lot.get("trade_date"))
            sell_date = _date_or_none(row.get("trade_date"))
            holding_days = (sell_date - buy_date).days if buy_date and sell_date else None
            realized_lots.append({
                "asset_type": key[0],
                "market": state.get("market") or "",
                "code": key[1],
                "name": _display_name(state),
                "sale_transaction_id": row.get("id"),
                "sale_transaction_order": transaction_order,
                "sale_date": row.get("trade_date") or "",
                "buy_transaction_id": lot.get("transaction_id"),
                "buy_transaction_order": lot.get("transaction_order"),
                "buy_trade_type": lot.get("trade_type"),
                "buy_date": lot.get("trade_date") or "",
                "shares": matched,
                "cost": matched_cost,
                "proceeds": matched_proceeds,
                "realized_profit": matched_proceeds - matched_cost,
                "holding_days": holding_days if holding_days is None else max(0, holding_days),
            })
            lot["shares"] -= matched
            remaining_to_sell -= matched
            if lot["shares"] <= _EPSILON:
                state["lots"].pop(0)
        if matched_shares > _EPSILON:
            proceeds = (gross - fee) * (matched_shares / shares)
            state["realized_profit"] += proceeds - cost_used
        if remaining_to_sell > _EPSILON:
            issues.append({
                "type": "unmatched_sell",
                "asset_type": key[0],
                "code": key[1],
                "name": _display_name(state),
                "shares": _round(remaining_to_sell, 6),
                "message": "卖出份额超过已录入的买入或期初持仓，成本和已实现收益不完整。",
            })

    positions = []
    for state in states.values():
        open_shares = sum(lot["shares"] for lot in state["lots"])
        remaining_cost = sum(lot["shares"] * lot["cost_per_share"] for lot in state["lots"])
        positions.append({
            "asset_type": state["asset_type"],
            "market": state["market"],
            "code": state["code"],
            "name": _display_name(state),
            "open_shares": _round(open_shares, 6),
            "remaining_cost": _round(remaining_cost),
            "average_cost": _round(remaining_cost / open_shares if open_shares > _EPSILON else None, 6),
            "realized_profit": _round(state["realized_profit"]),
            "buy_amount": _round(state["buy_amount"]),
            "opening_amount": _round(state["opening_amount"]),
            "sell_proceeds": _round(state["sell_proceeds"]),
            "total_fee": _round(state["total_fee"]),
            "transaction_count": state["transaction_count"],
        })
    positions.sort(key=lambda row: (row["asset_type"], row["code"]))
    if include_realized_lots:
        return positions, issues, realized_lots
    return positions, issues


def _validate_trade_date(value: str) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("交易日期必须为 YYYY-MM-DD") from exc


def _prepare_transaction(item: dict) -> dict:
    candidate = dict(item)
    candidate["trade_date"] = _validate_trade_date(candidate.get("trade_date"))
    candidate["code"] = str(candidate.get("code") or "").strip()
    candidate["asset_type"] = str(candidate.get("asset_type") or "").strip()
    candidate["trade_type"] = str(candidate.get("trade_type") or "").strip()
    candidate["market"] = str(candidate.get("market") or "").strip()
    candidate["name"] = str(candidate.get("name") or "").strip()
    return candidate


def _validate_transaction_sequence(candidates: list[dict]) -> None:
    if not candidates:
        raise ValueError("没有可保存的交易流水")
    existing = storage.list_portfolio_transactions()
    candidate_rows = [{**item, "id": 1_000_000_000 + index} for index, item in enumerate(candidates)]
    _, issues = _calculate_fifo([*existing, *candidate_rows])
    candidate_keys = {_asset_key(item) for item in candidates}
    candidate_issues = [
        issue for issue in issues
        if issue.get("type") == "unmatched_sell" and _asset_key(issue) in candidate_keys
    ]
    if candidate_issues:
        raise ValueError(
            "卖出份额超过已录入的可用份额，请先补录期初持仓或对应买入记录。"
        )


def create_transaction(item: dict) -> dict:
    """Persist one user-entered transaction after checking its FIFO sequence."""
    candidate = _prepare_transaction(item)
    _validate_transaction_sequence([candidate])
    return storage.add_portfolio_transaction(candidate)


def create_transactions_from_csv(items: list[dict], file_sha256: str, filename: str = "") -> dict:
    """Save a user-confirmed statement preview atomically after FIFO validation."""
    candidates = [_prepare_transaction(item) for item in items]
    _validate_transaction_sequence(candidates)
    saved = storage.add_portfolio_transactions(
        candidates,
        file_sha256=file_sha256,
        filename=filename,
    )
    return {
        "saved": saved,
        "count": len(saved),
        "source": "用户确认交易账单导入",
        "message": "账单原文件未保存；已保存确认后的交易字段和文件哈希。",
    }


def list_transactions() -> dict:
    rows = storage.list_portfolio_transactions()
    source_labels = {
        "manual": "手动录入",
        "csv_import": "账单导入",
        "tiantian_fund_transaction_export": "天天基金交易导出",
    }
    return {
        "items": [{
            **row,
            "trade_label": _TRADE_LABELS.get(row.get("trade_type"), row.get("trade_type") or "-"),
            "source_label": source_labels.get(row.get("source"), row.get("source") or "手动录入"),
        } for row in rows],
        "count": len(rows),
        "source": "用户录入交易流水",
    }


def delete_transaction(transaction_id: int) -> bool:
    return storage.delete_portfolio_transaction(transaction_id)


def _holding_map() -> dict[tuple[str, str], dict]:
    result = {}
    for row in storage.list_holdings():
        result[_asset_key(row)] = row
    return result


def ledger_overview() -> dict:
    rows = storage.list_portfolio_transactions()
    positions, integrity_issues = _calculate_fifo(rows)
    holdings = _holding_map()
    reconciled = []
    for position in positions:
        holding = holdings.get(_asset_key(position))
        current_amount = _number((holding or {}).get("amount"))
        confirmed_shares = _number((holding or {}).get("shares"))
        open_shares = _number(position.get("open_shares")) or 0
        shares_match = None
        if confirmed_shares is not None:
            shares_match = abs(confirmed_shares - open_shares) <= max(_EPSILON, abs(confirmed_shares) * 0.001)
        estimated_unrealized = None
        estimated_unrealized_rate = None
        if current_amount is not None and position.get("remaining_cost") is not None:
            estimated_unrealized = current_amount - position["remaining_cost"]
            if position["remaining_cost"]:
                estimated_unrealized_rate = estimated_unrealized / position["remaining_cost"] * 100
        reconciled.append({
            **position,
            "confirmed_amount": _round(current_amount),
            "confirmed_shares": _round(confirmed_shares, 6),
            "shares_match": shares_match,
            "estimated_unrealized_profit": _round(estimated_unrealized),
            "estimated_unrealized_rate": _round(estimated_unrealized_rate),
        })

    return {
        "source": "用户录入交易流水 / 用户确认持仓",
        "summary": {
            "transaction_count": len(rows),
            "position_count": len(positions),
            "open_cost": _round(sum(_number(row.get("remaining_cost")) or 0 for row in positions)),
            "realized_profit": _round(sum(_number(row.get("realized_profit")) or 0 for row in positions)),
            "total_fee": _round(sum(_number(row.get("total_fee")) or 0 for row in positions)),
            "integrity_issue_count": len(integrity_issues),
            "share_reconciled_count": sum(row.get("shares_match") is True for row in reconciled),
            "share_mismatch_count": sum(row.get("shares_match") is False for row in reconciled),
        },
        "positions": reconciled,
        "integrity_issues": integrity_issues,
        "method": {
            "cost_basis": "成本采用 FIFO（先进先出）计算，仅涵盖用户已录入的买入、卖出和期初持仓。",
            "unrealized": "账面未实现收益仅在当前确认金额与剩余成本均存在时计算；它不是券商对账结果。",
        },
    }


def _xirr(cashflows: list[tuple[date, float]]) -> tuple[float | None, str | None]:
    """Solve a single annualized XIRR root without fabricating a result when ambiguous."""
    combined: dict[date, float] = {}
    for flow_date, amount in cashflows:
        if abs(amount) <= _EPSILON:
            continue
        combined[flow_date] = combined.get(flow_date, 0) + amount
    values = sorted(combined.items(), key=lambda row: row[0])
    if len(values) < 2:
        return None, "至少需要两个不同日期的现金流或当前估值，才能计算资金加权收益率。"
    start_date = values[0][0]
    if values[-1][0] <= start_date:
        return None, "现金流与当前估值处于同一日期，无法年化计算资金加权收益率。"
    amounts = [amount for _, amount in values]
    if not any(amount < 0 for amount in amounts) or not any(amount > 0 for amount in amounts):
        return None, "现金流未同时包含投入和回收/当前估值，无法计算资金加权收益率。"

    def npv(rate: float) -> float:
        return sum(amount / ((1 + rate) ** ((flow_date - start_date).days / 365)) for flow_date, amount in values)

    # Search on log(1 + rate) so the negative-rate boundary and large positive rates are stable.
    x_min = math.log(0.0001)
    x_max = math.log(1001)
    samples = []
    for index in range(801):
        rate = math.exp(x_min + (x_max - x_min) * index / 800) - 1
        value = npv(rate)
        if math.isfinite(value):
            samples.append((rate, value))
    brackets = []
    for (left_rate, left_value), (right_rate, right_value) in zip(samples, samples[1:]):
        if left_value == 0:
            return left_rate, None
        if left_value * right_value < 0:
            brackets.append((left_rate, right_rate))
    if len(brackets) != 1:
        if len(brackets) > 1:
            return None, "现金流可能存在多个资金加权收益率根，系统不会任选一个数值。"
        return None, "在可计算区间内未找到资金加权收益率根，请核对现金流日期和当前估值。"

    left, right = brackets[0]
    for _ in range(120):
        middle = (left + right) / 2
        middle_value = npv(middle)
        if abs(middle_value) < 1e-9:
            return middle, None
        if npv(left) * middle_value < 0:
            right = middle
        else:
            left = middle
    return (left + right) / 2, None


def cashflow_performance(as_of: date | None = None) -> dict:
    """Return MWR only when the recorded cash flows cover every valued holding."""
    as_of = as_of or date.today()
    rows = storage.list_portfolio_transactions()
    positions, integrity_issues = _calculate_fifo(rows)
    holdings = _holding_map()
    transaction_keys = {_asset_key(row) for row in rows}
    valued_holdings = [row for row in holdings.values() if (_number(row.get("amount")) or 0) > 0]
    missing_value_holdings = [row for row in holdings.values() if row not in valued_holdings]
    untracked_holdings = [row for row in valued_holdings if _asset_key(row) not in transaction_keys]
    unvalued_open_positions = [
        position for position in positions
        if (_number(position.get("open_shares")) or 0) > _EPSILON
        and (_number((holdings.get(_asset_key(position)) or {}).get("amount")) or 0) <= 0
    ]
    total_current_value = sum(_number(row.get("amount")) or 0 for row in valued_holdings)
    tracked_current_value = sum(
        _number(row.get("amount")) or 0
        for row in valued_holdings
        if _asset_key(row) in transaction_keys
    )
    total_invested = 0.0
    total_returned = 0.0
    flows = []
    for row in rows:
        flow_date = datetime.strptime(str(row.get("trade_date")), "%Y-%m-%d").date()
        gross = (_number(row.get("shares")) or 0) * (_number(row.get("unit_price")) or 0)
        fee = _number(row.get("fee")) or 0
        if row.get("trade_type") in {"buy", "opening"}:
            amount = -(gross + fee)
            total_invested += gross + fee
        elif row.get("trade_type") == "sell":
            amount = gross - fee
            total_returned += amount
        else:
            continue
        flows.append((flow_date, amount))

    reasons = []
    if not rows:
        reasons.append("尚未录入交易流水，无法从现金流计算收益率。")
    if integrity_issues:
        reasons.append("交易流水存在份额缺口，成本和现金流复盘已暂停。")
    if untracked_holdings:
        reasons.append(f"有 {len(untracked_holdings)} 项已确认持仓没有对应交易流水，不能计算完整组合收益率。")
    if missing_value_holdings:
        reasons.append(f"有 {len(missing_value_holdings)} 项持仓缺少当前确认金额，不能作为收益率终值。")
    if unvalued_open_positions:
        reasons.append(f"有 {len(unvalued_open_positions)} 个账本剩余仓位没有当前确认金额，不能作为收益率终值。")

    status = "available"
    money_weighted_return = None
    if reasons:
        status = "partial" if rows and (untracked_holdings or missing_value_holdings or unvalued_open_positions) else "unavailable"
    else:
        if total_current_value > _EPSILON:
            flows.append((as_of, total_current_value))
        money_weighted_return, xirr_error = _xirr(flows)
        if xirr_error:
            status = "unavailable"
            reasons.append(xirr_error)

    net_invested = total_invested - total_returned
    cashflow_profit = total_current_value - net_invested if status == "available" else None
    return {
        "source": "用户录入交易流水 / 用户确认当前持仓金额",
        "status": status,
        "policy": "资金加权收益率只在全部已确认持仓均有可用现金流和当前估值时展示；缺口不会用估算数据补齐。",
        "summary": {
            "money_weighted_return_annualized": _round(money_weighted_return * 100 if money_weighted_return is not None else None, 4),
            "total_invested": _round(total_invested),
            "total_returned": _round(total_returned),
            "net_invested": _round(net_invested),
            "current_value": _round(total_current_value),
            "tracked_current_value": _round(tracked_current_value),
            "cashflow_profit": _round(cashflow_profit),
            "transaction_count": len(rows),
            "valued_holding_count": len(valued_holdings),
            "untracked_holding_count": len(untracked_holdings),
            "missing_value_holding_count": len(missing_value_holdings),
            "as_of": as_of.isoformat(),
        },
        "reasons": reasons,
        "method": {
            "money_weighted_return": "采用 XIRR 计算年化资金加权收益率，买入和期初持仓为现金流出，卖出与当前确认市值为现金流入。",
            "coverage": "当前持仓金额来自用户确认值；不完整流水、未估值仓位或份额缺口时不展示完整组合收益率。",
        },
    }


def _transaction_key(row: dict, transaction_order: int) -> str:
    transaction_id = row.get("id")
    if transaction_id not in (None, ""):
        return f"id:{transaction_id}"
    return f"order:{transaction_order}"


def _weighted_holding_days(matches: list[dict]) -> float | None:
    dated_matches = [
        row for row in matches
        if _number(row.get("shares")) and row.get("holding_days") is not None
    ]
    total_shares = sum(_number(row.get("shares")) or 0 for row in dated_matches)
    if total_shares <= _EPSILON:
        return None
    return _round(sum(
        (_number(row.get("holding_days")) or 0) * (_number(row.get("shares")) or 0)
        for row in dated_matches
    ) / total_shares, 1)


def trade_behavior_review() -> dict:
    """Review confirmed transaction behavior without turning incomplete records into signals."""
    rows = _sort_transactions(storage.list_portfolio_transactions())
    _, integrity_issues, realized_lots = _calculate_fifo(rows, include_realized_lots=True)

    asset_states: dict[tuple[str, str], dict] = {}
    sale_records: dict[str, dict] = {}
    total_turnover = 0.0
    total_fee = 0.0
    trade_dates: list[str] = []

    for transaction_order, row in enumerate(rows, start=1):
        key = _asset_key(row)
        state = asset_states.setdefault(key, {
            "asset_type": key[0],
            "market": row.get("market") or "",
            "code": key[1],
            "name": row.get("name") or key[1],
            "transaction_count": 0,
            "buy_or_opening_count": 0,
            "sell_count": 0,
            "turnover": 0.0,
            "fee": 0.0,
            "sale_records": [],
        })
        if row.get("name"):
            state["name"] = row["name"]
        if row.get("market"):
            state["market"] = row["market"]

        shares = _number(row.get("shares")) or 0
        unit_price = _number(row.get("unit_price")) or 0
        fee = _number(row.get("fee")) or 0
        gross = shares * unit_price
        trade_type = row.get("trade_type")
        state["transaction_count"] += 1
        state["turnover"] += gross
        state["fee"] += fee
        total_turnover += gross
        total_fee += fee
        if row.get("trade_date"):
            trade_dates.append(str(row["trade_date"]))

        if trade_type in {"buy", "opening"}:
            state["buy_or_opening_count"] += 1
        if trade_type != "sell":
            continue

        sale_key = _transaction_key(row, transaction_order)
        sale = {
            "key": sale_key,
            "asset_key": key,
            "asset_type": key[0],
            "market": state["market"],
            "code": key[1],
            "name": state["name"],
            "trade_date": row.get("trade_date") or "",
            "requested_shares": shares,
            "gross_amount": gross,
            "fee": fee,
            "net_proceeds": gross - fee,
            "matches": [],
        }
        sale_records[sale_key] = sale
        state["sell_count"] += 1
        state["sale_records"].append(sale_key)

    for match in realized_lots:
        sale_key = _transaction_key(
            {"id": match.get("sale_transaction_id")},
            int(match.get("sale_transaction_order") or 0),
        )
        sale = sale_records.get(sale_key)
        if sale is not None:
            sale["matches"].append(match)

    complete_sales = []
    partial_sell_count = 0
    unmatched_sell_count = 0
    matched_shares = 0.0
    unmatched_shares = 0.0
    for sale in sale_records.values():
        sale["matched_shares"] = sum(_number(row.get("shares")) or 0 for row in sale["matches"])
        sale["matched_cost"] = sum(_number(row.get("cost")) or 0 for row in sale["matches"])
        sale["matched_profit"] = sum(_number(row.get("realized_profit")) or 0 for row in sale["matches"])
        sale["unmatched_shares"] = max(0.0, sale["requested_shares"] - sale["matched_shares"])
        sale["average_holding_days"] = _weighted_holding_days(sale["matches"])
        sale["fully_matched"] = sale["unmatched_shares"] <= _EPSILON
        matched_shares += sale["matched_shares"]
        unmatched_shares += sale["unmatched_shares"]
        if sale["fully_matched"]:
            complete_sales.append(sale)
        elif sale["matched_shares"] > _EPSILON:
            partial_sell_count += 1
        else:
            unmatched_sell_count += 1

    successful_matches = [match for sale in complete_sales for match in sale["matches"]]
    winners = [sale for sale in complete_sales if sale["matched_profit"] > _EPSILON]
    losers = [sale for sale in complete_sales if sale["matched_profit"] < -_EPSILON]
    breakevens = [
        sale for sale in complete_sales
        if abs(sale["matched_profit"]) <= _EPSILON
    ]
    matched_realized_profit = sum(sale["matched_profit"] for sale in complete_sales)
    gross_profit = sum(sale["matched_profit"] for sale in winners)
    gross_loss = abs(sum(sale["matched_profit"] for sale in losers))
    completed_holding_days = _weighted_holding_days(successful_matches)

    asset_reviews = []
    for state in asset_states.values():
        asset_sales = [sale_records[key] for key in state["sale_records"] if key in sale_records]
        completed_asset_sales = [sale for sale in asset_sales if sale["fully_matched"]]
        matched_asset = [match for sale in completed_asset_sales for match in sale["matches"]]
        asset_partial = [sale for sale in asset_sales if not sale["fully_matched"]]
        asset_winners = [sale for sale in completed_asset_sales if sale["matched_profit"] > _EPSILON]
        asset_losers = [sale for sale in completed_asset_sales if sale["matched_profit"] < -_EPSILON]
        asset_reasons = []
        if not asset_sales:
            asset_status = "unavailable"
            asset_reasons.append("尚未录入卖出流水，不能复盘已实现结果。")
        elif asset_partial:
            asset_status = "partial" if completed_asset_sales else "unavailable"
            asset_reasons.append(f"有 {len(asset_partial)} 笔卖出未完整匹配可用份额。")
        else:
            asset_status = "available"
        win_loss_denominator = len(asset_winners) + len(asset_losers)
        asset_reviews.append({
            "asset_type": state["asset_type"],
            "market": state["market"],
            "code": state["code"],
            "name": state["name"],
            "status": asset_status,
            "transaction_count": state["transaction_count"],
            "buy_or_opening_count": state["buy_or_opening_count"],
            "sell_count": state["sell_count"],
            "fully_matched_sell_count": len(completed_asset_sales),
            "unmatched_sell_count": len(asset_partial),
            "turnover": _round(state["turnover"]),
            "total_fee": _round(state["fee"]),
            "matched_realized_profit": _round(
                sum(sale["matched_profit"] for sale in completed_asset_sales)
                if completed_asset_sales else None
            ),
            "win_count": len(asset_winners),
            "loss_count": len(asset_losers),
            "win_rate": _round(len(asset_winners) / win_loss_denominator * 100 if win_loss_denominator else None),
            "average_holding_days": _weighted_holding_days(matched_asset),
            "reasons": asset_reasons,
        })
    asset_reviews.sort(key=lambda row: (_number(row.get("turnover")) or 0, row.get("code") or ""), reverse=True)

    reasons = []
    if not rows:
        reasons.append("尚未录入交易流水，无法复盘交易频率、费用和持有期。")
    elif not sale_records:
        reasons.append("尚未录入卖出流水，不能根据已实现结果复盘交易行为。")
    if partial_sell_count or unmatched_sell_count:
        reasons.append(
            f"有 {partial_sell_count + unmatched_sell_count} 笔卖出无法完整匹配已录入份额；"
            "相关盈亏和持有期不会作为完整行为结论。"
        )
    if integrity_issues and not (partial_sell_count or unmatched_sell_count):
        reasons.append("交易流水存在份额完整性问题，复盘结论需要先核对。")

    if not rows or not sale_records or not complete_sales:
        status = "unavailable"
    elif partial_sell_count or unmatched_sell_count or integrity_issues:
        status = "partial"
    else:
        status = "available"

    win_loss_denominator = len(winners) + len(losers)
    return {
        "source": "用户录入交易流水",
        "status": status,
        "policy": "只复盘已匹配的真实交易事实，不用不完整流水推断交易能力，也不生成买卖指令。",
        "summary": {
            "transaction_count": len(rows),
            "trade_day_count": len(set(trade_dates)),
            "asset_count": len(asset_states),
            "sell_count": len(sale_records),
            "fully_matched_sell_count": len(complete_sales),
            "partial_sell_count": partial_sell_count,
            "unmatched_sell_count": unmatched_sell_count,
            "turnover": _round(total_turnover),
            "total_fee": _round(total_fee),
            "fee_rate": _round(total_fee / total_turnover * 100 if total_turnover > _EPSILON else None, 4),
            "matched_realized_profit": _round(matched_realized_profit if complete_sales else None),
            "win_count": len(winners),
            "loss_count": len(losers),
            "breakeven_count": len(breakevens),
            "win_rate": _round(len(winners) / win_loss_denominator * 100 if win_loss_denominator else None),
            "profit_loss_ratio": _round(gross_profit / gross_loss if gross_profit > _EPSILON and gross_loss > _EPSILON else None, 4),
            "average_holding_days": completed_holding_days,
        },
        "coverage": {
            "matched_lot_count": len(realized_lots),
            "matched_shares": _round(matched_shares, 6),
            "unmatched_shares": _round(unmatched_shares, 6),
            "integrity_issue_count": len(integrity_issues),
        },
        "asset_reviews": asset_reviews,
        "reasons": reasons,
        "method": {
            "matching": "卖出按 FIFO 与已录入买入或期初持仓逐批匹配；买入费用计入成本，卖出费用从成交回款中扣除。",
            "holding_days": "持有期按已匹配买入日至卖出日的自然日、以匹配份额加权计算。",
            "coverage": "仅完整匹配的卖出参与胜率、盈亏比和平均持有期；未匹配卖出会明确列为数据缺口。",
        },
    }


def create_snapshot(reason: str = "manual") -> dict:
    items = storage.list_holdings()
    if not items:
        raise ValueError("没有已确认持仓，无法记录组合快照")
    return storage.create_portfolio_snapshot(items, reason=reason)


def list_snapshots(limit: int = 24) -> dict:
    items = storage.list_portfolio_snapshots(limit=limit)
    latest = items[0] if items else None
    previous = items[1] if len(items) > 1 else None
    change = None
    if latest and previous:
        change = {
            "amount_change": _round((_number(latest.get("total_amount")) or 0) - (_number(previous.get("total_amount")) or 0)),
            "profit_change": _round((_number(latest.get("total_profit")) or 0) - (_number(previous.get("total_profit")) or 0)),
            "from": previous.get("captured_at"),
            "to": latest.get("captured_at"),
        }
    return {
        "source": "用户确认持仓快照",
        "items": items,
        "count": len(items),
        "latest_change": change,
        "method": "两次快照的金额变化可能包含申购、赎回或转入转出，不能直接视为投资收益。",
    }


def _snapshot_date(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _snapshot_asset_keys(snapshot: dict) -> set[tuple[str, str]]:
    keys = set()
    for item in snapshot.get("holdings") or []:
        amount = _number(item.get("amount")) or 0
        shares = _number(item.get("shares")) or 0
        key = _asset_key(item)
        if key[0] and key[1] and (amount > _EPSILON or shares > _EPSILON):
            keys.add(key)
    return keys


def snapshot_attribution() -> dict:
    """Attribute a snapshot interval only when user records cover its cash flows."""
    snapshots = storage.list_portfolio_snapshots(limit=24, include_holdings=True)
    base_summary = {
        "start_at": None,
        "end_at": None,
        "period_days": None,
        "start_amount": None,
        "end_amount": None,
        "asset_value_change": None,
        "net_cash_flow": None,
        "flow_adjusted_change": None,
        "modified_dietz_return": None,
        "weighted_capital": None,
        "transaction_count": 0,
    }
    base_coverage = {
        "snapshot_count": len(snapshots),
        "snapshot_asset_count": 0,
        "tracked_snapshot_asset_count": 0,
        "boundary_transaction_count": 0,
        "integrity_issue_count": 0,
    }
    policy = (
        "仅在两次不同日期快照、快照资产均有对应交易流水且不存在边界日时点歧义时，"
        "展示资金流调整后的区间变化；缺口不会被视为市场收益。"
    )
    method = {
        "return": (
            "区间回报采用日维度 Modified Dietz：买入/期初持仓作为外部投入，"
            "卖出净回款作为外部流出，交易费用计入资金流。"
        ),
        "boundary": "交易流水只有交易日期没有时点；快照起止日存在交易时，为避免归属错误不会计算区间回报。",
    }

    if len(snapshots) < 2:
        return {
            "source": "用户确认持仓快照 / 用户录入交易流水",
            "status": "unavailable",
            "policy": policy,
            "summary": base_summary,
            "coverage": base_coverage,
            "reasons": ["至少需要两次不同日期的确认持仓快照，才能做区间归因。"],
            "interval_transactions": [],
            "method": method,
        }

    latest = snapshots[0]
    end_date = _snapshot_date(latest.get("captured_at"))
    previous = next(
        (
            item for item in snapshots[1:]
            if end_date and (_snapshot_date(item.get("captured_at")) or end_date) < end_date
        ),
        None,
    )
    if previous is None or end_date is None:
        return {
            "source": "用户确认持仓快照 / 用户录入交易流水",
            "status": "unavailable",
            "policy": policy,
            "summary": base_summary,
            "coverage": base_coverage,
            "reasons": ["未找到两次不同日期的有效持仓快照。"],
            "interval_transactions": [],
            "method": method,
        }

    start_date = _snapshot_date(previous.get("captured_at"))
    if start_date is None or end_date <= start_date:
        return {
            "source": "用户确认持仓快照 / 用户录入交易流水",
            "status": "unavailable",
            "policy": policy,
            "summary": base_summary,
            "coverage": base_coverage,
            "reasons": ["快照日期无效或处于同一日期，无法计算区间回报。"],
            "interval_transactions": [],
            "method": method,
        }

    start_amount = _number(previous.get("total_amount"))
    end_amount = _number(latest.get("total_amount"))
    period_days = (end_date - start_date).days
    summary = {
        **base_summary,
        "start_at": previous.get("captured_at"),
        "end_at": latest.get("captured_at"),
        "period_days": period_days,
        "start_amount": _round(start_amount),
        "end_amount": _round(end_amount),
    }
    snapshot_assets = _snapshot_asset_keys(previous) | _snapshot_asset_keys(latest)
    rows = _sort_transactions(storage.list_portfolio_transactions())
    positions, integrity_issues = _calculate_fifo(rows)
    del positions
    transaction_keys = {_asset_key(row) for row in rows}
    missing_assets = sorted(snapshot_assets - transaction_keys)
    invalid_dates = [row for row in rows if _date_or_none(row.get("trade_date")) is None]
    boundary_rows = [
        row for row in rows
        if _date_or_none(row.get("trade_date")) in {start_date, end_date}
    ]
    interval_rows = [
        row for row in rows
        if (trade_date := _date_or_none(row.get("trade_date"))) and start_date < trade_date < end_date
    ]
    coverage = {
        **base_coverage,
        "snapshot_asset_count": len(snapshot_assets),
        "tracked_snapshot_asset_count": len(snapshot_assets) - len(missing_assets),
        "boundary_transaction_count": len(boundary_rows),
        "integrity_issue_count": len(integrity_issues),
    }
    reasons = []
    if start_amount is None or end_amount is None or start_amount <= _EPSILON:
        reasons.append("起始或结束快照缺少有效总金额，无法作为区间归因基准。")
    if not snapshot_assets:
        reasons.append("快照中没有可识别的持仓资产，无法核对交易流水覆盖。")
    if missing_assets:
        reasons.append(f"有 {len(missing_assets)} 项快照持仓没有对应交易流水，不能把金额变化视为投资收益。")
    if integrity_issues:
        reasons.append("交易流水存在份额缺口，区间资金流归因已暂停。")
    if invalid_dates:
        reasons.append("存在交易日期无效的流水，无法定位其是否属于快照区间。")
    if boundary_rows:
        reasons.append("快照起止日存在交易流水；因缺少交易时点，无法安全归属到区间内外。")

    if reasons:
        return {
            "source": "用户确认持仓快照 / 用户录入交易流水",
            "status": "unavailable",
            "policy": policy,
            "summary": summary,
            "coverage": coverage,
            "reasons": reasons,
            "interval_transactions": [],
            "method": method,
        }

    net_cash_flow = 0.0
    weighted_cash_flow = 0.0
    interval_transactions = []
    for row in interval_rows:
        trade_date = _date_or_none(row.get("trade_date"))
        shares = _number(row.get("shares")) or 0
        unit_price = _number(row.get("unit_price")) or 0
        fee = _number(row.get("fee")) or 0
        gross = shares * unit_price
        if row.get("trade_type") in {"buy", "opening"}:
            cash_flow = gross + fee
        elif row.get("trade_type") == "sell":
            cash_flow = -(gross - fee)
        else:
            continue
        weight = (end_date - trade_date).days / period_days
        net_cash_flow += cash_flow
        weighted_cash_flow += cash_flow * weight
        interval_transactions.append({
            "trade_date": row.get("trade_date"),
            "asset_type": row.get("asset_type"),
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "trade_type": row.get("trade_type"),
            "cash_flow": _round(cash_flow),
            "fee": _round(fee),
        })

    weighted_capital = start_amount + weighted_cash_flow
    asset_value_change = end_amount - start_amount
    flow_adjusted_change = asset_value_change - net_cash_flow
    if weighted_capital <= _EPSILON:
        return {
            "source": "用户确认持仓快照 / 用户录入交易流水",
            "status": "unavailable",
            "policy": policy,
            "summary": {
                **summary,
                "asset_value_change": _round(asset_value_change),
                "net_cash_flow": _round(net_cash_flow),
                "flow_adjusted_change": _round(flow_adjusted_change),
                "weighted_capital": _round(weighted_capital),
                "transaction_count": len(interval_transactions),
            },
            "coverage": coverage,
            "reasons": ["资金流加权后的区间资本不大于零，无法计算区间回报率。"],
            "interval_transactions": interval_transactions,
            "method": method,
        }

    return {
        "source": "用户确认持仓快照 / 用户录入交易流水",
        "status": "available",
        "policy": policy,
        "summary": {
            **summary,
            "asset_value_change": _round(asset_value_change),
            "net_cash_flow": _round(net_cash_flow),
            "flow_adjusted_change": _round(flow_adjusted_change),
            "modified_dietz_return": _round(flow_adjusted_change / weighted_capital * 100, 4),
            "weighted_capital": _round(weighted_capital),
            "transaction_count": len(interval_transactions),
        },
        "coverage": coverage,
        "reasons": [],
        "interval_transactions": interval_transactions,
        "method": method,
    }


def rebalance_review() -> dict:
    """Calculate only constraint breaches and contribution room; never emit buy/sell orders."""
    profile = storage.get_investment_profile()
    holdings = storage.list_holdings()
    ledger = ledger_overview()
    valid_holdings = [row for row in holdings if (_number(row.get("amount")) or 0) > 0]
    missing_amounts = [row for row in holdings if (_number(row.get("amount")) or 0) <= 0]
    total_amount = sum(_number(row.get("amount")) or 0 for row in valid_holdings)
    max_single_ratio = _number(profile.get("max_single_ratio")) if profile.get("configured") else None
    monthly_budget = _number(profile.get("monthly_budget")) if profile.get("configured") else None
    allocations = []
    for row in sorted(valid_holdings, key=lambda item: _number(item.get("amount")) or 0, reverse=True):
        amount = _number(row.get("amount")) or 0
        ratio = amount / total_amount * 100 if total_amount else None
        cap_amount = total_amount * max_single_ratio / 100 if max_single_ratio is not None else None
        excess_amount = max(0, amount - cap_amount) if cap_amount is not None else None
        room_before_cap = max(0, cap_amount - amount) if cap_amount is not None else None
        post_budget_ratio = None
        if monthly_budget and monthly_budget > 0:
            post_budget_ratio = amount / (total_amount + monthly_budget) * 100
        allocations.append({
            "asset_type": row.get("asset_type"),
            "market": row.get("market") or "",
            "code": row.get("code"),
            "name": _display_name(row),
            "amount": _round(amount),
            "current_ratio": _round(ratio),
            "max_single_ratio": _round(max_single_ratio),
            "cap_amount": _round(cap_amount),
            "excess_amount": _round(excess_amount),
            "room_before_cap": _round(room_before_cap),
            "post_budget_ratio_if_unchanged": _round(post_budget_ratio),
        })

    actions = []
    if not holdings:
        actions.append({
            "level": "high",
            "title": "先确认当前持仓，再做仓位复盘",
            "detail": "没有用户确认的持仓金额，不能计算真实仓位或上限空间。",
        })
    if missing_amounts:
        actions.append({
            "level": "high",
            "title": "补全缺失的持仓金额",
            "detail": f"有 {len(missing_amounts)} 项持仓未参与仓位计算，系统不会猜测它们的配置比例。",
        })
    if not profile.get("configured"):
        actions.append({
            "level": "medium",
            "title": "先保存单品上限和投入期限",
            "detail": "未保存的默认值不会被当成你的策略，因此不能判断是否超过你的仓位纪律。",
        })
    if ledger["summary"]["transaction_count"] == 0:
        actions.append({
            "level": "medium",
            "title": "补录交易流水或期初持仓",
            "detail": "当前只有市值和累计收益，无法用可追溯成本拆分已实现与未实现收益。",
        })
    if ledger["summary"]["integrity_issue_count"]:
        actions.append({
            "level": "high",
            "title": "修正交易流水的份额缺口",
            "detail": f"有 {ledger['summary']['integrity_issue_count']} 项卖出无法匹配已录入份额，相关成本与收益结论已降级。",
        })
    for row in allocations:
        if (row.get("excess_amount") or 0) > _EPSILON:
            actions.append({
                "level": "high" if (row.get("current_ratio") or 0) >= (max_single_ratio or 0) + 10 else "medium",
                "title": f"{row['name']} 超过单品上限",
                "detail": (
                    f"当前占比 {row['current_ratio']:.2f}%，超过你设定的 {max_single_ratio:.2f}% 上限；"
                    f"相对上限高出约 {row['excess_amount']:.2f}。先确认这是主动集中还是无意形成。"
                ),
                "code": row["code"],
            })
    if profile.get("configured") and total_amount > 0 and not any((row.get("excess_amount") or 0) > _EPSILON for row in allocations):
        actions.append({
            "level": "normal",
            "title": "当前没有超过单品上限的持仓",
            "detail": "这只说明已确认金额未触发你的集中度规则，不代表未来风险或收益已被预测。",
        })

    return {
        "source": "用户确认持仓 / 用户投资约束 / 用户录入交易流水",
        "policy": "只展示已保存约束下的超限金额和上限空间，用于复盘，不生成买卖指令。",
        "profile": profile,
        "summary": {
            "holding_count": len(holdings),
            "included_holding_count": len(valid_holdings),
            "missing_amount_count": len(missing_amounts),
            "total_amount": _round(total_amount),
            "monthly_budget": _round(monthly_budget),
        },
        "allocations": allocations,
        "actions": actions[:12],
        "ledger_summary": ledger["summary"],
        "method": {
            "cap": "单品上限以当前确认总金额计算；上限空间不是目标仓位或买入建议。",
            "budget": "月度预算只用于显示若不改变既有持仓时的比例变化，不预测收益。",
        },
    }
