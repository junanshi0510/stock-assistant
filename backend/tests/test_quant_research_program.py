# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
import sqlite3
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

from migrations import quant_research_program_v1  # noqa: E402
import data_fetch  # noqa: E402
import opportunity_service  # noqa: E402
from quant_fundamentals import (  # noqa: E402
    _clean_financials,
    load_tushare_point_in_time_fundamentals,
    tushare_code,
)
from quant_research_program_repository import (  # noqa: E402
    QuantResearchProgramRepository,
)
import quant_research_program_service as program_service  # noqa: E402
import quant_selection_service as selection_service  # noqa: E402
from quant_selection_engine import (  # noqa: E402
    _point_in_time_fundamental_values,
    run_selection_research,
)
from quant_selection_repository import QuantSelectionRepository  # noqa: E402


def _frames() -> tuple[dict[str, pd.DataFrame], list[dict], pd.DatetimeIndex]:
    dates = pd.bdate_range("2023-01-02", periods=360)
    frames = {}
    members = []
    for index in range(8):
        symbol = f"S{index:02d}"
        close = 30 * np.exp(
            np.cumsum(np.full(len(dates), 0.0002 + index * 0.00002))
        )
        frames[symbol] = pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": np.full(len(dates), 2_000_000),
                "execution_open": close,
                "raw_turnover": close * 2_000_000,
            }
        )
        members.append({"symbol": symbol, "name": symbol})
    benchmark = np.linspace(100, 118, len(dates))
    frames["SPY"] = pd.DataFrame(
        {
            "date": dates,
            "open": benchmark,
            "high": benchmark * 1.01,
            "low": benchmark * 0.99,
            "close": benchmark,
            "volume": np.full(len(dates), 10_000_000),
            "execution_open": benchmark,
            "raw_turnover": benchmark * 10_000_000,
        }
    )
    return frames, members, dates


def _policy() -> dict:
    return {
        "factor_weights": {
            "momentum": 0,
            "trend_quality": 0,
            "low_volatility": 0,
            "liquidity": 0,
            "fundamental_quality": 60,
            "value": 40,
        },
        "lookback_days": 126,
        "minimum_history_days": 126,
        "max_price_staleness_days": 7,
        "max_fundamental_staleness_days": 550,
        "max_valuation_staleness_days": 7,
        "minimum_price": 1,
        "minimum_average_turnover": 0,
        "minimum_composite_score": 0,
        "max_positions": 4,
        "construction_method": "equal_weight",
        "minimum_cash_pct": 10,
        "max_position_pct": 30,
        "initial_capital": 1_000_000,
        "rebalance_days": 21,
        "max_order_age_sessions": 3,
        "max_volume_participation_pct": 2.5,
        "slippage_bps": 8,
        "impact_bps": 20,
        "commission_bps": 5,
        "sell_tax_bps": 0,
        "minimum_order_notional": 100,
        "oos_segment_days": 126,
        "maximum_drawdown_pct": 30,
    }


def _fundamentals(
    members: list[dict],
    dates: pd.DatetimeIndex,
    *,
    future_multiplier: float,
) -> dict[str, dict[str, pd.DataFrame]]:
    output = {}
    first_signal = dates[126]
    future_announcement = dates[180]
    for index, member in enumerate(members):
        output[member["symbol"]] = {
            "financials": pd.DataFrame(
                [
                    {
                        "ann_date": first_signal - pd.Timedelta(days=30),
                        "end_date": first_signal - pd.Timedelta(days=90),
                        "roe": 8 + index,
                        "grossprofit_margin": 20 + index,
                        "ocf_to_or": 10 + index,
                        "debt_to_assets": 50 - index,
                    },
                    {
                        "ann_date": future_announcement,
                        "end_date": future_announcement
                        - pd.Timedelta(days=60),
                        "roe": future_multiplier * (index + 1),
                        "grossprofit_margin": future_multiplier * (index + 1),
                        "ocf_to_or": future_multiplier * (index + 1),
                        "debt_to_assets": 10,
                    },
                ]
            ),
            "valuations": pd.DataFrame(
                {
                    "trade_date": dates,
                    "pe_ttm": np.full(len(dates), 12 + index),
                    "pb": np.full(len(dates), 1.2 + index * 0.1),
                }
            ),
        }
    return output


class PointInTimeFundamentalTests(unittest.TestCase):
    def test_future_announcements_cannot_change_first_signal(self):
        frames, members, dates = _frames()
        common = {
            "frames": frames,
            "benchmark_symbol": "SPY",
            "universe_snapshots": [
                {
                    "as_of": dates[0],
                    "source": "historical-test",
                    "members": members,
                }
            ],
            "policy": _policy(),
            "universe_evidence": {
                "point_in_time_verified": True,
                "verification_detail": "fixture",
                "maximum_member_count": len(members),
            },
            "source_evidence": {
                symbol: {
                    "adjusted_source": "Polygon",
                    "raw_source": "Polygon raw",
                }
                for symbol in frames
            },
            "fundamental_evidence": {
                "source": "Tushare Pro",
                "point_in_time_verified": True,
                "verification_detail": "fixture",
                "requested_symbol_count": len(members),
                "loaded_symbol_count": len(members),
            },
        }
        first = run_selection_research(
            **common,
            fundamental_inputs=_fundamentals(
                members, dates, future_multiplier=100
            ),
        )
        second = run_selection_research(
            **common,
            fundamental_inputs=_fundamentals(
                members, dates, future_multiplier=-100
            ),
        )
        self.assertEqual(
            first["signals"][0]["ranked"],
            second["signals"][0]["ranked"],
        )
        for row in first["signals"][0]["ranked"]:
            self.assertLessEqual(
                row["fundamental_as_of"]["financial"]["ann_date"],
                first["signals"][0]["signal_date"],
            )
            self.assertLessEqual(
                row["fundamental_as_of"]["valuation"]["trade_date"],
                first["signals"][0]["signal_date"],
            )
        self.assertTrue(
            first["data_quality"]["fundamentals"][
                "point_in_time_verified"
            ]
        )

    def test_same_day_ambiguous_revision_is_removed(self):
        frame = pd.DataFrame(
            [
                {
                    "ann_date": "20260430",
                    "end_date": "20260331",
                    "roe": 10,
                    "grossprofit_margin": 20,
                    "ocf_to_or": 8,
                    "debt_to_assets": 40,
                },
                {
                    "ann_date": "20260430",
                    "end_date": "20260331",
                    "roe": 99,
                    "grossprofit_margin": 20,
                    "ocf_to_or": 8,
                    "debt_to_assets": 40,
                },
            ]
        )
        cleaned, evidence = _clean_financials(frame)
        self.assertTrue(cleaned.empty)
        self.assertEqual(evidence["ambiguous_revision_count"], 1)
        self.assertEqual(tushare_code("600519"), "600519.SH")
        self.assertEqual(tushare_code("300750"), "300750.SZ")

    def test_late_old_period_revision_cannot_replace_newer_report(self):
        signal_date = pd.Timestamp("2026-05-20")
        fundamentals = {
            "600519": {
                "financials": pd.DataFrame(
                    [
                        {
                            "ann_date": "2026-04-30",
                            "end_date": "2026-03-31",
                            "roe": 18,
                            "grossprofit_margin": 90,
                            "ocf_to_or": 22,
                            "debt_to_assets": 20,
                        },
                        {
                            "ann_date": "2026-05-15",
                            "end_date": "2025-12-31",
                            "roe": 1,
                            "grossprofit_margin": 1,
                            "ocf_to_or": 1,
                            "debt_to_assets": 99,
                        },
                    ]
                ),
                "valuations": pd.DataFrame(),
            }
        }
        values, metadata, error = _point_in_time_fundamental_values(
            symbol="600519",
            signal_date=signal_date,
            fundamentals={
                "600519": {
                    "financials": fundamentals["600519"][
                        "financials"
                    ].assign(
                        ann_date=lambda frame: pd.to_datetime(
                            frame["ann_date"]
                        ),
                        end_date=lambda frame: pd.to_datetime(
                            frame["end_date"]
                        ),
                    ),
                    "valuations": pd.DataFrame(),
                }
            },
            active_factors={"fundamental_quality"},
            max_financial_staleness_days=550,
            max_valuation_staleness_days=7,
        )
        self.assertIsNone(error)
        self.assertEqual(
            metadata["financial"]["report_period"], "2026-03-31"
        )
        self.assertGreater(values["fundamental_quality"], 10)

    def test_provider_only_calls_required_factor_endpoint(self):
        class FakePro:
            def __init__(self):
                self.fina_calls = 0
                self.daily_calls = 0

            def fina_indicator(self, **_kwargs):
                self.fina_calls += 1
                return pd.DataFrame(
                    [
                        {
                            "ann_date": "20260430",
                            "end_date": "20260331",
                            "roe": 18,
                            "grossprofit_margin": 40,
                            "ocf_to_or": 15,
                            "debt_to_assets": 30,
                        }
                    ]
                )

            def daily_basic(self, **_kwargs):
                self.daily_calls += 1
                return pd.DataFrame(
                    [
                        {
                            "trade_date": "20260529",
                            "pe_ttm": 15,
                            "pb": 2,
                        }
                    ]
                )

        provider = FakePro()
        inputs, evidence = load_tushare_point_in_time_fundamentals(
            provider,
            ["600519"],
            history_months=12,
            required_factors={"fundamental_quality"},
            end_date=dt.date(2026, 5, 31),
            max_workers=1,
        )
        self.assertEqual(provider.fina_calls, 1)
        self.assertEqual(provider.daily_calls, 0)
        self.assertEqual(
            evidence["required_factors"], ["fundamental_quality"]
        )
        self.assertTrue(inputs["600519"]["valuations"].empty)


class QuantProductionDataPathTests(unittest.TestCase):
    def tearDown(self):
        with data_fetch._cache_lock:
            data_fetch._cache.clear()

    @staticmethod
    def index_frame() -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "date": pd.bdate_range("2026-01-02", periods=8),
                "open": np.linspace(3900, 3970, 8),
                "high": np.linspace(3910, 3980, 8),
                "low": np.linspace(3890, 3960, 8),
                "close": np.linspace(3905, 3975, 8),
                "volume": np.full(8, 100_000_000),
            }
        )
        return frame

    def test_a_share_index_uses_dedicated_history_sources(self):
        calls = []

        def fake_index_source(symbol, start, end):
            calls.append((symbol, start, end))
            return self.index_frame()

        with data_fetch._cache_lock:
            data_fetch._cache.clear()
        with patch.object(
            data_fetch,
            "_A_INDEX_SOURCES",
            [("fixture-index", fake_index_source)],
        ):
            frame = data_fetch.get_history(
                "A股",
                "000300.sh",
                "20260101",
                "20260131",
            )
        self.assertEqual(
            calls,
            [("000300.SH", "20260101", "20260131")],
        )
        self.assertEqual(frame.attrs["source"], "fixture-index")
        self.assertEqual(len(frame), 8)

    def test_a_share_research_profile_skips_tushare_price_path(self):
        calls = []

        def fake_research_source(symbol, start, end):
            calls.append((symbol, start, end))
            return self.index_frame()

        with data_fetch._cache_lock:
            data_fetch._cache.clear()
        with patch.object(
            data_fetch,
            "_A_RESEARCH_SOURCES",
            [("fixture-research", fake_research_source)],
        ):
            frame = data_fetch.get_history(
                "A股",
                "600519",
                "20260101",
                "20260131",
                source_profile="a_share_research",
            )
        self.assertEqual(
            calls,
            [("600519", "20260101", "20260131")],
        )
        self.assertEqual(frame.attrs["source"], "fixture-research")

    def test_index_benchmark_does_not_request_stock_raw_prices(self):
        adjusted = self.index_frame()
        adjusted.attrs["source"] = "Tushare index_daily"
        adjusted.attrs["retrieved_at"] = "2026-07-26T00:00:00+00:00"
        with (
            patch.object(
                data_fetch,
                "get_history_months",
                return_value=adjusted,
            ),
            patch.object(
                data_fetch,
                "get_price_level_history_months",
            ) as raw_loader,
        ):
            frame, evidence = selection_service._load_asset(
                "A股",
                "000300.SH",
                36,
                require_raw=True,
            )
        raw_loader.assert_not_called()
        self.assertEqual(
            evidence["raw_source"],
            "benchmark_index_level_not_applicable",
        )
        self.assertFalse(evidence["raw_requested"])
        self.assertTrue(
            np.allclose(frame["execution_open"], frame["open"])
        )

    def test_default_preset_requires_no_tushare_factor_endpoint(self):
        presets = selection_service.presets()
        default_preset = presets[0]
        self.assertEqual(
            default_preset["id"],
            "a_frozen_price_research",
        )
        self.assertFalse(default_preset["promotion_capable"])
        self.assertEqual(
            default_preset["policy"]["benchmark_symbol"],
            "000300.SH",
        )
        self.assertEqual(
            default_preset["policy"]["universe_mode"],
            "frozen_symbols",
        )
        self.assertEqual(
            default_preset["policy"]["factor_weights"][
                "fundamental_quality"
            ],
            0,
        )
        self.assertEqual(
            default_preset["policy"]["factor_weights"]["value"],
            0,
        )
        value_preset = next(
            preset
            for preset in presets
            if preset["id"] == "a_frozen_pit_value_research"
        )
        self.assertGreater(
            value_preset["policy"]["factor_weights"]["value"],
            0,
        )
        self.assertIn(
            "本地时点因子仓库至少覆盖 4 只股票和研究窗口",
            value_preset["data_requirements"],
        )
        self.assertEqual(
            value_preset["policy"]["factor_data_mode"],
            "warehouse_only",
        )
        self.assertEqual(
            opportunity_service.PAPER_BENCHMARKS["A股"]["symbol"],
            "000300.SH",
        )

    def test_a_share_frozen_asset_uses_quota_safe_price_profile(self):
        adjusted = self.index_frame()
        adjusted.attrs["source"] = "BaoStock"
        with patch.object(
            data_fetch,
            "get_history_months",
            return_value=adjusted,
        ) as history_loader:
            _frame, evidence = selection_service._load_asset(
                "A股",
                "600519",
                36,
                require_raw=False,
            )
        self.assertEqual(
            history_loader.call_args.kwargs["source_profile"],
            "a_share_research",
        )
        self.assertEqual(
            evidence["source_profile"],
            "a_share_research",
        )


class QuantResearchProgramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "program.db"
        self.quant_repo = QuantSelectionRepository(self.path)
        # Ensure referenced quant-selection tables exist first.
        self.seed = self.quant_repo.create_run(
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            engine_version="test",
            policy={"name": "seed"},
        )
        self.program_repo = QuantResearchProgramRepository(self.path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def payload() -> dict:
        symbols = [
            {"symbol": symbol, "name": symbol}
            for symbol in (
                "AAPL",
                "MSFT",
                "NVDA",
                "AMZN",
                "GOOGL",
                "META",
            )
        ]
        return {
            "name": "固定日历研究",
            "acknowledged": True,
            "schedule": {
                "cadence": "monthly",
                "first_run_date": "2026-01-02",
                "run_time_local": "20:30",
                "planned_cycles": 6,
            },
            "policy": {
                "name": "US fixed pool",
                "market": "美股",
                "universe_mode": "frozen_symbols",
                "symbols": symbols,
                "benchmark_symbol": "SPY",
                "history_months": 36,
                "lookback_days": 126,
                "minimum_history_days": 126,
                "factor_weights": {
                    "momentum": 35,
                    "trend_quality": 25,
                    "low_volatility": 25,
                    "liquidity": 15,
                    "fundamental_quality": 0,
                    "value": 0,
                },
            },
        }

    def create(self):
        return program_service.create_program(
            self.payload(),
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            repo=self.program_repo,
        )

    def test_all_slots_are_preregistered_and_retirement_keeps_them(self):
        program = self.create()
        self.assertEqual(len(program["cycles"]), 6)
        self.assertEqual(
            len({cycle["slot_key"] for cycle in program["cycles"]}), 6
        )
        self.assertTrue(program["schedule"]["all_slots_preregistered"])
        retired = self.program_repo.retire_program(
            program["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="alice",
            reason="策略研究目标已经正式停止",
        )
        self.assertEqual(retired["state"], "retired")
        self.assertTrue(
            all(
                cycle["status"] == "retired_unrun"
                for cycle in retired["cycles"]
            )
        )
        with self.program_repo._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM quant_research_cycles WHERE program_id=?",
                    (program["id"],),
                )

    def test_due_cycle_preserves_research_only_outcome(self):
        program = self.create()
        run = self.quant_repo.create_run(
            tenant_id="public",
            user_id="alice",
            actor_id="scheduler",
            engine_version="test",
            policy=program["policy"],
        )
        self.quant_repo.mark_running(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="worker",
        )
        run = self.quant_repo.complete_run(
            run["id"],
            tenant_id="public",
            user_id="alice",
            actor_id="worker",
            status="succeeded",
            result={
                "promotion_gate": {
                    "status": "research_only",
                    "paper_shadow_eligible": False,
                    "passed_count": 8,
                    "total_count": 12,
                    "checks": [
                        {
                            "code": "cost_stress",
                            "label": "成本压力",
                            "passed": False,
                            "detail": "未通过",
                        }
                    ],
                }
            },
        )
        with patch.object(
            program_service.quant_selection_service,
            "start_run",
            return_value=run,
        ):
            result = program_service.reconcile_due_programs(
                now=dt.datetime(
                    2026, 1, 3, 12, tzinfo=dt.timezone.utc
                ),
                tenant_id="public",
                user_id="alice",
                program_id=program["id"],
                actor_id="scheduler",
                program_repo=self.program_repo,
                quant_repo=self.quant_repo,
            )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["actions"][0]["action"], "research_only")
        refreshed = self.program_repo.get_program(
            program["id"],
            tenant_id="public",
            user_id="alice",
        )
        first = refreshed["cycles"][0]
        self.assertEqual(first["status"], "research_only")
        self.assertEqual(first["run_id"], run["id"])
        self.assertEqual(
            first["outcome"]["failed_checks"][0]["code"],
            "cost_stress",
        )
        self.assertEqual(
            refreshed["summary"]["planned_cycle_count"], 6
        )

    def test_reconcilable_query_filters_scope_before_limit(self):
        alice = self.create()
        bob = program_service.create_program(
            self.payload(),
            tenant_id="public",
            user_id="bob",
            actor_id="bob",
            now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            repo=self.program_repo,
        )
        rows = self.program_repo.list_reconcilable(
            now=dt.datetime(2026, 1, 3, 12, tzinfo=dt.timezone.utc),
            limit=1,
            tenant_id="public",
            user_id="bob",
            program_id=bob["id"],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["program_id"], bob["id"])
        self.assertNotEqual(rows[0]["program_id"], alice["id"])

    def test_terminal_cycle_cannot_gain_nonterminal_status_event(self):
        program = self.create()
        cycle = program["cycles"][0]
        self.program_repo.complete_cycle(
            cycle["id"],
            status="failed",
            actor_id="scheduler",
            outcome={"stage": "test"},
        )
        with self.program_repo._connect() as connection:
            before = connection.execute(
                """
                SELECT COUNT(*) AS value
                FROM quant_research_cycle_events WHERE cycle_id=?
                """,
                (cycle["id"],),
            ).fetchone()["value"]
        result = self.program_repo.mark_run_status(
            cycle["id"],
            status="run_running",
            actor_id="late-worker",
        )
        with self.program_repo._connect() as connection:
            after = connection.execute(
                """
                SELECT COUNT(*) AS value
                FROM quant_research_cycle_events WHERE cycle_id=?
                """,
                (cycle["id"],),
            ).fetchone()["value"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(after, before)

    def test_scheduler_fails_closed_on_program_hash_mismatch(self):
        program = self.create()
        with self.program_repo._connect() as connection:
            connection.execute(
                "DROP TRIGGER trg_quant_research_programs_no_update"
            )
            connection.execute(
                """
                UPDATE quant_research_programs SET policy_json='{}'
                WHERE id=?
                """,
                (program["id"],),
            )
        with patch.object(
            program_service.quant_selection_service,
            "start_run",
        ) as start_run:
            result = program_service.reconcile_due_programs(
                now=dt.datetime(
                    2026, 1, 3, 12, tzinfo=dt.timezone.utc
                ),
                tenant_id="public",
                user_id="alice",
                program_id=program["id"],
                actor_id="scheduler",
                program_repo=self.program_repo,
                quant_repo=self.quant_repo,
            )
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            result["actions"][0]["action"], "integrity_failed"
        )
        start_run.assert_not_called()
        refreshed = self.program_repo.get_program(
            program["id"],
            tenant_id="public",
            user_id="alice",
        )
        self.assertEqual(
            refreshed["cycles"][0]["error_code"],
            "PROGRAM_INTEGRITY_FAILED",
        )

    def test_postgres_migration_is_registered_and_guarded(self):
        self.assertEqual(
            quant_research_program_v1.MIGRATION_ID,
            "quant-research-program.v1",
        )
        self.assertIn(
            "quant_research_cycles",
            quant_research_program_v1.POSTGRES_DDL,
        )
        self.assertIn(
            "cycle schedule is immutable",
            quant_research_program_v1.POSTGRES_GUARDS,
        )


if __name__ == "__main__":
    unittest.main()
