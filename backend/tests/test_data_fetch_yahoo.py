# -*- coding: utf-8 -*-
"""核心行情层的 Yahoo 港/美股 OHLCV 解析与数据源策略测试。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import data_fetch  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def yahoo_payload():
    timestamps = [
        int(pd.Timestamp("2024-01-02", tz="UTC").timestamp()),
        int(pd.Timestamp("2024-01-03", tz="UTC").timestamp()),
    ]
    return {
        "chart": {
            "error": None,
            "result": [{
                "timestamp": timestamps,
                "indicators": {"quote": [{
                    "open": [10.0, 10.5],
                    "high": [11.0, 11.5],
                    "low": [9.5, 10.0],
                    "close": [10.5, 11.0],
                    "volume": [1000, 1200],
                }]},
            }],
        }
    }


class YahooHistoryTests(unittest.TestCase):
    def test_hk_symbol_mapping_and_ohlcv_parsing(self):
        with patch.object(
            data_fetch.requests, "get", return_value=FakeResponse(yahoo_payload())
        ) as request:
            frame = data_fetch._normalize(
                data_fetch._src_hk_yahoo("00700", "20240101", "20240131")
            )

        self.assertTrue(request.call_args.args[0].endswith("/0700.HK"))
        self.assertEqual(request.call_args.kwargs["timeout"], 10)
        self.assertEqual(frame.columns.tolist(), ["date", "open", "close", "high", "low", "volume"])
        self.assertEqual(frame["close"].tolist(), [10.5, 11.0])
        self.assertEqual(frame.iloc[-1]["date"], pd.Timestamp("2024-01-03"))

    def test_us_class_share_uses_yahoo_hyphen_symbol(self):
        with patch.object(
            data_fetch.requests, "get", return_value=FakeResponse(yahoo_payload())
        ) as request:
            data_fetch._src_us_yahoo("BRK.B", "20240101", "20240131")

        self.assertTrue(request.call_args.args[0].endswith("/BRK-B"))

    def test_empty_yahoo_chart_is_reported(self):
        payload = {"chart": {"error": None, "result": []}}
        with patch.object(data_fetch.requests, "get", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(RuntimeError, "Yahoo 未返回"):
                data_fetch._src_us_yahoo("AAPL", "20240101", "20240131")

    def test_historical_source_chains_do_not_fall_back_to_sina(self):
        for market, sources in data_fetch._SOURCES.items():
            names = [name for name, _source in sources]
            self.assertNotIn("新浪", names, market)
        self.assertLess(
            [name for name, _ in data_fetch._SOURCES["美股"]].index("Yahoo Finance"),
            [name for name, _ in data_fetch._SOURCES["美股"]].index("东方财富"),
        )


if __name__ == "__main__":
    unittest.main()
