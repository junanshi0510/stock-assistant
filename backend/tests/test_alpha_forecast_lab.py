# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from alpha_forecast_engine import (  # noqa: E402
    AlphaForecastEngineError,
    ENGINE_VERSION,
    run_alpha_forecast_research,
)
from alpha_forecast_repository import (  # noqa: E402
    AlphaForecastConflict,
    AlphaForecastRepository,
    REQUIRED_TABLES,
)
import alpha_forecast_service as service  # noqa: E402
from migrations import alpha_forecast_lab_v1 as migration  # noqa: E402


def persistent_regime_market(
    *,
    symbols: int = 8,
    days: int = 1700,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    rng = np.random.default_rng(20260726)
    dates = pd.bdate_range("2018-01-02", periods=days)
    frames: dict[str, pd.DataFrame] = {}
    for index in range(symbols):
        regime = np.ones(days)
        regime[0] = 1 if index % 2 == 0 else -1
        for position in range(1, days):
            regime[position] = (
                regime[position - 1]
                if rng.random() < 0.985
                else -regime[position - 1]
            )
        returns = regime * 0.0015 + rng.normal(0, 0.004, days)
        frames[f"S{index}"] = pd.DataFrame(
            {
                "date": dates,
                "close": 100 * np.exp(np.cumsum(returns)),
                "volume": rng.lognormal(14, 0.25, days),
            }
        )
    benchmark = pd.DataFrame(
        {
            "date": dates,
            "close": 100
            * np.exp(np.cumsum(rng.normal(0, 0.001, days))),
            "volume": np.full(days, 10_000_000),
        }
    )
    return frames, benchmark


def engine_policy(**overrides):
    value = {
        "asset_type": "stock",
        "market": "美股",
        "horizons": [5, 20, 60],
        "objective": "benchmark_excess_after_cost",
        "round_trip_cost_bps": 10,
    }
    value.update(overrides)
    return value


def repository_policy(**overrides):
    value = {
        "name": "审计概率项目",
        "asset_type": "stock",
        "market": "美股",
        "symbols": [
            {"symbol": f"S{index}", "name": f"S{index}"}
            for index in range(5)
        ],
        "benchmark_symbol": "SPY",
        "horizons": [5, 20, 60],
        "objective": "benchmark_excess_after_cost",
        "round_trip_cost_bps": 20,
        "cadence_days": 7,
    }
    value.update(overrides)
    return value


class AlphaForecastEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames, cls.benchmark = persistent_regime_market()
        cls.result = run_alpha_forecast_research(
            frames=cls.frames,
            names={symbol: symbol for symbol in cls.frames},
            benchmark_frame=cls.benchmark,
            policy=engine_policy(),
            source_evidence={
                symbol: {"source": "synthetic-test-fixture"}
                for symbol in cls.frames
            },
        )

    def test_fixed_horizons_produce_purged_walk_forward_evidence(self):
        self.assertEqual(
            [item["horizon_sessions"] for item in self.result["horizons"]],
            [5, 20, 60],
        )
        for horizon in self.result["horizons"]:
            self.assertTrue(horizon["walk_forward_folds"])
            self.assertTrue(
                all(
                    item["purged"]
                    and item["train_label_end"] < item["test_start"]
                    for item in horizon["walk_forward_folds"]
                )
            )
            self.assertGreater(
                horizon["calibration"]["sample_count"], 0
            )
            self.assertGreater(
                horizon["evaluation"]["sample_count"], 0
            )

    def test_probability_is_published_only_after_all_historical_gates(self):
        self.assertTrue(
            any(
                item["historical_gate"]["passed"]
                for item in self.result["horizons"]
            )
        )
        for forecast in self.result["forecasts"]:
            if forecast["historical_gate_passed"]:
                self.assertIsNotNone(forecast["published_probability"])
                self.assertFalse(forecast["decision_eligible"])
                self.assertEqual(
                    forecast["release_state"],
                    "historical_validated_shadow",
                )
            else:
                self.assertIsNone(forecast["published_probability"])
                self.assertEqual(forecast["stance"], "证据不足·弃权")

    def test_metrics_include_calibration_and_economic_value(self):
        metrics = self.result["horizons"][0]["evaluation"]
        for key in (
            "brier_score",
            "baseline_brier_score",
            "brier_skill_score",
            "log_loss_improvement",
            "roc_auc",
            "expected_calibration_error",
            "high_low_return_spread_pct",
            "reliability_bins",
        ):
            self.assertIn(key, metrics)
        self.assertFalse(self.result["methodology"]["parameter_search"])
        self.assertTrue(self.result["methodology"]["costs_in_label"])

    def test_client_cannot_select_only_a_favorable_horizon(self):
        with self.assertRaises(AlphaForecastEngineError):
            run_alpha_forecast_research(
                frames=self.frames,
                names={},
                benchmark_frame=self.benchmark,
                policy=engine_policy(horizons=[5]),
            )

    def test_stock_research_fails_closed_without_benchmark(self):
        with self.assertRaises(AlphaForecastEngineError):
            run_alpha_forecast_research(
                frames=self.frames,
                names={},
                benchmark_frame=None,
                policy=engine_policy(),
            )


class AlphaForecastSourceGateTests(unittest.TestCase):
    def test_public_fallback_and_partial_frozen_pool_force_abstention(self):
        policy = repository_policy()
        gate = service._source_release_gate(
            policy=policy,
            sources={
                f"S{index}": {
                    "source": "Yahoo Finance",
                    "provider_tier": "public_fallback",
                }
                for index in range(4)
            }
            | {
                "__benchmark__": {
                    "source": "Polygon",
                    "provider_tier": "professional",
                }
            },
            loaded_symbols={f"S{index}" for index in range(4)},
        )
        self.assertFalse(gate["shadow_release_eligible"])
        self.assertFalse(gate["decision_source_eligible"])
        self.assertFalse(gate["checks"][0]["passed"])
        self.assertFalse(gate["checks"][1]["passed"])

    def test_stock_decision_source_gate_requires_professional_assets(self):
        policy = repository_policy()
        symbols = {f"S{index}" for index in range(5)}
        research_gate = service._source_release_gate(
            policy=policy,
            sources={
                symbol: {
                    "source": "BaoStock",
                    "provider_tier": "research_grade",
                }
                for symbol in symbols
            }
            | {
                "__benchmark__": {
                    "source": "BaoStock 指数日线",
                    "provider_tier": "research_grade",
                }
            },
            loaded_symbols=symbols,
        )
        self.assertTrue(research_gate["shadow_release_eligible"])
        self.assertFalse(research_gate["decision_source_eligible"])

        professional_gate = service._source_release_gate(
            policy=policy,
            sources={
                symbol: {
                    "source": "Polygon",
                    "provider_tier": "professional",
                }
                for symbol in symbols
            }
            | {
                "__benchmark__": {
                    "source": "Polygon",
                    "provider_tier": "professional",
                }
            },
            loaded_symbols=symbols,
        )
        self.assertTrue(professional_gate["shadow_release_eligible"])
        self.assertTrue(professional_gate["decision_source_eligible"])


class AlphaForecastRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = AlphaForecastRepository(
            Path(self.tempdir.name) / "alpha.db"
        )
        self.program = self.repository.create_program(
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="actor-a",
            engine_version=ENGINE_VERSION,
            policy=repository_policy(),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _completed_run(self, request_key="run-1", as_of="2025-01-02"):
        run, created = self.repository.create_run(
            self.program["id"],
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="actor-a",
            request_key=request_key,
            as_of_date=as_of,
        )
        self.assertTrue(created)
        self.repository.mark_running(
            run["id"],
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="worker",
        )
        forecasts = [
            {
                "schema_version": "alpha_forecast_payload.v1",
                "symbol": "S0",
                "name": "S0",
                "horizon_sessions": 5,
                "as_of_date": as_of,
                "eligible_after": "2025-01-15",
                "shadow_calibrated_probability": 0.75,
                "base_rate": 0.5,
                "stance": "看多候选",
                "historical_gate_passed": True,
                "decision_eligible": False,
                "objective": "benchmark_excess_after_cost",
                "benchmark_symbol": "SPY",
                "round_trip_cost_bps": 20,
            }
        ]
        return self.repository.complete_run(
            run["id"],
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="worker",
            status="succeeded",
            result={
                "data_quality": {
                    "loaded_assets": 5,
                    "requested_assets": 5,
                },
                "forecasts": forecasts,
            },
            forecasts=forecasts,
        )

    def test_scope_hashes_and_event_chains_are_verified(self):
        run = self._completed_run()
        self.assertTrue(self.program["integrity"]["verified"])
        self.assertTrue(run["integrity"]["verified"])
        self.assertTrue(run["integrity"]["event_chain"]["verified"])
        self.assertIsNone(
            self.repository.get_program(
                self.program["id"],
                tenant_id="tenant-a",
                user_id="other-user",
            )
        )

    def test_program_request_key_is_idempotent(self):
        run, created = self.repository.create_run(
            self.program["id"],
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="actor-a",
            request_key="stable",
            as_of_date="2025-01-02",
        )
        again, created_again = self.repository.create_run(
            self.program["id"],
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="actor-a",
            request_key="stable",
            as_of_date="2025-01-02",
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(run["id"], again["id"])

    def test_immutable_forecast_and_outcome_guards(self):
        self._completed_run()
        forecast = self.repository.list_pending_forecasts()[0]
        outcome_payload = {
            "observed_date": "2025-01-15",
            "target_return_pct": 3.0,
            "realized_label": 1,
            "actor_id": "observer",
        }
        outcome, created = self.repository.record_outcome(
            forecast["id"], payload=outcome_payload
        )
        again, created_again = self.repository.record_outcome(
            forecast["id"], payload=outcome_payload
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(outcome["id"], again["id"])
        with self.repository._connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    """
                    UPDATE alpha_forecasts SET stance='tampered'
                    WHERE id=?
                    """,
                    (forecast["id"],),
                )
            with self.assertRaises(Exception):
                connection.execute(
                    """
                    DELETE FROM alpha_forecast_outcomes WHERE id=?
                    """,
                    (outcome["id"],),
                )

    def test_program_state_machine_does_not_rewrite_policy(self):
        paused = self.repository.transition_program(
            self.program["id"],
            tenant_id="tenant-a",
            user_id="user-a",
            actor_id="actor-a",
            action="pause",
        )
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(
            paused["policy_sha256"], self.program["policy_sha256"]
        )
        with self.assertRaises(AlphaForecastConflict):
            self.repository.transition_program(
                self.program["id"],
                tenant_id="tenant-a",
                user_id="user-a",
                actor_id="actor-a",
                action="pause",
            )


class AlphaForecastServiceTests(unittest.TestCase):
    def test_policy_freezes_asset_specific_targets(self):
        stock = service.normalize_policy(
            {
                "name": "美股 Alpha",
                "asset_type": "stock",
                "market": "美股",
                "symbols": [
                    {"symbol": item}
                    for item in ("AAPL", "MSFT", "NVDA", "META")
                ],
                "history_months": 60,
            }
        )
        self.assertEqual(stock["horizons"], [5, 20, 60])
        self.assertEqual(
            stock["objective"], "benchmark_excess_after_cost"
        )
        self.assertEqual(stock["benchmark_symbol"], "SPY")
        fund = service.normalize_policy(
            {
                "name": "基金 Alpha",
                "asset_type": "fund",
                "market": "基金",
                "symbols": [
                    {"symbol": item}
                    for item in ("110011", "161725", "005827", "003095")
                ],
                "history_months": 120,
            }
        )
        self.assertEqual(fund["horizons"], [20, 60, 120])
        self.assertEqual(
            fund["objective"], "positive_return_after_cost"
        )
        self.assertFalse(fund["parameter_search"])

    def test_queue_dispatch_contains_only_durable_run_id(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = AlphaForecastRepository(Path(tempdir) / "queue.db")
            with (
                patch.object(service, "uses_celery_queue", return_value=True),
                patch.object(
                    service,
                    "enqueue_alpha_forecast_run",
                    return_value="celery-alpha-1",
                ) as enqueue,
            ):
                created = service.create_program(
                    {
                        "name": "美股概率项目",
                        "asset_type": "stock",
                        "market": "美股",
                        "symbols": [
                            {"symbol": item}
                            for item in ("AAPL", "MSFT", "NVDA", "META")
                        ],
                        "history_months": 60,
                    },
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="actor-a",
                    repo=repo,
                )
            run = created["initial_run"]
            enqueue.assert_called_once_with(run["id"])
            self.assertEqual(run["task_id"], "celery-alpha-1")
            self.assertEqual(run["status"], "queued")

    def test_exact_outcome_settlement_uses_frozen_objective_and_cost(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = AlphaForecastRepository(Path(tempdir) / "outcome.db")
            program = repo.create_program(
                tenant_id="public",
                user_id="user-a",
                actor_id="actor-a",
                engine_version=ENGINE_VERSION,
                policy=repository_policy(),
            )
            run, _ = repo.create_run(
                program["id"],
                tenant_id="public",
                user_id="user-a",
                actor_id="actor-a",
                request_key="settlement",
                as_of_date="2025-01-02",
            )
            repo.mark_running(
                run["id"],
                tenant_id="public",
                user_id="user-a",
                actor_id="worker",
            )
            forecast = {
                "symbol": "S0",
                "name": "S0",
                "horizon_sessions": 5,
                "as_of_date": "2025-01-02",
                "eligible_after": "2025-01-10",
                "shadow_calibrated_probability": 0.8,
                "base_rate": 0.5,
                "stance": "看多候选",
                "historical_gate_passed": True,
                "decision_eligible": False,
                "objective": "benchmark_excess_after_cost",
                "benchmark_symbol": "SPY",
                "round_trip_cost_bps": 100,
                "start_value": 100,
                "benchmark_start_value": 100,
            }
            repo.complete_run(
                run["id"],
                tenant_id="public",
                user_id="user-a",
                actor_id="worker",
                status="succeeded",
                result={
                    "data_quality": {
                        "loaded_assets": 5,
                        "requested_assets": 5,
                    }
                },
                forecasts=[forecast],
            )
            dates = pd.bdate_range("2025-01-02", periods=7)
            asset = pd.DataFrame(
                {
                    "date": dates,
                    # The refreshed source revised the signal-date value.
                    # Settlement must still use the value frozen in forecast.
                    "close": [200, 101, 102, 103, 104, 110, 111],
                }
            )
            benchmark = pd.DataFrame(
                {
                    "date": dates,
                    "close": [100, 100, 101, 101, 102, 102, 103],
                }
            )

            def loader(_market, symbol, **_kwargs):
                frame = benchmark if symbol == "SPY" else asset
                return frame.copy(), {"source": "verified-test-source"}

            with patch.object(
                service, "_load_stock_series", side_effect=loader
            ):
                result = service.settle_mature_outcomes(
                    tenant_id="public",
                    user_id="user-a",
                    repo=repo,
                )
            self.assertEqual(result["created_outcomes"], 1)
            evidence = repo.list_forward_evidence(
                program["id"],
                tenant_id="public",
                user_id="user-a",
            )[0]
            self.assertAlmostEqual(evidence["target_return_pct"], 7.0)
            self.assertEqual(evidence["realized_label"], 1)

    def test_pending_forecasts_can_be_scoped_to_one_program(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = AlphaForecastRepository(Path(tempdir) / "scope.db")
            program_ids = []
            for index in range(2):
                program = repo.create_program(
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="actor-a",
                    engine_version=ENGINE_VERSION,
                    policy={
                        **repository_policy(),
                        "name": f"scope-{index}",
                    },
                )
                program_ids.append(program["id"])
                run, _ = repo.create_run(
                    program["id"],
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="actor-a",
                    request_key=f"scope-{index}",
                    as_of_date="2025-01-02",
                )
                repo.mark_running(
                    run["id"],
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="worker",
                )
                repo.complete_run(
                    run["id"],
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="worker",
                    status="succeeded",
                    result={
                        "data_quality": {
                            "loaded_assets": 4,
                            "requested_assets": 4,
                        }
                    },
                    forecasts=[
                        {
                            "symbol": f"S{index}",
                            "name": f"S{index}",
                            "horizon_sessions": 5,
                            "as_of_date": "2025-01-02",
                            "eligible_after": "2025-01-10",
                            "shadow_calibrated_probability": 0.6,
                            "base_rate": 0.5,
                            "stance": "中性观察",
                            "historical_gate_passed": True,
                            "decision_eligible": False,
                        }
                    ],
                )
            pending = repo.list_pending_forecasts(
                tenant_id="public",
                user_id="user-a",
                program_id=program_ids[1],
            )
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["program_id"], program_ids[1])

    def test_forward_release_requires_and_can_pass_real_outcome_gates(self):
        with tempfile.TemporaryDirectory() as tempdir:
            repo = AlphaForecastRepository(Path(tempdir) / "score.db")
            program = repo.create_program(
                tenant_id="public",
                user_id="user-a",
                actor_id="actor-a",
                engine_version=ENGINE_VERSION,
                policy=repository_policy(),
            )
            for run_index in range(6):
                as_of = f"2025-01-{run_index + 2:02d}"
                run, _ = repo.create_run(
                    program["id"],
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="actor-a",
                    request_key=f"score-{run_index}",
                    as_of_date=as_of,
                )
                repo.mark_running(
                    run["id"],
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="worker",
                )
                forecasts = []
                for symbol_index in range(5):
                    positive = symbol_index < 2
                    forecasts.append(
                        {
                            "symbol": f"S{symbol_index}",
                            "name": f"S{symbol_index}",
                            "horizon_sessions": 5,
                            "as_of_date": as_of,
                            "eligible_after": "2025-02-01",
                            "shadow_calibrated_probability": (
                                0.95 if positive else 0.05
                            ),
                            "base_rate": 0.5,
                            "stance": (
                                "看多候选" if positive else "回避候选"
                            ),
                            "historical_gate_passed": True,
                            "decision_source_eligible": True,
                            "decision_eligible": False,
                        }
                    )
                repo.complete_run(
                    run["id"],
                    tenant_id="public",
                    user_id="user-a",
                    actor_id="worker",
                    status="succeeded",
                    result={
                        "data_quality": {
                            "loaded_assets": 5,
                            "requested_assets": 5,
                        }
                    },
                    forecasts=forecasts,
                )
                for forecast in repo.list_pending_forecasts(
                    tenant_id="public",
                    user_id="user-a",
                ):
                    if forecast["run_id"] != run["id"]:
                        continue
                    positive = forecast["symbol"] in {"S0", "S1"}
                    repo.record_outcome(
                        forecast["id"],
                        payload={
                            "observed_date": "2025-02-10",
                            "target_return_pct": (
                                2.0 if positive else -1.0
                            ),
                            "realized_label": int(positive),
                            "actor_id": "observer",
                        },
                    )
            scorecard = service.forward_scorecard(
                program["id"],
                tenant_id="public",
                user_id="user-a",
                repo=repo,
            )
            horizon = scorecard["horizons"][0]
            self.assertEqual(horizon["outcome_count"], 30)
            self.assertEqual(horizon["run_date_count"], 6)
            self.assertTrue(horizon["decision_eligible"])
            self.assertEqual(scorecard["status"], "qualified")


class AlphaForecastMigrationTests(unittest.TestCase):
    def test_migration_contains_all_tables_guards_and_advisory_lock(self):
        for table in REQUIRED_TABLES:
            self.assertIn(table, migration.POSTGRES_DDL)
        self.assertIn(
            "stock_assistant_alpha_program_guard",
            migration.POSTGRES_GUARDS,
        )
        self.assertIn(
            "stock_assistant_alpha_run_guard",
            migration.POSTGRES_GUARDS,
        )
        source = Path(migration.__file__).read_text(encoding="utf-8")
        self.assertIn("pg_advisory_xact_lock", source)
        self.assertEqual(migration.MIGRATION_ID, "alpha-forecast-lab.v1")


if __name__ == "__main__":
    unittest.main()
