# -*- coding: utf-8 -*-
"""Application service for real batch fund purchase execution and reconciliation."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any

import portfolio_exposure
import portfolio_review
import storage
from investment_policy import payload_sha256
from strategies.portfolio_batch_purchase_execution import (
    SHARE_TOLERANCE_RATIO,
    evaluate_batch_purchase_execution,
    evaluate_batch_purchase_reconciliation,
)
from strategies.portfolio_batch_purchase_preflight import MAX_CONFIRMATION_DAYS

from .repository import AgentRepository


NOT_PURCHASED_REASONS = {
    "platform_unavailable",
    "limit_insufficient",
    "insufficient_cash",
    "risk_reassessment",
    "user_cancelled",
    "other",
}


class BatchPurchaseExecutionValidationError(ValueError):
    pass


class BatchPurchaseExecutionConflictError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _datetime(value: Any, label: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise BatchPurchaseExecutionValidationError(f"{label}格式无效") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatchPurchaseExecutionValidationError(f"{label}必须包含时区")
    return parsed


def _date(value: Any, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError) as error:
        raise BatchPurchaseExecutionValidationError(f"{label}必须是 YYYY-MM-DD") from error


def _validated_now(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise BatchPurchaseExecutionValidationError("服务时间必须包含时区")
    return current.astimezone(dt.timezone.utc)


def _transaction_snapshot(transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        key: transaction.get(key)
        for key in (
            "id",
            "user_id",
            "asset_type",
            "market",
            "code",
            "name",
            "trade_type",
            "trade_date",
            "shares",
            "unit_price",
            "fee",
            "source",
            "created_at",
        )
    }


def _transaction_sha256(transaction: dict[str, Any]) -> str:
    return payload_sha256(_transaction_snapshot(transaction))


def _transaction_option(
    transaction: dict[str, Any],
    *,
    bound_to_current_batch: bool,
) -> dict[str, Any]:
    shares = _number(transaction.get("shares")) or 0.0
    unit_price = _number(transaction.get("unit_price")) or 0.0
    fee = _number(transaction.get("fee")) or 0.0
    return {
        "id": int(transaction.get("id") or 0),
        "code": str(transaction.get("code") or ""),
        "name": transaction.get("name") or transaction.get("code"),
        "trade_date": transaction.get("trade_date"),
        "shares": round(shares, 8),
        "unit_price": round(unit_price, 8),
        "fee_yuan": round(fee, 2),
        "cash_amount_yuan": round(shares * unit_price + fee, 2),
        "source": transaction.get("source"),
        "bound_to_current_batch": bound_to_current_batch,
    }


def _preflight(
    repository: AgentRepository,
    batch: dict[str, Any],
    *,
    expected_id: str,
    expected_hash: str,
    user_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event = batch.get("purchase_preflight_event")
    audit = repository.verify_batch_purchase_preflight_audit(
        str(batch.get("id") or ""), user_id=user_id
    )
    if (
        event is None
        or not event.get("integrity_verified")
        or not audit.get("verified")
        or str(event.get("id") or "") != expected_id
        or str(event.get("event_hash") or "") != expected_hash
    ):
        raise BatchPurchaseExecutionConflictError(
            "执行前复核不存在、不是最新版本或审计完整性失败"
        )
    payload = event.get("payload") or {}
    if (
        payload.get("status") != "ready_for_manual_purchase_review"
        or not (payload.get("decision_gate") or {}).get("manual_purchase_review_ready")
    ):
        raise BatchPurchaseExecutionConflictError("执行前复核从未通过全部确定性门禁")
    return event, payload


def _eligible_transactions(
    repository: AgentRepository,
    batch_id: str,
    preflight_payload: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        str(item.get("code") or ""): [] for item in preflight_payload.get("quotes") or []
    }
    quote_map = {
        str(item.get("code") or ""): item for item in preflight_payload.get("quotes") or []
    }
    switch_bound_ids = storage.list_fund_switch_bound_purchase_transaction_ids(
        user_id=user_id
    )
    batch_bindings = {
        int(item.get("transaction_id") or 0): item
        for item in repository.list_batch_purchase_transaction_bindings(user_id=user_id)
    }
    for transaction in storage.list_portfolio_transactions(user_id=user_id):
        code = str(transaction.get("code") or "")
        quote = quote_map.get(code)
        transaction_id = int(transaction.get("id") or 0)
        if (
            quote is None
            or transaction.get("asset_type") != "fund"
            or transaction.get("trade_type") != "buy"
        ):
            continue
        if transaction_id in switch_bound_ids:
            continue
        binding = batch_bindings.get(transaction_id)
        if binding is not None and str(binding.get("batch_id") or "") != batch_id:
            continue
        try:
            quoted_at = _datetime(quote.get("quoted_at"), f"{code} 报价时间")
            trade_date = _date(transaction.get("trade_date"), f"{code} 成交确认日期")
        except BatchPurchaseExecutionValidationError:
            continue
        if not quoted_at.date() <= trade_date <= quoted_at.date() + dt.timedelta(
            days=MAX_CONFIRMATION_DAYS
        ):
            continue
        result[code].append(_transaction_option(
            transaction,
            bound_to_current_batch=binding is not None,
        ))
    for items in result.values():
        items.sort(key=lambda item: (str(item.get("trade_date") or ""), int(item["id"])), reverse=True)
    return result


def _normalize_outcomes(
    repository: AgentRepository,
    batch_id: str,
    preflight_payload: dict[str, Any],
    request: dict[str, Any],
    *,
    user_id: str,
    current: dt.datetime,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    raw_outcomes = request.get("outcomes") or []
    quotes = {
        str(item.get("code") or ""): item for item in preflight_payload.get("quotes") or []
    }
    codes = [str(item.get("code") or "") for item in raw_outcomes]
    if set(codes) != set(quotes) or len(codes) != len(set(codes)):
        raise BatchPurchaseExecutionValidationError(
            "成交结果必须逐只且仅覆盖执行前复核中的全部基金"
        )

    normalized: list[dict[str, Any]] = []
    transactions: dict[int, dict[str, Any]] = {}
    transaction_bindings: list[dict[str, Any]] = []
    for raw in raw_outcomes:
        code = str(raw.get("code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise BatchPurchaseExecutionValidationError("基金代码必须是 6 位数字")
        resolution = str(raw.get("resolution") or "")
        if resolution == "not_purchased":
            reason = str(raw.get("not_purchased_reason") or "")
            if reason not in NOT_PURCHASED_REASONS:
                raise BatchPurchaseExecutionValidationError(f"{code} 缺少有效的未申购原因")
            normalized.append({
                "code": code,
                "resolution": resolution,
                "not_purchased_reason": reason,
                "not_purchased_detail": str(raw.get("not_purchased_detail") or "").strip()[:200],
            })
            continue
        if resolution != "purchased":
            raise BatchPurchaseExecutionValidationError(f"{code} 必须选择已申购或未申购")

        transaction_id = int(raw.get("transaction_id") or 0)
        transaction = storage.get_portfolio_transaction(transaction_id, user_id=user_id)
        if transaction is None:
            raise BatchPurchaseExecutionValidationError(f"{code} 的真实申购流水不存在")
        if (
            transaction.get("asset_type") != "fund"
            or transaction.get("trade_type") != "buy"
            or str(transaction.get("code") or "") != code
        ):
            raise BatchPurchaseExecutionValidationError(f"{code} 选择的不是该基金真实买入流水")
        if storage.fund_switch_lifecycle_transaction_is_bound(
            transaction_id, event_type="purchase_recorded", user_id=user_id
        ):
            raise BatchPurchaseExecutionConflictError(f"{code} 的流水已绑定基金替换批次")
        existing_binding = repository.get_batch_purchase_transaction_binding(
            transaction_id, user_id=user_id
        )
        if existing_binding is not None and str(existing_binding.get("batch_id")) != batch_id:
            raise BatchPurchaseExecutionConflictError(f"{code} 的流水已绑定其他批量任务")

        submitted_at = _datetime(raw.get("purchase_submitted_at"), f"{code} 申购提交时间")
        quote = quotes[code]
        quoted_at = _datetime(quote.get("quoted_at"), f"{code} 平台报价时间")
        quote_expires_at = _datetime(quote.get("quote_expires_at"), f"{code} 报价失效时间")
        if not quoted_at <= submitted_at <= quote_expires_at:
            raise BatchPurchaseExecutionValidationError(f"{code} 申购提交时间不在平台报价有效期内")
        if submitted_at.astimezone(dt.timezone.utc) > current + dt.timedelta(minutes=5):
            raise BatchPurchaseExecutionValidationError(f"{code} 申购提交时间不能晚于当前时间")
        trade_date = _date(transaction.get("trade_date"), f"{code} 成交确认日期")
        local_today = current.astimezone(submitted_at.tzinfo).date()
        if (
            trade_date < submitted_at.date()
            or trade_date > local_today
            or trade_date > quoted_at.date() + dt.timedelta(days=MAX_CONFIRMATION_DAYS)
        ):
            raise BatchPurchaseExecutionValidationError(
                f"{code} 成交确认日期必须介于提交日、今天和报价后 30 天内"
            )
        snapshot = _transaction_snapshot(transaction)
        transaction_hash = _transaction_sha256(transaction)
        transactions[transaction_id] = snapshot
        transaction_bindings.append({
            "transaction_id": transaction_id,
            "transaction_sha256": transaction_hash,
        })
        normalized.append({
            "code": code,
            "resolution": resolution,
            "transaction_id": transaction_id,
            "purchase_submitted_at": submitted_at.isoformat(timespec="seconds"),
            "acknowledged_order_variance": bool(raw.get("acknowledged_order_variance")),
        })
    return normalized, transactions, transaction_bindings


def _purchase_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (item for item in reversed(events) if item.get("event_type") == "purchases_recorded"),
        None,
    )


def transaction_integrity_snapshot(
    repository: AgentRepository,
    batch_id: str,
    events: list[dict[str, Any]],
    *,
    user_id: str,
) -> dict[str, Any]:
    checked = 0
    failures: list[str] = []
    seen: set[int] = set()
    current_transactions = {
        int(item.get("id") or 0): item
        for item in storage.list_portfolio_transactions(user_id=user_id)
    }
    batch_bindings = {
        int(item.get("transaction_id") or 0): item
        for item in repository.list_batch_purchase_transaction_bindings(user_id=user_id)
    }
    for event in events:
        if event.get("event_type") != "purchases_recorded":
            continue
        for outcome in (event.get("payload") or {}).get("outcomes") or []:
            transaction = outcome.get("transaction")
            if not transaction:
                continue
            transaction_id = int(transaction.get("id") or 0)
            if transaction_id in seen:
                continue
            seen.add(transaction_id)
            checked += 1
            expected_hash = payload_sha256(transaction)
            current = current_transactions.get(transaction_id)
            binding = batch_bindings.get(transaction_id)
            if current is None:
                failures.append(f"交易流水 #{transaction_id} 已不存在")
            elif _transaction_sha256(current) != expected_hash:
                failures.append(f"交易流水 #{transaction_id} 内容哈希已变化")
            elif (
                binding is None
                or str(binding.get("batch_id") or "") != batch_id
                or str(binding.get("transaction_sha256") or "") != expected_hash
            ):
                failures.append(f"交易流水 #{transaction_id} 的批次占用绑定无效")
    return {"verified": not failures, "checked_count": checked, "failures": failures}


def _reconciliation_preview(
    purchase_payload: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any]:
    purchased = [
        item for item in purchase_payload.get("outcomes") or []
        if item.get("resolution") == "purchased"
    ]
    holdings = storage.list_holdings(user_id=user_id)
    relevant_holdings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    ledger_hashes: dict[str, str] = {}
    for outcome in purchased:
        code = str(outcome.get("code") or "")
        scoped_holdings = [
            item for item in holdings
            if item.get("asset_type") == "fund" and str(item.get("code") or "") == code
        ]
        relevant_holdings.extend({
            key: item.get(key)
            for key in (
                "id", "asset_type", "market", "code", "name", "amount", "cost",
                "profit", "profit_rate", "shares", "source", "updated_at",
            )
        } for item in scoped_holdings)
        confirmed_values = [_number(item.get("shares")) for item in scoped_holdings]
        confirmed_complete = bool(scoped_holdings and all(value is not None for value in confirmed_values))
        confirmed_shares = sum(value or 0.0 for value in confirmed_values) if confirmed_complete else None
        ledger = portfolio_review.remaining_lot_snapshot("fund", code, user_id=user_id)
        ledger_hash = payload_sha256(ledger)
        ledger_hashes[code] = ledger_hash
        position = ledger.get("position") or {}
        open_shares = _number(position.get("open_shares"))
        issues = ledger.get("integrity_issues") or []
        tolerance = max(
            1e-6,
            abs(confirmed_shares or open_shares or 0.0) * SHARE_TOLERANCE_RATIO,
        )
        matches = bool(
            confirmed_shares is not None
            and open_shares is not None
            and not issues
            and abs(confirmed_shares - open_shares) <= tolerance
        )
        row_blockers = []
        if not scoped_holdings:
            row_blockers.append("当前持仓中尚无该基金")
        elif not confirmed_complete:
            row_blockers.append("当前持仓缺少确认份额")
        if open_shares is None:
            row_blockers.append("FIFO 账本没有未平仓份额")
        if issues:
            row_blockers.append("FIFO 账本存在卖出超录或方向异常")
        if confirmed_shares is not None and open_shares is not None and not matches:
            row_blockers.append("当前确认份额与 FIFO 未平仓份额不一致")
        blockers.extend(f"{code} {item}" for item in row_blockers)
        rows.append({
            "code": code,
            "name": outcome.get("name") or code,
            "holding_record_count": len(scoped_holdings),
            "confirmed_shares": round(confirmed_shares, 8) if confirmed_shares is not None else None,
            "fifo_open_shares": round(open_shares, 8) if open_shares is not None else None,
            "share_tolerance": round(tolerance, 8),
            "shares_match": matches,
            "fifo_integrity_issue_count": len(issues),
            "ledger_sha256": ledger_hash,
            "blockers": row_blockers,
        })
    relevant_holdings.sort(key=lambda item: (str(item.get("code") or ""), int(item.get("id") or 0)))
    relevant_holdings_hash = payload_sha256(relevant_holdings)
    ledger_set_hash = payload_sha256(ledger_hashes)
    return {
        "ready": bool(purchased and not blockers and all(item.get("shares_match") for item in rows)),
        "purchased_fund_count": len(purchased),
        "matched_fund_count": sum(bool(item.get("shares_match")) for item in rows),
        "items": rows,
        "blockers": blockers,
        "relevant_holdings_sha256": relevant_holdings_hash,
        "portfolio_holdings_sha256": portfolio_exposure.holdings_sha256(holdings),
        "ledger_sha256_by_code": ledger_hashes,
        "ledger_set_sha256": ledger_set_hash,
    }


def record_batch_purchase_execution(
    repository: AgentRepository,
    batch: dict[str, Any],
    request: dict[str, Any],
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    current = _validated_now(now)
    expected_preflight_id = str(request.get("expected_preflight_event_id") or "")
    expected_preflight_hash = str(request.get("expected_preflight_event_hash") or "")
    preflight_event, preflight_payload = _preflight(
        repository,
        batch,
        expected_id=expected_preflight_id,
        expected_hash=expected_preflight_hash,
        user_id=user_id,
    )
    outcomes, transactions, transaction_bindings = _normalize_outcomes(
        repository,
        str(batch.get("id") or ""),
        preflight_payload,
        request,
        user_id=user_id,
        current=current,
    )
    request_payload = {
        "expected_preflight_event_id": expected_preflight_id,
        "expected_preflight_event_hash": expected_preflight_hash,
        "expected_previous_event_hash": (
            str(request.get("expected_previous_event_hash"))
            if request.get("expected_previous_event_hash") else None
        ),
        "outcomes": outcomes,
        "transaction_bindings": transaction_bindings,
    }
    request_hash = _sha256(request_payload)
    bindings = {
        "batch_id": str(batch.get("id") or ""),
        "batch_input_sha256": str(batch.get("input_hash") or ""),
        "preflight_event_id": str(preflight_event.get("id") or ""),
        "preflight_event_hash": str(preflight_event.get("event_hash") or ""),
        "preflight_payload_sha256": str(preflight_event.get("payload_sha256") or ""),
        "request_sha256": request_hash,
        "transaction_bindings": transaction_bindings,
    }
    try:
        payload = evaluate_batch_purchase_execution(
            preflight_payload,
            outcomes,
            transactions,
            bindings=bindings,
            generated_at=current.isoformat(timespec="seconds"),
        )
        return repository.append_batch_purchase_execution_event(
            str(batch.get("id") or ""),
            payload,
            user_id=user_id,
            actor_id=actor_id,
            expected_previous_event_hash=(
                str(request.get("expected_previous_event_hash"))
                if request.get("expected_previous_event_hash") else None
            ),
            transaction_bindings=transaction_bindings,
        )
    except BatchPurchaseExecutionValidationError:
        raise
    except KeyError as error:
        raise BatchPurchaseExecutionConflictError(str(error)) from error
    except ValueError as error:
        message = str(error)
        if "必须明确确认" in message or "超过" in message or "缺少" in message or "无效" in message:
            raise BatchPurchaseExecutionValidationError(message) from error
        raise BatchPurchaseExecutionConflictError(message) from error


def reconcile_batch_purchase_holdings(
    repository: AgentRepository,
    batch: dict[str, Any],
    request: dict[str, Any],
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    current = _validated_now(now)
    events = repository.list_batch_purchase_execution_events(
        str(batch.get("id") or ""), user_id=user_id
    )
    if not events:
        raise BatchPurchaseExecutionConflictError("尚未回填真实申购成交")
    latest = events[-1]
    expected_purchase_id = str(request.get("expected_purchase_event_id") or "")
    expected_purchase_hash = str(request.get("expected_purchase_event_hash") or "")
    if latest.get("event_type") == "holdings_reconciled":
        latest_bindings = (latest.get("payload") or {}).get("bindings") or {}
        if (
            str(latest_bindings.get("purchase_event_id") or "") == expected_purchase_id
            and str(latest_bindings.get("purchase_event_hash") or "") == expected_purchase_hash
        ):
            return latest, False
        raise BatchPurchaseExecutionConflictError("批次已经完成持仓对账")
    if (
        latest.get("event_type") != "purchases_recorded"
        or str(latest.get("id") or "") != expected_purchase_id
        or str(latest.get("event_hash") or "") != expected_purchase_hash
        or str(request.get("expected_previous_event_hash") or "") != expected_purchase_hash
    ):
        raise BatchPurchaseExecutionConflictError("真实申购成交事件已变化，请刷新后重试")
    purchase_payload = latest.get("payload") or {}
    if not any(
        item.get("resolution") == "purchased" for item in purchase_payload.get("outcomes") or []
    ):
        raise BatchPurchaseExecutionConflictError("本批次没有实际申购，无需进行持仓对账")
    audit = repository.verify_batch_purchase_execution_audit(
        str(batch.get("id") or ""), user_id=user_id
    )
    transaction_integrity = transaction_integrity_snapshot(
        repository, str(batch.get("id") or ""), events, user_id=user_id
    )
    if not audit.get("verified") or not transaction_integrity.get("verified"):
        raise BatchPurchaseExecutionConflictError("执行事件链或绑定交易流水完整性失败")
    preview = _reconciliation_preview(purchase_payload, user_id=user_id)
    if not preview.get("ready"):
        raise BatchPurchaseExecutionValidationError(
            "；".join(str(item) for item in (preview.get("blockers") or [])[:3])
            or "当前持仓尚未通过 FIFO 对账"
        )
    preflight_event = batch.get("purchase_preflight_event") or {}
    transaction_bindings = list(
        ((purchase_payload.get("bindings") or {}).get("transaction_bindings") or [])
    )
    request_payload = {
        "expected_purchase_event_id": expected_purchase_id,
        "expected_purchase_event_hash": expected_purchase_hash,
        "expected_previous_event_hash": expected_purchase_hash,
        "relevant_holdings_sha256": preview.get("relevant_holdings_sha256"),
        "ledger_set_sha256": preview.get("ledger_set_sha256"),
    }
    bindings = {
        "batch_id": str(batch.get("id") or ""),
        "batch_input_sha256": str(batch.get("input_hash") or ""),
        "preflight_event_id": str(preflight_event.get("id") or ""),
        "preflight_event_hash": str(preflight_event.get("event_hash") or ""),
        "preflight_payload_sha256": str(preflight_event.get("payload_sha256") or ""),
        "request_sha256": _sha256(request_payload),
        "purchase_event_id": expected_purchase_id,
        "purchase_event_hash": expected_purchase_hash,
        "purchase_payload_sha256": str(latest.get("payload_sha256") or ""),
        "relevant_holdings_sha256": preview.get("relevant_holdings_sha256"),
        "ledger_set_sha256": preview.get("ledger_set_sha256"),
        "transaction_bindings": transaction_bindings,
    }
    try:
        payload = evaluate_batch_purchase_reconciliation(
            purchase_payload,
            preview,
            bindings=bindings,
            generated_at=current.isoformat(timespec="seconds"),
        )
        return repository.append_batch_purchase_execution_event(
            str(batch.get("id") or ""),
            payload,
            user_id=user_id,
            actor_id=actor_id,
            expected_previous_event_hash=expected_purchase_hash,
            transaction_bindings=transaction_bindings,
        )
    except BatchPurchaseExecutionValidationError:
        raise
    except KeyError as error:
        raise BatchPurchaseExecutionConflictError(str(error)) from error
    except ValueError as error:
        raise BatchPurchaseExecutionConflictError(str(error)) from error


def decorate_batch_purchase_execution(
    repository: AgentRepository,
    batch: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any] | None:
    preflight_event = batch.get("purchase_preflight_event")
    if preflight_event is None:
        return None
    preflight_payload = preflight_event.get("payload") or {}
    batch_id = str(batch.get("id") or "")
    events = repository.list_batch_purchase_execution_events(batch_id, user_id=user_id)
    eligible = _eligible_transactions(
        repository, batch_id, preflight_payload, user_id=user_id
    )
    if not events:
        return {
            "status": "awaiting_execution_record",
            "blockers": [],
            "eligible_transactions_by_code": eligible,
            "outcomes": [],
            "decision_gate": {
                "execution_facts_recorded": False,
                "holdings_reconciliation_ready": False,
                "execution_authorized": False,
                "automatic_purchase_allowed": False,
            },
            "snapshot": None,
            "policy": "这里只回填销售平台已发生的真实结果，不发起、授权或模拟订单。",
        }

    latest = events[-1]
    audit = repository.verify_batch_purchase_execution_audit(batch_id, user_id=user_id)
    transaction_integrity = transaction_integrity_snapshot(
        repository, batch_id, events, user_id=user_id
    )
    if (
        not latest.get("integrity_verified")
        or not audit.get("verified")
        or not transaction_integrity.get("verified")
    ):
        return {
            "status": "integrity_failed",
            "blockers": [
                *([] if audit.get("verified") else ["批次执行事件内容哈希或审计链失败"]),
                *(transaction_integrity.get("failures") or []),
            ],
            "eligible_transactions_by_code": {},
            "decision_gate": {
                "execution_facts_recorded": False,
                "holdings_reconciliation_ready": False,
                "execution_authorized": False,
                "automatic_purchase_allowed": False,
            },
            "snapshot": {
                "id": latest.get("id"),
                "event_hash": latest.get("event_hash"),
                "created_at": latest.get("created_at"),
                "integrity_verified": False,
                "audit_chain_verified": bool(audit.get("verified")),
                "audit_event_count": audit.get("event_count"),
            },
        }

    purchase_event = _purchase_event(events)
    if purchase_event is None:
        return None
    purchase_payload = purchase_event.get("payload") or {}
    payload = copy.deepcopy(latest.get("payload") or {})
    payload["purchase_summary"] = copy.deepcopy(purchase_payload.get("summary") or {})
    payload["purchase_outcomes"] = copy.deepcopy(purchase_payload.get("outcomes") or [])
    preview = _reconciliation_preview(purchase_payload, user_id=user_id)
    if latest.get("event_type") == "holdings_reconciled":
        bindings = payload.get("bindings") or {}
        reconciliation_current = bool(
            bindings.get("relevant_holdings_sha256") == preview.get("relevant_holdings_sha256")
            and bindings.get("ledger_set_sha256") == preview.get("ledger_set_sha256")
            and preview.get("ready")
        )
        payload["status"] = (
            "completed_reconciled" if reconciliation_current
            else "completed_reconciliation_stale"
        )
        payload["current_reconciliation"] = preview
        payload["current_bindings"] = {
            "transactions_current": True,
            "reconciliation_current": reconciliation_current,
        }
        payload["blockers"] = [] if reconciliation_current else [
            "对账完成后相关持仓或 FIFO 账本已变化，请以当前持仓重新评估后续动作"
        ]
        payload["eligible_transactions_by_code"] = {}
    else:
        payload["reconciliation_preview"] = preview
        payload["current_bindings"] = {"transactions_current": True}
        payload["blockers"] = list(payload.get("blockers") or [])
        payload["eligible_transactions_by_code"] = eligible
    payload["transaction_integrity"] = transaction_integrity
    payload["snapshot"] = {
        "id": latest.get("id"),
        "revision": latest.get("sequence_no"),
        "event_type": latest.get("event_type"),
        "event_hash": latest.get("event_hash"),
        "payload_sha256": latest.get("payload_sha256"),
        "previous_hash": latest.get("previous_hash"),
        "created_at": latest.get("created_at"),
        "integrity_verified": True,
        "audit_chain_verified": True,
        "audit_event_count": audit.get("event_count"),
    }
    return payload
