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
             ), \
             patch.object(
                 fund_switch_cost_service.storage,
                 "save_fund_switch_cost_review",
                 return_value={
                     "id": "fund_switch_cost_1",
                     "payload_sha256": "a" * 64,
                     "evidence_sha256": "b" * 64,
                     "created_at": "2026-01-31T00:00:00+00:00",
                     "integrity_verified": True,
                     "deduplicated": False,
                 },
             ) as save_review, \
             patch.object(
                 fund_switch_cost_service.fund_switch_quote_service,
                 "get_latest_quote",
                 return_value=None,
             ):
            result = fund_switch_cost_service.get_holding_fund_alternatives(
                9,
                user_id="user-a",
                review_on=date(2026, 1, 31),
            )

        review = result["alternatives"][0]["switch_cost_review"]
        self.assertEqual(review["status"], "ready_for_platform_quote")
        self.assertEqual(
            result["alternatives"][0]["switch_cost_binding"]["review_id"],
            "fund_switch_cost_1",
        )
        self.assertEqual(result["switch_cost_audit"]["holding_id"], 9)
        self.assertEqual(result["switch_cost_audit"]["summary"]["ready_for_platform_quote_count"], 1)
        save_review.assert_called_once_with(review, 9, user_id="user-a")

    def test_rejects_holding_from_another_user(self):
        with patch.object(fund_switch_cost_service.storage, "list_holdings", return_value=[]):
            with self.assertRaises(fund_switch_cost_service.HoldingNotFoundError):
                fund_switch_cost_service.get_holding_fund_alternatives(99, user_id="user-a")

    def test_blocked_candidate_is_not_persisted_as_quoteable_cost_snapshot(self):
        holding = {"id": 9, "asset_type": "fund", "code": "000001", "shares": 100}
        alternatives = {
            "source": "真实基金来源",
            "selected": {"code": "000001", "as_of": "2026-01-30", "unit_nav": 10},
            "alternatives": [{"code": "000002", "name": "候选基金"}],
        }
        blocked = {
            "status": "blocked_by_due_diligence",
            "decision_gate": {"eligible_for_platform_quote_confirmation": False},
        }
        with patch.object(fund_switch_cost_service.storage, "list_holdings", return_value=[holding]), \
             patch.object(fund_switch_cost_service.funds, "get_fund_alternatives", return_value=alternatives), \
             patch.object(fund_switch_cost_service.portfolio_review, "remaining_lot_snapshot", return_value={}), \
             patch.object(fund_switch_cost_service.funds, "_fund_fee_schedule", return_value={}), \
             patch.object(fund_switch_cost_service, "evaluate_fund_switch_cost", return_value=blocked), \
             patch.object(fund_switch_cost_service.storage, "save_fund_switch_cost_review") as save_review:
            result = fund_switch_cost_service.get_holding_fund_alternatives(9, user_id="user-a")

        self.assertIsNone(result["alternatives"][0]["switch_cost_binding"])
        save_review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
