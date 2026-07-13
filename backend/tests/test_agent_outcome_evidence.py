# -*- coding: utf-8 -*-
"""Post-run outcome Evidence is immutable, idempotent, and audited."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.repository import AgentRepository  # noqa: E402
from routers import agent as agent_router  # noqa: E402


def _outcome(as_of="2026-02-05"):
    return {
        "evaluator_id": "fund_decision_outcome",
        "evaluator_version": "1.0.0",
        "status": "observing",
        "code": "001480",
        "baseline": {"as_of": "2026-01-31", "unit_nav": 1.0},
        "observed": {
            "as_of": as_of,
            "unit_nav": 1.05,
            "confirmed_nav_count": 5,
            "return_pct": 5.0,
        },
        "milestones": [],
        "interpretation": {"status": "too_early", "label": "观察期不足"},
        "source": "真实确认净值测试快照",
        "source_url": "https://example.test/fund/001480",
        "provider_as_of": as_of,
        "policy": "测试策略不提前宣布成功。",
    }


class AgentOutcomeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")
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
            as_of="2026-01-31",
            quality_status="complete",
        )
        self.repository.finish_run(
            run["id"],
            status="completed",
            result={
                "schema_version": "fund_deep_research.v3",
                "fund": {"code": "001480", "name": "测试基金", "as_of": "2026-01-31", "unit_nav": 1.0},
                "personalized_decision": {"decision": {"action": "consider_tranche"}},
            },
        )
        self.run_id = run["id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_same_snapshot_is_idempotent_and_new_date_appends(self):
        first, first_created = self.repository.add_post_run_evidence(
            self.run_id,
            evidence_type="outcome_observation",
            subject_type="fund",
            subject_id="001480",
            provider="test",
            source_url="https://example.test",
            as_of="2026-02-05",
            schema_version="1.0.0",
            quality_status="complete",
            payload=_outcome(),
        )
        second, second_created = self.repository.add_post_run_evidence(
            self.run_id,
            evidence_type="outcome_observation",
            subject_type="fund",
            subject_id="001480",
            provider="test",
            source_url="https://example.test",
            as_of="2026-02-05",
            schema_version="1.0.0",
            quality_status="complete",
            payload={**_outcome(), "tampered_attempt": True},
        )
        _, third_created = self.repository.add_post_run_evidence(
            self.run_id,
            evidence_type="outcome_observation",
            subject_type="fund",
            subject_id="001480",
            provider="test",
            source_url="https://example.test",
            as_of="2026-02-06",
            schema_version="1.0.0",
            quality_status="complete",
            payload=_outcome("2026-02-06"),
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])
        self.assertNotIn("tampered_attempt", second["payload"])
        self.assertTrue(third_created)
        self.assertEqual(len(self.repository.list_evidence_by_type(self.run_id, "outcome_observation")), 2)
        self.assertTrue(self.repository.verify_run_evidence_integrity(self.run_id)["verified"])
        self.assertTrue(self.repository.verify_audit_chain(self.run_id)["verified"])

    def test_evaluate_route_binds_saved_baseline_and_is_idempotent(self):
        recomputed = {**_outcome(), "recomputed_but_not_persisted": True}
        with (
            patch.object(agent_router, "repository", self.repository),
            patch.object(
                agent_router.DecisionOutcomeService,
                "_invoke_tool",
                side_effect=[_outcome(), recomputed],
            ) as tool,
        ):
            first = agent_router.evaluate_agent_run(self.run_id)
            second = agent_router.evaluate_agent_run(self.run_id)
            listed = agent_router.list_agent_run_evaluations(self.run_id)

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(listed["count"], 1)
        tool.assert_called_with({
            "code": "001480",
            "name": "测试基金",
            "baseline_as_of": "2026-01-31",
            "baseline_nav": 1.0,
            "action": "consider_tranche",
        })
        self.assertTrue(first["evaluation"]["integrity_verified"])
        self.assertEqual(first["evaluation"]["payload_sha256"], second["evaluation"]["payload_sha256"])
        self.assertNotIn("recomputed_but_not_persisted", second["evaluation"])

    def test_partial_peer_quality_is_persisted_on_evidence(self):
        outcome = {
            **_outcome(),
            "evaluator_version": "1.1.0",
            "peer_comparison": {
                "status": "unavailable",
                "reason": "baseline_date_not_in_provider_comparable_series",
            },
            "quality": {"status": "partial"},
        }
        with (
            patch.object(agent_router, "repository", self.repository),
            patch.object(
                agent_router.DecisionOutcomeService,
                "_invoke_tool",
                return_value=outcome,
            ),
        ):
            result = agent_router.evaluate_agent_run(self.run_id)

        evidence = self.repository.get_evidence(
            self.run_id, result["evaluation"]["evidence_id"]
        )
        self.assertEqual(evidence["quality_status"], "partial")
        self.assertTrue(evidence["integrity_verified"])


if __name__ == "__main__":
    unittest.main()
