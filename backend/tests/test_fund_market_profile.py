# -*- coding: utf-8 -*-
"""Cross-market fund classification must be explicit and auditable."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from strategies.fund_market_profile import (  # noqa: E402
    STRATEGY_ID,
    STRATEGY_VERSION,
    build_fund_market_profile,
)
import funds  # noqa: E402


class FundMarketProfileTests(unittest.TestCase):
    def test_hang_seng_qdii_is_hong_kong_cross_border(self):
        result = build_fund_market_profile(
            code="013403",
            name="华夏恒生科技ETF发起式联接(QDII)C",
            fund_type="QDII-指数",
            benchmark_names=["恒生科技指数"],
        )

        self.assertEqual(result["strategy_id"], STRATEGY_ID)
        self.assertEqual(result["strategy_version"], STRATEGY_VERSION)
        self.assertEqual(result["resolution_status"], "identified")
        self.assertEqual(result["market"]["primary"], "hong_kong")
        self.assertEqual(result["market"]["required_permissions"], ["hong_kong"])
        self.assertTrue(result["fund"]["is_qdii"])
        self.assertTrue(result["market"]["currency_risk"])

    def test_nasdaq_qdii_is_united_states(self):
        result = build_fund_market_profile(
            code="000834",
            name="大成纳斯达克100ETF联接(QDII)A",
            fund_type="QDII-指数",
            benchmark_names=["纳斯达克100指数"],
        )

        self.assertEqual(result["market"]["primary"], "united_states")
        self.assertEqual(result["market"]["required_permissions"], ["united_states"])

    def test_global_qdii_requires_global_permission(self):
        result = build_fund_market_profile(
            code="000001",
            name="测试全球精选(QDII)",
            fund_type="QDII-普通股票",
            benchmark_names=["MSCI WORLD"],
        )

        self.assertEqual(result["market"]["primary"], "global")
        self.assertEqual(result["market"]["required_permissions"], ["global"])

    def test_qdii_without_region_evidence_is_not_guessed(self):
        result = build_fund_market_profile(
            code="000002",
            name="测试海外机会(QDII)",
            fund_type="QDII-混合",
            benchmark_names=[],
        )

        self.assertEqual(result["resolution_status"], "insufficient")
        self.assertEqual(result["market"]["primary"], "unknown_cross_border")
        self.assertEqual(result["market"]["required_permissions"], [])

    def test_domestic_fund_defaults_only_when_no_cross_border_signal_exists(self):
        result = build_fund_market_profile(
            code="110022",
            name="易方达消费行业股票",
            fund_type="股票型",
            benchmark_names=["沪深300"],
        )

        self.assertEqual(result["market"]["primary"], "mainland")
        self.assertFalse(result["market"]["cross_border"])

    def test_service_uses_provider_type_and_detail_benchmarks(self):
        with (
            patch.object(funds, "_fund_search_one", return_value={
                "code": "013403",
                "name": "华夏恒生科技ETF发起式联接(QDII)C",
                "type": "指数型-海外股票",
            }),
            patch.object(funds, "_fund_fact_sheet", return_value={
                "benchmark_comparison": {"series": [{"name": "恒生科技指数"}]},
            }),
        ):
            result = funds.get_fund_market_profile("013403")

        self.assertEqual(result["market"]["primary"], "hong_kong")
        self.assertEqual(result["benchmark_names"], ["恒生科技指数"])
        self.assertEqual(len(result["source_refs"]), 2)

    def test_service_refuses_to_guess_when_provider_metadata_is_missing(self):
        with (
            patch.object(funds, "_fund_search_one", return_value=None),
            patch.object(funds, "_fund_fact_sheet", return_value={}),
        ):
            with self.assertRaises(RuntimeError):
                funds.get_fund_market_profile("013403")


if __name__ == "__main__":
    unittest.main()
