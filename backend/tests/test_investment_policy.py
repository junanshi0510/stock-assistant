# -*- coding: utf-8 -*-
"""Investment policy validation is deterministic and conservative."""

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from investment_policy import validate_investment_policy  # noqa: E402


def valid_policy(**overrides):
    payload = {
        "risk": "balanced",
        "horizon": "mid_long",
        "experience_level": "intermediate",
        "primary_objective": "balanced_growth",
        "monthly_budget": 2000,
        "max_single_ratio": 30,
        "max_equity_ratio": 70,
        "max_industry_ratio": 25,
        "max_drawdown_pct": 25,
        "liquidity_reserve_months": 6,
        "allowed_fund_markets": ["mainland"],
        "accept_fx_risk": False,
        "emergency_fund_confirmed": True,
        "review_cycle_months": 6,
    }
    payload.update(overrides)
    return payload


class InvestmentPolicyValidationTests(unittest.TestCase):
    def test_valid_policy_has_stable_hash_and_no_errors(self):
        first = validate_investment_policy(valid_policy())
        second = validate_investment_policy(valid_policy(allowed_fund_markets=["mainland", "mainland"]))

        self.assertTrue(first["valid"])
        self.assertEqual(first["errors"], [])
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])
        self.assertEqual(len(first["payload_sha256"]), 64)

    def test_cross_border_market_requires_explicit_fx_consent(self):
        result = validate_investment_policy(
            valid_policy(allowed_fund_markets=["mainland", "hong_kong"])
        )

        self.assertFalse(result["valid"])
        self.assertIn("fx_consent_required", {item["code"] for item in result["errors"]})

    def test_risk_experience_horizon_and_liquidity_conflicts_block_activation(self):
        result = validate_investment_policy(valid_policy(
            risk="aggressive",
            horizon="short",
            experience_level="beginner",
            max_single_ratio=60,
            max_equity_ratio=100,
            max_drawdown_pct=50,
            liquidity_reserve_months=1,
            emergency_fund_confirmed=False,
        ))
        codes = {item["code"] for item in result["errors"]}

        self.assertFalse(result["valid"])
        self.assertIn("experience_risk_conflict", codes)
        self.assertIn("horizon_risk_conflict", codes)
        self.assertIn("liquidity_reserve_too_low", codes)
        self.assertIn("emergency_fund_not_confirmed", codes)

    def test_balanced_policy_cannot_claim_aggressive_limits(self):
        result = validate_investment_policy(valid_policy(
            max_single_ratio=55,
            max_equity_ratio=90,
            max_drawdown_pct=45,
        ))

        conflicts = [item for item in result["errors"] if item["code"] == "risk_limit_conflict"]
        self.assertEqual({item["field"] for item in conflicts}, {
            "max_single_ratio", "max_equity_ratio", "max_drawdown_pct",
        })


if __name__ == "__main__":
    unittest.main()
