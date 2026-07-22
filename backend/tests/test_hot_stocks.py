# -*- coding: utf-8 -*-
"""Professional hot-stock routing, provenance, fallback and failure boundaries."""

import json
import sys
import types
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
        hot_stocks._provider_bundle_cache.clear()
        hot_stocks._massive_day_cache.clear()
        hot_stocks._probe_cache.clear()

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
            ["not_configured", "not_configured", "success"],
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
        self.assertEqual(len(caught.exception.attempts), 3)

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
        tushare_attempt = next(
            item for item in caught.exception.attempts if item["provider"] == "tushare_pro_a"
        )
        self.assertEqual(tushare_attempt["status"], "circuit_open")

    def test_massive_builds_full_market_us_rankings_from_two_grouped_days(self):
        current = [
            {"T": "AAA", "c": 12, "v": 20000, "vw": 11.5, "n": 100},
            {"T": "BBB", "c": 8, "v": 90000, "vw": 8.2, "n": 300},
            {"T": "PENNY", "c": 0.5, "v": 500000, "vw": 0.5, "n": 400},
        ]
        previous = [
            {"T": "AAA", "c": 10, "v": 10000},
            {"T": "BBB", "c": 10, "v": 10000},
            {"T": "PENNY", "c": 0.4, "v": 10000},
        ]
        with patch.object(hot_stocks.config, "FUTU_OPEND_HOST", ""), \
             patch.object(hot_stocks.config, "MASSIVE_API_KEY", "massive-key"), \
             patch.object(hot_stocks.config, "POLYGON_API_KEY", ""), \
             patch.object(hot_stocks.config, "HOT_STOCK_US_MIN_PRICE", 1.0), \
             patch.object(hot_stocks.config, "HOT_STOCK_US_MIN_VOLUME", 10000), \
             patch.object(hot_stocks, "_massive_candidate_dates", return_value=["2026-07-21", "2026-07-20"]), \
             patch.object(hot_stocks, "_massive_grouped_day", side_effect=[current, previous]) as grouped:
            result = hot_stocks.get_hot_stock_bundle(
                "美股", ["gainers", "losers", "active"], 10
            )

        self.assertEqual(grouped.call_count, 2)
        self.assertEqual(result["provider"], "massive_eod_us")
        self.assertEqual(result["as_of"], "2026-07-21")
        self.assertEqual(result["rankings"]["gainers"][0]["symbol"], "AAA")
        self.assertEqual(result["rankings"]["losers"][0]["symbol"], "BBB")
        self.assertEqual(result["rankings"]["active"][0]["symbol"], "BBB")
        self.assertEqual(result["data_quality"]["eligible_rows"], 2)
        self.assertEqual(result["data_quality"]["excluded_rows"], 1)

    def test_massive_candidate_date_uses_current_session_only_after_eod_cutoff(self):
        eastern = hot_stocks.ZoneInfo("America/New_York")
        before_cutoff = hot_stocks.dt.datetime(2026, 7, 21, 17, 59, tzinfo=eastern)
        after_cutoff = hot_stocks.dt.datetime(2026, 7, 21, 18, 0, tzinfo=eastern)

        self.assertEqual(hot_stocks._massive_candidate_dates(before_cutoff)[0], "2026-07-20")
        self.assertEqual(hot_stocks._massive_candidate_dates(after_cutoff)[0], "2026-07-21")

    def test_unconfigured_massive_multiday_makes_no_network_request(self):
        with patch.object(hot_stocks.config, "MASSIVE_API_KEY", ""), \
             patch.object(hot_stocks.config, "POLYGON_API_KEY", ""), \
             patch.object(hot_stocks, "_massive_grouped_day") as grouped:
            with self.assertRaises(hot_stocks.ProviderNotConfigured):
                hot_stocks._massive_multiday_bundle(7, "gainers", 10)

        grouped.assert_not_called()

    def test_futu_snapshot_is_normalized_and_ranked_without_leaking_opend_details(self):
        class QuoteContext:
            def __init__(self, **_kwargs):
                self.closed = False

            def get_stock_basicinfo(self, _market, _security_type):
                return 0, FakeFrame([
                    {"code": "SH.600519", "name": "贵州茅台"},
                    {"code": "SH.600000", "name": "浦发银行"},
                ])

            def get_market_snapshot(self, _codes):
                return 0, FakeFrame([
                    {"code": "SH.600519", "name": "贵州茅台", "last_price": 1500,
                     "prev_close_price": 1450, "volume": 20, "turnover": 30000,
                     "update_time": "2026-07-22 10:30:00", "suspension": False},
                    {"code": "SH.600000", "name": "浦发银行", "last_price": 10,
                     "prev_close_price": 11, "volume": 1000, "turnover": 10000,
                     "update_time": "2026-07-22 10:30:01", "suspension": False},
                ])

            def close(self):
                self.closed = True

        fake_futu = types.SimpleNamespace(
            OpenQuoteContext=QuoteContext,
            Market=types.SimpleNamespace(SH="SH", SZ="SZ", HK="HK", US="US"),
            SecurityType=types.SimpleNamespace(STOCK="STOCK"),
            RET_OK=0,
        )
        with patch.dict(sys.modules, {"futu": fake_futu}), \
             patch.object(hot_stocks.importlib.util, "find_spec", return_value=object()), \
             patch.object(hot_stocks.config, "FUTU_OPEND_HOST", "127.0.0.1"), \
             patch.object(hot_stocks.config, "FUTU_OPEND_MARKETS", "A"), \
             patch.object(hot_stocks.config, "FUTU_OPEND_PORT", 11111), \
             patch.object(hot_stocks.config, "FUTU_SNAPSHOT_BATCH_SIZE", 400):
            result = hot_stocks.get_hot_stock_bundle("A股", ["gainers", "losers"], 10)

        self.assertEqual(result["provider"], "futu_opend_a")
        self.assertEqual(result["rankings"]["gainers"][0]["symbol"], "600519")
        self.assertEqual(result["rankings"]["losers"][0]["symbol"], "600000")
        self.assertEqual(result["data_freshness"], "realtime")

    def test_massive_multiday_path_is_full_market_and_never_calls_yahoo(self):
        bundle = {
            "source": "Massive（原 Polygon.io）",
            "provider": "massive_eod_us",
            "provider_tier": "professional",
            "data_freshness": "latest_completed_eod",
            "as_of": "2026-07-21",
            "scope": "全美股·7 个真实交易日·流动性过滤",
            "methodology": "真实交易日全市场计算",
            "data_quality": {"status": "pass", "eligible_rows": 5000},
            "rankings": {"gainers": [{"symbol": "AAA", "change_pct": 20.0}]},
            "retrieved_at": "2026-07-22T10:00:00+08:00",
        }
        with patch.object(hot_stocks.config, "MASSIVE_API_KEY", "massive-key"), \
             patch.object(hot_stocks.config, "POLYGON_API_KEY", ""), \
             patch.object(hot_stocks, "_massive_multiday_bundle", return_value=bundle) as massive, \
             patch.object(hot_stocks, "_yahoo_us_period_returns") as yahoo:
            result = hot_stocks.get_hot_stocks("美股", "7d", "gainers", 10)

        massive.assert_called_once_with(7, "gainers", 10)
        yahoo.assert_not_called()
        self.assertTrue(result["full_market_multiday"])
        self.assertEqual(result["scope"], bundle["scope"])
        self.assertEqual(result["items"][0]["symbol"], "AAA")

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
