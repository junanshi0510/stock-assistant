# -*- coding: utf-8 -*-
"""热门榜必须保持专业源口径，并正确展示多日涨跌。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import hot_stocks  # noqa: E402


class HotStocksTests(unittest.TestCase):
    def setUp(self):
        hot_stocks._cache.clear()

    def test_daily_ranking_reports_eastmoney_source(self):
        item = {
            "symbol": "600000", "name": "样本", "price": 10.0,
            "change_pct": 2.5, "volume": 1000.0, "secid": "1.600000",
        }
        with patch.object(hot_stocks, "_clist", return_value=[item]) as provider:
            result = hot_stocks.get_hot_stocks("A股", "1d", "gainers", 10)

        provider.assert_called_once_with("A股", "f3", 1, 10)
        self.assertEqual(result["source"], "东方财富")
        self.assertFalse(result["stale"])
        self.assertEqual(result["items"], [item])

    def test_us_daily_ranking_discloses_yahoo_methodology(self):
        item = {"symbol": "AAPL", "change_pct": 2.0}
        with patch.object(hot_stocks, "_yahoo_us_1d", return_value=[item]):
            result = hot_stocks.get_hot_stocks("美股", "1d", "gainers", 10)

        self.assertEqual(result["source"], "Yahoo Finance")
        self.assertIn("Yahoo Finance", result["methodology"])
        self.assertIn("retrieved_at", result)

    def test_multiday_active_keeps_activity_order_but_uses_period_return(self):
        candidates = [
            {"symbol": "AAA", "change_pct": 1.0},
            {"symbol": "BBB", "change_pct": -1.0},
        ]
        returns = {"AAA": 12.5, "BBB": -8.0}
        with patch.object(hot_stocks, "_hot_1d", return_value=candidates), \
             patch.object(hot_stocks, "_n_day_return", side_effect=lambda _m, s, _d: returns[s]):
            result = hot_stocks._hot_multiday("港股", "active", 7, 2)

        self.assertEqual([row["symbol"] for row in result], ["AAA", "BBB"])
        self.assertEqual([row["change_pct"] for row in result], [12.5, -8.0])

    def test_expired_cache_is_returned_and_marked_stale_on_provider_failure(self):
        item = {"symbol": "00700", "change_pct": 1.2}
        with patch.object(hot_stocks, "_hot_1d", return_value=[item]):
            hot_stocks.get_hot_stocks("港股", "1d", "active", 10)
        key = ("港股", "1d", "active", 10)
        _, cached_result = hot_stocks._cache[key]
        hot_stocks._cache[key] = (0, cached_result)

        with patch.object(hot_stocks, "_hot_1d", side_effect=RuntimeError("provider down")):
            result = hot_stocks.get_hot_stocks("港股", "1d", "active", 10)

        self.assertTrue(result["stale"])
        self.assertIn("缓存", result["warning"])


if __name__ == "__main__":
    unittest.main()
