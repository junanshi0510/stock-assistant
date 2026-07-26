# -*- coding: utf-8 -*-
"""Pre-registered calendar orchestration for causal quant research."""

from __future__ import annotations

import calendar
import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo

import quant_selection_forward_service
import quant_selection_service
from quant_research_program_repository import (
    QuantResearchProgramRepository,
    repository,
)
from quant_selection_forward_repository import (
    QuantSelectionForwardRepository,
    repository as forward_repository,
)
from quant_selection_repository import (
    QuantSelectionRepository,
    repository as quant_repository,
)


MARKET_TIMEZONES = {
    "A股": "Asia/Shanghai",
    "港股": "Asia/Hong_Kong",
    "美股": "America/New_York",
}
CADENCE_MONTHS = {"monthly": 1, "quarterly": 3}
PROGRAM_ACKNOWLEDGEMENT = (
    "我确认策略、因子权重和日历在首个批次前冻结；所有到期批次的"
    "通过、未通过和失败结果都会保留，不允许事后删除失败样本；"
    "系统只进行研究和前向纸面验证，不自动下单。"
)


def _parse_utc(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _add_months(value: dt.date, months: int) -> dt.date:
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def _default_first_date(now: dt.datetime, timezone: str) -> dt.date:
    local = now.astimezone(ZoneInfo(timezone))
    first_next_month = _add_months(local.date().replace(day=1), 1)
    return first_next_month


def normalize_schedule(
    payload: dict[str, Any] | None,
    *,
    market: str,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = dict(payload or {})
    cadence = str(source.get("cadence") or "monthly")
    if cadence not in CADENCE_MONTHS:
        raise ValueError("研究计划频率只能是 monthly 或 quarterly")
    timezone = MARKET_TIMEZONES[market]
    time_text = str(source.get("run_time_local") or "20:30")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
        raise ValueError("本地运行时间必须使用 HH:MM")
    hour, minute = (int(value) for value in time_text.split(":"))
    planned_cycles = int(source.get("planned_cycles", 6))
    if planned_cycles < 6 or planned_cycles > 24:
        raise ValueError("预登记批次数必须在 6-24 之间")
    current = _parse_utc(now)
    first_text = str(source.get("first_run_date") or "")
    try:
        first_date = (
            dt.date.fromisoformat(first_text)
            if first_text
            else _default_first_date(current, timezone)
        )
    except ValueError as error:
        raise ValueError("首次运行日期必须使用 YYYY-MM-DD") from error
    if first_date.day > 28:
        raise ValueError("首次运行日期必须是每月 1-28 日，避免月末漂移")
    zone = ZoneInfo(timezone)
    first_local = dt.datetime.combine(
        first_date, dt.time(hour, minute), tzinfo=zone
    )
    if first_local.astimezone(dt.timezone.utc) <= current:
        raise ValueError("首次运行时间必须晚于当前时间")

    slots = []
    for index in range(planned_cycles):
        local_date = _add_months(
            first_date, CADENCE_MONTHS[cadence] * index
        )
        local_value = dt.datetime.combine(
            local_date, dt.time(hour, minute), tzinfo=zone
        )
        utc_value = local_value.astimezone(dt.timezone.utc)
        slots.append(
            {
                "sequence_no": index + 1,
                "slot_key": (
                    f"{local_value.strftime('%Y-%m-%dT%H:%M')}|"
                    f"{timezone}"
                ),
                "scheduled_local": local_value.isoformat(
                    timespec="minutes"
                ),
                "scheduled_for": utc_value.isoformat(
                    timespec="milliseconds"
                ),
            }
        )
    schedule = {
        "schema_version": "quant_research_schedule.v1",
        "cadence": cadence,
        "cadence_months": CADENCE_MONTHS[cadence],
        "timezone": timezone,
        "run_time_local": time_text,
        "first_run_date": first_date.isoformat(),
        "planned_cycles": planned_cycles,
        "all_slots_preregistered": True,
        "missed_and_failed_slots_retained": True,
    }
    return schedule, slots


def create_program(
    payload: dict[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
    repo: QuantResearchProgramRepository = repository,
) -> dict[str, Any]:
    if not bool(payload.get("acknowledged")):
        raise ValueError("必须确认固定日历、失败留痕和仅纸面验证边界")
    policy = quant_selection_service.normalize_policy(
        dict(payload.get("policy") or {})
    )
    schedule, slots = normalize_schedule(
        payload.get("schedule"),
        market=policy["market"],
        now=now,
    )
    acknowledgement = {
        "schema_version": "quant_research_acknowledgement.v1",
        "accepted": True,
        "statement": PROGRAM_ACKNOWLEDGEMENT,
        "policy_frozen_before_first_cycle": True,
        "failed_cycles_retained": True,
        "automatic_broker_orders": False,
    }
    return repo.create_program(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        name=str(payload.get("name") or policy["name"])[:100],
        policy=policy,
        schedule=schedule,
        acknowledgement=acknowledgement,
        slots=slots,
    )


def overview(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 30,
    repo: QuantResearchProgramRepository = repository,
) -> dict[str, Any]:
    programs = repo.list_programs(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    return {
        "schema_version": "quant_research_program_overview.v1",
        "programs": programs,
        "summary": {
            "program_count": len(programs),
            "active_count": sum(
                program["state"] == "active" for program in programs
            ),
            "preregistered_cycle_count": sum(
                int(program["summary"]["planned_cycle_count"])
                for program in programs
            ),
            "completed_cycle_count": sum(
                int(program["summary"]["completed_cycle_count"])
                for program in programs
            ),
            "failed_cycle_count": sum(
                int(program["summary"]["failed_count"])
                for program in programs
            ),
        },
        "methodology": {
            "schedule": (
                "创建时一次性登记全部月度或季度槽位；系统不能删除失败槽位"
            ),
            "promotion": (
                "每个到期槽位独立运行冻结策略；只有历史门槛通过后才自动"
                "冻结纸面指令并接入无前视前向验证"
            ),
            "execution": "不连接券商、不生成股数、不自动提交订单",
        },
    }


def _terminal_outcome(run: dict[str, Any]) -> dict[str, Any]:
    result = run.get("result") or {}
    gate = result.get("promotion_gate") or {}
    return {
        "run_id": run.get("id"),
        "run_status": run.get("status"),
        "policy_sha256": run.get("policy_sha256"),
        "result_sha256": run.get("result_sha256"),
        "engine_version": run.get("engine_version"),
        "promotion_status": gate.get("status"),
        "promotion_passed_count": gate.get("passed_count"),
        "promotion_total_count": gate.get("total_count"),
        "paper_shadow_eligible": bool(
            gate.get("paper_shadow_eligible")
        ),
        "failed_checks": [
            {
                "code": item.get("code"),
                "label": item.get("label"),
                "detail": item.get("detail"),
            }
            for item in gate.get("checks") or []
            if not item.get("passed")
        ],
    }


def _reconcile_cycle(
    cycle: dict[str, Any],
    *,
    actor_id: str,
    program_repo: QuantResearchProgramRepository,
    quant_repo: QuantSelectionRepository,
    forward_repo: QuantSelectionForwardRepository,
) -> dict[str, Any]:
    program = cycle["program"]
    cycle_id = str(cycle["id"])
    tenant_id = str(cycle["tenant_id"])
    user_id = str(cycle["user_id"])
    if not bool((cycle.get("integrity") or {}).get("verified")) or not bool(
        (program.get("integrity") or {}).get("verified")
    ):
        program_repo.complete_cycle(
            cycle_id,
            status="failed",
            actor_id=actor_id,
            error_code="PROGRAM_INTEGRITY_FAILED",
            error_message="研究计划或批次完整性校验失败",
            outcome={
                "stage": "integrity",
                "program_verified": bool(
                    (program.get("integrity") or {}).get("verified")
                ),
                "cycle_verified": bool(
                    (cycle.get("integrity") or {}).get("verified")
                ),
            },
        )
        return {"cycle_id": cycle_id, "action": "integrity_failed"}
    if cycle["status"] == "scheduled":
        if program.get("state") != "active":
            return {"cycle_id": cycle_id, "action": "retired"}
        if not program_repo.claim_cycle(cycle_id, actor_id=actor_id):
            return {"cycle_id": cycle_id, "action": "already_claimed"}
        try:
            run = quant_selection_service.start_run(
                program["policy"],
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                repo=quant_repo,
            )
            cycle = program_repo.attach_run(
                cycle_id,
                run_id=str(run["id"]),
                run_status=str(run["status"]),
                actor_id=actor_id,
            )
        except Exception as error:
            program_repo.complete_cycle(
                cycle_id,
                status="failed",
                actor_id=actor_id,
                error_code="PROGRAM_RUN_DISPATCH_FAILED",
                error_message=str(error),
                outcome={
                    "stage": "dispatch",
                    "error": str(error)[:1000],
                },
            )
            return {"cycle_id": cycle_id, "action": "dispatch_failed"}

    run_id = cycle.get("run_id")
    if not run_id:
        program_repo.complete_cycle(
            cycle_id,
            status="failed",
            actor_id=actor_id,
            error_code="PROGRAM_ORPHANED_DISPATCH",
            error_message="调度已认领但没有绑定量化运行",
            outcome={"stage": "dispatch", "orphaned": True},
        )
        return {"cycle_id": cycle_id, "action": "orphaned"}
    run = quant_selection_service.refresh_run_status(
        str(run_id),
        tenant_id=tenant_id,
        user_id=user_id,
        repo=quant_repo,
    )
    if run is None:
        program_repo.complete_cycle(
            cycle_id,
            status="failed",
            actor_id=actor_id,
            error_code="PROGRAM_RUN_NOT_FOUND",
            error_message="已绑定量化运行不存在",
            outcome={"run_id": run_id, "missing": True},
        )
        return {"cycle_id": cycle_id, "action": "run_missing"}
    if run["status"] in {"queued", "running"}:
        program_repo.mark_run_status(
            cycle_id,
            status=(
                "run_running"
                if run["status"] == "running"
                else "run_queued"
            ),
            actor_id=actor_id,
        )
        return {"cycle_id": cycle_id, "action": run["status"]}
    if run["status"] in {"failed", "cancelled"}:
        program_repo.complete_cycle(
            cycle_id,
            status="failed",
            actor_id=actor_id,
            error_code=str(
                run.get("error_code") or "PROGRAM_QUANT_RUN_FAILED"
            ),
            error_message=str(
                run.get("error_message") or "量化运行失败"
            ),
            outcome=_terminal_outcome(run),
        )
        return {"cycle_id": cycle_id, "action": "run_failed"}

    outcome = _terminal_outcome(run)
    if not outcome["paper_shadow_eligible"]:
        program_repo.complete_cycle(
            cycle_id,
            status="research_only",
            actor_id=actor_id,
            outcome=outcome,
        )
        return {"cycle_id": cycle_id, "action": "research_only"}

    mandate, _ = quant_selection_service.freeze_shadow_mandate(
        str(run["id"]),
        acknowledged=True,
        expected_result_sha256=str(run["result_sha256"]),
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        repo=quant_repo,
    )
    validation, _ = quant_selection_forward_service.enroll_validation(
        str(mandate["id"]),
        acknowledged=True,
        expected_snapshot_sha256=str(mandate["snapshot_sha256"]),
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        quant_repo=quant_repo,
        forward_repo=forward_repo,
    )
    outcome["mandate_id"] = mandate["id"]
    outcome["mandate_snapshot_sha256"] = mandate["snapshot_sha256"]
    outcome["validation_id"] = validation["id"]
    program_repo.complete_cycle(
        cycle_id,
        status="forward_enrolled",
        actor_id=actor_id,
        outcome=outcome,
        mandate_id=str(mandate["id"]),
        validation_id=str(validation["id"]),
    )
    return {"cycle_id": cycle_id, "action": "forward_enrolled"}


def reconcile_due_programs(
    *,
    now: dt.datetime | None = None,
    limit: int = 50,
    tenant_id: str | None = None,
    user_id: str | None = None,
    program_id: str | None = None,
    actor_id: str = "system:quant-program-scheduler",
    program_repo: QuantResearchProgramRepository = repository,
    quant_repo: QuantSelectionRepository = quant_repository,
    forward_repo: QuantSelectionForwardRepository = forward_repository,
) -> dict[str, Any]:
    candidates = program_repo.list_reconcilable(
        now=now,
        limit=limit,
        tenant_id=tenant_id,
        user_id=user_id,
        program_id=program_id,
    )
    actions = []
    errors = []
    for cycle in candidates:
        try:
            actions.append(
                _reconcile_cycle(
                    cycle,
                    actor_id=actor_id,
                    program_repo=program_repo,
                    quant_repo=quant_repo,
                    forward_repo=forward_repo,
                )
            )
        except Exception as error:
            errors.append(
                {
                    "cycle_id": cycle["id"],
                    "error": str(error)[:1000],
                }
            )
    return {
        "processed": len(actions),
        "errors": errors,
        "actions": actions,
    }


def retire_program(
    program_id: str,
    *,
    reason: str,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    repo: QuantResearchProgramRepository = repository,
) -> dict[str, Any]:
    if len(str(reason).strip()) < 8:
        raise ValueError("终止原因至少需要 8 个字符，便于审计")
    return repo.retire_program(
        program_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        reason=str(reason).strip(),
    )
