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

    def test_linked_etf_report_parser_requires_matching_real_target_code(self):
        html = '<a href="http://fund.eastmoney.com/513180.html">查看相关ETF></a>'
        self.assertEqual(funds._parse_linked_etf_relation_html(html), "513180")
        self.assertIsNone(funds._parse_linked_etf_relation_html("<a>普通链接</a>"))

        content = """
2.1.1 目标基金基本情况
基金主代码 513180
5.9 报告期末按公允价值占基金资产净值比例大小排序的前十名基金投资明细
1 华夏恒生科技 ETF QDII 交易型开放式 华夏基金 10,143,413,65
5.63 94.60
2 - - - - - -
5.10 投资组合报告附注
"""
        parsed = funds._parse_linked_etf_report_content(content, "513180")
        self.assertEqual(parsed["target_code"], "513180")
        self.assertEqual(parsed["target_nav_ratio"], 94.6)

        with self.assertRaisesRegex(RuntimeError, "代码不一致"):
            funds._parse_linked_etf_report_content(content, "513181")

    def test_verified_etf_feeder_is_scaled_by_reported_target_nav_ratio(self):
        relation = {
            "code": "513180",
            "name": "恒生科技ETF华夏",
            "fund_type": "指数型-海外股票",
            "source_url": "https://fund.eastmoney.com/013403.html",
        }
        report = {
            "target_nav_ratio": 94.6,
            "period": "2026-03-31",
            "title": "2026年第1季度报告",
            "published_at": "2026-04-22",
            "source_url": "https://qcloud.fund.eastmoney.com/gonggao/013403,AN1.html",
            "attachment_url": "https://pdf.example.test/report.pdf",
        }
        target = {
            "code": "513180",
            "name": "恒生科技ETF华夏",
            "stock_period": "2026年1季度股票投资明细",
            "industry_period": "2026-03-31",
            "asset_period": "2026-03-31",
            "asset_allocation": {"stock_ratio": 96.8, "bond_ratio": 0, "cash_ratio": 1.2},
            "stocks": [{"code": "00700", "name": "腾讯控股", "nav_ratio": 8.5}],
            "bonds": [],
            "industries": [{"name": "资讯科技业", "nav_ratio": 80}],
        }
        with (
            patch.object(funds, "_linked_etf_periodic_report", return_value=report),
            patch.object(funds, "get_fund_portfolio", return_value=target),
        ):
            result = funds._linked_fund_portfolio(
                "013403",
                "华夏恒生科技ETF发起式联接(QDII)C",
                relation,
                lookthrough_depth=0,
            )

        self.assertEqual(result["linked_fund"]["target_code"], "513180")
        self.assertEqual(result["linked_fund"]["target_nav_ratio"], 94.6)
        self.assertEqual(result["asset_allocation"]["stock_ratio"], 91.5728)
        self.assertEqual(result["stocks"][0]["target_nav_ratio"], 8.5)
        self.assertEqual(result["stocks"][0]["nav_ratio"], 8.041)
        self.assertEqual(result["industries"][0]["nav_ratio"], 75.68)
        self.assertIn("逐层缩放", result["method"]["note"])

    def test_fund_portfolio_uses_verified_link_before_direct_stock_provider(self):
        linked_result = {
            "code": "013403",
            "name": "联接基金",
            "stocks": [],
            "industries": [],
            "linked_fund": {"target_code": "513180"},
        }
        with (
            patch.object(funds, "_fetch_profile", return_value={"name": "华夏恒生科技ETF联接"}),
            patch.object(funds, "_fund_fact_sheet", return_value={"name": "华夏恒生科技ETF联接"}),
            patch.object(funds, "_linked_etf_relation", return_value={"code": "513180"}) as relation,
            patch.object(funds, "_linked_fund_portfolio", return_value=linked_result) as linked,
            patch.object(funds.ak, "fund_portfolio_hold_em") as direct,
        ):
            result = funds.get_fund_portfolio("013403")

        self.assertEqual(result, linked_result)
        relation.assert_called_once_with("013403")
        linked.assert_called_once()
        direct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
