# -*- coding: utf-8 -*-
"""Application service for real batch-purchase outcome attribution snapshots."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import funds
import portfolio_review
from investment_policy import payload_sha256
from strategies.portfolio_batch_purchase_attribution import (
    evaluate_batch_purchase_attribution,
)

from .batch_purchase_execution import transaction_integrity_snapshot
from .repository import AgentRepository


class BatchPurchaseAttributionValidationError(ValueError):
    pass


class BatchPurchaseAttributionConflictError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validated_now(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise BatchPurchaseAttributionValidationError("服务时间必须包含时区")
    return current.astimezone(dt.timezone.utc)


def _execution_context(
    repository: AgentRepository,
    batch: dict[str, Any],
    *,
    user_id: str,
    expected_reconciliation_id: str | None = None,
    expected_reconciliation_hash: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    batch_id = str(batch.get("id") or "")
    events = repository.list_batch_purchase_execution_events(batch_id, user_id=user_id)
    audit = repository.verify_batch_purchase_execution_audit(batch_id, user_id=user_id)
    if not events or not audit.get("verified"):
        raise BatchPurchaseAttributionConflictError("批次真实成交审计链不存在或完整性失败")
    reconciliation = events[-1]
    if (
        reconciliation.get("event_type") != "holdings_reconciled"
        or not reconciliation.get("integrity_verified")
    ):
        raise BatchPurchaseAttributionConflictError("真实成交和持仓对账完成后才能生成绩效快照")
    if expected_reconciliation_id is not None and (
        str(reconciliation.get("id") or "") != expected_reconciliation_id
        or str(reconciliation.get("event_hash") or "") != expected_reconciliation_hash
    ):
        raise BatchPurchaseAttributionConflictError("持仓对账事件已变化，请刷新后重试")

    reconciliation_bindings = (reconciliation.get("payload") or {}).get("bindings") or {}
    purchase_event_id = str(reconciliation_bindings.get("purchase_event_id") or "")
    purchase = next(
        (item for item in events if str(item.get("id") or "") == purchase_event_id),
        None,
    )
    if (
        purchase is None
        or purchase.get("event_type") != "purchases_recorded"
        or not purchase.get("integrity_verified")
        or str(purchase.get("event_hash") or "")
        != str(reconciliation_bindings.get("purchase_event_hash") or "")
    ):
        raise BatchPurchaseAttributionConflictError("持仓对账没有绑定完整的真实成交事件")
    transaction_integrity = transaction_integrity_snapshot(
        repository,
        batch_id,
        events,
        user_id=user_id,
    )
    if not transaction_integrity.get("verified"):
        raise BatchPurchaseAttributionConflictError("批次绑定交易流水已删除或内容发生变化")
    return events, purchase, reconciliation


def _lot_state(
    purchase_payload: dict[str, Any],
    *,
    user_id: str,
) -> tuple[dict[int, dict[str, Any]], dict[str, str], dict[str, str], list[str]]:
    purchased = [
        item for item in purchase_payload.get("outcomes") or []
        if item.get("resolution") == "purchased" and item.get("transaction")
    ]
    transaction_ids = [
        int((item.get("transaction") or {}).get("id") or 0) for item in purchased
    ]
    if not transaction_ids or any(value <= 0 for value in transaction_ids):
        raise BatchPurchaseAttributionConflictError("批次没有可追踪的真实基金买入流水")
    try:
        snapshots = portfolio_review.purchase_lot_outcome_snapshots(
            transaction_ids,
            user_id=user_id,
        )
    except ValueError as error:
        raise BatchPurchaseAttributionConflictError(str(error)) from error

    ledger_hashes: dict[str, str] = {}
    holding_hashes: dict[str, str] = {}
    blockers: list[str] = []
    for item in purchased:
        transaction = item.get("transaction") or {}
        transaction_id = int(transaction.get("id") or 0)
        code = str(item.get("code") or transaction.get("code") or "")
        snapshot = snapshots.get(transaction_id) or {}
        ledger_hashes[str(transaction_id)] = payload_sha256({
            "transaction": snapshot.get("transaction"),
            "lot": snapshot.get("lot"),
            "asset_position": snapshot.get("asset_position"),
            "integrity_issues": snapshot.get("integrity_issues") or [],
            "relevant_transactions": snapshot.get("relevant_transactions") or [],
        })
        holding_hashes[str(transaction_id)] = payload_sha256(
            snapshot.get("holding_share_rows") or []
        )
        if not (snapshot.get("lot") or {}).get("complete"):
            blockers.append(f"{code} 的批次买入份额无法在 FIFO 账本中完整追踪")
        if snapshot.get("integrity_issues"):
            blockers.append(f"{code} 的交易账本存在完整性问题")
        if not (snapshot.get("holding_reconciliation") or {}).get("shares_match"):
            blockers.append(f"{code} 当前确认持仓份额与 FIFO 未平仓份额不一致")
    return snapshots, ledger_hashes, holding_hashes, blockers


def _load_market_sources(
    codes: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, str]]]:
    def load(job: tuple[str, str]) -> tuple[str, str, dict[str, Any] | None, str | None]:
        kind, code = job
        try:
            value = (
                funds.get_fund_nav_history(code, months=120)
                if kind == "nav" else funds.get_fund_dividends(code)
            )
            if not isinstance(value, dict):
                raise TypeError("真实基金工具返回格式异常")
            return kind, code, copy.deepcopy(value), None
        except Exception as error:
            return kind, code, None, str(error)[:240]

    jobs = [(kind, code) for code in codes for kind in ("nav", "distribution")]
    histories: dict[str, dict[str, Any]] = {}
    distributions: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(
        max_workers=min(8, max(1, len(jobs))),
        thread_name_prefix="batch-purchase-attribution",
    ) as pool:
        for kind, code, value, error in pool.map(load, jobs):
            if value is None:
                errors.append({
                    "source": kind,
                    "code": code,
                    "error": error or "真实数据不可用",
                })
            elif kind == "nav":
                histories[code] = value
            else:
                distributions[code] = value
    return histories, distributions, errors


def create_batch_purchase_attribution_snapshot(
    repository: AgentRepository,
    batch: dict[str, Any],
    request: dict[str, Any],
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    current = _validated_now(now)
    expected_reconciliation_id = str(request.get("expected_reconciliation_event_id") or "")
    expected_reconciliation_hash = str(
        request.get("expected_reconciliation_event_hash") or ""
    )
    _, purchase, reconciliation = _execution_context(
        repository,
        batch,
        user_id=user_id,
        expected_reconciliation_id=expected_reconciliation_id,
        expected_reconciliation_hash=expected_reconciliation_hash,
    )
    purchase_payload = purchase.get("payload") or {}
    snapshots, ledger_hashes, holding_hashes, blockers = _lot_state(
        purchase_payload,
        user_id=user_id,
    )
    if blockers:
        raise BatchPurchaseAttributionValidationError("；".join(blockers[:3]))

    codes = sorted({
        str(item.get("code") or "")
        for item in purchase_payload.get("outcomes") or []
        if item.get("resolution") == "purchased"
    })
    histories, distributions, source_errors = _load_market_sources(codes)
    previous_hash = (
        str(request.get("expected_previous_snapshot_hash"))
        if request.get("expected_previous_snapshot_hash") else None
    )
    source_hashes = {
        code: {
            "nav_sha256": payload_sha256(histories[code]) if code in histories else "",
            "distribution_sha256": (
                payload_sha256(distributions[code]) if code in distributions else ""
            ),
        }
        for code in codes
    }
    request_payload = {
        "expected_reconciliation_event_id": expected_reconciliation_id,
        "expected_reconciliation_event_hash": expected_reconciliation_hash,
        "expected_previous_snapshot_hash": previous_hash,
        "lot_ledger_sha256_by_transaction": ledger_hashes,
        "holding_shares_sha256_by_transaction": holding_hashes,
        "source_sha256_by_code": source_hashes,
        "source_errors": source_errors,
    }
    bindings = {
        "batch_id": str(batch.get("id") or ""),
        "batch_input_sha256": str(batch.get("input_hash") or ""),
        "purchase_event_id": str(purchase.get("id") or ""),
        "purchase_event_hash": str(purchase.get("event_hash") or ""),
        "purchase_payload_sha256": str(purchase.get("payload_sha256") or ""),
        "reconciliation_event_id": str(reconciliation.get("id") or ""),
        "reconciliation_event_hash": str(reconciliation.get("event_hash") or ""),
        "reconciliation_payload_sha256": str(reconciliation.get("payload_sha256") or ""),
        "request_sha256": _sha256(request_payload),
        "transaction_bindings": list(
            ((purchase_payload.get("bindings") or {}).get("transaction_bindings") or [])
        ),
        "lot_ledger_sha256_by_transaction": ledger_hashes,
        "holding_shares_sha256_by_transaction": holding_hashes,
        "source_sha256_by_code": source_hashes,
    }
    try:
        payload = evaluate_batch_purchase_attribution(
            purchase_payload,
            snapshots,
            histories,
            distributions,
            source_errors,
            bindings=bindings,
            generated_at=current.isoformat(timespec="seconds"),
        )
        return repository.append_batch_purchase_attribution_snapshot(
            str(batch.get("id") or ""),
            payload,
            user_id=user_id,
            actor_id=actor_id,
            expected_previous_event_hash=previous_hash,
        )
    except BatchPurchaseAttributionValidationError:
        raise
    except KeyError as error:
        raise BatchPurchaseAttributionConflictError(str(error)) from error
    except ValueError as error:
        raise BatchPurchaseAttributionConflictError(str(error)) from error


def decorate_batch_purchase_attribution(
    repository: AgentRepository,
    batch: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any] | None:
    batch_id = str(batch.get("id") or "")
    execution_events = repository.list_batch_purchase_execution_events(
        batch_id,
        user_id=user_id,
    )
    if not execution_events:
        return None
    if execution_events[-1].get("event_type") != "holdings_reconciled":
        return {
            "status": "awaiting_reconciliation",
            "refresh_ready": False,
            "blockers": ["真实成交与当前持仓完成 FIFO 对账后才能生成绩效快照"],
            "snapshot": None,
        }

    try:
        _, purchase, reconciliation = _execution_context(
            repository,
            batch,
            user_id=user_id,
        )
        _, ledger_hashes, holding_hashes, blockers = _lot_state(
            purchase.get("payload") or {},
            user_id=user_id,
        )
    except (BatchPurchaseAttributionConflictError, BatchPurchaseAttributionValidationError) as error:
        return {
            "status": "integrity_failed",
            "refresh_ready": False,
            "blockers": [str(error)],
            "snapshot": None,
        }

    snapshots = repository.list_batch_purchase_attribution_snapshots(
        batch_id,
        user_id=user_id,
    )
    refresh_ready = not blockers
    if not snapshots:
        return {
            "status": "ready_for_snapshot" if refresh_ready else "ledger_reconciliation_required",
            "refresh_ready": refresh_ready,
            "blockers": blockers,
            "expected_reconciliation_event_id": reconciliation.get("id"),
            "expected_reconciliation_event_hash": reconciliation.get("event_hash"),
            "snapshot": None,
        }

    latest = snapshots[-1]
    audit = repository.verify_batch_purchase_attribution_audit(
        batch_id,
        user_id=user_id,
    )
    if not latest.get("integrity_verified") or not audit.get("verified"):
        return {
            "status": "integrity_failed",
            "refresh_ready": False,
            "blockers": ["绩效快照内容哈希或审计链完整性失败"],
            "snapshot": {
                "id": latest.get("id"),
                "event_hash": latest.get("event_hash"),
                "created_at": latest.get("created_at"),
                "integrity_verified": False,
                "audit_chain_verified": bool(audit.get("verified")),
                "audit_event_count": audit.get("event_count"),
            },
        }

    payload = copy.deepcopy(latest.get("payload") or {})
    bindings = payload.get("bindings") or {}
    execution_current = bool(
        bindings.get("purchase_event_id") == purchase.get("id")
        and bindings.get("purchase_event_hash") == purchase.get("event_hash")
        and bindings.get("reconciliation_event_id") == reconciliation.get("id")
        and bindings.get("reconciliation_event_hash") == reconciliation.get("event_hash")
    )
    ledger_current = bindings.get("lot_ledger_sha256_by_transaction") == ledger_hashes
    holdings_current = bindings.get("holding_shares_sha256_by_transaction") == holding_hashes
    current = execution_current and ledger_current and holdings_current
    if not current:
        payload["status"] = "stale_refresh_required"
    payload["refresh_ready"] = refresh_ready
    payload["blockers"] = blockers
    payload["current_bindings"] = {
        "execution_current": execution_current,
        "ledger_current": ledger_current,
        "holding_shares_current": holdings_current,
        "all_current": current,
    }
    payload["expected_reconciliation_event_id"] = reconciliation.get("id")
    payload["expected_reconciliation_event_hash"] = reconciliation.get("event_hash")
    payload["snapshot"] = {
        "id": latest.get("id"),
        "revision": latest.get("sequence_no"),
        "event_hash": latest.get("event_hash"),
        "payload_sha256": latest.get("payload_sha256"),
        "previous_hash": latest.get("previous_hash"),
        "created_at": latest.get("created_at"),
        "integrity_verified": True,
        "audit_chain_verified": True,
        "audit_event_count": audit.get("event_count"),
    }
    return payload
