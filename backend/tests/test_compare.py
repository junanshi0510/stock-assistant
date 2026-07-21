# -*- coding: utf-8 -*-
"""指数对比统计口径和图表边界必须稳定。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import compare  # noqa: E402


def frame(values):
    dates = pd.bdate_range("2024-01-02", periods=len(values))
    return pd.DataFrame({"date": dates, "close": values})


class CompareTests(unittest.TestCase):
    def test_identical_series_has_beta_one_and_includes_latest_chart_point(self):
        values = 100 * np.cumprod(1 + np.sin(np.arange(320)) * 0.002 + 0.0004)
        stock = frame(values)
        benchmark = frame(values)
        with patch.object(compare, "_get_stock", return_value=(stock, "测试个股源")), \
             patch.object(compare, "_get_benchmark", return_value=("测试指数", benchmark, "测试源", False)):
            result = compare.compare("A股", "600000", 12)

        self.assertAlmostEqual(result["beta"], 1.0, places=2)
        self.assertAlmostEqual(result["correlation"], 1.0, places=2)
        self.assertEqual(result["weighted_excess"], 0.0)
        self.assertEqual(result["verdict"], "与大盘基本同步")
        self.assertEqual(result["rebased"][-1]["date"], stock.iloc[-1]["date"].strftime("%Y-%m-%d"))

    def test_multi_period_relative_strength_drives_verdict(self):
        benchmark_values = np.linspace(100, 103, 320)
        stock_values = np.linspace(100, 130, 320)
        stock = frame(stock_values)
        benchmark = frame(benchmark_values)
        with patch.object(compare, "_get_stock", return_value=(stock, "测试个股源")), \
             patch.object(compare, "_get_benchmark", return_value=("测试指数", benchmark, "测试源", False)):
            result = compare.compare("A股", "600000", 12)

        self.assertGreater(result["weighted_excess"], 3)
        self.assertEqual(result["periods_outperformed"], result["periods_available"])
        self.assertEqual(result["verdict"], "跑赢大盘(相对强势)")
        self.assertIn("tracking_error", result)
        self.assertIn("information_ratio", result)


if __name__ == "__main__":
    unittest.main()
