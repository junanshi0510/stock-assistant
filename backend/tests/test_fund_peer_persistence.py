# -*- coding: utf-8 -*-
"""Fund peer-persistence diagnostics use aligned provider observations only."""

import datetime as dt
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_peer_persistence import (  # noqa: E402
    DIAGNOSTIC_ID,
    DIAGNOSTIC_VERSION,
    evaluate_peer_persistence,
    unavailable_peer_persistence,
)


def _monthly_series(
    *,
    months: int = 36,
    fund_growth: float = 0.01,
    peer_growth: float = 0.02,
) -> dict:
    start = dt.date(2023, 1, 15)
    fund_index = 1.0
    peer_index = 1.0
    fund_points = []
    peer_points = []
    for offset in range(months + 1):
        month_index = start.year * 12 + start.month - 1 + offset
        year, month_zero = divmod(month_index, 12)
        observed_on = dt.date(year, month_zero + 1, 15).isoformat()
        if offset:
            fund_index *= 1 + fund_growth
            peer_index *= 1 + peer_growth
        fund_points.append({
            "date": observed_on,
            "cumulative_return_pct": (fund_index - 1) * 100,
        })
        peer_points.append({
            "date": observed_on,
            "cumulative_return_pct": (peer_index - 1) * 100,
        })
    return {"fund_points": fund_points, "points": peer_points}


class FundPeerPersistenceTests(unittest.TestCase):
    def test_persistent_lag_opens_review_but_never_redemption(self):
        result = evaluate_peer_persistence(_monthly_series())

        self.assertEqual(result["diagnostic_id"], DIAGNOSTIC_ID)
        self.assertEqual(result["diagnostic_version"], DIAGNOSTIC_VERSION)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["diagnosis"]["status"], "replacement_review")
        self.assertTrue(result["replacement_review"]["triggered"])
        self.assertFalse(result["replacement_review"]["automatic_redemption_allowed"])
        self.assertEqual(len(result["horizons"]), 3)
        self.assertEqual(len(result["quarters"]), 2)
        self.assertTrue(all(item["excess_return_pp"] < 0 for item in result["quarters"]))
        self.assertEqual(result["method"]["alignment"], "exact_common_provider_dates")

    def test_absolute_loss_that_beats_peer_is_not_replacement_trigger(self):
        result = evaluate_peer_persistence(
            _monthly_series(fund_growth=-0.005, peer_growth=-0.01)
        )

        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["diagnosis"]["status"], "relative_strength")
        self.assertFalse(result["replacement_review"]["triggered"])
        self.assertTrue(all(item["fund_return_pct"] < 0 for item in result["horizons"]))
        self.assertTrue(all(item["excess_return_pp"] > 0 for item in result["horizons"]))

    def test_missing_annual_window_can_only_produce_watch(self):
        result = evaluate_peer_persistence(_monthly_series(months=8))

        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["diagnosis"]["status"], "underperformance_watch")
        self.assertFalse(result["replacement_review"]["triggered"])
        annual = next(item for item in result["horizons"] if item["window"] == "12m")
        self.assertEqual(annual["status"], "insufficient_coverage")

    def test_real_stage_annual_is_accepted_only_after_dual_window_crosscheck(self):
        payload = _monthly_series(months=8)
        baseline = evaluate_peer_persistence(payload)
        available = {
            item["window"]: item
            for item in baseline["horizons"]
            if item["status"] == "available"
        }
        payload["stage_comparison"] = {
            "status": "available",
            "source": "东方财富基金阶段涨幅",
            "source_url": "https://fundf10.eastmoney.com/stage",
            "periods": {
                window: {
                    "fund_return_pct": available[window]["fund_return_pct"],
                    "peer_return_pct": available[window]["peer_return_pct"],
                }
                for window in ("3m", "6m")
            } | {
                "12m": {
                    "fund_return_pct": 5.0,
                    "peer_return_pct": 10.0,
                }
            },
        }

        result = evaluate_peer_persistence(payload)

        annual = next(item for item in result["horizons"] if item["window"] == "12m")
        self.assertEqual(annual["status"], "available")
        self.assertEqual(annual["period_basis"], "provider_defined_trailing_period")
        self.assertEqual(result["stage_validation"]["status"], "verified")
        self.assertTrue(result["replacement_review"]["triggered"])

    def test_mismatched_stage_periods_cannot_open_replacement_review(self):
        payload = _monthly_series(months=8)
        baseline = evaluate_peer_persistence(payload)
        available = {
            item["window"]: item
            for item in baseline["horizons"]
            if item["status"] == "available"
        }
        payload["stage_comparison"] = {
            "status": "available",
            "periods": {
                "3m": {
                    "fund_return_pct": available["3m"]["fund_return_pct"] + 1,
                    "peer_return_pct": available["3m"]["peer_return_pct"],
                },
                "6m": {
                    "fund_return_pct": available["6m"]["fund_return_pct"],
                    "peer_return_pct": available["6m"]["peer_return_pct"],
                },
                "12m": {"fund_return_pct": 5.0, "peer_return_pct": 10.0},
            },
        }

        result = evaluate_peer_persistence(payload)

        annual = next(item for item in result["horizons"] if item["window"] == "12m")
        self.assertEqual(annual["status"], "insufficient_coverage")
        self.assertEqual(result["stage_validation"]["status"], "rejected")
        self.assertFalse(result["replacement_review"]["triggered"])

    def test_non_overlapping_dates_do_not_use_nearest_proxy_series(self):
        payload = _monthly_series(months=18)
        for item in payload["points"]:
            item["date"] = (dt.date.fromisoformat(item["date"]) + dt.timedelta(days=1)).isoformat()

        result = evaluate_peer_persistence(payload)

        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["reason"], "aligned_observations_below_minimum")
        self.assertEqual(result["coverage"]["aligned_observation_count"], 0)
        self.assertEqual(result["horizons"], [])

    def test_provider_failure_has_no_proxy_observations(self):
        result = unavailable_peer_persistence("provider_native_peer_series_unavailable")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "provider_native_peer_series_unavailable")
        self.assertEqual(result["coverage"]["aligned_observation_count"], 0)
        self.assertEqual(result["horizons"], [])
        self.assertFalse(result["replacement_review"]["triggered"])


if __name__ == "__main__":
    unittest.main()
