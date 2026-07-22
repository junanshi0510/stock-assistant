# -*- coding: utf-8 -*-
"""Cost-basis and allocation review must remain auditable with mocked user facts."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_review  # noqa: E402
import storage  # noqa: E402


def trade(trade_type, shares, unit_price, fee=0, trade_date="2026-01-01", trade_id=1):
    return {
        "id": trade_id,
        "asset_type": "fund",
        "market": "基金",
        "code": "000001",
        "name": "示例基金",
        "trade_type": trade_type,
        "trade_date": trade_date,
        "shares": shares,
        "unit_price": unit_price,
        "fee": fee,
    }


class PortfolioReviewTests(unittest.TestCase):
    def test_fifo_cost_and_realized_profit_are_calculated_from_transactions(self):
        positions, issues = portfolio_review._calculate_fifo([
            trade("opening", 10, 10, fee=1, trade_id=1),
            trade("buy", 5, 12, fee=1, trade_date="2026-01-02", trade_id=2),
            trade("sell", 8, 15, fee=2, trade_date="2026-01-03", trade_id=3),
        ])

        self.assertEqual(issues, [])
        self.assertEqual(len(positions), 1)
        position = positions[0]
        self.assertEqual(position["open_shares"], 7)
        self.assertEqual(position["remaining_cost"], 81.2)
        self.assertEqual(position["average_cost"], 11.6)
        self.assertEqual(position["realized_profit"], 37.2)
        self.assertEqual(position["total_fee"], 4)

    def test_create_transaction_rejects_sell_without_recorded_available_shares(self):
        with patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=[]), \
             patch.object(portfolio_review.storage, "add_portfolio_transaction") as add_transaction:
            with self.assertRaisesRegex(ValueError, "卖出份额超过"):
                portfolio_review.create_transaction({
                    **trade("sell", 5, 10),
                    "trade_date": "2026-01-01",
                })
        add_transaction.assert_not_called()

    def test_rebalance_shows_only_user_rule_breach_and_missing_ledger(self):
        holdings = [
            {"asset_type": "fund", "market": "基金", "code": "000001", "name": "基金A", "amount": 10000},
            {"asset_type": "fund", "market": "基金", "code": "000002", "name": "基金B", "amount": 5000},
        ]
        profile = {
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": 2000,
            "max_single_ratio": 40,
            "configured": True,
            "updated_at": "2026-01-01T00:00:00",
        }
        valuation = {
            "status": "available",
            "snapshot": {"id": "valuation-current"},
            "binding": {"current": True},
            "runtime_gate": {"risk_analysis_eligible": True},
        }
        with patch.object(portfolio_review.portfolio_valuation, "current_valued_holdings", return_value=(holdings, valuation)), \
             patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=[]), \
             patch.object(portfolio_review.storage, "get_investment_profile", return_value=profile):
            result = portfolio_review.rebalance_review()

        first = result["allocations"][0]
        self.assertEqual(first["code"], "000001")
        self.assertEqual(first["current_ratio"], 66.67)
        self.assertEqual(first["excess_amount"], 4000)
        self.assertTrue(any("补录交易流水" in item["title"] for item in result["actions"]))
        self.assertTrue(any("超过单品上限" in item["title"] for item in result["actions"]))
        self.assertIn("不生成买卖指令", result["policy"])

    def test_rebalance_blocks_amount_actions_when_valuation_is_not_current(self):
        holdings = [
            {"asset_type": "fund", "market": "基金", "code": "000001", "name": "基金A", "amount": 10000},
        ]
        valuation = {
            "status": "available",
            "snapshot": {"id": "valuation-old"},
            "binding": {"current": False},
            "runtime_gate": {
                "risk_analysis_eligible": False,
                "reasons": ["持仓已变化"],
            },
        }
        with patch.object(portfolio_review.portfolio_valuation, "current_valued_holdings", return_value=(holdings, valuation)), \
             patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=[]), \
             patch.object(portfolio_review.storage, "get_investment_profile", return_value={"configured": True, "max_single_ratio": 40}):
            result = portfolio_review.rebalance_review()

        self.assertFalse(result["summary"]["valuation_eligible"])
        self.assertEqual(result["allocations"], [])
        self.assertTrue(any("刷新可信估值" in item["title"] for item in result["actions"]))

    def test_money_weighted_return_uses_confirmed_value_and_cashflows(self):
        rows = [trade("buy", 1000, 1, trade_date="2025-01-01")]
        holdings = [{
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "示例基金",
            "amount": 1100,
            "shares": 1000,
        }]
        with patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=rows), \
             patch.object(portfolio_review.storage, "list_holdings", return_value=holdings):
            result = portfolio_review.cashflow_performance(as_of=date(2026, 1, 1))

        self.assertEqual(result["status"], "available")
        self.assertAlmostEqual(result["summary"]["money_weighted_return_annualized"], 10.0, places=3)
        self.assertEqual(result["summary"]["cashflow_profit"], 100)

    def test_money_weighted_return_is_hidden_when_current_holding_lacks_cashflow(self):
        rows = [trade("buy", 1000, 1, trade_date="2025-01-01")]
        holdings = [
            {"asset_type": "fund", "market": "基金", "code": "000001", "name": "基金A", "amount": 1100},
            {"asset_type": "fund", "market": "基金", "code": "000002", "name": "基金B", "amount": 600},
        ]
        with patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=rows), \
             patch.object(portfolio_review.storage, "list_holdings", return_value=holdings):
            result = portfolio_review.cashflow_performance(as_of=date(2026, 1, 1))

        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["summary"]["money_weighted_return_annualized"])
        self.assertIsNone(result["summary"]["cashflow_profit"])
        self.assertEqual(result["summary"]["untracked_holding_count"], 1)

    def test_trade_behavior_uses_fifo_matched_sales_for_realized_outcomes(self):
        rows = [
            trade("buy", 10, 10, fee=1, trade_date="2026-01-01", trade_id=1),
            trade("buy", 10, 12, fee=1, trade_date="2026-01-11", trade_id=2),
            trade("sell", 15, 14, fee=2, trade_date="2026-02-01", trade_id=3),
        ]
        with patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=rows):
            result = portfolio_review.trade_behavior_review()

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["summary"]["fully_matched_sell_count"], 1)
        self.assertEqual(result["summary"]["matched_realized_profit"], 46.5)
        self.assertEqual(result["summary"]["win_count"], 1)
        self.assertEqual(result["summary"]["win_rate"], 100)
        self.assertEqual(result["summary"]["average_holding_days"], 27.7)
        self.assertEqual(result["asset_reviews"][0]["matched_realized_profit"], 46.5)

    def test_trade_behavior_hides_outcome_when_sell_is_not_fully_matched(self):
        rows = [
            trade("buy", 5, 10, trade_date="2026-01-01", trade_id=1),
            trade("sell", 10, 12, trade_date="2026-02-01", trade_id=2),
        ]
        with patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=rows):
            result = portfolio_review.trade_behavior_review()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["summary"]["fully_matched_sell_count"], 0)
        self.assertEqual(result["summary"]["partial_sell_count"], 1)
        self.assertIsNone(result["summary"]["matched_realized_profit"])
        self.assertEqual(result["coverage"]["unmatched_shares"], 5)
        self.assertTrue(result["reasons"])

    def test_snapshot_attribution_separates_cash_contribution_from_interval_change(self):
        snapshots = [
            {
                "id": 2,
                "captured_at": "2026-02-01T18:00:00",
                "total_amount": 1200,
                "holdings": [{"asset_type": "fund", "market": "基金", "code": "000001", "amount": 1200, "shares": 1100}],
            },
            {
                "id": 1,
                "captured_at": "2026-01-01T18:00:00",
                "total_amount": 1000,
                "holdings": [{"asset_type": "fund", "market": "基金", "code": "000001", "amount": 1000, "shares": 1000}],
            },
        ]
        rows = [
            trade("opening", 1000, 1, trade_date="2025-12-20", trade_id=1),
            trade("buy", 100, 1, trade_date="2026-01-15", trade_id=2),
        ]
        with patch.object(portfolio_review.storage, "list_portfolio_snapshots", return_value=snapshots), \
             patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=rows):
            result = portfolio_review.snapshot_attribution()

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["summary"]["asset_value_change"], 200)
        self.assertEqual(result["summary"]["net_cash_flow"], 100)
        self.assertEqual(result["summary"]["flow_adjusted_change"], 100)
        self.assertAlmostEqual(result["summary"]["modified_dietz_return"], 9.4801, places=4)
        self.assertEqual(result["summary"]["transaction_count"], 1)

    def test_snapshot_attribution_refuses_boundary_day_transactions_without_timestamps(self):
        snapshots = [
            {
                "id": 2,
                "captured_at": "2026-02-01T18:00:00",
                "total_amount": 1200,
                "holdings": [{"asset_type": "fund", "market": "基金", "code": "000001", "amount": 1200, "shares": 1100}],
            },
            {
                "id": 1,
                "captured_at": "2026-01-01T18:00:00",
                "total_amount": 1000,
                "holdings": [{"asset_type": "fund", "market": "基金", "code": "000001", "amount": 1000, "shares": 1000}],
            },
        ]
        rows = [
            trade("opening", 1000, 1, trade_date="2025-12-20", trade_id=1),
            trade("buy", 100, 1, trade_date="2026-01-01", trade_id=2),
        ]
        with patch.object(portfolio_review.storage, "list_portfolio_snapshots", return_value=snapshots), \
             patch.object(portfolio_review.storage, "list_portfolio_transactions", return_value=rows):
            result = portfolio_review.snapshot_attribution()

        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["summary"]["modified_dietz_return"])
        self.assertTrue(any("起止日" in reason for reason in result["reasons"]))


class PortfolioStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = storage._DB_PATH
        self.previous_conn = storage._conn
        with storage._lock:
            storage._conn = None
            storage._DB_PATH = str(Path(self.temp.name) / "portfolio-test.db")

    def tearDown(self):
        with storage._lock:
            if storage._conn is not None:
                storage._conn.close()
            storage._conn = self.previous_conn
            storage._DB_PATH = self.previous_path
        self.temp.cleanup()

    def test_transaction_and_snapshot_persist_only_confirmed_fields(self):
        created = storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "示例基金",
            "trade_type": "opening",
            "trade_date": "2026-01-01",
            "shares": 100,
            "unit_price": 1.2,
            "fee": 0,
            "note": "migration",
        })
        snapshot = storage.create_portfolio_snapshot([
            {
                "asset_type": "fund",
                "market": "基金",
                "code": "000001",
                "name": "示例基金",
                "amount": 130,
                "profit": 10,
                "yesterday_profit": 2,
                "shares": 100,
                "raw_text": "must not be copied into snapshot",
            }
        ], reason="manual_review")

        self.assertEqual(created["code"], "000001")
        self.assertEqual(len(storage.list_portfolio_transactions()), 1)
        self.assertEqual(snapshot["total_amount"], 130)
        self.assertEqual(storage.list_portfolio_snapshots()[0]["reason"], "manual_review")
        detailed_snapshot = storage.list_portfolio_snapshots(include_holdings=True)[0]
        self.assertEqual(detailed_snapshot["holdings"][0]["code"], "000001")
        self.assertNotIn("holdings_json", detailed_snapshot)

    def test_confirmed_csv_batch_is_atomic_and_cannot_be_imported_twice(self):
        saved = portfolio_review.create_transactions_from_csv([
            {
                "asset_type": "fund", "market": "基金", "code": "000001", "name": "示例基金",
                "trade_type": "buy", "trade_date": "2026-01-01", "shares": 100, "unit_price": 1,
                "fee": 0, "note": "csv row 1",
            },
            {
                "asset_type": "fund", "market": "基金", "code": "000001", "name": "示例基金",
                "trade_type": "sell", "trade_date": "2026-01-02", "shares": 50, "unit_price": 1.1,
                "fee": 0, "note": "csv row 2",
            },
        ], "a" * 64, "trades.csv")

        self.assertEqual(saved["count"], 2)
        self.assertTrue(storage.portfolio_import_exists("a" * 64))
        self.assertTrue(all(item["source"] == "csv_import" for item in storage.list_portfolio_transactions()))
        with self.assertRaisesRegex(ValueError, "已经导入过"):
            portfolio_review.create_transactions_from_csv([
                {
                    "asset_type": "fund", "market": "基金", "code": "000001", "name": "示例基金",
                    "trade_type": "buy", "trade_date": "2026-01-03", "shares": 1, "unit_price": 1,
                    "fee": 0,
                },
            ], "a" * 64, "trades.csv")

    def test_tiantian_transaction_batch_keeps_derived_source_label(self):
        saved = portfolio_review.create_transactions_from_csv([
            {
                "asset_type": "fund", "market": "基金", "code": "013403", "name": "华夏恒生科技ETF联接C",
                "trade_type": "buy", "trade_date": "2026-01-01", "shares": 100, "unit_price": 1.2,
                "fee": 0.1, "note": "导入业务: 定投申购", "source": "tiantian_fund_transaction_export",
            },
        ], "b" * 64, "天天基金-交易流水.xlsx")

        self.assertEqual(saved["count"], 1)
        self.assertEqual(storage.list_portfolio_transactions()[0]["source"], "tiantian_fund_transaction_export")
        self.assertEqual(portfolio_review.list_transactions()["items"][0]["source_label"], "天天基金交易导出")


if __name__ == "__main__":
    unittest.main()
