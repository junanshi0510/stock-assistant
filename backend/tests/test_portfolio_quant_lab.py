# -*- coding: utf-8 -*-
"""Portfolio quant lab must be out-of-sample, cost-aware and auditable."""

from __future__ import annotations

import inspect
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from auth import AuthPrincipal  # noqa: E402
from migrations import portfolio_quant_lab_v1 as quant_migration  # noqa: E402
import portfolio_quant_service as service  # noqa: E402
import portfolio_valuation  # noqa: E402
from portfolio_quant_repository import (  # noqa: E402
    PortfolioQuantConflictError,
    PortfolioQuantRepository,
)
from routers import portfolio as portfolio_router  # noqa: E402
from task_queue import (  # noqa: E402
    QUEUE_MARKET,
    TASK_PORTFOLIO_QUANT_RUN,
    celery_app,
)


def quant_policy(**overrides) -> dict:
    raw = {
        "construction_method": "risk_parity",
        "lookback_days": 126,
        "rebalance_days": 21,
        "commission_bps": 5,
        "slippage_bps": 10,
        "sell_tax_bps": 0,
        "max_turnover_pct": 100,
        "max_position_pct": 70,
        "minimum_trade_amount_cny": 1000,
        **overrides,
    }
    policy = service.normalize_policy(raw)
    policy.update(
        {
            "requested_max_position_pct": float(
                policy["max_position_pct"]
            ),
            "effective_total_portfolio_position_cap_pct": float(
                policy["max_position_pct"]
            ),
            "effective_stock_sleeve_position_cap_pct": float(
                policy["max_position_pct"]
            ),
            "profile_cap_applied": False,
        }
    )
    return policy


def quant_holdings(*, multi_market: bool = False) -> list[dict]:
    return [
        {
            "holding_id": 1,
            "asset_type": "stock",
            "market": "A股",
            "code": "000001",
            "name": "高波动持仓",
            "amount_cny": 80_000,
        },
        {
            "holding_id": 2,
            "asset_type": "stock",
            "market": "美股" if multi_market else "A股",
            "code": "000002",
            "name": "低波动甲",
            "amount_cny": 10_000,
        },
        {
            "holding_id": 3,
            "asset_type": "stock",
            "market": "A股",
            "code": "000003",
            "name": "低波动乙",
            "amount_cny": 10_000,
        },
    ]


def quant_evidence(
    *,
    holdings: list[dict] | None = None,
    holdings_sha256: str = "a" * 64,
) -> dict:
    eligible = holdings or quant_holdings()
    return {
        "schema_version": "portfolio_quant_evidence.v1",
        "holdings_sha256": holdings_sha256,
        "eligible_holdings": eligible,
        "excluded_holdings": [],
        "stock_sleeve": {
            "value_cny": 100_000,
            "portfolio_value_cny": 100_000,
            "portfolio_weight_pct": 100,
            "eligible_count": len(eligible),
            "excluded_count": 0,
        },
        "profile": {
            "configured": True,
            "profile_version_id": "profile-v1",
        },
        "valuation": {
            "snapshot_id": "valuation-v1",
            "risk_analysis_eligible": True,
            "trade_amount_eligible": True,
            "integrity_verified": True,
        },
        "known_limitations": [],
    }


def create_quant_run(
    repository: PortfolioQuantRepository,
    *,
    user_id: str = "owner",
    policy: dict | None = None,
    evidence: dict | None = None,
) -> dict:
    active_evidence = evidence or quant_evidence()
    return repository.create_run(
        tenant_id="public",
        user_id=user_id,
        actor_id=user_id,
        engine_version=service.ENGINE_VERSION,
        holdings_sha256=active_evidence["holdings_sha256"],
        profile_version_id="profile-v1",
        valuation_snapshot_id="valuation-v1",
        policy=policy or quant_policy(),
        evidence=active_evidence,
    )


DATES = pd.bdate_range(
    end=pd.Timestamp.now().normalize(),
    periods=650,
)
TIME_INDEX = np.arange(len(DATES))
RETURNS = {
    "000001": (
        0.0005
        + 0.023 * np.sin(TIME_INDEX * 0.37)
        + 0.006 * np.cos(TIME_INDEX * 0.11)
    ),
    "000002": (
        0.00035 + 0.004 * np.sin(TIME_INDEX * 0.19 + 1.2)
    ),
    "000003": (
        0.0003 + 0.005 * np.cos(TIME_INDEX * 0.23 + 2.4)
    ),
}


def synthetic_price_loader(
    market: str,
    code: str,
    months: int,
) -> tuple[pd.DataFrame, str]:
    del market, months
    close = 100 * np.cumprod(1 + RETURNS[code])
    return pd.DataFrame({"date": DATES, "close": close}), "Tushare"


class PortfolioQuantEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = PortfolioQuantRepository(
            Path(self.temp.name) / "quant.sqlite3"
        )

    def tearDown(self):
        self.temp.cleanup()

    def execute(
        self,
        *,
        policy: dict | None = None,
        evidence: dict | None = None,
        user_id: str = "owner",
    ) -> dict:
        run = create_quant_run(
            self.repository,
            user_id=user_id,
            policy=policy,
            evidence=evidence,
        )
        return service.execute_run(
            run["id"],
            tenant_id="public",
            user_id=user_id,
            repo=self.repository,
            price_loader=synthetic_price_loader,
        )

    def test_walk_forward_is_strict_cost_aware_and_risk_only(self):
        completed = self.execute()
        result = completed["result"]

        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(completed["integrity"]["verified"])
        self.assertGreaterEqual(result["walk_forward"]["fold_count"], 6)
        for fold in result["walk_forward"]["folds"]:
            self.assertLess(
                pd.Timestamp(fold["train_end"]),
                pd.Timestamp(fold["test_start"]),
            )
            self.assertEqual(fold["train_days"], 126)
            self.assertEqual(fold["test_days"], 21)

        models = {item["method"]: item for item in result["models"]}
        self.assertEqual(
            set(models),
            {
                "current_weights",
                "equal_weight",
                "inverse_volatility",
                "risk_parity",
                "minimum_variance",
            },
        )
        self.assertEqual(result["selected_method"], "risk_parity")
        self.assertEqual(
            [item["method"] for item in result["models"] if item["selected"]],
            ["risk_parity"],
        )
        self.assertEqual(
            result["methodology"]["optimization"],
            "只优化协方差与风险贡献，不使用历史收益率预测，也不按样本外结果自动挑选最佳模型",
        )
        self.assertLess(
            models["risk_parity"]["risk"]["risk_concentration_hhi"],
            models["current_weights"]["risk"]["risk_concentration_hhi"],
        )
        self.assertTrue(
            all(
                weight <= 70.0001
                for weight in models["risk_parity"][
                    "latest_stock_sleeve_weights_pct"
                ]
            )
        )
        self.assertGreater(
            models["risk_parity"]["performance"][
                "estimated_cost_drag_pct"
            ],
            0,
        )
        self.assertEqual(
            result["promotion_gate"]["status"], "paper_ready"
        )
        self.assertFalse(result["target"]["execution_authorized"])
        self.assertTrue(
            all(
                not item["quantity_generated"]
                for item in result["target"]["actions"]
            )
        )
        self.assertTrue(
            all(
                len(item["price_sha256"]) == 64
                for item in result["market_data"]
            )
        )

    def test_higher_cost_reduces_same_model_net_result(self):
        zero = self.execute(
            policy=quant_policy(
                commission_bps=0,
                slippage_bps=0,
                sell_tax_bps=0,
            ),
            user_id="zero-cost",
        )
        expensive = self.execute(
            policy=quant_policy(
                commission_bps=50,
                slippage_bps=100,
                sell_tax_bps=50,
            ),
            user_id="high-cost",
        )
        zero_selected = zero["result"]["selected_comparison"]["selected"]
        expensive_selected = expensive["result"][
            "selected_comparison"
        ]["selected"]
        self.assertGreater(
            zero_selected["cumulative_return_pct"],
            expensive_selected["cumulative_return_pct"],
        )
        self.assertGreater(
            expensive_selected["estimated_cost_drag_pct"],
            zero_selected["estimated_cost_drag_pct"],
        )

    def test_cross_market_run_is_research_only_without_fx_history(self):
        holdings = quant_holdings(multi_market=True)
        completed = self.execute(
            evidence=quant_evidence(holdings=holdings),
            user_id="multi-market",
        )
        gate = completed["result"]["promotion_gate"]
        self.assertFalse(gate["paper_mandate_eligible"])
        self.assertIn("single_market_fx_boundary", gate["failed_codes"])

    def test_free_fallback_history_can_be_researched_but_not_frozen(self):
        def fallback_loader(market, code, months):
            frame, _ = synthetic_price_loader(market, code, months)
            return frame, "Yahoo Finance"

        run = create_quant_run(
            self.repository,
            user_id="fallback-source",
        )
        completed = service.execute_run(
            run["id"],
            tenant_id="public",
            user_id="fallback-source",
            repo=self.repository,
            price_loader=fallback_loader,
        )
        gate = completed["result"]["promotion_gate"]
        self.assertFalse(gate["paper_mandate_eligible"])
        self.assertIn(
            "professional_history_sources",
            gate["failed_codes"],
        )


class PortfolioQuantRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "quant.sqlite3"
        self.repository = PortfolioQuantRepository(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_inputs_results_and_event_chain_are_scoped_and_immutable(self):
        run = create_quant_run(self.repository)
        self.assertIsNone(
            self.repository.get_run(
                run["id"],
                tenant_id="public",
                user_id="another-user",
            )
        )
        self.repository.mark_running(
            run["id"],
            tenant_id="public",
            user_id="owner",
            actor_id="worker",
        )
        completed = self.repository.complete_run(
            run["id"],
            tenant_id="public",
            user_id="owner",
            result={
                "data_quality": {
                    "eligible_asset_count": 3,
                    "requested_asset_count": 3,
                },
                "promotion_gate": {"status": "research_only"},
                "target": {"execution_authorized": False},
            },
            status="succeeded",
            actor_id="worker",
        )

        self.assertTrue(completed["audit"]["verified"])
        self.assertEqual(completed["audit"]["event_count"], 3)
        self.assertTrue(completed["result_verified"])
        with closing(sqlite3.connect(self.path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE portfolio_quant_runs "
                    "SET policy_json='{}' WHERE id=?",
                    (run["id"],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE portfolio_quant_runs "
                    "SET result_json='{}' WHERE id=?",
                    (run["id"],),
                )
            event_id = completed["events"][0]["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM portfolio_quant_run_events WHERE id=?",
                    (event_id,),
                )

    def test_paper_mandate_rechecks_bindings_and_is_idempotent(self):
        current_holdings = [
            {
                "id": 1,
                "asset_type": "stock",
                "market": "A股",
                "code": "000001",
                "name": "测试持仓",
                "amount": 100_000,
            }
        ]
        holdings_hash = portfolio_valuation.holdings_fingerprint(
            current_holdings
        )
        run = create_quant_run(
            self.repository,
            evidence=quant_evidence(holdings_sha256=holdings_hash),
        )
        self.repository.mark_running(
            run["id"],
            tenant_id="public",
            user_id="owner",
            actor_id="worker",
        )
        completed = self.repository.complete_run(
            run["id"],
            tenant_id="public",
            user_id="owner",
            result={
                "selected_method": "risk_parity",
                "data_quality": {
                    "eligible_asset_count": 3,
                    "requested_asset_count": 3,
                },
                "promotion_gate": {
                    "status": "paper_ready",
                    "paper_mandate_eligible": True,
                    "execution_authorized": False,
                },
                "target": {
                    "schema_version": "portfolio_quant_target.v1",
                    "execution_authorized": False,
                    "actions": [],
                },
            },
            status="succeeded",
            actor_id="worker",
        )
        valuation = {
            "snapshot": {"id": "valuation-v1"},
            "runtime_gate": {"trade_amount_eligible": True},
        }
        profile = {
            "configured": True,
            "profile_version_id": "profile-v1",
        }
        with (
            patch.object(
                service.storage,
                "list_holdings",
                return_value=current_holdings,
            ),
            patch.object(
                service.portfolio_valuation,
                "latest_portfolio_valuation",
                return_value=valuation,
            ),
            patch.object(
                service.storage,
                "get_investment_profile",
                return_value=profile,
            ),
        ):
            first, created = service.freeze_mandate(
                run["id"],
                acknowledged=True,
                expected_result_sha256=completed["result_sha256"],
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                repo=self.repository,
            )
            second, created_again = service.freeze_mandate(
                run["id"],
                acknowledged=True,
                expected_result_sha256=completed["result_sha256"],
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                repo=self.repository,
            )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["integrity"]["verified"])
        self.assertFalse(first["target"]["execution_authorized"])
        with closing(sqlite3.connect(self.path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM portfolio_quant_mandates WHERE id=?",
                    (first["id"],),
                )


class PortfolioQuantApiTests(unittest.TestCase):
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

    def test_create_route_passes_subject_scope_and_strict_policy(self):
        request = portfolio_router.PortfolioQuantRunRequest(
            construction_method="minimum_variance",
            lookback_days=252,
            rebalance_days=63,
        )
        expected = {"id": "quant-route", "status": "queued"}
        with patch.object(
            portfolio_router.portfolio_quant_service,
            "start_run",
            return_value=expected,
        ) as start:
            result = portfolio_router.create_portfolio_quant_run(
                request,
                principal=self.principal(),
            )

        self.assertEqual(result, expected)
        kwargs = start.call_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "public")
        self.assertEqual(kwargs["user_id"], "portfolio-owner-a")
        self.assertEqual(kwargs["actor_id"], "actor-a")
        self.assertEqual(
            start.call_args.args[0]["construction_method"],
            "minimum_variance",
        )

    def test_mandate_route_maps_stale_hash_to_conflict(self):
        request = portfolio_router.PortfolioQuantMandateRequest(
            acknowledged=True,
            expected_result_sha256="f" * 64,
        )
        with (
            patch.object(
                portfolio_router.portfolio_quant_service,
                "freeze_mandate",
                side_effect=PortfolioQuantConflictError("结果已变化"),
            ),
            self.assertRaises(HTTPException) as caught,
        ):
            portfolio_router.create_portfolio_quant_mandate(
                "quant-stale",
                request,
                principal=self.principal(),
            )
        self.assertEqual(caught.exception.status_code, 409)


class PortfolioQuantProductionContractTests(unittest.TestCase):
    def test_postgres_schema_and_queue_route_are_declared(self):
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS portfolio_quant_runs",
            quant_migration.POSTGRES_DDL,
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS portfolio_quant_mandates",
            quant_migration.POSTGRES_DDL,
        )
        source = inspect.getsource(
            quant_migration.install_portfolio_quant_lab_schema
        )
        self.assertIn("BEFORE UPDATE OR DELETE", source)
        self.assertEqual(
            celery_app.conf.task_routes[TASK_PORTFOLIO_QUANT_RUN]["queue"],
            QUEUE_MARKET,
        )


if __name__ == "__main__":
    unittest.main()
