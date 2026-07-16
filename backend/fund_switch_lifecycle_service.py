# -*- coding: utf-8 -*-
"""Audited fund replacement lifecycle from settled cash to outcome attribution."""

from __future__ import annotations

import copy
import datetime as dt
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fund_switch_execution_service
import funds
import portfolio_exposure
import portfolio_review
import storage
from database import INTEGRITY_ERRORS
from investment_policy import payload_sha256
from strategies.fund_switch_lifecycle import (
    CASH_TOLERANCE_YUAN,
    evaluate_attribution_snapshot,
    evaluate_holdings_reconciliation,
    evaluate_purchase_record,
    evaluate_purchase_requote,
    evaluate_redemption_settlement,
)
from strategies.fund_switch_cost import build_lot_binding


PURCHASE_QUOTE_TTL_HOURS = 24
MAX_CONFIRMATION_DAYS = 30
SHARE_TOLERANCE_RATIO = 0.001
TERMINAL_CASE_STATUSES = {
    "completed_attribution_available",
    "completed_attribution_blocked",
    "integrity_failed",
}
_CASE_START_LOCK = threading.RLock()


class LifecycleNotFoundError(LookupError):
    pass


class LifecycleConflictError(RuntimeError):
    pass


class LifecycleValidationError(ValueError):
    pass


def _append_lifecycle_event(
    payload: dict[str, Any],
    *,
    actor_id: str,
    user_id: str,
) -> dict[str, Any]:
    try:
        return storage.append_fund_switch_lifecycle_event(
            payload,
            actor_id=actor_id,
            user_id=user_id,
        )
    except INTEGRITY_ERRORS as error:
        raise LifecycleConflictError(
            "真实交易流水或批次事件已被其他请求绑定，请刷新后重试"
        ) from error
    except (LookupError, ValueError) as error:
        raise LifecycleConflictError(
            f"基金替换批次状态已变化，请刷新后重试：{error}"
        ) from error


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _datetime(value: Any, field: str) -> dt.datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = dt.datetime.fromisoformat(text)
    except ValueError as error:
        raise LifecycleValidationError(f"{field}必须是包含时区的 ISO 时间") from error
    if result.tzinfo is None:
        raise LifecycleValidationError(f"{field}必须包含时区")
    return result


def _date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value or "").strip())
    except ValueError as error:
        raise LifecycleValidationError(f"{field}必须是 YYYY-MM-DD") from error


def _validated_now(now: dt.datetime | None) -> dt.datetime:
    current = now or _utc_now()
    if current.tzinfo is None:
        raise LifecycleValidationError("服务时间必须包含时区")
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


def _review(
    holding_id: int,
    candidate_code: str,
    execution_review_id: str,
    execution_review_hash: str,
    user_id: str,
) -> dict[str, Any]:
    review = storage.get_fund_switch_execution_review(
        execution_review_id,
        user_id=user_id,
    )
    if review is None:
        raise LifecycleNotFoundError("执行前审查不存在或不属于当前用户")
    if (
        int(review.get("holding_id") or 0) != int(holding_id)
        or str(review.get("candidate_code") or "") != candidate_code
    ):
        raise LifecycleConflictError("执行前审查与当前持仓或候选基金不一致")
    audit = storage.verify_fund_switch_execution_audit(
        int(holding_id),
        candidate_code,
        user_id=user_id,
    )
    if (
        not review.get("integrity_verified")
        or not audit.get("verified")
        or str(review.get("review_hash") or "") != execution_review_hash
    ):
        raise LifecycleConflictError("执行前审查审计链或版本绑定已变化")
    if (
        review.get("status") != "ready_for_redemption_review"
        or not ((review.get("payload") or {}).get("decision_gate") or {}).get(
            "redemption_review_ready"
        )
    ):
        raise LifecycleConflictError("该执行前审查从未通过人工赎回复核门禁")
    return review


def _event(events: list[dict], event_type: str, *, latest: bool = True) -> dict | None:
    matches = [item for item in events if item.get("event_type") == event_type]
    if not matches:
        return None
    return matches[-1] if latest else matches[0]


def _identity(events: list[dict]) -> dict[str, Any]:
    if not events:
        raise LifecycleNotFoundError("基金替换批次不存在")
    first = events[0]
    payload = first.get("payload") or {}
    return {
        "case_id": first.get("case_id"),
        "holding_id": int(first.get("holding_id") or 0),
        "selected_code": str(first.get("selected_code") or ""),
        "candidate_code": str(first.get("candidate_code") or ""),
        "candidate_name": str(payload.get("candidate_name") or first.get("candidate_code") or ""),
        "execution_review_id": str(first.get("execution_review_id") or ""),
        "execution_review_hash": str(first.get("execution_review_hash") or ""),
    }


def _transaction_option(item: dict[str, Any]) -> dict[str, Any]:
    shares = _number(item.get("shares")) or 0
    price = _number(item.get("unit_price")) or 0
    fee = _number(item.get("fee")) or 0
    gross = shares * price
    cash = gross - fee if item.get("trade_type") == "sell" else gross + fee
    return {
        "id": item.get("id"),
        "trade_date": item.get("trade_date"),
        "trade_type": item.get("trade_type"),
        "code": item.get("code"),
        "name": item.get("name") or item.get("code"),
        "shares": _round(shares, 8),
        "unit_price": _round(price, 8),
        "fee_yuan": _round(fee),
        "gross_yuan": _round(gross),
        "cash_amount_yuan": _round(cash),
        "source": item.get("source"),
    }


def _eligible_redemptions(
    review: dict[str, Any] | None,
    selected_code: str,
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    if review is None:
        return []
    stages = (review.get("payload") or {}).get("manual_stages") or []
    stage = next((item for item in stages if item.get("id") == "redemption_submission"), {})
    expected_shares = _number(stage.get("shares"))
    if expected_shares is None:
        quote_id = str(
            (((review.get("payload") or {}).get("bindings") or {}).get("quote_event_id"))
            or ""
        )
        quote = storage.get_fund_switch_quote_event(quote_id, user_id=user_id)
        cost_review = (
            storage.get_fund_switch_cost_review(
                str((quote or {}).get("review_id") or ""),
                user_id=user_id,
            )
            if quote else None
        )
        expected_shares = _number(
            ((((cost_review or {}).get("payload") or {}).get("ledger_binding") or {}).get("confirmed_shares"))
        )
    if expected_shares is None or expected_shares <= 0:
        return []
    options = []
    for item in storage.list_portfolio_transactions(user_id=user_id):
        if (
            item.get("asset_type") != "fund"
            or item.get("trade_type") != "sell"
            or str(item.get("code") or "") != selected_code
            or storage.fund_switch_lifecycle_transaction_is_bound(
                int(item.get("id") or 0),
                event_type="redemption_settled",
                user_id=user_id,
            )
        ):
            continue
        shares = _number(item.get("shares"))
        tolerance = max(1e-6, abs(expected_shares or 0) * SHARE_TOLERANCE_RATIO)
        if expected_shares is not None and (
            shares is None or abs(shares - expected_shares) > tolerance
        ):
            continue
        options.append(_transaction_option(item))
    return options


def _eligible_purchases(
    candidate_code: str,
    settlement_date: str,
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    options = []
    for item in storage.list_portfolio_transactions(user_id=user_id):
        if (
            item.get("asset_type") != "fund"
            or item.get("trade_type") != "buy"
            or str(item.get("code") or "") != candidate_code
            or str(item.get("trade_date") or "") < settlement_date
            or storage.fund_switch_lifecycle_transaction_is_bound(
                int(item.get("id") or 0),
                event_type="purchase_recorded",
                user_id=user_id,
            )
        ):
            continue
        options.append(_transaction_option(item))
    return options


def _lot_snapshot_from_rows(
    rows: list[dict[str, Any]],
    asset_type: str,
    code: str,
) -> dict[str, Any]:
    positions, issues = portfolio_review._calculate_fifo(
        rows,
        include_remaining_lots=True,
    )
    position = next(
        (
            item for item in positions
            if item.get("asset_type") == asset_type
            and str(item.get("code") or "") == code
        ),
        None,
    )
    scoped_issues = [
        item for item in issues
        if item.get("asset_type") == asset_type
        and str(item.get("code") or "") == code
    ]
    return {
        "position": position,
        "remaining_lots": (position or {}).get("remaining_lots") or [],
        "integrity_issues": scoped_issues,
        "transaction_count": int((position or {}).get("transaction_count") or 0),
    }


def _reconciliation_preview(
    identity: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any]:
    holdings = storage.list_holdings(user_id=user_id)
    selected_code = identity["selected_code"]
    candidate_code = identity["candidate_code"]
    selected_ledger = portfolio_review.remaining_lot_snapshot(
        "fund", selected_code, user_id=user_id
    )
    candidate_ledger = portfolio_review.remaining_lot_snapshot(
        "fund", candidate_code, user_id=user_id
    )
    selected_position = selected_ledger.get("position") or {}
    candidate_position = candidate_ledger.get("position") or {}
    selected_open = _number(selected_position.get("open_shares")) or 0
    candidate_open = _number(candidate_position.get("open_shares"))
    selected_holding = next(
        (
            item for item in holdings
            if item.get("asset_type") == "fund"
            and str(item.get("code") or "") == selected_code
        ),
        None,
    )
    candidate_holding = next(
        (
            item for item in holdings
            if item.get("asset_type") == "fund"
            and str(item.get("code") or "") == candidate_code
        ),
        None,
    )
    selected_confirmed = _number((selected_holding or {}).get("shares"))
    candidate_confirmed = _number((candidate_holding or {}).get("shares"))
    selected_ledger_clear = selected_open <= max(1e-6, selected_open * SHARE_TOLERANCE_RATIO)
    selected_holding_clear = selected_holding is None or (
        selected_confirmed is not None
        and selected_confirmed <= max(1e-6, abs(selected_confirmed) * SHARE_TOLERANCE_RATIO)
    )
    candidate_tolerance = max(
        1e-6,
        abs(candidate_confirmed or candidate_open or 0) * SHARE_TOLERANCE_RATIO,
    )
    candidate_matches = bool(
        candidate_holding is not None
        and candidate_confirmed is not None
        and candidate_open is not None
        and abs(candidate_confirmed - candidate_open) <= candidate_tolerance
    )
    ledger_clean = not (
        (selected_ledger.get("integrity_issues") or [])
        + (candidate_ledger.get("integrity_issues") or [])
    )
    reasons = []
    if not selected_ledger_clear:
        reasons.append("原基金 FIFO 账本仍有剩余份额")
    if not selected_holding_clear:
        reasons.append("当前确认持仓仍包含原基金份额")
    if not candidate_matches:
        reasons.append("候选基金当前确认份额与 FIFO 账本不一致")
    if not ledger_clean:
        reasons.append("原基金或候选基金交易流水存在份额缺口")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "selected": {
            "code": selected_code,
            "ledger_open_shares": _round(selected_open, 8),
            "confirmed_shares": _round(selected_confirmed, 8),
            "holding_present": selected_holding is not None,
        },
        "candidate": {
            "code": candidate_code,
            "ledger_open_shares": _round(candidate_open, 8),
            "confirmed_shares": _round(candidate_confirmed, 8),
            "holding_present": candidate_holding is not None,
        },
        "holdings_sha256": portfolio_exposure.holdings_sha256(holdings),
        "selected_ledger_sha256": payload_sha256(selected_ledger),
        "candidate_ledger_sha256": payload_sha256(candidate_ledger),
    }


def _reconciliation_binding_state(
    reconciliation_event: dict | None,
    identity: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any]:
    if reconciliation_event is None:
        return {
            "current": False,
            "preview": None,
            "reasons": ["尚未完成当前持仓与 FIFO 账本对账"],
        }
    preview = _reconciliation_preview(identity, user_id=user_id)
    bindings = (reconciliation_event.get("payload") or {}).get("bindings") or {}
    reasons = list(preview.get("reasons") or [])
    if preview.get("selected_ledger_sha256") != bindings.get("selected_ledger_sha256"):
        reasons.append("对账后原基金交易账本发生变化")
    if preview.get("candidate_ledger_sha256") != bindings.get("candidate_ledger_sha256"):
        reasons.append("对账后候选基金交易账本发生变化")
    return {
        "current": not reasons,
        "preview": preview,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _event_transaction_integrity(
    event: dict | None,
    *,
    transaction_id_field: str,
    transaction_hash_field: str,
    user_id: str,
) -> tuple[bool, str | None]:
    if event is None:
        return True, None
    bindings = (event.get("payload") or {}).get("bindings") or {}
    transaction_id = int(bindings.get(transaction_id_field) or 0)
    expected_hash = str(bindings.get(transaction_hash_field) or "")
    transaction = storage.get_portfolio_transaction(transaction_id, user_id=user_id)
    if transaction is None:
        return False, f"{transaction_id_field}_missing"
    if _transaction_sha256(transaction) != expected_hash:
        return False, f"{transaction_id_field}_changed"
    return True, None


def _purchase_quote_binding_state(
    quote_event: dict,
    *,
    user_id: str,
    now: dt.datetime,
) -> dict[str, Any]:
    payload = quote_event.get("payload") or {}
    bindings = payload.get("bindings") or {}
    quote = payload.get("purchase_quote") or {}
    try:
        expires = _datetime(quote.get("quote_expires_at"), "申购报价失效时间")
    except LifecycleValidationError:
        expires = None
    profile = storage.get_investment_profile(user_id=user_id)
    holdings = storage.list_holdings(user_id=user_id)
    return {
        "quote_current": bool(expires and now <= expires.astimezone(dt.timezone.utc)),
        "profile_current": bool(
            str(profile.get("profile_version_id") or "")
            == str(bindings.get("profile_version_id") or "")
            and str(profile.get("payload_sha256") or "")
            == str(bindings.get("profile_payload_sha256") or "")
            and profile.get("configured")
        ),
        "holdings_current": (
            portfolio_exposure.holdings_sha256(holdings)
            == bindings.get("current_holdings_sha256")
        ),
    }


def decorate_case(
    case_id: str,
    *,
    user_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _validated_now(now)
    events = storage.list_fund_switch_lifecycle_events(case_id, user_id=user_id)
    identity = _identity(events)
    audit = storage.verify_fund_switch_lifecycle_audit(case_id, user_id=user_id)
    settlement = _event(events, "redemption_settled", latest=False)
    quote = _event(events, "purchase_requoted")
    purchase = _event(events, "purchase_recorded", latest=False)
    reconciliation = _event(events, "holdings_reconciled", latest=False)
    attribution = _event(events, "attribution_snapshot")
    reconciliation_bindings = _reconciliation_binding_state(
        reconciliation,
        identity,
        user_id=user_id,
    ) if reconciliation is not None else {
        "current": False,
        "preview": None,
        "reasons": [],
    }
    redemption_ok, redemption_reason = _event_transaction_integrity(
        settlement,
        transaction_id_field="redemption_transaction_id",
        transaction_hash_field="redemption_transaction_sha256",
        user_id=user_id,
    )
    purchase_ok, purchase_reason = _event_transaction_integrity(
        purchase,
        transaction_id_field="purchase_transaction_id",
        transaction_hash_field="purchase_transaction_sha256",
        user_id=user_id,
    )
    integrity_verified = bool(
        audit.get("verified") and redemption_ok and purchase_ok
    )
    quote_bindings = (
        _purchase_quote_binding_state(quote, user_id=user_id, now=current)
        if quote is not None and purchase is None
        else {"quote_current": False, "profile_current": False, "holdings_current": False}
    )
    if not integrity_verified:
        status = "integrity_failed"
        reason = audit.get("reason") or redemption_reason or purchase_reason
    elif reconciliation is not None:
        if attribution is None:
            status = (
                "completed_attribution_pending"
                if reconciliation_bindings.get("current")
                else "completed_attribution_blocked"
            )
        else:
            status = str(attribution.get("status") or "completed_attribution_blocked")
        reason = (
            status
            if reconciliation_bindings.get("current") or attribution is not None
            else "post_reconciliation_ledger_changed"
        )
    elif purchase is not None:
        status = "purchase_recorded_reconciliation_pending"
        reason = status
    elif quote is None:
        status = "settled_purchase_requote_required"
        reason = status
    elif quote.get("status") != "ready_for_manual_purchase_review":
        status = "purchase_requote_blocked"
        reason = str((((quote.get("payload") or {}).get("decision_gate") or {}).get("reason")) or status)
    elif not quote_bindings.get("quote_current"):
        status = "purchase_requote_expired"
        reason = "post_settlement_purchase_quote_expired"
    elif not quote_bindings.get("profile_current") or not quote_bindings.get("holdings_current"):
        status = "purchase_requote_superseded"
        reason = next(
            key for key in ("profile_current", "holdings_current")
            if not quote_bindings.get(key)
        )
    else:
        status = "ready_for_manual_purchase_review"
        reason = "all_post_settlement_purchase_gates_passed"

    settlement_payload = (settlement or {}).get("payload") or {}
    settlement_date = str(
        (settlement_payload.get("redemption") or {}).get("settled_on") or ""
    )
    preview = (
        reconciliation_bindings.get("preview")
        if reconciliation is not None
        else _reconciliation_preview(identity, user_id=user_id) if purchase else None
    )
    manual_purchase_ready = bool(
        status == "ready_for_manual_purchase_review" and integrity_verified
    )
    return {
        **identity,
        "status": status,
        "reason": reason,
        "revision": len(events),
        "settlement": (settlement_payload.get("redemption") or None),
        "purchase_quote": (((quote or {}).get("payload") or {}).get("purchase_quote") or None),
        "purchase": (((purchase or {}).get("payload") or {}).get("purchase") or None),
        "reconciliation": (((reconciliation or {}).get("payload") or {}).get("reconciliation") or None),
        "reconciliation_preview": preview,
        "attribution": (((attribution or {}).get("payload") or {}).get("attribution") or None),
        "attribution_blockers": (
            reconciliation_bindings.get("reasons") or []
            if reconciliation is not None and not reconciliation_bindings.get("current")
            else []
        ),
        "gates": (((quote or {}).get("payload") or {}).get("gates") or []),
        "events": [
            {
                "id": item.get("id"),
                "revision": item.get("sequence_no"),
                "event_type": item.get("event_type"),
                "status": item.get("status"),
                "generated_at": (item.get("payload") or {}).get("generated_at"),
                "event_hash": item.get("event_hash"),
            }
            for item in events
        ],
        "eligible_purchase_transactions": (
            _eligible_purchases(
                identity["candidate_code"], settlement_date, user_id=user_id
            )
            if settlement and purchase is None else []
        ),
        "decision_gate": {
            "settlement_confirmed": settlement is not None and integrity_verified,
            "manual_purchase_review_ready": manual_purchase_ready,
            "purchase_recorded": purchase is not None and integrity_verified,
            "holdings_reconciled": reconciliation is not None and integrity_verified,
            "attribution_refresh_ready": bool(
                reconciliation is not None
                and reconciliation_bindings.get("current")
                and integrity_verified
            ),
            "historical_attribution_available": bool(
                attribution
                and (((attribution.get("payload") or {}).get("attribution") or {}).get("status"))
                == "available"
                and integrity_verified
            ),
            "execution_authorized": False,
            "automatic_redemption_allowed": False,
            "automatic_purchase_allowed": False,
            "reason": reason,
        },
        "integrity": {
            "verified": integrity_verified,
            "audit_chain_verified": bool(audit.get("verified")),
            "redemption_transaction_current": redemption_ok,
            "purchase_transaction_current": purchase_ok,
            "purchase_quote_bindings": quote_bindings,
            "reconciliation_bindings": reconciliation_bindings,
            "event_count": audit.get("event_count"),
            "chain_head": audit.get("chain_head"),
        },
        "policy": (
            "替换批次只追踪用户已经确认的真实流水和公开净值；"
            "人工复核状态不是下单授权，历史归因不是未来盈利承诺。"
        ),
    }


def get_candidate_context(
    holding_id: int,
    candidate_code: str,
    *,
    user_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    candidate_code = str(candidate_code or "").strip()
    heads = storage.list_fund_switch_lifecycle_case_heads(
        user_id=user_id,
        holding_id=int(holding_id),
        candidate_code=candidate_code,
        limit=1,
    )
    case = (
        decorate_case(heads[0]["case_id"], user_id=user_id, now=now)
        if heads else None
    )
    if case and case.get("status") not in TERMINAL_CASE_STATUSES:
        return {
            "status": "available",
            "case": case,
            "eligible_redemption_transactions": [],
            "can_start_new": False,
        }
    review_rows = storage.list_fund_switch_execution_reviews(
        holding_id=int(holding_id),
        candidate_code=candidate_code,
        user_id=user_id,
        limit=1,
    )
    review = review_rows[0] if review_rows else None
    review_is_new = bool(
        review
        and str(review.get("id") or "")
        != str((case or {}).get("execution_review_id") or "")
    )
    selected_code = str((review or {}).get("selected_code") or "")
    execution_ready = bool(
        review_is_new
        and review.get("status") == "ready_for_redemption_review"
        and review.get("integrity_verified")
    )
    eligible = (
        _eligible_redemptions(review, selected_code, user_id=user_id)
        if execution_ready else []
    )
    return {
        "status": "terminal" if case else "not_started",
        "case": case,
        "eligible_redemption_transactions": eligible,
        "execution_review_id": (review or {}).get("id") if review_is_new else None,
        "execution_review_hash": (review or {}).get("review_hash") if review_is_new else None,
        "execution_review_ready": execution_ready,
        "can_start_new": bool(case and execution_ready),
        "policy": "只有通过审计的执行前审查和匹配全部确认份额的真实赎回流水才能启动批次。",
    }


def create_redemption_settlement(
    holding_id: int,
    candidate_code: str,
    request: dict[str, Any],
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    with _CASE_START_LOCK:
        return _create_redemption_settlement(
            holding_id,
            candidate_code,
            request,
            user_id=user_id,
            actor_id=actor_id,
            now=now,
        )


def _create_redemption_settlement(
    holding_id: int,
    candidate_code: str,
    request: dict[str, Any],
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _validated_now(now)
    candidate_code = str(candidate_code or "").strip()
    if not re.fullmatch(r"\d{6}", candidate_code):
        raise LifecycleValidationError("候选基金代码必须为 6 位数字")
    execution_review_id = str(request.get("expected_execution_review_id") or "").strip()
    execution_review_hash = str(request.get("expected_execution_review_hash") or "").strip()
    existing_heads = storage.list_fund_switch_lifecycle_case_heads(
        user_id=user_id,
        holding_id=int(holding_id),
        candidate_code=candidate_code,
        limit=100,
    )
    for head in existing_heads:
        existing_case = decorate_case(head["case_id"], user_id=user_id, now=current)
        if existing_case.get("status") not in TERMINAL_CASE_STATUSES:
            raise LifecycleConflictError("该持仓与候选基金已有进行中的替换批次")
        if existing_case.get("execution_review_id") == execution_review_id:
            raise LifecycleConflictError("旧替换批次使用过的执行前审查不能重复启动新批次")
    review = _review(
        holding_id,
        candidate_code,
        execution_review_id,
        execution_review_hash,
        user_id,
    )
    selected_code = str(review.get("selected_code") or "")
    transaction_id = int(request.get("redemption_transaction_id") or 0)
    transaction = storage.get_portfolio_transaction(transaction_id, user_id=user_id)
    if transaction is None:
        raise LifecycleNotFoundError("赎回交易流水不存在或不属于当前用户")
    if (
        transaction.get("asset_type") != "fund"
        or transaction.get("trade_type") != "sell"
        or str(transaction.get("code") or "") != selected_code
    ):
        raise LifecycleValidationError("所选流水不是当前基金的真实赎回记录")
    if storage.fund_switch_lifecycle_transaction_is_bound(
        transaction_id,
        event_type="redemption_settled",
        user_id=user_id,
    ):
        raise LifecycleConflictError("该赎回流水已绑定其他基金替换批次")

    quote_id = str(((review.get("payload") or {}).get("bindings") or {}).get("quote_event_id") or "")
    quote = storage.get_fund_switch_quote_event(quote_id, user_id=user_id)
    if quote is None or not quote.get("integrity_verified"):
        raise LifecycleConflictError("执行前审查绑定的平台报价事件不可用")
    cost_review = storage.get_fund_switch_cost_review(
        str(quote.get("review_id") or ""),
        user_id=user_id,
    )
    if cost_review is None or not cost_review.get("integrity_verified"):
        raise LifecycleConflictError("平台报价绑定的 FIFO 成本快照不可用")
    stages = (review.get("payload") or {}).get("manual_stages") or []
    stage = next((item for item in stages if item.get("id") == "redemption_submission"), {})
    expected_shares = _number(stage.get("shares"))
    if expected_shares is None:
        expected_shares = _number(
            (((cost_review.get("payload") or {}).get("ledger_binding") or {}).get("confirmed_shares"))
        )
    actual_shares = _number(transaction.get("shares"))
    tolerance = max(1e-6, abs(expected_shares or 0) * SHARE_TOLERANCE_RATIO)
    if (
        expected_shares is None
        or actual_shares is None
        or abs(expected_shares - actual_shares) > tolerance
    ):
        raise LifecycleValidationError("赎回流水份额未覆盖执行前审查绑定的全部确认份额")

    platform_quote = (quote.get("payload") or {}).get("platform_quote") or {}
    quoted_at = _datetime(platform_quote.get("quoted_at"), "赎回报价时间")
    quote_expires = _datetime(platform_quote.get("quote_expires_at"), "赎回报价失效时间")
    submitted_at = _datetime(request.get("redemption_submitted_at"), "赎回提交时间")
    if not quoted_at <= submitted_at <= quote_expires:
        raise LifecycleValidationError("赎回提交时间不在绑定的平台报价有效期内")
    if submitted_at.astimezone(dt.timezone.utc) > current + dt.timedelta(minutes=5):
        raise LifecycleValidationError("赎回提交时间不能晚于当前时间")
    trade_date = _date(transaction.get("trade_date"), "赎回确认日期")
    settled_on = _date(request.get("settled_on"), "实际到账日期")
    if trade_date < submitted_at.date():
        raise LifecycleValidationError("赎回确认日期不能早于赎回提交日期")
    current_local_date = current.astimezone(submitted_at.tzinfo).date()
    if settled_on < trade_date or settled_on > current_local_date:
        raise LifecycleValidationError("实际到账日期必须介于赎回确认日与今天之间")
    actual_received = _number(request.get("actual_received_yuan"))
    if actual_received is None or actual_received <= 0:
        raise LifecycleValidationError("实际到账金额必须大于 0")

    rows_before_redemption = [
        item for item in storage.list_portfolio_transactions(user_id=user_id)
        if int(item.get("id") or 0) != transaction_id
    ]
    before_snapshot = _lot_snapshot_from_rows(
        rows_before_redemption,
        "fund",
        selected_code,
    )
    before_binding = build_lot_binding(before_snapshot, expected_shares)
    expected_binding = (cost_review.get("payload") or {}).get("ledger_binding") or {}
    if (
        before_binding.get("schema_version") != expected_binding.get("schema_version")
        or before_binding.get("payload_sha256") != expected_binding.get("payload_sha256")
    ):
        raise LifecycleConflictError("执行前审查后账本除本次赎回外发生变化，请重新核对批次")

    case_id = f"fund_switch_case_{uuid.uuid4().hex}"
    bindings = {
        "execution_review_id": execution_review_id,
        "execution_review_hash": execution_review_hash,
        "quote_event_id": quote.get("id"),
        "quote_event_hash": quote.get("event_hash"),
        "redemption_transaction_id": transaction_id,
        "redemption_transaction_sha256": _transaction_sha256(transaction),
        "purchase_transaction_id": None,
        "purchase_transaction_sha256": "",
        "redemption_submitted_at": submitted_at.isoformat(timespec="seconds"),
        "pre_redemption_ledger_sha256": before_binding.get("payload_sha256"),
    }
    try:
        payload = evaluate_redemption_settlement(
            case_id=case_id,
            holding_id=int(holding_id),
            selected_code=selected_code,
            candidate_code=candidate_code,
            candidate_name=str((review.get("payload") or {}).get("candidate_name") or candidate_code),
            execution_review=review,
            redemption_transaction=transaction,
            settled_on=settled_on.isoformat(),
            actual_received_yuan=actual_received,
            acknowledged_quote_variance=bool(request.get("acknowledged_quote_variance")),
            bindings=bindings,
            generated_at=current.isoformat(timespec="seconds"),
        )
    except ValueError as error:
        raise LifecycleValidationError(str(error)) from error
    _append_lifecycle_event(
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return get_candidate_context(
        holding_id, candidate_code, user_id=user_id, now=current
    )


def create_purchase_requote(
    case_id: str,
    request: dict[str, Any],
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _validated_now(now)
    events = storage.list_fund_switch_lifecycle_events(case_id, user_id=user_id)
    identity = _identity(events)
    if _event(events, "purchase_recorded", latest=False):
        raise LifecycleConflictError("该替换批次已经回填实际申购成交")
    settlement = _event(events, "redemption_settled", latest=False)
    if settlement is None:
        raise LifecycleConflictError("尚未确认真实赎回到账")
    if not request.get("acknowledged_platform_quote"):
        raise LifecycleValidationError("必须确认申购金额和费用来自到账后的销售平台页面")
    quoted_at = _datetime(request.get("quoted_at"), "申购报价时间")
    quoted_utc = quoted_at.astimezone(dt.timezone.utc)
    if quoted_utc > current + dt.timedelta(minutes=5):
        raise LifecycleValidationError("申购报价时间不能晚于当前时间")
    if current - quoted_utc > dt.timedelta(hours=PURCHASE_QUOTE_TTL_HOURS):
        raise LifecycleValidationError("申购报价已超过 24 小时，请重新从平台确认")
    expires = quoted_at + dt.timedelta(hours=PURCHASE_QUOTE_TTL_HOURS)
    confirmation_date = _date(
        request.get("expected_confirmation_date"), "预计申购确认日期"
    )
    if not quoted_at.date() <= confirmation_date <= quoted_at.date() + dt.timedelta(days=MAX_CONFIRMATION_DAYS):
        raise LifecycleValidationError("预计申购确认日期必须在报价日起 30 天内")
    order_amount = _number(request.get("candidate_order_amount_yuan"))
    entry_fee = _number(request.get("candidate_entry_fee_yuan"))
    if order_amount is None or order_amount <= 0:
        raise LifecycleValidationError("候选申购金额必须大于 0")
    if entry_fee is None or entry_fee < 0 or entry_fee >= order_amount:
        raise LifecycleValidationError("候选申购费必须大于等于 0 且小于申购金额")
    settled_cash = _number(
        (((settlement.get("payload") or {}).get("redemption") or {}).get("actual_received_yuan"))
    )
    if settled_cash is None or order_amount > settled_cash + CASH_TOLERANCE_YUAN:
        raise LifecycleValidationError("候选申购金额不能超过本批次真实到账资金")

    holdings = storage.list_holdings(user_id=user_id)
    profile = storage.get_investment_profile(user_id=user_id)
    candidate_net = order_amount - entry_fee
    projected = fund_switch_execution_service._project_holdings(
        holdings,
        selected_holding_id=identity["holding_id"],
        candidate_code=identity["candidate_code"],
        candidate_name=identity["candidate_name"],
        candidate_net_amount=candidate_net,
        generated_at=current.isoformat(timespec="seconds"),
    )
    projected_hash = portfolio_exposure.holdings_sha256(projected)
    raw_sources, failed_sources, disclosure_hashes = (
        fund_switch_execution_service._load_disclosures(projected)
    )
    max_age_days = int(os.environ.get(
        "PORTFOLIO_EXPOSURE_MAX_AGE_DAYS",
        portfolio_exposure.DEFAULT_MAX_AGE_DAYS,
    ))
    exposure = portfolio_exposure.build_exposure_snapshot(
        projected,
        raw_sources,
        target_code=identity["candidate_code"],
        failed_sources=failed_sources,
        profile_version_id=profile.get("profile_version_id"),
        observed_on=current.date(),
        max_age_days=max_age_days,
    )
    market_profile = fund_switch_execution_service._market_profile(
        identity["candidate_code"]
    )
    settlement_bindings = (settlement.get("payload") or {}).get("bindings") or {}
    bindings = {
        "execution_review_id": identity["execution_review_id"],
        "execution_review_hash": identity["execution_review_hash"],
        "redemption_transaction_id": settlement_bindings.get("redemption_transaction_id"),
        "redemption_transaction_sha256": settlement_bindings.get("redemption_transaction_sha256"),
        "purchase_transaction_id": None,
        "purchase_transaction_sha256": "",
        "settlement_event_id": settlement.get("id"),
        "settlement_event_hash": settlement.get("event_hash"),
        "profile_version_id": str(profile.get("profile_version_id") or ""),
        "profile_payload_sha256": str(profile.get("payload_sha256") or ""),
        "current_holdings_sha256": portfolio_exposure.holdings_sha256(holdings),
        "projected_holdings_sha256": projected_hash,
        "market_profile_sha256": payload_sha256(market_profile),
        "projected_exposure_sha256": payload_sha256(exposure),
        "fund_disclosure_sha256": disclosure_hashes,
    }
    payload = evaluate_purchase_requote(
        case_id=identity["case_id"],
        holding_id=identity["holding_id"],
        selected_code=identity["selected_code"],
        candidate_code=identity["candidate_code"],
        candidate_name=identity["candidate_name"],
        settlement_event=settlement,
        quoted_at=quoted_at.isoformat(timespec="seconds"),
        quote_expires_at=expires.isoformat(timespec="seconds"),
        expected_confirmation_date=confirmation_date.isoformat(),
        platform_name=str(request.get("platform_name") or "").strip()[:80],
        order_amount_yuan=order_amount,
        entry_fee_yuan=entry_fee,
        purchase_available=bool(request.get("candidate_purchase_available")),
        platform_quote_acknowledged=True,
        profile=profile,
        market_profile=market_profile,
        projected_holdings=projected,
        projected_exposure=exposure,
        bindings=bindings,
        generated_at=current.isoformat(timespec="seconds"),
    )
    _append_lifecycle_event(
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return decorate_case(case_id, user_id=user_id, now=current)


def record_purchase(
    case_id: str,
    request: dict[str, Any],
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _validated_now(now)
    decorated = decorate_case(case_id, user_id=user_id, now=current)
    if decorated.get("status") != "ready_for_manual_purchase_review":
        raise LifecycleConflictError("当前申购重报价已过期、被替代或未通过全部门禁")
    events = storage.list_fund_switch_lifecycle_events(case_id, user_id=user_id)
    identity = _identity(events)
    settlement = _event(events, "redemption_settled", latest=False)
    quote = _event(events, "purchase_requoted")
    expected_quote_id = str(request.get("expected_purchase_quote_event_id") or "")
    expected_quote_hash = str(request.get("expected_purchase_quote_event_hash") or "")
    if (
        quote is None
        or quote.get("id") != expected_quote_id
        or quote.get("event_hash") != expected_quote_hash
    ):
        raise LifecycleConflictError("到账后申购报价已变化，请刷新后重新核对")
    transaction_id = int(request.get("purchase_transaction_id") or 0)
    transaction = storage.get_portfolio_transaction(transaction_id, user_id=user_id)
    if transaction is None:
        raise LifecycleNotFoundError("申购交易流水不存在或不属于当前用户")
    if (
        transaction.get("asset_type") != "fund"
        or transaction.get("trade_type") != "buy"
        or str(transaction.get("code") or "") != identity["candidate_code"]
    ):
        raise LifecycleValidationError("所选流水不是候选基金的真实申购记录")
    if storage.fund_switch_lifecycle_transaction_is_bound(
        transaction_id,
        event_type="purchase_recorded",
        user_id=user_id,
    ):
        raise LifecycleConflictError("该申购流水已绑定其他基金替换批次")
    submitted_at = _datetime(request.get("purchase_submitted_at"), "申购提交时间")
    quote_payload = (quote.get("payload") or {}).get("purchase_quote") or {}
    quoted_at = _datetime(quote_payload.get("quoted_at"), "申购报价时间")
    expires = _datetime(quote_payload.get("quote_expires_at"), "申购报价失效时间")
    if not quoted_at <= submitted_at <= expires:
        raise LifecycleValidationError("申购提交时间不在到账后报价有效期内")
    if submitted_at.astimezone(dt.timezone.utc) > current + dt.timedelta(minutes=5):
        raise LifecycleValidationError("申购提交时间不能晚于当前时间")
    confirmation_date = _date(transaction.get("trade_date"), "申购确认日期")
    current_local_date = current.astimezone(submitted_at.tzinfo).date()
    if confirmation_date < submitted_at.date() or confirmation_date > current_local_date:
        raise LifecycleValidationError("申购确认日期必须介于申购提交日与今天之间")
    settled_cash = _number(
        ((((settlement or {}).get("payload") or {}).get("redemption") or {}).get("actual_received_yuan"))
    )
    if settled_cash is None:
        raise LifecycleConflictError("真实到账金额绑定缺失")
    settlement_bindings = (settlement.get("payload") or {}).get("bindings") or {}
    bindings = {
        "execution_review_id": identity["execution_review_id"],
        "execution_review_hash": identity["execution_review_hash"],
        "redemption_transaction_id": settlement_bindings.get("redemption_transaction_id"),
        "redemption_transaction_sha256": settlement_bindings.get("redemption_transaction_sha256"),
        "purchase_transaction_id": transaction_id,
        "purchase_transaction_sha256": _transaction_sha256(transaction),
        "settlement_event_id": settlement.get("id"),
        "settlement_event_hash": settlement.get("event_hash"),
        "purchase_quote_event_id": quote.get("id"),
        "purchase_quote_event_hash": quote.get("event_hash"),
    }
    try:
        payload = evaluate_purchase_record(
            case_id=identity["case_id"],
            holding_id=identity["holding_id"],
            selected_code=identity["selected_code"],
            candidate_code=identity["candidate_code"],
            candidate_name=identity["candidate_name"],
            purchase_quote_event=quote,
            purchase_transaction=transaction,
            submitted_at=submitted_at.isoformat(timespec="seconds"),
            acknowledged_order_variance=bool(request.get("acknowledged_order_variance")),
            settled_cash_yuan=settled_cash,
            bindings=bindings,
            generated_at=current.isoformat(timespec="seconds"),
        )
    except ValueError as error:
        raise LifecycleValidationError(str(error)) from error
    _append_lifecycle_event(
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return decorate_case(case_id, user_id=user_id, now=current)


def reconcile_holdings(
    case_id: str,
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _validated_now(now)
    events = storage.list_fund_switch_lifecycle_events(case_id, user_id=user_id)
    identity = _identity(events)
    if _event(events, "holdings_reconciled", latest=False):
        raise LifecycleConflictError("该替换批次已经完成持仓对账")
    purchase = _event(events, "purchase_recorded", latest=False)
    settlement = _event(events, "redemption_settled", latest=False)
    if purchase is None or settlement is None:
        raise LifecycleConflictError("必须先回填实际赎回和申购成交")
    preview = _reconciliation_preview(identity, user_id=user_id)
    purchase_bindings = (purchase.get("payload") or {}).get("bindings") or {}
    bindings = {
        "execution_review_id": identity["execution_review_id"],
        "execution_review_hash": identity["execution_review_hash"],
        "redemption_transaction_id": purchase_bindings.get("redemption_transaction_id"),
        "redemption_transaction_sha256": purchase_bindings.get("redemption_transaction_sha256"),
        "purchase_transaction_id": purchase_bindings.get("purchase_transaction_id"),
        "purchase_transaction_sha256": purchase_bindings.get("purchase_transaction_sha256"),
        "purchase_event_id": purchase.get("id"),
        "purchase_event_hash": purchase.get("event_hash"),
        "current_holdings_sha256": preview.get("holdings_sha256"),
        "selected_ledger_sha256": preview.get("selected_ledger_sha256"),
        "candidate_ledger_sha256": preview.get("candidate_ledger_sha256"),
    }
    try:
        payload = evaluate_holdings_reconciliation(
            case_id=identity["case_id"],
            holding_id=identity["holding_id"],
            selected_code=identity["selected_code"],
            candidate_code=identity["candidate_code"],
            candidate_name=identity["candidate_name"],
            reconciliation=preview,
            bindings=bindings,
            generated_at=current.isoformat(timespec="seconds"),
        )
    except ValueError as error:
        raise LifecycleValidationError(str(error)) from error
    _append_lifecycle_event(
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return decorate_case(case_id, user_id=user_id, now=current)


def create_attribution_snapshot(
    case_id: str,
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = _validated_now(now)
    events = storage.list_fund_switch_lifecycle_events(case_id, user_id=user_id)
    identity = _identity(events)
    reconciliation = _event(events, "holdings_reconciled", latest=False)
    settlement = _event(events, "redemption_settled", latest=False)
    purchase = _event(events, "purchase_recorded", latest=False)
    if reconciliation is None or settlement is None or purchase is None:
        raise LifecycleConflictError("真实成交和持仓对账完成后才能生成替换收益归因")
    current_case = decorate_case(case_id, user_id=user_id, now=current)
    if not (current_case.get("integrity") or {}).get("verified"):
        raise LifecycleConflictError("替换批次或绑定交易流水完整性失败，不能生成收益归因")
    if not (current_case.get("decision_gate") or {}).get("attribution_refresh_ready"):
        reasons = current_case.get("attribution_blockers") or []
        detail = "；".join(str(item) for item in reasons[:3])
        raise LifecycleConflictError(
            f"持仓对账后相关账本已变化，当前归因算法不能继续跟踪该批次{f'：{detail}' if detail else ''}"
        )

    def load(kind: str, code: str) -> tuple[str, dict | None, str | None]:
        try:
            value = (
                funds.get_fund_nav_history(code, months=120)
                if kind == "nav"
                else funds.get_fund_dividends(code)
            )
            if not isinstance(value, dict):
                raise TypeError("真实基金工具返回格式异常")
            return f"{kind}:{code}", copy.deepcopy(value), None
        except Exception as error:
            return f"{kind}:{code}", None, str(error)[:240]

    jobs = [
        ("nav", identity["selected_code"]),
        ("nav", identity["candidate_code"]),
        ("distribution", identity["selected_code"]),
        ("distribution", identity["candidate_code"]),
    ]
    values: dict[str, dict] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="fund-switch-attribution") as pool:
        for key, value, error in pool.map(lambda job: load(*job), jobs):
            if value is not None:
                values[key] = value
            else:
                kind, code = key.split(":", 1)
                errors.append({"source": kind, "code": code, "error": error or "真实数据不可用"})
    source_history = values.get(f"nav:{identity['selected_code']}")
    candidate_history = values.get(f"nav:{identity['candidate_code']}")
    source_distributions = values.get(f"distribution:{identity['selected_code']}")
    candidate_distributions = values.get(f"distribution:{identity['candidate_code']}")
    purchase_bindings = (purchase.get("payload") or {}).get("bindings") or {}
    bindings = {
        "execution_review_id": identity["execution_review_id"],
        "execution_review_hash": identity["execution_review_hash"],
        "redemption_transaction_id": purchase_bindings.get("redemption_transaction_id"),
        "redemption_transaction_sha256": purchase_bindings.get("redemption_transaction_sha256"),
        "purchase_transaction_id": purchase_bindings.get("purchase_transaction_id"),
        "purchase_transaction_sha256": purchase_bindings.get("purchase_transaction_sha256"),
        "reconciliation_event_id": reconciliation.get("id"),
        "reconciliation_event_hash": reconciliation.get("event_hash"),
        "source_nav_sha256": payload_sha256(source_history) if source_history else "",
        "candidate_nav_sha256": payload_sha256(candidate_history) if candidate_history else "",
        "source_distributions_sha256": payload_sha256(source_distributions) if source_distributions else "",
        "candidate_distributions_sha256": payload_sha256(candidate_distributions) if candidate_distributions else "",
    }
    payload = evaluate_attribution_snapshot(
        case_id=identity["case_id"],
        holding_id=identity["holding_id"],
        selected_code=identity["selected_code"],
        candidate_code=identity["candidate_code"],
        candidate_name=identity["candidate_name"],
        redemption=(settlement.get("payload") or {}).get("redemption") or {},
        purchase=(purchase.get("payload") or {}).get("purchase") or {},
        source_history=source_history,
        candidate_history=candidate_history,
        source_distributions=source_distributions,
        candidate_distributions=candidate_distributions,
        source_errors=errors,
        bindings=bindings,
        generated_at=current.isoformat(timespec="seconds"),
    )
    _append_lifecycle_event(
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return decorate_case(case_id, user_id=user_id, now=current)


def list_cases(
    *,
    user_id: str = "default",
    limit: int = 50,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    heads = storage.list_fund_switch_lifecycle_case_heads(
        user_id=user_id,
        limit=limit,
    )
    items = [
        decorate_case(item["case_id"], user_id=user_id, now=now)
        for item in heads
    ]
    return {
        "status": "available" if items else "not_recorded",
        "count": len(items),
        "items": items,
        "summary": {
            "active_count": sum(
                item.get("status") not in TERMINAL_CASE_STATUSES
                for item in items
            ),
            "reconciled_count": sum(
                (item.get("decision_gate") or {}).get("holdings_reconciled") is True
                for item in items
            ),
            "attribution_available_count": sum(
                (item.get("decision_gate") or {}).get("historical_attribution_available") is True
                for item in items
            ),
            "integrity_failed_count": sum(
                item.get("status") == "integrity_failed" for item in items
            ),
        },
        "policy": "批次结果来自用户确认流水和真实公开净值；不把未执行建议计入收益。",
    }


def agent_lifecycle_summary(
    user_id: str,
    *,
    target_code: str = "",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    result = list_cases(user_id=user_id, limit=12, now=now)
    items = result.get("items") or []
    if target_code:
        items = [
            item for item in items
            if target_code in {
                item.get("selected_code"),
                item.get("candidate_code"),
            }
        ]
    return {
        "status": "available" if items else "not_recorded",
        "count": len(items),
        "items": [
            {
                "case_id": item.get("case_id"),
                "selected_code": item.get("selected_code"),
                "candidate_code": item.get("candidate_code"),
                "status": item.get("status"),
                "settlement": {
                    key: (item.get("settlement") or {}).get(key)
                    for key in (
                        "settled_on",
                        "actual_gross_yuan",
                        "actual_fee_yuan",
                        "actual_received_yuan",
                        "gross_variance_yuan",
                    )
                },
                "purchase": {
                    key: (item.get("purchase") or {}).get(key)
                    for key in (
                        "confirmation_date",
                        "actual_asset_amount_yuan",
                        "actual_fee_yuan",
                        "residual_cash_yuan",
                    )
                },
                "attribution": {
                    "status": (item.get("attribution") or {}).get("status"),
                    "metrics": {
                        key: ((item.get("attribution") or {}).get("metrics") or {}).get(key)
                        for key in (
                            "as_of",
                            "incremental_value_vs_hold_yuan",
                            "incremental_return_vs_hold_pct",
                            "total_switch_fees_yuan",
                        )
                    },
                    "reasons": (item.get("attribution") or {}).get("reasons") or [],
                },
                "integrity_verified": (item.get("integrity") or {}).get("verified"),
                "execution_authorized": False,
            }
            for item in items
        ],
        "policy": (
            "模型只能描述已确认流水和已发生的历史增量结果；"
            "不得把批次状态解释为自动交易授权或未来盈利概率。"
        ),
    }
