# -*- coding: utf-8 -*-
"""Decision twin must be conservative, deterministic, scoped and immutable."""

from __future__ import annotations

import sqlite3
import inspect
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_decision_twin as twin  # noqa: E402
import portfolio_exposure  # noqa: E402
from auth import AuthPrincipal  # noqa: E402
from migrations import portfolio_decision_twin_v1 as twin_migration  # noqa: E402
from portfolio_twin_repository import PortfolioTwinRepository  # noqa: E402
from routers import portfolio as portfolio_router  # noqa: E402


def profile(**overrides):
    return {
        "configured": True,
        "profile_version_id": "ips_v1",
        "max_drawdown_pct": 15,
        "max_single_ratio": 70,
        "max_equity_ratio": 100,
        "max_industry_ratio": 60,
        **overrides,
    }


def exposure_for(holdings, **overrides):
    value = portfolio_exposure.build_exposure_snapshot(holdings, {})
    value.update(overrides)
    value["snapshot"] = {"id": "exposure_v1", "payload_sha256": "e" * 64}
    value["integrity"] = {"verified": True}
    return value


def scenario(*, a=-10, hk=-10, us=-10, unknown=-20, budget=15, targets=None):
    return {
        "name": "确定性测试情景",
        "market_shocks": [
            {"market": "mainland", "shock_pct": a},
            {"market": "hong_kong", "shock_pct": hk},
            {"market": "united_states", "shock_pct": us},
            {"market": "global", "shock_pct": us},
            {"market": "unknown", "shock_pct": unknown},
        ],
        "loss_budget_pct": budget,
        "hypothetical_positions": targets or [],
    }


class PortfolioDecisionTwinEngineTests(unittest.TestCase):
    def test_exact_cross_market_stocks_have_exact_first_order_pnl(self):
        holdings = [
            {"id": 1, "asset_type": "stock", "market": "A股", "code": "600519", "name": "甲", "amount": 6000},
            {"id": 2, "asset_type": "stock", "market": "美股", "code": "AAPL", "name": "乙", "amount": 3000},
            {"id": 3, "asset_type": "cash", "market": "", "code": "CNY", "name": "现金", "amount": 1000},
        ]
        result = twin.build_decision_twin(
            holdings=holdings,
            exposure=exposure_for(holdings),
            profile=profile(),
            scenario=scenario(a=-10, us=-20, unknown=-30),
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["current"]["pnl_interval"]["lower_amount"], -1200)
        self.assertEqual(result["current"]["pnl_interval"]["upper_amount"], -1200)
        self.assertFalse(result["current"]["risk_budget"]["breached"])
        self.assertEqual(result["current"]["allocation"]["cash_ratio"], 10)

    def test_missing_fund_classification_widens_interval_instead_of_imputation(self):
        holdings = [
            {"id": 8, "asset_type": "fund", "market": "基金", "code": "000001", "name": "测试基金", "amount": 10000},
        ]
        exposure = {
            "holdings_sha256": portfolio_exposure.holdings_sha256(holdings),
            "markets": [
                {"market": "mainland", "contributors": [{"code": "000001", "amount": 2000}]}
            ],
            "industries": [
                {"name": "信息技术", "contributors": [{"code": "000001", "amount": 2000}]}
            ],
            "funds": [
                {
                    "code": "000001",
                    "equity_interval": {"lower_ratio": 20, "upper_ratio": 80},
                    "industry_unknown_ratio": 60,
                }
            ],
            "quality": {"decision_eligible": False, "reasons": ["披露区间不完整"]},
            "snapshot": {"id": "exposure_partial", "payload_sha256": "f" * 64},
            "integrity": {"verified": True},
        }
        value = scenario(a=-10, us=0, unknown=-30, budget=30)
        value["industry_shocks"] = [{"industry": "信息技术", "shock_pct": -5}]

        result = twin.build_decision_twin(
            holdings=holdings,
            exposure=exposure,
            profile=profile(max_drawdown_pct=30),
            scenario=value,
        )

        interval = result["current"]["pnl_interval"]
        self.assertEqual(result["status"], "partial")
        self.assertLess(interval["lower_amount"], interval["upper_amount"])
        self.assertGreater(interval["width_amount"], 0)
        self.assertFalse(result["decision_gate"]["decision_eligible"])

    def test_what_if_comparison_reverse_stress_and_minimum_notional_repair(self):
        holdings = [
            {"id": 1, "asset_type": "stock", "market": "A股", "code": "600000", "name": "高风险仓", "amount": 6000},
            {"id": 2, "asset_type": "stock", "market": "美股", "code": "MSFT", "name": "低风险仓", "amount": 3000},
            {"id": 3, "asset_type": "cash", "market": "", "code": "CNY", "name": "现金", "amount": 1000},
        ]
        raw = scenario(a=-20, us=-5, unknown=-25, budget=10)
        result = twin.build_decision_twin(
            holdings=holdings,
            exposure=exposure_for(holdings),
            profile=profile(max_drawdown_pct=30),
            scenario=raw,
        )

        self.assertTrue(result["current"]["risk_budget"]["breached"])
        self.assertEqual(result["repair_plan"]["status"], "available")
        self.assertEqual(result["repair_plan"]["actions"][0]["holding_id"], "1")
        self.assertAlmostEqual(result["repair_plan"]["total_shift_to_cash"], 1750, places=1)
        self.assertFalse(result["repair_plan"]["after"]["breached"])
        self.assertEqual(result["reverse_stress"]["status"], "already_breached")

        what_if = scenario(
            a=-20,
            us=-5,
            unknown=-25,
            budget=10,
            targets=[{"holding_id": 1, "target_amount": 3000}],
        )
        changed = twin.build_decision_twin(
            holdings=holdings,
            exposure=exposure_for(holdings),
            profile=profile(max_drawdown_pct=30),
            scenario=what_if,
        )
        self.assertTrue(changed["comparison"]["what_if_changed"])
        self.assertGreater(changed["comparison"]["worst_loss_improvement_amount"], 0)
        self.assertEqual(changed["proposed"]["allocation"]["cash_ratio"], 40)

    def test_mixed_direction_scenario_does_not_publish_non_monotonic_threshold(self):
        holdings = [
            {"id": 1, "asset_type": "stock", "market": "A股", "code": "600000", "name": "风险仓", "amount": 7000},
            {"id": 2, "asset_type": "stock", "market": "美股", "code": "MSFT", "name": "对冲仓", "amount": 3000},
        ]
        raw = scenario(a=-20, us=10, unknown=-25, budget=10)

        result = twin.build_decision_twin(
            holdings=holdings,
            exposure=exposure_for(holdings),
            profile=profile(),
            scenario=raw,
        )

        self.assertEqual(
            result["reverse_stress"]["status"], "unsupported_mixed_direction"
        )
        self.assertNotIn("breach_multiplier", result["reverse_stress"])

    def test_what_if_cannot_create_leverage_or_external_funding(self):
        holdings = [
            {"id": 1, "asset_type": "stock", "market": "A股", "code": "600000", "name": "持仓", "amount": 10000},
        ]
        raw = scenario(
            targets=[{"holding_id": 1, "target_amount": 10001}],
        )
        with self.assertRaisesRegex(ValueError, "不允许隐含杠杆或外部注资"):
            twin.build_decision_twin(
                holdings=holdings,
                exposure=exposure_for(holdings),
                profile=profile(),
                scenario=raw,
            )


class PortfolioTwinRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "twin.db"
        self.repository = PortfolioTwinRepository(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_run_is_hash_verified_immutable_and_tenant_scoped(self):
        created = self.repository.create_run(
            tenant_id="tenant-a",
            user_id="owner",
            actor_id="owner",
            method_version=twin.METHOD_VERSION,
            status="complete",
            scenario={"name": "test"},
            holdings=[{"id": 1, "amount": 100}],
            exposure={"snapshot": {"id": "e1"}},
            profile={"profile_version_id": "p1"},
            result={"status": "complete"},
        )

        self.assertTrue(created["integrity"]["verified"])
        self.assertIsNone(
            self.repository.get_run(
                created["id"], tenant_id="tenant-b", user_id="owner"
            )
        )
        self.assertEqual(
            self.repository.list_runs(tenant_id="tenant-a", user_id="owner")[0]["id"],
            created["id"],
        )
        summary = self.repository.list_runs(tenant_id="tenant-a", user_id="owner")[0]
        self.assertNotIn("result", summary)
        self.assertFalse(summary["integrity"]["verified"])
        self.assertTrue(summary["integrity"]["available_checks_verified"])
        self.assertFalse(summary["integrity"]["checks_complete"])
        with closing(sqlite3.connect(self.path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE portfolio_twin_runs SET status='partial' WHERE id=?",
                    (created["id"],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM portfolio_twin_runs WHERE id=?", (created["id"],)
                )


class PortfolioDecisionTwinApiTests(unittest.TestCase):
    @staticmethod
    def principal() -> AuthPrincipal:
        return AuthPrincipal(
            user_id="actor-a",
            subject_id="portfolio-owner-a",
            username="owner-a",
            display_name="Owner A",
            role="user",
            must_change_password=False,
            session_id="session-a",
        )

    def test_create_route_refreshes_scoped_exposure_and_persists_frozen_run(self):
        holdings = [
            {"id": 1, "asset_type": "stock", "market": "A股", "code": "600000", "name": "持仓", "amount": 10000},
        ]
        policy = profile(profile_version_id="ips-route")
        exposure = exposure_for(holdings)
        request = portfolio_router.PortfolioTwinRunRequest(
            name="路由闭环",
            market_shocks=[
                {"market": "mainland", "shock_pct": -10},
                {"market": "hong_kong", "shock_pct": -10},
                {"market": "united_states", "shock_pct": -10},
                {"market": "global", "shock_pct": -10},
                {"market": "unknown", "shock_pct": -20},
            ],
            loss_budget_pct=15,
        )
        persisted = {"id": "portfolio_twin_route", "status": "complete"}
        actor = self.principal()

        with (
            patch.object(
                portfolio_router.storage,
                "get_investment_profile",
                return_value=policy,
            ) as profile_mock,
            patch.object(
                portfolio_router,
                "_call_portfolio_data",
                return_value=exposure,
            ) as exposure_mock,
            patch.object(
                portfolio_router.portfolio_valuation,
                "current_valued_holdings",
                return_value=(holdings, {
                    "status": "available",
                    "snapshot": {"id": "valuation-route"},
                    "binding": {"current": True},
                    "runtime_gate": {"risk_analysis_eligible": True},
                }),
            ) as holdings_mock,
            patch.object(
                portfolio_router.portfolio_twin_repository,
                "create_run",
                return_value=persisted,
            ) as create_mock,
        ):
            result = portfolio_router.create_portfolio_twin_run(
                request,
                principal=actor,
            )

        self.assertEqual(result, persisted)
        profile_mock.assert_called_once_with(user_id="portfolio-owner-a")
        holdings_mock.assert_called_once_with(
            user_id="portfolio-owner-a",
            tenant_id="public",
            repository=portfolio_router.portfolio_valuation_repository,
        )
        exposure_mock.assert_called_once_with(
            "portfolio.exposure_snapshot",
            {"profile_version_id": "ips-route"},
            principal=actor,
            error_prefix="组合数字孪生的真实暴露快照生成失败",
        )
        frozen = create_mock.call_args.kwargs
        self.assertEqual(frozen["tenant_id"], "public")
        self.assertEqual(frozen["user_id"], "portfolio-owner-a")
        self.assertEqual(frozen["actor_id"], "actor-a")
        self.assertEqual(frozen["holdings"], holdings)
        self.assertEqual(frozen["exposure"], exposure)
        self.assertEqual(frozen["profile"], policy)
        self.assertEqual(frozen["result"]["method_version"], twin.METHOD_VERSION)

    def test_create_route_rejects_stale_valuation_before_fetching_exposure(self):
        request = portfolio_router.PortfolioTwinRunRequest(
            name="过期估值门禁",
            market_shocks=[{"market": "mainland", "shock_pct": -10}],
            loss_budget_pct=15,
        )
        valuation = {
            "status": "available",
            "snapshot": {"id": "valuation-old"},
            "binding": {"current": False},
            "runtime_gate": {
                "risk_analysis_eligible": False,
                "reasons": ["持仓已变化"],
            },
        }
        with (
            patch.object(
                portfolio_router.storage,
                "get_investment_profile",
                return_value=profile(),
            ),
            patch.object(
                portfolio_router.portfolio_valuation,
                "current_valued_holdings",
                return_value=([{"id": 1, "amount": 1000}], valuation),
            ),
            patch.object(portfolio_router, "_call_portfolio_data") as exposure_mock,
            self.assertRaises(HTTPException) as caught,
        ):
            portfolio_router.create_portfolio_twin_run(
                request,
                principal=self.principal(),
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("持仓已变化", str(caught.exception.detail))
        exposure_mock.assert_not_called()


class PortfolioDecisionTwinMigrationTests(unittest.TestCase):
    def test_postgres_schema_declares_immutable_scoped_runs(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS portfolio_twin_runs", twin_migration.POSTGRES_DDL)
        self.assertIn("tenant_id TEXT NOT NULL", twin_migration.POSTGRES_DDL)
        self.assertIn("idx_portfolio_twin_runs_scope", twin_migration.POSTGRES_DDL)
        self.assertIn(
            "BEFORE UPDATE OR DELETE",
            inspect.getsource(twin_migration.install_portfolio_twin_schema),
        )


if __name__ == "__main__":
    unittest.main()
