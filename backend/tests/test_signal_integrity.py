# -*- coding: utf-8 -*-
"""Rule scores and experimental ML diagnostics must not impersonate probabilities."""

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
import market_data_operations  # noqa: E402
import ml_model  # noqa: E402


def history(rows: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(20260722)
    returns = rng.normal(0.00035, 0.012, rows)
    close = 100 * np.cumprod(1 + returns)
    spread = rng.uniform(0.002, 0.018, rows)
    dates = pd.bdate_range("2024-01-02", periods=rows)
    return pd.DataFrame({
        "date": dates,
        "open": close * (1 + rng.normal(0, 0.003, rows)),
        "high": close * (1 + spread),
        "low": close * (1 - spread),
        "close": close,
        "volume": rng.integers(100_000, 500_000, rows),
    })


class SignalIntegrityTests(unittest.TestCase):
    def test_rule_score_exposes_state_but_no_probability(self):
        result = analysis.score(history())

        self.assertNotIn("probability", result)
        self.assertIn(result["direction"], {"技术偏强", "技术偏弱", "技术中性"})
        self.assertFalse(result["signal_integrity"]["calibrated_probability"])
        self.assertFalse(result["signal_integrity"]["decision_eligible"])
        self.assertTrue(result["signal_integrity"]["validation_required"])

    def test_stock_analysis_api_contract_carries_integrity_statement(self):
        frame = history()
        with patch.object(
            market_data_operations.data_fetch,
            "get_history_months",
            return_value=frame,
        ):
            result = market_data_operations._analyze_stock({
                "market": "A股",
                "symbol": "600000",
                "months": 12,
            })

        self.assertNotIn("probability", result)
        self.assertEqual(result["signal_integrity"]["kind"], "rule_based_technical_state")
        self.assertGreater(len(result["candles"]), 300)

    def test_experimental_ml_only_returns_historical_validation(self):
        result = ml_model.predict(history(520), horizon=10)

        self.assertNotIn("latest_up_probability", result)
        self.assertFalse(result["latest_forecast_available"])
        self.assertFalse(result["calibrated_probability"])
        self.assertFalse(result["decision_eligible"])
        self.assertEqual(result["research_status"], "historical_validation_only")


if __name__ == "__main__":
    unittest.main()
