# -*- coding: utf-8 -*-
"""Route-level fund regressions that do not require live market providers."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import funds as fund_router  # noqa: E402


class FundRouterTests(unittest.TestCase):
    def test_hot_fund_parameters_are_forwarded(self):
        expected = {"items": [{"code": "110022"}]}
        with patch.object(fund_router.funds_mod, "get_hot_funds", return_value=expected) as service:
            actual = fund_router.get_hot_funds("index", 12, "6m", True)

        self.assertEqual(actual, expected)
        service.assert_called_once_with(
            category="index",
            limit=12,
            sort="6m",
            include_categories=True,
        )

    def test_invalid_fund_input_remains_a_400(self):
        with patch.object(fund_router.funds_mod, "analyze_fund", side_effect=ValueError("invalid code")):
            with self.assertRaises(HTTPException) as context:
                fund_router.analyze_fund("000000", 36)

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, "invalid code")

    def test_provider_failure_remains_a_502_with_context(self):
        with patch.object(fund_router.funds_mod, "get_fund_portfolio", side_effect=RuntimeError("provider unavailable")):
            with self.assertRaises(HTTPException) as context:
                fund_router.fund_portfolio("110022", None)

        self.assertEqual(context.exception.status_code, 502)
        self.assertIn("真实基金持仓数据获取失败", context.exception.detail)
        self.assertIn("provider unavailable", context.exception.detail)

    def test_disclosure_changes_forwards_code_and_year(self):
        expected = {"status": "available", "code": "110022"}
        with patch.object(fund_router.funds_mod, "get_fund_disclosure_changes", return_value=expected) as service:
            actual = fund_router.fund_disclosure_changes("110022", "2025")

        self.assertEqual(actual, expected)
        service.assert_called_once_with(code="110022", year="2025")

    def test_fund_estimate_forwards_code(self):
        expected = {"status": "available", "code": "110022"}
        with patch.object(fund_router.funds_mod, "get_fund_estimate", return_value=expected) as service:
            actual = fund_router.fund_estimate("110022")

        self.assertEqual(actual, expected)
        service.assert_called_once_with(code="110022")

    def test_compare_request_forwards_all_codes_and_months(self):
        expected = {"codes": ["110022", "001480"]}
        request = fund_router.FundCompareRequest(codes=["110022", "001480"], months=24)
        with patch.object(fund_router.funds_mod, "compare_funds", return_value=expected) as service:
            actual = fund_router.fund_compare(request)

        self.assertEqual(actual, expected)
        service.assert_called_once_with(codes=["110022", "001480"], months=24)


if __name__ == "__main__":
    unittest.main()
