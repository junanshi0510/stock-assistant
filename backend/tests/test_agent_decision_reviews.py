# -*- coding: utf-8 -*-
"""Decision review queue joins immutable user intent with real NAV Evidence."""

import datetime as dt
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.decision_reviews import DecisionReviewService  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from routers import agent as agent_router  # noqa: E402


class AgentDecisionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")
        self.service = DecisionReviewService(self.repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, code: str, *, user_id: str = "anonymous") -> dict:
        run, _ = self.repository.create_run(
            "fund_deep_research",
            {"code": code, "months": 60},
            tenant_id="public",
            user_id=user_id,
        )
        step = self.repository.start_step(
            run["id"],
            step_key="fund_analysis",
            sequence_no=1,
            tool_name="fund.analysis.get",
            tool_version="1.0.0",
            required=True,
            input_payload={"code": code},
        )
        self.repository.complete_step_with_evidence(
            run["id"],
            step["id"],
            status="succeeded",
            payload={"code": code, "latest": {"unit_nav": 1.0}},
            evidence_type="calculation",
            subject_type="fund",
            subject_id=code,
            provider="test-confirmed-nav",
            source_url=f"https://example.test/fund/{code}",
            as_of="2026-01-31",
            quality_status="complete",
        )
        self.repository.finish_run(
            run["id"],
            status="completed",
            result={
                "schema_version": "fund_deep_research.v6",
                "fund": {
                    "code": code,
                    "name": f"测试基金{code}",
                    "as_of": "2026-01-31",
                    "unit_nav": 1.0,
                },
                "conclusion": {
                    "headline": "保持观察并按证据复盘",
                    "risk_band": "中风险",
                    "timing_label": "等待确认",
                },
                "personalized_decision": {
                    "decision": {"action": "consider_tranche"}
                },
            },
        )
        return self.repository.get_run(run["id"], include_details=False)

    def _feedback(
        self,
        run: dict,
        planned_review_at: str | None,
        *,
        expected_previous_hash: str | None = None,
    ) -> dict:
        event, _ = self.repository.append_run_feedback(
            run["id"],
            user_id=str(run["user_id"]),
            actor_id=str(run["user_id"]),
            feedback_verdict="helpful",
            user_decision="observe",
            reason_codes=["evidence_clear"],
            note="等待真实确认净值后复盘",
            planned_review_at=planned_review_at,
            expected_previous_hash=expected_previous_hash,
        )
        return event

    def _add_outcome(
        self,
        run: dict,
        as_of: str,
        return_pct: float = 3.5,
        *,
        provider_as_of: str | None = None,
    ) -> dict:
        provider_date = provider_as_of or as_of
        payload = {
            "evaluator_id": "fund_decision_outcome",
            "evaluator_version": "1.1.0",
            "status": "observing",
            "code": run["result"]["fund"]["code"],
            "baseline": {"as_of": "2026-01-31", "unit_nav": 1.0},
            "observed": {
                "as_of": as_of,
                "unit_nav": 1 + return_pct / 100,
                "confirmed_nav_count": 8,
                "return_pct": return_pct,
            },
            "peer_comparison": {
                "status": "available",
                "period_return_pct": 2.0,
                "relative_excess_return_pct": 1.47,
            },
            "interpretation": {
                "status": "too_early",
                "label": "仍处于观察期",
            },
            "source": "真实确认净值测试源",
            "provider_as_of": provider_date,
        }
        evidence, _ = self.repository.add_post_run_evidence(
            run["id"],
            evidence_type="outcome_observation",
            subject_type="fund",
            subject_id=run["result"]["fund"]["code"],
            provider="test-confirmed-nav",
            source_url="https://example.test/fund/outcome",
            as_of=provider_date,
            schema_version="1.1.0",
            quality_status="complete",
            payload=payload,
        )
        return evidence

    def test_statuses_require_outcome_on_or_after_planned_review_date(self):
        blocked = self._run("000001")
        due = self._run("000002")
        early = self._run("000003")
        ready = self._run("000004")
        upcoming = self._run("000005")
        unscheduled = self._run("000006")

        self._feedback(blocked, "2026-02-01")
        self._feedback(due, "2026-02-01")
        self._feedback(early, "2026-02-10")
        self._feedback(ready, "2026-02-10")
        self._feedback(upcoming, "2026-02-20")
        self._feedback(unscheduled, None)
        self._add_outcome(
            early,
            "2026-02-09",
            return_pct=-1.25,
            provider_as_of="2026-02-11",
        )
        self._add_outcome(ready, "2026-02-10", return_pct=3.5)

        connection = sqlite3.connect(self.repository.db_path)
        try:
            connection.execute(
                "UPDATE agent_evidence SET payload_json='{}' WHERE run_id=?",
                (blocked["id"],),
            )
            connection.commit()
        finally:
            connection.close()

        result = self.service.list_reviews(
            tenant_id="public",
            user_id="anonymous",
            limit=20,
            as_of=dt.date(2026, 2, 10),
        )

        by_code = {item["run"]["code"]: item for item in result["items"]}
        self.assertEqual(by_code["000001"]["status"], "blocked")
        self.assertEqual(by_code["000002"]["status"], "due")
        self.assertEqual(by_code["000003"]["status"], "due")
        self.assertEqual(
            by_code["000003"]["current_outcome"]["provider_as_of"],
            "2026-02-11",
        )
        self.assertEqual(
            by_code["000003"]["current_outcome"]["observed_as_of"],
            "2026-02-09",
        )
        self.assertEqual(by_code["000004"]["status"], "ready")
        self.assertEqual(by_code["000005"]["status"], "upcoming")
        self.assertEqual(by_code["000006"]["status"], "unscheduled")
        self.assertEqual(result["counts"], {
            "blocked": 1,
            "due": 2,
            "ready": 1,
            "upcoming": 1,
            "unscheduled": 1,
        })
        self.assertEqual(
            [item["status"] for item in result["items"]],
            ["blocked", "due", "due", "ready", "upcoming", "unscheduled"],
        )
        self.assertIsNone(by_code["000001"]["current_outcome"])
        self.assertEqual(
            by_code["000004"]["current_outcome"]["measurement"],
            "fund_confirmed_nav_change_since_run_baseline",
        )
        self.assertFalse(
            by_code["000004"]["current_outcome"]["user_execution_inferred"]
        )
        self.assertFalse(
            by_code["000004"]["current_outcome"]["personal_pnl_inferred"]
        )

    def test_latest_journal_revision_wins_and_user_scope_is_strict(self):
        owned = self._run("100001", user_id="user-a")
        first = self._feedback(owned, "2026-02-01")
        revised = self._feedback(
            owned,
            "2026-02-20",
            expected_previous_hash=first["event_hash"],
        )
        other = self._run("100002", user_id="user-b")
        self._feedback(other, "2026-02-01")

        result = self.service.list_reviews(
            tenant_id="public",
            user_id="user-a",
            limit=20,
            as_of=dt.date(2026, 2, 10),
        )

        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(result["items"][0]["run_id"], owned["id"])
        self.assertEqual(result["items"][0]["status"], "upcoming")
        self.assertEqual(result["items"][0]["feedback"]["id"], revised["id"])
        self.assertEqual(result["items"][0]["feedback"]["sequence_no"], 2)

    def test_status_filter_limit_and_public_api_do_not_expose_identity_columns(self):
        due = self._run("200001")
        ready = self._run("200002")
        self._feedback(due, "2026-02-01")
        self._feedback(ready, "2026-02-01")
        self._add_outcome(ready, "2026-02-02")

        filtered = self.service.list_reviews(
            tenant_id="public",
            user_id="anonymous",
            limit=1,
            status="ready",
            as_of=dt.date(2026, 2, 10),
        )
        self.assertEqual(filtered["count"], 1)
        self.assertEqual(filtered["filtered_total"], 1)
        self.assertEqual(filtered["items"][0]["run_id"], ready["id"])

        attention = self.service.list_reviews(
            tenant_id="public",
            user_id="anonymous",
            limit=20,
            status="attention",
            as_of=dt.date(2026, 2, 10),
        )
        self.assertEqual(attention["filter"], "attention")
        self.assertEqual(
            {item["run_id"] for item in attention["items"]},
            {due["id"], ready["id"]},
        )

        with patch.object(agent_router, "repository", self.repository):
            response = agent_router.list_agent_decision_reviews(
                limit=20,
                review_status=None,
            )
        serialized = json.dumps(response, ensure_ascii=False)
        self.assertNotIn('"tenant_id"', serialized)
        self.assertNotIn('"user_id"', serialized)
        self.assertNotIn('"actor_id"', serialized)
        self.assertEqual(response["total_candidates"], 2)


if __name__ == "__main__":
    unittest.main()
