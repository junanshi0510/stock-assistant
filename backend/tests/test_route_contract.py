# -*- coding: utf-8 -*-
"""Keep public API paths stable while routers are refactored internally."""

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from main import app  # noqa: E402


EXPECTED_OPERATIONS = {
    "/api/markets": {"GET"},
    "/api/presets": {"GET"},
    "/api/search_us": {"GET"},
    "/api/analyze": {"GET"},
    "/api/backtest": {"GET"},
    "/api/fundamentals": {"GET"},
    "/api/quote": {"GET"},
    "/api/ml": {"GET"},
    "/api/news": {"GET"},
    "/api/compare": {"GET"},
    "/api/scan": {"POST"},
    "/api/multi_compare": {"POST"},
    "/api/watchlist": {"GET", "POST", "DELETE"},
    "/api/holdings": {"GET", "POST"},
    "/api/holdings/insights": {"GET"},
    "/api/holdings/exposure": {"GET"},
    "/api/investment-profile": {"GET", "PUT"},
    "/api/decision-center": {"GET"},
    "/api/portfolio/transactions": {"GET", "POST"},
    "/api/portfolio/transactions/parse-csv": {"POST"},
    "/api/portfolio/transactions/import-csv": {"POST"},
    "/api/portfolio/transactions/{transaction_id}": {"DELETE"},
    "/api/portfolio/ledger": {"GET"},
    "/api/portfolio/performance": {"GET"},
    "/api/portfolio/behavior": {"GET"},
    "/api/portfolio/attribution": {"GET"},
    "/api/portfolio/rebalance": {"GET"},
    "/api/portfolio/snapshots": {"GET", "POST"},
    "/api/holdings/{holding_id}": {"DELETE"},
    "/api/holdings/parse-text": {"POST"},
    "/api/holdings/parse-file": {"POST"},
    "/api/holdings/ocr-upload": {"POST"},
    "/api/alerts": {"GET", "DELETE"},
    "/api/alerts/scan": {"POST"},
    "/api/hot": {"GET"},
    "/api/sectors": {"GET"},
    "/api/market/daily": {"GET"},
    "/api/funds/hot": {"GET"},
    "/api/funds/categories": {"GET"},
    "/api/funds/opportunities": {"GET"},
    "/api/funds/search": {"GET"},
    "/api/funds/analyze": {"GET"},
    "/api/funds/portfolio": {"GET"},
    "/api/funds/estimate": {"GET"},
    "/api/funds/disclosure-changes": {"GET"},
    "/api/funds/peers": {"GET"},
    "/api/funds/alternatives": {"GET"},
    "/api/funds/dividends": {"GET"},
    "/api/funds/compare": {"POST"},
    "/api/funds/overlap": {"POST"},
    "/api/v1/agent/tools": {"GET"},
    "/api/v1/agent/runs": {"GET", "POST"},
    "/api/v1/agent/runs/{run_id}": {"GET"},
    "/api/v1/agent/runs/{run_id}/rerun": {"POST"},
    "/api/v1/agent/runs/{run_id}/cancel": {"POST"},
    "/api/v1/agent/runs/{run_id}/evidence/{evidence_id}": {"GET"},
    "/api/v1/agent/runs/{run_id}/audit": {"GET"},
}


class RouteContractTests(unittest.TestCase):
    def test_public_api_paths_and_methods_are_unchanged(self):
        schema = app.openapi()
        actual = {
            path: {method.upper() for method in operations}
            for path, operations in schema["paths"].items()
            if path.startswith("/api/")
        }
        self.assertEqual(actual, EXPECTED_OPERATIONS)


if __name__ == "__main__":
    unittest.main()
