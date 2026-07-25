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

from migrations import quant_selection_lab_v1 as migration  # noqa: E402
from quant_selection_engine import run_selection_research  # noqa: E402
from quant_selection_repository import (  # noqa: E402
    QuantSelectionConflictError,
    QuantSelectionRepository,
)
import quant_selection_service as service  # noqa: E402


def synthetic_market(
    *,
    symbols: int = 8,
    days: int = 520,
    low_capacity: bool = False,
) -> tuple[dict[str, pd.DataFrame], list[dict], pd.DatetimeIndex]:
    rng = np.random.default_rng(20260725)
    dates = pd.bdate_range("2023-01-02", periods=days)
    frames: dict[str, pd.DataFrame] = {}
    members = []
    for index in range(symbols):
        symbol = f"S{index:02d}"
        returns = (
            0.0001
            + index * 0.00004
            + rng.normal(0, 0.009 + index * 0.0003, days)
        )
        close = 50 * np.exp(np.cumsum(returns))
        opened = close * (1 + rng.normal(0, 0.0015, days))
        volume = (
            np.full(days, 1_000.0)
            if low_capacity
            else np.full(days, 2_000_000 + index * 100_000.0)
        )
        frames[symbol] = pd.DataFrame(
            {
                "date": dates,
                "open": opened,
                "high": np.maximum(opened, close) * 1.01,
                "low": np.minimum(opened, close) * 0.99,
                "close": close,
                "volume": volume,
                "execution_open": opened,
                "raw_turnover": opened * volume,
            }
        )
        members.append({"symbol": symbol, "name": symbol})
    benchmark_returns = rng.normal(0.0002, 0.007, days)
    benchmark = 100 * np.exp(np.cumsum(benchmark_returns))
    frames["SPY"] = pd.DataFrame(
        {
            "date": dates,
            "open": benchmark,
            "high": benchmark * 1.01,
            "low": benchmark * 0.99,
            "close": benchmark,
            "volume": np.full(days, 10_000_000.0),
            "execution_open": benchmark,
            "raw_turnover": benchmark * 10_000_000,
        }
    )
    return frames, members, dates


def engine_policy(**overrides) -> dict:
    value = {
        "factor_weights": {
            "momentum": 35.0,
            "trend_quality": 25.0,
            "low_volatility": 25.0,
            "liquidity": 15.0,
        },
        "lookback_days": 126,
        "minimum_history_days": 126,
        "max_price_staleness_days": 7,
        "minimum_price": 1.0,
        "minimum_average_turnover": 0.0,
        "minimum_composite_score": 0.0,
        "max_positions": 4,
        "construction_method": "score_inverse_volatility",
        "minimum_cash_pct": 10.0,
        "max_position_pct": 30.0,
        "initial_capital": 1_000_000.0,
        "rebalance_days": 21,
        "max_order_age_sessions": 3,
        "max_volume_participation_pct": 2.5,
        "slippage_bps": 8.0,
        "impact_bps": 20.0,
        "commission_bps": 5.0,
        "sell_tax_bps": 0.0,
        "minimum_order_notional": 100.0,
        "oos_segment_days": 126,
        "maximum_drawdown_pct": 30.0,
    }
    value.update(overrides)
    return value


def source_evidence(frames: dict[str, pd.DataFrame]) -> dict[str, dict]:
    return {
        symbol: {
            "adjusted_source": "Polygon",
            "raw_source": "Polygon 未复权日线",
        }
        for symbol in frames
    }


class QuantSelectionEngineTests(unittest.TestCase):
    def test_point_in_time_membership_and_next_open_execution(self):
        frames, members, dates = synthetic_market()
        first_members = members[:-1]
        snapshots = [
            {
                "as_of": dates[0],
                "source": "historical-test",
                "members": first_members,
            },
            {
                "as_of": dates[210],
                "source": "historical-test",
                "members": members,
            },
        ]
        result = run_selection_research(
            frames=frames,
            benchmark_symbol="SPY",
            universe_snapshots=snapshots,
            policy=engine_policy(),
            universe_evidence={
                "point_in_time_verified": True,
                "verification_detail": "test fixture",
                "maximum_member_count": len(members),
            },
            source_evidence=source_evidence(frames),
        )
        newcomer = members[-1]["symbol"]
        before = [
            signal
            for signal in result["signals"]
            if pd.Timestamp(signal["signal_date"]) < dates[210]
        ]
        after = [
            signal
            for signal in result["signals"]
            if pd.Timestamp(signal["signal_date"]) >= dates[210]
        ]
        self.assertTrue(before)
        self.assertTrue(after)
        self.assertTrue(
            all(
                newcomer
                not in {row["symbol"] for row in signal["ranked"]}
                for signal in before
            )
        )
        self.assertTrue(
            any(
                newcomer
                in {row["symbol"] for row in signal["ranked"]}
                for signal in after
            )
        )
        self.assertGreater(len(result["fills"]), 0)
        self.assertTrue(
            all(
                pd.Timestamp(fill["fill_date"])
                > pd.Timestamp(fill["signal_date"])
                for fill in result["fills"]
            )
        )

    def test_future_prices_cannot_change_first_signal(self):
        frames, members, dates = synthetic_market()
        snapshots = [
            {
                "as_of": dates[0],
                "source": "historical-test",
                "members": members,
            }
        ]
        first = run_selection_research(
            frames=frames,
            benchmark_symbol="SPY",
            universe_snapshots=snapshots,
            policy=engine_policy(),
            universe_evidence={
                "point_in_time_verified": True,
                "verification_detail": "test fixture",
                "maximum_member_count": len(members),
            },
            source_evidence=source_evidence(frames),
        )
        first_signal_date = pd.Timestamp(
            first["signals"][0]["signal_date"]
        )
        changed = {symbol: frame.copy() for symbol, frame in frames.items()}
        for symbol, frame in changed.items():
            mask = frame["date"] > first_signal_date
            if symbol != "SPY":
                frame.loc[mask, ["open", "high", "low", "close", "execution_open"]] *= (
                    1 + np.linspace(0, 2, int(mask.sum()))
                )[:, None]
        second = run_selection_research(
            frames=changed,
            benchmark_symbol="SPY",
            universe_snapshots=snapshots,
            policy=engine_policy(),
            universe_evidence={
                "point_in_time_verified": True,
                "verification_detail": "test fixture",
                "maximum_member_count": len(members),
            },
            source_evidence=source_evidence(changed),
        )
        self.assertEqual(
            first["signals"][0]["ranked"],
            second["signals"][0]["ranked"],
        )
        self.assertEqual(
            first["signals"][0]["targets"],
            second["signals"][0]["targets"],
        )

    def test_volume_capacity_causes_partial_fills_and_expiry(self):
        frames, members, dates = synthetic_market(low_capacity=True)
        result = run_selection_research(
            frames=frames,
            benchmark_symbol="SPY",
            universe_snapshots=[
                {
                    "as_of": dates[0],
                    "source": "historical-test",
                    "members": members,
                }
            ],
            policy=engine_policy(
                max_volume_participation_pct=0.1,
                minimum_order_notional=1.0,
                max_order_age_sessions=2,
            ),
            universe_evidence={
                "point_in_time_verified": True,
                "verification_detail": "test fixture",
                "maximum_member_count": len(members),
            },
            source_evidence=source_evidence(frames),
        )
        execution = result["execution"]
        self.assertGreater(execution["partial_fill_count"], 0)
        self.assertGreater(execution["cancelled_notional"], 0)
        self.assertGreater(execution["unfilled_requested_pct"], 10)
        capacity_check = next(
            item
            for item in result["promotion_gate"]["checks"]
            if item["code"] == "capacity"
        )
        self.assertFalse(capacity_check["passed"])

    def test_cost_stress_is_not_better_than_base_due_to_costs(self):
        frames, members, dates = synthetic_market()
        result = run_selection_research(
            frames=frames,
            benchmark_symbol="SPY",
            universe_snapshots=[
                {
                    "as_of": dates[0],
                    "source": "historical-test",
                    "members": members,
                }
            ],
            policy=engine_policy(),
            universe_evidence={
                "point_in_time_verified": True,
                "verification_detail": "test fixture",
                "maximum_member_count": len(members),
            },
            source_evidence=source_evidence(frames),
        )
        self.assertLessEqual(
            result["stress_test"]["performance"]["total_return_pct"],
            result["performance"]["total_return_pct"],
        )
        self.assertGreater(
            result["stress_test"]["execution"]["total_cost"],
            result["execution"]["total_cost"],
        )


class QuantSelectionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = QuantSelectionRepository(
            Path(self.temp.name) / "selection.db"
        )

    def tearDown(self):
        self.temp.cleanup()

    def _completed(self, *, ready: bool = True):
        run = self.repo.create_run(
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            engine_version="test",
            policy={"name": "test"},
        )
        self.repo.mark_running(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="worker",
        )
        return self.repo.complete_run(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="worker",
            status="succeeded",
            result={
                "promotion_gate": {
                    "status": "paper_ready" if ready else "research_only",
                    "paper_shadow_eligible": ready,
                },
                "latest_signal": {
                    "targets": [
                        {"symbol": "A", "target_weight_pct": 45},
                        {"symbol": "B", "target_weight_pct": 45},
                    ]
                },
            },
        )

    def test_event_chain_tenant_scope_and_result_immutability(self):
        run = self._completed()
        self.assertTrue(run["integrity"]["verified"])
        self.assertIsNone(
            self.repo.get_run(
                run["id"],
                tenant_id="public",
                user_id="bob",
            )
        )
        with self.repo._connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    """
                    UPDATE quant_selection_runs SET policy_json='{}'
                    WHERE id=?
                    """,
                    (run["id"],),
                )
        with self.repo._connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    """
                    UPDATE quant_selection_runs SET result_json='{}'
                    WHERE id=?
                    """,
                    (run["id"],),
                )

    def test_shadow_mandate_is_idempotent_and_immutable(self):
        run = self._completed()
        first, created = self.repo.create_shadow_mandate(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            snapshot={"targets": ["A", "B"]},
        )
        second, created_again = self.repo.create_shadow_mandate(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            snapshot={"targets": ["changed"]},
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["integrity"]["verified"])
        with self.repo._connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    """
                    DELETE FROM quant_selection_shadow_mandates
                    WHERE id=?
                    """,
                    (first["id"],),
                )


class QuantSelectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = QuantSelectionRepository(
            Path(self.temp.name) / "selection.db"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_normalize_policy_blocks_small_manual_pool(self):
        with self.assertRaisesRegex(ValueError, "至少需要 6"):
            service.normalize_policy(
                {
                    "market": "美股",
                    "universe_mode": "frozen_symbols",
                    "symbols": [{"symbol": "AAPL"}],
                }
            )

    def test_embedded_run_freezes_inputs_and_result(self):
        frames, members, dates = synthetic_market(days=430)
        payload = {
            "name": "service test",
            "market": "美股",
            "universe_mode": "frozen_symbols",
            "symbols": members,
            "benchmark_symbol": "SPY",
            "history_months": 36,
            "lookback_days": 126,
            "minimum_history_days": 126,
            "minimum_average_turnover": 0,
            "minimum_composite_score": 0,
            "rebalance_days": 21,
        }

        def load_asset(_market, symbol, _months):
            return frames[symbol], {
                "adjusted_source": "Polygon",
                "raw_source": "Polygon 未复权日线",
                "raw_error": None,
            }

        universe = [
            {
                "as_of": dates[0].strftime("%Y-%m-%d"),
                "source": "test-history",
                "members": members,
            }
        ]
        evidence = {
            "mode": "test",
            "label": "test",
            "source": "test",
            "point_in_time_verified": True,
            "verification_detail": "test fixture",
            "maximum_member_count": len(members),
            "unique_symbol_count": len(members),
            "warning": None,
        }
        with patch.object(
            service, "uses_celery_queue", return_value=False
        ), patch.object(
            service,
            "_load_universe",
            return_value=(universe, evidence),
        ), patch.object(
            service,
            "_load_asset",
            side_effect=load_asset,
        ):
            run = service.start_run(
                payload,
                tenant_id="public",
                user_id="alice",
                actor_id="alice",
                repo=self.repo,
            )
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["integrity"]["verified"])
        self.assertEqual(
            run["result"]["data_quality"]["loaded_asset_count"],
            len(members),
        )
        self.assertTrue(
            all(
                pd.Timestamp(fill["fill_date"])
                > pd.Timestamp(fill["signal_date"])
                for fill in run["result"]["fills"]
            )
        )

    def test_freeze_requires_ready_gate_and_matching_digest(self):
        run = self.repo.create_run(
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            engine_version="test",
            policy={"name": "test"},
        )
        self.repo.mark_running(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="worker",
        )
        completed = self.repo.complete_run(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="worker",
            status="succeeded",
            result={
                "policy": {"rebalance_days": 21},
                "universe": {},
                "data_quality": {},
                "promotion_gate": {
                    "paper_shadow_eligible": True,
                    "status": "paper_ready",
                },
                "latest_signal": {
                    "targets": [
                        {"symbol": "A", "target_weight_pct": 45},
                        {"symbol": "B", "target_weight_pct": 45},
                    ]
                },
            },
        )
        with self.assertRaisesRegex(
            QuantSelectionConflictError, "摘要"
        ):
            service.freeze_shadow_mandate(
                run["id"],
                acknowledged=True,
                expected_result_sha256="0" * 64,
                tenant_id="public",
                user_id="alice",
                actor_id="alice",
                repo=self.repo,
            )
        item, created = service.freeze_shadow_mandate(
            run["id"],
            acknowledged=True,
            expected_result_sha256=completed["result_sha256"],
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            repo=self.repo,
        )
        self.assertTrue(created)
        self.assertTrue(item["integrity"]["verified"])
        self.assertFalse(
            item["snapshot"]["forward_rules"]["broker_order_submission"]
        )

    def test_migration_contains_tables_and_guard(self):
        self.assertEqual(
            migration.MIGRATION_ID,
            "quant-selection-lab.v1",
        )
        self.assertIn("quant_selection_runs", migration.POSTGRES_DDL)
        self.assertIn(
            "quant_selection_shadow_mandates",
            migration.POSTGRES_DDL,
        )
        self.assertIn(
            "quant selection run input is immutable",
            migration.POSTGRES_GUARDS,
        )


if __name__ == "__main__":
    unittest.main()
