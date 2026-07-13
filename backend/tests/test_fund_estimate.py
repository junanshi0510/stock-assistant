# -*- coding: utf-8 -*-
"""Fund estimate regression coverage without live provider calls."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


class FundEstimateTests(unittest.TestCase):
    def test_estimate_keeps_confirmed_nav_separate_from_provider_estimate(self):
        profile = {
            "code": "110022",
            "name": "示例基金",
            "confirmed_nav_date": "2026-07-10",
            "confirmed_nav": 2.5,
            "estimate_date": "2026-07-11 14:30",
            "estimate_nav": 2.55,
            "estimate_return": 2.0,
        }
        history = pd.DataFrame([
            {"date": "2026-07-08", "unit_nav": 2.6},
            {"date": "2026-07-09", "unit_nav": 2.5},
        ])
        with (
            patch.object(funds, "_fetch_profile", return_value=profile),
            patch.object(funds, "_fetch_nav_history", return_value=history),
        ):
            result = funds.get_fund_estimate("110022")

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["confirmed"]["unit_nav"], 2.5)
        self.assertEqual(result["estimate"]["unit_nav"], 2.55)
        self.assertEqual(result["estimate"]["change_pct"], 2.0)
        self.assertEqual(result["estimate"]["change_value"], 0.05)
        self.assertEqual(result["level_recurrence"]["status"], "crossed_between")
        self.assertEqual(result["level_recurrence"]["target"]["value"], 2.55)
        self.assertIn("不等于基金最终确认净值", result["policy"])

    def test_missing_estimate_is_explicit_instead_of_reusing_confirmed_nav(self):
        profile = {
            "code": "110022",
            "name": "示例基金",
            "confirmed_nav_date": "2026-07-10",
            "confirmed_nav": 2.5,
            "estimate_date": "",
            "estimate_nav": None,
            "estimate_return": None,
        }
        with patch.object(funds, "_fetch_profile", return_value=profile):
            result = funds.get_fund_estimate("110022")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["confirmed"]["unit_nav"], 2.5)
        self.assertIsNone(result["estimate"]["unit_nav"])
        self.assertEqual(result["level_recurrence"]["status"], "unavailable")
        self.assertIn("不会用历史净值", result["reason"])

    def test_invalid_code_is_rejected(self):
        with self.assertRaises(ValueError):
            funds.get_fund_estimate("ABC")


if __name__ == "__main__":
    unittest.main()
