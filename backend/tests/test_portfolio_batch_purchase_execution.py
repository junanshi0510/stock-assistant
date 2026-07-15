# -*- coding: utf-8 -*-
"""Real batch purchases remain budget-bound, immutable, and FIFO-reconciled."""

import datetime as dt
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_exposure  # noqa: E402
import storage  # noqa: E402
from agent import batch_purchase_execution  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from investment_policy import payload_sha256  # noqa: E402
from routers.agent import (  # noqa: E402
    BatchPurchaseExecutionOutcomeRequest,
    CreateBatchPurchaseExecutionRequest,
)


NOW = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
QUOTED_AT = "2026-07-15T01:00:00+00:00"
EXPIRES_AT = "2026-07-16T01:00:00+00:00"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BatchPurchaseExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = storage._DB_PATH
        self.previous_conn = storage._conn
        self.db_path = str(Path(self.temp.name) / "execution.db")
        with storage._lock:
            storage._conn = None
            storage._DB_PATH = self.db_path
        storage.list_holdings(user_id="user-one")
        self.repository = AgentRepository(self.db_path)
        self.batch, self.preflight = self._create_ready_batch()

    def tearDown(self):
        with storage._lock:
            if storage._conn is not None:
                storage._conn.close()
            storage._conn = self.previous_conn
            storage._DB_PATH = self.previous_path
        self.temp.cleanup()

    def _create_ready_batch(self):
        batch, _ = self.repository.create_batch(
            "fund_deep_research",
            {
                "codes": ["000001", "000002"],
                "planned_amount": 3000,
                "acknowledged_available_cash": True,
                "include_portfolio_context": True,
                "question": "比较真实证据并完成批量基金申购后的成交对账。",
            },
            user_id="user-one",
            profile_version_id="ips-v1",
        )
        for item in batch["items"]:
            self.repository.finish_run(
                item["run"]["id"],
                status="completed",
                result={"fund": {"code": item["code"], "name": f"基金{item['code']}"}},
            )
        completed = self.repository.get_batch(batch["id"])
        run_set = []
        for item in completed["items"]:
            result = item["run"]["result"]
            run_set.append({
                "sequence_no": item["sequence_no"],
                "code": item["code"],
                "run_id": item["run"]["id"],
                "status": item["run"]["status"],
                "result_sha256": hashlib.sha256(
                    _canonical(result).encode("utf-8")
                ).hexdigest(),
            })
        holdings_hash = portfolio_exposure.holdings_sha256([])
        allocation = {
            "schema_version": "portfolio_batch_allocation.v1",
            "strategy_id": "portfolio_batch_allocation",
            "strategy_version": "1.0.0",
            "status": "ready",
            "bindings": {
                "batch_id": completed["id"],
                "batch_input_sha256": completed["input_hash"],
                "run_set": run_set,
                "run_set_sha256": hashlib.sha256(
                    _canonical(run_set).encode("utf-8")
                ).hexdigest(),
                "profile_version_id": "ips-v1",
                "profile_payload_sha256": "a" * 64,
                "portfolio_holdings_sha256": holdings_hash,
            },
            "budget": {
                "requested_total_yuan": 3000,
                "allocated_total_yuan": 3000,
            },
            "allocation": {
                "items": [
                    {"code": "000001", "name": "基金000001", "allocated_amount_yuan": 1500},
                    {"code": "000002", "name": "基金000002", "allocated_amount_yuan": 1500},
                ],
            },
            "decision_gate": {"manual_allocation_review_ready": True},
        }
        allocation_event, _ = self.repository.create_batch_allocation_event(
            completed["id"], allocation, user_id="user-one", actor_id="actor-one"
        )
        preflight = {
            "schema_version": "portfolio_batch_purchase_preflight.v1",
            "strategy_id": "portfolio_batch_purchase_preflight",
            "strategy_version": "1.0.0",
            "generated_at": NOW.isoformat(),
            "status": "ready_for_manual_purchase_review",
            "bindings": {
                "batch_id": completed["id"],
                "batch_input_sha256": completed["input_hash"],
                "allocation_event_id": allocation_event["id"],
                "allocation_event_hash": allocation_event["event_hash"],
                "allocation_payload_sha256": allocation_event["payload_sha256"],
                "request_sha256": "b" * 64,
            },
            "quotes": [
                {
                    "code": code,
                    "name": f"基金{code}",
                    "allocated_amount_yuan": 1500,
                    "platform_name": "真实销售平台",
                    "quoted_at": QUOTED_AT,
                    "quote_expires_at": EXPIRES_AT,
                    "order_amount_yuan": 1000,
                    "entry_fee_yuan": 1,
                    "expected_confirmation_date": "2026-07-16",
                    "ready": True,
                }
                for code in ("000001", "000002")
            ],
            "cashflow": {"proposed_order_total_yuan": 2000},
            "decision_gate": {
                "manual_purchase_review_ready": True,
                "execution_authorized": False,
                "automatic_purchase_allowed": False,
            },
        }
        preflight_event, _ = self.repository.append_batch_purchase_preflight_event(
            completed["id"],
            preflight,
            user_id="user-one",
            actor_id="actor-one",
            expected_previous_event_hash=None,
        )
        return self.repository.get_batch(completed["id"]), preflight_event

    def _transaction(self, code="000001", *, shares=999, user_id="user-one"):
        return storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": code,
            "name": f"基金{code}",
            "trade_type": "buy",
            "trade_date": "2026-07-15",
            "shares": shares,
            "unit_price": 1,
            "fee": 1,
            "source": "manual",
        }, user_id=user_id)

    def _request(self, transaction_id, *, previous_hash=None, acknowledge=False):
        return {
            "expected_preflight_event_id": self.preflight["id"],
            "expected_preflight_event_hash": self.preflight["event_hash"],
            "expected_previous_event_hash": previous_hash,
            "outcomes": [
                {
                    "code": "000001",
                    "resolution": "purchased",
                    "transaction_id": transaction_id,
                    "purchase_submitted_at": "2026-07-15T02:00:00+00:00",
                    "acknowledged_order_variance": acknowledge,
                },
                {
                    "code": "000002",
                    "resolution": "not_purchased",
                    "not_purchased_reason": "user_cancelled",
                    "not_purchased_detail": "平台确认前取消",
                },
            ],
        }

    def _record(self, request):
        return batch_purchase_execution.record_batch_purchase_execution(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            request,
            user_id="user-one",
            actor_id="actor-one",
            now=NOW,
        )

    def test_record_binds_real_transaction_and_is_idempotent(self):
        transaction = self._transaction()
        event, created = self._record(self._request(transaction["id"]))
        duplicate, duplicate_created = self._record(self._request(transaction["id"]))

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], event["id"])
        self.assertEqual(event["payload"]["status"], "purchases_recorded_reconciliation_pending")
        self.assertEqual(event["payload"]["summary"]["actual_cash_total_yuan"], 1000)
        binding = self.repository.get_batch_purchase_transaction_binding(
            transaction["id"], user_id="user-one"
        )
        self.assertEqual(binding["batch_id"], self.batch["id"])
        self.assertFalse(event["payload"]["decision_gate"]["automatic_purchase_allowed"])

    def test_all_not_purchased_closes_without_fake_transaction(self):
        request = {
            "expected_preflight_event_id": self.preflight["id"],
            "expected_preflight_event_hash": self.preflight["event_hash"],
            "expected_previous_event_hash": None,
            "outcomes": [
                {
                    "code": code,
                    "resolution": "not_purchased",
                    "not_purchased_reason": "risk_reassessment",
                    "not_purchased_detail": "重新评估后取消",
                }
                for code in ("000001", "000002")
            ],
        }
        event, _ = self._record(request)

        self.assertEqual(event["payload"]["status"], "completed_no_purchase")
        self.assertEqual(event["payload"]["summary"]["purchased_count"], 0)
        self.assertEqual(event["payload"]["summary"]["actual_cash_total_yuan"], 0)

    def test_budget_overrun_and_unacknowledged_variance_are_rejected(self):
        over_budget = self._transaction(shares=1600)
        with self.assertRaises(batch_purchase_execution.BatchPurchaseExecutionValidationError):
            self._record(self._request(over_budget["id"], acknowledge=True))

        material_variance = self._transaction(shares=1200)
        with self.assertRaises(batch_purchase_execution.BatchPurchaseExecutionValidationError):
            self._record(self._request(material_variance["id"]))
        accepted, _ = self._record(self._request(material_variance["id"], acknowledge=True))
        self.assertTrue(accepted["payload"]["outcomes"][0]["material_variance"])

    def test_transaction_owner_and_stale_revision_are_rejected(self):
        foreign = self._transaction(user_id="user-two")
        with self.assertRaises(batch_purchase_execution.BatchPurchaseExecutionValidationError):
            self._record(self._request(foreign["id"]))

        transaction = self._transaction()
        first, _ = self._record(self._request(transaction["id"]))
        replacement = self._transaction(shares=998)
        with self.assertRaises(batch_purchase_execution.BatchPurchaseExecutionConflictError):
            self._record(self._request(replacement["id"], previous_hash=None, acknowledge=True))
        self.assertEqual(first["sequence_no"], 1)

    def test_concurrent_batches_cannot_bind_the_same_transaction(self):
        transaction = self._transaction()
        second_batch, second_preflight = self._create_ready_batch()
        first_request = self._request(transaction["id"])
        second_request = {
            **first_request,
            "expected_preflight_event_id": second_preflight["id"],
            "expected_preflight_event_hash": second_preflight["event_hash"],
        }
        barrier = threading.Barrier(2)

        def record(batch, request):
            barrier.wait(timeout=2)
            try:
                event, _ = batch_purchase_execution.record_batch_purchase_execution(
                    self.repository,
                    self.repository.get_batch(batch["id"]),
                    request,
                    user_id="user-one",
                    actor_id="actor-one",
                    now=NOW,
                )
                return event["batch_id"]
            except batch_purchase_execution.BatchPurchaseExecutionConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda args: record(*args),
                [(self.batch, first_request), (second_batch, second_request)],
            ))

        self.assertEqual(results.count("conflict"), 1)
        binding = self.repository.get_batch_purchase_transaction_binding(
            transaction["id"], user_id="user-one"
        )
        self.assertIn(binding["batch_id"], {self.batch["id"], second_batch["id"]})

    def test_preflight_cannot_be_revised_after_execution(self):
        transaction = self._transaction()
        self._record(self._request(transaction["id"]))
        revised_payload = dict(self.preflight["payload"])
        revised_payload["bindings"] = {
            **revised_payload["bindings"],
            "request_sha256": "c" * 64,
        }
        with self.assertRaises(ValueError):
            self.repository.append_batch_purchase_preflight_event(
                self.batch["id"],
                revised_payload,
                user_id="user-one",
                actor_id="actor-one",
                expected_previous_event_hash=self.preflight["event_hash"],
            )

    def test_fifo_reconciliation_closes_and_later_holding_change_is_stale(self):
        transaction = self._transaction()
        purchase, _ = self._record(self._request(transaction["id"]))
        holding = storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "amount": 999,
            "shares": 999,
            "source": "manual",
        }, user_id="user-one")
        request = {
            "expected_purchase_event_id": purchase["id"],
            "expected_purchase_event_hash": purchase["event_hash"],
            "expected_previous_event_hash": purchase["event_hash"],
        }
        reconciled, created = batch_purchase_execution.reconcile_batch_purchase_holdings(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            request,
            user_id="user-one",
            actor_id="actor-one",
            now=NOW,
        )
        current = batch_purchase_execution.decorate_batch_purchase_execution(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            user_id="user-one",
        )

        self.assertTrue(created)
        self.assertEqual(reconciled["event_type"], "holdings_reconciled")
        self.assertEqual(current["status"], "completed_reconciled")
        self.assertTrue(current["current_bindings"]["reconciliation_current"])

        storage.upsert_holding({
            **holding,
            "shares": 998,
            "amount": 998,
        }, user_id="user-one")
        stale = batch_purchase_execution.decorate_batch_purchase_execution(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            user_id="user-one",
        )
        self.assertEqual(stale["status"], "completed_reconciliation_stale")

    def test_reconciliation_blocks_until_confirmed_shares_match_fifo(self):
        transaction = self._transaction()
        purchase, _ = self._record(self._request(transaction["id"]))
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "amount": 900,
            "shares": 900,
            "source": "manual",
        }, user_id="user-one")
        with self.assertRaises(batch_purchase_execution.BatchPurchaseExecutionValidationError):
            batch_purchase_execution.reconcile_batch_purchase_holdings(
                self.repository,
                self.repository.get_batch(self.batch["id"]),
                {
                    "expected_purchase_event_id": purchase["id"],
                    "expected_purchase_event_hash": purchase["event_hash"],
                    "expected_previous_event_hash": purchase["event_hash"],
                },
                user_id="user-one",
                actor_id="actor-one",
                now=NOW,
            )

    def test_deleted_bound_transaction_marks_integrity_failed(self):
        transaction = self._transaction()
        self._record(self._request(transaction["id"]))
        storage.delete_portfolio_transaction(transaction["id"], user_id="user-one")
        decorated = batch_purchase_execution.decorate_batch_purchase_execution(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            user_id="user-one",
        )
        self.assertEqual(decorated["status"], "integrity_failed")
        self.assertTrue(any("已不存在" in item for item in decorated["blockers"]))

    def test_execution_events_and_bindings_are_immutable(self):
        transaction = self._transaction()
        event, _ = self._record(self._request(transaction["id"]))
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE agent_batch_purchase_execution_events SET actor_id='changed' WHERE id=?",
                    (event["id"],),
                )
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM agent_batch_purchase_transaction_bindings WHERE transaction_id=?",
                    (transaction["id"],),
                )

    def test_request_models_require_complete_real_outcomes(self):
        with self.assertRaises(Exception):
            BatchPurchaseExecutionOutcomeRequest(
                code="000001",
                resolution="purchased",
                transaction_id=1,
            )
        with self.assertRaises(Exception):
            CreateBatchPurchaseExecutionRequest(
                expected_preflight_event_id="preflight_event",
                expected_preflight_event_hash="a" * 64,
                outcomes=[
                    {
                        "code": "000001",
                        "resolution": "not_purchased",
                        "not_purchased_reason": "user_cancelled",
                    },
                    {
                        "code": "000001",
                        "resolution": "not_purchased",
                        "not_purchased_reason": "user_cancelled",
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
