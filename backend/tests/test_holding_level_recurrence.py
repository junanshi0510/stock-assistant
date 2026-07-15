# -*- coding: utf-8 -*-
"""Batch holding recurrence keeps real-source failures isolated and user scoped."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import holding_level_recurrence  # noqa: E402
from auth import AuthPrincipal  # noqa: E402
from routers import portfolio as portfolio_router  # noqa: E402


def holding(asset_type: str, market: str, code: str, holding_id: int) -> dict:
    return {
        "id": holding_id,
        "asset_type": asset_type,
        "market": market,
        "code": code,
        "name": f"资产{code}",
    }


def metric(asset_type: str, status: str) -> dict:
    return {
        "metric_id": "asset_level_recurrence",
        "metric_version": "1.0.0",
        "asset_type": asset_type,
        "status": status,
        "target": {"label": "真实目标", "value": 1.2, "as_of": "2026-07-15"},
        "history": {},
        "occurrence": None,
        "nearest": None,
    }


class HoldingLevelRecurrenceTests(unittest.TestCase):
    def test_batch_preserves_order_and_routes_funds_and_stocks(self):
        rows = [
            holding("fund", "基金", "013403", 1),
            holding("stock", "港股", "00700", 2),
        ]
        stock_calls = []
        fund_calls = []

        def stock_provider(market, code, months):
            stock_calls.append((market, code, months))
            return {"level_recurrence": metric("stock", "reached")}

        def fund_provider(code):
            fund_calls.append(code)
            return {
                "level_recurrence": metric("fund", "crossed_between"),
                "conditioned_forward": {
                    "strategy_id": "fund_conditioned_forward_return",
                    "strategy_version": "1.0.0",
                    "status": "evaluated",
                },
            }

        result = holding_level_recurrence.build_holding_level_recurrence(
            rows,
            stock_months=72,
            stock_provider=stock_provider,
            fund_provider=fund_provider,
        )

        self.assertEqual([item["holding_id"] for item in result["items"]], [1, 2])
        self.assertEqual(fund_calls, ["013403"])
        self.assertEqual(stock_calls, [("港股", "00700", 72)])
        self.assertEqual(result["summary"]["matched_count"], 2)
        self.assertEqual(result["summary"]["unavailable_count"], 0)
        self.assertEqual(result["summary"]["historical_context_evaluated_count"], 1)
        self.assertEqual(
            result["items"][0]["conditioned_forward"]["strategy_id"],
            "fund_conditioned_forward_return",
        )
        self.assertIsNone(result["items"][1]["conditioned_forward"])
        self.assertEqual(result["coverage"]["stock_history_months"], 72)

    def test_one_provider_failure_does_not_replace_or_drop_other_rows(self):
        rows = [
            holding("fund", "基金", "013403", 1),
            holding("stock", "A股", "600519", 2),
        ]

        def failed_fund(_code):
            raise RuntimeError("provider timeout")

        result = holding_level_recurrence.build_holding_level_recurrence(
            rows,
            stock_provider=lambda _market, _code, _months: {
                "level_recurrence": metric("stock", "not_found_in_coverage")
            },
            fund_provider=failed_fund,
        )

        fund_result = result["items"][0]["recurrence"]
        self.assertEqual(fund_result["status"], "unavailable")
        self.assertIn("provider timeout", fund_result["reason"])
        self.assertIsNone(fund_result["target"]["value"])
        self.assertEqual(
            result["items"][1]["recurrence"]["status"],
            "not_found_in_coverage",
        )
        self.assertEqual(result["summary"]["available_count"], 1)
        self.assertEqual(result["summary"]["unavailable_count"], 1)

    def test_route_reads_only_the_authenticated_subject_holdings(self):
        principal = AuthPrincipal(
            user_id="user-a",
            subject_id="subject-a",
            username="investor",
            display_name="Investor",
            role="user",
            must_change_password=False,
            session_id="session-a",
        )
        rows = [holding("fund", "基金", "013403", 1)]
        expected = {"schema_version": "holding_level_recurrence.v1", "items": []}
        with (
            patch.object(
                portfolio_router.storage,
                "list_holdings",
                return_value=rows,
            ) as list_holdings,
            patch.object(
                portfolio_router.holding_level_recurrence,
                "build_holding_level_recurrence",
                return_value=expected,
            ) as build,
        ):
            result = portfolio_router.get_holdings_level_recurrence(
                months=84,
                principal=principal,
            )

        self.assertEqual(result, expected)
        list_holdings.assert_called_once_with(user_id="subject-a")
        build.assert_called_once_with(rows, stock_months=84, max_workers=6)


if __name__ == "__main__":
    unittest.main()
