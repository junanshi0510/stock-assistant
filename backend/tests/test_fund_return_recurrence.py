# -*- coding: utf-8 -*-
"""Fund return recurrence must distinguish a prior episode from today's run."""

import datetime as dt
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402
from strategies.fund_return_recurrence import (  # noqa: E402
    METRIC_ID,
    METRIC_VERSION,
    evaluate_fund_return_recurrence,
    find_previous_return_occurrence,
)


def _business_day_points(count: int) -> list[tuple[str, float]]:
    date_value = dt.date(2022, 1, 3)
    points = []
    index = 0
    while len(points) < count:
        if date_value.weekday() < 5:
            nav = 1.0 + index * 0.00045 + 0.12 * math.sin(index / 27)
            points.append((date_value.isoformat(), round(nav, 6)))
            index += 1
        date_value += dt.timedelta(days=1)
    return points


class FundReturnRecurrenceTests(unittest.TestCase):
    def test_skips_current_band_and_finds_prior_independent_episode(self):
        rows = [
            {"date": "2026-01-01", "return": 9.9},
            {"date": "2026-01-02", "return": 10.1},
            {"date": "2026-01-03", "return": 5.0},
            {"date": "2026-01-04", "return": 3.0},
            {"date": "2026-01-05", "return": 4.0},
            {"date": "2026-01-06", "return": 5.0},
            {"date": "2026-01-07", "return": 6.0},
            {"date": "2026-01-08", "return": 9.95},
            {"date": "2026-01-09", "return": 10.0},
        ]

        result = find_previous_return_occurrence(rows, 10.0)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["current_episode"]["start_date"], "2026-01-08")
        self.assertEqual(result["current_episode"]["observation_count"], 2)
        self.assertEqual(result["previous"]["date"], "2026-01-02")
        self.assertEqual(result["previous"]["return"], 10.1)
        self.assertEqual(result["previous"]["difference_pp"], 0.1)

    def test_nearest_fallback_is_not_claimed_as_same_level(self):
        rows = [
            {"date": "2026-01-01", "return": 8.0},
            {"date": "2026-01-02", "return": 9.0},
            {"date": "2026-01-03", "return": 5.0},
            {"date": "2026-01-04", "return": 4.0},
            {"date": "2026-01-05", "return": 5.0},
            {"date": "2026-01-06", "return": 6.0},
            {"date": "2026-01-07", "return": 7.0},
            {"date": "2026-01-08", "return": 12.0},
        ]

        result = find_previous_return_occurrence(rows, 12.0)

        self.assertEqual(result["status"], "nearest_only")
        self.assertEqual(result["previous"]["date"], "2026-01-02")
        self.assertEqual(result["previous"]["absolute_difference_pp"], 3.0)
        self.assertEqual(
            result["previous"]["method"],
            "nearest_prior_independent_observation",
        )

    def test_all_standard_windows_return_versioned_recurrence(self):
        result = evaluate_fund_return_recurrence(_business_day_points(420))

        self.assertEqual(result["metric_id"], METRIC_ID)
        self.assertEqual(result["metric_version"], METRIC_VERSION)
        self.assertEqual([item["key"] for item in result["items"]], ["1m", "3m", "6m", "1y"])
        self.assertTrue(all(item["status"] == "available" for item in result["items"]))
        for item in result["items"]:
            self.assertIsNotNone(item["current_return"])
            self.assertIn(
                item["recurrence"]["status"],
                {"matched", "nearest_only", "no_prior_history"},
            )

    def test_short_window_is_explicitly_unavailable(self):
        result = evaluate_fund_return_recurrence(_business_day_points(100))
        one_year = next(item for item in result["items"] if item["key"] == "1y")

        self.assertEqual(one_year["status"], "insufficient_history")
        self.assertIsNone(one_year["current_return"])
        self.assertIsNone(one_year["recurrence"])

    def test_fund_analysis_exposes_same_recurrence_used_by_timing_table(self):
        points = _business_day_points(420)
        frame = pd.DataFrame([
            {
                "date": date_value,
                "unit_nav": nav,
                "acc_nav": nav,
                "daily_return": None,
                "subscribe_status": "开放申购",
                "redeem_status": "开放赎回",
            }
            for date_value, nav in points
        ])
        with patch.object(funds, "_fetch_nav_history", return_value=frame), \
             patch.object(funds, "_fund_fact_sheet", return_value={"name": "真实净值测试基金"}):
            result = funds.analyze_fund("001480", 36, include_profile=False)

        recurrence = result["return_recurrence"]
        self.assertEqual(recurrence["metric_id"], METRIC_ID)
        self.assertEqual(result["timing"]["rolling_returns"], recurrence["items"])
        self.assertEqual(len(recurrence["items"]), 4)


if __name__ == "__main__":
    unittest.main()
