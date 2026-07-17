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
    "/api/holdings/{holding_id}/fund-alternatives": {"GET"},
    "/api/holdings/{holding_id}/fund-switch-quotes": {"GET", "POST"},
    "/api/holdings/{holding_id}/fund-switch-quotes/{candidate_code}/audit": {"GET"},
    "/api/holdings/{holding_id}/fund-switch-execution-reviews/{candidate_code}": {"GET", "POST"},
    "/api/holdings/{holding_id}/fund-switch-cases/{candidate_code}": {"GET"},
    "/api/holdings/{holding_id}/fund-switch-cases/{candidate_code}/settlements": {"POST"},
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
    "/api/portfolio/fund-switch-cases": {"GET"},
    "/api/portfolio/fund-switch-cases/{case_id}": {"GET"},
    "/api/portfolio/fund-switch-cases/{case_id}/purchase-requotes": {"POST"},
    "/api/portfolio/fund-switch-cases/{case_id}/purchases": {"POST"},
    "/api/portfolio/fund-switch-cases/{case_id}/reconciliation": {"POST"},
    "/api/portfolio/fund-switch-cases/{case_id}/attribution-snapshots": {"POST"},
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
    "/api/holdings/ocr-jobs/{job_id}": {"GET", "DELETE"},
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
    "/api/funds/peer-persistence": {"GET"},
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
    "/api/v1/agent/batches/{batch_id}/allocation": {"POST"},
    "/api/v1/agent/batches/{batch_id}/purchase-preflight": {"POST"},
    "/api/v1/agent/batches/{batch_id}/purchase-execution": {"POST"},
    "/api/v1/agent/batches/{batch_id}/purchase-reconciliation": {"POST"},
    "/api/v1/agent/batches/{batch_id}/purchase-attribution": {"POST"},
    "/api/v1/agent/batches/{batch_id}/cancel": {"POST"},
    "/api/v1/agent/runs": {"GET", "POST"},
    "/api/v1/agent/runs/{run_id}": {"GET"},
    "/api/v1/agent/runs/{run_id}/feedback": {"GET", "POST"},
    "/api/v1/agent/decision-reviews": {"GET"},
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

    def test_fund_switch_cashflow_and_execution_bindings_are_required(self):
        schemas = app.openapi()["components"]["schemas"]
        quote_required = set(schemas["FundSwitchQuoteRequest"]["required"])
        execution_required = set(
            schemas["FundSwitchExecutionReviewRequest"]["required"]
        )
        self.assertTrue({
            "redemption_gross_yuan",
            "candidate_order_amount_yuan",
            "acknowledged_settlement_risk",
        }.issubset(quote_required))
        self.assertEqual(execution_required, {
            "expected_quote_event_id",
            "expected_quote_event_hash",
            "acknowledged_holding_thesis",
        })

    def test_batch_purchase_preflight_requires_snapshot_and_platform_facts(self):
        schemas = app.openapi()["components"]["schemas"]
        request_required = set(
            schemas["CreateBatchPurchasePreflightRequest"]["required"]
        )
        quote_required = set(schemas["BatchPurchaseQuoteRequest"]["required"])
        self.assertEqual(request_required, {
            "expected_allocation_event_id",
            "expected_allocation_event_hash",
            "acknowledged_platform_quotes",
            "quotes",
        })
        self.assertTrue({
            "code",
            "platform_name",
            "quoted_at",
            "order_amount_yuan",
            "purchase_status",
        }.issubset(quote_required))

    def test_batch_purchase_execution_requires_real_event_bindings(self):
        schemas = app.openapi()["components"]["schemas"]
        execution_required = set(
            schemas["CreateBatchPurchaseExecutionRequest"]["required"]
        )
        outcome_required = set(
            schemas["BatchPurchaseExecutionOutcomeRequest"]["required"]
        )
        reconciliation_required = set(
            schemas["CreateBatchPurchaseReconciliationRequest"]["required"]
        )
        attribution_required = set(
            schemas["CreateBatchPurchaseAttributionRequest"]["required"]
        )
        self.assertEqual(execution_required, {
            "expected_preflight_event_id",
            "expected_preflight_event_hash",
            "outcomes",
        })
        self.assertEqual(outcome_required, {"code", "resolution"})
        self.assertEqual(reconciliation_required, {
            "expected_purchase_event_id",
            "expected_purchase_event_hash",
            "expected_previous_event_hash",
        })
        self.assertEqual(attribution_required, {
            "expected_reconciliation_event_id",
            "expected_reconciliation_event_hash",
        })

    def test_fund_switch_lifecycle_evidence_bindings_are_required(self):
        schemas = app.openapi()["components"]["schemas"]
        settlement_required = set(
            schemas["FundSwitchSettlementRequest"]["required"]
        )
        requote_required = set(
            schemas["FundSwitchPurchaseRequoteRequest"]["required"]
        )
        purchase_required = set(
            schemas["FundSwitchPurchaseRecordRequest"]["required"]
        )

        self.assertEqual(settlement_required, {
            "expected_execution_review_id",
            "expected_execution_review_hash",
            "redemption_transaction_id",
            "redemption_submitted_at",
            "settled_on",
            "actual_received_yuan",
        })
        self.assertEqual(requote_required, {
            "platform_name",
            "quoted_at",
            "candidate_order_amount_yuan",
            "candidate_entry_fee_yuan",
            "expected_confirmation_date",
            "candidate_purchase_available",
            "acknowledged_platform_quote",
        })
        self.assertEqual(purchase_required, {
            "expected_purchase_quote_event_id",
            "expected_purchase_quote_event_hash",
            "purchase_transaction_id",
            "purchase_submitted_at",
        })

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
