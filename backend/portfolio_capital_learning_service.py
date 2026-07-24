# -*- coding: utf-8 -*-
"""Capital-plan execution reconciliation and decision-outcome learning.

This module binds a frozen, research-only capital plan to user-confirmed real
transactions. It records what happened; it never places an order or promises a
return. Exact 5/20/60 trading-day observations separate selection outcome from
implementation timing and allocation drift.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
import threading
from typing import Any, Callable

import pandas as pd

import data_fetch
import opportunity_service
import storage
from background_jobs import BackgroundJobRepository
from portfolio_capital_learning_repository import (
    ENGINE_VERSION,
    PortfolioCapitalExecutionNotFoundError,
    PortfolioCapitalLearningConflictError,
    PortfolioCapitalLearningRepository,
    PortfolioCapitalOutcomeNotFoundError,
    repository as learning_repository,
)
from portfolio_capital_repository import (
    PortfolioCapitalPlanNotFoundError,
    PortfolioCapitalRepository,
    repository as plan_repository,
    sha256_payload,
)
from task_queue import (
    QUEUE_MARKET,
    TaskQueueConfigurationError,
    TaskQueueUnavailableError,
    enqueue_background_job,
    uses_celery_queue,
)


EXECUTION_WINDOW_DAYS = 45
HORIZONS = (5, 20, 60)
EXECUTION_ACKNOWLEDGMENT_VERSION = "capital-execution-confirmation.v1"
EXECUTION_ACKNOWLEDGMENT = (
    "我确认所选流水是真实成交，并确认人民币结算金额仅用于计划对账；"
    "本记录不会创建订单，也不代表收益承诺。"
)
DEVIATION_REVIEW_VERSION = "capital-execution-deviation-review.v1"
DEVIATION_REVIEW_ACKNOWLEDGMENT = (
    "我已复核本次执行偏差，确认实际成交事实不变，并理解复核只解除流程门禁，"
    "不会消除已占用预算或改变历史收益归因。"
)


class PortfolioCapitalOutcomeJobNotFoundError(RuntimeError):
    """Raised when an outcome-refresh job is absent or outside the caller scope."""

    pass


def _now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _now(value).isoformat(timespec="seconds")


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _asset_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("market") or "").strip(),
        str(item.get("code") or item.get("symbol") or "").strip(),
    )


def _transaction_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
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


def _transaction_sha256(item: dict[str, Any]) -> str:
    return sha256_payload(_transaction_snapshot(item))


def _plan_or_raise(
    plan_id: str,
    *,
    tenant_id: str,
    user_id: str,
    plan_repo: PortfolioCapitalRepository,
) -> dict[str, Any]:
    plan = plan_repo.get_plan(
        plan_id, tenant_id=tenant_id, user_id=user_id
    )
    if plan is None:
        raise PortfolioCapitalPlanNotFoundError(
            "资本决策计划不存在"
        )
    if not (plan.get("integrity") or {}).get("verified"):
        raise PortfolioCapitalLearningConflictError(
            "资本决策计划完整性校验失败"
        )
    return plan


def _planned_candidates(
    plan: dict[str, Any]
) -> list[dict[str, Any]]:
    result = plan.get("result") or {}
    rows = []
    for item in result.get("candidate_actions") or []:
        planned = _number(item.get("planned_amount_cny"), 0.0) or 0.0
        if planned <= 0:
            continue
        rows.append(
            {
                "market": str(item.get("market") or ""),
                "symbol": str(item.get("symbol") or ""),
                "name": item.get("name") or item.get("symbol"),
                "planned_amount_cny": round(planned, 2),
            }
        )
    return rows


def _verify_execution_chain(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(events, key=lambda item: int(item["event_no"]))
    previous_hash = None
    failures: list[str] = []
    for expected_no, item in enumerate(ordered, start=1):
        if int(item.get("event_no") or 0) != expected_no:
            failures.append(f"event_no:{item.get('id')}")
        if item.get("previous_event_hash") != previous_hash:
            failures.append(f"previous_hash:{item.get('id')}")
        if not (item.get("integrity") or {}).get("verified"):
            failures.append(f"integrity:{item.get('id')}")
        previous_hash = item.get("event_hash")
    return {
        "verified": not failures,
        "event_count": len(ordered),
        "failures": failures,
        "head_event_hash": previous_hash,
    }


def verify_execution_event(
    event: dict[str, Any],
    *,
    user_id: str,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
    all_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    loader = transaction_loader or storage.list_portfolio_transactions
    current_rows = {
        int(item["id"]): item
        for item in loader(user_id=user_id)
        if item.get("id") is not None
    }
    missing: list[int] = []
    changed: list[int] = []
    for confirmed in (event.get("evidence") or {}).get(
        "transactions", []
    ):
        transaction_id = int(confirmed.get("transaction_id") or 0)
        current = current_rows.get(transaction_id)
        if current is None:
            missing.append(transaction_id)
            continue
        if _transaction_sha256(current) != confirmed.get(
            "transaction_sha256"
        ):
            changed.append(transaction_id)
    chain = _verify_execution_chain(all_events or [event])
    stored = bool((event.get("integrity") or {}).get("verified"))
    ledger_verified = not missing and not changed
    return {
        "verified": bool(stored and ledger_verified and chain["verified"]),
        "stored_event_verified": stored,
        "ledger_verified": ledger_verified,
        "missing_transaction_ids": missing,
        "changed_transaction_ids": changed,
        "chain": chain,
        "notice": (
            "事件、前序链和当前交易流水一致"
            if stored and ledger_verified and chain["verified"]
            else "执行记录与当前流水或审计链不一致；预算仍保留占用并暂停新计划"
        ),
    }


def _execution_lifecycle(
    latest: dict[str, Any] | None,
    verification: dict[str, Any] | None = None,
    *,
    plan_status: str = "ready",
) -> str:
    if plan_status != "ready":
        return "not_applicable"
    if latest is None:
        return "awaiting_execution"
    if verification is not None and not verification.get("verified"):
        return "integrity_failed"
    return str(latest.get("status") or "integrity_failed")


def _eligible_transactions(
    plan: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    learning_repo: PortfolioCapitalLearningRepository,
    transaction_loader: Callable[..., list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    decision_date = _date(plan.get("decision_date"))
    if decision_date is None:
        return []
    cutoff = decision_date + dt.timedelta(days=EXECUTION_WINDOW_DAYS)
    planned_keys = {
        (item["market"], item["symbol"])
        for item in _planned_candidates(plan)
    }
    bindings = {
        int(item["transaction_id"]): item
        for item in learning_repo.list_bindings(
            tenant_id=tenant_id, user_id=user_id
        )
    }
    result = []
    for row in transaction_loader(user_id=user_id):
        trade_date = _date(row.get("trade_date"))
        if (
            row.get("asset_type") != "stock"
            or row.get("trade_type") != "buy"
            or trade_date is None
            or trade_date < decision_date
            or trade_date > cutoff
        ):
            continue
        transaction_id = int(row["id"])
        binding = bindings.get(transaction_id)
        if binding and binding.get("plan_id") != plan.get("id"):
            continue
        market = str(row.get("market") or "")
        gross_local = (
            (_number(row.get("shares"), 0.0) or 0.0)
            * (_number(row.get("unit_price"), 0.0) or 0.0)
            + (_number(row.get("fee"), 0.0) or 0.0)
        )
        mainland = market == "A股"
        result.append(
            {
                **_transaction_snapshot(row),
                "transaction_sha256": _transaction_sha256(row),
                "plan_match": _asset_key(row) in planned_keys,
                "already_bound_to_plan": bool(binding),
                "confirmed_settled_amount_cny": (
                    round(float(binding["settled_amount_cny"]), 2)
                    if binding
                    else None
                ),
                "suggested_settled_amount_cny": (
                    round(gross_local, 2) if mainland else None
                ),
                "requires_cny_confirmation": True,
            }
        )
    result.sort(
        key=lambda item: (
            not item["already_bound_to_plan"],
            not item["plan_match"],
            str(item.get("trade_date") or ""),
            int(item["id"]),
        )
    )
    return result


def get_plan_execution_context(
    plan_id: str,
    *,
    tenant_id: str,
    user_id: str,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    plan = _plan_or_raise(
        plan_id,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
    )
    loader = transaction_loader or storage.list_portfolio_transactions
    events = learning_repo.list_executions(
        plan_id, tenant_id=tenant_id, user_id=user_id
    )
    latest = events[0] if events else None
    verification = (
        verify_execution_event(
            latest,
            user_id=user_id,
            transaction_loader=loader,
            all_events=events,
        )
        if latest
        else None
    )
    candidates = _planned_candidates(plan)
    planned_total = round(
        sum(float(item["planned_amount_cny"]) for item in candidates), 2
    )
    return {
        "schema_version": "portfolio_capital_execution_context.v1",
        "engine_version": ENGINE_VERSION,
        "plan": {
            "id": plan["id"],
            "status": plan["status"],
            "decision_date": plan["decision_date"],
            "created_at": plan["created_at"],
            "result_sha256": plan["result_sha256"],
            "integrity": plan["integrity"],
            "primary_action": (plan.get("result") or {}).get(
                "primary_action"
            ),
            "capital": (plan.get("result") or {}).get("capital"),
        },
        "planned_candidates": candidates,
        "planned_amount_cny": planned_total,
        "lifecycle_status": _execution_lifecycle(
            latest,
            verification,
            plan_status=str(plan.get("status") or ""),
        ),
        "latest_execution": latest,
        "execution_verification": verification,
        "events": events,
        "eligible_transactions": (
            _eligible_transactions(
                plan,
                user_id=user_id,
                tenant_id=tenant_id,
                learning_repo=learning_repo,
                transaction_loader=loader,
            )
            if plan.get("status") == "ready"
            else []
        ),
        "window": {
            "starts_on": plan["decision_date"],
            "ends_on": (
                _date(plan["decision_date"])
                + dt.timedelta(days=EXECUTION_WINDOW_DAYS)
            ).isoformat(),
            "calendar_days": EXECUTION_WINDOW_DAYS,
        },
        "acknowledgment": {
            "version": EXECUTION_ACKNOWLEDGMENT_VERSION,
            "text": EXECUTION_ACKNOWLEDGMENT,
            "text_sha256": sha256_payload(
                {"text": EXECUTION_ACKNOWLEDGMENT}
            ),
        },
        "boundaries": {
            "execution_authorized": False,
            "automatic_order_creation": False,
            "settled_amount_is_user_confirmed": True,
            "notice": (
                "这里只对账已经发生的真实买入。港股和美股必须确认实际人民币结算金额，"
                "系统不会用不可靠的即期汇率改写成交事实。"
            ),
        },
    }


def _reconcile(
    plan: dict[str, Any],
    confirmed: list[dict[str, Any]],
) -> dict[str, Any]:
    planned = _planned_candidates(plan)
    planned_by_key = {
        (item["market"], item["symbol"]): item for item in planned
    }
    actual_by_key: dict[tuple[str, str], float] = {}
    lag_weighted_days = 0.0
    total_settled = 0.0
    decision_date = _date(plan.get("decision_date")) or dt.date.today()
    for item in confirmed:
        snapshot = item["transaction"]
        key = _asset_key(snapshot)
        settled = float(item["settled_amount_cny"])
        actual_by_key[key] = actual_by_key.get(key, 0.0) + settled
        total_settled += settled
        trade_date = _date(snapshot.get("trade_date")) or decision_date
        lag_weighted_days += max(
            0, (trade_date - decision_date).days
        ) * settled

    rows = []
    on_plan_actual = 0.0
    for key, candidate in planned_by_key.items():
        planned_amount = float(candidate["planned_amount_cny"])
        actual = round(actual_by_key.pop(key, 0.0), 2)
        on_plan_actual += actual
        ratio = actual / planned_amount * 100 if planned_amount else None
        if actual <= 0:
            state = "unfilled"
        elif ratio is not None and ratio < 85:
            state = "partial"
        elif ratio is not None and ratio <= 115:
            state = "aligned"
        else:
            state = "over"
        rows.append(
            {
                **candidate,
                "actual_settled_amount_cny": actual,
                "deviation_amount_cny": round(
                    actual - planned_amount, 2
                ),
                "execution_ratio_pct": (
                    round(ratio, 2) if ratio is not None else None
                ),
                "status": state,
            }
        )
    off_plan = [
        {
            "market": market,
            "symbol": symbol,
            "actual_settled_amount_cny": round(amount, 2),
            "status": "off_plan",
        }
        for (market, symbol), amount in sorted(actual_by_key.items())
        if amount > 0
    ]
    off_plan_amount = sum(
        float(item["actual_settled_amount_cny"]) for item in off_plan
    )
    planned_total = sum(
        float(item["planned_amount_cny"]) for item in planned
    )
    coverage = (
        min(100.0, on_plan_actual / planned_total * 100)
        if planned_total > 0
        else 0.0
    )
    deviation_numerator = sum(
        abs(float(item["deviation_amount_cny"])) for item in rows
    ) + off_plan_amount
    deviation_pct = (
        deviation_numerator / planned_total * 100
        if planned_total > 0
        else 100.0
    )
    if (
        off_plan_amount > max(1.0, planned_total * 0.05)
        or total_settled > planned_total * 1.15
        or deviation_pct > 30
    ):
        status = "deviated"
    elif coverage < 85:
        status = "partial"
    else:
        status = "reconciled"
    return {
        "schema_version": "portfolio_capital_execution_reconciliation.v1",
        "engine_version": ENGINE_VERSION,
        "plan_id": plan["id"],
        "decision_date": plan["decision_date"],
        "status": status,
        "planned_amount_cny": round(planned_total, 2),
        "actual_settled_amount_cny": round(total_settled, 2),
        "on_plan_settled_amount_cny": round(on_plan_actual, 2),
        "off_plan_settled_amount_cny": round(off_plan_amount, 2),
        "plan_coverage_pct": round(coverage, 2),
        "absolute_deviation_pct": round(deviation_pct, 2),
        "weighted_execution_lag_calendar_days": round(
            lag_weighted_days / total_settled if total_settled else 0.0,
            2,
        ),
        "candidate_reconciliation": rows,
        "off_plan_transactions": off_plan,
        "transaction_ids": sorted(
            int(item["transaction"]["id"]) for item in confirmed
        ),
        "interpretation": (
            "reconciled 表示金额与标的基本落在冻结计划的 ±15% 区间；"
            "partial 表示尚未完成；deviated 表示存在明显超额、错配或计划外买入。"
        ),
        "execution_authorized": False,
    }


def create_execution_event(
    plan_id: str,
    *,
    transactions: list[dict[str, Any]],
    acknowledged: bool,
    expected_previous_event_hash: str | None,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    if not acknowledged:
        raise ValueError("必须确认所选流水和人民币结算金额")
    if not transactions:
        raise ValueError("至少选择一笔真实买入流水")
    if len(transactions) > 100:
        raise ValueError("单个执行事件最多绑定 100 笔流水")
    plan = _plan_or_raise(
        plan_id,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
    )
    if plan.get("status") != "ready" or not _planned_candidates(plan):
        raise PortfolioCapitalLearningConflictError(
            "只有冻结后且状态为 ready 的资本计划可以绑定成交"
        )
    loader = transaction_loader or storage.list_portfolio_transactions
    live_rows = {
        int(item["id"]): item
        for item in loader(user_id=user_id)
        if item.get("id") is not None
    }
    existing_events = learning_repo.list_executions(
        plan_id, tenant_id=tenant_id, user_id=user_id
    )
    latest = existing_events[0] if existing_events else None
    actual_previous_hash = (
        str(latest["event_hash"]) if latest else None
    )
    prior_confirmations = {
        int(item["transaction_id"]): item
        for item in (latest or {}).get("evidence", {}).get(
            "transactions", []
        )
    }
    request_by_id: dict[int, dict[str, Any]] = {}
    for item in transactions:
        transaction_id = int(item.get("transaction_id") or 0)
        settled = _number(item.get("settled_amount_cny"))
        if transaction_id <= 0:
            raise ValueError("交易流水 ID 无效")
        if settled is None or settled <= 0 or settled > 100_000_000:
            raise ValueError(
                f"交易 {transaction_id} 的人民币结算金额无效"
            )
        if transaction_id in request_by_id:
            raise ValueError(f"交易 {transaction_id} 重复选择")
        request_by_id[transaction_id] = {
            "transaction_id": transaction_id,
            "settled_amount_cny": round(settled, 2),
        }
    if not set(prior_confirmations).issubset(request_by_id):
        raise PortfolioCapitalLearningConflictError(
            "新事件只能追加成交，不能移除已经确认的流水"
        )

    decision_date = _date(plan.get("decision_date"))
    if decision_date is None:
        raise PortfolioCapitalLearningConflictError(
            "冻结计划缺少有效决策日期"
        )
    cutoff = decision_date + dt.timedelta(days=EXECUTION_WINDOW_DAYS)
    all_bindings = {
        int(item["transaction_id"]): item
        for item in learning_repo.list_bindings(
            tenant_id=tenant_id, user_id=user_id
        )
    }
    confirmed = []
    bindings = []
    for transaction_id, request in sorted(request_by_id.items()):
        row = live_rows.get(transaction_id)
        if row is None:
            raise PortfolioCapitalLearningConflictError(
                f"交易 {transaction_id} 不存在或已被删除"
            )
        trade_date = _date(row.get("trade_date"))
        if (
            row.get("asset_type") != "stock"
            or row.get("trade_type") != "buy"
            or trade_date is None
            or trade_date < decision_date
            or trade_date > cutoff
        ):
            raise ValueError(
                f"交易 {transaction_id} 不是计划后 {EXECUTION_WINDOW_DAYS} 天内的股票买入"
            )
        existing_binding = all_bindings.get(transaction_id)
        if existing_binding and existing_binding.get("plan_id") != plan_id:
            raise PortfolioCapitalLearningConflictError(
                f"交易 {transaction_id} 已绑定其他资本计划"
            )
        digest = _transaction_sha256(row)
        prior = prior_confirmations.get(transaction_id)
        if prior and (
            prior.get("transaction_sha256") != digest
            or round(float(prior.get("settled_amount_cny") or 0), 2)
            != request["settled_amount_cny"]
        ):
            raise PortfolioCapitalLearningConflictError(
                f"交易 {transaction_id} 的既有确认信息不可改写"
            )
        confirmed.append(
            {
                "transaction": _transaction_snapshot(row),
                "transaction_sha256": digest,
                "settled_amount_cny": request[
                    "settled_amount_cny"
                ],
            }
        )
        bindings.append(
            {
                "transaction_id": transaction_id,
                "transaction_sha256": digest,
                "settled_amount_cny": request[
                    "settled_amount_cny"
                ],
            }
        )

    same_as_latest = bool(
        latest
        and set(prior_confirmations) == set(request_by_id)
        and all(
            round(
                float(
                    prior_confirmations[transaction_id].get(
                        "settled_amount_cny"
                    )
                    or 0
                ),
                2,
            )
            == request_by_id[transaction_id][
                "settled_amount_cny"
            ]
            and prior_confirmations[transaction_id].get(
                "transaction_sha256"
            )
            == next(
                item["transaction_sha256"]
                for item in confirmed
                if int(item["transaction"]["id"])
                == transaction_id
            )
            for transaction_id in request_by_id
        )
    )
    if same_as_latest and latest is not None:
        latest["verification"] = verify_execution_event(
            latest,
            user_id=user_id,
            transaction_loader=loader,
            all_events=existing_events,
        )
        return latest, False
    if actual_previous_hash != expected_previous_event_hash:
        raise PortfolioCapitalLearningConflictError(
            "执行记录已变化，请刷新后再提交"
        )

    reconciliation = _reconcile(plan, confirmed)
    evidence = {
        "schema_version": "portfolio_capital_execution_evidence.v1",
        "engine_version": ENGINE_VERSION,
        "plan": {
            "id": plan["id"],
            "schema_version": plan["schema_version"],
            "engine_version": plan["engine_version"],
            "decision_date": plan["decision_date"],
            "evidence_sha256": plan["evidence_sha256"],
            "result_sha256": plan["result_sha256"],
        },
        "previous_event_hash": actual_previous_hash,
        "transactions": [
            {
                "transaction_id": int(item["transaction"]["id"]),
                "transaction": item["transaction"],
                "transaction_sha256": item[
                    "transaction_sha256"
                ],
                "settled_amount_cny": item[
                    "settled_amount_cny"
                ],
            }
            for item in confirmed
        ],
        "acknowledgment": {
            "version": EXECUTION_ACKNOWLEDGMENT_VERSION,
            "text": EXECUTION_ACKNOWLEDGMENT,
            "text_sha256": sha256_payload(
                {"text": EXECUTION_ACKNOWLEDGMENT}
            ),
            "acknowledged": True,
        },
    }
    saved, created = learning_repo.create_execution_event(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        plan_id=plan_id,
        status=str(reconciliation["status"]),
        planned_amount_cny=float(
            reconciliation["planned_amount_cny"]
        ),
        settled_amount_cny=float(
            reconciliation["actual_settled_amount_cny"]
        ),
        evidence=evidence,
        result=reconciliation,
        transaction_bindings=bindings,
        expected_previous_event_hash=actual_previous_hash,
        now=now,
    )
    all_events = learning_repo.list_executions(
        plan_id, tenant_id=tenant_id, user_id=user_id
    )
    saved["verification"] = verify_execution_event(
        saved,
        user_id=user_id,
        transaction_loader=loader,
        all_events=all_events,
    )
    return saved, created


def review_execution_deviation(
    plan_id: str,
    *,
    note: str,
    acknowledged: bool,
    expected_previous_event_hash: str,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    if not acknowledged:
        raise ValueError("必须确认已经复核执行偏差")
    normalized_note = str(note or "").strip()
    if len(normalized_note) < 10:
        raise ValueError("偏差复核原因至少填写 10 个字符")
    if len(normalized_note) > 500:
        raise ValueError("偏差复核原因最多 500 个字符")
    plan = _plan_or_raise(
        plan_id,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
    )
    events = learning_repo.list_executions(
        plan_id, tenant_id=tenant_id, user_id=user_id
    )
    latest = events[0] if events else None
    if latest is None:
        raise PortfolioCapitalExecutionNotFoundError(
            "该计划还没有执行事件"
        )
    if latest.get("status") == "reviewed":
        latest["verification"] = verify_execution_event(
            latest,
            user_id=user_id,
            transaction_loader=(
                transaction_loader
                or storage.list_portfolio_transactions
            ),
            all_events=events,
        )
        return latest, False
    if latest.get("status") != "deviated":
        raise PortfolioCapitalLearningConflictError(
            "只有明显偏离计划的执行事件需要偏差复核"
        )
    if latest.get("event_hash") != expected_previous_event_hash:
        raise PortfolioCapitalLearningConflictError(
            "执行记录已变化，请刷新后再复核"
        )
    loader = transaction_loader or storage.list_portfolio_transactions
    verification = verify_execution_event(
        latest,
        user_id=user_id,
        transaction_loader=loader,
        all_events=events,
    )
    if not verification.get("verified"):
        raise PortfolioCapitalLearningConflictError(
            "执行事件、前序链或当前流水不一致，不能复核放行"
        )
    prior_evidence = latest.get("evidence") or {}
    transactions = prior_evidence.get("transactions") or []
    review = {
        "version": DEVIATION_REVIEW_VERSION,
        "acknowledgment": DEVIATION_REVIEW_ACKNOWLEDGMENT,
        "acknowledgment_sha256": sha256_payload(
            {"text": DEVIATION_REVIEW_ACKNOWLEDGMENT}
        ),
        "acknowledged": True,
        "note": normalized_note,
        "reviewed_at": _iso(now),
    }
    evidence = {
        **prior_evidence,
        "previous_event_hash": latest["event_hash"],
        "deviation_review": review,
    }
    result = {
        **(latest.get("result") or {}),
        "status": "reviewed",
        "deviation_review": review,
        "interpretation": (
            "实际偏差已由用户复核；历史计划、成交金额、预算占用和偏差数值均未改写。"
        ),
    }
    bindings = [
        {
            "transaction_id": int(item["transaction_id"]),
            "transaction_sha256": item["transaction_sha256"],
            "settled_amount_cny": float(
                item["settled_amount_cny"]
            ),
        }
        for item in transactions
    ]
    saved, created = learning_repo.create_execution_event(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        plan_id=plan_id,
        status="reviewed",
        planned_amount_cny=float(
            result.get("planned_amount_cny") or 0
        ),
        settled_amount_cny=float(
            result.get("actual_settled_amount_cny") or 0
        ),
        evidence=evidence,
        result=result,
        transaction_bindings=bindings,
        expected_previous_event_hash=str(latest["event_hash"]),
        now=now,
    )
    all_events = learning_repo.list_executions(
        plan_id, tenant_id=tenant_id, user_id=user_id
    )
    saved["verification"] = verify_execution_event(
        saved,
        user_id=user_id,
        transaction_loader=loader,
        all_events=all_events,
    )
    return saved, created


class _ObservationCapture:
    def __init__(self, positions: list[dict[str, Any]]) -> None:
        self.positions = positions

    def get_paper_basket(
        self, basket_id: str, *, user_id: str
    ) -> dict[str, Any]:
        return {
            "id": basket_id,
            "user_id": user_id,
            "snapshot_verified": True,
            "snapshot": {
                "positions": self.positions,
                "cash_pct": 0,
            },
        }

    def append_paper_observation(
        self,
        basket_id: str,
        *,
        user_id: str,
        observed_at: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "basket_id": basket_id,
            "user_id": user_id,
            "observed_at": observed_at,
            "payload": payload,
        }


def _history_cache(
    loader: Callable[..., pd.DataFrame],
) -> Callable[..., pd.DataFrame]:
    cache: dict[tuple[str, str, int, int], pd.DataFrame] = {}
    lock = threading.Lock()

    def cached(
        market: str,
        symbol: str,
        months: int,
        *,
        fetch_months: int | None = None,
    ) -> pd.DataFrame:
        key = (
            str(market),
            str(symbol),
            int(months),
            int(fetch_months or months),
        )
        with lock:
            frame = cache.get(key)
        if frame is None:
            loaded = loader(
                market,
                symbol,
                months,
                fetch_months=fetch_months or months,
            ).copy()
            with lock:
                cache[key] = loaded
            frame = loaded
        return frame.copy()

    return cached


def _planned_positions(
    plan: dict[str, Any],
    *,
    history_loader: Callable[..., pd.DataFrame],
) -> list[dict[str, Any]]:
    decision_date = str(plan["decision_date"])[:10]
    entry_timestamp = pd.Timestamp(decision_date)
    current_date = dt.datetime.now(dt.timezone.utc).date()
    parsed_date = _date(decision_date) or current_date
    age_days = max(0, (current_date - parsed_date).days)
    months = max(6, min(24, int(math.ceil(age_days / 30)) + 3))
    candidates = _planned_candidates(plan)
    total = sum(float(item["planned_amount_cny"]) for item in candidates)
    positions = []
    for item in candidates:
        frame = history_loader(
            item["market"],
            item["symbol"],
            months,
            fetch_months=months,
        )
        normalized = frame.copy()
        normalized["date"] = pd.to_datetime(
            normalized["date"], errors="coerce"
        )
        normalized["close"] = pd.to_numeric(
            normalized["close"], errors="coerce"
        )
        baseline = normalized[
            normalized["date"] <= entry_timestamp
        ].dropna(subset=["date", "close"])
        if baseline.empty:
            raise ValueError(
                f"{item['market']} {item['symbol']} 在决策日前没有可用收盘价"
            )
        row = baseline.sort_values("date").iloc[-1]
        positions.append(
            {
                "market": item["market"],
                "symbol": item["symbol"],
                "name": item["name"],
                "weight_pct": round(
                    float(item["planned_amount_cny"]) / total * 100,
                    6,
                ),
                "entry_price": float(row["close"]),
                "entry_date": decision_date,
                "entry_price_date": pd.Timestamp(
                    row["date"]
                ).strftime("%Y-%m-%d"),
            }
        )
    return positions


def _executed_positions(
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    transactions = (event.get("evidence") or {}).get(
        "transactions", []
    )
    total = sum(
        float(item.get("settled_amount_cny") or 0)
        for item in transactions
    )
    if total <= 0:
        return []
    return [
        {
            "market": str(
                (item.get("transaction") or {}).get("market") or ""
            ),
            "symbol": str(
                (item.get("transaction") or {}).get("code") or ""
            ),
            "name": (
                (item.get("transaction") or {}).get("name")
                or (item.get("transaction") or {}).get("code")
            ),
            "weight_pct": round(
                float(item.get("settled_amount_cny") or 0)
                / total
                * 100,
                6,
            ),
            "entry_price": float(
                (item.get("transaction") or {}).get("unit_price")
                or 0
            ),
            "entry_date": str(
                (item.get("transaction") or {}).get("trade_date")
                or ""
            )[:10],
            "transaction_id": int(item.get("transaction_id") or 0),
        }
        for item in transactions
    ]


def _observe_positions(
    positions: list[dict[str, Any]],
    *,
    basket_id: str,
    user_id: str,
    history_loader: Callable[..., pd.DataFrame],
) -> dict[str, Any]:
    captured = opportunity_service.observe_paper_basket(
        basket_id,
        user_id=user_id,
        repo=_ObservationCapture(positions),
        history_loader=history_loader,
    )
    return captured["payload"]


def _horizon_map(
    observation: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    return {
        int(item.get("trading_days") or 0): item
        for item in observation.get("horizons") or []
    }


def _regime_snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    committee = (plan.get("result") or {}).get(
        "investment_committee"
    ) or {}
    regime = committee.get("market_regime") or {}
    return {
        "status": regime.get("status")
        or (plan.get("result") or {}).get("data_quality", {}).get(
            "regime_status"
        ),
        "label": regime.get("label")
        or regime.get("status")
        or "unknown",
        "risk_budget_multiplier": regime.get(
            "risk_budget_multiplier"
        ),
        "snapshot_id": (plan.get("result") or {}).get(
            "data_lineage", {}
        ).get("regime_snapshot_id"),
    }


def refresh_plan_outcome(
    plan_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    execution_event_id: str | None = None,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    history_loader: Callable[..., pd.DataFrame] | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    current = _now(now)
    plan = _plan_or_raise(
        plan_id,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
    )
    event = (
        learning_repo.get_execution_event(
            execution_event_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if execution_event_id
        else learning_repo.latest_execution(
            plan_id, tenant_id=tenant_id, user_id=user_id
        )
    )
    if event is None or event.get("plan_id") != plan_id:
        raise PortfolioCapitalExecutionNotFoundError(
            "该资本计划还没有可观察的执行事件"
        )
    if not (event.get("integrity") or {}).get("verified"):
        raise PortfolioCapitalLearningConflictError(
            "执行事件完整性校验失败，不能生成结果观察"
        )
    loader = _history_cache(
        history_loader or data_fetch.get_history_months
    )
    planned_positions = _planned_positions(
        plan, history_loader=loader
    )
    executed_positions = _executed_positions(event)
    if not planned_positions or not executed_positions:
        raise PortfolioCapitalLearningConflictError(
            "计划或执行篮子为空，不能生成结果观察"
        )
    planned_observation = _observe_positions(
        planned_positions,
        basket_id=f"planned:{plan_id}",
        user_id=user_id,
        history_loader=loader,
    )
    executed_observation = _observe_positions(
        executed_positions,
        basket_id=f"executed:{event['id']}",
        user_id=user_id,
        history_loader=loader,
    )
    planned_horizons = _horizon_map(planned_observation)
    executed_horizons = _horizon_map(executed_observation)
    horizons = []
    for horizon in HORIZONS:
        planned_metric = planned_horizons.get(horizon) or {}
        executed_metric = executed_horizons.get(horizon) or {}
        complete = bool(
            planned_metric.get("complete")
            and executed_metric.get("complete")
        )
        planned_excess = (
            _number(planned_metric.get("net_excess_return_pct"))
            if complete
            else None
        )
        executed_excess = (
            _number(executed_metric.get("net_excess_return_pct"))
            if complete
            else None
        )
        implementation_gap = (
            executed_excess - planned_excess
            if planned_excess is not None
            and executed_excess is not None
            else None
        )
        horizons.append(
            {
                "trading_days": horizon,
                "status": "complete" if complete else "collecting",
                "exact_horizon": True,
                "planned_decision_excess_return_pct": (
                    round(planned_excess, 3)
                    if planned_excess is not None
                    else None
                ),
                "executed_path_excess_return_pct": (
                    round(executed_excess, 3)
                    if executed_excess is not None
                    else None
                ),
                "implementation_gap_pct": (
                    round(implementation_gap, 3)
                    if implementation_gap is not None
                    else None
                ),
                "planned_outcome_dates": planned_metric.get(
                    "outcome_dates"
                )
                or [],
                "executed_outcome_dates": executed_metric.get(
                    "outcome_dates"
                )
                or [],
                "planned_coverage_pct": planned_metric.get(
                    "covered_position_weight_pct"
                ),
                "executed_coverage_pct": executed_metric.get(
                    "covered_position_weight_pct"
                ),
                "benchmark_coverage_pct": min(
                    float(
                        planned_metric.get(
                            "benchmark_coverage_weight_pct"
                        )
                        or 0
                    ),
                    float(
                        executed_metric.get(
                            "benchmark_coverage_weight_pct"
                        )
                        or 0
                    ),
                ),
            }
        )
    completed = sum(
        1 for item in horizons if item["status"] == "complete"
    )
    status = (
        "complete"
        if completed == len(HORIZONS)
        else "partial"
        if completed
        else "collecting"
    )
    result = {
        "schema_version": "portfolio_capital_outcome_learning.v1",
        "engine_version": ENGINE_VERSION,
        "plan_id": plan_id,
        "execution_event_id": event["id"],
        "observed_at": _iso(current),
        "status": status,
        "completed_horizon_count": completed,
        "horizons": horizons,
        "regime": _regime_snapshot(plan),
        "planned_counterfactual": planned_observation,
        "executed_path": executed_observation,
        "attribution": {
            "selection": (
                "冻结计划在决策日收盘基线上的成本后相对基准结果"
            ),
            "implementation": (
                "真实成交日期、成交标的和确认人民币权重相对计划造成的差值"
            ),
            "formula": (
                "implementation_gap = executed_path_excess - "
                "planned_decision_excess"
            ),
        },
        "boundaries": {
            "personal_account_pnl": False,
            "fx_included": False,
            "dividends_tax_and_financing_included": False,
            "cost_scenario_bps": opportunity_service.PAPER_COST_SCENARIO_BPS,
            "return_guaranteed": False,
            "notice": (
                "这是本币价格收益的同口径决策归因，不是券商账户净盈亏。"
                "跨市场汇率、分红税、融资和实际滑点未完整计入。"
            ),
        },
    }
    evidence = {
        "schema_version": "portfolio_capital_outcome_evidence.v1",
        "engine_version": ENGINE_VERSION,
        "plan": {
            "id": plan["id"],
            "decision_date": plan["decision_date"],
            "evidence_sha256": plan["evidence_sha256"],
            "result_sha256": plan["result_sha256"],
        },
        "execution": {
            "id": event["id"],
            "event_hash": event["event_hash"],
            "evidence_sha256": event["evidence_sha256"],
            "result_sha256": event["result_sha256"],
        },
        "planned_positions": planned_positions,
        "executed_positions": executed_positions,
        "observation_method": {
            "horizons": list(HORIZONS),
            "exact_trading_days": True,
            "cost_scenario_bps": opportunity_service.PAPER_COST_SCENARIO_BPS,
            "benchmarks": opportunity_service.PAPER_BENCHMARKS,
        },
    }
    return learning_repo.create_outcome_snapshot(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        plan_id=plan_id,
        execution_event_id=str(event["id"]),
        observed_at=_iso(current),
        status=status,
        evidence=evidence,
        result=result,
        idempotency_key=(
            f"{event['event_hash']}:{current.date().isoformat()}"
        ),
        now=current,
    )


def list_plan_outcomes(
    plan_id: str,
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 100,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
) -> dict[str, Any]:
    _plan_or_raise(
        plan_id,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
    )
    items = learning_repo.list_outcomes(
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


def get_outcome(
    outcome_id: str,
    *,
    tenant_id: str,
    user_id: str,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
) -> dict[str, Any]:
    item = learning_repo.get_outcome(
        outcome_id, tenant_id=tenant_id, user_id=user_id
    )
    if item is None:
        raise PortfolioCapitalOutcomeNotFoundError(
            "资本计划结果观察不存在"
        )
    return item


def monthly_execution_summary(
    *,
    tenant_id: str,
    user_id: str,
    as_of: dt.date | None = None,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    current_date = as_of or dt.datetime.now(dt.timezone.utc).date()
    month = current_date.strftime("%Y-%m")
    plans = [
        item
        for item in plan_repo.list_plans(
            tenant_id=tenant_id, user_id=user_id, limit=100
        )
        if str(item.get("decision_date") or "").startswith(month)
        and item.get("status") == "ready"
    ]
    confirmed = 0.0
    plan_rows = []
    loader = transaction_loader or storage.list_portfolio_transactions
    current_transactions = loader(user_id=user_id)
    cached_loader = lambda **_: current_transactions
    for plan in plans:
        event = learning_repo.latest_execution(
            str(plan["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if event is not None:
            confirmed += float(event.get("settled_amount_cny") or 0)
        event_verification = (
            verify_execution_event(
                event,
                user_id=user_id,
                transaction_loader=cached_loader,
                all_events=learning_repo.list_executions(
                    str(plan["id"]),
                    tenant_id=tenant_id,
                    user_id=user_id,
                ),
            )
            if event
            else None
        )
        lifecycle = _execution_lifecycle(
            event,
            event_verification,
        )
        plan_rows.append(
            {
                "plan_id": plan["id"],
                "decision_date": plan["decision_date"],
                "created_at": plan["created_at"],
                "planned_amount_cny": (
                    (plan.get("result") or {}).get("capital") or {}
                ).get("planned_deployment_cny"),
                "execution_event_id": (
                    event.get("id") if event else None
                ),
                "execution_event_hash": (
                    event.get("event_hash") if event else None
                ),
                "execution_verified": (
                    event_verification.get("verified")
                    if event_verification
                    else None
                ),
                "execution_status": lifecycle,
                "confirmed_settled_amount_cny": (
                    float(event.get("settled_amount_cny") or 0)
                    if event
                    else 0.0
                ),
            }
        )
    plan_rows.sort(
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("plan_id") or ""),
        ),
        reverse=True,
    )
    latest = plan_rows[0] if plan_rows else None
    blocker = (
        "previous_capital_plan_open"
        if latest
        and latest.get("execution_status")
        in {
            "awaiting_execution",
            "partial",
            "deviated",
            "integrity_failed",
        }
        else None
    )
    return {
        "schema_version": "portfolio_capital_month_execution.v1",
        "month": month,
        "confirmed_settled_amount_cny": round(confirmed, 2),
        "ready_plan_count": len(plan_rows),
        "latest_ready_plan": latest,
        "blocking_reason": blocker,
        "plans": plan_rows,
    }


def _mean_ci(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "sample_count": 0,
            "mean": None,
            "descriptive_ci95": None,
        }
    mean = statistics.fmean(values)
    interval = None
    if len(values) >= 4:
        margin = (
            1.96
            * statistics.stdev(values)
            / math.sqrt(len(values))
        )
        interval = {
            "lower": round(mean - margin, 3),
            "upper": round(mean + margin, 3),
            "method": "normal descriptive interval",
        }
    return {
        "sample_count": len(values),
        "mean": round(mean, 3),
        "descriptive_ci95": interval,
    }


def build_learning_scorecard(
    *,
    tenant_id: str,
    user_id: str,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
) -> dict[str, Any]:
    events = [
        item
        for item in learning_repo.list_execution_scopes(limit=2000)
        if item.get("tenant_id") == tenant_id
        and item.get("user_id") == user_id
        and (item.get("integrity") or {}).get("verified")
    ]
    outcomes = learning_repo.list_outcomes(
        tenant_id=tenant_id, user_id=user_id, limit=1000
    )
    latest_by_event: dict[str, dict[str, Any]] = {}
    for item in outcomes:
        event_id = str(item.get("execution_event_id") or "")
        if (
            event_id not in latest_by_event
            and (item.get("integrity") or {}).get("verified")
        ):
            latest_by_event[event_id] = item
    event_ids = {str(item["id"]) for item in events}
    latest = [
        item
        for event_id, item in latest_by_event.items()
        if event_id in event_ids
    ]
    horizon_rows = []
    regime_rows: dict[str, list[float]] = {}
    for item in latest:
        result = item.get("result") or {}
        regime = str(
            (result.get("regime") or {}).get("label") or "unknown"
        )
        for metric in result.get("horizons") or []:
            if metric.get("status") != "complete":
                continue
            executed = _number(
                metric.get("executed_path_excess_return_pct")
            )
            planned = _number(
                metric.get("planned_decision_excess_return_pct")
            )
            gap = _number(metric.get("implementation_gap_pct"))
            if executed is None or planned is None or gap is None:
                continue
            horizon_rows.append(
                {
                    "plan_id": item["plan_id"],
                    "execution_event_id": item[
                        "execution_event_id"
                    ],
                    "outcome_id": item["id"],
                    "trading_days": int(metric["trading_days"]),
                    "planned_excess_return_pct": planned,
                    "executed_excess_return_pct": executed,
                    "implementation_gap_pct": gap,
                    "regime": regime,
                }
            )
            if int(metric["trading_days"]) == 20:
                regime_rows.setdefault(regime, []).append(executed)
    horizon_scorecards = []
    for horizon in HORIZONS:
        scoped = [
            item
            for item in horizon_rows
            if item["trading_days"] == horizon
        ]
        planned_values = [
            float(item["planned_excess_return_pct"])
            for item in scoped
        ]
        executed_values = [
            float(item["executed_excess_return_pct"])
            for item in scoped
        ]
        gap_values = [
            float(item["implementation_gap_pct"])
            for item in scoped
        ]
        executed_stats = _mean_ci(executed_values)
        horizon_scorecards.append(
            {
                "trading_days": horizon,
                "mature_plan_count": len(scoped),
                "positive_excess_rate_pct": (
                    round(
                        sum(value > 0 for value in executed_values)
                        / len(executed_values)
                        * 100,
                        2,
                    )
                    if executed_values
                    else None
                ),
                "planned_decision": _mean_ci(planned_values),
                "executed_path": executed_stats,
                "implementation_gap": _mean_ci(gap_values),
                "worst_executed_excess_return_pct": (
                    round(min(executed_values), 3)
                    if executed_values
                    else None
                ),
                "evidence_status": (
                    "decision_eligible"
                    if len(scoped) >= 6
                    else "collecting"
                ),
            }
        )
    primary = next(
        item
        for item in horizon_scorecards
        if item["trading_days"] == 20
    )
    primary_mean = (primary["executed_path"] or {}).get("mean")
    primary_gap = (primary["implementation_gap"] or {}).get("mean")
    if primary["mature_plan_count"] < 6:
        status = "collecting"
        next_action = (
            "继续按冻结计划记录真实成交，至少积累 6 个独立的 20 交易日结果后再调整资本规则。"
        )
    elif primary_mean is not None and primary_mean < 0:
        status = "review_selection"
        next_action = (
            "20 日执行路径平均跑输基准；暂停扩大试投，优先复核选股与市场状态适配。"
        )
    elif primary_gap is not None and primary_gap < -1:
        status = "review_execution"
        next_action = (
            "计划本身优于执行路径，但实施差值偏低；优先复核成交时点、错配和超额投入。"
        )
    else:
        status = "stable"
        next_action = (
            "当前样本未显示明显选择或执行劣化；仍按投资政策的小额上限推进并持续观察。"
        )
    return {
        "schema_version": "portfolio_capital_learning_scorecard.v1",
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(),
        "status": status,
        "next_action": next_action,
        "execution_plan_count": len(events),
        "observed_plan_count": len(latest),
        "horizons": horizon_scorecards,
        "regime_breakdown_20d": [
            {
                "regime": regime,
                **_mean_ci(values),
                "positive_excess_rate_pct": round(
                    sum(value > 0 for value in values)
                    / len(values)
                    * 100,
                    2,
                ),
            }
            for regime, values in sorted(regime_rows.items())
        ],
        "methodology": (
            "每个计划只使用最新累计执行事件及其最新完整观察；"
            "只统计计划篮子和执行篮子都达到至少 90% 覆盖的精确 5/20/60 交易日结果。"
        ),
        "boundaries": {
            "minimum_primary_sample": 6,
            "overlapping_samples_possible": True,
            "descriptive_not_predictive": True,
            "return_guaranteed": False,
        },
    }


def learning_overview(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 50,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    plans = plan_repo.list_plans(
        tenant_id=tenant_id, user_id=user_id, limit=limit
    )
    loader = transaction_loader or storage.list_portfolio_transactions
    current_transactions = loader(user_id=user_id)
    cached_loader = lambda **_: current_transactions
    items = []
    for plan in plans:
        event = learning_repo.latest_execution(
            str(plan["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        outcome = (
            learning_repo.latest_outcome(
                str(event["id"]),
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if event
            else None
        )
        verification = (
            verify_execution_event(
                event,
                user_id=user_id,
                transaction_loader=cached_loader,
                all_events=learning_repo.list_executions(
                    str(plan["id"]),
                    tenant_id=tenant_id,
                    user_id=user_id,
                ),
            )
            if event
            else None
        )
        items.append(
            {
                "plan_id": plan["id"],
                "plan_status": plan["status"],
                "decision_date": plan["decision_date"],
                "created_at": plan["created_at"],
                "primary_action": (plan.get("result") or {}).get(
                    "primary_action"
                ),
                "capital": (plan.get("result") or {}).get("capital"),
                "lifecycle_status": _execution_lifecycle(
                    event,
                    verification,
                    plan_status=str(plan.get("status") or ""),
                ),
                "latest_execution": (
                    {
                        key: event.get(key)
                        for key in (
                            "id",
                            "event_no",
                            "status",
                            "settled_amount_cny",
                            "event_hash",
                            "created_at",
                        )
                    }
                    | {"integrity": event.get("integrity")}
                    if event
                    else None
                ),
                "latest_outcome": (
                    {
                        key: outcome.get(key)
                        for key in (
                            "id",
                            "status",
                            "observed_at",
                            "created_at",
                        )
                    }
                    | {
                        "completed_horizon_count": (
                            outcome.get("result") or {}
                        ).get("completed_horizon_count"),
                        "horizons": (
                            outcome.get("result") or {}
                        ).get("horizons")
                        or [],
                        "integrity": outcome.get("integrity"),
                    }
                    if outcome
                    else None
                ),
            }
        )
    month = monthly_execution_summary(
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
        learning_repo=learning_repo,
        transaction_loader=cached_loader,
    )
    counts: dict[str, int] = {}
    for item in items:
        lifecycle = str(item["lifecycle_status"])
        counts[lifecycle] = counts.get(lifecycle, 0) + 1
    return {
        "schema_version": "portfolio_capital_learning_overview.v1",
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(),
        "summary": {
            "plan_count": len(items),
            "lifecycle_counts": counts,
            "confirmed_month_to_date_cny": month[
                "confirmed_settled_amount_cny"
            ],
        },
        "month_execution": month,
        "scorecard": build_learning_scorecard(
            tenant_id=tenant_id,
            user_id=user_id,
            learning_repo=learning_repo,
        ),
        "items": items,
        "boundaries": {
            "execution_authorized": False,
            "return_guaranteed": False,
            "notice": (
                "学习结果用于识别策略选择和执行偏差，不会自动放大仓位或创建订单。"
            ),
        },
    }


def queue_plan_outcome_refresh(
    plan_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    plan_repo: PortfolioCapitalRepository = plan_repository,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    transaction_loader: Callable[..., list[dict[str, Any]]]
    | None = None,
    jobs: BackgroundJobRepository | None = None,
    enqueue: Callable[[dict[str, Any], BackgroundJobRepository], str]
    | None = None,
    embedded_dispatch: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Persist and dispatch a non-blocking, user-scoped outcome refresh."""

    context = get_plan_execution_context(
        plan_id,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_repo=plan_repo,
        learning_repo=learning_repo,
        transaction_loader=transaction_loader,
    )
    latest = context.get("latest_execution")
    if latest is None:
        raise PortfolioCapitalLearningConflictError(
            "资本计划尚未绑定真实成交，不能开始结果观察"
        )
    verification = context.get("execution_verification") or {}
    if not verification.get("verified"):
        raise PortfolioCapitalLearningConflictError(
            "执行事件或当前交易流水完整性校验失败，结果观察已暂停"
        )

    job_repo = jobs or BackgroundJobRepository()
    payload = {
        "operation": "portfolio.capital_outcome",
        "input": {
            "plan_id": plan_id,
            "execution_event_id": latest["id"],
            "tenant_id": tenant_id,
            "user_id": user_id,
            "actor_id": actor_id,
        },
    }
    job, created = job_repo.create_job(
        job_type="market_data_operation",
        queue_name=QUEUE_MARKET,
        payload=payload,
        tenant_id=tenant_id,
        user_id=user_id,
        max_attempts=2 if uses_celery_queue() else 1,
    )

    dispatch_state = "deduplicated"
    if created and uses_celery_queue():
        try:
            (enqueue or enqueue_background_job)(job, job_repo)
            dispatch_state = "worker_queued"
        except (
            TaskQueueConfigurationError,
            TaskQueueUnavailableError,
        ):
            # The durable scheduler will dispatch this queued job after the
            # broker recovers. Do not turn a recoverable queue outage into a
            # duplicate synchronous market-data request.
            dispatch_state = "deferred"
        except Exception:
            # A broker can fail between readiness probing and send_task.
            # The PostgreSQL job remains queued for the durable dispatcher.
            dispatch_state = "deferred"
    elif created and embedded_dispatch is not None:
        embedded_dispatch(str(job["id"]), str(job_repo.target))
        dispatch_state = "embedded"
    elif created:
        dispatch_state = "deferred"

    return {
        "job_id": str(job["id"]),
        "status": str(job["status"]),
        "created": bool(created),
        "dispatch_state": dispatch_state,
        "plan_id": plan_id,
        "execution_event_id": str(latest["id"]),
        "poll_url": (
            "/api/portfolio/capital-decision/outcome-jobs/"
            f"{job['id']}"
        ),
        "created_at": job.get("created_at"),
    }


def execute_embedded_plan_outcome_job(
    job_id: str,
    database_target: str | None = None,
) -> dict[str, Any]:
    """Run one durable market outcome job after an embedded API response."""

    jobs = BackgroundJobRepository(database_target)
    worker_id = f"embedded-capital-outcome:{threading.get_ident()}"
    job = jobs.claim_job(
        str(job_id),
        worker_id,
        lease_seconds=360,
    )
    if job is None:
        return {"job_id": str(job_id), "status": "not_claimed"}
    if job.get("status") in {
        "succeeded",
        "partial",
        "failed",
        "cancelled",
    }:
        return {"job_id": str(job_id), "status": str(job["status"])}

    payload = job.get("payload") or {}
    operation = str(payload.get("operation") or "")
    operation_input = dict(payload.get("input") or {})
    valid_scope = (
        job.get("job_type") == "market_data_operation"
        and job.get("queue_name") == QUEUE_MARKET
        and operation == "portfolio.capital_outcome"
        and str(operation_input.get("user_id") or "")
        == str(job.get("user_id") or "")
        and str(operation_input.get("tenant_id") or "")
        == str(job.get("tenant_id") or "")
    )
    if not valid_scope:
        failed = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="CAPITAL_OUTCOME_JOB_SCOPE_INVALID",
            error_message="capital outcome job scope is invalid",
            retryable=False,
        )
        return {"job_id": str(job_id), "status": str(failed["status"])}

    try:
        from market_data_operations import execute_operation

        result = execute_operation(operation, operation_input)
        completed = jobs.complete_job(
            str(job_id),
            worker_id,
            result,
        )
        return {
            "job_id": str(job_id),
            "status": str(completed["status"]),
        }
    except Exception as error:
        failed = jobs.fail_job(
            str(job_id),
            worker_id,
            error_code="CAPITAL_OUTCOME_REFRESH_FAILED",
            error_message=str(error),
            retryable=False,
        )
        return {"job_id": str(job_id), "status": str(failed["status"])}


def get_plan_outcome_refresh_job(
    job_id: str,
    *,
    tenant_id: str,
    user_id: str,
    jobs: BackgroundJobRepository | None = None,
) -> dict[str, Any]:
    """Return a sanitized outcome-refresh job only to its owning scope."""

    job_repo = jobs or BackgroundJobRepository()
    job = job_repo.get_job(str(job_id), include_payload=True)
    payload = (job or {}).get("payload") or {}
    operation_input = payload.get("input") or {}
    if (
        job is None
        or str(job.get("tenant_id") or "") != str(tenant_id)
        or str(job.get("user_id") or "") != str(user_id)
        or str(payload.get("operation") or "")
        != "portfolio.capital_outcome"
        or str(operation_input.get("user_id") or "") != str(user_id)
    ):
        raise PortfolioCapitalOutcomeJobNotFoundError(
            "资本计划结果观察任务不存在"
        )

    response = {
        "job_id": str(job["id"]),
        "status": str(job["status"]),
        "plan_id": str(operation_input.get("plan_id") or ""),
        "execution_event_id": str(
            operation_input.get("execution_event_id") or ""
        ),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "attempt_count": int(job.get("attempt_count") or 0),
        "audit": job_repo.verify_event_chain(str(job_id)),
    }
    if job["status"] in {"succeeded", "partial"}:
        response["result"] = job.get("result")
    elif job["status"] in {"failed", "cancelled"}:
        response["error"] = {
            "code": job.get("error_code") or "CAPITAL_OUTCOME_JOB_FAILED",
            "message": job.get("error_message")
            or "资本计划结果观察未完成",
        }
    return response


def dispatch_due_outcomes(
    *,
    now: dt.datetime | None = None,
    limit: int = 50,
    minimum_interval_hours: float = 18.0,
    learning_repo: PortfolioCapitalLearningRepository = learning_repository,
    jobs: BackgroundJobRepository | None = None,
    enqueue: Callable[[dict[str, Any], BackgroundJobRepository], str]
    | None = None,
) -> dict[str, Any]:
    current = _now(now)
    if not uses_celery_queue() and jobs is None:
        return {
            "status": "embedded",
            "eligible": 0,
            "created": 0,
            "deduplicated": 0,
            "skipped": 0,
        }
    job_repo = jobs or BackgroundJobRepository()
    dispatch = enqueue or enqueue_background_job
    created_count = 0
    deduplicated = 0
    skipped = 0
    eligible = 0
    errors = []
    job_ids = []
    for event in learning_repo.list_execution_scopes(
        limit=max(100, limit * 5)
    ):
        if created_count >= limit:
            break
        if not (event.get("integrity") or {}).get("verified"):
            skipped += 1
            continue
        tenant_id = str(event.get("tenant_id") or "public")
        user_id = str(event.get("user_id") or "")
        latest = learning_repo.latest_outcome(
            str(event["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if latest and not (latest.get("integrity") or {}).get(
            "verified"
        ):
            skipped += 1
            continue
        if (latest or {}).get("status") == "complete":
            skipped += 1
            continue
        last_observed = _datetime(
            (latest or {}).get("observed_at")
            or (latest or {}).get("created_at")
        )
        if (
            last_observed
            and (current - last_observed).total_seconds()
            < minimum_interval_hours * 3600
        ):
            skipped += 1
            continue
        eligible += 1
        payload = {
            "operation": "portfolio.capital_outcome",
            "input": {
                "plan_id": event["plan_id"],
                "execution_event_id": event["id"],
                "tenant_id": tenant_id,
                "user_id": user_id,
                "actor_id": "capital-learning-scheduler",
            },
        }
        try:
            job, created = job_repo.create_job(
                job_type="market_data_operation",
                queue_name=QUEUE_MARKET,
                payload=payload,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=(
                    f"capital-outcome:{event['event_hash']}:"
                    f"{current.date().isoformat()}"
                ),
                max_attempts=2,
            )
            if created:
                dispatch(job, job_repo)
                created_count += 1
                job_ids.append(str(job["id"]))
            else:
                deduplicated += 1
        except Exception as error:
            errors.append(
                {
                    "execution_event_id": event["id"],
                    "error": str(error)[:240],
                }
            )
    return {
        "status": "partial" if errors else "succeeded",
        "eligible": eligible,
        "created": created_count,
        "deduplicated": deduplicated,
        "skipped": skipped,
        "job_ids": job_ids,
        "errors": errors,
    }
