# -*- coding: utf-8 -*-
"""Deterministic tests for the second-stage fund replacement gate."""

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_alternative_due_diligence import (  # noqa: E402
    evaluate_alternative_due_diligence,
)


def fee_payload(total=1.4, entry=0.15, redemption=True):
    return {
        "status": "available",
        "operating": {"declared_annual_total_rate_pct": total},
        "purchase": {"first_band_current_rate_pct": entry},
        "redemption": {
            "bands": [{"holding_period": "大于等于30天", "rate_pct": 0.0}]
            if redemption else []
        },
    }


def portfolio(stocks, industries):
    return {
        "stocks": [
            {"code": code, "name": name, "nav_ratio": ratio}
            for code, name, ratio in stocks
        ],
        "industries": [
            {"name": name, "nav_ratio": ratio}
            for name, ratio in industries
        ],
        "stock_period": "2026-03-31",
        "industry_period": "2026-03-31",
    }


def durability(eligible=True):
    return {
        "status": "durable_advantage" if eligible else "mixed_evidence",
        "decision_gate": {"eligible_for_due_diligence": eligible},
    }


def selected_payload():
    return {
        "code": "000001",
        "name": "当前基金",
        "fees": fee_payload(1.4),
        "portfolio": portfolio(
            [("A", "甲公司", 12), ("B", "乙公司", 8)],
            [("消费", 35), ("医药", 25)],
        ),
        "managers": [{"id": "m1", "name": "当前经理", "score": 75}],
    }


def candidate_payload(*, candidate_portfolio=None, fees=None, eligible=True):
    return {
        "code": "000002",
        "name": "候选基金",
        "fees": fees if fees is not None else fee_payload(1.0, 0.1),
        "portfolio": candidate_portfolio if candidate_portfolio is not None else portfolio(
            [("C", "丙公司", 10), ("D", "丁公司", 7)],
            [("科技", 40), ("通信", 20)],
        ),
        "managers": [{"id": "m2", "name": "候选经理", "score": 80}],
        "durability": durability(eligible),
    }


class FundAlternativeDueDiligenceTests(unittest.TestCase):
    def test_distinct_lower_cost_candidate_opens_user_cost_review(self):
        result = evaluate_alternative_due_diligence(
            selected_payload(),
            [candidate_payload()],
        )

        row = result["candidates"][0]
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(row["status"], "distinct_candidate")
        self.assertEqual(row["overlap"]["stock_overlap_lower_bound_pct"], 0)
        self.assertEqual(row["fees"]["annual_rate_delta_pp"], -0.4)
        self.assertTrue(row["decision_gate"]["eligible_for_holding_period_cost_review"])
        self.assertFalse(row["decision_gate"]["automatic_switch_allowed"])

    def test_high_overlap_without_cost_edge_is_rejected(self):
        duplicate = portfolio(
            [("A", "甲公司", 12), ("B", "乙公司", 8)],
            [("消费", 30), ("医药", 22)],
        )
        row = evaluate_alternative_due_diligence(
            selected_payload(),
            [candidate_payload(candidate_portfolio=duplicate, fees=fee_payload(1.4))],
        )["candidates"][0]

        self.assertEqual(row["status"], "duplicate_without_cost_edge")
        self.assertEqual(row["overlap"]["stock_overlap_lower_bound_pct"], 20)
        self.assertEqual(row["overlap"]["industry_overlap_lower_bound_pct"], 52)
        self.assertFalse(row["decision_gate"]["eligible_for_holding_period_cost_review"])

    def test_high_overlap_with_material_cost_edge_can_continue(self):
        duplicate = portfolio(
            [("A", "甲公司", 12), ("B", "乙公司", 8)],
            [("消费", 35), ("医药", 25)],
        )
        row = evaluate_alternative_due_diligence(
            selected_payload(),
            [candidate_payload(candidate_portfolio=duplicate, fees=fee_payload(1.0))],
        )["candidates"][0]

        self.assertEqual(row["status"], "duplicate_but_cost_edge")
        self.assertTrue(row["decision_gate"]["eligible_for_holding_period_cost_review"])

    def test_broad_industry_match_without_common_stocks_is_not_called_high_duplicate(self):
        broad_style_match = portfolio(
            [("C", "丙公司", 12), ("D", "丁公司", 8)],
            [("消费", 35), ("医药", 25)],
        )
        row = evaluate_alternative_due_diligence(
            selected_payload(),
            [candidate_payload(candidate_portfolio=broad_style_match, fees=fee_payload(1.4))],
        )["candidates"][0]

        self.assertEqual(row["overlap"]["stock_overlap_lower_bound_pct"], 0)
        self.assertEqual(row["overlap"]["industry_overlap_lower_bound_pct"], 60)
        self.assertEqual(row["overlap"]["level"], "medium")
        self.assertEqual(row["status"], "partial_overlap_candidate")

    def test_failed_durability_blocks_even_distinct_cheaper_candidate(self):
        row = evaluate_alternative_due_diligence(
            selected_payload(),
            [candidate_payload(eligible=False)],
        )["candidates"][0]

        self.assertEqual(row["status"], "blocked_by_durability")
        self.assertFalse(row["decision_gate"]["eligible_for_holding_period_cost_review"])

    def test_missing_redemption_schedule_keeps_fee_evidence_incomplete(self):
        row = evaluate_alternative_due_diligence(
            selected_payload() | {"fees": fee_payload(1.4, redemption=False)},
            [candidate_payload()],
        )["candidates"][0]

        self.assertEqual(row["status"], "incomplete_fee_evidence")
        self.assertIsNone(row["fees"]["actual_redemption_rate_pct"])

    def test_missing_periodic_holdings_never_becomes_zero_overlap(self):
        unavailable = {
            "status": "unavailable",
            "reason": "real_periodic_portfolio_unavailable",
            "stocks": [],
            "industries": [],
        }
        row = evaluate_alternative_due_diligence(
            selected_payload(),
            [candidate_payload(candidate_portfolio=unavailable)],
        )["candidates"][0]

        self.assertEqual(row["status"], "insufficient_disclosure")
        self.assertEqual(row["overlap"]["status"], "unavailable")
        self.assertIsNone(row["overlap"]["stock_overlap_lower_bound_pct"])
        self.assertTrue(row["source_gaps"])


if __name__ == "__main__":
    unittest.main()
