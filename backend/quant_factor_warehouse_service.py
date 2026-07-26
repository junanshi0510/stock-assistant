# -*- coding: utf-8 -*-
"""Quota-aware point-in-time factor collection, coverage, and replay."""

from __future__ import annotations

import datetime as dt
import math
import os
import re
from typing import Any

import pandas as pd

import config
from background_jobs import sanitize_worker_error
from quant_factor_repository import (
    QuantFactorConflictError,
    QuantFactorRepository,
    repository,
    sha256_payload,
)


WAREHOUSE_VERSION = "quant_factor_warehouse@1.0.0"
PROVIDER = "tushare"
CHINA_TIMEZONE = dt.timezone(dt.timedelta(hours=8))
DAILY_FIELDS = (
    "ts_code,trade_date,close,turnover_rate_f,pe_ttm,pb,dv_ttm,"
    "total_mv,circ_mv"
)
FINANCIAL_FIELDS = (
    "ts_code,ann_date,end_date,roe,grossprofit_margin,ocf_to_or,"
    "debt_to_assets,update_flag"
)
DEFAULT_RESEARCH_SYMBOLS = (
    "600519",
    "300750",
    "601318",
    "600036",
    "000858",
    "000333",
    "002594",
    "600900",
    "601899",
    "600276",
    "000651",
    "601088",
)


def _china_now() -> dt.datetime:
    return dt.datetime.now(CHINA_TIMEZONE)


def _parse_date(value: Any, name: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须使用 YYYY-MM-DD") from error
    return parsed


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().split(".")[0]
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError(f"A股代码格式无效:{symbol or '(空)'}")
    return symbol


def _tushare_code(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    if normalized.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif normalized.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{normalized}.{suffix}"


def _tushare_pro():
    token = str(config.TUSHARE_TOKEN or "").strip()
    if not token:
        raise ValueError("A股时点因子仓库需要配置 TUSHARE_TOKEN")
    import tushare as ts

    return ts.pro_api(token)


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    output = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            output.append(cursor)
        cursor += dt.timedelta(days=1)
    return output


def _latest_candidate_weekdays(
    *,
    now: dt.datetime | None = None,
    limit: int = 10,
) -> list[dt.date]:
    current = (now or _china_now()).astimezone(CHINA_TIMEZONE)
    cursor = current.date()
    if current.hour < 18:
        cursor -= dt.timedelta(days=1)
    output = []
    while len(output) < max(1, int(limit)):
        if cursor.weekday() < 5:
            output.append(cursor)
        cursor -= dt.timedelta(days=1)
    return output


def normalize_backfill_policy(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    source = dict(payload or {})
    dataset = str(source.get("dataset") or "valuation_daily")
    if dataset not in {"valuation_daily", "financial_indicator"}:
        raise ValueError("因子数据集只能是每日估值或公告日财务指标")
    end = _parse_date(
        source.get("end_date") or _china_now().date().isoformat(),
        "结束日期",
    )
    start = _parse_date(
        source.get("start_date")
        or (end - dt.timedelta(days=3 * 366)).isoformat(),
        "开始日期",
    )
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if end > _china_now().date():
        raise ValueError("回填结束日期不能晚于今天")
    maximum_days = max(
        366,
        int(os.getenv("QUANT_FACTOR_MAX_BACKFILL_DAYS", "1830")),
    )
    if (end - start).days > maximum_days:
        raise ValueError(
            f"单个回填计划最多覆盖 {maximum_days} 个自然日"
        )
    symbols = sorted(
        {
            _normalize_symbol(item)
            for item in (source.get("symbols") or [])
            if str(item or "").strip()
        }
    )
    if dataset == "financial_indicator" and not symbols:
        raise ValueError("公告日财务指标回填至少需要 1 只 A 股")
    if len(symbols) > 40:
        raise ValueError("公告日财务指标回填最多支持 40 只 A 股")
    if dataset == "valuation_daily":
        symbols = []
    return {
        "schema_version": "quant_factor_backfill_policy.v1",
        "warehouse_version": WAREHOUSE_VERSION,
        "dataset": dataset,
        "provider": PROVIDER,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "symbols": symbols,
        "call_budget_per_dispatch": 1,
        "collection_order": (
            "recent_to_oldest"
            if dataset == "valuation_daily"
            else "symbol_ascending"
        ),
        "availability_contract": (
            "trade_date"
            if dataset == "valuation_daily"
            else "announcement_date"
        ),
    }


def _plan_progress(
    plan: dict[str, Any],
    *,
    repo: QuantFactorRepository,
) -> dict[str, Any]:
    policy = plan.get("policy") or {}
    dataset = str(plan.get("dataset") or policy.get("dataset") or "")
    start = _parse_date(plan["start_date"], "开始日期")
    end = _parse_date(plan["end_date"], "结束日期")
    if dataset == "valuation_daily":
        targets = [
            item.isoformat() for item in _weekdays(start, end)
        ]
        resolved = repo.resolved_daily_dates(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
    else:
        targets = list(plan.get("symbols") or [])
        resolved = repo.resolved_financial_symbols(
            targets,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
        )
    pending = [target for target in targets if target not in resolved]
    if dataset == "valuation_daily":
        pending.sort(reverse=True)
    else:
        pending.sort()
    total = len(targets)
    completed = total - len(pending)
    return {
        "target_count": total,
        "completed_target_count": completed,
        "pending_target_count": len(pending),
        "progress_pct": round(
            completed / total * 100, 2
        )
        if total
        else 100.0,
        "next_target": pending[0] if pending else None,
        "resolved_empty_trading_days_included": True,
    }


def create_backfill_plan(
    payload: dict[str, Any] | None,
    *,
    actor_id: str,
    repo: QuantFactorRepository = repository,
    auto_dispatch: bool = True,
) -> dict[str, Any]:
    policy = normalize_backfill_policy(payload)
    plan = repo.create_plan(policy, actor_id=actor_id)
    result = {
        "item": {
            **plan,
            "progress": _plan_progress(plan, repo=repo),
        },
        "dispatch": None,
    }
    if auto_dispatch:
        try:
            result["dispatch"] = schedule_due_sync(
                actor_id=f"{actor_id}:plan-start",
                repo=repo,
            )
        except Exception as error:
            # The plan is already durable. Returning it prevents an API/Redis
            # outage from encouraging the administrator to create duplicates;
            # the scheduler can redispatch the queued run after recovery.
            result["dispatch"] = {
                "status": "initial_dispatch_failed",
                "detail": (
                    "回填计划已保存；首次调度失败，将由后续调度器继续"
                ),
                "error_type": type(error).__name__,
            }
    return result


def transition_backfill_plan(
    plan_id: str,
    action: str,
    *,
    repo: QuantFactorRepository = repository,
) -> dict[str, Any]:
    status_by_action = {
        "pause": "paused",
        "resume": "active",
        "cancel": "cancelled",
    }
    status = status_by_action.get(str(action))
    if status is None:
        raise ValueError("计划操作只能是 pause、resume 或 cancel")
    item = repo.transition_plan(plan_id, status)
    return {**item, "progress": _plan_progress(item, repo=repo)}


def _normalize_sync_request(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    source = dict(payload or {})
    dataset = str(source.get("dataset") or "valuation_daily")
    if dataset not in {"valuation_daily", "financial_indicator"}:
        raise ValueError("因子数据集无效")
    mode = str(source.get("mode") or "live_incremental")
    if mode not in {"live_incremental", "historical_backfill"}:
        raise ValueError("同步模式无效")
    request: dict[str, Any] = {
        "schema_version": "quant_factor_sync_request.v1",
        "warehouse_version": WAREHOUSE_VERSION,
        "dataset": dataset,
        "provider": PROVIDER,
        "mode": mode,
        "plan_id": source.get("plan_id"),
        "target_date": None,
        "target_symbol": None,
        "period_start": None,
        "period_end": None,
    }
    today = _china_now().date()
    if dataset == "valuation_daily":
        target = _parse_date(
            source.get("target_date")
            or _latest_candidate_weekdays(limit=1)[0].isoformat(),
            "目标交易日",
        )
        if target > today:
            raise ValueError("目标交易日不能晚于今天")
        request["target_date"] = target.isoformat()
    else:
        request["target_symbol"] = _normalize_symbol(
            source.get("target_symbol")
        )
        period_end = _parse_date(
            source.get("period_end") or today.isoformat(),
            "报告期结束",
        )
        period_start = _parse_date(
            source.get("period_start")
            or (period_end - dt.timedelta(days=4 * 366)).isoformat(),
            "报告期开始",
        )
        if period_start > period_end or period_end > today:
            raise ValueError("财务指标报告期范围无效")
        request["period_start"] = period_start.isoformat()
        request["period_end"] = period_end.isoformat()
    return request


def _dispatch_sync_run(
    run: dict[str, Any],
    *,
    repo: QuantFactorRepository,
) -> dict[str, Any]:
    from task_queue import (
        enqueue_quant_factor_sync,
        uses_celery_queue,
    )

    if uses_celery_queue():
        task_id = enqueue_quant_factor_sync(str(run["id"]))
        return {
            "dispatched": True,
            "mode": "celery",
            "task_id": task_id,
        }
    execute_sync_run(
        str(run["id"]),
        actor_id="embedded-quant-factor-worker",
        repo=repo,
    )
    return {
        "dispatched": True,
        "mode": "embedded",
        "task_id": None,
    }


def request_sync(
    payload: dict[str, Any] | None,
    *,
    actor_id: str,
    repo: QuantFactorRepository = repository,
) -> dict[str, Any]:
    request = _normalize_sync_request(payload)
    run, created = repo.create_sync_run(request, actor_id=actor_id)
    if run["status"] == "failed" and int(run["attempt_count"] or 0) < 3:
        run = repo.requeue_failed_sync(
            str(run["id"]),
            actor_id=actor_id,
            max_attempts=3,
        )
    dispatch = None
    if run["status"] == "queued":
        dispatch = _dispatch_sync_run(run, repo=repo)
        run = repo.get_sync_run(str(run["id"])) or run
    return {
        "item": run,
        "created": created,
        "dispatch": dispatch,
    }


def _clean_daily_frame(
    frame: pd.DataFrame | None,
    *,
    target_date: dt.date,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if frame is None or frame.empty:
        return [], {"input_rows": 0, "malformed_rows": 0}
    required = {"ts_code", "trade_date"}
    if not required.issubset(frame.columns):
        raise ValueError("Tushare daily_basic 返回字段不完整")
    output = []
    malformed = 0
    fields = [
        "ts_code",
        "trade_date",
        "close",
        "turnover_rate_f",
        "pe_ttm",
        "pb",
        "dv_ttm",
        "total_mv",
        "circ_mv",
    ]
    for _, source in frame.iterrows():
        trade_date = _frame_date(source.get("trade_date"))
        ts_code = str(source.get("ts_code") or "").strip().upper()
        symbol = ts_code.split(".")[0]
        if (
            trade_date != target_date
            or not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", ts_code)
            or not re.fullmatch(r"\d{6}", symbol)
        ):
            malformed += 1
            continue
        payload = {
            key: _json_scalar(source.get(key))
            for key in fields
        }
        output.append(
            {
                "symbol": symbol,
                "ts_code": ts_code,
                "trade_date": trade_date.isoformat(),
                "pe_ttm": _finite(source.get("pe_ttm")),
                "pb": _finite(source.get("pb")),
                "dividend_yield_ttm": _finite(source.get("dv_ttm")),
                "total_market_value": _finite(source.get("total_mv")),
                "circulating_market_value": _finite(
                    source.get("circ_mv")
                ),
                "free_turnover_rate": _finite(
                    source.get("turnover_rate_f")
                ),
                "payload": payload,
            }
        )
    return output, {
        "input_rows": int(len(frame)),
        "malformed_rows": malformed,
    }


def _clean_financial_frame(
    frame: pd.DataFrame | None,
    *,
    target_symbol: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if frame is None or frame.empty:
        return [], {"input_rows": 0, "malformed_rows": 0}
    required = {"ts_code", "ann_date", "end_date"}
    if not required.issubset(frame.columns):
        raise ValueError("Tushare fina_indicator 返回字段不完整")
    output = []
    malformed = 0
    fields = [
        "ts_code",
        "ann_date",
        "end_date",
        "roe",
        "grossprofit_margin",
        "ocf_to_or",
        "debt_to_assets",
        "update_flag",
    ]
    for _, source in frame.iterrows():
        announcement = _frame_date(source.get("ann_date"))
        report_end = _frame_date(source.get("end_date"))
        ts_code = str(source.get("ts_code") or "").strip().upper()
        symbol = ts_code.split(".")[0]
        if (
            announcement is None
            or report_end is None
            or report_end > announcement
            or symbol != target_symbol
            or not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", ts_code)
        ):
            malformed += 1
            continue
        payload = {
            key: _json_scalar(source.get(key))
            for key in fields
        }
        output.append(
            {
                "symbol": symbol,
                "ts_code": ts_code,
                "announcement_date": announcement.isoformat(),
                "report_end_date": report_end.isoformat(),
                "roe": _finite(source.get("roe")),
                "gross_profit_margin": _finite(
                    source.get("grossprofit_margin")
                ),
                "operating_cashflow_to_revenue": _finite(
                    source.get("ocf_to_or")
                ),
                "debt_to_assets": _finite(
                    source.get("debt_to_assets")
                ),
                "update_flag": _json_scalar(
                    source.get("update_flag")
                ),
                "payload": payload,
            }
        )
    return output, {
        "input_rows": int(len(frame)),
        "malformed_rows": malformed,
    }


def _complete_plan_if_ready(
    plan_id: str | None,
    *,
    repo: QuantFactorRepository,
) -> None:
    if not plan_id:
        return
    plan = repo.get_plan(str(plan_id))
    if not plan or plan["status"] != "active":
        return
    progress = _plan_progress(plan, repo=repo)
    if progress["pending_target_count"] == 0:
        repo.transition_plan(str(plan_id), "completed")


def execute_sync_run(
    run_id: str,
    *,
    actor_id: str = "quant-factor-worker",
    repo: QuantFactorRepository = repository,
    pro: Any | None = None,
) -> dict[str, Any]:
    claimed = repo.claim_sync_run(run_id, actor_id=actor_id)
    if claimed is None:
        raise ValueError("量化因子同步任务不存在")
    if claimed["status"] != "running":
        return claimed
    request = dict(claimed.get("request") or {})
    retrieved_at = dt.datetime.now(
        dt.timezone.utc
    ).isoformat(timespec="milliseconds")
    try:
        client = pro or _tushare_pro()
        if claimed["dataset"] == "valuation_daily":
            target = _parse_date(claimed["target_date"], "目标交易日")
            frame = client.daily_basic(
                ts_code="",
                trade_date=target.strftime("%Y%m%d"),
                fields=DAILY_FIELDS,
            )
            rows, clean_stats = _clean_daily_frame(
                frame,
                target_date=target,
            )
            write_stats = repo.save_daily_observations(
                run_id,
                rows,
                expected_attempt=int(claimed["attempt_count"]),
                provider=claimed["provider"],
                capture_mode=claimed["mode"],
                retrieved_at=retrieved_at,
            )
        else:
            symbol = _normalize_symbol(claimed["target_symbol"])
            period_start = _parse_date(
                claimed["period_start"],
                "报告期开始",
            )
            period_end = _parse_date(
                claimed["period_end"],
                "报告期结束",
            )
            frame = client.fina_indicator(
                ts_code=_tushare_code(symbol),
                start_date=period_start.strftime("%Y%m%d"),
                end_date=period_end.strftime("%Y%m%d"),
                fields=FINANCIAL_FIELDS,
            )
            rows, clean_stats = _clean_financial_frame(
                frame,
                target_symbol=symbol,
            )
            write_stats = repo.save_financial_observations(
                run_id,
                rows,
                expected_attempt=int(claimed["attempt_count"]),
                provider=claimed["provider"],
                capture_mode=claimed["mode"],
                retrieved_at=retrieved_at,
            )
        stats = {
            "schema_version": "quant_factor_sync_result.v1",
            "warehouse_version": WAREHOUSE_VERSION,
            "dataset": claimed["dataset"],
            "provider": claimed["provider"],
            "mode": claimed["mode"],
            "target_date": claimed.get("target_date"),
            "target_symbol": claimed.get("target_symbol"),
            "period_start": claimed.get("period_start"),
            "period_end": claimed.get("period_end"),
            "retrieved_at": retrieved_at,
            **clean_stats,
            **write_stats,
            "no_data": len(rows) == 0,
            "availability_contract": (
                "trade_date"
                if claimed["dataset"] == "valuation_daily"
                else "announcement_date"
            ),
        }
        completed = repo.complete_sync_run(
            run_id,
            actor_id=actor_id,
            stats=stats,
            partial=bool(
                stats["malformed_rows"] or stats["conflict_keys"]
            ),
        )
        _complete_plan_if_ready(
            request.get("plan_id"),
            repo=repo,
        )
        return completed
    except QuantFactorConflictError:
        # A scheduler may have recovered an expired lease while a stale
        # provider call was still returning. The old worker must not rewrite
        # the recovered run or append a second failure event.
        raise
    except Exception as error:
        safe_message = sanitize_worker_error(error)
        try:
            repo.fail_sync_run(
                run_id,
                actor_id=actor_id,
                error_code="QUANT_FACTOR_PROVIDER_FAILED",
                error_message=safe_message,
            )
        except QuantFactorConflictError:
            pass
        raise RuntimeError(safe_message) from None


def _failed_retry_due(run: dict[str, Any]) -> bool:
    if run.get("status") != "failed":
        return False
    if int(run.get("attempt_count") or 0) >= 3:
        return False
    value = run.get("completed_at")
    try:
        completed = dt.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return True
    cooldown = max(
        60,
        int(os.getenv("QUANT_FACTOR_RETRY_COOLDOWN_SECONDS", "3900")),
    )
    return (
        dt.datetime.now(dt.timezone.utc) - completed
    ).total_seconds() >= cooldown


def _queued_redispatch_due(
    run: dict[str, Any],
    *,
    now: dt.datetime | None = None,
) -> bool:
    try:
        created = dt.datetime.fromisoformat(
            str(run.get("created_at")).replace("Z", "+00:00")
        )
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return True
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    threshold = max(
        60,
        int(
            os.getenv(
                "QUANT_FACTOR_QUEUE_REDISPATCH_SECONDS",
                "600",
            )
        ),
    )
    return (
        current.astimezone(dt.timezone.utc)
        - created.astimezone(dt.timezone.utc)
    ).total_seconds() >= threshold


def _schedule_request(
    request: dict[str, Any],
    *,
    actor_id: str,
    repo: QuantFactorRepository,
) -> dict[str, Any]:
    run, created = repo.create_sync_run(request, actor_id=actor_id)
    if run["status"] == "failed":
        if int(run.get("attempt_count") or 0) >= 3:
            return {
                "status": "retry_exhausted",
                "run_id": run["id"],
                "created": False,
            }
        if not _failed_retry_due(run):
            return {
                "status": "retry_cooldown",
                "run_id": run["id"],
                "created": False,
            }
        run = repo.requeue_failed_sync(
            str(run["id"]),
            actor_id=actor_id,
            max_attempts=3,
        )
    if run["status"] == "queued":
        dispatch = _dispatch_sync_run(run, repo=repo)
        return {
            "status": "dispatched",
            "run_id": run["id"],
            "created": created,
            "dispatch": dispatch,
        }
    return {
        "status": str(run["status"]),
        "run_id": run["id"],
        "created": created,
    }


def schedule_due_sync(
    *,
    actor_id: str = "system:quant-factor-scheduler",
    repo: QuantFactorRepository = repository,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    repo.recover_stale_syncs(
        actor_id=f"{actor_id}:lease-recovery",
    )
    if not str(config.TUSHARE_TOKEN or "").strip():
        return {
            "status": "not_configured",
            "detail": "TUSHARE_TOKEN 未配置，调度器不会请求供应商",
        }
    active = repo.oldest_active_sync()
    if active is not None:
        if (
            active["status"] == "queued"
            and _queued_redispatch_due(active, now=now)
        ):
            return {
                "status": "redispatched_queued",
                "run_id": active["id"],
                "created": False,
                "dispatch": _dispatch_sync_run(active, repo=repo),
                "detail": (
                    "发现未被 Worker 领取的持久任务，已重新派发同一 run_id"
                ),
            }
        return {
            "status": "active_run_exists",
            "detail": "已有量化因子同步正在排队或运行",
            "run_id": active["id"],
        }

    for target in _latest_candidate_weekdays(now=now, limit=10):
        target_text = target.isoformat()
        if repo.target_resolved(
            dataset="valuation_daily",
            target_date=target_text,
        ):
            continue
        scheduled = _schedule_request(
            _normalize_sync_request(
                {
                    "dataset": "valuation_daily",
                    "mode": "live_incremental",
                    "target_date": target_text,
                }
            ),
            actor_id=actor_id,
            repo=repo,
        )
        if scheduled.get("status") == "retry_exhausted":
            continue
        return scheduled

    for plan in repo.list_active_plans(limit=20):
        progress = _plan_progress(plan, repo=repo)
        target = progress["next_target"]
        if target is None:
            repo.transition_plan(str(plan["id"]), "completed")
            continue
        if plan["dataset"] == "valuation_daily":
            request = _normalize_sync_request(
                {
                    "dataset": "valuation_daily",
                    "mode": "historical_backfill",
                    "target_date": target,
                    "plan_id": plan["id"],
                }
            )
        else:
            request = _normalize_sync_request(
                {
                    "dataset": "financial_indicator",
                    "mode": "historical_backfill",
                    "target_symbol": target,
                    "period_start": plan["start_date"],
                    "period_end": plan["end_date"],
                    "plan_id": plan["id"],
                }
            )
        return _schedule_request(
            request,
            actor_id=actor_id,
            repo=repo,
        )
    return {
        "status": "idle",
        "detail": "最近交易日已覆盖，且没有待执行的回填计划",
    }


def _freshness_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = dt.date.fromisoformat(str(value))
    except ValueError:
        return None
    return max(0, (_china_now().date() - parsed).days)


def overview(
    *,
    can_manage: bool,
    repo: QuantFactorRepository = repository,
    plan_limit: int = 30,
    run_limit: int = 30,
) -> dict[str, Any]:
    stats = repo.dataset_stats()
    for item in stats.values():
        item["row_count"] = int(item.get("row_count") or 0)
        item["symbol_count"] = int(item.get("symbol_count") or 0)
        item["date_count"] = int(item.get("date_count") or 0)
        item["freshness_days"] = _freshness_days(
            item.get("last_date")
        )
    plans = []
    for plan in repo.list_plans(limit=plan_limit):
        plan = dict(plan)
        plan.pop("actor_id", None)
        plans.append(
            {**plan, "progress": _plan_progress(plan, repo=repo)}
        )
    runs = []
    for run in repo.list_sync_runs(limit=run_limit):
        run = dict(run)
        run.pop("actor_id", None)
        run.pop("request_key", None)
        runs.append(run)
    coverage = repo.symbol_coverage(list(DEFAULT_RESEARCH_SYMBOLS))
    active_runs = [
        item for item in runs if item["status"] in {"queued", "running"}
    ]
    valuation = stats["valuation_daily"]
    return {
        "schema_version": "quant_factor_warehouse_overview.v1",
        "warehouse_version": WAREHOUSE_VERSION,
        "status": (
            "empty"
            if valuation["row_count"] == 0
            else (
                "degraded"
                if valuation["conflict_key_count"] > 0
                else "ready"
            )
        ),
        "provider": {
            "id": PROVIDER,
            "label": "Tushare Pro",
            "configured": bool(str(config.TUSHARE_TOKEN or "").strip()),
            "credential_exposed": False,
        },
        "datasets": stats,
        "research_pool_coverage": coverage,
        "plans": plans,
        "runs": runs,
        "summary": {
            "active_plan_count": sum(
                item["status"] == "active" for item in plans
            ),
            "active_run_count": len(active_runs),
            "failed_run_count": sum(
                item["status"] == "failed" for item in runs
            ),
            "default_research_symbol_count": len(
                DEFAULT_RESEARCH_SYMBOLS
            ),
        },
        "scheduler": {
            "enabled": bool(str(config.TUSHARE_TOKEN or "").strip()),
            "interval_seconds": max(
                3900,
                int(
                    os.getenv(
                        "QUANT_FACTOR_SYNC_INTERVAL_SECONDS",
                        "3900",
                    )
                ),
            ),
            "call_budget_per_dispatch": 1,
            "priority": [
                "最近 A 股交易日全市场 daily_basic",
                "活动回填计划的下一个未覆盖目标",
            ],
        },
        "controls": {
            "can_manage": bool(can_manage),
            "delete_supported": False,
            "plan_actions": ["pause", "resume", "cancel"],
        },
        "data_contract": {
            "daily_availability": "trade_date <= signal_date",
            "financial_availability": (
                "announcement_date <= signal_date；报告期不能代替公告日"
            ),
            "conflicting_revisions": (
                "同一供应商、股票和可见日期存在不同内容哈希时整组排除"
            ),
            "raw_observations_immutable": True,
            "sync_events_hash_chained": True,
            "research_reads_provider": False,
        },
    }


def load_point_in_time_fundamentals(
    symbols: list[str],
    *,
    history_months: int,
    required_factors: set[str],
    end_date: dt.date | None = None,
    repo: QuantFactorRepository = repository,
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, Any]]:
    required = set(required_factors) & {
        "fundamental_quality",
        "value",
    }
    if not required:
        raise ValueError("至少需要一个财务或估值因子")
    end = end_date or _china_now().date()
    start = end - dt.timedelta(days=int(history_months) * 31)
    financial_start = start - dt.timedelta(days=550)
    unique_symbols = sorted(
        {_normalize_symbol(symbol) for symbol in symbols}
    )
    daily_rows = (
        repo.load_daily_rows(
            unique_symbols,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        if "value" in required
        else []
    )
    financial_rows = (
        repo.load_financial_rows(
            unique_symbols,
            start_date=financial_start.isoformat(),
            end_date=end.isoformat(),
        )
        if "fundamental_quality" in required
        else []
    )
    daily_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in daily_rows:
        daily_by_symbol.setdefault(str(row["symbol"]), []).append(row)
    financial_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in financial_rows:
        financial_by_symbol.setdefault(str(row["symbol"]), []).append(
            row
        )

    inputs: dict[str, dict[str, pd.DataFrame]] = {}
    failures = []
    assets = []
    used_ids: list[str] = []
    total_conflicts = 0
    total_rows = 0
    live_rows = 0
    providers = set()
    capture_modes = set()
    for symbol in unique_symbols:
        valuation_records = daily_by_symbol.get(symbol, [])
        financial_records = financial_by_symbol.get(symbol, [])
        valuation_conflicts = 0
        financial_conflicts = 0
        valuations = []
        for trade_date in sorted(
            {str(row["trade_date"]) for row in valuation_records}
        ):
            group = [
                row
                for row in valuation_records
                if str(row["trade_date"]) == trade_date
            ]
            hashes = {str(row["payload_sha256"]) for row in group}
            if len(hashes) > 1:
                valuation_conflicts += 1
                continue
            selected = sorted(
                group,
                key=lambda row: (
                    str(row["retrieved_at"]),
                    str(row["id"]),
                ),
            )[0]
            valuations.append(
                {
                    "trade_date": trade_date,
                    "pe_ttm": selected.get("pe_ttm"),
                    "pb": selected.get("pb"),
                }
            )
            used_ids.append(str(selected["id"]))
        financials = []
        financial_keys = sorted(
            {
                (
                    str(row["announcement_date"]),
                    str(row["report_end_date"]),
                )
                for row in financial_records
            }
        )
        for announcement_date, report_end_date in financial_keys:
            group = [
                row
                for row in financial_records
                if str(row["announcement_date"]) == announcement_date
                and str(row["report_end_date"]) == report_end_date
            ]
            hashes = {str(row["payload_sha256"]) for row in group}
            if len(hashes) > 1:
                financial_conflicts += 1
                continue
            selected = sorted(
                group,
                key=lambda row: (
                    str(row["retrieved_at"]),
                    str(row["id"]),
                ),
            )[0]
            financials.append(
                {
                    "ann_date": announcement_date,
                    "end_date": report_end_date,
                    "roe": selected.get("roe"),
                    "grossprofit_margin": selected.get(
                        "gross_profit_margin"
                    ),
                    "ocf_to_or": selected.get(
                        "operating_cashflow_to_revenue"
                    ),
                    "debt_to_assets": selected.get("debt_to_assets"),
                }
            )
            used_ids.append(str(selected["id"]))

        combined_records = valuation_records + financial_records
        total_rows += len(combined_records)
        live_rows += sum(
            row.get("capture_mode") == "live_incremental"
            for row in combined_records
        )
        providers.update(
            str(row.get("provider") or "")
            for row in combined_records
            if row.get("provider")
        )
        capture_modes.update(
            str(row.get("capture_mode") or "")
            for row in combined_records
            if row.get("capture_mode")
        )
        total_conflicts += valuation_conflicts + financial_conflicts
        missing = []
        if "value" in required and not valuations:
            missing.append("value")
        if "fundamental_quality" in required and not financials:
            missing.append("fundamental_quality")
        if missing:
            failures.append(
                {
                    "symbol": symbol,
                    "error": (
                        "仓库缺少必需历史:"
                        + ", ".join(sorted(missing))
                    ),
                }
            )
        else:
            inputs[symbol] = {
                "valuations": pd.DataFrame(valuations),
                "financials": pd.DataFrame(financials),
            }
        assets.append(
            {
                "symbol": symbol,
                "valuation_row_count": len(valuations),
                "financial_row_count": len(financials),
                "valuation_conflict_count": valuation_conflicts,
                "financial_conflict_count": financial_conflicts,
                "valuation_first_date": (
                    valuations[0]["trade_date"] if valuations else None
                ),
                "valuation_last_date": (
                    valuations[-1]["trade_date"] if valuations else None
                ),
                "financial_first_announcement": (
                    financials[0]["ann_date"] if financials else None
                ),
                "financial_last_announcement": (
                    financials[-1]["ann_date"] if financials else None
                ),
            }
        )
    snapshot_sha256 = sha256_payload(sorted(set(used_ids)))
    verified = bool(inputs) and total_conflicts == 0
    evidence = {
        "schema_version": "quant_factor_warehouse_evidence.v1",
        "warehouse_version": WAREHOUSE_VERSION,
        "source": (
            "A股时点因子仓库"
            + (
                "（" + " + ".join(sorted(providers)) + "）"
                if providers
                else ""
            )
        ),
        "required_factors": sorted(required),
        "availability_rules": {
            "financials": "ann_date <= signal_date",
            "valuation": "trade_date <= signal_date",
            "report_period_is_not_availability_date": True,
            "conflicting_content_hash_groups_are_excluded": True,
        },
        "point_in_time_verified": verified,
        "verification_detail": (
            "研究只读取本地不可变仓库；财务按公告日、估值按交易日截断"
            if verified
            else (
                f"仓库存在 {total_conflicts} 个内容冲突组或必需历史覆盖不足，"
                "结果只能保留研究资格"
            )
        ),
        "research_provider_call_count": 0,
        "requested_symbol_count": len(unique_symbols),
        "loaded_symbol_count": len(inputs),
        "failed_symbols": failures,
        "malformed_row_count": 0,
        "ambiguous_revision_count": total_conflicts,
        "warehouse_row_count": total_rows,
        "live_capture_row_count": live_rows,
        "live_capture_coverage_pct": (
            round(live_rows / total_rows * 100, 2)
            if total_rows
            else 0.0
        ),
        "capture_modes": sorted(capture_modes),
        "snapshot_sha256": snapshot_sha256,
        "assets": assets,
        "history_start": start.isoformat(),
        "history_end": end.isoformat(),
    }
    return inputs, evidence
