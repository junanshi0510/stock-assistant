# -*- coding: utf-8 -*-
"""Deterministic lifecycle rules for real batch fund purchase records."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


STRATEGY_ID = "portfolio_batch_purchase_execution"
STRATEGY_VERSION = "1.0.0"
SCHEMA_VERSION = f"{STRATEGY_ID}.v1"
MONEY_TOLERANCE = 0.02
SHARE_TOLERANCE_RATIO = 0.001
ORDER_VARIANCE_RATIO = 0.01
ORDER_VARIANCE_YUAN = 2.0
FEE_VARIANCE_RATIO = 0.20
FEE_VARIANCE_YUAN = 1.0


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: Any) -> float | None:
    number = _number(value)
    return round(number, 2) if number is not None and number >= 0 else None


def _datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo is not None else None


def evaluate_batch_purchase_execution(
    preflight: dict[str, Any],
    outcomes: list[dict[str, Any]],
    transactions: dict[int, dict[str, Any]],
    *,
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Bind user-confirmed execution facts without authorizing or placing an order."""
    generated = _datetime(generated_at)
    if generated is None:
        raise ValueError("成交回填时间必须包含时区")
    if preflight.get("status") != "ready_for_manual_purchase_review":
        raise ValueError("绑定的执行前复核从未通过全部确定性门禁")

    quote_rows = preflight.get("quotes") or []
    quotes = {str(item.get("code") or ""): item for item in quote_rows}
    outcome_codes = [str(item.get("code") or "") for item in outcomes]
    if set(outcome_codes) != set(quotes) or len(outcome_codes) != len(set(outcome_codes)):
        raise ValueError("成交结果必须逐只且仅覆盖执行前复核中的全部基金")

    rows: list[dict[str, Any]] = []
    purchased_count = 0
    skipped_count = 0
    actual_cash_total = 0.0
    actual_fee_total = 0.0
    allocated_total = 0.0
    expected_order_total = 0.0

    outcome_by_code = {str(item.get("code") or ""): item for item in outcomes}
    for quote in quote_rows:
        code = str(quote.get("code") or "")
        outcome = outcome_by_code[code]
        resolution = str(outcome.get("resolution") or "")
        allocated = _number(quote.get("allocated_amount_yuan")) or 0.0
        expected_order = _number(quote.get("order_amount_yuan")) or 0.0
        expected_fee = _number(quote.get("entry_fee_yuan")) or 0.0
        allocated_total += allocated
        expected_order_total += expected_order

        if resolution == "not_purchased":
            skipped_count += 1
            rows.append({
                "code": code,
                "name": quote.get("name") or code,
                "resolution": resolution,
                "not_purchased_reason": outcome.get("not_purchased_reason"),
                "not_purchased_detail": outcome.get("not_purchased_detail") or None,
                "allocated_amount_yuan": _money(allocated),
                "expected_order_amount_yuan": _money(expected_order),
                "transaction": None,
                "actual_cash_amount_yuan": None,
                "actual_fee_yuan": None,
                "order_variance_yuan": None,
                "fee_variance_yuan": None,
                "variance_acknowledged": False,
            })
            continue
        if resolution != "purchased":
            raise ValueError(f"{code} 缺少明确的已申购或未申购结果")

        transaction_id = int(outcome.get("transaction_id") or 0)
        transaction = transactions.get(transaction_id)
        if transaction is None:
            raise ValueError(f"{code} 缺少已验证的真实申购流水")
        shares = _number(transaction.get("shares"))
        unit_price = _number(transaction.get("unit_price"))
        fee = _number(transaction.get("fee"))
        if shares is None or shares <= 0 or unit_price is None or unit_price <= 0:
            raise ValueError(f"{code} 的真实申购流水份额或成交单价无效")
        if fee is None or fee < 0:
            raise ValueError(f"{code} 的真实申购流水费用无效")
        actual_cash = shares * unit_price + fee
        if actual_cash > allocated + MONEY_TOLERANCE:
            raise ValueError(
                f"{code} 实际占用资金 {actual_cash:.2f} 元超过已分配 {allocated:.2f} 元"
            )
        order_variance = actual_cash - expected_order
        fee_variance = fee - expected_fee
        order_material = abs(order_variance) > max(
            ORDER_VARIANCE_YUAN, expected_order * ORDER_VARIANCE_RATIO
        )
        fee_material = abs(fee_variance) > max(
            FEE_VARIANCE_YUAN, expected_fee * FEE_VARIANCE_RATIO
        )
        variance_acknowledged = bool(outcome.get("acknowledged_order_variance"))
        if (order_material or fee_material) and not variance_acknowledged:
            raise ValueError(
                f"{code} 实际成交金额或费用与执行前复核存在显著差异，必须明确确认"
            )

        purchased_count += 1
        actual_cash_total += actual_cash
        actual_fee_total += fee
        rows.append({
            "code": code,
            "name": quote.get("name") or transaction.get("name") or code,
            "resolution": resolution,
            "not_purchased_reason": None,
            "not_purchased_detail": None,
            "allocated_amount_yuan": _money(allocated),
            "expected_order_amount_yuan": _money(expected_order),
            "expected_fee_yuan": _money(expected_fee),
            "purchase_submitted_at": outcome.get("purchase_submitted_at"),
            "transaction": transaction,
            "actual_cash_amount_yuan": _money(actual_cash),
            "actual_fee_yuan": _money(fee),
            "order_variance_yuan": round(order_variance, 2),
            "fee_variance_yuan": round(fee_variance, 2),
            "variance_acknowledged": variance_acknowledged,
            "material_variance": bool(order_material or fee_material),
        })

    if actual_cash_total > allocated_total + MONEY_TOLERANCE:
        raise ValueError("批次实际占用资金超过不可变组合分配总额")

    has_purchase = purchased_count > 0
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": generated.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "event_type": "purchases_recorded",
        "status": (
            "purchases_recorded_reconciliation_pending"
            if has_purchase else "completed_no_purchase"
        ),
        "bindings": bindings,
        "summary": {
            "fund_count": len(rows),
            "purchased_count": purchased_count,
            "not_purchased_count": skipped_count,
            "allocated_total_yuan": _money(allocated_total),
            "expected_order_total_yuan": _money(expected_order_total),
            "actual_cash_total_yuan": _money(actual_cash_total),
            "actual_fee_total_yuan": _money(actual_fee_total),
            "unused_allocated_cash_yuan": _money(max(0.0, allocated_total - actual_cash_total)),
        },
        "outcomes": rows,
        "decision_gate": {
            "execution_facts_recorded": True,
            "holdings_reconciliation_ready": has_purchase,
            "completed_without_purchase": not has_purchase,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "order_submitted_by_system": False,
        },
        "policy": (
            "本事件只绑定用户在销售平台完成后录入的真实成交流水或未成交原因；"
            "系统未连接券商/销售账户、未授权或提交订单，也不把成交回填解释为盈利保证。"
        ),
    }


def evaluate_batch_purchase_reconciliation(
    purchase_event: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    bindings: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    """Close the lifecycle only when confirmed holdings match the FIFO ledger."""
    generated = _datetime(generated_at)
    if generated is None:
        raise ValueError("持仓对账时间必须包含时区")
    if purchase_event.get("event_type") != "purchases_recorded":
        raise ValueError("必须先绑定真实申购成交")
    if not reconciliation.get("ready"):
        blockers = reconciliation.get("blockers") or []
        raise ValueError("；".join(str(item) for item in blockers[:3]) or "当前持仓尚未通过 FIFO 对账")
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": generated.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "event_type": "holdings_reconciled",
        "status": "completed_reconciled",
        "bindings": bindings,
        "reconciliation": reconciliation,
        "decision_gate": {
            "execution_facts_recorded": True,
            "holdings_reconciled": True,
            "execution_authorized": False,
            "automatic_purchase_allowed": False,
            "order_submitted_by_system": False,
        },
        "policy": (
            "对账只证明用户确认的当前基金份额与真实交易流水按 FIFO 计算的未平仓份额一致；"
            "后续新增、卖出、更正流水或持仓变化会令当前绑定失效，但不会改写历史事件。"
        ),
    }
