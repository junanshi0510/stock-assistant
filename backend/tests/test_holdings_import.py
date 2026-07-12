# -*- coding: utf-8 -*-
"""User-exported holding statements must be previewed, not silently persisted."""

import io
import sys
import unittest
from pathlib import Path

from openpyxl import Workbook


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import holdings_import  # noqa: E402


class HoldingsImportTests(unittest.TestCase):
    def test_tiantian_fund_csv_maps_holding_metrics_without_writing(self):
        csv_data = (
            "基金代码,基金名称,持有金额,持仓成本,昨日收益,持仓收益,持仓收益率,持有份额\n"
            "013403,华夏恒生科技ETF发起式联接(QDII)C,1886.16,2374.94,0.00,-488.78,-20.58%,1880.23\n"
        ).encode("utf-8-sig")

        result = holdings_import.parse_holdings_file(csv_data, filename="天天基金持仓.csv")

        self.assertEqual(result["template"]["id"], "tiantian_fund")
        self.assertEqual(result["source"], "用户上传持仓账单（仅预览，未写入持仓）")
        self.assertEqual(len(result["candidates"]), 1)
        row = result["candidates"][0]
        self.assertEqual(row["code"], "013403")
        self.assertEqual(row["source"], "tiantian_fund_export")
        self.assertEqual(row["amount"], 1886.16)
        self.assertEqual(row["cost"], 2374.94)
        self.assertEqual(row["profit"], -488.78)
        self.assertEqual(row["profit_rate"], -20.58)
        self.assertEqual(row["raw_text"], "")

    def test_excel_numeric_code_is_restored_to_six_digits_and_cost_can_be_derived(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["基金代码", "基金名称", "持有金额", "持仓成本价", "持有份额"])
        worksheet.append([13403, "华夏恒生科技ETF发起式联接(QDII)C", 1886.16, 1.2, 1560])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = holdings_import.parse_holdings_file(buffer.getvalue(), filename="fund-holdings.xlsx")

        row = result["candidates"][0]
        self.assertEqual(row["code"], "013403")
        self.assertEqual(row["cost"], 1872)
        self.assertTrue(any("前导零" in warning for warning in result["warnings"]))
        self.assertTrue(any("成本价乘以份额" in warning for warning in result["warnings"]))

    def test_duplicate_fund_rows_are_excluded_instead_of_overwriting_a_holding(self):
        csv_data = (
            "基金代码,基金名称,持有金额\n"
            "013403,基金A,100\n"
            "013403,基金A,200\n"
        ).encode("utf-8")

        result = holdings_import.parse_holdings_file(csv_data, filename="holdings.csv")

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["amount"], 100)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("重复", result["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
