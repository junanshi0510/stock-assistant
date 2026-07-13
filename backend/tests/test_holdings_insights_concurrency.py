# -*- coding: utf-8 -*-
"""Fund trend and overlap sources must share one bounded concurrent window."""

import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import holdings  # noqa: E402


class HoldingsInsightsConcurrencyTests(unittest.TestCase):
    def test_trends_and_overlap_start_concurrently(self):
        barrier = threading.Barrier(3)

        def analyze_fund(code, months=36):
            self.assertEqual(months, 36)
            barrier.wait(timeout=2)
            return {
                "code": code,
                "name": f"基金{code}",
                "as_of": "2026-07-12",
                "trend_state": "震荡",
                "style": "均衡",
                "metrics": {},
                "latest": {},
                "source": "真实净值",
            }

        def analyze_overlap(codes):
            barrier.wait(timeout=2)
            return {
                "codes": codes,
                "summary": {"pair_count": 1, "high_overlap_pair_count": 0},
                "pairwise": [],
                "shared_stocks": [],
            }

        fake_funds = types.SimpleNamespace(
            analyze_fund=analyze_fund,
            analyze_fund_overlap=analyze_overlap,
        )
        rows = [
            {"asset_type": "fund", "market": "基金", "code": "000001", "name": "基金1", "amount": 600},
            {"asset_type": "fund", "market": "基金", "code": "000002", "name": "基金2", "amount": 400},
        ]
        with patch.object(holdings.storage, "list_holdings", return_value=rows), \
             patch.dict(sys.modules, {"funds": fake_funds}):
            result = holdings.holdings_insights(max_funds=2)

        self.assertEqual(len(result["fund_trends"]), 2)
        self.assertEqual(result["fund_errors"], [])
        self.assertEqual(result["overlap"]["summary"]["pair_count"], 1)


if __name__ == "__main__":
    unittest.main()
