# -*- coding: utf-8 -*-
"""Look-through exposure must only aggregate disclosed fund portfolio data."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


def portfolio(code, name, stocks, industries):
    return {
        "code": code,
        "name": name,
        "stock_period": "2026年1季度",
        "industry_period": "2026-03-31",
        "stocks": stocks,
        "industries": industries,
    }


class FundExposureTests(unittest.TestCase):
    def setUp(self):
        funds._cache.clear()

    def test_disclosed_stocks_and_industries_are_weighted_by_confirmed_amount(self):
        holdings = [
            {"asset_type": "fund", "code": "000001", "name": "基金A", "amount": 6000},
            {"asset_type": "fund", "code": "000002", "name": "基金B", "amount": 4000},
        ]
        disclosures = {
            "000001": portfolio("000001", "基金A", [
                {"code": "600000", "name": "股票甲", "nav_ratio": 10},
                {"code": "600001", "name": "股票乙", "nav_ratio": 5},
            ], [{"name": "信息技术", "nav_ratio": 20}]),
            "000002": portfolio("000002", "基金B", [
                {"code": "600000", "name": "股票甲", "nav_ratio": 8},
                {"code": "600002", "name": "股票丙", "nav_ratio": 12},
            ], [{"name": "信息技术", "nav_ratio": 30}]),
        }
        with patch.object(funds, "get_fund_portfolio", side_effect=lambda code: disclosures[code]):
            result = funds.aggregate_fund_exposure(holdings, max_funds=6)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["summary"]["fund_amount_coverage"], 100)
        self.assertEqual(result["summary"]["stock_disclosed_portfolio_ratio"], 17)
        self.assertEqual(result["stocks"][0]["code"], "600000")
        self.assertEqual(result["stocks"][0]["portfolio_ratio"], 9.2)
        self.assertEqual(result["industries"][0]["name"], "信息技术")
        self.assertEqual(result["industries"][0]["portfolio_ratio"], 24)
        self.assertIn("不推断未披露仓位", result["policy"])

    def test_provider_failure_marks_exposure_partial_without_substituting_data(self):
        holdings = [
            {"asset_type": "fund", "code": "000011", "name": "基金A", "amount": 6000},
            {"asset_type": "fund", "code": "000012", "name": "基金B", "amount": 4000},
        ]
        disclosure = portfolio("000011", "基金A", [
            {"code": "600010", "name": "股票甲", "nav_ratio": 10},
        ], [{"name": "信息技术", "nav_ratio": 20}])

        def load(code):
            if code == "000012":
                raise RuntimeError("provider timeout")
            return disclosure

        with patch.object(funds, "get_fund_portfolio", side_effect=load):
            result = funds.aggregate_fund_exposure(holdings, max_funds=6)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["summary"]["loaded_fund_amount"], 6000)
        self.assertEqual(result["summary"]["fund_amount_coverage"], 60)
        self.assertEqual(result["summary"]["failed_count"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertTrue(result["reasons"])


if __name__ == "__main__":
    unittest.main()
