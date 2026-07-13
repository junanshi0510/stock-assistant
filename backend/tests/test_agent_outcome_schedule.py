# -*- coding: utf-8 -*-
"""Durable outcome schedules use leases, retries, and explicit eligibility."""

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.outcome_worker import OutcomeScheduleWorker  # noqa: E402
from agent.outcomes import DecisionOutcomeService  # noqa: E402
from agent.registry import ToolRegistry  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from agent.worker import AgentWorker  # noqa: E402
from routers import agent as agent_router  # noqa: E402


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 13, 2, 0, tzinfo=UTC)


class AgentOutcomeScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")
        self.run_id = self._create_terminal_run("wait")
        self.service = DecisionOutcomeService(self.repository, ToolRegistry())

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_terminal_run(self, action):
        run, _ = self.repository.create_run(
            "fund_deep_research",
            {"code": "001480", "months": 60},
        )
        step = self.repository.start_step(
            run["id"],
            step_key="fund_analysis",
            sequence_no=1,
            tool_name="fund.analysis.get",
            tool_version="1.0.0",
            required=True,
            input_payload={"code": "001480"},
        )
        self.repository.complete_step_with_evidence(
            run["id"],
            step["id"],
            status="succeeded",
            payload={"code": "001480", "latest": {"unit_nav": 1.0}},
            evidence_type="calculation",
            subject_type="fund",
            subject_id="001480",
            provider="test",
            source_url="https://example.test",
            as_of="2026-07-10",
            quality_status="complete",
        )
        self.repository.finish_run(
            run["id"],
            status="completed",
            result={
                "schema_version": "fund_deep_research.v3",
                "fund": {
                    "code": "001480",
                    "name": "测试基金",
                    "as_of": "2026-07-10",
                    "unit_nav": 1.0,
                },
                "personalized_decision": {"decision": {"action": action}},
            },
        )
        return run["id"]

    def _outcome_evidence(self):
        return self.repository.add_post_run_evidence(
            self.run_id,
            evidence_type="outcome_observation",
            subject_type="fund",
            subject_id="001480",
            provider="test",
            source_url="https://example.test",
            as_of="2026-07-11",
            schema_version="1.0.0",
            quality_status="complete",
            payload={"provider_as_of": "2026-07-11", "status": "observing"},
        )[0]

    def test_auto_schedule_is_only_for_directional_decisions_and_respects_user_pause(self):
        schedule, created = self.service.ensure_schedule_for_run(
            self.repository.get_run(self.run_id),
            interval_hours=24,
        )
        self.assertTrue(created)
        self.assertEqual(schedule["status"], "active")

        paused, changed = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=False,
            interval_hours=24,
            now=NOW,
        )
        self.assertTrue(changed)
        self.assertEqual(paused["status"], "paused")

        preserved, created_again = self.service.ensure_schedule_for_run(
            self.repository.get_run(self.run_id),
            interval_hours=24,
        )
        self.assertFalse(created_again)
        self.assertEqual(preserved["status"], "paused")

        non_directional_id = self._create_terminal_run("setup_required")
        missing, non_directional_created = self.service.ensure_schedule_for_run(
            self.repository.get_run(non_directional_id)
        )
        self.assertIsNone(missing)
        self.assertFalse(non_directional_created)
        self.assertEqual(
            self.service.eligibility(self.repository.get_run(non_directional_id))["reason"],
            "decision_not_directional",
        )
        self.assertTrue(self.repository.verify_audit_chain(self.run_id)["verified"])

    def test_expired_lease_can_be_reclaimed_and_stale_worker_is_fenced(self):
        schedule, _ = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now=NOW,
        )
        first = self.repository.claim_due_outcome_schedule("worker-1", now=NOW)
        self.assertEqual(first["id"], schedule["id"])
        self.assertIsNone(self.repository.claim_due_outcome_schedule("worker-2", now=NOW))

        after_expiry = NOW + dt.timedelta(seconds=121)
        second = self.repository.claim_due_outcome_schedule("worker-2", now=after_expiry)
        self.assertEqual(second["id"], schedule["id"])
        evidence = self._outcome_evidence()
        with self.assertRaisesRegex(RuntimeError, "租约已失效"):
            self.repository.complete_outcome_schedule(
                schedule["id"],
                "worker-1",
                provider_as_of="2026-07-11",
                evidence_id=evidence["id"],
                evidence_created=True,
                now=after_expiry,
            )
        completed = self.repository.complete_outcome_schedule(
            schedule["id"],
            "worker-2",
            provider_as_of="2026-07-11",
            evidence_id=evidence["id"],
            evidence_created=True,
            now=after_expiry,
        )
        self.assertIsNone(completed["lease_owner"])
        self.assertEqual(completed["consecutive_failures"], 0)
        self.assertEqual(completed["last_evidence_id"], evidence["id"])

    def test_retryable_failure_uses_backoff_and_nonretryable_failure_pauses(self):
        schedule, _ = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now=NOW,
        )
        self.repository.claim_due_outcome_schedule("worker-1", now=NOW)
        retried = self.repository.fail_outcome_schedule(
            schedule["id"],
            "worker-1",
            error_code="PROVIDER_TIMEOUT",
            error_message="timeout",
            retryable=True,
            now=NOW,
        )
        self.assertEqual(retried["status"], "active")
        self.assertEqual(retried["consecutive_failures"], 1)
        self.assertEqual(
            retried["next_run_at"],
            (NOW + dt.timedelta(minutes=15)).isoformat(timespec="milliseconds"),
        )
        self.assertIsNone(
            self.repository.claim_due_outcome_schedule(
                "worker-2", now=NOW + dt.timedelta(minutes=14)
            )
        )
        self.repository.claim_due_outcome_schedule(
            "worker-2", now=NOW + dt.timedelta(minutes=15)
        )
        paused = self.repository.fail_outcome_schedule(
            schedule["id"],
            "worker-2",
            error_code="SOURCE_INTEGRITY_FAILED",
            error_message="invalid evidence",
            retryable=False,
            now=NOW + dt.timedelta(minutes=15),
        )
        self.assertEqual(paused["status"], "paused")
        self.assertIsNone(paused["next_run_at"])
        self.assertEqual(paused["consecutive_failures"], 2)

    def test_retry_budget_pauses_after_five_consecutive_failures(self):
        schedule, _ = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now=NOW,
        )
        current = NOW
        failed = None
        for attempt in range(1, 6):
            claimed = self.repository.claim_due_outcome_schedule(
                f"worker-{attempt}",
                now=current,
            )
            self.assertIsNotNone(claimed)
            failed = self.repository.fail_outcome_schedule(
                schedule["id"],
                f"worker-{attempt}",
                error_code="OUTCOME_PROVIDER_FAILED",
                error_message="provider unavailable",
                retryable=True,
                now=current,
            )
            if failed["next_run_at"]:
                current = dt.datetime.fromisoformat(failed["next_run_at"])

        self.assertEqual(failed["status"], "paused")
        self.assertIsNone(failed["next_run_at"])
        self.assertEqual(failed["consecutive_failures"], 5)

        resumed, changed = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now=current,
        )
        self.assertTrue(changed)
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(resumed["consecutive_failures"], 0)
        self.assertIsNone(resumed["last_error_code"])

    def test_active_configuration_does_not_release_an_inflight_lease(self):
        schedule, _ = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now=NOW,
        )
        claimed = self.repository.claim_due_outcome_schedule("worker-1", now=NOW)
        updated, changed = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=48,
            run_immediately=True,
            now=NOW,
        )

        self.assertTrue(changed)
        self.assertEqual(updated["lease_owner"], "worker-1")
        self.assertEqual(updated["lease_expires_at"], claimed["lease_expires_at"])
        self.assertIsNone(self.repository.claim_due_outcome_schedule("worker-2", now=NOW))

    def test_worker_commits_persisted_evidence_metadata(self):
        schedule, _ = self.repository.configure_outcome_schedule(
            self.run_id,
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now=NOW,
        )
        evidence = self._outcome_evidence()
        service = Mock()
        service.evaluate_run.return_value = {
            "created": True,
            "evaluation": {
                "provider_as_of": "2026-07-11",
                "evidence_id": evidence["id"],
            },
        }
        worker = OutcomeScheduleWorker(
            self.repository,
            service,
            poll_interval=30,
            lease_seconds=120,
        )

        self.assertTrue(worker.run_once(now=NOW))
        updated = self.repository.get_outcome_schedule(self.run_id)
        self.assertEqual(updated["id"], schedule["id"])
        self.assertEqual(updated["last_provider_as_of"], "2026-07-11")
        self.assertEqual(updated["last_evidence_id"], evidence["id"])
        self.assertEqual(updated["attempt_count"], 1)
        self.assertTrue(self.repository.verify_audit_chain(self.run_id)["verified"])

    def test_schedule_api_reports_eligibility_and_persists_user_controls(self):
        with (
            patch.object(agent_router, "repository", self.repository),
            patch.object(agent_router, "start_worker") as start_worker,
        ):
            initial = agent_router.get_agent_run_outcome_schedule(self.run_id)
            enabled = agent_router.configure_agent_run_outcome_schedule(
                self.run_id,
                agent_router.OutcomeScheduleRequest(
                    enabled=True,
                    interval_hours=48,
                    run_immediately=True,
                ),
            )
            disabled = agent_router.configure_agent_run_outcome_schedule(
                self.run_id,
                agent_router.OutcomeScheduleRequest(
                    enabled=False,
                    interval_hours=48,
                ),
            )

        self.assertTrue(initial["eligibility"]["eligible"])
        self.assertIsNone(initial["schedule"])
        self.assertEqual(enabled["schedule"]["status"], "active")
        self.assertEqual(enabled["schedule"]["interval_hours"], 48)
        self.assertEqual(disabled["schedule"]["status"], "paused")
        self.assertNotIn("lease_owner", enabled["schedule"])
        self.assertNotIn("tenant_id", enabled["schedule"])
        start_worker.assert_called_once_with()

    def test_agent_worker_auto_enrolls_new_actionable_terminal_run(self):
        queued, _ = self.repository.create_run(
            "fund_deep_research",
            {"code": "001480", "months": 60, "new": True},
        )
        runner = Mock()

        def finish(_claimed):
            step = self.repository.start_step(
                queued["id"],
                step_key="fund_analysis",
                sequence_no=1,
                tool_name="fund.analysis.get",
                tool_version="1.0.0",
                required=True,
                input_payload={"code": "001480"},
            )
            self.repository.complete_step_with_evidence(
                queued["id"],
                step["id"],
                status="succeeded",
                payload={"code": "001480", "latest": {"unit_nav": 1.0}},
                evidence_type="calculation",
                subject_type="fund",
                subject_id="001480",
                provider="test",
                source_url="https://example.test",
                as_of="2026-07-10",
                quality_status="complete",
            )
            return self.repository.finish_run(
                queued["id"],
                status="completed",
                result={
                    "schema_version": "fund_deep_research.v3",
                    "fund": {
                        "code": "001480",
                        "name": "测试基金",
                        "as_of": "2026-07-10",
                        "unit_nav": 1.0,
                    },
                    "personalized_decision": {"decision": {"action": "consider_tranche"}},
                },
            )

        runner.execute.side_effect = finish
        worker = AgentWorker(
            self.repository,
            runner,
            terminal_callback=lambda finished: self.service.ensure_schedule_for_run(finished),
        )

        self.assertTrue(worker.run_once())
        schedule = self.repository.get_outcome_schedule(queued["id"])
        self.assertEqual(schedule["status"], "active")
        self.assertEqual(schedule["interval_hours"], 24)

    def test_backfill_is_idempotent_and_skips_non_directional_history(self):
        non_directional_id = self._create_terminal_run("setup_required")

        self.assertEqual(self.service.backfill_eligible_schedules(limit=100), 1)
        self.assertEqual(self.service.backfill_eligible_schedules(limit=100), 0)
        self.assertIsNotNone(self.repository.get_outcome_schedule(self.run_id))
        self.assertIsNone(self.repository.get_outcome_schedule(non_directional_id))


if __name__ == "__main__":
    unittest.main()
