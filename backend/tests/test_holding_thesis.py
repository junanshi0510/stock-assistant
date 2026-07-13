# -*- coding: utf-8 -*-
"""Holding thesis versions must remain user-authored, immutable, and auditable."""

import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import holding_thesis  # noqa: E402
import portfolio_action_report  # noqa: E402
import storage  # noqa: E402


def saved_holding(*, profit_rate: float = -18) -> dict:
    return {
        "asset_type": "fund",
        "market": "基金",
        "code": "013403",
        "name": "华夏恒生科技ETF发起式联接(QDII)C",
        "amount": 1886.16,
        "cost": 2374.94,
        "profit": -488.78,
        "profit_rate": profit_rate,
        "shares": 1000,
        "source": "manual",
    }


def thesis_payload(**overrides) -> dict:
    payload = {
        "asset_type": "fund",
        "market": "基金",
        "code": "013403",
        "role": "satellite_growth",
        "thesis_summary": "通过港股科技龙头获得长期成长暴露，同时严格限制卫星仓比例。",
        "expected_holding_months": 36,
        "review_date": (dt.date.today() + dt.timedelta(days=90)).isoformat(),
        "max_loss_pct": 15,
        "max_drawdown_pct": 20,
        "add_condition": "估值和趋势同时改善且组合仓位仍低于上限时再复核新增。",
        "exit_condition": "基金目标风格发生漂移、长期逻辑失效或风险边界触发后复核退出。",
    }
    payload.update(overrides)
    return payload


class HoldingThesisStorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = storage._DB_PATH
        self.original_conn = storage._conn
        storage._DB_PATH = os.path.join(self.tempdir.name, "test.db")
        storage._conn = None
        storage.upsert_holding(saved_holding())

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.original_conn
        storage._DB_PATH = self.original_path
        self.tempdir.cleanup()

    def test_revisions_are_immutable_hash_linked_and_reported_in_coverage(self):
        first = holding_thesis.save_thesis(thesis_payload())
        second = holding_thesis.save_thesis(thesis_payload(thesis_summary=(
            "继续保留港股科技卫星仓，但只有基本面和趋势证据同时改善时才考虑新增。"
        )))

        self.assertEqual(first["item"]["version_no"], 1)
        self.assertEqual(second["item"]["version_no"], 2)
        self.assertEqual(second["item"]["previous_version_id"], first["item"]["id"])
        self.assertTrue(second["verification"]["verified"])
        coverage = holding_thesis.list_with_coverage()["coverage"]
        self.assertEqual(coverage["active_thesis_count"], 1)
        self.assertEqual(coverage["missing_count"], 0)

        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE holding_thesis_versions SET state='archived' WHERE id=?",
                (second["item"]["id"],),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "DELETE FROM holding_thesis_versions WHERE id=?",
                (first["item"]["id"],),
            )

    def test_real_holding_loss_and_fund_drawdown_trigger_review_not_auto_sell(self):
        record = holding_thesis.save_thesis(thesis_payload())["item"]
        assessment = holding_thesis.evaluate_thesis(
            record,
            holding=saved_holding(profit_rate=-18),
            trend={"current_drawdown": -24, "source": "真实基金净值"},
        )

        self.assertEqual(assessment["status"], "risk_limit_breached")
        self.assertEqual(
            {item["code"] for item in assessment["breaches"]},
            {"holding_loss_limit_reached", "asset_drawdown_limit_reached"},
        )
        self.assertFalse(assessment["manual_conditions"]["machine_verified"])

    def test_recreated_holding_does_not_inherit_deleted_holding_plan(self):
        holding_thesis.save_thesis(thesis_payload())
        original = storage.list_holdings()[0]
        self.assertTrue(storage.delete_holding(original["id"]))
        replacement = storage.upsert_holding(saved_holding())

        coverage = holding_thesis.list_with_coverage()["coverage"]
        self.assertNotEqual(replacement["id"], original["id"])
        self.assertEqual(coverage["active_thesis_count"], 0)
        self.assertEqual(coverage["missing_count"], 1)

    def test_full_chain_verification_is_not_limited_to_history_page_size(self):
        for version_no in range(1, 102):
            holding_thesis.save_thesis(thesis_payload(thesis_summary=(
                f"第{version_no}版持有逻辑用于验证完整审计链，不允许分页边界制造错误结论。"
            )))

        verification = storage.verify_holding_thesis_chain("fund", "基金", "013403")

        self.assertTrue(verification["verified"])
        self.assertEqual(verification["version_count"], 101)

    def test_thesis_change_invalidates_prior_action_report(self):
        initial = holding_thesis.save_thesis(thesis_payload())["item"]
        payload = {
            "schema_version": portfolio_action_report.SCHEMA_VERSION,
            "ruleset_version": portfolio_action_report.RULESET_VERSION,
            "holdings_sha256": portfolio_action_report.action_holdings_sha256(
                storage.list_holdings()
            ),
            "theses_sha256": holding_thesis.theses_sha256([initial]),
            "profile_version_id": None,
            "status": "reviewable",
            "holdings": [],
        }
        saved = storage.save_portfolio_action_report(payload)
        holding_thesis.save_thesis(thesis_payload(thesis_summary=(
            "新版本改变了原持有逻辑，因此旧行动报告必须立即停止继续使用。"
        )))

        loaded = portfolio_action_report.load_action_report(saved["id"])
        self.assertFalse(loaded["binding"]["current"])
        self.assertIn("holding_theses_changed", loaded["binding"]["reasons"])


class HoldingThesisActionReportTests(unittest.TestCase):
    def test_missing_thesis_becomes_explicit_review_step(self):
        item = saved_holding(profit_rate=2)
        result = portfolio_action_report.build_action_report(
            holdings_provider=lambda: [item],
            profile_provider=lambda: {
                "configured": True,
                "profile_version_id": "ips_1",
                "max_drawdown_pct": 30,
            },
            insights_provider=lambda _max: {
                "summary": {"total_profit": 20},
                "allocation": [{"asset_type": "fund", "code": "013403", "ratio": 100}],
                "fund_trends": [{
                    "code": "013403",
                    "as_of": "2026-07-12",
                    "current_drawdown": -3,
                    "max_drawdown": -25,
                    "source": "真实基金净值",
                }],
                "fund_errors": [],
                "overlap": {},
            },
            ledger_provider=lambda: {"summary": {"transaction_count": 1}, "positions": []},
            performance_provider=lambda: {"status": "available", "summary": {}},
            rebalance_provider=lambda: {"allocations": []},
            theses_provider=lambda: [],
        )

        self.assertEqual(result["holdings"][0]["decision"]["action"], "thesis_review")
        self.assertEqual(result["summary"]["thesis_missing_count"], 1)
        self.assertIn(
            "complete-holding-theses",
            [step["id"] for step in result["strategy"]["steps"]],
        )

    def test_thesis_source_failure_is_not_reported_as_user_omission(self):
        item = saved_holding(profit_rate=2)

        def failed_theses():
            raise RuntimeError("thesis database unavailable")

        result = portfolio_action_report.build_action_report(
            holdings_provider=lambda: [item],
            profile_provider=lambda: {
                "configured": True,
                "profile_version_id": "ips_1",
                "max_drawdown_pct": 30,
            },
            insights_provider=lambda _max: {
                "summary": {"total_profit": 20},
                "allocation": [{"asset_type": "fund", "code": "013403", "ratio": 100}],
                "fund_trends": [{
                    "code": "013403",
                    "as_of": "2026-07-12",
                    "current_drawdown": -3,
                    "max_drawdown": -25,
                    "source": "真实基金净值",
                }],
                "fund_errors": [],
                "overlap": {},
            },
            ledger_provider=lambda: {"summary": {"transaction_count": 1}, "positions": []},
            performance_provider=lambda: {"status": "available", "summary": {}},
            rebalance_provider=lambda: {"allocations": []},
            theses_provider=failed_theses,
        )

        row = result["holdings"][0]
        self.assertEqual(row["decision"]["action"], "data_required")
        self.assertIn("holding_thesis_source_unavailable", row["decision"]["blockers"])
        self.assertIsNone(result["summary"]["thesis_missing_count"])
        self.assertNotIn(
            "complete-holding-theses",
            [step["id"] for step in result["strategy"]["steps"]],
        )


if __name__ == "__main__":
    unittest.main()
