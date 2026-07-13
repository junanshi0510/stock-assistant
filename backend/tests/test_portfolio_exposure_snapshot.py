# -*- coding: utf-8 -*-
"""Portfolio exposure snapshots must remain conservative, immutable and auditable."""

import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_exposure  # noqa: E402
import storage  # noqa: E402
from routers import portfolio as portfolio_router  # noqa: E402
from strategies.personalized_fund_decision import evaluate_personalized_fund_decision  # noqa: E402


def source(code, *, stock_ratio=80, industries=None, stocks=None, period="2026-06-30"):
    return {
        "source": "真实基金披露测试源",
        "source_url": f"https://example.test/{code}",
        "code": code,
        "name": f"基金{code}",
        "asset_period": period,
        "stock_period": period,
        "industry_period": period,
        "asset_allocation": {"stock_ratio": stock_ratio, "bond_ratio": 100 - stock_ratio},
        "stocks": stocks or [],
        "industries": industries or [],
    }


def decision_analysis():
    return {
        "code": "000003",
        "metrics": {"annual_volatility": 18, "max_drawdown": -12},
        "timing": {"score": 65},
        "playbook": {"role": {"risk_band": "均衡偏波动"}},
        "conditioned_forward": {
            "strategy_id": "fund_conditioned_forward_return",
            "strategy_version": "1.0.0",
            "decision": "research",
            "confidence": {"level": "medium"},
            "primary_horizon": "6m",
            "horizons": [{"horizon": "6m", "analog": {"positive_rate": 60, "median_return": 5, "worst_return": -10}}],
        },
    }


def decision_context(*, equity_limit=60, industry_limit=50):
    return {
        "profile": {
            "configured": True,
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": 6000,
            "max_single_ratio": 60,
            "max_equity_ratio": equity_limit,
            "max_industry_ratio": industry_limit,
            "max_drawdown_pct": 30,
            "allowed_fund_markets": ["mainland"],
            "accept_fx_risk": False,
            "profile_version_id": "ips_v1",
        },
        "portfolio": {
            "holding_count": 2,
            "amount_complete": True,
            "total_amount": 10000,
            "holdings_sha256": "a" * 64,
        },
        "target_holding": {"exists": True, "amount": 1000, "ratio": 10},
    }


def market_profile():
    return {
        "resolution_status": "identified",
        "market": {
            "primary": "mainland",
            "label": "中国内地",
            "required_permissions": ["mainland"],
            "cross_border": False,
            "currency_risk": False,
        },
    }


def released_strategy_governance():
    return {
        "schema_version": "strategy_runtime_gate.v1",
        "strategy": {
            "strategy_id": "fund_conditioned_forward_return",
            "strategy_version": "1.0.0",
            "status": "active",
            "manifest_sha256": "c" * 64,
        },
        "execution": {
            "decision_use_allowed": True,
            "mode": "active",
            "reason_code": "strategy_released",
            "reason": "测试策略已通过发布门禁",
        },
        "release": {
            "manifest_integrity_verified": True,
            "audit_chain": {"verified": True},
            "release_ready": True,
            "required_check_count": 6,
            "passed_check_count": 6,
        },
    }


def decision_exposure(*, equity_lower=50, equity_upper=50, industry_lower=10, industry_upper=20):
    return {
        "status": "complete",
        "profile_version_id": "ips_v1",
        "target_code": "000003",
        "holdings_sha256": "a" * 64,
        "summary": {
            "equity": {
                "lower_amount": equity_lower * 100,
                "upper_amount": equity_upper * 100,
                "lower_ratio": equity_lower,
                "upper_ratio": equity_upper,
            },
            "industry": {
                "unknown_equity_amount": 500,
                "max_lower_ratio": industry_lower,
                "max_upper_ratio": industry_upper,
            },
        },
        "industries": [{"name": "信息技术", "lower_amount": 500, "lower_ratio": 5}],
        "target": {
            "status": "available",
            "equity_interval": {"lower_ratio": 80, "upper_ratio": 80, "exact": True},
            "industry_unknown_ratio": 10,
            "industries": [{"name": "信息技术", "lower_ratio": 25, "upper_ratio": 35}],
        },
        "quality": {"decision_eligible": True, "reasons": []},
        "snapshot": {"id": "exposure_v1", "payload_sha256": "b" * 64},
        "integrity": {"verified": True},
    }


class PortfolioExposureModelTests(unittest.TestCase):
    def test_cross_market_exposure_and_unknown_industry_are_not_hidden(self):
        holdings = [
            {"asset_type": "fund", "market": "基金", "code": "000001", "amount": 6000},
            {"asset_type": "fund", "market": "基金", "code": "000002", "amount": 3000},
            {"asset_type": "stock", "market": "港股", "code": "00700", "name": "腾讯控股", "amount": 1000},
        ]
        sources = {
            "000001": source(
                "000001",
                stock_ratio=80,
                stocks=[
                    {"code": "600000", "name": "浦发银行", "nav_ratio": 10},
                    {"code": "00700", "name": "腾讯控股", "nav_ratio": 10},
                    {"code": "MSFT", "name": "Microsoft", "nav_ratio": 5},
                ],
                industries=[
                    {"name": "信息技术", "nav_ratio": 40},
                    {"name": "医药生物", "nav_ratio": 30},
                ],
            ),
            "000002": source(
                "000002",
                stock_ratio=20,
                industries=[{"name": "信息技术", "nav_ratio": 5}],
            ),
            "000003": source(
                "000003",
                stock_ratio=70,
                industries=[{"name": "信息技术", "nav_ratio": 60}],
            ),
        }

        result = portfolio_exposure.build_exposure_snapshot(
            holdings,
            sources,
            target_code="000003",
            profile_version_id="ips_v1",
            observed_on=dt.date(2026, 7, 13),
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["summary"]["equity"]["lower_ratio"], 64)
        self.assertEqual(result["summary"]["equity"]["upper_ratio"], 64)
        self.assertEqual(result["summary"]["industry"]["unknown_equity_amount"], 2050)
        market_names = {row["market"] for row in result["markets"]}
        self.assertEqual(market_names, {"mainland", "hong_kong", "united_states"})
        self.assertGreater(result["summary"]["market"]["unknown_equity_amount"], 0)
        self.assertTrue(result["quality"]["decision_eligible"])

    def test_stale_or_missing_target_disclosure_never_becomes_decision_eligible(self):
        holdings = [{"asset_type": "fund", "code": "000001", "amount": 10000}]
        stale = source("000001", period="2025-06-30")
        result = portfolio_exposure.build_exposure_snapshot(
            holdings,
            {"000001": stale},
            target_code="000003",
            observed_on=dt.date(2026, 7, 13),
        )

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["quality"]["decision_eligible"])
        self.assertEqual(result["target"]["status"], "unavailable")
        self.assertTrue(result["quality"]["reasons"])

    def test_zero_equity_bond_fund_does_not_require_industry_disclosure(self):
        bond_source = source("000001", stock_ratio=0, industries=[], period="2026-06-30")
        bond_source["industry_period"] = ""
        result = portfolio_exposure.build_exposure_snapshot(
            [{"asset_type": "fund", "code": "000001", "amount": 10000}],
            {"000001": bond_source},
            observed_on=dt.date(2026, 7, 13),
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["summary"]["equity"]["upper_ratio"], 0)
        self.assertTrue(result["quality"]["decision_eligible"])

    def test_equity_capacity_uses_worst_case_upper_bound(self):
        result = evaluate_personalized_fund_decision(
            decision_analysis(),
            decision_context(equity_limit=60),
            market_profile(),
            decision_exposure(),
            released_strategy_governance(),
            planned_amount=6000,
        )

        self.assertEqual(result["decision"]["action"], "consider_tranche")
        self.assertEqual(result["budget"]["maximum_additional_by_equity_limit"], 5000)
        self.assertEqual(result["budget"]["allowed_full_amount"], 5000)

    def test_industry_lower_bound_breach_blocks_new_risk(self):
        result = evaluate_personalized_fund_decision(
            decision_analysis(),
            decision_context(industry_limit=30),
            market_profile(),
            decision_exposure(industry_lower=35, industry_upper=45),
            released_strategy_governance(),
            planned_amount=1000,
        )

        self.assertEqual(result["decision"]["action"], "reduce_exposure")
        self.assertIsNone(result["budget"]["allowed_full_amount"])
        gate = next(item for item in result["gates"] if item["code"] == "industry_exposure_limit")
        self.assertEqual(gate["status"], "block")

    def test_uncertain_industry_upper_bound_abstains_instead_of_assuming_diversification(self):
        result = evaluate_personalized_fund_decision(
            decision_analysis(),
            decision_context(industry_limit=30),
            market_profile(),
            decision_exposure(industry_lower=20, industry_upper=40),
            released_strategy_governance(),
            planned_amount=1000,
        )

        self.assertEqual(result["decision"]["action"], "exposure_data_required")
        self.assertEqual(result["status"], "abstained")
        self.assertIsNone(result["budget"]["first_tranche_amount"])


class PortfolioExposureStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = storage._DB_PATH
        self.old_conn = storage._conn
        storage._DB_PATH = str(Path(self.temp_dir.name) / "exposure.db")
        storage._conn = None

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.old_conn
        storage._DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_snapshot_is_content_addressed_immutable_and_hash_verified(self):
        payload = portfolio_exposure.build_exposure_snapshot(
            [{"asset_type": "fund", "code": "000001", "amount": 10000}],
            {"000001": source("000001")},
            observed_on=dt.date(2026, 7, 13),
        )
        first = storage.save_portfolio_exposure_snapshot(payload)
        second = storage.save_portfolio_exposure_snapshot(payload)

        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])
        self.assertTrue(storage.verify_portfolio_exposure_snapshot(first["id"])["verified"])
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE portfolio_exposure_snapshots SET status='changed' WHERE id=?",
                (first["id"],),
            )

    def test_provider_failure_is_persisted_as_partial_without_fallback(self):
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "测试基金",
            "amount": 10000,
            "source": "manual",
        })

        def unavailable(_code):
            raise RuntimeError("provider timeout")

        result = portfolio_exposure.refresh_exposure_snapshot(
            target_code="000001",
            provider=unavailable,
            observed_on=dt.date(2026, 7, 13),
        )

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["quality"]["decision_eligible"])
        self.assertEqual(result["failed_sources"][0]["error"], "provider timeout")
        self.assertTrue(result["integrity"]["verified"])


class PortfolioExposureApiTests(unittest.TestCase):
    def test_refresh_endpoint_pins_current_active_profile_version_server_side(self):
        expected = {"status": "complete", "snapshot": {"id": "exposure_api"}}
        with (
            patch.object(
                portfolio_router.storage,
                "get_investment_profile",
                return_value={"configured": True, "profile_version_id": "ips_active"},
            ),
            patch.object(
                portfolio_router.portfolio_exposure,
                "refresh_exposure_snapshot",
                return_value=expected,
            ) as refresh,
        ):
            result = portfolio_router.create_holdings_exposure_snapshot(
                portfolio_router.PortfolioExposureSnapshotRequest(target_code="000003")
            )

        self.assertEqual(result, expected)
        refresh.assert_called_once_with(
            target_code="000003",
            profile_version_id="ips_active",
        )


if __name__ == "__main__":
    unittest.main()
