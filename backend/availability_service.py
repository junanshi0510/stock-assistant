# -*- coding: utf-8 -*-
"""User-visible availability control plane, SLOs and safe degradation modes."""

from __future__ import annotations

import datetime as dt
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import health
from availability_repository import AvailabilityRepository, canonical_json, sha256_text
from observability import (
    AVAILABILITY_COMPONENT_STATE,
    AVAILABILITY_INCIDENT_EVENTS,
    AVAILABILITY_PROBES,
    sanitize_log_value,
)
from task_queue import (
    QUEUE_AGENT,
    QUEUE_LLM,
    QUEUE_MARKET,
    QUEUE_OCR,
    QUEUE_SCHEDULER,
    uses_celery_queue,
)


PROBE_INTERVAL_SECONDS = max(60, int(os.getenv("AVAILABILITY_PROBE_INTERVAL_SECONDS", "300")))
STALE_AFTER_SECONDS = max(
    PROBE_INTERVAL_SECONDS * 2,
    int(os.getenv("AVAILABILITY_STALE_SECONDS", "900")),
)
FAILURE_THRESHOLD = max(1, int(os.getenv("AVAILABILITY_FAILURE_THRESHOLD", "2")))
RECOVERY_THRESHOLD = max(1, int(os.getenv("AVAILABILITY_RECOVERY_THRESHOLD", "2")))
QUEUE_WARN_DEPTH = max(1, int(os.getenv("AVAILABILITY_QUEUE_WARN_DEPTH", "100")))
QUEUE_OUTAGE_DEPTH = max(
    QUEUE_WARN_DEPTH + 1,
    int(os.getenv("AVAILABILITY_QUEUE_OUTAGE_DEPTH", "1000")),
)
_MARKETS = ("A股", "港股", "美股")
_QUEUES = (QUEUE_AGENT, QUEUE_MARKET, QUEUE_LLM, QUEUE_OCR, QUEUE_SCHEDULER)
_STATE_SCORE = {"unknown": 0, "outage": 1, "degraded": 2, "operational": 3}
_SENSITIVE_DETAIL_KEY = re.compile(
    r"(?i)(authorization|credential|password|secret|token|api[_-]?key|access[_-]?key)"
)

repository = AvailabilityRepository()


def _utc_now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _parse_time(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc_now(parsed)


def _safe_details(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "truncated"
    if isinstance(value, dict):
        return {
            str(key)[:80]: (
                "***"
                if _SENSITIVE_DETAIL_KEY.search(str(key))
                else _safe_details(item, depth=depth + 1)
            )
            for key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_details(item, depth=depth + 1) for item in list(value)[:30]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_log_value(value, limit=300)


def _component(
    component_id: str,
    label: str,
    category: str,
    state: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "label": label,
        "category": category,
        "observed_state": state if state in _STATE_SCORE else "unknown",
        "message": sanitize_log_value(message, limit=300),
        "details": _safe_details(details or {}),
    }


def _provider_status_from_worker() -> dict[str, Any]:
    if not uses_celery_queue():
        import hot_stocks

        return hot_stocks.get_provider_status()
    from market_data_gateway import execute_market_operation

    return execute_market_operation(
        "market.providers",
        {},
        timeout_seconds=30,
        tenant_id="platform",
        user_id="availability-monitor",
        max_attempts=1,
    )


def _deep_market_probe(market: str) -> dict[str, Any]:
    if not uses_celery_queue():
        import hot_stocks

        return hot_stocks.probe_provider(market)
    from market_data_gateway import execute_market_operation

    return execute_market_operation(
        "market.providers_probe",
        {"market": market},
        timeout_seconds=120,
        tenant_id="platform",
        user_id="availability-monitor",
        max_attempts=1,
    )


def _deep_provider_probes() -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="availability-provider") as pool:
        futures = {pool.submit(_deep_market_probe, market): market for market in _MARKETS}
        for future in as_completed(futures):
            market = futures[future]
            try:
                value = future.result()
                results[market] = {
                    "market": market,
                    "available": bool(value.get("available")),
                    "provider": value.get("provider"),
                    "source": value.get("source"),
                    "as_of": value.get("as_of"),
                    "latency_ms": value.get("latency_ms"),
                    "attempt_states": [
                        str(item.get("status") or "unknown")
                        for item in (value.get("attempts") or [])[:6]
                    ],
                }
            except Exception as error:
                results[market] = {
                    "market": market,
                    "available": False,
                    "error_type": type(error).__name__,
                }
    return results


def collect_components(
    *,
    deep: bool = False,
    health_result: dict[str, Any] | None = None,
    provider_result: dict[str, Any] | None = None,
    deep_results: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    runtime = health_result or health.readiness(use_cache=False)
    components: list[dict[str, Any]] = []

    database = runtime.get("database") or {}
    components.append(_component(
        "database",
        "权威数据库",
        "core",
        "operational" if database.get("ready") else "outage",
        "PostgreSQL 与生产 Schema 可用" if database.get("ready") else "权威数据库或生产 Schema 不可用",
        {
            "dialect": database.get("dialect"),
            "platform_schema": database.get("platform_schema"),
            "opportunity_schema": database.get("opportunity_schema"),
            "portfolio_twin_schema": database.get("portfolio_twin_schema"),
            "portfolio_valuation_schema": database.get("portfolio_valuation_schema"),
            "availability_schema": database.get("availability_schema"),
            "error_type": database.get("error"),
        },
    ))

    redis = runtime.get("redis") or {}
    components.append(_component(
        "redis",
        "任务消息总线",
        "runtime",
        "operational" if redis.get("ready") else "outage",
        "Redis 可派发持久任务" if redis.get("ready") else "Redis 不可用，新的后台任务会失败关闭",
        {"mode": redis.get("mode"), "error_type": redis.get("error")},
    ))

    workers = runtime.get("workers") or {}
    available_queues = {
        queue
        for values in (workers.get("workers") or {}).values()
        for queue in values or []
    }
    if workers.get("mode") == "embedded":
        available_queues = set(_QUEUES)
    queue_labels = {
        QUEUE_AGENT: "Agent 编排 Worker",
        QUEUE_MARKET: "行情 Worker",
        QUEUE_LLM: "模型合成 Worker",
        QUEUE_OCR: "私有 OCR Worker",
        QUEUE_SCHEDULER: "持久调度 Worker",
    }
    for queue in _QUEUES:
        ready = queue in available_queues
        components.append(_component(
            f"worker:{queue}",
            queue_labels[queue],
            "worker",
            "operational" if ready else "outage",
            "消费队列正常" if ready else f"缺少 {queue} 队列消费者",
            {"queue": queue, "mode": workers.get("mode")},
        ))

    depths = redis.get("queue_depths") or {}
    for queue in _QUEUES:
        if queue not in depths and redis.get("mode") != "embedded":
            state, depth = "unknown", None
        else:
            depth = int(depths.get(queue) or 0)
            state = (
                "outage" if depth >= QUEUE_OUTAGE_DEPTH
                else "degraded" if depth >= QUEUE_WARN_DEPTH
                else "operational"
            )
        components.append(_component(
            f"queue:{queue}",
            f"{queue} 队列积压",
            "queue",
            state,
            "积压处于阈值内" if state == "operational" else "任务积压超过运行阈值",
            {
                "depth": depth,
                "warning_threshold": QUEUE_WARN_DEPTH,
                "outage_threshold": QUEUE_OUTAGE_DEPTH,
            },
        ))

    objects = runtime.get("object_storage") or {}
    required = bool(objects.get("required"))
    object_ready = bool(objects.get("ready"))
    object_state = "operational" if object_ready else ("outage" if required else "degraded")
    components.append(_component(
        "object_storage",
        "私有对象存储",
        "storage",
        object_state,
        "私有 OSS 可读写" if object_ready else "私有对象链路不可用",
        {
            "provider": objects.get("provider"),
            "region": objects.get("region"),
            "encryption": objects.get("encryption"),
            "required": required,
            "error_type": objects.get("error"),
        },
    ))

    provider_error = None
    providers = provider_result
    if providers is None:
        try:
            providers = _provider_status_from_worker()
        except Exception as error:
            provider_error = type(error).__name__
            providers = {"markets": []}
    deep_values = deep_results
    if deep and deep_values is None:
        deep_values = _deep_provider_probes()
    market_rows = {str(item.get("market")): item for item in providers.get("markets") or []}
    provider_state_map = {
        "ready": "operational",
        "configured_unverified": "unknown",
        "circuit_open": "degraded",
        "configuration_invalid": "degraded",
        "configuration_required": "degraded",
    }
    for market in _MARKETS:
        row = market_rows.get(market) or {}
        deep_row = (deep_values or {}).get(market)
        if deep_row is not None:
            state = "operational" if deep_row.get("available") else "degraded"
            message = "专业行情主动探测成功" if deep_row.get("available") else "专业行情主动探测失败，可继续读取最近可信结果"
        elif provider_error:
            state = "unknown"
            message = "行情 Worker 状态读取失败"
        else:
            state = provider_state_map.get(str(row.get("state") or ""), "unknown")
            message = {
                "operational": "至少一条专业行情路线最近成功",
                "unknown": "专业行情已配置，等待真实调用验证",
                "degraded": "专业行情路线受限，页面只能明确降级或拒绝刷新",
            }.get(state, "行情路线状态未知")
        components.append(_component(
            f"market:{market}",
            f"{market}专业行情",
            "market",
            state,
            message,
            {
                "provider": (deep_row or {}).get("provider") or row.get("provider"),
                "provider_label": row.get("provider_label"),
                "configured": row.get("configured"),
                "available_provider_count": row.get("available_provider_count"),
                "provider_count": row.get("provider_count"),
                "expected_freshness": row.get("expected_freshness"),
                "deep_probe": bool(deep_row),
                "deep_available": deep_row.get("available") if deep_row else None,
            },
        ))

    metadata = {
        "traffic_ready": bool(runtime.get("ready")),
        "full_service_ready": bool(runtime.get("full_service_ready", runtime.get("ready"))),
        "provider_policy_version": providers.get("policy_version"),
        "provider_status_error_type": provider_error,
        "deep_probe": bool(deep),
        "deep_results": _safe_details(deep_values or {}),
        "thresholds": {
            "failure_confirmations": FAILURE_THRESHOLD,
            "recovery_confirmations": RECOVERY_THRESHOLD,
            "queue_warning_depth": QUEUE_WARN_DEPTH,
            "queue_outage_depth": QUEUE_OUTAGE_DEPTH,
        },
    }
    return components, metadata


def _scheduled_probe_id(current: dt.datetime) -> str:
    bucket = int(current.timestamp()) // PROBE_INTERVAL_SECONDS
    identity = canonical_json({"kind": "scheduled", "bucket": bucket, "interval": PROBE_INTERVAL_SECONDS})
    return f"availability_probe_{sha256_text(identity)[:32]}"


def run_probe(
    *,
    trigger_type: str,
    actor_id: str,
    deep: bool = False,
    now: dt.datetime | None = None,
    repository_instance: AvailabilityRepository | None = None,
    health_result: dict[str, Any] | None = None,
    provider_result: dict[str, Any] | None = None,
    deep_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    repo = repository_instance or repository
    started = _utc_now(now)
    components, metadata = collect_components(
        deep=deep,
        health_result=health_result,
        provider_result=provider_result,
        deep_results=deep_results,
    )
    completed = _utc_now(now) if now is not None else _utc_now()
    probe_id = _scheduled_probe_id(completed) if trigger_type == "scheduled" else None
    result = repo.record_probe(
        trigger_type="manual_deep" if deep and trigger_type == "manual" else trigger_type,
        actor_id=actor_id,
        observations=components,
        metadata=metadata,
        started_at=started,
        completed_at=completed,
        probe_id=probe_id,
        failure_threshold=FAILURE_THRESHOLD,
        recovery_threshold=RECOVERY_THRESHOLD,
    )
    payload = result.get("payload") or {}
    if not result.get("deduplicated"):
        AVAILABILITY_PROBES.labels(
            trigger=str(payload.get("trigger_type") or trigger_type),
            status=str(payload.get("overall_status") or "unknown"),
        ).inc()
        for component in payload.get("components") or []:
            AVAILABILITY_COMPONENT_STATE.labels(
                component=str(component.get("component_id") or "unknown")
            ).set(_STATE_SCORE.get(str(component.get("observed_state")), 0))
        for transition in payload.get("transitions") or []:
            AVAILABILITY_INCIDENT_EVENTS.labels(
                event=str(transition.get("event_type") or "unknown")
            ).inc()
    return result


def _component_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("component_id")): item
        for item in payload.get("components") or []
    }


def _state(components: dict[str, dict[str, Any]], component_id: str) -> str:
    item = components.get(component_id) or {}
    observed = str(item.get("observed_state") or "unknown")
    return observed if observed in _STATE_SCORE else "unknown"


def build_capabilities(payload: dict[str, Any]) -> dict[str, Any]:
    components = _component_map(payload)
    database_ok = _state(components, "database") == "operational"
    redis_ok = _state(components, "redis") == "operational"
    worker = {
        queue: _state(components, f"worker:{queue}") == "operational"
        for queue in _QUEUES
    }
    queue_state = {
        queue: _state(components, f"queue:{queue}")
        for queue in _QUEUES
    }
    queue_usable = {
        queue: state in {"operational", "degraded"}
        for queue, state in queue_state.items()
    }
    markets = {market: _state(components, f"market:{market}") for market in _MARKETS}
    market_good = [market for market, state in markets.items() if state == "operational"]
    market_refresh_mode = (
        "normal" if len(market_good) == len(_MARKETS) and queue_state[QUEUE_MARKET] == "operational"
        else "partial" if market_good
        else "unavailable"
    )
    background_ok = database_ok and redis_ok
    valuation_available = (
        background_ok
        and worker[QUEUE_MARKET]
        and queue_usable[QUEUE_MARKET]
        and bool(market_good)
    )
    agent_available = (
        background_ok
        and worker[QUEUE_AGENT]
        and worker[QUEUE_MARKET]
        and queue_usable[QUEUE_AGENT]
        and queue_usable[QUEUE_MARKET]
    )
    llm_ok = worker[QUEUE_LLM] and queue_usable[QUEUE_LLM]
    object_ok = _state(components, "object_storage") == "operational"
    decision_mode = (
        "unavailable" if not database_ok
        else "normal" if valuation_available and market_refresh_mode == "normal"
        else "read_only_degraded"
    )
    return {
        "decision_mode": {
            "mode": decision_mode,
            "available": database_ok,
            "fresh_actions_allowed": decision_mode == "normal",
            "message": {
                "normal": "关键事实链路可刷新；具体投资动作仍受数据与策略门禁约束。",
                "read_only_degraded": "已保存事实可继续读取；受影响市场的新研究与金额动作必须等待恢复。",
                "unavailable": "权威数据库不可用，平台不能安全提供投资事实。",
            }[decision_mode],
        },
        "saved_data_read": {
            "available": database_ok,
            "mode": "normal" if database_ok else "unavailable",
        },
        "market_refresh": {
            "available": valuation_available,
            "mode": market_refresh_mode,
            "markets": markets,
        },
        "portfolio_valuation_refresh": {
            "available": valuation_available,
            "mode": market_refresh_mode,
        },
        "agent_research": {
            "available": agent_available,
            "mode": (
                "unavailable" if not agent_available
                else "normal" if llm_ok
                else "deterministic_only"
            ),
        },
        "private_ocr_import": {
            "available": background_ok and worker[QUEUE_OCR] and queue_usable[QUEUE_OCR] and object_ok,
            "mode": "normal" if background_ok and worker[QUEUE_OCR] and queue_usable[QUEUE_OCR] and object_ok else "unavailable",
        },
        "durable_scheduling": {
            "available": background_ok and worker[QUEUE_SCHEDULER] and queue_usable[QUEUE_SCHEDULER],
            "mode": "normal" if background_ok and worker[QUEUE_SCHEDULER] and queue_usable[QUEUE_SCHEDULER] else "unavailable",
        },
    }


_SLO_GROUPS = {
    "core_access": {
        "label": "权威事实读取",
        "target": float(os.getenv("AVAILABILITY_SLO_CORE", "99.9")),
        "components": ("database",),
    },
    "background_processing": {
        "label": "持久后台处理",
        "target": float(os.getenv("AVAILABILITY_SLO_BACKGROUND", "99.0")),
        "components": ("redis", "worker:market-data", "worker:agent", "worker:scheduler"),
    },
    "private_asset_pipeline": {
        "label": "私有文件链路",
        "target": float(os.getenv("AVAILABILITY_SLO_PRIVATE_ASSET", "99.0")),
        "components": ("object_storage", "worker:ocr"),
    },
    "professional_market_routes": {
        "label": "三市场专业行情",
        "target": float(os.getenv("AVAILABILITY_SLO_MARKET", "95.0")),
        "components": tuple(f"market:{market}" for market in _MARKETS),
    },
}


def calculate_slos(runs: list[dict[str, Any]], *, now: dt.datetime | None = None) -> dict[str, Any]:
    current = _utc_now(now)
    windows = {"24h": dt.timedelta(hours=24), "7d": dt.timedelta(days=7), "30d": dt.timedelta(days=30)}
    result: dict[str, Any] = {}
    for group_id, definition in _SLO_GROUPS.items():
        group_windows: dict[str, Any] = {}
        target = max(0.0, min(100.0, float(definition["target"])))
        for window_id, delta in windows.items():
            good = bad = unknown = 0
            for run in runs:
                if str(run.get("trigger_type") or "") != "scheduled":
                    continue
                completed = _parse_time(run.get("completed_at") or run.get("created_at"))
                if completed is None or completed < current - delta:
                    continue
                components = _component_map(run.get("payload") or {})
                states = [_state(components, item) for item in definition["components"]]
                if len(states) != len(definition["components"]) or "unknown" in states:
                    unknown += 1
                elif all(state == "operational" for state in states):
                    good += 1
                else:
                    bad += 1
            known = good + bad
            availability_pct = round(good / known * 100, 4) if known else None
            bad_fraction = bad / known if known else None
            allowed_fraction = max(0.000001, 1 - target / 100)
            burn_rate = round(bad_fraction / allowed_fraction, 3) if bad_fraction is not None else None
            remaining = (
                round(max(0.0, 1 - bad_fraction / allowed_fraction) * 100, 2)
                if bad_fraction is not None else None
            )
            group_windows[window_id] = {
                "sample_count": known,
                "good_count": good,
                "bad_count": bad,
                "unknown_count": unknown,
                "availability_pct": availability_pct,
                "target_pct": target,
                "burn_rate": burn_rate,
                "error_budget_remaining_pct": remaining,
                "enough_samples": known >= 12,
            }
        result[group_id] = {
            "label": definition["label"],
            "target_pct": target,
            "components": list(definition["components"]),
            "windows": group_windows,
        }
    return {
        "method_version": "probe_window_sli.v1",
        "eligible_trigger_types": ["scheduled"],
        "probe_interval_seconds": PROBE_INTERVAL_SECONDS,
        "minimum_samples": 12,
        "objectives_are_internal_not_sla": True,
        "groups": result,
    }


def _monitor_age(latest: dict[str, Any] | None, *, now: dt.datetime | None = None) -> tuple[float | None, bool]:
    if latest is None:
        return None, True
    completed = _parse_time(latest.get("completed_at") or latest.get("created_at"))
    if completed is None:
        return None, True
    age = max(0.0, (_utc_now(now) - completed).total_seconds())
    return round(age, 1), age > STALE_AFTER_SECONDS


def public_summary(
    *,
    repository_instance: AvailabilityRepository | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    repo = repository_instance or repository
    latest = repo.latest_probe()
    age, stale = _monitor_age(latest, now=now)
    if latest is None:
        return {
            "status": "unknown",
            "monitoring_stale": True,
            "observed_at": None,
            "age_seconds": None,
            "capabilities": {},
            "open_incident_count": 0,
            "notice": "可用性监测尚未形成首个快照。",
        }
    payload = latest.get("payload") or {}
    capabilities = build_capabilities(payload)
    status = "unknown" if stale else str(payload.get("overall_status") or "unknown")
    open_count = sum(1 for item in repo.list_incidents(limit=100) if item["status"] == "open")
    notice = capabilities["decision_mode"]["message"]
    if stale:
        notice = "可用性快照已过期；已保存事实仍可读取，但不要把旧健康状态当作当前保证。"
    return {
        "status": status,
        "effective_status": payload.get("effective_status"),
        "monitoring_stale": stale,
        "observed_at": payload.get("completed_at"),
        "age_seconds": age,
        "fresh_for_seconds": STALE_AFTER_SECONDS,
        "capabilities": capabilities,
        "open_incident_count": open_count,
        "notice": notice,
        "schema_version": payload.get("schema_version"),
    }


def admin_dashboard(
    *,
    repository_instance: AvailabilityRepository | None = None,
    now: dt.datetime | None = None,
    history_limit: int = 288,
) -> dict[str, Any]:
    repo = repository_instance or repository
    runs = repo.list_probes(limit=max(history_limit, 10_000))
    latest = runs[0] if runs else None
    public = public_summary(repository_instance=repo, now=now)
    history = [
        {
            "probe_id": item["id"],
            "trigger_type": item["trigger_type"],
            "overall_status": item["overall_status"],
            "effective_status": item["effective_status"],
            "completed_at": item["completed_at"],
            "integrity_verified": bool((item.get("integrity") or {}).get("verified")),
            "summary": (item.get("payload") or {}).get("summary") or {},
            "transition_count": len((item.get("payload") or {}).get("transitions") or []),
        }
        for item in runs[: max(1, min(1000, int(history_limit)))]
    ]
    verification = {
        "latest_probe": (
            repo.verify_probe(str(latest["id"])) if latest else {"verified": True, "probe_id": None}
        ),
        "incident_events": repo.verify_incident_events(),
    }
    return {
        **public,
        "latest": latest,
        "history": history,
        "history_count": len(history),
        "incidents": repo.list_incidents(limit=50),
        "slos": calculate_slos(runs, now=now),
        "verification": verification,
        "policy": {
            "probe_interval_seconds": PROBE_INTERVAL_SECONDS,
            "stale_after_seconds": STALE_AFTER_SECONDS,
            "failure_confirmations": FAILURE_THRESHOLD,
            "recovery_confirmations": RECOVERY_THRESHOLD,
            "safe_degradation": "read_only_degraded",
        },
    }


__all__ = [
    "admin_dashboard",
    "build_capabilities",
    "calculate_slos",
    "collect_components",
    "public_summary",
    "repository",
    "run_probe",
]
