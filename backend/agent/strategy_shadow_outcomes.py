# -*- coding: utf-8 -*-
"""Version-bound Shadow signal enrollment, observation, and gated reporting."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from statistics import fmean
from typing import Any

from strategies.fund_strategy_cohort import (
    COHORT_TAXONOMY_ID,
    COHORT_TAXONOMY_VERSION,
    build_strategy_shadow_cohort,
)

from .registry import ToolRegistry
from .repository import AgentRepository


SHADOW_OUTCOME_SCHEMA_VERSION = "strategy_shadow_outcome.v1"
SHADOW_ENROLLMENT_POLICY_VERSION = "strategy_shadow_enrollment@1.0.0"
SHADOW_OUTCOME_EVIDENCE_VERSION = "1.1.0"
SHADOW_REPORT_VERSION = "strategy_shadow_report@1.1.0"
MIN_RELEASE_GRADE_OUTCOMES = 30
MIN_DISTINCT_FUNDS = 10


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _wilson_interval(successes: int, total: int) -> dict[str, float] | None:
    if total < 1:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return {
        "lower_pct": round(max(0.0, center - margin) * 100, 2),
        "upper_pct": round(min(1.0, center + margin) * 100, 2),
        "confidence_level": 0.95,
        "method": "wilson_score",
    }


def build_shadow_aggregate(
    samples: list[dict[str, Any]],
    *,
    integrity_failures: int,
    scan_complete: bool,
) -> dict[str, Any]:
    release_grade = [item for item in samples if item.get("release_grade")]
    distinct_funds = len({str(item["fund_code"]) for item in release_grade})
    aggregate_ready = (
        len(release_grade) >= MIN_RELEASE_GRADE_OUTCOMES
        and distinct_funds >= MIN_DISTINCT_FUNDS
        and int(integrity_failures) == 0
        and bool(scan_complete)
    )
    metrics = None
    if aggregate_ready:
        directional_hits = sum(bool(item["directionally_correct"]) for item in release_grade)
        peer_samples = [item for item in release_grade if item["peer_edge_correct"] is not None]
        peer_hits = sum(bool(item["peer_edge_correct"]) for item in peer_samples)
        metrics = {
            "sample_count": len(release_grade),
            "directional_hit_rate_pct": round(directional_hits / len(release_grade) * 100, 2),
            "directional_hit_rate_interval": _wilson_interval(
                directional_hits,
                len(release_grade),
            ),
            "mean_signed_unit_nav_return_pct": round(
                fmean(float(item["signed_unit_nav_return_pct"]) for item in release_grade),
                4,
            ),
            "peer_edge_sample_count": len(peer_samples),
            "peer_edge_hit_rate_pct": (
                round(peer_hits / len(peer_samples) * 100, 2) if peer_samples else None
            ),
            "peer_edge_hit_rate_interval": _wilson_interval(peer_hits, len(peer_samples)),
        }
    return {
        "release_grade_count": len(release_grade),
        "distinct_release_grade_funds": distinct_funds,
        "aggregate_available": aggregate_ready,
        "metrics": metrics,
    }


def build_segmented_shadow_aggregate(
    samples: list[dict[str, Any]],
    *,
    integrity_failures: int,
    scan_complete: bool,
    classification_complete: bool,
) -> dict[str, Any]:
    """Disclose metrics only inside comparable Cohorts; never pool heterogeneous axes."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    dimensions_by_key: dict[str, dict[str, Any]] = {}
    observed_cohort_keys: set[str] = set()
    unclassified_observed = 0
    for sample in samples:
        cohort = sample.get("cohort") or {}
        key = str((cohort.get("keys") or {}).get("release_cohort") or "")
        cohort_verified = bool(sample.get("cohort_integrity_verified"))
        cohort_eligible = bool(
            (cohort.get("release_classification") or {}).get("eligible")
        )
        if not key or not cohort_verified:
            unclassified_observed += 1
            continue
        observed_cohort_keys.add(key)
        comparable = bool(sample.get("release_grade") and cohort_eligible)
        normalized = dict(sample)
        normalized["release_grade"] = comparable
        grouped.setdefault(key, []).append(normalized)
        dimensions_by_key[key] = cohort.get("dimensions") or {}

    segments = []
    for key in sorted(grouped):
        segment_samples = grouped[key]
        aggregate = build_shadow_aggregate(
            segment_samples,
            integrity_failures=(
                int(integrity_failures)
                if classification_complete
                else max(1, int(integrity_failures))
            ),
            scan_complete=bool(scan_complete and classification_complete),
        )
        regime_counts = Counter(
            str(((item.get("cohort") or {}).get("keys") or {}).get("regime_cohort") or "unknown")
            for item in segment_samples
            if item.get("release_grade")
        )
        segments.append({
            "key": key,
            "dimensions": dimensions_by_key[key],
            "observed_count": len(segment_samples),
            "release_grade_count": aggregate["release_grade_count"],
            "distinct_release_grade_funds": aggregate["distinct_release_grade_funds"],
            "regime_counts": dict(sorted(regime_counts.items())),
            "disclosure_gate": {
                "aggregate_available": aggregate["aggregate_available"],
                "minimum_release_grade_outcomes": MIN_RELEASE_GRADE_OUTCOMES,
                "minimum_distinct_funds": MIN_DISTINCT_FUNDS,
                "reason": (
                    None
                    if aggregate["aggregate_available"]
                    else "该可比 Cohort 的样本数、基金覆盖或完整性未达披露门槛。"
                ),
            },
            "metrics": aggregate["metrics"],
        })

    comparable_release_grade_count = sum(
        int(item["release_grade_count"]) for item in segments
    )
    pooled_available = bool(
        len(observed_cohort_keys) == 1
        and unclassified_observed == 0
        and classification_complete
        and int(integrity_failures) == 0
        and len(segments) == 1
        and segments[0]["disclosure_gate"]["aggregate_available"]
    )
    if pooled_available:
        pooled_reason = None
        pooled_metrics = segments[0]["metrics"]
    elif len(observed_cohort_keys) > 1:
        pooled_reason = "存在多个市场、资产、载体或预测周期 Cohort，禁止返回混合总体绩效。"
        pooled_metrics = None
    elif unclassified_observed or not classification_complete:
        pooled_reason = "存在未绑定或未通过完整性校验的 Cohort，禁止返回总体绩效。"
        pooled_metrics = None
    else:
        pooled_reason = "单一可比 Cohort 的样本数、基金覆盖或完整性未达披露门槛。"
        pooled_metrics = None
    return {
        "release_grade_count": comparable_release_grade_count,
        "distinct_release_grade_funds": len({
            str(item.get("fund_code"))
            for item in samples
            if item.get("release_grade")
            and item.get("cohort_integrity_verified")
            and ((item.get("cohort") or {}).get("release_classification") or {}).get("eligible")
        }),
        "observed_cohort_count": len(observed_cohort_keys),
        "unclassified_observed_count": unclassified_observed,
        "aggregate_available": pooled_available,
        "metrics": pooled_metrics,
        "reason": pooled_reason,
        "segments": segments,
    }


class StrategyShadowOutcomeError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = str(code)
        self.retryable = bool(retryable)


class StrategyShadowOutcomeService:
    TOOL_NAME = "fund.strategy_shadow_outcome.get"
    TOOL_VERSION = "1.0.0"

    def __init__(self, repository: AgentRepository, registry: ToolRegistry) -> None:
        self.repository = repository
        self.registry = registry

    def _workflow_audit_head(self, run_id: str) -> str | None:
        head = None
        for event in self.repository.list_audit_events(run_id):
            if str(event.get("event_type") or "").startswith("strategy.shadow."):
                break
            head = event.get("event_hash")
        return str(head) if head else None

    def _signal_snapshot(self, run: dict[str, Any]) -> dict[str, Any]:
        result = run.get("result") or {}
        strategy = result.get("strategy") or {}
        governance = strategy.get("governance") or {}
        governed = governance.get("strategy") or {}
        execution = governance.get("execution") or {}
        release = governance.get("release") or {}
        fund = result.get("fund") or {}
        condition = strategy.get("condition") or {}
        signal = strategy.get("signal") or {}
        confidence = strategy.get("confidence") or {}
        horizon_name = str(strategy.get("primary_horizon") or "")
        horizon = next(
            (
                item for item in strategy.get("horizons") or []
                if str(item.get("horizon") or "") == horizon_name
            ),
            None,
        )
        strategy_id = str(strategy.get("strategy_id") or "")
        strategy_version = str(strategy.get("strategy_version") or "")
        manifest_sha256 = str(governed.get("manifest_sha256") or "")
        governance_evidence_id = str(governance.get("evidence_id") or "")
        signal_evidence_id = str(strategy.get("evidence_id") or "")
        baseline_as_of = str(fund.get("as_of") or "")
        baseline_nav = _number(fund.get("unit_nav"))
        direction = str(signal.get("direction") or "")
        observation_days = int((horizon or {}).get("observation_days") or 0)
        run_id = str(run.get("id") or "")
        return {
            "schema_version": SHADOW_OUTCOME_SCHEMA_VERSION,
            "enrollment_policy_version": SHADOW_ENROLLMENT_POLICY_VERSION,
            "source_run": {
                "run_id": run_id,
                "completed_at": run.get("completed_at"),
                "result_schema_version": result.get("schema_version"),
                "workflow_audit_chain_head": self._workflow_audit_head(run_id),
            },
            "strategy": {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "manifest_sha256": manifest_sha256,
                "status_at_signal": str(governed.get("status") or ""),
                "calculation_allowed_at_signal": bool(execution.get("calculation_allowed")),
                "decision_use_allowed_at_signal": bool(execution.get("decision_use_allowed")),
                "release_ready_at_signal": bool(release.get("release_ready")),
                "governance_evidence_id": governance_evidence_id,
                "signal_evidence_id": signal_evidence_id,
            },
            "fund": {
                "code": str(fund.get("code") or (run.get("input") or {}).get("code") or ""),
                "name": fund.get("name"),
                "market": ((result.get("market_profile") or {}).get("market") or {}).get("primary"),
            },
            "baseline": {
                "as_of": baseline_as_of,
                "unit_nav": baseline_nav,
                "condition_as_of": condition.get("as_of"),
            },
            "signal": {
                "direction": direction,
                "decision": str(strategy.get("decision") or ""),
                "strength": signal.get("strength"),
                "confidence_level": str(confidence.get("level") or "unavailable"),
                "horizon": horizon_name,
                "observation_days": observation_days,
                "horizon_status": (horizon or {}).get("status"),
            },
        }

    def eligibility(self, run: dict[str, Any] | None) -> dict[str, Any]:
        if run is None:
            return {"eligible": False, "reason": "run_not_found"}
        if run.get("intent") != "fund_deep_research":
            return {"eligible": False, "reason": "unsupported_intent"}
        if run.get("status") not in {"completed", "partial"} or not run.get("result"):
            return {"eligible": False, "reason": "run_not_research_terminal"}
        snapshot = self._signal_snapshot(run)
        if (run.get("result") or {}).get("schema_version") not in {
            "fund_deep_research.v4",
            "fund_deep_research.v5",
            "fund_deep_research.v6",
        }:
            return {"eligible": False, "reason": "governance_snapshot_not_available"}
        strategy = snapshot["strategy"]
        signal = snapshot["signal"]
        fund = snapshot["fund"]
        baseline = snapshot["baseline"]
        if signal["direction"] not in {"positive", "negative"}:
            return {"eligible": False, "reason": "strategy_signal_not_directional"}
        if signal["horizon_status"] != "available" or signal["observation_days"] < 1:
            return {"eligible": False, "reason": "strategy_horizon_not_evaluable"}
        if not re.fullmatch(r"\d{6}", fund["code"]):
            return {"eligible": False, "reason": "invalid_fund_code"}
        if _date(baseline["as_of"]) is None or _number(baseline["unit_nav"]) is None:
            return {"eligible": False, "reason": "missing_confirmed_nav_baseline"}
        if baseline["condition_as_of"] != baseline["as_of"]:
            return {"eligible": False, "reason": "signal_and_nav_baseline_misaligned"}
        if not strategy["calculation_allowed_at_signal"]:
            return {"eligible": False, "reason": "strategy_calculation_was_blocked"}
        if strategy["status_at_signal"] not in {"shadow", "canary", "active"}:
            return {"eligible": False, "reason": "strategy_status_not_observable"}
        if not strategy["strategy_id"] or not strategy["strategy_version"]:
            return {"eligible": False, "reason": "missing_strategy_version"}
        current = self.repository.get_strategy_version(
            strategy["strategy_id"],
            strategy["strategy_version"],
        )
        if current is None:
            return {"eligible": False, "reason": "strategy_version_unregistered"}
        if not current.get("manifest_integrity_verified"):
            return {"eligible": False, "reason": "strategy_manifest_integrity_failed"}
        if current.get("manifest_sha256") != strategy["manifest_sha256"]:
            return {"eligible": False, "reason": "strategy_manifest_binding_failed"}
        integrity = self.repository.verify_run_evidence_integrity(str(run["id"]))
        if not integrity.get("verified"):
            return {"eligible": False, "reason": "source_run_integrity_failed"}
        for key in ("governance_evidence_id", "signal_evidence_id"):
            evidence_id = strategy[key]
            evidence = self.repository.get_evidence(str(run["id"]), evidence_id)
            if evidence is None or not evidence.get("integrity_verified"):
                return {"eligible": False, "reason": f"{key}_integrity_failed"}
        market_profile = (run.get("result") or {}).get("market_profile") or {}
        market_evidence_id = str(market_profile.get("evidence_id") or "")
        market_evidence = self.repository.get_evidence(str(run["id"]), market_evidence_id)
        if market_evidence is None or not market_evidence.get("integrity_verified"):
            return {"eligible": False, "reason": "market_profile_evidence_integrity_failed"}
        return {
            "eligible": True,
            "reason": None,
            "strategy_id": strategy["strategy_id"],
            "strategy_version": strategy["strategy_version"],
            "signal_direction": signal["direction"],
            "horizon": signal["horizon"],
            "observation_days": signal["observation_days"],
            "snapshot_sha256": _sha256(snapshot),
        }

    @staticmethod
    def _due_at(baseline_as_of: str, observation_days: int) -> str:
        baseline = _date(baseline_as_of)
        if baseline is None:
            raise ValueError("Shadow 入组基线日无效")
        calendar_estimate = math.ceil(int(observation_days) * 365.25 / 252) + 14
        due_date = baseline + dt.timedelta(days=calendar_estimate)
        return dt.datetime.combine(
            due_date,
            dt.time(hour=2),
            tzinfo=dt.timezone.utc,
        ).isoformat(timespec="milliseconds")

    def ensure_enrollment(
        self,
        run: dict[str, Any] | None,
        *,
        actor_id: str = "strategy-shadow-runtime-v1",
        now: str | dt.datetime | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        eligibility = self.eligibility(run)
        if not eligibility["eligible"]:
            return None, False
        assert run is not None
        snapshot = self._signal_snapshot(run)
        strategy = snapshot["strategy"]
        signal = snapshot["signal"]
        fund = snapshot["fund"]
        baseline = snapshot["baseline"]
        enrollment, created = self.repository.ensure_strategy_shadow_enrollment(
            str(run["id"]),
            strategy_id=strategy["strategy_id"],
            strategy_version=strategy["strategy_version"],
            manifest_sha256=strategy["manifest_sha256"],
            strategy_status=strategy["status_at_signal"],
            governance_evidence_id=strategy["governance_evidence_id"],
            signal_evidence_id=strategy["signal_evidence_id"],
            fund_code=fund["code"],
            fund_name=fund["name"],
            baseline_as_of=baseline["as_of"],
            baseline_nav=float(baseline["unit_nav"]),
            signal_direction=signal["direction"],
            signal_decision=signal["decision"],
            confidence_level=signal["confidence_level"],
            horizon=signal["horizon"],
            observation_days=signal["observation_days"],
            signal_snapshot=snapshot,
            due_at=self._due_at(baseline["as_of"], signal["observation_days"]),
            actor_id=actor_id,
            now=now,
        )
        self.ensure_cohort(
            enrollment,
            actor_id=actor_id,
            now=now,
        )
        return enrollment, created

    def ensure_cohort(
        self,
        enrollment: dict[str, Any],
        *,
        actor_id: str = "strategy-shadow-cohort-runtime-v1",
        now: str | dt.datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        run = self.repository.get_run(str(enrollment.get("run_id") or ""))
        if run is None:
            raise ValueError("Shadow Cohort 缺少来源 Run")
        market_profile = (run.get("result") or {}).get("market_profile") or {}
        market_evidence_id = str(market_profile.get("evidence_id") or "")
        market_evidence = self.repository.get_evidence(str(run["id"]), market_evidence_id)
        signal_evidence = self.repository.get_evidence(
            str(run["id"]),
            str(enrollment.get("signal_evidence_id") or ""),
        )
        cohort = build_strategy_shadow_cohort(
            enrollment=enrollment,
            market_profile_evidence=market_evidence or {},
            signal_evidence=signal_evidence or {},
        )
        return self.repository.ensure_strategy_shadow_cohort(
            str(enrollment["id"]),
            cohort=cohort,
            actor_id=actor_id,
            now=now,
        )

    def backfill_missing_cohorts(self, *, limit: int = 1000) -> dict[str, Any]:
        enrollments = self.repository.list_strategy_shadow_enrollments_missing_cohort(
            limit=max(1, min(int(limit), 10000)),
        )
        created = 0
        failures = []
        for enrollment in enrollments:
            try:
                _, was_created = self.ensure_cohort(
                    enrollment,
                    actor_id="strategy-shadow-cohort-backfill-v1",
                )
                created += int(was_created)
            except Exception as error:
                failures.append({
                    "enrollment_id": enrollment.get("id"),
                    "run_id": enrollment.get("run_id"),
                    "error_type": type(error).__name__,
                })
        return {
            "scanned": len(enrollments),
            "created": created,
            "failed": len(failures),
            "failures": failures[:20],
        }

    def backfill_eligible_enrollments(self, *, limit: int = 1000) -> int:
        created_count = 0
        scanned = 0
        after_completed_at = None
        after_run_id = None
        scan_limit = max(1, min(int(limit), 10000))
        while scanned < scan_limit:
            batch_limit = min(100, scan_limit - scanned)
            batch = self.repository.list_unenrolled_strategy_shadow_runs(
                limit=batch_limit,
                after_completed_at=after_completed_at,
                after_run_id=after_run_id,
            )
            if not batch:
                break
            for run in batch:
                enrollment, created = self.ensure_enrollment(
                    run,
                    actor_id="strategy-shadow-backfill-v1",
                )
                if enrollment is not None and created:
                    created_count += 1
            scanned += len(batch)
            after_completed_at = str(batch[-1].get("completed_at") or "")
            after_run_id = str(batch[-1].get("id") or "")
            if len(batch) < batch_limit:
                break
        return created_count

    def verify_enrollment(self, enrollment: dict[str, Any]) -> dict[str, Any]:
        run_id = str(enrollment.get("run_id") or "")
        if not enrollment.get("signal_snapshot_integrity_verified"):
            return {"verified": False, "reason": "signal_snapshot_hash_failed"}
        run = self.repository.get_run(run_id)
        if run is None:
            return {"verified": False, "reason": "source_run_missing"}
        integrity = self.repository.verify_run_evidence_integrity(run_id)
        if not integrity.get("verified"):
            return {"verified": False, "reason": "source_run_integrity_failed"}
        fresh_snapshot = self._signal_snapshot(run)
        if _sha256(fresh_snapshot) != enrollment.get("signal_snapshot_sha256"):
            return {"verified": False, "reason": "source_run_snapshot_binding_failed"}
        snapshot = enrollment.get("signal_snapshot") or {}
        strategy = snapshot.get("strategy") or {}
        signal = snapshot.get("signal") or {}
        fund = snapshot.get("fund") or {}
        baseline = snapshot.get("baseline") or {}
        bound_fields = {
            "strategy_id": strategy.get("strategy_id"),
            "strategy_version": strategy.get("strategy_version"),
            "manifest_sha256": strategy.get("manifest_sha256"),
            "governance_evidence_id": strategy.get("governance_evidence_id"),
            "signal_evidence_id": strategy.get("signal_evidence_id"),
            "fund_code": fund.get("code"),
            "baseline_as_of": baseline.get("as_of"),
            "baseline_nav": baseline.get("unit_nav"),
            "signal_direction": signal.get("direction"),
            "horizon": signal.get("horizon"),
            "observation_days": signal.get("observation_days"),
        }
        for key, expected in bound_fields.items():
            actual = enrollment.get(key)
            if key == "baseline_nav":
                if _number(actual) != _number(expected):
                    return {"verified": False, "reason": f"row_binding_failed:{key}"}
            elif str(actual) != str(expected):
                return {"verified": False, "reason": f"row_binding_failed:{key}"}
        current = self.repository.get_strategy_version(
            str(enrollment["strategy_id"]),
            str(enrollment["strategy_version"]),
        )
        if (
            current is None
            or not current.get("manifest_integrity_verified")
            or current.get("manifest_sha256") != enrollment.get("manifest_sha256")
        ):
            return {"verified": False, "reason": "current_manifest_binding_failed"}
        audit = self.repository.verify_audit_chain(run_id)
        if not audit.get("verified"):
            return {"verified": False, "reason": "source_audit_chain_failed"}
        expected_status = None
        expected_next_run_at = None
        expected_evidence_id = None
        expected_observed_as_of = None
        expected_failure_count = 0
        enrollment_event_count = 0
        for event in self.repository.list_audit_events(run_id):
            details = event.get("details") or {}
            if details.get("enrollment_id") != enrollment.get("id"):
                continue
            enrollment_event_count += 1
            event_type = event.get("event_type")
            if event_type == "strategy.shadow.enrolled":
                if expected_status is not None:
                    return {"verified": False, "reason": "duplicate_enrollment_audit_event"}
                if details.get("signal_snapshot_sha256") != enrollment.get("signal_snapshot_sha256"):
                    return {"verified": False, "reason": "audit_snapshot_binding_failed"}
                expected_status = details.get("status")
                expected_next_run_at = details.get("next_run_at")
            elif event_type == "strategy.shadow.observation.pending":
                expected_status = details.get("status")
                expected_next_run_at = details.get("next_run_at")
                expected_failure_count = 0
            elif event_type == "strategy.shadow.observation.failed":
                expected_status = details.get("status")
                expected_next_run_at = details.get("next_run_at")
                expected_failure_count = int(details.get("consecutive_failures") or 0)
            elif event_type == "strategy.shadow.observation.completed":
                expected_status = details.get("status")
                expected_next_run_at = details.get("next_run_at")
                expected_evidence_id = details.get("evidence_id")
                expected_observed_as_of = details.get("observed_as_of")
                expected_failure_count = 0
        if expected_status is None:
            return {"verified": False, "reason": "enrollment_audit_event_missing"}
        if expected_status != enrollment.get("status"):
            return {"verified": False, "reason": "enrollment_status_replay_failed"}
        if expected_next_run_at != enrollment.get("next_run_at"):
            return {"verified": False, "reason": "enrollment_schedule_replay_failed"}
        if expected_failure_count != int(enrollment.get("consecutive_failures") or 0):
            return {"verified": False, "reason": "enrollment_failure_count_replay_failed"}
        if expected_status == "observed" and (
            expected_evidence_id != enrollment.get("last_evidence_id")
            or expected_observed_as_of != enrollment.get("observed_as_of")
        ):
            return {"verified": False, "reason": "enrollment_outcome_replay_failed"}
        return {
            "verified": True,
            "reason": None,
            "audit_event_count": enrollment_event_count,
            "source_evidence_count": integrity.get("evidence_count"),
            "source_audit_chain_head": audit.get("chain_head"),
        }

    def verify_cohort(
        self,
        cohort_record: dict[str, Any] | None,
        enrollment: dict[str, Any],
    ) -> dict[str, Any]:
        if cohort_record is None:
            return {"verified": False, "reason": "cohort_binding_missing"}
        if not cohort_record.get("cohort_integrity_verified"):
            return {"verified": False, "reason": "cohort_snapshot_hash_failed"}
        if cohort_record.get("enrollment_id") != enrollment.get("id"):
            return {"verified": False, "reason": "cohort_enrollment_binding_failed"}
        run_id = str(enrollment.get("run_id") or "")
        market_evidence = self.repository.get_evidence(
            run_id,
            str(cohort_record.get("market_profile_evidence_id") or ""),
        )
        signal_evidence = self.repository.get_evidence(
            run_id,
            str(cohort_record.get("signal_evidence_id") or ""),
        )
        if market_evidence is None or signal_evidence is None:
            return {"verified": False, "reason": "cohort_source_evidence_missing"}
        if (
            not market_evidence.get("integrity_verified")
            or not signal_evidence.get("integrity_verified")
            or market_evidence.get("payload_sha256")
            != cohort_record.get("market_profile_payload_sha256")
            or signal_evidence.get("payload_sha256")
            != cohort_record.get("signal_payload_sha256")
        ):
            return {"verified": False, "reason": "cohort_source_evidence_integrity_failed"}
        try:
            rebuilt = build_strategy_shadow_cohort(
                enrollment=enrollment,
                market_profile_evidence=market_evidence,
                signal_evidence=signal_evidence,
            )
        except ValueError as error:
            return {"verified": False, "reason": f"cohort_rebuild_failed:{error}"}
        if _sha256(rebuilt) != cohort_record.get("cohort_sha256"):
            return {"verified": False, "reason": "cohort_source_rebuild_hash_failed"}
        cohort = cohort_record.get("cohort") or {}
        dimensions = cohort.get("dimensions") or {}
        release = cohort.get("release_classification") or {}
        bound_fields = {
            "strategy_id": (cohort.get("enrollment_binding") or {}).get("strategy_id"),
            "strategy_version": (cohort.get("enrollment_binding") or {}).get("strategy_version"),
            "fund_code": (cohort.get("enrollment_binding") or {}).get("fund_code"),
            "horizon": (dimensions.get("horizon") or {}).get("name"),
            "observation_days": (dimensions.get("horizon") or {}).get(
                "confirmed_nav_observations"
            ),
            "taxonomy_id": (cohort.get("taxonomy") or {}).get("id"),
            "taxonomy_version": (cohort.get("taxonomy") or {}).get("version"),
            "market_primary": (dimensions.get("market") or {}).get("primary"),
            "asset_class": (dimensions.get("asset_class") or {}).get("primary"),
            "vehicle_type": (dimensions.get("vehicle") or {}).get("type"),
            "trend_regime": (dimensions.get("signal_regime") or {}).get("trend"),
            "drawdown_regime": (dimensions.get("signal_regime") or {}).get(
                "drawdown_band"
            ),
            "release_cohort_key": (cohort.get("keys") or {}).get("release_cohort"),
            "regime_cohort_key": (cohort.get("keys") or {}).get("regime_cohort"),
        }
        for key, expected in bound_fields.items():
            if str(cohort_record.get(key)) != str(expected):
                return {"verified": False, "reason": f"cohort_row_binding_failed:{key}"}
        if bool(cohort_record.get("release_eligible")) != bool(release.get("eligible")):
            return {"verified": False, "reason": "cohort_row_binding_failed:release_eligible"}
        cohort_evidence = self.repository.get_evidence(
            run_id,
            str(cohort_record.get("evidence_id") or ""),
        )
        if (
            cohort_evidence is None
            or cohort_evidence.get("evidence_type") != "strategy_shadow_cohort"
            or not cohort_evidence.get("integrity_verified")
            or cohort_evidence.get("payload_sha256") != cohort_record.get("cohort_sha256")
            or cohort_evidence.get("payload") != cohort
        ):
            return {"verified": False, "reason": "cohort_evidence_binding_failed"}
        audit = self.repository.verify_audit_chain(run_id)
        if not audit.get("verified"):
            return {"verified": False, "reason": "cohort_audit_chain_failed"}
        binding_events = []
        for event in self.repository.list_audit_events(run_id):
            if event.get("event_type") != "strategy.shadow.cohort.bound":
                continue
            details = event.get("details") or {}
            if details.get("cohort_id") == cohort_record.get("id"):
                binding_events.append(details)
        if len(binding_events) != 1:
            return {"verified": False, "reason": "cohort_binding_audit_event_count_failed"}
        details = binding_events[0]
        expected_audit = {
            "enrollment_id": cohort_record.get("enrollment_id"),
            "evidence_id": cohort_record.get("evidence_id"),
            "cohort_sha256": cohort_record.get("cohort_sha256"),
            "taxonomy_id": cohort_record.get("taxonomy_id"),
            "taxonomy_version": cohort_record.get("taxonomy_version"),
            "market_profile_evidence_id": cohort_record.get("market_profile_evidence_id"),
            "signal_evidence_id": cohort_record.get("signal_evidence_id"),
            "release_cohort_key": cohort_record.get("release_cohort_key"),
            "regime_cohort_key": cohort_record.get("regime_cohort_key"),
            "release_eligible": cohort_record.get("release_eligible"),
        }
        if any(details.get(key) != value for key, value in expected_audit.items()):
            return {"verified": False, "reason": "cohort_binding_audit_replay_failed"}
        return {
            "verified": True,
            "reason": None,
            "taxonomy_id": cohort_record.get("taxonomy_id"),
            "taxonomy_version": cohort_record.get("taxonomy_version"),
            "cohort_sha256": cohort_record.get("cohort_sha256"),
            "evidence_id": cohort_record.get("evidence_id"),
            "audit_chain_head": audit.get("chain_head"),
        }

    def _invoke_tool(self, enrollment: dict[str, Any]) -> dict[str, Any]:
        definition = self.registry.get(self.TOOL_NAME, self.TOOL_VERSION)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="strategy-shadow-outcome")
        future = executor.submit(definition.handler, {
            "code": enrollment["fund_code"],
            "baseline_as_of": enrollment["baseline_as_of"],
            "baseline_nav": enrollment["baseline_nav"],
            "signal_direction": enrollment["signal_direction"],
            "horizon": enrollment["horizon"],
            "observation_days": enrollment["observation_days"],
        })
        try:
            return future.result(timeout=float(definition.timeout_seconds))
        except FutureTimeoutError as error:
            future.cancel()
            raise StrategyShadowOutcomeError(
                "SHADOW_OUTCOME_TOOL_TIMEOUT",
                "策略 Shadow 真实净值观测超过执行时限",
                retryable=True,
            ) from error
        except StrategyShadowOutcomeError:
            raise
        except ValueError as error:
            raise StrategyShadowOutcomeError(
                "INVALID_SHADOW_OUTCOME_INPUT",
                str(error),
                retryable=False,
            ) from error
        except Exception as error:
            raise StrategyShadowOutcomeError(
                "SHADOW_OUTCOME_PROVIDER_FAILED",
                f"策略 Shadow 真实结果读取失败:{error}",
                retryable=True,
            ) from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def evaluate_enrollment(
        self,
        enrollment: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        verification = self.verify_enrollment(enrollment)
        if not verification["verified"]:
            raise StrategyShadowOutcomeError(
                "SHADOW_ENROLLMENT_INTEGRITY_FAILED",
                f"Shadow 入组快照或状态校验失败:{verification['reason']}",
                retryable=False,
            )
        cohort_record = self.repository.get_strategy_shadow_cohort(str(enrollment["id"]))
        cohort_verification = self.verify_cohort(cohort_record, enrollment)
        if not cohort_verification["verified"]:
            raise StrategyShadowOutcomeError(
                "SHADOW_COHORT_INTEGRITY_FAILED",
                f"Shadow Cohort 绑定或审计校验失败:{cohort_verification['reason']}",
                retryable=False,
            )
        outcome = self._invoke_tool(enrollment)
        status = str(outcome.get("status") or "")
        if status == "blocked":
            raise StrategyShadowOutcomeError(
                str(outcome.get("reason_code") or "SHADOW_OUTCOME_BLOCKED"),
                str(outcome.get("reason") or "策略 Shadow 结果被数据完整性门禁阻断"),
                retryable=False,
            )
        if status == "pending":
            return {
                "status": "pending",
                "outcome": outcome,
                "verification": verification,
                "cohort_verification": cohort_verification,
            }
        if status != "observed":
            raise StrategyShadowOutcomeError(
                "INVALID_SHADOW_OUTCOME_STATUS",
                f"策略 Shadow 结果返回未知状态:{status}",
                retryable=False,
            )
        run = self.repository.get_run(str(enrollment["run_id"]))
        assert run is not None
        outcome["strategy_binding"] = {
            "strategy_id": enrollment["strategy_id"],
            "strategy_version": enrollment["strategy_version"],
            "manifest_sha256": enrollment["manifest_sha256"],
            "strategy_status_at_signal": enrollment["strategy_status"],
            "signal_snapshot_sha256": enrollment["signal_snapshot_sha256"],
            "enrollment_policy_version": SHADOW_ENROLLMENT_POLICY_VERSION,
        }
        outcome["source_run"] = {
            "run_id": enrollment["run_id"],
            "completed_at": run.get("completed_at"),
            "result_schema_version": (run.get("result") or {}).get("schema_version"),
            "workflow_audit_chain_head": (
                (enrollment.get("signal_snapshot") or {}).get("source_run") or {}
            ).get("workflow_audit_chain_head"),
            "current_audit_chain_head": verification.get("source_audit_chain_head"),
        }
        outcome["enrollment"] = {
            "enrollment_id": enrollment["id"],
            "baseline_as_of": enrollment["baseline_as_of"],
            "horizon": enrollment["horizon"],
            "observation_days": enrollment["observation_days"],
            "non_overlapping_per_fund_version_horizon": True,
        }
        outcome["cohort_binding"] = {
            "cohort_id": cohort_record["id"],
            "cohort_sha256": cohort_record["cohort_sha256"],
            "cohort_evidence_id": cohort_record["evidence_id"],
            "taxonomy_id": cohort_record["taxonomy_id"],
            "taxonomy_version": cohort_record["taxonomy_version"],
            "release_cohort_key": cohort_record["release_cohort_key"],
            "regime_cohort_key": cohort_record["regime_cohort_key"],
            "release_eligible": cohort_record["release_eligible"],
        }
        quality_status = str((outcome.get("quality") or {}).get("status") or "partial")
        if quality_status not in {"complete", "partial"}:
            quality_status = "partial"
        observed_as_of = str((outcome.get("observed") or {}).get("as_of") or "")
        evidence, created = self.repository.add_post_run_evidence(
            str(enrollment["run_id"]),
            evidence_type="strategy_shadow_outcome",
            subject_type="fund_strategy",
            subject_id=f"{enrollment['strategy_id']}@{enrollment['strategy_version']}:{enrollment['fund_code']}",
            provider=str(outcome.get("source") or self.TOOL_NAME),
            source_url=str(outcome.get("source_url") or "") or None,
            as_of=observed_as_of,
            schema_version=SHADOW_OUTCOME_EVIDENCE_VERSION,
            quality_status=quality_status,
            payload=outcome,
            actor_type="system",
            actor_id=actor_id,
        )
        return {
            "status": "observed",
            "created": created,
            "outcome": evidence.get("payload") or {},
            "evidence": evidence,
            "verification": verification,
            "cohort_verification": cohort_verification,
        }

    @staticmethod
    def public_enrollment(enrollment: dict[str, Any] | None) -> dict[str, Any] | None:
        if enrollment is None:
            return None
        return {
            key: enrollment.get(key)
            for key in (
                "id",
                "run_id",
                "strategy_id",
                "strategy_version",
                "manifest_sha256",
                "strategy_status",
                "fund_code",
                "fund_name",
                "baseline_as_of",
                "baseline_nav",
                "signal_direction",
                "signal_decision",
                "confidence_level",
                "horizon",
                "observation_days",
                "signal_snapshot_sha256",
                "signal_snapshot_integrity_verified",
                "status",
                "exclusion_reason",
                "blocking_enrollment_id",
                "next_run_at",
                "attempt_count",
                "consecutive_failures",
                "last_started_at",
                "last_finished_at",
                "last_provider_as_of",
                "observed_as_of",
                "last_evidence_id",
                "last_error_code",
                "created_at",
                "updated_at",
            )
        }

    @staticmethod
    def public_cohort(cohort_record: dict[str, Any] | None) -> dict[str, Any] | None:
        if cohort_record is None:
            return None
        cohort = cohort_record.get("cohort") or {}
        return {
            "id": cohort_record.get("id"),
            "enrollment_id": cohort_record.get("enrollment_id"),
            "evidence_id": cohort_record.get("evidence_id"),
            "taxonomy_id": cohort_record.get("taxonomy_id"),
            "taxonomy_version": cohort_record.get("taxonomy_version"),
            "cohort_sha256": cohort_record.get("cohort_sha256"),
            "cohort_integrity_verified": cohort_record.get("cohort_integrity_verified"),
            "release_eligible": cohort_record.get("release_eligible"),
            "release_cohort_key": cohort_record.get("release_cohort_key"),
            "regime_cohort_key": cohort_record.get("regime_cohort_key"),
            "dimensions": cohort.get("dimensions") or {},
            "release_classification": cohort.get("release_classification") or {},
            "created_at": cohort_record.get("created_at"),
        }

    def report(
        self,
        strategy_id: str,
        strategy_version: str,
        *,
        limit: int = 200,
    ) -> dict[str, Any] | None:
        strategy = self.repository.get_strategy_version(strategy_id, strategy_version)
        if strategy is None:
            return None
        total_enrollments = self.repository.count_strategy_shadow_enrollments(
            strategy_id,
            strategy_version,
        )
        enrollments = self.repository.list_strategy_shadow_enrollments(
            strategy_id,
            strategy_version,
            limit=2000,
        )
        scan_complete = total_enrollments == len(enrollments)
        statuses = Counter(str(item.get("status") or "unknown") for item in enrollments)
        exclusions = Counter(
            str(item.get("exclusion_reason"))
            for item in enrollments
            if item.get("exclusion_reason")
        )
        cohort_records = self.repository.list_strategy_shadow_cohorts(
            strategy_id,
            strategy_version,
            limit=2000,
        )
        cohort_by_enrollment = {
            str(item["enrollment_id"]): item for item in cohort_records
        }
        samples = []
        integrity_failures = 0
        cohort_integrity_failures = 0
        cohort_missing = 0
        cohort_release_eligible = 0
        market_counts: Counter[str] = Counter()
        asset_counts: Counter[str] = Counter()
        horizon_counts: Counter[str] = Counter()
        for enrollment in enrollments:
            record_integrity_failed = not bool(
                enrollment.get("signal_snapshot_integrity_verified")
            )
            cohort_record = cohort_by_enrollment.get(str(enrollment["id"]))
            cohort_verification = self.verify_cohort(cohort_record, enrollment)
            cohort_verified = bool(cohort_verification.get("verified"))
            if cohort_record is None:
                cohort_missing += 1
            if not cohort_verified:
                cohort_integrity_failures += 1
                record_integrity_failed = True
            else:
                cohort_release_eligible += int(bool(cohort_record.get("release_eligible")))
                market_counts[str(cohort_record.get("market_primary") or "unknown")] += 1
                asset_counts[str(cohort_record.get("asset_class") or "unknown")] += 1
                horizon_counts[str(cohort_record.get("horizon") or "unknown")] += 1
            if enrollment.get("status") != "observed":
                if record_integrity_failed:
                    integrity_failures += 1
                continue
            enrollment_verification = self.verify_enrollment(enrollment)
            enrollment_verified = bool(enrollment_verification.get("verified"))
            if not enrollment_verified:
                record_integrity_failed = True
            if not enrollment.get("last_evidence_id"):
                integrity_failures += 1
                continue
            evidence = self.repository.get_evidence(
                str(enrollment["run_id"]),
                str(enrollment["last_evidence_id"]),
            )
            payload = (evidence or {}).get("payload") or {}
            binding = payload.get("strategy_binding") or {}
            cohort_binding = payload.get("cohort_binding") or {}
            evidence_verified = bool(
                evidence
                and evidence.get("integrity_verified")
                and binding.get("strategy_id") == strategy_id
                and binding.get("strategy_version") == strategy_version
                and binding.get("manifest_sha256") == enrollment.get("manifest_sha256")
                and binding.get("signal_snapshot_sha256") == enrollment.get("signal_snapshot_sha256")
                and enrollment_verified
                and cohort_verified
                and cohort_binding.get("cohort_id") == (cohort_record or {}).get("id")
                and cohort_binding.get("cohort_sha256")
                == (cohort_record or {}).get("cohort_sha256")
                and cohort_binding.get("taxonomy_id") == COHORT_TAXONOMY_ID
                and cohort_binding.get("taxonomy_version") == COHORT_TAXONOMY_VERSION
            )
            score = payload.get("score") or {}
            observed = payload.get("observed") or {}
            peer = payload.get("peer_comparison") or {}
            schema_valid = bool(
                payload.get("evaluator_id") == "fund_strategy_shadow_outcome"
                and payload.get("evaluator_version") == "1.0.0"
                and payload.get("status") == "observed"
                and _date(observed.get("as_of")) is not None
                and _number(observed.get("unit_nav_return_pct")) is not None
                and isinstance(score.get("directionally_correct"), bool)
                and _number(score.get("signed_unit_nav_return_pct")) is not None
                and (
                    score.get("peer_edge_correct") is None
                    or isinstance(score.get("peer_edge_correct"), bool)
                )
            )
            evidence_verified = bool(evidence_verified and schema_valid)
            if not evidence_verified:
                record_integrity_failed = True
            if record_integrity_failed:
                integrity_failures += 1
            cohort_payload = (cohort_record or {}).get("cohort") or {}
            samples.append({
                "enrollment_id": enrollment["id"],
                "run_id": enrollment["run_id"],
                "fund_code": enrollment["fund_code"],
                "fund_name": enrollment.get("fund_name"),
                "baseline_as_of": enrollment["baseline_as_of"],
                "observed_as_of": enrollment.get("observed_as_of"),
                "signal_direction": enrollment["signal_direction"],
                "horizon": enrollment["horizon"],
                "observation_days": enrollment["observation_days"],
                "unit_nav_return_pct": observed.get("unit_nav_return_pct"),
                "directionally_correct": score.get("directionally_correct"),
                "signed_unit_nav_return_pct": score.get("signed_unit_nav_return_pct"),
                "peer_status": peer.get("status"),
                "relative_excess_return_pct": peer.get("relative_excess_return_pct"),
                "peer_edge_correct": score.get("peer_edge_correct"),
                "release_grade": bool(score.get("release_grade") and evidence_verified),
                "evidence_id": evidence.get("id") if evidence else None,
                "payload_sha256": evidence.get("payload_sha256") if evidence else None,
                "integrity_verified": evidence_verified,
                "cohort_integrity_verified": cohort_verified,
                "cohort": {
                    "taxonomy": cohort_payload.get("taxonomy") or {},
                    "dimensions": cohort_payload.get("dimensions") or {},
                    "keys": cohort_payload.get("keys") or {},
                    "release_classification": (
                        cohort_payload.get("release_classification") or {}
                    ),
                },
            })
        classification_complete = bool(
            scan_complete
            and len(cohort_records) == len(enrollments)
            and cohort_missing == 0
            and cohort_integrity_failures == 0
        )
        aggregate = build_segmented_shadow_aggregate(
            samples,
            integrity_failures=integrity_failures,
            scan_complete=scan_complete,
            classification_complete=classification_complete,
        )
        return {
            "schema_version": SHADOW_REPORT_VERSION,
            "strategy": {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "manifest_sha256": strategy.get("manifest_sha256"),
                "status": strategy.get("status"),
                "manifest_integrity_verified": strategy.get("manifest_integrity_verified"),
            },
            "enrollment": {
                "total": total_enrollments,
                "verified_scan_count": len(enrollments),
                "scan_complete": scan_complete,
                "status_counts": dict(sorted(statuses.items())),
                "exclusion_reason_counts": dict(sorted(exclusions.items())),
                "non_overlap_unit": "strategy_version+fund_code+horizon",
                "selection": "chronological_first_signal_after_previous_observed_window",
            },
            "cohort_binding": {
                "taxonomy_id": COHORT_TAXONOMY_ID,
                "taxonomy_version": COHORT_TAXONOMY_VERSION,
                "bound_count": len(cohort_records),
                "missing_count": cohort_missing,
                "integrity_failure_count": cohort_integrity_failures,
                "release_eligible_count": cohort_release_eligible,
                "classification_complete": classification_complete,
                "market_counts": dict(sorted(market_counts.items())),
                "asset_class_counts": dict(sorted(asset_counts.items())),
                "horizon_counts": dict(sorted(horizon_counts.items())),
            },
            "observation": {
                "observed_count": len(samples),
                "release_grade_count": aggregate["release_grade_count"],
                "distinct_release_grade_funds": aggregate["distinct_release_grade_funds"],
                "integrity_failure_count": integrity_failures,
                "observed_cohort_count": aggregate["observed_cohort_count"],
                "unclassified_observed_count": aggregate["unclassified_observed_count"],
            },
            "disclosure_gate": {
                "aggregate_available": aggregate["aggregate_available"],
                "minimum_release_grade_outcomes": MIN_RELEASE_GRADE_OUTCOMES,
                "minimum_distinct_funds": MIN_DISTINCT_FUNDS,
                "comparability_unit": "horizon+market+asset_class+vehicle_type",
                "cross_cohort_pooling": "forbidden",
                "reason": aggregate["reason"],
            },
            "metrics": aggregate["metrics"],
            "segments": aggregate["segments"],
            "samples": samples[: max(1, min(int(limit), 500))],
            "release_effect": "none_manual_review_required",
            "policy": "Shadow Outcome 只在预测周期、市场、资产类别和基金载体一致的 Cohort 内披露绩效；不同 Cohort 禁止池化，达到门槛也不会自动改变策略发布状态或保证未来收益。",
        }
