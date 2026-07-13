# -*- coding: utf-8 -*-
"""Versioned Shadow samples are durable, non-overlapping, and fail closed."""

import copy
import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for candidate in (PROJECT_ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from agent.registry import ToolDefinition  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from agent.strategy_governance import StrategyGovernanceService  # noqa: E402
from agent.strategy_shadow_outcomes import (  # noqa: E402
    StrategyShadowOutcomeService,
    build_segmented_shadow_aggregate,
    build_shadow_aggregate,
)
from agent.strategy_shadow_worker import StrategyShadowOutcomeWorker  # noqa: E402
from agent.workflow import AgentWorkflowRunner  # noqa: E402
from backend.tests.test_agent_runtime import _analysis, _registry  # noqa: E402


def _observed_outcome(payload):
    return {
        "evaluator_id": "fund_strategy_shadow_outcome",
        "evaluator_version": "1.0.0",
        "status": "observed",
        "code": payload["code"],
        "signal": {
            "direction": payload["signal_direction"],
            "horizon": payload["horizon"],
            "confirmed_nav_observations": payload["observation_days"],
        },
        "baseline": {
            "as_of": payload["baseline_as_of"],
            "unit_nav": payload["baseline_nav"],
        },
        "observed": {
            "as_of": "2027-01-05",
            "unit_nav": 1.35,
            "confirmed_nav_observation_number": payload["observation_days"],
            "calendar_days": 179,
            "unit_nav_return_pct": 9.356,
        },
        "peer_comparison": {
            "status": "available",
            "relative_excess_return_pct": 2.1,
        },
        "score": {
            "directionally_correct": True,
            "signed_unit_nav_return_pct": 9.356,
            "peer_edge_correct": True,
            "release_grade": True,
        },
        "provider_as_of": "2027-01-06",
        "source": "real_provider_test_snapshot",
        "source_url": "https://example.test/001480",
        "quality": {"status": "complete", "no_synthetic_data": True},
    }


def _pending_outcome(payload):
    return {
        "evaluator_id": "fund_strategy_shadow_outcome",
        "evaluator_version": "1.0.0",
        "status": "pending",
        "code": payload["code"],
        "provider_as_of": "2026-12-31",
        "progress": {
            "available_observations": payload["observation_days"] - 1,
            "required_observations": payload["observation_days"],
        },
        "quality": {"status": "partial", "no_synthetic_data": True},
    }


class StrategyShadowOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent.db"
        self.repository = AgentRepository(self.db_path)
        self.governance = StrategyGovernanceService(self.repository)
        self.governance.seed_defaults()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _service(self, outcome_handler=_observed_outcome):
        registry = _registry(governance_handler=self.governance.evaluate_runtime_use)
        registry.register(ToolDefinition(
            name="fund.strategy_shadow_outcome.get",
            version="1.0.0",
            description="test shadow outcome",
            risk_level="R0",
            timeout_seconds=5,
            handler=outcome_handler,
        ))
        return StrategyShadowOutcomeService(self.repository, registry)

    def _run(self, *, analysis_handler=_analysis):
        registry = _registry(
            analysis_handler=analysis_handler,
            governance_handler=self.governance.evaluate_runtime_use,
        )
        created, is_new = self.repository.create_run(
            "fund_deep_research",
            {
                "intent": "fund_deep_research",
                "code": "001480",
                "months": 60,
                "include_estimate": False,
                "include_disclosure_changes": False,
                "include_alternatives": False,
                "include_portfolio_context": False,
            },
        )
        self.assertTrue(is_new)
        claimed = self.repository.claim_next_run("test-worker")
        AgentWorkflowRunner(self.repository, registry).execute(claimed)
        return self.repository.get_run(created["id"])

    def test_shadow_run_enrolls_with_exact_version_and_immutable_snapshot(self):
        service = self._service()
        run = self._run()
        eligibility = service.eligibility(run)
        enrollment, created = service.ensure_enrollment(
            run,
            now="2026-07-13T00:00:00+00:00",
        )

        self.assertTrue(eligibility["eligible"])
        self.assertTrue(created)
        self.assertEqual(enrollment["status"], "scheduled")
        self.assertEqual(enrollment["strategy_id"], "fund_conditioned_forward_return")
        self.assertEqual(enrollment["strategy_version"], "1.0.0")
        self.assertEqual(enrollment["strategy_status"], "shadow")
        self.assertEqual(enrollment["signal_direction"], "positive")
        self.assertEqual(enrollment["horizon"], "6m")
        self.assertEqual(enrollment["observation_days"], 126)
        self.assertTrue(enrollment["signal_snapshot_integrity_verified"])
        self.assertTrue(service.verify_enrollment(enrollment)["verified"])
        cohort = self.repository.get_strategy_shadow_cohort(enrollment["id"])
        self.assertIsNotNone(cohort)
        self.assertEqual(cohort["taxonomy_version"], "1.0.0")
        self.assertEqual(cohort["market_primary"], "mainland")
        self.assertEqual(cohort["asset_class"], "mixed")
        self.assertTrue(cohort["release_eligible"])
        self.assertTrue(service.verify_cohort(cohort, enrollment)["verified"])
        cohort_evidence = self.repository.get_evidence(run["id"], cohort["evidence_id"])
        self.assertEqual(cohort_evidence["evidence_type"], "strategy_shadow_cohort")
        self.assertTrue(cohort_evidence["integrity_verified"])
        repeated, repeated_created = service.ensure_enrollment(run)
        self.assertFalse(repeated_created)
        self.assertEqual(repeated["id"], enrollment["id"])
        self.assertEqual(
            len(self.repository.list_evidence_by_type(run["id"], "strategy_shadow_cohort")),
            1,
        )

    def test_chronological_non_overlap_rule_persists_excluded_signal(self):
        service = self._service()
        first, _ = service.ensure_enrollment(self._run())
        second, created = service.ensure_enrollment(self._run())

        self.assertTrue(created)
        self.assertEqual(first["status"], "scheduled")
        self.assertEqual(second["status"], "excluded")
        self.assertEqual(second["exclusion_reason"], "prior_window_in_progress")
        self.assertEqual(second["blocking_enrollment_id"], first["id"])
        self.assertIsNone(second["next_run_at"])
        self.assertTrue(service.verify_enrollment(second)["verified"])

    def test_lease_worker_persists_one_observed_evidence_and_gated_report(self):
        service = self._service()
        run = self._run()
        enrollment, _ = service.ensure_enrollment(run)
        worker = StrategyShadowOutcomeWorker(
            self.repository,
            service,
            poll_interval=1,
            lease_seconds=60,
        )

        self.assertTrue(worker.run_once(now="2028-01-01T00:00:00+00:00"))
        observed = self.repository.get_strategy_shadow_enrollment(run["id"])
        self.assertEqual(observed["status"], "observed")
        self.assertEqual(observed["observed_as_of"], "2027-01-05")
        self.assertIsNotNone(observed["last_evidence_id"])
        evidence = self.repository.get_evidence(run["id"], observed["last_evidence_id"])
        self.assertTrue(evidence["integrity_verified"])
        self.assertEqual(evidence["evidence_type"], "strategy_shadow_outcome")
        self.assertEqual(evidence["schema_version"], "1.1.0")
        self.assertEqual(
            evidence["payload"]["strategy_binding"]["signal_snapshot_sha256"],
            enrollment["signal_snapshot_sha256"],
        )
        cohort = self.repository.get_strategy_shadow_cohort(enrollment["id"])
        self.assertEqual(
            evidence["payload"]["cohort_binding"]["cohort_sha256"],
            cohort["cohort_sha256"],
        )
        self.assertTrue(service.verify_enrollment(observed)["verified"])
        self.assertTrue(self.repository.verify_audit_chain(run["id"])["verified"])

        report = service.report("fund_conditioned_forward_return", "1.0.0")
        self.assertEqual(report["schema_version"], "strategy_shadow_report@1.1.0")
        self.assertEqual(report["observation"]["observed_count"], 1)
        self.assertEqual(report["observation"]["release_grade_count"], 1)
        self.assertFalse(report["disclosure_gate"]["aggregate_available"])
        self.assertIsNone(report["metrics"])
        self.assertTrue(report["cohort_binding"]["classification_complete"])
        self.assertEqual(report["cohort_binding"]["bound_count"], 1)
        self.assertEqual(len(report["segments"]), 1)

    def test_pending_provider_result_releases_lease_without_creating_evidence(self):
        service = self._service(_pending_outcome)
        run = self._run()
        enrollment, _ = service.ensure_enrollment(run)
        worker = StrategyShadowOutcomeWorker(self.repository, service)

        self.assertTrue(worker.run_once(now="2028-01-01T00:00:00+00:00"))
        pending = self.repository.get_strategy_shadow_enrollment(run["id"])
        self.assertEqual(pending["status"], "scheduled")
        self.assertIsNone(pending["lease_owner"])
        self.assertEqual(pending["last_provider_as_of"], "2026-12-31")
        self.assertEqual(pending["next_run_at"], "2028-01-02T00:00:00.000+00:00")
        self.assertEqual(
            self.repository.list_evidence_by_type(run["id"], "strategy_shadow_outcome"),
            [],
        )
        self.assertTrue(service.verify_enrollment(pending)["verified"])
        self.assertEqual(enrollment["attempt_count"], 0)

    def test_only_one_worker_can_claim_a_due_enrollment(self):
        service = self._service()
        run = self._run()
        service.ensure_enrollment(run)
        first = self.repository.claim_due_strategy_shadow_enrollment(
            "worker-a",
            now="2028-01-01T00:00:00+00:00",
        )
        second = self.repository.claim_due_strategy_shadow_enrollment(
            "worker-b",
            now="2028-01-01T00:00:00+00:00",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_snapshot_tamper_is_blocked_before_provider_call(self):
        provider_calls = []

        def handler(payload):
            provider_calls.append(payload)
            return _observed_outcome(payload)

        service = self._service(handler)
        run = self._run()
        service.ensure_enrollment(run)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE agent_strategy_shadow_enrollments SET signal_snapshot_json='{}' WHERE run_id=?",
                (run["id"],),
            )
            connection.commit()
        finally:
            connection.close()
        worker = StrategyShadowOutcomeWorker(self.repository, service)
        worker.run_once(now="2028-01-01T00:00:00+00:00")

        blocked = self.repository.get_strategy_shadow_enrollment(run["id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["last_error_code"], "SHADOW_ENROLLMENT_INTEGRITY_FAILED")
        self.assertEqual(provider_calls, [])
        self.assertTrue(self.repository.verify_audit_chain(run["id"])["verified"])

    def test_cohort_tamper_is_blocked_before_provider_call(self):
        provider_calls = []

        def handler(payload):
            provider_calls.append(payload)
            return _observed_outcome(payload)

        service = self._service(handler)
        run = self._run()
        enrollment, _ = service.ensure_enrollment(run)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE agent_strategy_shadow_cohorts SET cohort_json='{}' WHERE enrollment_id=?",
                (enrollment["id"],),
            )
            connection.commit()
        finally:
            connection.close()

        StrategyShadowOutcomeWorker(self.repository, service).run_once(
            now="2028-01-01T00:00:00+00:00"
        )
        blocked = self.repository.get_strategy_shadow_enrollment(run["id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["last_error_code"], "SHADOW_COHORT_INTEGRITY_FAILED")
        self.assertEqual(provider_calls, [])

    def test_direct_status_tamper_fails_audit_state_replay(self):
        service = self._service()
        run = self._run()
        service.ensure_enrollment(run)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE agent_strategy_shadow_enrollments SET status='observed' WHERE run_id=?",
                (run["id"],),
            )
            connection.commit()
        finally:
            connection.close()
        tampered = self.repository.get_strategy_shadow_enrollment(run["id"])
        verification = service.verify_enrollment(tampered)
        self.assertFalse(verification["verified"])
        self.assertEqual(verification["reason"], "enrollment_status_replay_failed")

    def test_retryable_provider_failures_back_off_then_block(self):
        calls = []

        def unavailable(payload):
            calls.append(payload)
            raise RuntimeError("provider unavailable")

        service = self._service(unavailable)
        run = self._run()
        service.ensure_enrollment(run)
        worker = StrategyShadowOutcomeWorker(self.repository, service)
        start = dt.datetime(2028, 1, 1, tzinfo=dt.timezone.utc)
        for index in range(8):
            worker.run_once(now=start + dt.timedelta(days=index))

        blocked = self.repository.get_strategy_shadow_enrollment(run["id"])
        self.assertEqual(len(calls), 8)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["consecutive_failures"], 8)
        self.assertIsNone(blocked["next_run_at"])
        self.assertTrue(service.verify_enrollment(blocked)["verified"])

    def test_new_signal_after_observed_window_can_enroll(self):
        service = self._service()
        first_run = self._run()
        service.ensure_enrollment(first_run)
        StrategyShadowOutcomeWorker(self.repository, service).run_once(
            now="2028-01-01T00:00:00+00:00"
        )

        def later_analysis(payload):
            result = copy.deepcopy(_analysis(payload))
            result["as_of"] = "2027-01-06"
            result["latest"]["unit_nav"] = 1.36
            strategy = result["conditioned_forward"]
            strategy["condition"]["as_of"] = "2027-01-06"
            strategy["condition"]["latest_nav"] = 1.36
            strategy["coverage"]["end_date"] = "2027-01-06"
            return result

        later_run = self._run(analysis_handler=later_analysis)
        later, created = service.ensure_enrollment(later_run)
        self.assertTrue(created)
        self.assertEqual(later["status"], "scheduled")
        self.assertIsNone(later["exclusion_reason"])

    def test_aggregate_metrics_are_hidden_until_sample_and_fund_thresholds(self):
        samples = [
            {
                "fund_code": f"{index % 10:06d}",
                "release_grade": True,
                "directionally_correct": index % 3 != 0,
                "signed_unit_nav_return_pct": 2.0 + index / 10,
                "peer_edge_correct": index % 2 == 0,
            }
            for index in range(30)
        ]
        below = build_shadow_aggregate(
            samples[:29],
            integrity_failures=0,
            scan_complete=True,
        )
        ready = build_shadow_aggregate(
            samples,
            integrity_failures=0,
            scan_complete=True,
        )
        tampered = build_shadow_aggregate(
            samples,
            integrity_failures=1,
            scan_complete=True,
        )

        self.assertFalse(below["aggregate_available"])
        self.assertIsNone(below["metrics"])
        self.assertTrue(ready["aggregate_available"])
        self.assertEqual(ready["metrics"]["sample_count"], 30)
        self.assertIsNotNone(ready["metrics"]["directional_hit_rate_interval"])
        self.assertFalse(tampered["aggregate_available"])
        self.assertIsNone(tampered["metrics"])

    @staticmethod
    def _segmented_samples(market, asset, vehicle, *, start=0):
        key = f"horizon=6m|market={market}|asset={asset}|vehicle={vehicle}"
        return [
            {
                "fund_code": f"{(start + index) % 10:06d}",
                "release_grade": True,
                "directionally_correct": index % 3 != 0,
                "signed_unit_nav_return_pct": 1.0 + index / 10,
                "peer_edge_correct": index % 2 == 0,
                "cohort_integrity_verified": True,
                "cohort": {
                    "dimensions": {
                        "horizon": {"name": "6m", "confirmed_nav_observations": 126},
                        "market": {"primary": market},
                        "asset_class": {"primary": asset},
                        "vehicle": {"type": vehicle},
                    },
                    "keys": {
                        "release_cohort": key,
                        "regime_cohort": f"{key}|trend=above_ma60|drawdown=normal_pullback",
                    },
                    "release_classification": {"eligible": True},
                },
            }
            for index in range(30)
        ]

    def test_single_comparable_cohort_can_disclose_after_threshold(self):
        aggregate = build_segmented_shadow_aggregate(
            self._segmented_samples("mainland", "mixed", "domestic"),
            integrity_failures=0,
            scan_complete=True,
            classification_complete=True,
        )
        self.assertTrue(aggregate["aggregate_available"])
        self.assertEqual(len(aggregate["segments"]), 1)
        self.assertTrue(aggregate["segments"][0]["disclosure_gate"]["aggregate_available"])

    def test_heterogeneous_cohorts_disclose_segments_but_never_pooled_metrics(self):
        samples = [
            *self._segmented_samples("hong_kong", "equity", "qdii"),
            *self._segmented_samples("united_states", "equity", "qdii", start=20),
        ]
        aggregate = build_segmented_shadow_aggregate(
            samples,
            integrity_failures=0,
            scan_complete=True,
            classification_complete=True,
        )
        self.assertFalse(aggregate["aggregate_available"])
        self.assertIsNone(aggregate["metrics"])
        self.assertEqual(len(aggregate["segments"]), 2)
        self.assertTrue(all(
            item["disclosure_gate"]["aggregate_available"]
            for item in aggregate["segments"]
        ))
        self.assertIn("禁止返回混合总体绩效", aggregate["reason"])

    def test_incomplete_cohort_scan_hides_segment_and_pooled_metrics(self):
        aggregate = build_segmented_shadow_aggregate(
            self._segmented_samples("mainland", "mixed", "domestic"),
            integrity_failures=0,
            scan_complete=True,
            classification_complete=False,
        )
        self.assertFalse(aggregate["aggregate_available"])
        self.assertIsNone(aggregate["metrics"])
        self.assertFalse(
            aggregate["segments"][0]["disclosure_gate"]["aggregate_available"]
        )

    def test_legacy_enrollment_can_bind_cohort_without_rewriting_signal_snapshot(self):
        service = self._service()
        run = self._run()
        snapshot = service._signal_snapshot(run)
        strategy = snapshot["strategy"]
        signal = snapshot["signal"]
        fund = snapshot["fund"]
        baseline = snapshot["baseline"]
        enrollment, _ = self.repository.ensure_strategy_shadow_enrollment(
            run["id"],
            strategy_id=strategy["strategy_id"],
            strategy_version=strategy["strategy_version"],
            manifest_sha256=strategy["manifest_sha256"],
            strategy_status=strategy["status_at_signal"],
            governance_evidence_id=strategy["governance_evidence_id"],
            signal_evidence_id=strategy["signal_evidence_id"],
            fund_code=fund["code"],
            fund_name=fund["name"],
            baseline_as_of=baseline["as_of"],
            baseline_nav=baseline["unit_nav"],
            signal_direction=signal["direction"],
            signal_decision=signal["decision"],
            confidence_level=signal["confidence_level"],
            horizon=signal["horizon"],
            observation_days=signal["observation_days"],
            signal_snapshot=snapshot,
            due_at=service._due_at(baseline["as_of"], signal["observation_days"]),
        )
        frozen_hash = enrollment["signal_snapshot_sha256"]
        self.assertIsNone(self.repository.get_strategy_shadow_cohort(enrollment["id"]))

        result = service.backfill_missing_cohorts(limit=10)
        rebound = self.repository.get_strategy_shadow_enrollment(run["id"])
        cohort = self.repository.get_strategy_shadow_cohort(enrollment["id"])
        self.assertEqual(result["created"], 1)
        self.assertEqual(rebound["signal_snapshot_sha256"], frozen_hash)
        self.assertTrue(service.verify_cohort(cohort, rebound)["verified"])

    def test_public_enrollment_does_not_expose_tenant_or_user(self):
        view = StrategyShadowOutcomeService.public_enrollment({
            "id": "shadow-1",
            "run_id": "run-1",
            "tenant_id": "private-tenant",
            "user_id": "private-user",
            "strategy_id": "strategy",
            "strategy_version": "1.0.0",
            "status": "scheduled",
            "last_error_code": "PROVIDER_FAILED",
            "last_error_message": "internal provider topology",
        })
        self.assertNotIn("tenant_id", view)
        self.assertNotIn("user_id", view)
        self.assertNotIn("last_error_message", view)
        self.assertEqual(view["last_error_code"], "PROVIDER_FAILED")
        self.assertEqual(view["status"], "scheduled")

    def test_public_cohort_exposes_dimensions_without_source_payloads(self):
        view = StrategyShadowOutcomeService.public_cohort({
            "id": "cohort-1",
            "enrollment_id": "shadow-1",
            "evidence_id": "ev-cohort",
            "taxonomy_id": "fund_strategy_shadow_cohort",
            "taxonomy_version": "1.0.0",
            "cohort_sha256": "a" * 64,
            "cohort_integrity_verified": True,
            "release_eligible": True,
            "release_cohort_key": "market=mainland",
            "regime_cohort_key": "market=mainland|trend=above_ma60",
            "created_at": "2026-07-13T00:00:00+00:00",
            "cohort": {
                "dimensions": {"market": {"primary": "mainland"}},
                "release_classification": {"eligible": True},
                "source_evidence": {"private_provider_payload": {"token": "secret"}},
            },
            "tenant_id": "private-tenant",
            "user_id": "private-user",
        })
        self.assertNotIn("tenant_id", view)
        self.assertNotIn("user_id", view)
        self.assertNotIn("source_evidence", view)
        self.assertEqual(view["dimensions"]["market"]["primary"], "mainland")

    def test_backfill_cursor_skips_invalid_run_without_blocking_later_runs(self):
        runs = [self._run() for _ in range(3)]
        candidates = self.repository.list_unenrolled_strategy_shadow_runs(limit=10)
        invalid = candidates[0]
        signal_evidence_id = invalid["result"]["strategy"]["evidence_id"]
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE agent_evidence SET payload_json='{}' WHERE id=?",
                (signal_evidence_id,),
            )
            connection.commit()
        finally:
            connection.close()

        created = self._service().backfill_eligible_enrollments(limit=3)
        self.assertEqual(created, 2)
        self.assertIsNone(self.repository.get_strategy_shadow_enrollment(invalid["id"]))
        enrolled_ids = {
            item["run_id"]
            for item in self.repository.list_strategy_shadow_enrollments(
                "fund_conditioned_forward_return",
                "1.0.0",
            )
        }
        self.assertEqual(enrolled_ids, {run["id"] for run in runs} - {invalid["id"]})


if __name__ == "__main__":
    unittest.main()
