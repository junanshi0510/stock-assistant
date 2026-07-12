# -*- coding: utf-8 -*-
"""CSV preview must be transparent and exclude ambiguous rows before confirmation."""

import sys
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import transaction_import  # noqa: E402


class TransactionImportTests(unittest.TestCase):
    def test_chinese_statement_columns_preview_without_writing(self):
        payload = (
            "成交日期,证券代码,证券名称,买卖标志,成交数量,成交金额,手续费,市场\n"
            "2025/01/02,013403,华夏恒生科技ETF联接C,申购,1000,1000,1,基金\n"
            "2025/02/02,013403,华夏恒生科技ETF联接C,赎回,200,220,1,基金\n"
        ).encode("utf-8")

        result = transaction_import.parse_transaction_csv(payload, filename="fund-trades.csv")

        self.assertEqual(result["encoding"], "utf-8-sig")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["trade_type"], "buy")
        self.assertEqual(result["candidates"][0]["unit_price"], 1)
        self.assertEqual(result["candidates"][1]["trade_type"], "sell")
        self.assertEqual(result["candidates"][1]["unit_price"], 1.1)
        self.assertIn("原始 CSV 不会保存", result["privacy"])

    def test_unknown_trade_type_is_excluded_instead_of_guessed(self):
        payload = (
            "日期,代码,方向,份额,单价\n"
            "2025-01-02,000001,转换,100,1\n"
            "2025-01-03,000001,买入,100,1\n"
        ).encode("utf-8")

        result = transaction_import.parse_transaction_csv(payload, filename="trades.csv")

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["csv_row"], 3)
        self.assertEqual(result["errors"][0]["row"], 2)
        self.assertIn("无法识别", result["errors"][0]["message"])

    def test_tiantian_confirmation_xlsx_keeps_only_confirmed_cashflow_rows(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([
            "确认日期", "基金代码", "基金名称", "业务名称", "确认份额",
            "确认净值", "确认金额", "手续费", "确认状态",
        ])
        sheet.append([
            datetime(2025, 1, 2), 13403, "华夏恒生科技ETF联接C", "定投申购",
            1000, 1.2, 1200, 1.2, "确认成功",
        ])
        sheet.append([
            datetime(2025, 2, 2), 13403, "华夏恒生科技ETF联接C", "赎回",
            200, 1.25, 250, 0.3, "已确认",
        ])
        sheet.append([
            datetime(2025, 3, 2), 13403, "华夏恒生科技ETF联接C", "基金转换入",
            100, 1.3, 130, 0, "确认成功",
        ])
        sheet.append([
            datetime(2025, 4, 2), 13403, "华夏恒生科技ETF联接C", "申购",
            100, 1.4, 140, 0, "交易失败",
        ])
        payload = BytesIO()
        workbook.save(payload)
        workbook.close()

        result = transaction_import.parse_transaction_file(
            payload.getvalue(),
            filename="天天基金-交易流水.xlsx",
        )

        self.assertEqual(result["template"]["id"], "tiantian_fund_transaction")
        self.assertEqual(result["format"], "Excel")
        self.assertEqual(result["encoding"], "xlsx")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["code"], "013403")
        self.assertEqual(result["candidates"][0]["trade_type"], "buy")
        self.assertEqual(result["candidates"][1]["trade_type"], "sell")
        self.assertEqual(result["candidates"][0]["source"], "tiantian_fund_transaction_export")
        self.assertEqual({row["row"] for row in result["errors"]}, {4, 5})
        self.assertTrue(any("现金流含义不完整" in row["message"] for row in result["errors"]))
        self.assertTrue(any("不是已确认成功" in row["message"] for row in result["errors"]))


if __name__ == "__main__":
    unittest.main()
