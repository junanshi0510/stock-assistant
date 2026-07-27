# -*- coding: utf-8 -*-
"""Whole-portfolio capital decisions must be bounded, auditable and scoped."""

from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_capital_decision as service  # noqa: E402
import portfolio_exposure  # noqa: E402
from migrations import portfolio_capital_decision_v1  # noqa: E402
from portfolio_capital_repository import (  # noqa: E402
    PortfolioCapitalRepository,
)


class FakeProfitRepository:
    def get_scorecard(self, scorecard_id: str, *, user_id: str):
        if scorecard_id != "profit_score_1" or user_id != "owner":
            return None
        return {
            "id": scorecard_id,
            "integrity_verified": True,
            "scorecard": {"schema_version": "opportunity_profit_scorecard.v1"},
        }


def fixtures(*, existing_action: str = "hold_review"):
    holdings = [
        {
            "id": 1,
            "asset_type": "fund",
            "market": "基金",
            "code": "510300",
            "name": "沪深300ETF",
            "amount": 50_000,
            "shares": 10_000,
            "source": "manual",
            "updated_at": "2026-07-23T08:00:00+00:00",
            "valuation_snapshot_id": "valuation_1",
            "valuation_method": "automatic_confirmed_price",
            "valuation_price_as_of": "2026-07-23",
        },
        {
            "id": 2,
            "asset_type": "fund",
            "market": "基金",
            "code": "159915",
            "name": "创业板ETF",
            "amount": 50_000,
            "shares": 20_000,
            "source": "manual",
            "updated_at": "2026-07-23T08:00:00+00:00",
            "valuation_snapshot_id": "valuation_1",
            "valuation_method": "automatic_confirmed_price",
            "valuation_price_as_of": "2026-07-23",
        },
    ]
    valuation_payload = {
        "summary": {"total_value": 100_000},
        "coverage": {
            "automatic_value_pct": 100,
            "professional_value_pct": 100,
        },
        "positions": [
            {
                "holding_id": 1,
                "asset_type": "fund",
                "market": "基金",
                "code": "510300",
                "name": "沪深300ETF",
                "base_value": 50_000,
                "ratio": 50,
            },
            {
                "holding_id": 2,
                "asset_type": "fund",
                "market": "基金",
                "code": "159915",
                "name": "创业板ETF",
                "base_value": 50_000,
                "ratio": 50,
            }
        ],
    }
    valuation = {
        "status": "available",
        "snapshot": {
            "id": "valuation_1",
            "schema_version": "portfolio_valuation_snapshot.v1",
            "method_version": "confirmed_market_value.v1",
            "holdings_sha256": "a" * 64,
            "status": "complete",
            "fresh_until": "2026-07-24T08:00:00+00:00",
            "payload_sha256": "b" * 64,
            "created_at": "2026-07-23T08:05:00+00:00",
            "payload": valuation_payload,
        },
        "binding": {"current": True},
        "runtime_gate": {
            "risk_analysis_eligible": True,
            "trade_amount_eligible": True,
            "integrity_verified": True,
            "reasons": [],
        },
    }
    profile = {
        "configured": True,
        "profile_version_id": "ips_1",
        "version_no": 1,
        "payload_sha256": "c" * 64,
        "risk": "balanced",
        "horizon": "mid_long",
        "experience_level": "experienced",
        "primary_objective": "long_term_growth",
        "monthly_budget": 10_000,
        "max_single_ratio": 60,
        "max_equity_ratio": 80,
        "max_industry_ratio": 40,
        "max_drawdown_pct": 25,
        "allowed_fund_markets": ["mainland"],
        "accept_fx_risk": False,
        "integrity_verified": True,
        "review_due_at": "2027-01-01T00:00:00+00:00",
        "governance_integrity": {"verified": True},
    }
    report = {
        "schema_version": "portfolio_action_report.v2",
        "status": "reviewable",
        "as_of": "2026-07-23T08:05:00+00:00",
        "binding": {"current": True, "reasons": []},
        "integrity": {"verified": True},
        "report": {
            "id": "action_report_1",
            "schema_version": "portfolio_action_report.v2",
            "ruleset_version": "portfolio_action_rules.v3",
            "holdings_sha256": "d" * 64,
            "theses_sha256": "e" * 64,
            "profile_version_id": "ips_1",
            "status": "reviewable",
            "payload_sha256": "f" * 64,
            "created_at": "2026-07-23T08:06:00+00:00",
        },
        "summary": {"holding_count": 2, "total_amount": 100_000},
        "readiness": {
            "status": "reviewable",
            "valuation_eligible": True,
        },
        "holdings": [
            {
                "id": 1,
                "asset_type": "fund",
                "market": "基金",
                "code": "510300",
                "name": "沪深300ETF",
                "amount": 50_000,
                "allocation_ratio": 50,
                "decision": {
                    "action": existing_action,
                    "label": (
                        "保持仓位，按计划复核"
                        if existing_action == "hold_review"
                        else "暂停新增，复核降仓"
                    ),
                    "rationale": "测试用不可变持仓行动结论",
                    "review_amount": (
                        None if existing_action == "hold_review" else 10_000
                    ),
                    "blockers": [],
                },
                "thesis_review": {"status": "active"},
            },
            {
                "id": 2,
                "asset_type": "fund",
                "market": "基金",
                "code": "159915",
                "name": "创业板ETF",
                "amount": 50_000,
                "allocation_ratio": 50,
                "decision": {
                    "action": existing_action,
                    "label": (
                        "保持仓位，按计划复核"
                        if existing_action == "hold_review"
                        else "暂停新增，复核降仓"
                    ),
                    "rationale": "测试用不可变持仓行动结论",
                    "review_amount": (
                        None if existing_action == "hold_review" else 10_000
                    ),
                    "blockers": [],
                },
                "thesis_review": {"status": "active"},
            },
        ],
    }
    exposure = {
        "schema_version": "portfolio_exposure_snapshot.v1",
        "model_version": "exposure_interval.v1",
        "status": "complete",
        "evaluated_on": "2026-07-23",
        "profile_version_id": "ips_1",
        "holdings_sha256": portfolio_exposure.holdings_sha256(holdings),
        "summary": {
            "holding_count": 2,
            "total_amount": 100_000,
            "equity": {
                "lower_amount": 30_000,
                "upper_amount": 30_000,
                "lower_ratio": 30,
                "upper_ratio": 30,
            },
            "industry": {
                "unknown_equity_amount": 5_000,
                "unknown_equity_ratio": 5,
                "max_lower_ratio": 10,
                "max_upper_ratio": 15,
            },
            "market": {
                "unknown_equity_amount": 0,
                "unknown_equity_ratio": 0,
            },
        },
        "funds": [
            {
                "code": "510300",
                "name": "沪深300ETF",
                "amount": 50_000,
                "status": "loaded",
                "equity_interval": {
                    "lower_ratio": 30,
                    "upper_ratio": 30,
                },
                "industry_unknown_ratio": 5,
            },
            {
                "code": "159915",
                "name": "创业板ETF",
                "amount": 50_000,
                "status": "loaded",
                "equity_interval": {
                    "lower_ratio": 30,
                    "upper_ratio": 30,
                },
                "industry_unknown_ratio": 5,
            },
        ],
        "industries": [
            {
                "name": "金融",
                "lower_amount": 10_000,
                "upper_amount": 15_000,
                "contributors": [
                    {
                        "code": "510300",
                        "name": "沪深300ETF",
                        "amount": 5_000,
                    },
                    {
                        "code": "159915",
                        "name": "创业板ETF",
                        "amount": 5_000,
                    },
                ],
            }
        ],
        "markets": [
            {
                "market": "mainland",
                "lower_amount": 30_000,
                "upper_amount": 30_000,
                "contributors": [
                    {
                        "code": "510300",
                        "name": "沪深300ETF",
                        "amount": 15_000,
                    },
                    {
                        "code": "159915",
                        "name": "创业板ETF",
                        "amount": 15_000,
                    },
                ],
            }
        ],
        "quality": {
            "decision_eligible": True,
            "amount_complete": True,
            "reasons": [],
        },
        "valuation_binding": {
            "snapshot_id": "valuation_1",
            "current": True,
            "risk_analysis_eligible": True,
        },
        "snapshot": {
            "id": "exposure_1",
            "schema_version": "portfolio_exposure_snapshot.v1",
            "holdings_sha256": portfolio_exposure.holdings_sha256(
                holdings
            ),
            "profile_version_id": "ips_1",
            "status": "complete",
            "payload_sha256": "1" * 64,
            "created_at": "2026-07-23T08:07:00+00:00",
        },
        "integrity": {"verified": True},
    }
    profit_lab = {
        "schema_version": "opportunity_profit_lab.v1",
        "items": [
            {
                "strategy": {
                    "id": "strategy_1",
                    "name": "跨市场质量动量",
                    "version_id": "strategy_version_1",
                },
                "policy": {
                    "id": "profit_policy_1",
                    "values": {"primary_horizon": 20},
                },
                "evidence_cutoff_at": "2026-07-23T08:08:00+00:00",
                "horizons": [
                    {
                        "horizon_trading_days": 20,
                        "mature_count": 8,
                        "mean_net_excess_return_pct": 2.5,
                        "positive_excess_rate_pct": 75,
                        "mean_excess_ci95": {
                            "lower": 0.8,
                            "upper": 4.2,
                        },
                        "mean_excess_familywise_ci95": {
                            "lower": 0.4,
                            "upper": 4.6,
                        },
                        "worst_cohort_drawdown_pct": 8,
                    }
                ],
                "capital_gate": {
                    "status": "limited_manual_pilot",
                    "capital_eligible": True,
                    "maximum_manual_pilot_pct": 5,
                    "reasons": ["全部前瞻门禁通过"],
                },
                "capital_plan": {
                    "status": "available",
                    "basket_id": "basket_1",
                    "valuation_snapshot_id": "valuation_1",
                    "profile_version_id": "ips_1",
                    "pilot_cap_pct": 5,
                    "pilot_cap_cny": 5_000,
                    "planned_budget_cny": 5_000,
                    "positions": [
                        {
                            "market": "A股",
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "source_weight_pct": 60,
                        },
                        {
                            "market": "A股",
                            "symbol": "000858",
                            "name": "五粮液",
                            "source_weight_pct": 40,
                        },
                    ],
                    "reasons": [],
                },
                "latest_persisted": {
                    "id": "profit_score_1",
                    "payload_sha256": "2" * 64,
                    "binding_current": True,
                },
            }
        ],
    }
    return holdings, valuation, profile, report, exposure, profit_lab


def build_kwargs(*, existing_action: str = "hold_review"):
    holdings, valuation, profile, report, exposure, profit_lab = fixtures(
        existing_action=existing_action
    )
    return {
        "user_id": "owner",
        "tenant_id": "public",
        "now": dt.datetime(
            2026, 7, 23, 9, 0, tzinfo=dt.timezone.utc
        ),
        "holdings_valuation_loader": lambda: (holdings, valuation),
        "profile_loader": lambda: profile,
        "action_report_loader": lambda: report,
        "exposure_loader": (
            lambda _holdings, _profile, _valuation_id: (exposure, [])
        ),
        "profit_lab_loader": lambda: profit_lab,
        "regime_context_loader": lambda rows: {
            "engine_version": "test-regime@1",
            "evidence_sha256": "9" * 64,
            "status": "risk_on",
            "label": "偏强",
            "portfolio_risk_budget": {"multiplier": 1.0},
            "market_states": [],
            "strategy_fits": [
                {
                    "strategy_id": row.get("strategy_id"),
                    "fit_status": "neutral",
                    "allocation_tilt": 1.0,
                    "market_risk_budget_multiplier": 1.0,
                    "matched_regime": "risk_on",
                    "matched_cohort_count": 4,
                    "current_regime": {
                        "status": "risk_on",
                        "coverage_pct": 100,
                    },
                    "reasons": [],
                }
                for row in rows
            ],
            "persistence": {
                "latest_snapshot": None,
                "binding_current": False,
            },
        },
        "profit_repo": FakeProfitRepository(),
    }


def frozen_alpha_context(
    *,
    candidates=None,
    vetoes=None,
    status="paper_ready",
):
    route = {
        "schema_version": "alpha_capital_route.v1",
        "engine_version": "multi_horizon_alpha_capital_router@1.0.0",
        "status": status,
        "summary": {
            "model_invested_pct": sum(
                item.get("model_target_weight_pct", 0)
                for item in candidates or []
            ),
            "model_cash_pct": 80,
        },
        "sleeves": {
            "core_target_pct": 70,
            "satellite_target_pct": 30,
        },
        "drift": {
            "state": "baseline",
            "rebalance_review_required": False,
        },
        "candidates": candidates or [],
        "vetoes": vetoes or [],
    }
    return {
        "schema_version": "alpha_capital_current_mandate.v1",
        "current": True,
        "capital_eligible": True,
        "reason": None,
        "latest_mandate": {
            "id": "alpha_capital_1",
            "status": status,
        },
        "mandate": {
            "id": "alpha_capital_1",
            "schema_version": "alpha_capital_mandate.v1",
            "engine_version": route["engine_version"],
            "status": "paper_ready",
            "profile_version_id": "ips_1",
            "evidence_cutoff_at": "2026-07-23T08:30:00+00:00",
            "evidence_sha256": "7" * 64,
            "result_sha256": "8" * 64,
            "created_at": "2026-07-23T08:31:00+00:00",
            "result": route,
        },
    }


class PortfolioCapitalDecisionEngineTests(unittest.TestCase):
    def test_forward_qualified_candidates_receive_bounded_manual_amounts(self):
        result = service.build_capital_decision(**build_kwargs())

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            result["primary_action"]["code"], "limited_manual_pilot"
        )
        self.assertAlmostEqual(
            result["capital"]["planned_deployment_cny"], 2_250, places=2
        )
        self.assertAlmostEqual(
            result["capital"]["planned_cash_reserve_cny"], 7_750, places=2
        )
        candidates = {
            item["symbol"]: item
            for item in result["candidate_actions"]
        }
        self.assertAlmostEqual(
            candidates["600519"]["planned_amount_cny"], 1_250, places=2
        )
        self.assertAlmostEqual(
            candidates["000858"]["planned_amount_cny"], 1_000, places=2
        )
        self.assertEqual(
            result["investment_committee"]["status"], "concentrated"
        )
        self.assertEqual(
            result["investment_committee"]["summary"][
                "committee_investable_pct"
            ],
            50,
        )
        self.assertTrue(
            candidates["600519"]["committee_rank"]
            < candidates["000858"]["committee_rank"]
        )
        self.assertTrue(
            all(
                not item["execution_authorized"]
                for item in result["candidate_actions"]
            )
        )
        self.assertFalse(
            result["boundaries"]["automatic_order_creation"]
        )
        self.assertEqual(len(result["stress_matrix"]), 4)
        self.assertTrue(
            all(item["policy_passed"] for item in result["stress_matrix"])
        )

    def test_defensive_regime_reduces_downstream_capital_plan(self):
        baseline = service.build_capital_decision(**build_kwargs())
        kwargs = build_kwargs()
        neutral_loader = kwargs["regime_context_loader"]

        def defensive(rows):
            context = neutral_loader(rows)
            context["status"] = "defensive"
            context["label"] = "防守"
            context["portfolio_risk_budget"]["multiplier"] = 0.60
            for item in context["strategy_fits"]:
                item["market_risk_budget_multiplier"] = 0.60
                item["matched_regime"] = "defensive"
            return context

        kwargs["regime_context_loader"] = defensive
        defensive_result = service.build_capital_decision(**kwargs)

        committee = defensive_result["investment_committee"]
        self.assertEqual(
            committee["summary"]["base_committee_investable_pct"],
            50,
        )
        self.assertEqual(
            committee["summary"]["committee_investable_pct"],
            30,
        )
        self.assertLess(
            defensive_result["capital"]["planned_deployment_cny"],
            baseline["capital"]["planned_deployment_cny"],
        )
        self.assertEqual(
            defensive_result["data_quality"][
                "regime_risk_budget_multiplier"
            ],
            0.6,
        )

    def test_existing_reduce_review_preempts_all_new_capital(self):
        result = service.build_capital_decision(
            **build_kwargs(existing_action="reduce_review")
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["primary_action"]["code"], "reduce_review"
        )
        self.assertEqual(
            result["capital"]["planned_deployment_cny"], 0
        )
        self.assertIn(
            "existing_position_review_required",
            result["blocking_reasons"],
        )

    def test_live_gate_without_frozen_scorecard_stays_watch_only(self):
        kwargs = build_kwargs()
        original = kwargs["profit_lab_loader"]()
        original["items"][0]["latest_persisted"] = None
        kwargs["profit_lab_loader"] = lambda: original

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "watch")
        self.assertEqual(
            result["capital"]["planned_deployment_cny"], 0
        )
        self.assertEqual(
            result["data_quality"][
                "live_capital_eligible_strategy_count"
            ],
            1,
        )
        self.assertEqual(
            result["data_quality"]["eligible_strategy_count"], 0
        )

    def test_monthly_budget_is_stricter_than_global_pilot_cap(self):
        kwargs = build_kwargs()
        profile = kwargs["profile_loader"]()
        profile["monthly_budget"] = 2_000
        kwargs["profile_loader"] = lambda: profile

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            result["capital"]["global_pilot_cap_cny"], 2_000
        )
        self.assertEqual(
            result["capital"]["planned_deployment_cny"], 900
        )
        self.assertEqual(
            result["capital"]["planned_cash_reserve_cny"], 1_100
        )
        candidates = {
            item["symbol"]: item["planned_amount_cny"]
            for item in result["candidate_actions"]
        }
        self.assertEqual(candidates, {"600519": 500, "000858": 400})

    def test_confirmed_execution_consumes_budget_and_open_plan_blocks_stacking(self):
        kwargs = build_kwargs()
        kwargs["execution_summary_loader"] = lambda _as_of: {
            "schema_version": "portfolio_capital_month_execution.v1",
            "month": "2026-07",
            "confirmed_settled_amount_cny": 9_000,
            "ready_plan_count": 1,
            "latest_ready_plan": {
                "plan_id": "capital_plan_previous",
                "execution_status": "partial",
                "confirmed_settled_amount_cny": 9_000,
            },
            "blocking_reason": "previous_capital_plan_open",
            "plans": [],
        }

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["primary_action"]["code"],
            "reconcile_previous_capital_plan",
        )
        self.assertEqual(
            result["capital"]["policy_monthly_budget_cny"], 10_000
        )
        self.assertEqual(
            result["capital"]["confirmed_month_to_date_cny"], 9_000
        )
        self.assertEqual(
            result["capital"]["remaining_monthly_budget_cny"], 1_000
        )
        self.assertEqual(
            result["capital"]["planned_deployment_cny"], 0
        )
        self.assertIn(
            "previous_capital_plan_open",
            result["blocking_reasons"],
        )

    def test_unknown_candidate_industry_uses_conservative_capacity(self):
        kwargs = build_kwargs()
        profile = kwargs["profile_loader"]()
        profile["max_industry_ratio"] = 16
        kwargs["profile_loader"] = lambda: profile

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(
            result["capital"]["conservative_industry_capacity_cny"],
            2_600,
            places=2,
        )
        self.assertAlmostEqual(
            result["capital"]["planned_deployment_cny"],
            2_250,
            places=2,
        )
        candidates = {
            item["symbol"]: item["planned_amount_cny"]
            for item in result["candidate_actions"]
        }
        self.assertAlmostEqual(candidates["600519"], 1_250, places=2)
        self.assertAlmostEqual(candidates["000858"], 1_000, places=2)
        self.assertTrue(
            all(item["policy_passed"] for item in result["stress_matrix"])
        )

    def test_current_portfolio_policy_breach_blocks_new_capital(self):
        kwargs = build_kwargs()
        profile = kwargs["profile_loader"]()
        profile["max_industry_ratio"] = 10
        kwargs["profile_loader"] = lambda: profile

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["capital"]["planned_deployment_cny"], 0
        )
        self.assertIn(
            "current_portfolio_outside_policy",
            result["blocking_reasons"],
        )
        self.assertTrue(
            any(
                gate["code"] == "whole_portfolio_policy"
                and gate["status"] == "block"
                for gate in result["gates"]
            )
        )

    def test_stale_action_report_fails_closed(self):
        kwargs = build_kwargs()
        report = kwargs["action_report_loader"]()
        report["binding"]["current"] = False
        kwargs["action_report_loader"] = lambda: report

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["capital"]["planned_deployment_cny"], 0
        )
        self.assertIn(
            "portfolio_action_report_not_current",
            result["blocking_reasons"],
        )

    def test_frozen_alpha_route_can_supply_alpha_only_candidate(self):
        kwargs = build_kwargs()
        profit_lab = kwargs["profit_lab_loader"]()
        profit_lab["items"] = []
        kwargs["profit_lab_loader"] = lambda: profit_lab
        context = frozen_alpha_context(
            candidates=[
                {
                    "key": "stock:A股:300750",
                    "asset_type": "stock",
                    "market": "A股",
                    "symbol": "300750",
                    "name": "宁德时代",
                    "program_id": "alpha_program_1",
                    "run_id": "alpha_run_1",
                    "sleeve": "core",
                    "model_target_weight_pct": 20,
                    "weighted_raw_edge": 0.12,
                    "weighted_effective_edge": 0.08,
                    "weighted_reliability": 0.67,
                    "eligible_horizon_count": 3,
                    "horizons": [],
                    "capital_bridge_state": "stock_candidate",
                }
            ]
        )
        kwargs["alpha_mandate_loader"] = (
            lambda _profile, _holdings, _now: context
        )

        result = service.build_capital_decision(**kwargs)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capital"]["alpha_pilot_cap_cny"], 2_000)
        self.assertEqual(result["capital"]["global_pilot_cap_cny"], 2_000)
        candidate = result["candidate_actions"][0]
        self.assertEqual(candidate["symbol"], "300750")
        self.assertEqual(candidate["planned_amount_cny"], 400)
        self.assertTrue(candidate["calibrated_probability"])
        self.assertEqual(
            candidate["alpha_support"]["mandate_id"],
            "alpha_capital_1",
        )
        self.assertEqual(
            result["data_quality"]["alpha_candidate_count"], 1
        )

    def test_alpha_and_opportunity_support_use_max_not_sum(self):
        baseline = service.build_capital_decision(**build_kwargs())
        kwargs = build_kwargs()
        context = frozen_alpha_context(
            candidates=[
                {
                    "key": "stock:A股:600519",
                    "asset_type": "stock",
                    "market": "A股",
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "program_id": "alpha_program_1",
                    "run_id": "alpha_run_1",
                    "sleeve": "core",
                    "model_target_weight_pct": 20,
                    "weighted_raw_edge": 0.12,
                    "weighted_effective_edge": 0.08,
                    "weighted_reliability": 0.67,
                    "eligible_horizon_count": 3,
                    "horizons": [],
                    "capital_bridge_state": "stock_candidate",
                }
            ]
        )
        kwargs["alpha_mandate_loader"] = (
            lambda _profile, _holdings, _now: context
        )

        result = service.build_capital_decision(**kwargs)
        baseline_candidate = next(
            item
            for item in baseline["candidate_actions"]
            if item["symbol"] == "600519"
        )
        merged = next(
            item
            for item in result["candidate_actions"]
            if item["symbol"] == "600519"
        )

        self.assertEqual(
            merged["planned_amount_cny"],
            baseline_candidate["planned_amount_cny"],
        )
        self.assertEqual(merged["alpha_desired_amount_cny"], 400)
        self.assertIsNotNone(merged["alpha_support"])

    def test_frozen_negative_alpha_veto_preempts_opportunity_add(self):
        kwargs = build_kwargs()
        context = frozen_alpha_context(
            status="abstained",
            vetoes=[
                {
                    "asset_type": "stock",
                    "market": "A股",
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "program_id": "alpha_program_1",
                    "run_id": "alpha_run_1",
                    "state": "defensive",
                    "label": "多周期负向共识",
                    "weighted_raw_edge": -0.10,
                    "weighted_effective_edge": -0.07,
                }
            ]
        )
        kwargs["alpha_mandate_loader"] = (
            lambda _profile, _holdings, _now: context
        )

        result = service.build_capital_decision(**kwargs)
        vetoed = next(
            item
            for item in result["candidate_actions"]
            if item["symbol"] == "600519"
        )

        self.assertEqual(vetoed["planned_amount_cny"], 0)
        self.assertIn(
            "alpha_probability_veto:defensive",
            vetoed["blockers"],
        )
        self.assertEqual(
            result["alpha_probability_vetoes"][0]["symbol"],
            "600519",
        )
        self.assertFalse(
            result["boundaries"]["automatic_order_creation"]
        )

    def test_only_held_fund_with_verified_exposure_can_top_up(self):
        kwargs = build_kwargs()
        context = frozen_alpha_context(
            candidates=[
                {
                    "key": "fund:基金:510300",
                    "asset_type": "fund",
                    "market": "基金",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "program_id": "fund_program",
                    "run_id": "fund_run",
                    "sleeve": "core",
                    "model_target_weight_pct": 20,
                    "weighted_raw_edge": 0.10,
                    "weighted_effective_edge": 0.07,
                    "weighted_reliability": 0.70,
                    "eligible_horizon_count": 3,
                    "horizons": [],
                    "capital_bridge_state": "held_fund_top_up_candidate",
                },
                {
                    "key": "fund:基金:110011",
                    "asset_type": "fund",
                    "market": "基金",
                    "symbol": "110011",
                    "name": "易方达优质精选",
                    "program_id": "fund_program",
                    "run_id": "fund_run",
                    "sleeve": "core",
                    "model_target_weight_pct": 20,
                    "weighted_raw_edge": 0.09,
                    "weighted_effective_edge": 0.06,
                    "weighted_reliability": 0.68,
                    "eligible_horizon_count": 3,
                    "horizons": [],
                    "capital_bridge_state": "new_fund_due_diligence_only",
                },
            ]
        )
        kwargs["alpha_mandate_loader"] = (
            lambda _profile, _holdings, _now: context
        )

        result = service.build_capital_decision(**kwargs)
        rows = {
            item["symbol"]: item
            for item in result["candidate_actions"]
        }

        self.assertGreater(rows["510300"]["planned_amount_cny"], 0)
        self.assertNotIn(
            "new_fund_requires_due_diligence",
            rows["510300"]["blockers"],
        )
        self.assertEqual(rows["110011"]["planned_amount_cny"], 0)
        self.assertIn(
            "new_fund_requires_due_diligence",
            rows["110011"]["blockers"],
        )

    def test_stale_fund_exposure_blocks_alpha_top_up(self):
        kwargs = build_kwargs()
        exposure, _ = kwargs["exposure_loader"]([], {}, None)
        exposure["valuation_binding"]["current"] = False
        kwargs["exposure_loader"] = (
            lambda _holdings, _profile, _valuation_id: (exposure, [])
        )
        context = frozen_alpha_context(
            candidates=[
                {
                    "key": "fund:基金:510300",
                    "asset_type": "fund",
                    "market": "基金",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "program_id": "fund_program",
                    "run_id": "fund_run",
                    "sleeve": "core",
                    "model_target_weight_pct": 20,
                    "weighted_raw_edge": 0.10,
                    "weighted_effective_edge": 0.07,
                    "weighted_reliability": 0.70,
                    "eligible_horizon_count": 3,
                    "horizons": [],
                    "capital_bridge_state": "held_fund_top_up_candidate",
                }
            ]
        )
        kwargs["alpha_mandate_loader"] = (
            lambda _profile, _holdings, _now: context
        )

        result = service.build_capital_decision(**kwargs)
        candidate = next(
            item
            for item in result["candidate_actions"]
            if item["symbol"] == "510300"
        )

        self.assertEqual(candidate["planned_amount_cny"], 0)
        self.assertIn(
            "fund_exposure_not_verified",
            candidate["blockers"],
        )


class PortfolioCapitalRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(
            Path(self.tempdir.name) / "capital-plans.sqlite3"
        )
        self.repository = PortfolioCapitalRepository(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_freeze_is_idempotent_immutable_and_user_scoped(self):
        kwargs = build_kwargs()
        first, created = service.freeze_capital_decision(
            **kwargs,
            actor_id="owner",
            plan_repo=self.repository,
        )
        second, duplicate_created = service.freeze_capital_decision(
            **kwargs,
            actor_id="owner",
            plan_repo=self.repository,
        )

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["integrity"]["verified"])
        self.assertIsNone(
            self.repository.get_plan(
                first["id"],
                tenant_id="public",
                user_id="other",
            )
        )
        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE portfolio_capital_decision_plans
                    SET status='blocked' WHERE id=?
                    """,
                    (first["id"],),
                )
        finally:
            connection.close()


class PortfolioCapitalMigrationTests(unittest.TestCase):
    def test_postgres_schema_is_scoped_versioned_and_immutable(self):
        ddl = portfolio_capital_decision_v1.POSTGRES_DDL
        source = inspect.getsource(
            portfolio_capital_decision_v1.install_portfolio_capital_schema
        )
        self.assertEqual(
            portfolio_capital_decision_v1.MIGRATION_ID,
            "portfolio-capital-decision.v1",
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS portfolio_capital_decision_plans",
            ddl,
        )
        self.assertIn("tenant_id TEXT NOT NULL", ddl)
        self.assertIn("evidence_sha256 TEXT NOT NULL", ddl)
        self.assertIn(
            "UNIQUE(tenant_id, user_id, engine_version, evidence_sha256)",
            ddl,
        )
        self.assertIn("BEFORE UPDATE OR DELETE", source)
        self.assertIn("platform_schema_migrations", source)


if __name__ == "__main__":
    unittest.main()
