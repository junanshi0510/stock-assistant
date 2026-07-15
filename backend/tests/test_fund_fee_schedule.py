# -*- coding: utf-8 -*-
"""Structured parsing tests for provider-disclosed fund fee schedules."""

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


HTML = """
<html><head><title>测试基金(000001)基金费率</title></head><body>
<table><tr><td>管理费率</td><td>1.20%（每年）</td><td>托管费率</td><td>0.20%（每年）</td><td>销售服务费率</td><td>---</td></tr></table>
<table><thead><tr><th>适用金额</th><th>费率</th></tr></thead><tbody><tr><td>小于50万元</td><td>1.20%</td></tr></tbody></table>
<table><thead><tr><th>适用金额</th><th>原费率|平台优惠费率</th></tr></thead><tbody>
<tr><td>小于50万元</td><td>1.50% | 0.15%</td></tr>
<tr><td>大于等于500万元</td><td>每笔1000元</td></tr>
</tbody></table>
<table><thead><tr><th>适用期限</th><th>赎回费率</th></tr></thead><tbody>
<tr><td>小于7天</td><td>1.50%</td></tr>
<tr><td>大于等于730天</td><td>0.00%</td></tr>
</tbody></table>
</body></html>
"""


class FundFeeScheduleTests(unittest.TestCase):
    def test_parses_operating_purchase_and_redemption_fees(self):
        result = funds._parse_fund_fee_schedule_html("000001", HTML)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["operating"]["management_rate_pct"], 1.2)
        self.assertEqual(result["operating"]["sales_service_rate_pct"], 0.0)
        self.assertEqual(result["operating"]["declared_annual_total_rate_pct"], 1.4)
        self.assertEqual(result["purchase"]["first_band_source_rate_pct"], 1.5)
        self.assertEqual(result["purchase"]["first_band_current_rate_pct"], 0.15)
        self.assertEqual(result["purchase"]["bands"][1]["fixed_fee_yuan"], 1000.0)
        self.assertEqual(result["redemption"]["bands"][0]["rate_pct"], 1.5)
        self.assertEqual(result["redemption"]["bands"][1]["rate_pct"], 0.0)
        self.assertTrue(result["operating"]["nav_already_net_of_operating_fees"])

    def test_zero_purchase_fee_is_preserved_as_real_zero(self):
        html = HTML.replace("1.50% | 0.15%", "0.00%")
        result = funds._parse_fund_fee_schedule_html("000001", html)

        self.assertEqual(result["purchase"]["first_band_current_rate_pct"], 0.0)

    def test_missing_redemption_table_raises_instead_of_fallback(self):
        html = HTML.replace("适用期限", "其他字段").replace("赎回费率", "其他费率")

        with self.assertRaisesRegex(RuntimeError, "缺少运作费用或赎回费率表"):
            funds._parse_fund_fee_schedule_html("000001", html)


if __name__ == "__main__":
    unittest.main()
