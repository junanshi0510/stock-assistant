# -*- coding: utf-8 -*-
"""Portfolio-aware decisions must obey user constraints before suggesting money."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.portfolio_context import get_portfolio_context  # noqa: E402
from strategies.personalized_fund_decision import (  # noqa: E402
    STRATEGY_ID,
    STRATEGY_VERSION,
    evaluate_personalized_fund_decision,
)


def _analysis(decision="research", confidence="medium", risk_band="均衡偏波动"):
    return {
        "metrics": {"annual_volatility": 18.0},
        "timing": {"score": 62},
        "playbook": {"role": {"risk_band": risk_band}},
        "conditioned_forward": {
            "strategy_id": "fund_conditioned_forward_return",
            "decision": decision,
            "confidence": {"level": confidence},
            "primary_horizon": "6m",
            "condition": {"as_of": "2026-07-10"},
            "horizons": [{
                "horizon": "6m",
                "analog": {
                    "positive_rate": 62.5,
                    "median_return": 6.2,
                    "worst_return": -12.0,
                },
            }],
            "invalidation_conditions": [{"field": "trend", "invalid_when": "changes"}],
        },
    }


def _context(*, configured=True, total=10000, target_amount=1000, target_ratio=10, max_ratio=35):
    return {
        "profile": {
            "configured": configured,
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": 1000,
            "max_single_ratio": max_ratio,
            "allowed_fund_markets": ["mainland"],
            "accept_fx_risk": False,
        },
        "portfolio": {
            "holding_count": 3,
            "amount_complete": True,
            "total_amount": total,
        },
        "target_holding": {
            "exists": target_amount > 0,
            "amount": target_amount,
            "ratio": target_ratio,
            "profit_rate": -5.0,
        },
    }


def _market(primary="mainland", *, resolution="identified", qdii=False):
    permission = [] if resolution != "identified" else [primary]
    return {
        "resolution_status": resolution,
        "fund": {"is_qdii": qdii},
        "market": {
            "primary": primary,
            "label": {"mainland": "中国内地", "hong_kong": "中国香港", "united_states": "美国"}.get(primary, "待确认"),
            "required_permissions": permission,
            "cross_border": primary != "mainland",
            "currency_risk": primary != "mainland",
        },
        "benchmark_names": ["沪深300" if primary == "mainland" else "恒生科技指数"],
        "valuation": {"confirmed_nav_lag": "以确认净值日为准"},
    }


class PersonalizedFundDecisionTests(unittest.TestCase):
    def test_missing_profile_abstains_without_amount(self):
        result = evaluate_personalized_fund_decision(
            _analysis(), _context(configured=False), _market(), planned_amount=2000
        )

        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["decision"]["action"], "setup_required")
        self.assertIsNone(result["budget"]["first_tranche_amount"])
        self.assertIsNone(result["portfolio"]["max_single_ratio"])
        self.assertIn("investment_profile", result["missing_requirements"])

    def test_unconfigured_storage_defaults_are_not_treated_as_user_choices(self):
        holdings = []
        profile = {
            "configured": False,
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": None,
            "max_single_ratio": 35,
            "updated_at": None,
        }
        with (
            patch("agent.portfolio_context.storage.list_holdings", return_value=holdings),
            patch("agent.portfolio_context.storage.get_investment_profile", return_value=profile),
        ):
            result = get_portfolio_context({"code": "001480"})

        self.assertFalse(result["profile"]["configured"])
        self.assertIsNone(result["profile"]["risk"])
        self.assertIsNone(result["profile"]["horizon"])
        self.assertIsNone(result["profile"]["max_single_ratio"])

    def test_position_limit_overrides_positive_history_and_returns_reduction(self):
        result = evaluate_personalized_fund_decision(
            _analysis(),
            _context(total=10000, target_amount=5000, target_ratio=50, max_ratio=35),
            _market(),
            planned_amount=1000,
        )

        self.assertEqual(result["decision"]["action"], "reduce_exposure")
        self.assertAlmostEqual(result["budget"]["suggested_reduction_amount"], 2307.69, places=2)
        self.assertIsNone(result["budget"]["first_tranche_amount"])

    def test_positive_history_within_limits_returns_auditable_tranche(self):
        result = evaluate_personalized_fund_decision(
            _analysis(), _context(), _market(), planned_amount=1000
        )

        self.assertEqual(result["strategy_id"], STRATEGY_ID)
        self.assertEqual(result["strategy_version"], STRATEGY_VERSION)
        self.assertEqual(result["decision"]["action"], "consider_tranche")
        self.assertEqual(result["budget"]["allowed_full_amount"], 1000)
        self.assertEqual(result["budget"]["tranche_count"], 4)
        self.assertEqual(result["budget"]["first_tranche_amount"], 250)
        self.assertEqual(result["portfolio"]["projected_ratio_after_full_amount"], 18.18)

    def test_negative_historical_condition_blocks_averaging_down(self):
        result = evaluate_personalized_fund_decision(
            _analysis(decision="avoid_for_now"), _context(), _market(), planned_amount=1000
        )

        self.assertEqual(result["decision"]["action"], "wait")
        self.assertIsNone(result["budget"]["allowed_full_amount"])
        self.assertIsNone(result["budget"]["first_tranche_amount"])

    def test_risk_conflict_blocks_additional_amount(self):
        context = _context()
        context["profile"]["risk"] = "stable"
        result = evaluate_personalized_fund_decision(
            _analysis(risk_band="进攻型"), context, _market(), planned_amount=1000
        )

        self.assertEqual(result["decision"]["action"], "do_not_add")
        self.assertIsNone(result["budget"]["allowed_full_amount"])
        self.assertIsNone(result["budget"]["first_tranche_amount"])

    def test_short_horizon_blocks_non_stable_fund(self):
        context = _context()
        context["profile"]["horizon"] = "short"
        result = evaluate_personalized_fund_decision(
            _analysis(), context, _market(), planned_amount=1000
        )

        self.assertEqual(result["decision"]["action"], "do_not_add")
        self.assertIsNone(result["budget"]["allowed_full_amount"])

    def test_hong_kong_fund_requires_permission_and_fx_acknowledgement(self):
        context = _context()
        result = evaluate_personalized_fund_decision(
            _analysis(), context, _market("hong_kong", qdii=True), planned_amount=1000
        )

        self.assertEqual(result["decision"]["action"], "do_not_add")
        self.assertIsNone(result["budget"]["allowed_full_amount"])
        blocked = {item["code"] for item in result["gates"] if item["status"] == "block"}
        self.assertIn("fund_market_permission", blocked)
        self.assertIn("foreign_exchange_risk", blocked)

    def test_hong_kong_fund_can_reach_tranche_only_after_explicit_consent(self):
        context = _context()
        context["profile"]["allowed_fund_markets"].append("hong_kong")
        context["profile"]["accept_fx_risk"] = True
        result = evaluate_personalized_fund_decision(
            _analysis(), context, _market("hong_kong", qdii=True), planned_amount=1000
        )

        self.assertEqual(result["decision"]["action"], "consider_tranche")
        self.assertEqual(result["market_context"]["primary"], "hong_kong")
        self.assertEqual(result["budget"]["first_tranche_amount"], 250)

    def test_unresolved_qdii_market_blocks_amount(self):
        context = _context()
        context["profile"]["allowed_fund_markets"] = ["mainland", "hong_kong", "united_states", "global"]
        context["profile"]["accept_fx_risk"] = True
        result = evaluate_personalized_fund_decision(
            _analysis(),
            context,
            _market("unknown_cross_border", resolution="insufficient", qdii=True),
            planned_amount=1000,
        )

        self.assertEqual(result["decision"]["action"], "market_data_required")
        self.assertIsNone(result["budget"]["allowed_full_amount"])
        self.assertIn("fund_market_identification", result["missing_requirements"])

    def test_portfolio_context_only_reads_confirmed_storage_fields(self):
        holdings = [{
            "asset_type": "fund",
            "market": "基金",
            "code": "001480",
            "name": "测试基金",
            "amount": 2000,
            "profit": -100,
            "profit_rate": -5,
            "source": "manual",
            "updated_at": "2026-07-10T10:00:00",
        }]
        profile = {
            "configured": True,
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": 1000,
            "max_single_ratio": 35,
            "updated_at": "2026-07-10T11:00:00",
        }
        with (
            patch("agent.portfolio_context.storage.list_holdings", return_value=holdings),
            patch("agent.portfolio_context.storage.get_investment_profile", return_value=profile),
        ):
            result = get_portfolio_context({"code": "001480"})

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["data_classification"], "private_financial")
        self.assertEqual(result["target_holding"]["ratio"], 100)
        self.assertEqual(result["data_gaps"], [])


if __name__ == "__main__":
    unittest.main()
