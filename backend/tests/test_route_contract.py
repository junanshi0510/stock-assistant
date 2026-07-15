# -*- coding: utf-8 -*-
"""Keep public API paths stable while routers are refactored internally."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from main import app  # noqa: E402
from routers import agent as agent_router  # noqa: E402


EXPECTED_OPERATIONS = {
    "/api/auth/session": {"GET"},
    "/api/auth/login": {"POST"},
    "/api/auth/register": {"POST"},
    "/api/auth/logout": {"POST"},
    "/api/auth/change-password": {"POST"},
    "/api/admin/overview": {"GET"},
    "/api/admin/users": {"GET", "POST"},
    "/api/admin/users/{user_id}": {"PATCH"},
    "/api/admin/users/{user_id}/reset-password": {"POST"},
    "/api/admin/auth-audit": {"GET"},
    "/api/markets": {"GET"},
    "/api/presets": {"GET"},
    "/api/search_us": {"GET"},
    "/api/analyze": {"GET"},
    "/api/backtest": {"GET"},
    "/api/fundamentals": {"GET"},
    "/api/quote": {"GET"},
    "/api/quote/level-history": {"GET"},
    "/api/ml": {"GET"},
    "/api/news": {"GET"},
    "/api/compare": {"GET"},
    "/api/scan": {"POST"},
    "/api/multi_compare": {"POST"},
    "/api/watchlist": {"GET", "POST", "DELETE"},
    "/api/holdings": {"GET", "POST"},
    "/api/holdings/level-recurrence": {"GET"},
    "/api/holdings/insights": {"GET"},
    "/api/holdings/exposure": {"GET"},
    "/api/holdings/exposure-snapshots": {"GET", "POST"},
    "/api/holdings/exposure-snapshots/{snapshot_id}": {"GET"},
    "/api/investment-profile": {"GET", "PUT"},
    "/api/investment-profile/drafts": {"POST"},
    "/api/investment-profile/versions": {"GET"},
    "/api/investment-profile/versions/{version_id}/activate": {"POST"},
    "/api/investment-profile/audit": {"GET"},
    "/api/decision-center": {"GET"},
    "/api/decision-tasks": {"GET"},
    "/api/decision-tasks/summary": {"GET"},
    "/api/decision-tasks/{task_id}": {"PATCH"},
    "/api/decision-tasks/{task_id}/audit": {"GET"},
    "/api/decision-check-schedule": {"GET", "PUT"},
    "/api/portfolio/transactions": {"GET", "POST"},
    "/api/portfolio/transactions/parse-csv": {"POST"},
    "/api/portfolio/transactions/import-csv": {"POST"},
    "/api/portfolio/transactions/{transaction_id}": {"DELETE"},
    "/api/portfolio/ledger": {"GET"},
    "/api/portfolio/performance": {"GET"},
    "/api/portfolio/behavior": {"GET"},
    "/api/portfolio/attribution": {"GET"},
    "/api/portfolio/rebalance": {"GET"},
    "/api/portfolio/theses": {"GET", "POST"},
    "/api/portfolio/theses/{asset_type}/{code}": {"GET"},
    "/api/portfolio/action-reports": {"GET", "POST"},
    "/api/portfolio/action-reports/latest": {"GET"},
    "/api/portfolio/action-reports/{report_id}": {"GET"},
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
    "/api/v1/agent/model/status": {"GET"},
    "/api/v1/agent/strategies": {"GET"},
    "/api/v1/agent/strategies/{strategy_id}/{strategy_version}": {"GET"},
    "/api/v1/agent/strategies/{strategy_id}/{strategy_version}/shadow-outcomes": {"GET"},
    "/api/v1/agent/batches": {"GET", "POST"},
    "/api/v1/agent/batches/{batch_id}": {"GET"},
    "/api/v1/agent/batches/{batch_id}/cancel": {"POST"},
    "/api/v1/agent/runs": {"GET", "POST"},
    "/api/v1/agent/runs/{run_id}": {"GET"},
    "/api/v1/agent/runs/{run_id}/strategy-shadow-outcome": {"GET"},
    "/api/v1/agent/runs/{run_id}/evaluate": {"POST"},
    "/api/v1/agent/runs/{run_id}/evaluations": {"GET"},
    "/api/v1/agent/runs/{run_id}/outcome-schedule": {"GET", "PUT"},
    "/api/v1/agent/runs/{run_id}/comparison": {"GET"},
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

    def test_public_strategy_audit_hides_operator_identity_and_reason(self):
        event = {
            "sequence_no": 2,
            "event_type": "strategy.status.changed",
            "actor_role": "reviewer",
            "actor_id": "internal-reviewer@example.test",
            "details": {
                "from_status": "shadow",
                "to_status": "canary",
                "reason": "internal incident and approval details",
                "release_assessment": {"release_ready": True},
            },
            "previous_hash": "a" * 64,
            "event_hash": "b" * 64,
            "created_at": "2026-07-13T00:00:00+00:00",
        }
        with (
            patch.object(
                agent_router.strategy_governance,
                "get_public",
                return_value={"strategy_id": "sample", "strategy_version": "1.0.0"},
            ),
            patch.object(
                agent_router.repository,
                "list_strategy_audit_events",
                return_value=[event],
            ),
            patch.object(
                agent_router.repository,
                "verify_strategy_audit_chain",
                return_value={"verified": True},
            ),
        ):
            response = agent_router.get_agent_strategy("sample", "1.0.0")

        public_event = response["audit"]["items"][0]
        self.assertNotIn("actor_id", public_event)
        self.assertNotIn("reason", public_event["details"])
        self.assertEqual(public_event["actor_role"], "reviewer")
        self.assertEqual(public_event["details"]["to_status"], "canary")
        self.assertTrue(response["audit"]["verification"]["verified"])

    def test_tool_catalog_exposes_versioned_shadow_outcome_as_read_only(self):
        catalog = agent_router.get_agent_tool_catalog()["items"]
        item = next(
            value for value in catalog
            if value["name"] == "fund.strategy_shadow_outcome.get"
        )
        self.assertEqual(item["version"], "1.0.0")
        self.assertEqual(item["risk_level"], "R0")


if __name__ == "__main__":
    unittest.main()
