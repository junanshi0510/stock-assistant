# -*- coding: utf-8 -*-
"""Unified research sources must produce reviewable, non-executable actions."""

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import decision_sources  # noqa: E402


class OpportunityRepo:
    def __init__(self, *, basket=None):
        self.basket = basket

    def list_runs(self, **_kwargs):
        return [{"id": "opp_run_1", "status": "succeeded", "completed_at": "2026-07-22T01:00:00Z"}]

    def get_run(self, *_args, **_kwargs):
        return {
            "id": "opp_run_1",
            "status": "succeeded",
            "result_verified": True,
            "completed_at": "2026-07-22T01:00:00Z",
            "result": {
                "funnel": {"evaluated": 18, "qualified": 2, "unavailable": 0},
                "portfolio": {"positions": [{"symbol": "AAA"}, {"symbol": "BBB"}]},
            },
        }

    def list_paper_baskets(self, **_kwargs):
        return [self.basket] if self.basket else []


class AgentRepo:
    def list_runs(self, **_kwargs):
        return ([{
            "id": "agent_run_1",
            "status": "completed",
            "completed_at": "2026-07-22T02:00:00Z",
            "input": {"code": "110022"},
            "result": {
                "fund": {"code": "110022"},
                "conclusion": {"status": "research_ready", "headline": "证据支持继续观察"},
                "personalized_decision": {"decision": {"action": "watch"}},
            },
        }], False)

    def verify_run_evidence_integrity(self, _run_id):
        return {"verified": True, "evidence_count": 8}


class TwinRepo:
    def list_runs(self, **_kwargs):
        return [{"id": "twin_run_1"}]

    def get_run(self, *_args, **_kwargs):
        return {
            "id": "twin_run_1",
            "status": "complete",
            "created_at": "2026-07-22T03:00:00Z",
            "integrity": {"verified": True},
            "result": {
                "current": {
                    "risk_budget": {
                        "breached": True,
                        "utilization_pct": 132.4,
                        "worst_loss_amount": 13240,
                    }
                },
                "repair_plan": {"total_shift_to_cash": 4200},
                "decision_gate": {"decision_eligible": True, "reasons": []},
            },
        }


class BrokenRepo:
    def list_runs(self, **_kwargs):
        raise RuntimeError("database unavailable")


class DecisionSourceTests(unittest.TestCase):
    def test_sources_converge_into_one_non_executable_queue(self):
        result = decision_sources.build_research_snapshot(
            user_id="owner",
            opportunity_repo=OpportunityRepo(),
            agent_repo=AgentRepo(),
            twin_repo=TwinRepo(),
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["summary"]["ready_source_count"], 3)
        action_ids = {item["id"] for item in result["actions"]}
        self.assertIn("opportunity-freeze-opp_run_1", action_ids)
        self.assertIn("agent-review-agent_run_1", action_ids)
        self.assertIn("twin-risk-budget-twin_run_1", action_ids)
        self.assertTrue(all(item["execution_authorized"] is False for item in result["actions"]))
        self.assertTrue(all(item.get("evidence_status") for item in result["actions"]))
        self.assertTrue(all(item.get("validation_state") for item in result["actions"]))

    def test_paper_observation_closes_forward_validation_gap(self):
        basket = {
            "id": "basket_1",
            "run_id": "opp_run_1",
            "latest_observation": {
                "id": "obs_1",
                "payload_verified": True,
                "payload": {"status": "complete", "failed_count": 0},
            },
        }
        result = decision_sources.build_research_snapshot(
            user_id="owner",
            opportunity_repo=OpportunityRepo(basket=basket),
            agent_repo=AgentRepo(),
            twin_repo=TwinRepo(),
        )

        opportunity = next(item for item in result["sources"] if item["id"] == "opportunity")
        self.assertEqual(opportunity["validation_state"], "paper_tracking")
        self.assertEqual(result["summary"]["paper_tracking_count"], 1)
        self.assertNotIn(
            "opportunity-freeze-opp_run_1",
            {item["id"] for item in result["actions"]},
        )

    def test_partial_paper_observation_does_not_close_validation_gap(self):
        basket = {
            "id": "basket_1",
            "run_id": "opp_run_1",
            "latest_observation": {
                "id": "obs_partial",
                "payload_verified": True,
                "payload": {
                    "status": "partial",
                    "failed_count": 1,
                    "covered_position_weight_pct": 55,
                },
            },
        }
        result = decision_sources.build_research_snapshot(
            user_id="owner",
            opportunity_repo=OpportunityRepo(basket=basket),
            agent_repo=AgentRepo(),
            twin_repo=TwinRepo(),
        )

        opportunity = next(item for item in result["sources"] if item["id"] == "opportunity")
        self.assertEqual(opportunity["validation_state"], "paper_incomplete")
        self.assertEqual(result["summary"]["paper_tracking_count"], 0)
        self.assertEqual(result["summary"]["paper_pending_count"], 1)
        self.assertIn(
            "opportunity-observation-gap-obs_partial",
            {item["id"] for item in result["actions"]},
        )

    def test_one_repository_failure_is_explicit_and_defers_resolution(self):
        result = decision_sources.build_research_snapshot(
            user_id="owner",
            opportunity_repo=BrokenRepo(),
            agent_repo=AgentRepo(),
            twin_repo=TwinRepo(),
        )

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["resolution_evidence_complete"])
        self.assertEqual(result["summary"]["unavailable_source_count"], 1)
        failed = next(item for item in result["sources"] if item["id"] == "opportunity")
        self.assertEqual(failed["status"], "unavailable")
        self.assertIn("database unavailable", failed["error"])


if __name__ == "__main__":
    unittest.main()
