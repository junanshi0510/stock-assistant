# -*- coding: utf-8 -*-
"""Portfolio action reports must stay deterministic, bound, and conservative."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_action_report  # noqa: E402
import holding_thesis  # noqa: E402
import storage  # noqa: E402


def holding(code: str, amount: float, *, profit: float = 0) -> dict:
    return {
        "id": int(code),
        "asset_type": "fund",
        "market": "基金",
        "code": code,
        "name": f"基金{code}",
        "amount": amount,
        "cost": amount - profit,
        "yesterday_profit": 0,
        "profit": profit,
        "profit_rate": profit / (amount - profit) * 100 if amount != profit else None,
        "shares": amount,
        "source": "manual",
        "updated_at": "2026-07-13T08:00:00",
    }


def profile(configured: bool = True) -> dict:
    return {
        "configured": configured,
        "profile_version_id": "ips_v1" if configured else None,
        "max_single_ratio": 50,
        "max_equity_ratio": 70,
        "max_industry_ratio": 30,
        "max_drawdown_pct": 25,
    }


def insights() -> dict:
    return {
        "summary": {
            "total_profit": -200,
            "weighted_profit_rate": -2,
            "top1_ratio": 70,
            "top3_ratio": 100,
        },
        "allocation": [
            {"asset_type": "fund", "code": "000001", "amount": 7000, "ratio": 70},
            {"asset_type": "fund", "code": "000002", "amount": 3000, "ratio": 30},
        ],
        "fund_trends": [
            {"code": "000001", "as_of": "2026-07-12", "return_3m": -3, "max_drawdown": -20, "current_drawdown": -5, "source": "真实净值"},
            {"code": "000002", "as_of": "2026-07-12", "return_3m": 2, "max_drawdown": -15, "current_drawdown": -2, "source": "真实净值"},
        ],
        "fund_errors": [],
        "overlap_error": None,
        "overlap": {
            "source": "基金定期报告",
            "funds": [
                {"code": "000001", "stock_period": "2026-03-31"},
                {"code": "000002", "stock_period": "2026-03-31"},
            ],
            "pairwise": [{
                "fund_a": "000001",
                "fund_b": "000002",
                "fund_a_name": "基金000001",
                "fund_b_name": "基金000002",
                "level": "中度重合",
                "stock_overlap_weight": 12,
                "industry_overlap_weight": 50,
                "common_stock_count": 2,
                "common_stocks": [{"code": "600000", "name": "示例", "min_ratio": 4}],
                "common_industries": [{"name": "科技", "min_ratio": 20}],
            }],
            "shared_stocks": [],
            "shared_industries": [],
            "failed": [],
            "summary": {"pair_count": 1, "high_overlap_pair_count": 1},
            "method": {"note": "定期报告"},
        },
    }


class PortfolioActionReportTests(unittest.TestCase):
    def build(self, *, configured=True, source=None, theses=None):
        return portfolio_action_report.build_action_report(
            holdings_provider=lambda: [holding("000001", 7000, profit=-300), holding("000002", 3000, profit=100)],
            profile_provider=lambda: profile(configured),
            insights_provider=lambda _max_funds: source or insights(),
            ledger_provider=lambda: {"summary": {"transaction_count": 2, "integrity_issue_count": 0}, "positions": []},
            performance_provider=lambda: {"status": "available", "summary": {}, "reasons": []},
            rebalance_provider=lambda: {
                "allocations": [
                    {"asset_type": "fund", "code": "000001", "current_ratio": 70, "max_single_ratio": 50, "excess_amount": 2000},
                    {"asset_type": "fund", "code": "000002", "current_ratio": 30, "max_single_ratio": 50, "excess_amount": 0},
                ]
            },
            theses_provider=lambda: theses or [],
        )

    def test_cap_breach_and_real_overlap_create_ordered_reviews(self):
        result = self.build()

        self.assertEqual(result["status"], "reviewable")
        rows = {row["code"]: row for row in result["holdings"]}
        self.assertEqual(rows["000001"]["decision"]["action"], "reduce_review")
        self.assertEqual(rows["000001"]["decision"]["review_amount"], 2000)
        self.assertEqual(rows["000002"]["decision"]["action"], "pause_add")
        step_ids = [step["id"] for step in result["strategy"]["steps"]]
        self.assertIn("reduce-review-000001", step_ids)
        self.assertIn("deduplicate-000001-000002", step_ids)
        self.assertEqual(result["readiness"]["overlap_available_pairs"], 1)

    def test_missing_active_policy_blocks_position_instructions(self):
        result = self.build(configured=False)

        self.assertEqual(result["status"], "blocked")
        self.assertTrue(all(row["decision"]["action"] == "data_required" for row in result["holdings"]))
        self.assertEqual(result["strategy"]["steps"][0]["id"], "activate-policy")

    def test_failed_real_fund_source_is_exposed_and_not_replaced(self):
        source = insights()
        source["fund_trends"] = [source["fund_trends"][0]]
        source["fund_errors"] = [{"code": "000002", "error": "provider timeout"}]
        source["overlap"] = None
        source["overlap_error"] = "disclosure timeout"

        result = self.build(source=source)

        self.assertEqual(result["status"], "partial")
        row = next(row for row in result["holdings"] if row["code"] == "000002")
        self.assertEqual(row["decision"]["action"], "data_required")
        self.assertIn("provider timeout", str(row["evidence"]))
        self.assertEqual(result["overlap"]["status"], "unavailable")

    def test_no_holdings_never_produces_an_investment_instruction(self):
        result = portfolio_action_report.build_action_report(
            holdings_provider=lambda: [],
            profile_provider=lambda: profile(),
            insights_provider=lambda _max_funds: {"summary": {}, "allocation": [], "fund_trends": [], "fund_errors": []},
            ledger_provider=lambda: {"summary": {}, "positions": []},
            performance_provider=lambda: {"status": "unavailable", "summary": {}, "reasons": []},
            rebalance_provider=lambda: {"allocations": []},
            theses_provider=lambda: [],
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["holdings"], [])
        self.assertEqual(result["strategy"]["steps"][0]["id"], "confirm-holdings")


class PortfolioActionReportStorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = storage._DB_PATH
        self.original_conn = storage._conn
        storage._DB_PATH = os.path.join(self.tempdir.name, "test.db")
        storage._conn = None

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.original_conn
        storage._DB_PATH = self.original_path
        self.tempdir.cleanup()

    def test_report_is_immutable_hashed_and_invalidated_by_holding_change(self):
        storage.upsert_holding(holding("000001", 1000))
        payload = {
            "schema_version": portfolio_action_report.SCHEMA_VERSION,
            "ruleset_version": portfolio_action_report.RULESET_VERSION,
            "holdings_sha256": portfolio_action_report.action_holdings_sha256(storage.list_holdings()),
            "theses_sha256": holding_thesis.theses_sha256([]),
            "profile_version_id": None,
            "status": "blocked",
            "holdings": [],
        }
        saved = storage.save_portfolio_action_report(payload)
        self.assertTrue(storage.verify_portfolio_action_report(saved["id"])["verified"])
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE portfolio_action_reports SET status='reviewable' WHERE id=?",
                (saved["id"],),
            )

        storage.upsert_holding(holding("000001", 1200))
        loaded = portfolio_action_report.load_action_report(saved["id"])
        self.assertFalse(loaded["binding"]["current"])
        self.assertIn("holdings_changed", loaded["binding"]["reasons"])


if __name__ == "__main__":
    unittest.main()
