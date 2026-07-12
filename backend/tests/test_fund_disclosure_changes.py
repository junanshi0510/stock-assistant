# -*- coding: utf-8 -*-
"""Regression coverage for comparisons of real fund disclosure shapes."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


def _portfolio(year, stock_period, stocks, industries, top10):
    return {
        "source": "天天基金投资组合 / 东方财富基金档案",
        "code": "110022",
        "name": "示例基金",
        "year": str(year),
        "stock_period": stock_period,
        "bond_period": "",
        "industry_period": stock_period,
        "stocks": stocks,
        "industries": industries,
        "summary": {"top10_stock_ratio": top10},
    }


class FundDisclosureChangesTests(unittest.TestCase):
    def setUp(self):
        funds._cache.clear()

    def tearDown(self):
        funds._cache.clear()

    def test_compares_two_distinct_disclosures_without_calling_list_changes_trades(self):
        latest = _portfolio(
            2026,
            "2026年1季度",
            [
                {"code": "000001", "name": "平安银行", "nav_ratio": 10.0},
                {"code": "000002", "name": "万科A", "nav_ratio": 8.0},
                {"code": "000004", "name": "国华网安", "nav_ratio": 5.0},
            ],
            [
                {"name": "信息技术", "nav_ratio": 35.0},
                {"name": "医药生物", "nav_ratio": 10.0},
            ],
            55.0,
        )
        previous = _portfolio(
            2025,
            "2025年4季度",
            [
                {"code": "000001", "name": "平安银行", "nav_ratio": 5.0},
                {"code": "000002", "name": "万科A", "nav_ratio": 10.0},
                {"code": "000003", "name": "PT金田A", "nav_ratio": 7.0},
                {"code": "000005", "name": "世纪星源", "nav_ratio": 0.0},
            ],
            [
                {"name": "医药生物", "nav_ratio": 30.0},
                {"name": "金融", "nav_ratio": 10.0},
            ],
            45.0,
        )

        with patch.object(funds, "get_fund_portfolio", side_effect=[latest, previous]):
            result = funds.get_fund_disclosure_changes("110022")

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["latest"]["stock_period"], "2026年1季度")
        self.assertEqual(result["previous"]["stock_period"], "2025年4季度")
        self.assertEqual(result["added_stocks"][0]["code"], "000004")
        self.assertEqual(result["removed_stocks"][0]["code"], "000003")
        self.assertNotIn("000005", [row["code"] for row in result["removed_stocks"]])
        self.assertEqual(result["stock_changes"][0]["code"], "000001")
        self.assertEqual(result["stock_changes"][0]["delta"], 5.0)
        self.assertEqual(result["summary"]["top10_stock_ratio_change"], 10.0)
        self.assertTrue(result["summary"]["industry_focus_changed"])
        self.assertIn("不等于已经清仓", result["policy"])

    def test_returns_unavailable_when_two_requests_point_to_same_actual_period(self):
        latest = _portfolio(
            2026,
            "2025年4季度",
            [{"code": "000001", "name": "平安银行", "nav_ratio": 10.0}],
            [{"name": "金融", "nav_ratio": 30.0}],
            10.0,
        )
        previous = _portfolio(
            2025,
            "2025年4季度",
            [{"code": "000001", "name": "平安银行", "nav_ratio": 10.0}],
            [{"name": "金融", "nav_ratio": 30.0}],
            10.0,
        )

        with patch.object(funds, "get_fund_portfolio", side_effect=[latest, previous]):
            result = funds.get_fund_disclosure_changes("110022")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["stock_changes"], [])
        self.assertIn("同一报告期", result["reasons"][0])


if __name__ == "__main__":
    unittest.main()
