# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import analysis  # noqa: E402
import backtest  # noqa: E402


def execution_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class ExecutionBacktestTests(unittest.TestCase):
    def test_signal_enters_next_open_and_costs_reduce_return(self):
        frame = execution_frame([
            {"date": "2026-01-02", "open": 99, "high": 101, "low": 98, "close": 100, "atr": 1},
            {"date": "2026-01-05", "open": 100, "high": 103.5, "low": 99, "close": 103, "atr": 1},
            {"date": "2026-01-06", "open": 103, "high": 104, "low": 102, "close": 103, "atr": 1},
            {"date": "2026-01-07", "open": 103, "high": 104, "low": 102, "close": 103, "atr": 1},
        ])

        result = backtest.simulate_long_execution(
            frame,
            {0: 70},
            horizon=3,
            stop_atr=2,
            target_atr=3,
            commission_bps=5,
            slippage_bps=5,
        )

        self.assertEqual(result["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], "2026-01-02")
        self.assertEqual(trade["entry_date"], "2026-01-05")
        self.assertEqual(trade["exit_reason"], "target")
        self.assertAlmostEqual(trade["gross_return_pct"], 3.0, places=3)
        self.assertLess(trade["net_return_pct"], trade["gross_return_pct"])
        self.assertGreater(result["average_cost_drag_pct"], 0)

    def test_same_bar_stop_and_target_uses_conservative_stop_first(self):
        frame = execution_frame([
            {"date": "2026-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "atr": 1},
            {"date": "2026-01-05", "open": 100, "high": 104, "low": 97, "close": 101, "atr": 1},
            {"date": "2026-01-06", "open": 101, "high": 102, "low": 100, "close": 101, "atr": 1},
            {"date": "2026-01-07", "open": 101, "high": 102, "low": 100, "close": 101, "atr": 1},
        ])

        result = backtest.simulate_long_execution(
            frame,
            {0: 70},
            horizon=3,
            stop_atr=2,
            target_atr=3,
            commission_bps=0,
            slippage_bps=0,
        )

        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "stop_first_ambiguous")
        self.assertEqual(trade["exit_price"], 98.0)
        self.assertEqual(result["same_bar_ambiguous_count"], 1)
        self.assertTrue(trade["same_bar_path_ambiguous"])

    def test_gap_through_stop_can_exceed_account_risk_budget(self):
        frame = execution_frame([
            {"date": "2026-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "atr": 1},
            {"date": "2026-01-05", "open": 100, "high": 101, "low": 99, "close": 100, "atr": 1},
            {"date": "2026-01-06", "open": 95, "high": 96, "low": 94, "close": 95, "atr": 1},
            {"date": "2026-01-07", "open": 95, "high": 96, "low": 94, "close": 95, "atr": 1},
        ])

        result = backtest.simulate_long_execution(
            frame,
            {0: 70},
            horizon=3,
            stop_atr=2,
            target_atr=3,
            commission_bps=0,
            slippage_bps=0,
            risk_per_trade_pct=1,
            max_position_pct=100,
        )

        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "gap_stop")
        self.assertEqual(trade["position_pct"], 50.0)
        self.assertAlmostEqual(trade["account_return_pct"], -2.5, places=3)
        self.assertTrue(trade["risk_budget_breached"])
        self.assertEqual(result["risk_budget_breach_count"], 1)

    def test_full_backtest_keeps_directional_stats_and_adds_execution_result(self):
        dates = pd.bdate_range("2025-01-02", periods=150)
        close = np.linspace(100, 140, len(dates))
        frame = pd.DataFrame({
            "date": dates,
            "open": close - 0.1,
            "high": close + 1.2,
            "low": close - 1.2,
            "close": close,
            "volume": np.linspace(1_000_000, 1_300_000, len(dates)),
        })

        with patch.object(analysis, "_evaluate", return_value=(70.0, [])):
            result = backtest.backtest(
                frame,
                horizon=10,
                entry_score=65,
                commission_bps=5,
                slippage_bps=5,
            )

        self.assertGreater(result["samples"], 0)
        self.assertIn("directional_accuracy", result)
        self.assertEqual(
            result["execution"]["policy_version"],
            "stock_signal_execution_backtest@1.0.0",
        )
        self.assertGreater(result["execution"]["trade_count"], 0)
        self.assertEqual(result["methodology"]["execution_entry"], "next_trading_day_open")

    def test_execution_parameters_are_bounded(self):
        frame = execution_frame([
            {"date": "2026-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "atr": 1},
            {"date": "2026-01-05", "open": 100, "high": 101, "low": 99, "close": 100, "atr": 1},
        ])
        with self.assertRaisesRegex(ValueError, "risk_per_trade_pct"):
            backtest.simulate_long_execution(
                frame,
                {0: 70},
                risk_per_trade_pct=9,
            )


if __name__ == "__main__":
    unittest.main()
