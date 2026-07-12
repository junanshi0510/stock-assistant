# -*- coding: utf-8 -*-
"""Versioned fund strategy must use historical observations without look-ahead."""

import datetime as dt
import math
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_conditioned_forward import (  # noqa: E402
    STRATEGY_ID,
    STRATEGY_VERSION,
    classify_condition,
    evaluate_conditioned_forward_strategy,
)


def _business_day_points(count: int) -> list[tuple[str, float]]:
    date_value = dt.date(2018, 1, 1)
    points = []
    index = 0
    while len(points) < count:
        if date_value.weekday() < 5:
            trend = 1.0 + index * 0.00035
            cycle = 0.16 * math.sin(index / 38) + 0.05 * math.sin(index / 11)
            points.append((date_value.isoformat(), round(trend + cycle, 6)))
            index += 1
        date_value += dt.timedelta(days=1)
    return points


class FundConditionedStrategyTests(unittest.TestCase):
    def test_strategy_reports_analogs_baseline_and_versioned_method(self):
        points = _business_day_points(1800)
        result = evaluate_conditioned_forward_strategy(points)

        self.assertEqual(result["strategy_id"], STRATEGY_ID)
        self.assertEqual(result["strategy_version"], STRATEGY_VERSION)
        self.assertEqual(result["status"], "evaluated")
        self.assertIn(result["decision"], {"research", "avoid_for_now", "hold_review"})
        self.assertIn(result["signal"]["direction"], {"positive", "negative", "mixed"})
        self.assertIn(result["confidence"]["level"], {"low", "medium"})
        self.assertEqual(result["method"]["sampling"], "calendar_month_last_observation")
        self.assertEqual(result["method"]["matching_fields"], ["trend", "drawdown_band"])
        self.assertEqual(len(result["horizons"]), 3)

        available = [item for item in result["horizons"] if item["status"] == "available"]
        self.assertTrue(available)
        for item in available:
            self.assertGreaterEqual(item["analog"]["sample_count"], 6)
            self.assertGreaterEqual(
                item["baseline"]["sample_count"],
                item["analog"]["sample_count"],
            )
            self.assertLess(item["analog"]["sample_end"], result["coverage"]["end_date"])
            self.assertIsNotNone(item["edge"]["positive_rate"])

    def test_condition_at_historical_index_does_not_read_future_values(self):
        points = _business_day_points(500)
        historical_index = 260
        before = classify_condition(points, historical_index)
        mutated = list(points)
        mutated[historical_index + 1:] = [
            (date_value, nav * 10)
            for date_value, nav in mutated[historical_index + 1:]
        ]
        after = classify_condition(mutated, historical_index)

        self.assertEqual(before, after)

    def test_short_history_abstains_instead_of_guessing(self):
        result = evaluate_conditioned_forward_strategy(_business_day_points(59))

        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["decision"], "data_required")
        self.assertEqual(result["signal"]["direction"], "unavailable")
        self.assertEqual(result["confidence"]["level"], "unavailable")
        self.assertEqual(result["horizons"], [])


if __name__ == "__main__":
    unittest.main()
