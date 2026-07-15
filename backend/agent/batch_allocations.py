# -*- coding: utf-8 -*-
"""Application service for immutable portfolio-level Agent batch allocations."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .batches import summarize_batch
from .repository import AgentRepository, RUN_TERMINAL_STATUSES
from strategies.portfolio_batch_allocation import evaluate_portfolio_batch_allocation


class BatchAllocationValidationError(ValueError):
    pass


class BatchAllocationConflictError(RuntimeError):
    pass


def create_batch_allocation(
    repository: AgentRepository,
    batch: dict[str, Any],
    *,
    expected_batch_input_hash: str,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    expected = str(expected_batch_input_hash or "").strip()
    actual = str(batch.get("input_hash") or "")
    if expected != actual:
        raise BatchAllocationConflictError("批次输入已变化，请刷新后重新复核")

    existing = batch.get("allocation_event")
    if existing is not None:
        if not existing.get("integrity_verified"):
            raise BatchAllocationConflictError("已保存的批次资金分配事件完整性失败")
        return existing, False

    items = batch.get("items") or []
    if not items or any(
        str((item.get("run") or {}).get("status") or "") not in RUN_TERMINAL_STATUSES
        for item in items
    ):
        raise BatchAllocationConflictError("全部子 Run 到达终态后才能生成组合资金分配")
    if not (batch.get("input") or {}).get("include_portfolio_context"):
        raise BatchAllocationValidationError("批次未启用真实持仓与投资政策，不能生成组合资金分配")

    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    summary = summarize_batch(batch)
    payload = evaluate_portfolio_batch_allocation(
        batch,
        summary.get("holding_overlap") or {},
        generated_at=current.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    try:
        return repository.create_batch_allocation_event(
            str(batch.get("id") or ""),
            payload,
            user_id=user_id,
            actor_id=actor_id,
        )
    except KeyError as error:
        raise BatchAllocationConflictError(str(error)) from error
    except ValueError as error:
        raise BatchAllocationConflictError(str(error)) from error
