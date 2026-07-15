# -*- coding: utf-8 -*-
"""Deterministic FIFO and disclosed-fee switch-cost review tests."""

import sys
import unittest
from datetime import date
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_switch_cost import evaluate_fund_switch_cost  # noqa: E402


def redemption_band(minimum, maximum, rate, label):
    return {
        "holding_period": label,
        "rate_pct": rate,
        "interval_status": "parsed",
        "min_holding_days": minimum,
        "min_inclusive": True if minimum is not None else None,
        "max_holding_days": maximum,
        "max_inclusive": False if maximum is not None else None,
    }


def purchase_band(source=1.0, current=0.1):
    return {
        "amount_range": "全部金额",
        "source_rate_pct": source,
        "current_rate_pct": current,
        "fixed_fee_yuan": None,
        "interval_status": "parsed",
        "min_amount_yuan": 0,
        "min_inclusive": True,
        "max_amount_yuan": None,
        "max_inclusive": None,
    }


class FundSwitchCostTests(unittest.TestCase):
    def setUp(self):
        self.holding = {
            "id": 1,
            "asset_type": "fund",
            "code": "000001",
            "shares": 100,
            "amount": 1000,
        }
        self.lots = {
            "transaction_count": 2,
            "integrity_issues": [],
            "position": {"open_shares": 100},
            "remaining_lots": [
                {
                    "transaction_id": 1,
                    "trade_type": "buy",
                    "trade_date": "2026-01-25",
                    "shares": 50,
                },
                {
                    "transaction_id": 2,
                    "trade_type": "buy",
                    "trade_date": "2025-12-31",
                    "shares": 50,
                },
            ],
        }
        self.selected_fees = {
            "status": "available",
            "source_url": "https://example.test/selected",
            "redemption": {
                "bands": [
                    redemption_band(0, 7, 1.5, "小于7天"),
                    redemption_band(7, None, 0, "大于等于7天"),
                ],
            },
        }
        self.candidate_fees = {
            "status": "available",
            "source_url": "https://example.test/candidate",
            "purchase": {"bands": [purchase_band()]},
        }
        self.valuation = {
            "unit_nav": 10,
            "as_of": "2026-01-30",
            "source_url": "https://example.test/nav",
        }
        self.durability = {"rolling": {"12m": {"median_excess_pp": 4.0}}}
        self.due_diligence = {
            "decision_gate": {"eligible_for_holding_period_cost_review": True},
        }

    def evaluate(self, **overrides):
        payload = {
            "holding": self.holding,
            "lot_snapshot": self.lots,
            "selected_fees": self.selected_fees,
            "candidate_fees": self.candidate_fees,
            "valuation": self.valuation,
            "durability": self.durability,
            "due_diligence": self.due_diligence,
            "candidate_code": "000002",
            "candidate_name": "候选基金",
            "review_on": date(2026, 1, 31),
        }
        payload.update(overrides)
        return evaluate_fund_switch_cost(**payload)

    def test_matches_each_fifo_lot_and_calculates_two_disclosed_cost_snapshots(self):
        result = self.evaluate()

        self.assertEqual(result["status"], "ready_for_platform_quote")
        self.assertEqual(result["redemption"]["disclosed_fee_yuan"], 7.5)
        self.assertEqual(result["redemption"]["lot_breakdown"][0]["matched_band"], "小于7天")
        self.assertEqual(result["redemption"]["lot_breakdown"][1]["rate_pct"], 0.0)
        self.assertEqual(result["candidate_entry"]["matched_band"], "全部金额")
        self.assertAlmostEqual(
            result["cost_snapshots"]["page_promotional"]["total_switching_cost_yuan"],
            8.49,
            places=2,
        )
        self.assertEqual(result["historical_cost_hurdle"]["page_promotional_coverage_months"], 2.5)
        self.assertFalse(result["decision_gate"]["executable_switch_cost_confirmed"])
        self.assertEqual(len(result["evidence_sha256"]), 64)

    def test_opening_lot_date_is_not_treated_as_purchase_confirmation_date(self):
        lots = {**self.lots, "remaining_lots": [{**self.lots["remaining_lots"][0], "trade_type": "opening", "shares": 100}], "position": {"open_shares": 100}}

        result = self.evaluate(lot_snapshot=lots)

        self.assertEqual(result["status"], "lot_date_unverified")
        self.assertIsNone(result["cost_snapshots"])

    def test_share_mismatch_blocks_cost_calculation(self):
        result = self.evaluate(holding={**self.holding, "shares": 120})

        self.assertEqual(result["status"], "share_reconciliation_failed")

    def test_upstream_due_diligence_gate_blocks_even_with_complete_ledger(self):
        result = self.evaluate(
            due_diligence={"decision_gate": {"eligible_for_holding_period_cost_review": False}},
        )

        self.assertEqual(result["status"], "blocked_by_due_diligence")

    def test_unparsed_redemption_interval_is_not_guessed(self):
        fees = {
            **self.selected_fees,
            "redemption": {"bands": [{"holding_period": "未知规则", "rate_pct": 1, "interval_status": "unparsed"}]},
        }

        result = self.evaluate(selected_fees=fees)

        self.assertEqual(result["status"], "redemption_band_unmatched")


if __name__ == "__main__":
    unittest.main()

