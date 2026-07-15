# -*- coding: utf-8 -*-
"""Service wiring tests for real fee and periodic-report due diligence."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


class FundAlternativeDueDiligenceServiceTests(unittest.TestCase):
    def test_service_forwards_real_source_payloads_and_durability(self):
        selected = {
            "code": "000001",
            "name": "当前基金",
            "managers": [{"id": "m1", "name": "当前经理"}],
        }
        alternatives = [{
            "code": "000002",
            "name": "候选基金",
            "managers": [{"id": "m2", "name": "候选经理"}],
        }]
        audit = {
            "candidates": [{
                "code": "000002",
                "status": "durable_advantage",
                "decision_gate": {"eligible_for_due_diligence": True},
            }],
        }
        portfolios = {
            code: {
                "code": code,
                "stocks": [{"code": code, "name": code, "nav_ratio": 10}],
                "industries": [],
            }
            for code in ("000001", "000002")
        }
        fees = {
            code: {
                "status": "available",
                "code": code,
                "operating": {"declared_annual_total_rate_pct": 1.0},
                "purchase": {"first_band_current_rate_pct": 0.1},
                "redemption": {"bands": [{"holding_period": "30天以上", "rate_pct": 0}]},
            }
            for code in ("000001", "000002")
        }
        expected = {"status": "evaluated", "candidates": []}

        with (
            patch.object(funds, "get_fund_portfolio", side_effect=lambda code: portfolios[code]),
            patch.object(funds, "_fund_fee_schedule", side_effect=lambda code: fees[code]),
            patch.object(
                funds,
                "evaluate_alternative_due_diligence",
                return_value=expected,
            ) as evaluator,
        ):
            result = funds._alternative_due_diligence_audit(selected, alternatives, audit)

        self.assertEqual(result, expected)
        selected_payload, candidate_payloads = evaluator.call_args.args
        self.assertIs(selected_payload["portfolio"], portfolios["000001"])
        self.assertIs(selected_payload["fees"], fees["000001"])
        self.assertEqual(selected_payload["managers"][0]["id"], "m1")
        self.assertIs(candidate_payloads[0]["portfolio"], portfolios["000002"])
        self.assertTrue(
            candidate_payloads[0]["durability"]["decision_gate"]["eligible_for_due_diligence"]
        )

    def test_source_failure_is_explicit_and_not_replaced(self):
        selected = {"code": "000001", "name": "当前基金", "managers": []}
        alternatives = [{"code": "000002", "name": "候选基金", "managers": []}]
        audit = {"candidates": [{"code": "000002", "decision_gate": {}}]}

        def portfolio(code):
            if code == "000002":
                raise RuntimeError("portfolio timeout")
            return {"code": code, "stocks": [], "industries": []}

        with (
            patch.object(funds, "get_fund_portfolio", side_effect=portfolio),
            patch.object(funds, "_fund_fee_schedule", side_effect=RuntimeError("fee timeout")),
            patch.object(
                funds,
                "evaluate_alternative_due_diligence",
                return_value={"status": "partial"},
            ) as evaluator,
        ):
            funds._alternative_due_diligence_audit(selected, alternatives, audit)

        selected_payload, candidate_payloads = evaluator.call_args.args
        self.assertEqual(selected_payload["fees"]["status"], "unavailable")
        self.assertIn("fee timeout", selected_payload["fees"]["reason"])
        self.assertEqual(candidate_payloads[0]["portfolio"]["status"], "unavailable")
        self.assertIn("portfolio timeout", candidate_payloads[0]["portfolio"]["reason"])


if __name__ == "__main__":
    unittest.main()
