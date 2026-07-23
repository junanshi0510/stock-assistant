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
