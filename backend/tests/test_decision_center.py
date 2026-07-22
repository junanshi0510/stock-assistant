# -*- coding: utf-8 -*-
"""Decision-center rules must remain deterministic when providers are mocked."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import decision_center  # noqa: E402


class DecisionCenterTests(unittest.TestCase):
    @staticmethod
    def _current_valuation(holding_count: int) -> dict:
        return {
            "status": "available",
            "snapshot": {
                "id": "valuation-current",
                "payload": {
                    "coverage": {
                        "valued_count": holding_count,
                        "count_coverage_pct": 100,
                        "stale_count": 0,
                        "automatic_value_pct": 100,
                        "professional_value_pct": 100,
                    },
                },
            },
            "binding": {"current": True},
            "runtime_gate": {
                "risk_analysis_eligible": True,
                "trade_amount_eligible": True,
                "execution_authorized": False,
                "reasons": [],
            },
        }

    def setUp(self):
        def fake_sync(actions, **kwargs):
            items = [
                {
                    "id": f"task-{item['id']}",
                    "action_key": item["id"],
                    "revision": 1,
                    "status": "open",
                    "priority": item["priority"],
                    "first_seen_at": "2026-07-15T00:00:00+00:00",
                    "last_seen_at": "2026-07-15T00:00:00+00:00",
                }
                for item in actions
                if item["id"] != "no-high-priority-item"
            ]
            return {
                "status": "available",
                "generated_at": "2026-07-15T00:00:00+00:00",
                "items": items,
                "summary": {
                    "open_count": len(items),
                    "snoozed_count": 0,
                    "acknowledged_count": 0,
                    "resolved_count": 0,
                    "active_count": len(items),
                },
                "resolution_deferred": not kwargs.get("resolve_absent", True),
            }

        self.task_sync = patch.object(
            decision_center.storage,
            "sync_decision_tasks",
            side_effect=fake_sync,
        )
        self.research_snapshot = patch.object(
            decision_center.decision_sources,
            "build_research_snapshot",
            return_value={
                "status": "available",
                "sources": [],
                "actions": [],
                "errors": [],
                "resolution_evidence_complete": True,
                "summary": {
                    "ready_source_count": 0,
                    "paper_tracking_count": 0,
                    "paper_pending_count": 0,
                },
            },
        )
        self.task_sync.start()
        self.research_snapshot.start()

    def tearDown(self):
        self.research_snapshot.stop()
        self.task_sync.stop()

    def test_workflow_exposes_one_ordered_next_action(self):
        profile = {"configured": False, "review_required": False}
        portfolio = {
            "status": "available",
            "summary": {"holding_count": 2},
            "allocation": [{"amount": 600}, {"amount": 400}],
            "ledger_summary": {"transaction_count": 0},
            "performance": {"status": "unavailable"},
            "valuation": self._current_valuation(2),
        }
        with patch.object(decision_center.holding_thesis, "list_with_coverage", return_value={
            "coverage": {"active_thesis_count": 0, "verified_thesis_count": 0},
        }), patch.object(decision_center.portfolio_action_report, "load_latest_action_report", return_value=None):
            workflow = decision_center._decision_workflow(profile, portfolio, {
                "status": "available",
                "sources": [{"id": "agent"}],
                "summary": {"ready_source_count": 1},
            })

        states = {item["id"]: item["state"] for item in workflow["stages"]}
        self.assertEqual(states["holdings"], "complete")
        self.assertEqual(states["policy"], "incomplete")
        self.assertEqual(states["theses"], "incomplete")
        self.assertEqual(states["research"], "complete")
        self.assertEqual(workflow["next_action"]["id"], "policy")
        self.assertEqual(workflow["progress_pct"], 60)

    def test_workflow_is_ready_only_when_every_evidence_gate_is_current(self):
        profile = {
            "configured": True,
            "review_required": False,
            "integrity_verified": True,
            "version_no": 3,
        }
        portfolio = {
            "status": "available",
            "summary": {"holding_count": 2},
            "allocation": [{"amount": 600}, {"amount": 400}],
            "ledger_summary": {"transaction_count": 4},
            "performance": {"status": "available"},
            "valuation": self._current_valuation(2),
        }
        with patch.object(decision_center.holding_thesis, "list_with_coverage", return_value={
            "coverage": {"active_thesis_count": 2, "verified_thesis_count": 2},
        }), patch.object(decision_center.portfolio_action_report, "load_latest_action_report", return_value={
            "status": "reviewable",
            "binding": {"current": True},
            "integrity": {"verified": True},
        }):
            workflow = decision_center._decision_workflow(profile, portfolio, {
                "status": "available",
                "sources": [{"id": "agent"}, {"id": "opportunity"}, {"id": "twin"}],
                "summary": {
                    "ready_source_count": 2,
                    "paper_tracking_count": 1,
                    "paper_pending_count": 0,
                },
            })

        self.assertTrue(workflow["decision_ready"])
        self.assertTrue(workflow["validation_ready"])
        self.assertTrue(workflow["measurement_ready"])
        self.assertEqual(workflow["completed_count"], 5)
        self.assertEqual(workflow["progress_pct"], 100)
        self.assertIsNone(workflow["next_action"])

    def test_missing_transaction_history_does_not_block_research_decision(self):
        profile = {
            "configured": True,
            "review_required": False,
            "integrity_verified": True,
            "version_no": 1,
        }
        portfolio = {
            "status": "available",
            "summary": {"holding_count": 1},
            "allocation": [{"amount": 1000}],
            "ledger_summary": {"transaction_count": 0},
            "performance": {"status": "unavailable"},
            "valuation": self._current_valuation(1),
        }
        research = {
            "status": "available",
            "sources": [{"id": "agent"}],
            "summary": {"ready_source_count": 1},
        }
        with patch.object(decision_center.holding_thesis, "list_with_coverage", return_value={
            "coverage": {"active_thesis_count": 1, "verified_thesis_count": 1},
        }):
            workflow = decision_center._decision_workflow(profile, portfolio, research)

        states = {item["id"]: item["state"] for item in workflow["stages"]}
        self.assertTrue(workflow["decision_ready"])
        self.assertEqual(states["measurement"], "optional")
        self.assertFalse(workflow["measurement_ready"])
        self.assertIsNone(workflow["next_action"])

    def test_partial_paper_observation_keeps_validation_incomplete(self):
        profile = {
            "configured": True,
            "review_required": False,
            "integrity_verified": True,
            "version_no": 1,
        }
        portfolio = {
            "status": "available",
            "summary": {"holding_count": 1},
            "allocation": [{"amount": 1000}],
            "ledger_summary": {"transaction_count": 0},
            "performance": {"status": "unavailable"},
            "valuation": self._current_valuation(1),
        }
        research = {
            "status": "available",
            "sources": [{"id": "opportunity"}],
            "summary": {
                "ready_source_count": 1,
                "paper_tracking_count": 0,
                "paper_pending_count": 1,
            },
        }
        with patch.object(decision_center.holding_thesis, "list_with_coverage", return_value={
            "coverage": {"active_thesis_count": 1, "verified_thesis_count": 1},
        }):
            workflow = decision_center._decision_workflow(profile, portfolio, research)

        states = {item["id"]: item["state"] for item in workflow["stages"]}
        self.assertTrue(workflow["decision_ready"])
        self.assertEqual(states["validation"], "incomplete")
        self.assertFalse(workflow["validation_ready"])
        self.assertEqual(workflow["next_action"]["id"], "validation")

    def test_real_portfolio_evidence_creates_review_queue(self):
        portfolio = {
            "source": "confirmed holdings / real fund NAV",
            "summary": {
                "holding_count": 2,
                "total_amount": 10000,
                "total_profit": -600,
                "top1_ratio": 48,
                "top3_ratio": 100,
                "concentration_level": "high",
            },
            "allocation": [
                {"code": "110022", "name": "Fund A", "amount": 4800, "profit": -500},
                {"code": "001480", "name": "Fund B", "amount": 5200, "profit": -100},
            ],
            "fund_trends": [
                {
                    "code": "110022",
                    "name": "Fund A",
                    "holding_ratio": 48,
                    "current_drawdown": -15.4,
                    "return_3m": -11.2,
                    "source": "real fund NAV",
                    "as_of": "2026-07-10",
                }
            ],
            "fund_errors": [],
            "overlap": {
                "summary": {
                    "high_overlap_pair_count": 1,
                    "avg_stock_overlap_weight": 28.6,
                }
            },
            "overlap_error": None,
            "notes": [],
        }
        market = {
            "as_of": "2026-07-10",
            "summary": {"top_industry": {"name": "Semiconductor", "change_pct": 3.5}},
            "risks": [{"title": "Volatility elevated", "text": "Use lower size until volatility normalizes."}],
            "fund_candidates": [],
            "failed": [],
            "method": {},
        }
        profile = {
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": None,
            "max_single_ratio": 35,
            "configured": True,
            "updated_at": "2026-07-10T10:00:00",
        }

        with patch.object(decision_center.storage, "get_investment_profile", return_value=profile), \
             patch.object(decision_center.holdings_mod, "holdings_insights", return_value=portfolio), \
             patch.object(decision_center.portfolio_valuation, "latest_portfolio_valuation", return_value=self._current_valuation(2)), \
             patch.object(decision_center.portfolio_valuation, "overlay_insights_with_valuation", side_effect=lambda data, valuation: data), \
             patch.object(decision_center.market_daily_mod, "get_market_daily", return_value=market), \
             patch.object(decision_center.portfolio_review, "ledger_overview", return_value={"summary": {"transaction_count": 2}, "integrity_issues": []}), \
             patch.object(decision_center.portfolio_review, "rebalance_review", return_value={"allocations": []}), \
             patch.object(decision_center.portfolio_review, "cashflow_performance", return_value={"status": "available", "summary": {}, "reasons": []}):
            result = decision_center.build_decision_center()

        action_ids = {item["id"] for item in result["actions"]}
        self.assertIn("single-position-limit", action_ids)
        self.assertIn("review-loss-contribution", action_ids)
        self.assertIn("fund-drawdown-110022", action_ids)
        self.assertIn("fund-overlap", action_ids)
        self.assertIn("market-risk-review", action_ids)
        self.assertEqual(result["portfolio"]["status"], "available")
        self.assertEqual(result["market"]["status"], "available")
        self.assertEqual(result["task_inbox"]["status"], "available")
        self.assertFalse(result["task_inbox"]["resolution_deferred"])
        self.assertTrue(all(item.get("task") for item in result["actions"]))

    def test_unavailable_market_is_explicit_and_not_replaced(self):
        portfolio = {
            "source": "confirmed holdings",
            "summary": {"holding_count": 0, "total_amount": None},
            "allocation": [],
            "fund_trends": [],
            "fund_errors": [],
            "overlap": None,
            "overlap_error": None,
            "notes": [],
        }
        profile = {
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": None,
            "max_single_ratio": 35,
            "configured": False,
            "updated_at": None,
        }

        with patch.object(decision_center.storage, "get_investment_profile", return_value=profile), \
             patch.object(decision_center.holdings_mod, "holdings_insights", return_value=portfolio), \
             patch.object(decision_center.market_daily_mod, "get_market_daily", side_effect=RuntimeError("provider down")), \
             patch.object(decision_center.portfolio_review, "ledger_overview", return_value={"summary": {}, "integrity_issues": []}), \
             patch.object(decision_center.portfolio_review, "rebalance_review", return_value={"allocations": []}), \
             patch.object(decision_center.portfolio_review, "cashflow_performance", return_value={"status": "unavailable", "summary": {}, "reasons": []}):
            result = decision_center.build_decision_center()

        action_ids = {item["id"] for item in result["actions"]}
        self.assertIn("import-holdings", action_ids)
        self.assertIn("market-source-unavailable", action_ids)
        self.assertEqual(result["market"]["status"], "unavailable")
        self.assertEqual(result["market"]["error"], "provider down")
        self.assertEqual(result["summary"]["unavailable_count"], 1)
        self.assertTrue(result["task_inbox"]["resolution_deferred"])

    def test_ledger_failure_does_not_hide_available_holding_review(self):
        portfolio = {
            "source": "confirmed holdings",
            "summary": {"holding_count": 1, "total_amount": 1000, "top1_ratio": 100, "top3_ratio": 100},
            "allocation": [{"asset_type": "fund", "code": "000001", "name": "Fund A", "amount": 1000}],
            "fund_trends": [],
            "fund_errors": [],
            "overlap": None,
            "overlap_error": None,
            "notes": [],
        }
        market = {"as_of": "2026-07-10", "summary": {}, "risks": [], "fund_candidates": [], "failed": [], "method": {}}
        profile = {
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": None,
            "max_single_ratio": 35,
            "configured": True,
            "updated_at": "2026-07-10T10:00:00",
        }

        with patch.object(decision_center.storage, "get_investment_profile", return_value=profile), \
             patch.object(decision_center.holdings_mod, "holdings_insights", return_value=portfolio), \
             patch.object(decision_center.market_daily_mod, "get_market_daily", return_value=market), \
             patch.object(decision_center.portfolio_review, "ledger_overview", side_effect=RuntimeError("ledger corrupt")), \
             patch.object(decision_center.portfolio_review, "rebalance_review", return_value={"allocations": []}), \
             patch.object(decision_center.portfolio_review, "cashflow_performance", return_value={"status": "unavailable", "summary": {}, "reasons": []}):
            result = decision_center.build_decision_center()

        action_ids = {item["id"] for item in result["actions"]}
        self.assertEqual(result["portfolio"]["status"], "available")
        self.assertIn("ledger-review-unavailable", action_ids)
        self.assertNotIn("record-transaction-ledger", action_ids)

    def test_partial_cashflow_coverage_becomes_a_review_action(self):
        portfolio = {
            "source": "confirmed holdings",
            "summary": {"holding_count": 2, "total_amount": 1600, "top1_ratio": 62.5, "top3_ratio": 100},
            "allocation": [
                {"asset_type": "fund", "code": "000001", "name": "Fund A", "amount": 1000},
                {"asset_type": "fund", "code": "000002", "name": "Fund B", "amount": 600},
            ],
            "fund_trends": [], "fund_errors": [], "overlap": None, "overlap_error": None, "notes": [],
        }
        market = {"as_of": "2026-07-10", "summary": {}, "risks": [], "fund_candidates": [], "failed": [], "method": {}}
        profile = {"risk": "balanced", "horizon": "mid_long", "monthly_budget": None, "max_single_ratio": 80, "configured": True, "updated_at": "2026-07-10T10:00:00"}
        with patch.object(decision_center.storage, "get_investment_profile", return_value=profile), \
             patch.object(decision_center.holdings_mod, "holdings_insights", return_value=portfolio), \
             patch.object(decision_center.market_daily_mod, "get_market_daily", return_value=market), \
             patch.object(decision_center.portfolio_review, "ledger_overview", return_value={"summary": {"transaction_count": 1}, "integrity_issues": []}), \
             patch.object(decision_center.portfolio_review, "rebalance_review", return_value={"allocations": []}), \
             patch.object(decision_center.portfolio_review, "cashflow_performance", return_value={
                 "status": "partial", "summary": {"untracked_holding_count": 1}, "reasons": ["有 1 项已确认持仓没有对应交易流水。"],
             }):
            result = decision_center.build_decision_center()

        action_ids = {item["id"] for item in result["actions"]}
        self.assertIn("complete-cashflow-performance", action_ids)

    def test_overlap_failure_preserves_previous_overlap_risk(self):
        portfolio = {
            "source": "confirmed holdings",
            "summary": {"holding_count": 1, "total_amount": 1000, "top1_ratio": 100, "top3_ratio": 100},
            "allocation": [{"asset_type": "fund", "code": "000001", "name": "Fund A", "amount": 1000}],
            "fund_trends": [],
            "fund_errors": [],
            "overlap": None,
            "overlap_error": "overlap provider down",
            "notes": [],
        }
        market = {"as_of": "2026-07-10", "summary": {}, "risks": [], "fund_candidates": [], "failed": [], "method": {}}
        profile = {"risk": "balanced", "horizon": "mid_long", "max_single_ratio": 100, "configured": True}

        with patch.object(decision_center.storage, "get_investment_profile", return_value=profile), \
             patch.object(decision_center.holdings_mod, "holdings_insights", return_value=portfolio), \
             patch.object(decision_center.market_daily_mod, "get_market_daily", return_value=market), \
             patch.object(decision_center.portfolio_review, "ledger_overview", return_value={"summary": {"transaction_count": 1}, "integrity_issues": []}), \
             patch.object(decision_center.portfolio_review, "rebalance_review", return_value={"allocations": []}), \
             patch.object(decision_center.portfolio_review, "cashflow_performance", return_value={"status": "available", "summary": {}, "reasons": []}):
            result = decision_center.build_decision_center()

        self.assertIn("fund-overlap-unavailable", {item["id"] for item in result["actions"]})
        self.assertTrue(result["task_inbox"]["resolution_deferred"])
        self.assertIn("基金持仓重合度", {item["scope"] for item in result["unavailable"]})


if __name__ == "__main__":
    unittest.main()
