# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

import opportunity_profit_service as profit_service
import opportunity_service
from migrations import opportunity_profit_engine_v1
from opportunity_profit_repository import OpportunityProfitRepository
from opportunity_repository import OpportunityRepository


def strategy_definition(name: str = "前瞻收益策略") -> dict:
    return opportunity_service.normalize_definition(
        {
            "template_id": "custom",
            "name": name,
            "description": "用于验证成本后前瞻收益和基准超额的固定策略",
            "markets": ["A股"],
            "history_months": 18,
            "universe": {
                "include_presets": False,
                "include_watchlist": False,
                "hot_lists": [],
                "hot_limit_per_market": 8,
                "symbols": [
                    {"market": "A股", "symbol": "600519", "name": "贵州茅台"}
                ],
            },
            "factors": {
                "momentum": 30,
                "value": 15,
                "quality": 20,
                "growth": 15,
                "risk": 20,
            },
            "gates": {
                "min_history_days": 180,
                "max_data_age_days": 10,
                "min_technical_score": 45,
                "min_return_3m": -15,
                "max_annual_vol": 80,
                "max_drawdown_pct": 60,
                "min_factor_coverage": 0.4,
                "min_composite_score": 58,
                "require_fundamentals": False,
            },
            "portfolio": {
                "max_positions": 8,
                "max_position_pct": 20,
                "min_cash_pct": 10,
                "max_pair_correlation": 0.85,
                "defensive_cash_add_pct": 10,
                "weighting": "equal",
            },
        }
    )


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str, str], dict] = {}

    def create_job(self, **kwargs):
        key = (
            kwargs["user_id"],
            kwargs["job_type"],
            kwargs["idempotency_key"],
        )
        if key in self.jobs:
            return self.jobs[key], False
        job = {
            "id": f"job-{len(self.jobs) + 1}",
            "job_type": kwargs["job_type"],
            "queue_name": kwargs["queue_name"],
            "payload": kwargs["payload"],
        }
        self.jobs[key] = job
        return job, True


class OpportunityProfitEngineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "profit.db")
        self.opp = OpportunityRepository(self.database)
        # The opportunity repository creates the referenced base tables first.
        self.opp.list_strategies(user_id="owner")
        self.profit = OpportunityProfitRepository(self.database)
        self.basket_sequence = 0

    def tearDown(self):
        self.tempdir.cleanup()

    def create_strategy(self, name: str = "前瞻收益策略") -> dict:
        return self.opp.create_strategy(
            user_id="owner",
            definition=strategy_definition(name),
            actor_id="owner",
        )

    def create_basket(
        self,
        strategy: dict,
        *,
        gross_return: float = 5.0,
        benchmark_return: float = 1.0,
        complete: bool = True,
        frozen_at: dt.datetime | None = None,
    ) -> dict:
        frozen_at = frozen_at or (
            dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc)
            + dt.timedelta(days=34 * self.basket_sequence)
        )
        self.basket_sequence += 1
        run = self.opp.create_run(
            strategy["id"], user_id="owner", actor_id="owner"
        )
        self.opp.mark_running(run["id"], user_id="owner", actor_id="worker")
        result = {
            "schema_version": "opportunity_run_result.v1",
            "funnel": {"evaluated": 1, "universe": 1},
            "strategy": {
                "id": strategy["id"],
                "version_id": strategy["version_id"],
                "version_no": strategy["current_version_no"],
                "sha256": strategy["definition_sha256"],
                "name": strategy["definition"]["name"],
            },
            "portfolio": {
                "positions": [
                    {
                        "market": "A股",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "weight_pct": 90,
                        "entry_price": 100,
                        "entry_date": frozen_at.date().isoformat(),
                        "price_source": "test",
                    }
                ],
                "cash_pct": 10,
                "warnings": [],
            },
        }
        self.opp.complete_run(
            run["id"],
            user_id="owner",
            result=result,
            status="succeeded",
            actor_id="worker",
        )
        basket, _ = opportunity_service.create_paper_basket(
            run["id"],
            user_id="owner",
            repo=self.opp,
            now=frozen_at,
        )
        horizons = (5, 20, 60) if complete else (5,)
        for sequence, elapsed in enumerate(horizons, start=1):
            payload = {
                "schema_version": "opportunity_paper_observation.v2",
                "observed_at": (
                    frozen_at + dt.timedelta(days=elapsed * 2)
                ).isoformat(),
                "status": "complete",
                "gross_weighted_return_pct": gross_return * elapsed / 20,
                "weighted_return_pct": gross_return * elapsed / 20,
                "benchmark_return_pct": benchmark_return * elapsed / 20,
                "covered_position_weight_pct": 90,
                "benchmark_coverage_weight_pct": 90,
                "invested_weight_pct": 90,
                "observed_trading_days_min": elapsed,
                "observed_trading_days_max": elapsed,
                "max_horizon_complete": elapsed >= 60,
                "positions": [],
            }
            self.opp.append_paper_observation(
                basket["id"],
                user_id="owner",
                observed_at=payload["observed_at"],
                payload=payload,
                idempotency_key=f"{basket['id']}:{elapsed}",
            )
        return self.opp.get_paper_basket(basket["id"], user_id="owner")

    def test_strategy_needs_independent_forward_cohorts_before_capital(self):
        strategy = self.create_strategy()
        for _ in range(2):
            self.create_basket(strategy)

        scorecard = profit_service.build_scorecard(
            strategy["id"],
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )

        self.assertEqual(scorecard["capital_gate"]["status"], "collecting")
        self.assertEqual(scorecard["capital_gate"]["maximum_manual_pilot_pct"], 0)
        primary = next(
            item
            for item in scorecard["horizons"]
            if item["horizon_trading_days"] == 20
        )
        self.assertEqual(primary["mature_count"], 2)
        self.assertAlmostEqual(primary["mean_net_return_pct"], 4.73, places=2)
        self.assertAlmostEqual(
            primary["mean_net_excess_return_pct"], 3.73, places=2
        )

    def test_policy_cannot_weaken_engine_capital_safety_floors(self):
        weak_values = {
            "round_trip_cost_bps": 0,
            "minimum_coverage_pct": 50,
            "minimum_mature_baskets": 3,
            "minimum_positive_excess_rate_pct": 40,
            "maximum_cohort_drawdown_pct": 80,
            "maximum_manual_pilot_pct": 10,
            "latest_basket_max_age_days": 90,
        }
        for field, value in weak_values.items():
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    profit_service.normalize_policy(
                        {**profit_service.DEFAULT_POLICY, field: value}
                    )

    def test_existing_sqlite_observation_table_upgrades_before_index_creation(self):
        legacy_database = str(Path(self.tempdir.name) / "legacy-profit.db")
        connection = sqlite3.connect(legacy_database)
        try:
            connection.execute(
                """
                CREATE TABLE opportunity_paper_observations (
                    id TEXT PRIMARY KEY,
                    basket_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(basket_id, sequence_no)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        upgraded = OpportunityRepository(legacy_database)
        upgraded.list_strategies(user_id="owner")
        connection = sqlite3.connect(legacy_database)
        try:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(opportunity_paper_observations)"
                )
            }
            indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list(opportunity_paper_observations)"
                )
            }
        finally:
            connection.close()
        self.assertIn("idempotency_key", columns)
        self.assertIn("idx_opportunity_observation_idempotency", indexes)

    def test_positive_cost_after_benchmark_evidence_unlocks_only_manual_pilot(self):
        strategy = self.create_strategy()
        for _ in range(6):
            self.create_basket(strategy)

        scorecard = profit_service.build_scorecard(
            strategy["id"],
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )

        gate = scorecard["capital_gate"]
        self.assertEqual(gate["status"], "limited_manual_pilot")
        self.assertTrue(gate["capital_eligible"])
        self.assertFalse(gate["execution_authorized"])
        self.assertTrue(gate["checks"]["confidence_interval_above_zero"])

    def test_overlapping_runs_cannot_be_used_to_manufacture_sample_size(self):
        strategy = self.create_strategy("防重复样本策略")
        same_start = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        for _ in range(6):
            self.create_basket(strategy, frozen_at=same_start)

        scorecard = profit_service.build_scorecard(
            strategy["id"],
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )

        primary = next(
            item
            for item in scorecard["horizons"]
            if item["horizon_trading_days"] == 20
        )
        self.assertEqual(primary["mature_count"], 1)
        self.assertEqual(primary["overlap_excluded_count"], 5)
        self.assertEqual(primary["independence_spacing_days"], 28)
        self.assertEqual(scorecard["capital_gate"]["status"], "collecting")
        self.assertEqual(
            scorecard["capital_gate"]["maximum_manual_pilot_pct"], 0
        )

    def test_multiple_strategy_search_tightens_the_capital_gate(self):
        cohorts = [
            {
                "status": "mature",
                "net_return_pct": value + 1,
                "net_excess_return_pct": value,
                "cohort_max_drawdown_pct": 2,
            }
            for value in (0.2, 0.4, 0.6, 0.8, 1.0, 1.2)
        ]
        single = profit_service._horizon_summary(
            20, cohorts, strategy_family_size=1
        )
        searched = profit_service._horizon_summary(
            20, cohorts, strategy_family_size=20
        )

        self.assertGreater(single["mean_excess_ci95"]["lower"], 0)
        self.assertGreater(
            single["mean_excess_familywise_ci95"]["lower"], 0
        )
        self.assertLessEqual(
            searched["mean_excess_familywise_ci95"]["lower"], 0
        )
        single_gate = profit_service._capital_gate(
            single,
            profit_service.DEFAULT_POLICY,
            basket_count=6,
        )
        searched_gate = profit_service._capital_gate(
            searched,
            profit_service.DEFAULT_POLICY,
            basket_count=6,
        )
        self.assertEqual(single_gate["status"], "limited_manual_pilot")
        self.assertEqual(searched_gate["status"], "watch")
        self.assertFalse(searched_gate["checks"]["multiple_testing_guard"])

    def test_archiving_or_versioning_cannot_erase_prior_strategy_trials(self):
        strategy = self.create_strategy("第一版")
        self.create_basket(strategy, complete=False)
        second_version = self.opp.add_strategy_version(
            strategy["id"],
            user_id="owner",
            definition=strategy_definition("第二版"),
            actor_id="owner",
        )
        self.create_basket(second_version, complete=False)

        self.assertEqual(
            self.opp.count_tested_strategy_versions(user_id="owner"), 2
        )
        self.opp.archive_strategy(strategy["id"], user_id="owner")
        self.assertEqual(
            self.opp.count_tested_strategy_versions(user_id="owner"), 2
        )

    def test_negative_forward_alpha_suspends_strategy(self):
        strategy = self.create_strategy("负超额策略")
        for _ in range(6):
            self.create_basket(
                strategy, gross_return=0.2, benchmark_return=2.0
            )

        scorecard = profit_service.build_scorecard(
            strategy["id"],
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )

        self.assertEqual(scorecard["capital_gate"]["status"], "suspended")
        self.assertFalse(scorecard["capital_gate"]["capital_eligible"])
        self.assertEqual(
            scorecard["capital_plan"]["planned_budget_cny"], 0
        )

    def test_manual_pilot_plan_is_capped_by_ips_budget_and_current_valuation(self):
        strategy = self.create_strategy()
        for _ in range(6):
            self.create_basket(strategy)
        profile = {
            "configured": True,
            "governance_integrity": {"verified": True},
            "monthly_budget": 10_000,
            "max_single_ratio": 20,
            "allowed_fund_markets": ["mainland"],
            "profile_version_id": "profile-1",
        }
        valuation = {
            "snapshot": {
                "id": "valuation-1",
                "payload": {
                    "summary": {"total_value": 100_000},
                    "positions": [],
                },
            },
            "runtime_gate": {
                "trade_amount_eligible": True,
                "reasons": [],
            },
        }
        with (
            patch.object(
                profit_service.storage,
                "get_investment_profile",
                return_value=profile,
            ),
            patch.object(
                profit_service.portfolio_valuation,
                "latest_portfolio_valuation",
                return_value=valuation,
            ),
        ):
            scorecard = profit_service.build_scorecard(
                strategy["id"],
                user_id="owner",
                now=dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc),
                opp_repo=self.opp,
                profit_repo=self.profit,
            )

        plan = scorecard["capital_plan"]
        self.assertEqual(plan["status"], "available")
        self.assertEqual(plan["planned_budget_cny"], 5_000)
        self.assertEqual(plan["allocated_amount_cny"], 4_500)
        self.assertEqual(plan["unallocated_cash_cny"], 500)
        self.assertFalse(plan["execution_authorized"])

    def test_persisted_scorecard_is_immutable_deduplicated_and_user_scoped(self):
        strategy = self.create_strategy()
        for _ in range(6):
            self.create_basket(strategy)
        fixed = dt.datetime(2026, 7, 23, 12, tzinfo=dt.timezone.utc)

        first, created = profit_service.persist_scorecard(
            strategy["id"],
            user_id="owner",
            actor_id="owner",
            now=fixed,
            opp_repo=self.opp,
            profit_repo=self.profit,
        )
        second, created_again = profit_service.persist_scorecard(
            strategy["id"],
            user_id="owner",
            actor_id="owner",
            now=fixed + dt.timedelta(minutes=5),
            opp_repo=self.opp,
            profit_repo=self.profit,
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["integrity_verified"])
        self.assertIsNone(
            self.profit.get_scorecard(first["id"], user_id="another-user")
        )
        profit_service.save_policy(
            strategy["id"],
            {
                **profit_service.DEFAULT_POLICY,
                "minimum_mean_excess_return_pct": 0.75,
            },
            user_id="owner",
            actor_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )
        lab = profit_service.profit_lab_overview(
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )
        self.assertFalse(
            lab["items"][0]["latest_persisted"]["binding_current"]
        )

    def test_daily_dispatch_stops_mature_baskets_and_deduplicates_jobs(self):
        strategy = self.create_strategy()
        self.create_basket(strategy, complete=True)
        self.create_basket(strategy, complete=False)
        jobs = FakeJobRepository()
        enqueue = Mock(return_value="task-1")
        fixed = dt.datetime(2026, 7, 23, 12, tzinfo=dt.timezone.utc)

        first = profit_service.dispatch_due_observations(
            now=fixed,
            minimum_interval_hours=0,
            opp_repo=self.opp,
            jobs=jobs,
            enqueue=enqueue,
        )
        second = profit_service.dispatch_due_observations(
            now=fixed,
            minimum_interval_hours=0,
            opp_repo=self.opp,
            jobs=jobs,
            enqueue=enqueue,
        )

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["deduplicated"], 1)
        enqueue.assert_called_once()

    def test_observation_uses_market_benchmark_cost_and_idempotency(self):
        strategy = self.create_strategy()
        basket = self.create_basket(
            strategy,
            complete=False,
            frozen_at=dt.datetime(
                2026, 1, 2, tzinfo=dt.timezone.utc
            ),
        )
        dates = pd.bdate_range("2026-01-02", periods=80)

        def history_loader(_market, symbol, _months, **_kwargs):
            start, end = (100.0, 110.0) if symbol == "600519" else (100.0, 104.0)
            close = pd.Series(
                [start + (end - start) * index / (len(dates) - 1) for index in range(len(dates))]
            )
            frame = pd.DataFrame(
                {
                    "date": dates,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": [1000] * len(dates),
                }
            )
            frame.attrs["source"] = "test-provider"
            return frame

        first = opportunity_service.observe_paper_basket(
            basket["id"],
            user_id="owner",
            repo=self.opp,
            history_loader=history_loader,
        )
        second = opportunity_service.observe_paper_basket(
            basket["id"],
            user_id="owner",
            repo=self.opp,
            history_loader=history_loader,
        )

        payload = first["payload"]
        self.assertEqual(payload["schema_version"], "opportunity_paper_observation.v2")
        self.assertAlmostEqual(payload["gross_weighted_return_pct"], 9.0, places=2)
        self.assertAlmostEqual(payload["benchmark_return_pct"], 3.6, places=2)
        self.assertAlmostEqual(payload["cost_drag_pct"], 0.27, places=2)
        self.assertAlmostEqual(payload["net_excess_return_pct"], 5.13, places=2)
        exact_20 = next(
            item
            for item in payload["horizons"]
            if item["trading_days"] == 20
        )
        self.assertTrue(exact_20["exact_horizon"])
        self.assertTrue(exact_20["complete"])
        self.assertAlmostEqual(
            exact_20["gross_weighted_return_pct"], 180 / 79, places=2
        )
        self.assertAlmostEqual(
            exact_20["net_excess_return_pct"],
            180 / 79 - 0.27 - 72 / 79,
            places=2,
        )
        scorecard = profit_service.build_scorecard(
            strategy["id"],
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )
        cohort = scorecard["cohorts"]["20"][0]
        self.assertEqual(cohort["horizon_measurement"], "exact_trading_day")
        self.assertEqual(cohort["trading_days_observed"], 20)
        self.assertAlmostEqual(
            cohort["net_excess_return_pct"],
            exact_20["net_excess_return_pct"],
            places=2,
        )
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["id"], second["id"])

    def test_production_migration_is_locked_versioned_and_immutable(self):
        self.assertEqual(
            opportunity_profit_engine_v1.MIGRATION_ID,
            "opportunity-profit-engine.v1",
        )
        ddl = opportunity_profit_engine_v1.POSTGRES_DDL
        guards = opportunity_profit_engine_v1.POSTGRES_GUARDS
        migrate_source = inspect.getsource(
            opportunity_profit_engine_v1.migrate
        )
        self.assertIn("idempotency_key", ddl)
        self.assertIn("opportunity_profit_policy_versions", ddl)
        self.assertIn("opportunity_profit_scorecards", ddl)
        self.assertIn("stock_assistant_reject_mutation", guards)
        self.assertIn("pg_advisory_xact_lock", migrate_source)


if __name__ == "__main__":
    unittest.main()
