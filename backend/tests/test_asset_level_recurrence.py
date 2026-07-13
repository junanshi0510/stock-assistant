# -*- coding: utf-8 -*-
"""Live price/NAV level recurrence must preserve source and time granularity."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import quotes  # noqa: E402
from strategies.asset_level_recurrence import (  # noqa: E402
    METRIC_ID,
    METRIC_VERSION,
    evaluate_fund_level_recurrence,
    evaluate_stock_level_recurrence,
)


class AssetLevelRecurrenceTests(unittest.TestCase):
    def test_live_quote_uses_tencent_fields_without_sina_fallback(self):
        fields = [""] * 46
        values = {
            1: "贵州茅台", 3: "1204.98", 4: "1182.19", 5: "1182.20",
            9: "1204.98", 19: "1204.99", 30: "20260710161445",
            31: "22.79", 32: "1.93", 33: "1204.98", 34: "1170.28",
            36: "52213", 37: "622334", 39: "18.21", 45: "15063.23",
        }
        for index, item in values.items():
            fields[index] = item
        quotes._cache.clear()
        with patch.object(quotes, "_fetch_tencent", return_value=fields):
            result = quotes.get_quote("A股", "600519")

        self.assertEqual(result["source"], "腾讯证券单股行情")
        self.assertEqual(result["price"], 1204.98)
        self.assertEqual(result["as_of"], "2026-07-10 16:14:45")
        self.assertEqual(result["volume"], 5221300)

    def test_stock_uses_prior_unadjusted_daily_range_and_excludes_quote_day(self):
        result = evaluate_stock_level_recurrence(
            current_price=10.5,
            quote_as_of="2026-07-13 10:30:00",
            quote_source="真实实时行情",
            history_source="真实未复权日线",
            market="A股",
            symbol="600000",
            bars=[
                {"date": "2026-07-10", "low": 10.2, "high": 10.8, "close": 10.6},
                {"date": "2026-07-13", "low": 10.4, "high": 10.7, "close": 10.5},
            ],
        )

        self.assertEqual(result["metric_id"], METRIC_ID)
        self.assertEqual(result["metric_version"], METRIC_VERSION)
        self.assertEqual(result["status"], "reached")
        self.assertEqual(result["occurrence"]["date"], "2026-07-10")
        self.assertEqual(result["history"]["adjustment"], "none")

    def test_stock_no_match_reports_nearest_without_claiming_reached(self):
        result = evaluate_stock_level_recurrence(
            current_price=20.0,
            quote_as_of="2026-07-13 10:30:00",
            quote_source="真实实时行情",
            history_source="真实未复权日线",
            market="港股",
            symbol="00700",
            bars=[
                {"date": "2026-07-09", "low": 10.0, "high": 11.0, "close": 10.5},
                {"date": "2026-07-10", "low": 12.0, "high": 13.0, "close": 12.5},
            ],
        )

        self.assertEqual(result["status"], "not_found_in_coverage")
        self.assertIsNone(result["occurrence"])
        self.assertEqual(result["nearest"]["date"], "2026-07-10")
        self.assertEqual(result["nearest"]["value"], 13.0)

    def test_fund_exact_confirmed_nav_is_reported_as_date_not_intraday_time(self):
        result = evaluate_fund_level_recurrence(
            estimate_nav=1.2345,
            estimate_as_of="2026-07-13 14:30",
            estimate_source="真实基金估值",
            history_source="真实确认净值",
            code="110022",
            points=[
                {"date": "2026-07-09", "unit_nav": 1.2},
                {"date": "2026-07-10", "unit_nav": 1.2345},
            ],
        )

        self.assertEqual(result["status"], "reached_exact")
        self.assertEqual(result["occurrence"]["kind"], "exact_observation")
        self.assertEqual(result["occurrence"]["date"], "2026-07-10")

    def test_fund_crossing_preserves_date_interval(self):
        result = evaluate_fund_level_recurrence(
            estimate_nav=1.25,
            estimate_as_of="2026-07-13 14:30",
            estimate_source="真实基金估值",
            history_source="真实确认净值",
            code="110022",
            points=[
                {"date": "2026-07-08", "unit_nav": 1.3},
                {"date": "2026-07-09", "unit_nav": 1.2},
                {"date": "2026-07-10", "unit_nav": 1.28},
            ],
        )

        self.assertEqual(result["status"], "crossed_between")
        self.assertEqual(result["occurrence"]["from_date"], "2026-07-09")
        self.assertEqual(result["occurrence"]["to_date"], "2026-07-10")
        self.assertEqual(result["occurrence"]["direction"], "up")

    def test_fund_uses_latest_crossing_instead_of_much_older_exact_value(self):
        result = evaluate_fund_level_recurrence(
            estimate_nav=1.25,
            estimate_as_of="2026-07-13 14:30",
            estimate_source="真实基金估值",
            history_source="真实确认净值",
            code="110022",
            points=[
                {"date": "2025-01-02", "unit_nav": 1.25},
                {"date": "2026-07-09", "unit_nav": 1.2},
                {"date": "2026-07-10", "unit_nav": 1.3},
            ],
        )

        self.assertEqual(result["status"], "crossed_between")
        self.assertEqual(result["occurrence"]["to_date"], "2026-07-10")

    def test_quote_service_binds_live_quote_to_real_raw_history(self):
        quote = {
            "available": True,
            "market": "A股",
            "symbol": "600000",
            "source": "真实行情测试源",
            "price": 10.5,
            "as_of": "2026-07-13 10:30:00",
        }
        history = pd.DataFrame([{
            "date": pd.Timestamp("2026-07-10"),
            "open": 10.3,
            "high": 10.8,
            "low": 10.2,
            "close": 10.6,
            "volume": 1000,
        }])
        with (
            patch.object(quotes, "get_quote", return_value=quote),
            patch.object(
                quotes.data_fetch,
                "get_price_level_history_months",
                return_value=(history, "真实未复权日线测试源"),
            ),
        ):
            result = quotes.get_quote_level_history("A股", "600000", months=60)

        self.assertEqual(result["level_recurrence"]["status"], "reached")
        self.assertEqual(
            result["level_recurrence"]["history"]["source"],
            "真实未复权日线测试源",
        )


if __name__ == "__main__":
    unittest.main()
