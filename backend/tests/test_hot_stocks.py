# -*- coding: utf-8 -*-
"""Professional hot-stock routing, provenance, fallback and failure boundaries."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import hot_stocks  # noqa: E402


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows
        self.empty = not rows

    def to_dict(self, orient="records"):
        if orient != "records":
            raise AssertionError("tests expect record conversion")
        return list(self.rows)


def _bundle(market="港股", item=None):
    item = item or {"symbol": "00700", "name": "腾讯", "change_pct": 1.2}
    return {
        "source": "Tushare Pro",
        "provider": "tushare_pro_hk",
        "provider_tier": "professional",
        "data_freshness": "latest_completed_eod",
        "as_of": "2026-07-21",
        "scope": "全市场·最近完整交易日",
        "methodology": "test methodology",
        "rankings": {"active": [item], "gainers": [item], "losers": [item]},
        "retrieved_at": "2026-07-22T10:00:00+08:00",
        "degraded": False,
        "provider_attempts": [{"provider": "test", "status": "success"}],
    }


class HotStocksTests(unittest.TestCase):
    def setUp(self):
        hot_stocks._cache.clear()
        hot_stocks._name_cache.clear()
        hot_stocks._provider_runtime.clear()

    def test_unconfigured_professional_source_uses_explicit_degraded_fallback(self):
        item = {
            "symbol": "600000", "name": "样本", "price": 10.0,
            "change_pct": 2.5, "volume": 1000.0, "secid": "1.600000",
        }
        with patch.object(hot_stocks.config, "TUSHARE_TOKEN", ""), \
             patch.object(hot_stocks.config, "HOT_STOCK_PUBLIC_FALLBACK_ENABLED", True), \
             patch.object(hot_stocks, "_clist", return_value=[item]) as provider:
            result = hot_stocks.get_hot_stocks("A股", "1d", "gainers", 10)

        provider.assert_called_once_with("A股", "f3", 1, 10)
        self.assertEqual(result["source"], "东方财富")
        self.assertEqual(result["provider_tier"], "public_fallback")
        self.assertTrue(result["degraded"])
        self.assertIn("TUSHARE_TOKEN", result["warning"])
        self.assertEqual(
            [attempt["status"] for attempt in result["provider_attempts"]],
            ["not_configured", "success"],
        )

    def test_tushare_bundle_sorts_all_three_lists_from_one_daily_snapshot(self):
        class Pro:
            def __init__(self):
                self.daily_calls = 0

            def trade_cal(self, **_kwargs):
                return FakeFrame([{"cal_date": "20260721"}])

            def daily(self, **_kwargs):
                self.daily_calls += 1
                return FakeFrame([
                    {"ts_code": "600519.SH", "close": 1500, "pct_chg": 2.1, "vol": 10, "amount": 100},
                    {"ts_code": "000858.SZ", "close": 120, "pct_chg": -3.2, "vol": 20, "amount": 300},
                ])

            def stock_basic(self, **_kwargs):
                return FakeFrame([
                    {"ts_code": "600519.SH", "symbol": "600519", "name": "贵州茅台"},
                    {"ts_code": "000858.SZ", "symbol": "000858", "name": "五粮液"},
                ])

        pro = Pro()
        with patch.object(hot_stocks.config, "TUSHARE_TOKEN", "configured-token"), \
             patch.object(hot_stocks, "_tushare_client", return_value=pro):
            result = hot_stocks.get_hot_stock_bundle(
                "A股", ["gainers", "losers", "active"], 10
            )

        self.assertEqual(pro.daily_calls, 1)
        self.assertEqual(result["provider_tier"], "professional")
        self.assertFalse(result["degraded"])
        self.assertEqual(result["as_of"], "2026-07-21")
        self.assertEqual(result["rankings"]["gainers"][0]["symbol"], "600519")
        self.assertEqual(result["rankings"]["losers"][0]["symbol"], "000858")
        self.assertEqual(result["rankings"]["active"][0]["symbol"], "000858")

    def test_alpha_vantage_serves_three_us_lists_with_one_api_call(self):
        response = MagicMock()
        response.json.return_value = {
            "last_updated": "2026-07-21 16:15:59 US/Eastern",
            "top_gainers": [{"ticker": "AAA", "price": "12.5", "change_percentage": "8.1%", "volume": "100"}],
            "top_losers": [{"ticker": "BBB", "price": "7.5", "change_percentage": "-6.2%", "volume": "200"}],
            "most_actively_traded": [{"ticker": "CCC", "price": "20", "change_percentage": "1.0%", "volume": "900"}],
        }
        with patch.object(hot_stocks.config, "ALPHAVANTAGE_API_KEY", "alpha-secret"), \
             patch.object(hot_stocks.config, "ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT", ""), \
             patch.object(hot_stocks.requests, "get", return_value=response) as request:
            result = hot_stocks.get_hot_stock_bundle(
                "美股", ["gainers", "losers", "active"], 20
            )

        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["source"], "Alpha Vantage")
        self.assertEqual(result["data_freshness"], "end_of_day")
        self.assertEqual(result["rankings"]["gainers"][0]["change_pct"], 8.1)
        self.assertEqual(result["rankings"]["active"][0]["symbol"], "CCC")

    def test_provider_failure_redacts_secrets_and_returns_actionable_error(self):
        with patch.object(hot_stocks.config, "TUSHARE_TOKEN", "very-secret-token"), \
             patch.object(hot_stocks.config, "HOT_STOCK_PUBLIC_FALLBACK_ENABLED", True), \
             patch.object(hot_stocks, "_tushare_bundle", side_effect=RuntimeError("token=very-secret-token rejected")), \
             patch.object(hot_stocks, "_public_bundle", side_effect=requests.ConnectionError("connection aborted")):
            with self.assertRaises(hot_stocks.HotStockProviderUnavailable) as caught:
                hot_stocks.get_hot_stock_bundle("A股", ["gainers", "losers"], 10)

        serialized = json.dumps(caught.exception.attempts, ensure_ascii=False)
        self.assertNotIn("very-secret-token", str(caught.exception))
        self.assertNotIn("very-secret-token", serialized)
        self.assertIn("TUSHARE_TOKEN", str(caught.exception))
        self.assertIn("不会回退新浪", str(caught.exception))
        self.assertEqual(len(caught.exception.attempts), 2)

    def test_circuit_breaker_stops_repeated_calls_after_threshold(self):
        with patch.object(hot_stocks.config, "TUSHARE_TOKEN", "configured"), \
             patch.object(hot_stocks.config, "HOT_STOCK_PUBLIC_FALLBACK_ENABLED", False), \
             patch.object(hot_stocks.config, "HOT_STOCK_PROVIDER_FAILURE_THRESHOLD", 2), \
             patch.object(hot_stocks.config, "HOT_STOCK_PROVIDER_CIRCUIT_SECONDS", 60), \
             patch.object(hot_stocks, "_tushare_bundle", side_effect=RuntimeError("provider down")) as provider:
            for _ in range(2):
                with self.assertRaises(hot_stocks.HotStockProviderUnavailable):
                    hot_stocks.get_hot_stock_bundle("A股", ["gainers"], 10)
            with self.assertRaises(hot_stocks.HotStockProviderUnavailable) as caught:
                hot_stocks.get_hot_stock_bundle("A股", ["gainers"], 10)

        self.assertEqual(provider.call_count, 2)
        self.assertEqual(caught.exception.attempts[0]["status"], "circuit_open")

    def test_provider_status_never_exposes_keys(self):
        with patch.object(hot_stocks.config, "TUSHARE_TOKEN", "tushare-secret"), \
             patch.object(hot_stocks.config, "ALPHAVANTAGE_API_KEY", "alpha-secret"), \
             patch.object(hot_stocks.config, "ALPHAVANTAGE_MARKET_DATA_ENTITLEMENT", "delayed"):
            result = hot_stocks.get_provider_status()

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("tushare-secret", serialized)
        self.assertNotIn("alpha-secret", serialized)
        self.assertFalse(result["secrets_exposed"])
        self.assertFalse(result["active_probe"])
        self.assertEqual(len(result["markets"]), 3)
        self.assertTrue(all(item["configured"] for item in result["markets"]))

    def test_multiday_active_keeps_activity_order_but_uses_period_return(self):
        candidates = [
            {"symbol": "AAA", "change_pct": 1.0},
            {"symbol": "BBB", "change_pct": -1.0},
        ]
        returns = {"AAA": 12.5, "BBB": -8.0}
        with patch.object(hot_stocks, "_hot_1d", return_value=candidates), \
             patch.object(hot_stocks, "_n_day_return", side_effect=lambda _m, symbol, _d: returns[symbol]):
            result = hot_stocks._hot_multiday("港股", "active", 7, 2)

        self.assertEqual([row["symbol"] for row in result], ["AAA", "BBB"])
        self.assertEqual([row["change_pct"] for row in result], [12.5, -8.0])

    def test_expired_cache_is_returned_and_marked_stale_on_provider_failure(self):
        with patch.object(hot_stocks, "get_hot_stock_bundle", return_value=_bundle()):
            hot_stocks.get_hot_stocks("港股", "1d", "active", 10)
        key = ("港股", "1d", "active", 10)
        _, cached_result = hot_stocks._cache[key]
        hot_stocks._cache[key] = (0, cached_result)

        failure = hot_stocks.HotStockProviderUnavailable(
            "all providers failed", [{"provider": "test", "status": "failed"}]
        )
        with patch.object(hot_stocks, "get_hot_stock_bundle", side_effect=failure):
            result = hot_stocks.get_hot_stocks("港股", "1d", "active", 10)

        self.assertTrue(result["stale"])
        self.assertTrue(result["degraded"])
        self.assertIn("缓存", result["warning"])
        self.assertEqual(result["provider_attempts"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
