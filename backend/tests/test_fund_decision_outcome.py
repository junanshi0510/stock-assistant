# -*- coding: utf-8 -*-
"""Decision outcomes use only later confirmed NAV and do not score too early."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_decision_outcome import evaluate_fund_decision_outcome  # noqa: E402
from funds import get_fund_decision_outcome  # noqa: E402


def _points(count: int, *, start=1.0, daily_step=0.01):
    return [
        {"date": f"2026-02-{index + 1:02d}", "unit_nav": start + daily_step * (index + 1)}
        for index in range(count)
    ]


class FundDecisionOutcomeTests(unittest.TestCase):
    @patch("funds._fetch_nav_history")
    def test_service_uses_provider_history_but_scores_only_after_baseline(self, fetch_history):
        fetch_history.return_value = pd.DataFrame([
            {"date": "2026-01-30", "unit_nav": 0.98},
            {"date": "2026-01-31", "unit_nav": 1.0},
            {"date": "2026-02-01", "unit_nav": 1.01},
            {"date": "2026-02-02", "unit_nav": 1.02},
        ])

        result = get_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
        )

        fetch_history.assert_called_once_with("001480", months=120)
        self.assertEqual(result["observed"]["confirmed_nav_count"], 2)
        self.assertEqual(result["observed"]["as_of"], "2026-02-02")
        self.assertEqual(result["provider_as_of"], "2026-02-02")
        self.assertEqual(result["quality"]["provider_observation_count"], 4)
        self.assertTrue(result["quality"]["confirmed_nav_only"])

    def test_no_later_confirmed_nav_remains_pending(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=[{"date": "2026-01-31", "unit_nav": 1.0}],
        )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["observed"]["confirmed_nav_count"], 0)
        self.assertEqual(result["interpretation"]["status"], "too_early")

    def test_short_observation_window_never_claims_success(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=_points(5),
        )

        self.assertEqual(result["status"], "observing")
        self.assertEqual(result["milestones"][0]["status"], "observed")
        self.assertEqual(result["interpretation"]["status"], "too_early")

    def test_add_exposure_is_directionally_evaluable_after_twenty_samples(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=_points(20),
        )

        self.assertEqual(result["status"], "evaluable")
        self.assertEqual(result["interpretation"]["status"], "favorable")
        self.assertEqual(result["milestones"][1]["return_pct"], 20.0)

    def test_wait_action_records_preserved_downside_not_user_profit(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="wait",
            points=_points(20, daily_step=-0.01),
        )

        self.assertEqual(result["interpretation"]["status"], "capital_preserved")
        self.assertIn("不等于用户真实收益", result["interpretation"]["reason"])

    def test_setup_required_is_never_scored_from_market_direction(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="setup_required",
            points=_points(20),
        )

        self.assertEqual(result["interpretation"]["status"], "not_scored")


if __name__ == "__main__":
    unittest.main()
