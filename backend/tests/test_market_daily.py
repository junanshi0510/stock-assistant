# -*- coding: utf-8 -*-
"""Market-daily aggregation must degrade explicitly when a real source is slow."""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import market_daily  # noqa: E402


class MarketDailyTests(unittest.TestCase):
    def test_slow_source_is_reported_without_blocking_ready_real_sources(self):
        def slow_fund_categories():
            time.sleep(0.6)
            return {"items": []}

        def sector_analysis(*_args, **_kwargs):
            return {
                "industries": {"items": [{"name": "Real sector", "leaders": []}]},
                "concepts": {"items": []},
            }

        def hot_stock_bundle(*_args, **_kwargs):
            return {"rankings": {"gainers": [], "losers": []}}

        with patch.object(market_daily, "_cache_get", return_value=None), \
             patch.object(market_daily, "_cache_put"), \
             patch.object(market_daily, "_SOURCE_DEADLINE_SECONDS", 0.1), \
             patch.object(market_daily.sectors, "get_sector_analysis", side_effect=sector_analysis), \
             patch.object(market_daily.funds, "get_fund_categories", side_effect=slow_fund_categories), \
             patch.object(market_daily.funds, "get_fund_opportunities", return_value={"top_items": []}), \
             patch.object(market_daily.hot_stocks, "get_hot_stock_bundle", side_effect=hot_stock_bundle):
            started = time.monotonic()
            result = market_daily.get_market_daily()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.35)
        self.assertEqual(result["industries"][0]["name"], "Real sector")
        self.assertIn("fund_categories", {item["source"] for item in result["failed"]})
        self.assertEqual(result["fund_categories"], [])


if __name__ == "__main__":
    unittest.main()
