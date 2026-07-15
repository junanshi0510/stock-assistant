# -*- coding: utf-8 -*-
"""User-scoped service binding tests for fund replacement costs."""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import fund_switch_cost_service  # noqa: E402


class FundSwitchCostServiceTests(unittest.TestCase):
    def test_enriches_candidates_with_user_scoped_cost_review(self):
        holding = {"id": 9, "asset_type": "fund", "code": "000001", "shares": 100, "amount": 1000}
        alternatives = {
            "source": "真实基金来源",
            "selected": {"code": "000001", "as_of": "2026-01-30", "unit_nav": 10},
            "alternatives": [{
                "code": "000002",
                "name": "候选基金",
                "durability": {"rolling": {"12m": {"median_excess_pp": 4}}},
                "due_diligence": {"decision_gate": {"eligible_for_holding_period_cost_review": True}},
            }],
        }
        lots = {
            "transaction_count": 1,
            "integrity_issues": [],
            "position": {"open_shares": 100},
            "remaining_lots": [{
                "transaction_id": 3,
                "trade_type": "buy",
                "trade_date": "2025-12-01",
                "shares": 100,
            }],
        }
        selected_fee = {
            "status": "available",
            "source_url": "selected",
            "redemption": {"bands": [{
                "holding_period": "全部",
                "rate_pct": 0,
                "interval_status": "parsed",
                "min_holding_days": 0,
                "min_inclusive": True,
                "max_holding_days": None,
                "max_inclusive": None,
            }]},
        }
        candidate_fee = {
            "status": "available",
            "source_url": "candidate",
            "purchase": {"bands": [{
                "amount_range": "全部",
                "source_rate_pct": 0,
                "current_rate_pct": 0,
                "fixed_fee_yuan": None,
                "interval_status": "parsed",
                "min_amount_yuan": 0,
                "min_inclusive": True,
                "max_amount_yuan": None,
                "max_inclusive": None,
            }]},
        }

        with patch.object(fund_switch_cost_service.storage, "list_holdings", return_value=[holding]), \
             patch.object(fund_switch_cost_service.funds, "get_fund_alternatives", return_value=alternatives), \
             patch.object(fund_switch_cost_service.portfolio_review, "remaining_lot_snapshot", return_value=lots), \
             patch.object(
                 fund_switch_cost_service.funds,
                 "_fund_fee_schedule",
                 side_effect=lambda code: selected_fee if code == "000001" else candidate_fee,
             ):
            result = fund_switch_cost_service.get_holding_fund_alternatives(
                9,
                user_id="user-a",
                review_on=date(2026, 1, 31),
            )

        review = result["alternatives"][0]["switch_cost_review"]
        self.assertEqual(review["status"], "ready_for_platform_quote")
        self.assertEqual(result["switch_cost_audit"]["holding_id"], 9)
        self.assertEqual(result["switch_cost_audit"]["summary"]["ready_for_platform_quote_count"], 1)

    def test_rejects_holding_from_another_user(self):
        with patch.object(fund_switch_cost_service.storage, "list_holdings", return_value=[]):
            with self.assertRaises(fund_switch_cost_service.HoldingNotFoundError):
                fund_switch_cost_service.get_holding_fund_alternatives(99, user_id="user-a")


if __name__ == "__main__":
    unittest.main()

