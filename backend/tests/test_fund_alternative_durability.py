# -*- coding: utf-8 -*-
"""Rolling alternative durability uses only provider daily-return evidence."""

import datetime as dt
import math
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_alternative_durability import (  # noqa: E402
    DIAGNOSTIC_ID,
    DIAGNOSTIC_VERSION,
    evaluate_alternative_durability,
)


def _series(code: str, return_fn, *, days: int = 1100, offset_days: int = 0, step_days: int = 1):
    start = dt.date(2023, 1, 1) + dt.timedelta(days=offset_days)
    return {
        "code": code,
        "name": f"基金{code}",
        "points": [
            {
                "date": (start + dt.timedelta(days=index * step_days)).isoformat(),
                "daily_return_pct": return_fn(index),
            }
            for index in range(days)
        ],
    }


def _selected_return(index: int) -> float:
    return 0.01 + math.sin(index / 23) * 0.08


class FundAlternativeDurabilityTests(unittest.TestCase):
    def test_persistent_candidate_opens_due_diligence_but_never_trading(self):
        selected = _series("000001", _selected_return)
        candidate = _series("000002", lambda index: _selected_return(index) + 0.02)

        result = evaluate_alternative_durability(selected, [candidate])
        row = result["candidates"][0]

        self.assertEqual(result["diagnostic_id"], DIAGNOSTIC_ID)
        self.assertEqual(result["diagnostic_version"], DIAGNOSTIC_VERSION)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(row["status"], "durable_advantage")
        self.assertGreaterEqual(row["rolling"]["6m"]["win_rate_pct"], 60)
        self.assertGreaterEqual(row["rolling"]["12m"]["win_rate_pct"], 60)
        self.assertTrue(row["decision_gate"]["eligible_for_due_diligence"])
        self.assertFalse(row["decision_gate"]["automatic_purchase_allowed"])
        self.assertFalse(row["decision_gate"]["automatic_redemption_allowed"])

    def test_recent_surge_without_long_history_is_not_durable_advantage(self):
        selected = _series("000001", _selected_return)
        candidate = _series(
            "000002",
            lambda index: _selected_return(index) + (0.25 if index >= 850 else -0.03),
        )

        row = evaluate_alternative_durability(selected, [candidate])["candidates"][0]

        self.assertEqual(row["status"], "recent_leader_only")
        self.assertFalse(row["decision_gate"]["eligible_for_due_diligence"])
        self.assertGreater(row["rolling"]["6m"]["latest"]["excess_return_pp"], 0)

    def test_persistent_candidate_in_extreme_hot_zone_is_held_back(self):
        selected = _series("000001", _selected_return)
        candidate = _series(
            "000002",
            lambda index: _selected_return(index) + (0.12 if index >= 900 else 0.02),
        )

        row = evaluate_alternative_durability(selected, [candidate])["candidates"][0]

        self.assertEqual(row["status"], "advantage_but_hot")
        self.assertTrue(row["risk"]["hot_entry_risk"])
        self.assertFalse(row["decision_gate"]["eligible_for_due_diligence"])

    def test_provider_daily_return_gap_stops_candidate_diagnosis(self):
        selected = _series("000001", _selected_return)
        candidate = _series("000002", lambda index: _selected_return(index) + 0.02)
        candidate["points"][500]["daily_return_pct"] = None

        result = evaluate_alternative_durability(selected, [candidate])
        row = result["candidates"][0]

        self.assertEqual(result["status"], "partial")
        self.assertEqual(row["status"], "insufficient_data")
        self.assertEqual(row["decision_gate"]["reason"], "provider_daily_return_gap")

    def test_non_overlapping_provider_dates_are_not_nearest_date_matched(self):
        selected = _series("000001", _selected_return, days=600, step_days=2)
        candidate = _series(
            "000002",
            lambda index: _selected_return(index) + 0.02,
            days=600,
            offset_days=1,
            step_days=2,
        )

        row = evaluate_alternative_durability(selected, [candidate])["candidates"][0]

        self.assertEqual(row["status"], "insufficient_data")
        self.assertEqual(row["coverage"]["common_date_count"], 0)
        self.assertFalse(row["decision_gate"]["eligible_for_due_diligence"])

    def test_invalid_selected_series_stops_the_whole_diagnostic(self):
        selected = _series("000001", _selected_return, days=30)
        candidate = _series("000002", lambda index: _selected_return(index) + 0.02)

        result = evaluate_alternative_durability(selected, [candidate])

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["candidates"], [])
        self.assertIn("不使用单位净值", result["policy"])


if __name__ == "__main__":
    unittest.main()
