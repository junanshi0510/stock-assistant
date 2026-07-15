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
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_exposure  # noqa: E402
import storage  # noqa: E402
from agent import batch_purchase_attribution, batch_purchase_execution  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from investment_policy import payload_sha256  # noqa: E402
from routers.agent import (  # noqa: E402
    BatchPurchaseExecutionOutcomeRequest,
    CreateBatchPurchaseAttributionRequest,
    CreateBatchPurchaseExecutionRequest,
)


NOW = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
QUOTED_AT = "2026-07-15T01:00:00+00:00"
EXPIRES_AT = "2026-07-16T01:00:00+00:00"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _nav_history(code, points):
    return {
        "code": code,
        "source": "真实确认净值测试源",
        "source_url": f"https://example.test/{code}",
        "as_of": points[-1][0],
        "observation_count": len(points),
        "points": [
            {"date": date_value, "unit_nav": nav, "acc_nav": nav}
            for date_value, nav in points
        ],
    }


def _distributions(code, *, dividends=None, splits=None):
    return {
        "code": code,
        "source": "真实分红拆分测试源",
        "dividends": dividends or [],
        "splits": splits or [],
    }


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

    def _reconciled_purchase(self):
        transaction = self._transaction()
        purchase, _ = self._record(self._request(transaction["id"]))
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "amount": 999,
            "shares": 999,
            "source": "manual",
        }, user_id="user-one")
        reconciliation, _ = batch_purchase_execution.reconcile_batch_purchase_holdings(
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
        return transaction, purchase, reconciliation

    def _attribution_request(self, reconciliation, previous_hash=None):
        return {
            "expected_reconciliation_event_id": reconciliation["id"],
            "expected_reconciliation_event_hash": reconciliation["event_hash"],
            "expected_previous_snapshot_hash": previous_hash,
        }

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

    def test_attribution_uses_real_nav_and_waits_for_observation_window(self):
        _, _, reconciliation = self._reconciled_purchase()
        nav = _nav_history("000001", [
            ("2026-07-15", 1.0),
            ("2026-08-14", 1.1),
        ])
        with (
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_nav_history",
                return_value=nav,
            ),
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_dividends",
                return_value=_distributions("000001"),
            ),
        ):
            snapshot, created = (
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation),
                    user_id="user-one",
                    actor_id="actor-one",
                    now=dt.datetime(2026, 8, 15, 2, 0, tzinfo=dt.timezone.utc),
                )
            )
            duplicate, duplicate_created = (
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation),
                    user_id="user-one",
                    actor_id="actor-one",
                    now=dt.datetime(2026, 8, 15, 2, 0, tzinfo=dt.timezone.utc),
                )
            )

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], snapshot["id"])
        self.assertEqual(snapshot["payload"]["status"], "available")
        metrics = snapshot["payload"]["items"][0]["metrics"]
        self.assertEqual(metrics["original_cost_yuan"], 1000.0)
        self.assertEqual(metrics["current_remaining_value_yuan"], 1098.9)
        self.assertEqual(metrics["total_profit_yuan"], 98.9)
        self.assertEqual(metrics["total_return_pct"], 9.89)
        self.assertEqual(metrics["observation_days"], 30)
        self.assertTrue(snapshot["payload"]["decision_gate"]["decision_review_eligible"])
        decorated = batch_purchase_attribution.decorate_batch_purchase_attribution(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            user_id="user-one",
        )
        self.assertTrue(decorated["current_bindings"]["all_current"])
        self.assertEqual(decorated["snapshot"]["audit_event_count"], 1)

    def test_attribution_tracks_fifo_sales_and_marks_changed_ledger_stale(self):
        _, _, reconciliation = self._reconciled_purchase()
        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "trade_type": "sell",
            "trade_date": "2026-08-10",
            "shares": 499,
            "unit_price": 1.2,
            "fee": 1,
            "source": "manual",
        }, user_id="user-one")
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "amount": 575,
            "shares": 500,
            "source": "manual",
        }, user_id="user-one")
        with (
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_nav_history",
                return_value=_nav_history("000001", [
                    ("2026-07-15", 1.0),
                    ("2026-08-10", 1.2),
                    ("2026-08-14", 1.15),
                ]),
            ),
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_dividends",
                return_value=_distributions("000001"),
            ),
        ):
            snapshot, _ = (
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation),
                    user_id="user-one",
                    actor_id="actor-one",
                    now=dt.datetime(2026, 8, 15, 2, 0, tzinfo=dt.timezone.utc),
                )
            )
        metrics = snapshot["payload"]["items"][0]["metrics"]
        self.assertEqual(metrics["realized_proceeds_yuan"], 597.8)
        self.assertEqual(metrics["current_remaining_value_yuan"], 575.0)
        self.assertEqual(metrics["total_profit_yuan"], 172.8)

        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "trade_type": "sell",
            "trade_date": "2026-08-15",
            "shares": 100,
            "unit_price": 1.1,
            "fee": 0,
            "source": "manual",
        }, user_id="user-one")
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "amount": 440,
            "shares": 400,
            "source": "manual",
        }, user_id="user-one")
        stale = batch_purchase_attribution.decorate_batch_purchase_attribution(
            self.repository,
            self.repository.get_batch(self.batch["id"]),
            user_id="user-one",
        )
        self.assertEqual(stale["status"], "stale_refresh_required")
        self.assertTrue(stale["refresh_ready"])
        self.assertFalse(stale["current_bindings"]["ledger_current"])

    def test_attribution_accepts_fully_realized_lot_without_empty_holding(self):
        _, _, reconciliation = self._reconciled_purchase()
        holding = storage.list_holdings(user_id="user-one")[0]
        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "trade_type": "sell",
            "trade_date": "2026-08-10",
            "shares": 999,
            "unit_price": 1.2,
            "fee": 1,
            "source": "manual",
        }, user_id="user-one")
        storage.delete_holding(holding["id"], user_id="user-one")
        with (
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_nav_history",
                return_value=_nav_history("000001", [
                    ("2026-07-15", 1.0),
                    ("2026-08-10", 1.2),
                ]),
            ),
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_dividends",
                return_value=_distributions("000001"),
            ),
        ):
            snapshot, _ = (
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation),
                    user_id="user-one",
                    actor_id="actor-one",
                    now=dt.datetime(2026, 8, 15, 2, 0, tzinfo=dt.timezone.utc),
                )
            )
        item = snapshot["payload"]["items"][0]
        self.assertEqual(item["lot"]["remaining_shares"], 0.0)
        self.assertEqual(item["metrics"]["current_remaining_value_yuan"], 0.0)
        self.assertEqual(item["metrics"]["realized_proceeds_yuan"], 1197.8)
        self.assertEqual(item["metrics"]["total_profit_yuan"], 197.8)
        self.assertEqual(item["as_of"], "2026-08-10")
        self.assertTrue(item["holding_reconciliation"]["shares_match"])

    def test_attribution_rejects_holding_mismatch_and_never_calls_provider(self):
        _, _, reconciliation = self._reconciled_purchase()
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "基金000001",
            "amount": 998,
            "shares": 998,
            "source": "manual",
        }, user_id="user-one")
        with patch.object(
            batch_purchase_attribution.funds,
            "get_fund_nav_history",
        ) as nav_provider:
            with self.assertRaises(
                batch_purchase_attribution.BatchPurchaseAttributionValidationError
            ):
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation),
                    user_id="user-one",
                    actor_id="actor-one",
                )
        nav_provider.assert_not_called()

    def test_attribution_persists_blocked_sources_and_is_immutable(self):
        _, _, reconciliation = self._reconciled_purchase()
        nav = _nav_history("000001", [
            ("2026-07-15", 1.0),
            ("2026-08-14", 1.1),
        ])
        with (
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_nav_history",
                return_value=nav,
            ),
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_dividends",
                return_value=_distributions("000001", dividends=[{
                    "ex_dividend_date": "2026-07-20",
                    "cash_per_share": 0.02,
                }]),
            ),
        ):
            blocked, _ = (
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation),
                    user_id="user-one",
                    actor_id="actor-one",
                    now=dt.datetime(2026, 8, 15, 2, 0, tzinfo=dt.timezone.utc),
                )
            )
        self.assertEqual(blocked["payload"]["status"], "unavailable")
        self.assertEqual(blocked["payload"]["aggregate"]["metrics"], {})
        self.assertTrue(blocked["payload"]["items"][0]["corporate_actions"])

        with (
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_nav_history",
                side_effect=RuntimeError("provider timeout"),
            ),
            patch.object(
                batch_purchase_attribution.funds,
                "get_fund_dividends",
                return_value=_distributions("000001"),
            ),
        ):
            unavailable, created = (
                batch_purchase_attribution.create_batch_purchase_attribution_snapshot(
                    self.repository,
                    self.repository.get_batch(self.batch["id"]),
                    self._attribution_request(reconciliation, blocked["event_hash"]),
                    user_id="user-one",
                    actor_id="actor-one",
                    now=dt.datetime(2026, 8, 16, 2, 0, tzinfo=dt.timezone.utc),
                )
            )
        self.assertTrue(created)
        self.assertEqual(unavailable["sequence_no"], 2)
        self.assertTrue(unavailable["payload"]["items"][0]["source_errors"])
        audit = self.repository.verify_batch_purchase_attribution_audit(
            self.batch["id"], user_id="user-one"
        )
        self.assertTrue(audit["verified"])
        self.assertEqual(audit["event_count"], 2)
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE agent_batch_purchase_attribution_snapshots SET actor_id='changed' WHERE id=?",
                    (blocked["id"],),
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
        request = CreateBatchPurchaseAttributionRequest(
            expected_reconciliation_event_id="reconciliation_event",
            expected_reconciliation_event_hash="a" * 64,
        )
        self.assertIsNone(request.expected_previous_snapshot_hash)


if __name__ == "__main__":
    unittest.main()
