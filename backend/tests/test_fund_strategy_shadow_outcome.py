# -*- coding: utf-8 -*-
"""Shadow outcomes use exact frozen baselines and exact observation horizons."""

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_strategy_shadow_outcome import (  # noqa: E402
    evaluate_fund_strategy_shadow_outcome,
)


def _evaluate(**overrides):
    payload = {
        "code": "013403",
        "baseline_as_of": "2026-01-02",
        "baseline_nav": 1.0,
        "signal_direction": "positive",
        "horizon": "3m",
        "observation_days": 2,
        "points": [
            {"date": "2026-01-02", "unit_nav": 1.0},
            {"date": "2026-01-05", "unit_nav": 1.05},
            {"date": "2026-01-06", "unit_nav": 1.10},
            {"date": "2026-01-07", "unit_nav": 0.80},
        ],
    }
    payload.update(overrides)
    return evaluate_fund_strategy_shadow_outcome(**payload)


class FundStrategyShadowOutcomeTests(unittest.TestCase):
    def test_waits_until_exact_confirmed_nav_observation_count(self):
        result = _evaluate(
            observation_days=4,
            points=[
                {"date": "2026-01-02", "unit_nav": 1.0},
                {"date": "2026-01-05", "unit_nav": 1.02},
                {"date": "2026-01-06", "unit_nav": 1.03},
            ],
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["progress"]["available_observations"], 2)
        self.assertEqual(result["progress"]["required_observations"], 4)

    def test_scores_the_nth_observation_and_ignores_later_prices(self):
        result = _evaluate()
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["observed"]["as_of"], "2026-01-06")
        self.assertEqual(result["observed"]["unit_nav_return_pct"], 10.0)
        self.assertTrue(result["score"]["directionally_correct"])
        self.assertEqual(result["score"]["signed_unit_nav_return_pct"], 10.0)

    def test_does_not_substitute_a_nearby_baseline_date(self):
        result = _evaluate(
            baseline_as_of="2026-01-03",
            observation_days=1,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "baseline_date_missing_from_provider_history")

    def test_provider_revision_does_not_overwrite_frozen_baseline(self):
        result = _evaluate(baseline_nav=1.2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "baseline_nav_provider_revision")
        self.assertEqual(result["provider_baseline_nav"], 1.0)

    def test_peer_edge_uses_exact_provider_dates(self):
        result = _evaluate(
            peer_series={
                "name": "同类平均",
                "source": "provider",
                "source_url": "https://example.test/source",
                "points": [
                    {"date": "2026-01-02", "cumulative_return_pct": 20},
                    {"date": "2026-01-06", "cumulative_return_pct": 44},
                ],
                "fund_points": [
                    {"date": "2026-01-02", "cumulative_return_pct": 10},
                    {"date": "2026-01-06", "cumulative_return_pct": 21},
                ],
            },
        )
        self.assertEqual(result["peer_comparison"]["status"], "available")
        self.assertEqual(result["peer_comparison"]["period_return_pct"], 20.0)
        self.assertEqual(result["peer_comparison"]["fund_return_pct"], 10.0)
        self.assertFalse(result["score"]["peer_edge_correct"])
        self.assertTrue(result["score"]["release_grade"])

    def test_negative_direction_scores_falling_nav_and_peer_underperformance(self):
        result = _evaluate(
            signal_direction="negative",
            points=[
                {"date": "2026-01-02", "unit_nav": 1.0},
                {"date": "2026-01-05", "unit_nav": 0.95},
                {"date": "2026-01-06", "unit_nav": 0.90},
            ],
            peer_series={
                "points": [
                    {"date": "2026-01-02", "cumulative_return_pct": 0},
                    {"date": "2026-01-06", "cumulative_return_pct": -5},
                ],
                "fund_points": [
                    {"date": "2026-01-02", "cumulative_return_pct": 0},
                    {"date": "2026-01-06", "cumulative_return_pct": -10},
                ],
            },
        )
        self.assertTrue(result["score"]["directionally_correct"])
        self.assertEqual(result["score"]["signed_unit_nav_return_pct"], 10.0)
        self.assertTrue(result["score"]["peer_edge_correct"])

    def test_mixed_signal_is_not_scorable(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            _evaluate(signal_direction="mixed")


if __name__ == "__main__":
    unittest.main()
