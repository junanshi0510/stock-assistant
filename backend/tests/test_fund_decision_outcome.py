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
from funds import _fund_peer_comparison_series, get_fund_decision_outcome  # noqa: E402


def _points(count: int, *, start=1.0, daily_step=0.01):
    return [
        {"date": f"2026-02-{index + 1:02d}", "unit_nav": start + daily_step * (index + 1)}
        for index in range(count)
    ]


def _peer_series(
    *, baseline=0.0, daily_step=0.5, fund_baseline=0.0, fund_daily_step=1.0, count=20
):
    return {
        "name": "同类平均",
        "source": "东方财富基金详情页 Data_grandTotal",
        "source_url": "https://fund.eastmoney.com/001480.html",
        "points": [
            {"date": "2026-01-31", "cumulative_return_pct": baseline},
            *[
                {
                    "date": f"2026-02-{index + 1:02d}",
                    "cumulative_return_pct": baseline + daily_step * (index + 1),
                }
                for index in range(count)
            ],
        ],
        "fund_points": [
            {"date": "2026-01-31", "cumulative_return_pct": fund_baseline},
            *[
                {
                    "date": f"2026-02-{index + 1:02d}",
                    "cumulative_return_pct": fund_baseline + fund_daily_step * (index + 1),
                }
                for index in range(count)
            ],
        ],
    }


class FundDecisionOutcomeTests(unittest.TestCase):
    @patch("funds._fund_peer_comparison_series")
    @patch("funds._fetch_nav_history")
    def test_service_uses_provider_history_and_native_peer_series(
        self, fetch_history, fetch_peer
    ):
        fetch_history.return_value = pd.DataFrame([
            {"date": "2026-01-30", "unit_nav": 0.98},
            {"date": "2026-01-31", "unit_nav": 1.0},
            {"date": "2026-02-01", "unit_nav": 1.01},
            {"date": "2026-02-02", "unit_nav": 1.02},
        ])
        fetch_peer.return_value = {
            **_peer_series(count=2),
            "series_start": "2026-01-31",
            "series_end": "2026-02-02",
            "observation_count": 3,
        }

        result = get_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
        )

        fetch_history.assert_called_once_with("001480", months=120)
        fetch_peer.assert_called_once_with("001480")
        self.assertEqual(result["observed"]["confirmed_nav_count"], 2)
        self.assertEqual(result["observed"]["as_of"], "2026-02-02")
        self.assertEqual(result["provider_as_of"], "2026-02-02")
        self.assertEqual(result["quality"]["provider_observation_count"], 4)
        self.assertTrue(result["quality"]["confirmed_nav_only"])
        self.assertEqual(result["quality"]["status"], "complete")
        self.assertEqual(result["peer_comparison"]["status"], "available")
        self.assertEqual(result["peer_comparison"]["period_return_pct"], 1.0)
        self.assertEqual(result["peer_comparison"]["return_spread_pp"], 1.0)

    @patch("funds._fund_peer_comparison_series", side_effect=RuntimeError("upstream missing"))
    @patch("funds._fetch_nav_history")
    def test_service_keeps_absolute_outcome_but_marks_missing_peer_partial(
        self, fetch_history, _fetch_peer
    ):
        fetch_history.return_value = pd.DataFrame([
            {"date": "2026-01-31", "unit_nav": 1.0},
            {"date": "2026-02-01", "unit_nav": 1.01},
        ])

        result = get_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
        )

        self.assertEqual(result["observed"]["return_pct"], 1.0)
        self.assertEqual(result["peer_comparison"]["status"], "unavailable")
        self.assertIn("upstream missing", result["peer_comparison"]["reason"])
        self.assertEqual(result["quality"]["status"], "partial")
        self.assertTrue(result["quality"]["no_proxy_fallback"])
        self.assertEqual(result["source"], "东方财富基金确认净值")

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

    def test_nonzero_peer_baseline_produces_period_and_relative_returns(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=_points(20),
            peer_series=_peer_series(
                baseline=10.0,
                daily_step=0.55,
                fund_baseline=5.0,
                fund_daily_step=1.05,
            ),
        )

        peer = result["peer_comparison"]
        self.assertEqual(peer["status"], "available")
        self.assertEqual(peer["period_return_pct"], 10.0)
        self.assertEqual(peer["fund_return_pct"], 20.0)
        self.assertEqual(peer["return_spread_pp"], 10.0)
        self.assertEqual(peer["relative_excess_return_pct"], 9.0909)
        self.assertEqual(peer["unit_nav_return_pct"], 20.0)
        self.assertEqual(result["interpretation"]["status"], "favorable_with_peer_edge")
        self.assertEqual(result["evaluator_version"], "1.1.0")

    def test_positive_fund_return_below_peer_is_not_called_selection_edge(self):
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=_points(20),
            peer_series=_peer_series(
                baseline=0.0,
                daily_step=1.5,
                fund_baseline=0.0,
                fund_daily_step=1.25,
            ),
        )

        self.assertEqual(result["observed"]["return_pct"], 20.0)
        self.assertEqual(result["peer_comparison"]["period_return_pct"], 30.0)
        self.assertEqual(result["peer_comparison"]["fund_return_pct"], 25.0)
        self.assertLess(result["peer_comparison"]["relative_excess_return_pct"], 0)
        self.assertEqual(result["interpretation"]["status"], "positive_but_lagging_peer")

    def test_missing_exact_observation_date_refuses_relative_comparison(self):
        peer = _peer_series(count=20)
        peer["points"] = [
            item for item in peer["points"] if item["date"] != "2026-02-20"
        ]
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=_points(20),
            peer_series=peer,
        )

        self.assertEqual(result["peer_comparison"]["status"], "unavailable")
        self.assertEqual(
            result["peer_comparison"]["reason"],
            "observed_date_not_in_provider_comparable_series",
        )
        self.assertEqual(result["interpretation"]["status"], "favorable")
        self.assertIn("同类基准不可用", result["interpretation"]["reason"])

    def test_missing_exact_baseline_date_never_uses_nearest_peer_date(self):
        peer = _peer_series(count=20)
        peer["points"] = peer["points"][1:]
        result = evaluate_fund_decision_outcome(
            code="001480",
            baseline_as_of="2026-01-31",
            baseline_nav=1.0,
            action="consider_tranche",
            points=_points(20),
            peer_series=peer,
        )

        self.assertEqual(result["peer_comparison"]["status"], "unavailable")
        self.assertEqual(
            result["peer_comparison"]["reason"],
            "baseline_date_not_in_provider_comparable_series",
        )
        self.assertEqual(result["method"]["benchmark_alignment"], "exact_provider_date_only")

    @patch("funds._fetch_detail_js")
    def test_peer_parser_accepts_only_explicit_provider_label(self, fetch_detail):
        fetch_detail.return_value = (
            'var fS_name = "测试基金"; var Data_grandTotal = [{"name":"测试基金","data":[[1769817600000,0],[1769904000000,1]]},'
            '{"name":"同类平均","data":[[1769817600000,10],[1769904000000,11.1]]}];/* end */'
        )
        result = _fund_peer_comparison_series("001480")

        self.assertEqual(result["name"], "同类平均")
        self.assertEqual(result["observation_count"], 2)
        self.assertEqual(result["points"][0]["cumulative_return_pct"], 10.0)
        self.assertEqual(result["fund_points"][1]["cumulative_return_pct"], 1.0)

    @patch("funds._fetch_detail_js")
    def test_peer_parser_rejects_market_index_as_fallback(self, fetch_detail):
        fetch_detail.return_value = (
            'var fS_name = "测试基金"; var Data_grandTotal = [{"name":"测试基金","data":[[1769817600000,0],[1769904000000,1]]},'
            '{"name":"沪深300","data":[[1769817600000,10],[1769904000000,11.1]]}];/* end */'
        )

        with self.assertRaisesRegex(RuntimeError, "没有明确标记"):
            _fund_peer_comparison_series("001480")

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
