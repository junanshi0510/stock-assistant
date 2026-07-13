# -*- coding: utf-8 -*-

import sys
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import fund_intelligence  # noqa: E402


class FundIntelligenceTests(unittest.TestCase):
    def test_eastmoney_news_uses_bounded_real_jsonp_request(self):
        payload = {
            "result": {
                "cmsArticleWebOld": [{
                    "code": "202607130001",
                    "title": "<em>腾讯控股</em>发布业务进展",
                    "content": "来自公告与机构采访的真实摘要",
                    "date": "2026-07-13 09:30:00",
                    "mediaName": "证券时报网",
                }]
            }
        }
        response = Mock()
        response.text = f"stockAssistantNews({json.dumps(payload, ensure_ascii=False)});"
        response.raise_for_status.return_value = None

        with patch.object(fund_intelligence.requests, "get", return_value=response) as request:
            rows = fund_intelligence._eastmoney_news("00700", "腾讯控股", 2)

        self.assertEqual(rows[0]["publisher"], "证券时报网")
        self.assertEqual(rows[0]["title"], "腾讯控股发布业务进展")
        self.assertTrue(rows[0]["url"].startswith("https://finance.eastmoney.com/a/"))
        self.assertEqual(request.call_args.kwargs["timeout"], 8)

    def test_real_provider_rows_are_weighted_and_news_is_marked_untrusted(self):
        profile = {
            "fund": {"name": "港股测试基金"},
            "market": {
                "primary": "hong_kong",
                "label": "中国香港",
                "cross_border": True,
                "currency_risk": True,
            },
        }
        portfolio = {
            "source": "真实定期报告测试源",
            "source_url": "https://example.test/disclosure",
            "name": "港股测试基金",
            "stock_period": "2026-03-31",
            "bond_period": "",
            "industry_period": "2026-03-31",
            "asset_period": "2026-03-31",
            "asset_allocation": {"stock_ratio": 90},
            "summary": {"top10_stock_ratio": 70},
            "industries": [{"name": "信息技术", "nav_ratio": 40}],
            "stocks": [
                {"code": "00700", "name": "腾讯控股", "nav_ratio": 10},
                {"code": "09988", "name": "阿里巴巴-W", "nav_ratio": 5},
            ],
        }

        def quote(_market, symbol):
            return {
                "source": "腾讯证券单股行情",
                "price": 100,
                "change_pct": 2 if symbol == "00700" else -1,
                "amount": 1000,
                "pe": 20,
                "market_cap": 100000,
                "as_of": "2026-07-13 10:00:00",
                "delay_note": "",
            }

        def news(_market, symbol, name, _limit):
            return [{
                "symbol": symbol,
                "holding_name": name,
                "title": f"{name}真实新闻",
                "summary": "来自真实发布机构的摘要",
                "published_at": "2026-07-13 09:00:00",
                "publisher": "测试发布机构",
                "url": "https://example.test/news",
                "provider": "真实新闻测试源",
                "provider_sentiment": None,
                "untrusted_external_content": True,
            }]

        with patch.object(fund_intelligence.funds, "get_fund_market_profile", return_value=profile), \
             patch.object(fund_intelligence.funds, "get_fund_portfolio", return_value=portfolio), \
             patch.object(fund_intelligence.quotes, "get_quote", side_effect=quote), \
             patch.object(fund_intelligence, "_news_for_holding", side_effect=news):
            result = fund_intelligence.get_fund_intelligence(
                "999998", holding_limit=2, news_per_holding=1
            )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["holding_pulse"]["quoted_count"], 2)
        self.assertEqual(result["holding_pulse"]["weighted_change_pct"], 1.0)
        self.assertEqual(result["holding_pulse"]["advancing_weight_pct"], 66.67)
        self.assertEqual(result["news"]["count"], 2)
        self.assertTrue(result["news"]["untrusted_external_content"])
        self.assertTrue(all(item["untrusted_external_content"] for item in result["news"]["items"]))
        self.assertEqual(result["failed"], [])

    def test_missing_portfolio_does_not_invent_holdings(self):
        with patch.object(
            fund_intelligence.funds,
            "get_fund_market_profile",
            return_value={"market": {"primary": "mainland"}},
        ), patch.object(
            fund_intelligence.funds,
            "get_fund_portfolio",
            side_effect=RuntimeError("真实披露不可用"),
        ):
            result = fund_intelligence.get_fund_intelligence("999997")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["news"]["items"], [])
        self.assertIn("不猜测持仓", result["policy"])


if __name__ == "__main__":
    unittest.main()
