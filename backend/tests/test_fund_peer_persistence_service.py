# -*- coding: utf-8 -*-
"""Fund peer-persistence service preserves native-source and no-proxy rules."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


class FundPeerPersistenceServiceTests(unittest.TestCase):
    def test_stage_comparison_parser_reads_fund_and_peer_rows(self):
        fragment = """
        <div class="jdzfnew">
          <ul><li class="title">近3月</li><li>-2.50%</li><li>1.25%</li><li>0.40%</li></ul>
          <ul><li class="title">近6月</li><li>3.00%</li><li>4.50%</li><li>2.10%</li></ul>
          <ul><li class="title">近1年</li><li>8.00%</li><li>12.00%</li><li>7.00%</li></ul>
        </div>
        """

        periods = funds._parse_peer_stage_comparison_html(fragment)

        self.assertEqual(periods["3m"]["fund_return_pct"], -2.5)
        self.assertEqual(periods["3m"]["peer_return_pct"], 1.25)
        self.assertEqual(periods["12m"]["excess_return_pp"], -4.0)

    def test_service_passes_provider_native_series_to_versioned_diagnostic(self):
        series = {
            "fund_name": "测试基金",
            "fund_series_name": "测试基金",
            "name": "同类平均",
            "source": "东方财富基金详情页 Data_grandTotal",
            "source_url": "https://fund.eastmoney.com/001480.html",
            "series_start": "2023-01-01",
            "series_end": "2026-01-01",
            "fund_observation_count": 700,
            "observation_count": 700,
            "fund_points": [],
            "points": [],
        }
        evaluated = {
            "diagnostic_id": "fund_peer_relative_persistence",
            "diagnostic_version": "1.0.0",
            "status": "evaluated",
            "as_of": "2026-01-01",
        }
        with (
            patch.object(funds, "_fund_peer_comparison_series", return_value=series),
            patch.object(
                funds,
                "evaluate_peer_persistence",
                return_value=evaluated,
            ) as evaluator,
        ):
            result = funds.get_fund_peer_persistence("001480")

        evaluator.assert_called_once_with(series)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["peer_name"], "同类平均")
        self.assertEqual(result["provider_series"]["fund_observation_count"], 700)
        self.assertIn("不允许自动赎回", result["policy"])

    def test_provider_failure_is_explicit_and_does_not_call_evaluator(self):
        with (
            patch.object(
                funds,
                "_fund_peer_comparison_series",
                side_effect=RuntimeError("native series missing"),
            ),
            patch.object(funds, "evaluate_peer_persistence") as evaluator,
        ):
            result = funds.get_fund_peer_persistence("001480")

        evaluator.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("native series missing", result["reason"])
        self.assertEqual(result["coverage"]["aligned_observation_count"], 0)
        self.assertIn("不使用市场指数", result["policy"])

    def test_invalid_code_is_rejected_before_provider_call(self):
        with patch.object(funds, "_fund_peer_comparison_series") as provider:
            with self.assertRaises(ValueError):
                funds.get_fund_peer_persistence("123")
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
