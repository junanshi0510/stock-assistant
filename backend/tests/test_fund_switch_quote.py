# -*- coding: utf-8 -*-
"""Platform quote confirmations remain real, scoped, fresh, and auditable."""

import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import fund_switch_quote_service  # noqa: E402
import portfolio_review  # noqa: E402
import storage  # noqa: E402
from investment_policy import payload_sha256  # noqa: E402
from strategies.fund_switch_cost import build_lot_binding  # noqa: E402


def cost_review(candidate_code="000002", review_on="2026-07-15"):
    payload = {
        "diagnostic_id": "fund_switch_cost_review",
        "diagnostic_version": "1.0.0",
        "holding_id": 1,
        "selected_code": "000001",
        "candidate_code": candidate_code,
        "candidate_name": "候选基金",
        "review_on": review_on,
        "status": "ready_for_platform_quote",
        "redemption": {
            "gross_value_yuan": 1000.0,
            "disclosed_fee_yuan": 10.0,
            "net_proceeds_yuan": 990.0,
        },
        "cost_snapshots": {
            "page_promotional": {"total_switching_cost_yuan": 12.0},
            "standard_disclosed": {"total_switching_cost_yuan": 20.0},
        },
        "historical_cost_hurdle": {
            "rolling_12m_median_excess_pp": 4.0,
        },
        "decision_gate": {
            "eligible_for_platform_quote_confirmation": True,
            "cost_snapshot_complete": True,
            "executable_switch_cost_confirmed": False,
            "automatic_switch_allowed": False,
        },
    }
    payload["evidence_sha256"] = payload_sha256(payload)
    return payload


def quote_request(review, **overrides):
    payload = {
        "review_id": review["id"],
        "expected_review_payload_sha256": review["payload_sha256"],
        "platform_name": "真实销售平台",
        "quoted_at": "2026-07-15T08:00:00+08:00",
        "redemption_fee_yuan": 10.0,
        "candidate_entry_fee_yuan": 5.0,
        "expected_redemption_arrival_date": "2026-07-18",
        "candidate_purchase_available": True,
        "acknowledged_platform_quote": True,
        "acknowledged_fee_variance": False,
        "note": "提交页复核",
    }
    payload.update(overrides)
    return payload


class FundSwitchQuoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = storage._DB_PATH
        self.previous_conn = storage._conn
        with storage._lock:
            storage._conn = None
            storage._DB_PATH = str(Path(self.temp.name) / "fund-switch.db")
        holding = storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "amount": 1000,
            "shares": 1000,
            "source": "manual",
        }, user_id="user-a")
        self.holding_id = int(holding["id"])
        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "trade_type": "buy",
            "trade_date": "2025-01-01",
            "shares": 1000,
            "unit_price": 1,
            "fee": 0,
            "source": "manual",
        }, user_id="user-a")
        review_payload = cost_review()
        review_payload["holding_id"] = self.holding_id
        review_payload["ledger_binding"] = build_lot_binding(
            portfolio_review.remaining_lot_snapshot(
                "fund",
                "000001",
                user_id="user-a",
            ),
            1000,
        )
        review_payload.pop("evidence_sha256", None)
        review_payload["evidence_sha256"] = payload_sha256(review_payload)
        self.review = storage.save_fund_switch_cost_review(
            review_payload,
            self.holding_id,
            user_id="user-a",
        )
        self.now = dt.datetime(2026, 7, 15, 0, 30, tzinfo=dt.timezone.utc)

    def tearDown(self):
        with storage._lock:
            if storage._conn is not None:
                storage._conn.close()
            storage._conn = self.previous_conn
            storage._DB_PATH = self.previous_path
        self.temp.cleanup()

    def submit(self, **overrides):
        return fund_switch_quote_service.submit_fund_switch_quote(
            self.holding_id,
            quote_request(self.review, **overrides),
            user_id="user-a",
            actor_id="actor-a",
            now=self.now,
        )

    def test_cost_review_is_content_addressed_immutable_and_user_scoped(self):
        duplicate = storage.save_fund_switch_cost_review(
            self.review["payload"],
            self.holding_id,
            user_id="user-a",
        )
        self.assertEqual(duplicate["id"], self.review["id"])
        self.assertTrue(duplicate["deduplicated"])
        self.assertTrue(storage.verify_fund_switch_cost_review(
            self.review["id"], user_id="user-a"
        )["verified"])
        self.assertIsNone(storage.get_fund_switch_cost_review(
            self.review["id"], user_id="user-b"
        ))
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE fund_switch_cost_reviews SET status='changed' WHERE id=?",
                (self.review["id"],),
            )

    def test_current_quote_confirms_cost_but_never_allows_automatic_switch(self):
        result = self.submit()

        self.assertEqual(result["status"], "confirmed_current")
        self.assertEqual(result["revision"], 1)
        self.assertEqual(
            result["payload"]["confirmed_cost"]["total_switching_cost_yuan"],
            15.0,
        )
        self.assertEqual(
            result["payload"]["historical_cost_hurdle"]["confirmed_cost_coverage_months"],
            4.5,
        )
        self.assertTrue(result["payload"]["decision_gate"]["executable_switch_cost_confirmed"])
        self.assertFalse(result["payload"]["decision_gate"]["automatic_switch_allowed"])
        self.assertTrue(result["integrity"]["verified"])
        audit = storage.verify_fund_switch_quote_audit(
            self.holding_id, "000002", user_id="user-a"
        )
        self.assertTrue(audit["verified"])

    def test_new_quote_appends_revision_and_old_rows_cannot_be_mutated(self):
        first = self.submit()
        second = self.submit(note="再次从平台确认")

        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["integrity"]["previous_hash"], first["integrity"]["event_hash"])
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "DELETE FROM fund_switch_quote_events WHERE id=?",
                (first["id"],),
            )

    def test_quote_expires_dynamically_without_overwriting_audit_event(self):
        self.submit()
        expired = fund_switch_quote_service.get_latest_quote(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=dt.datetime(2026, 7, 16, 0, 1, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(expired["status"], "expired")
        self.assertFalse(expired["payload"]["decision_gate"]["quote_current"])
        self.assertFalse(expired["payload"]["decision_gate"]["executable_switch_cost_confirmed"])

    def test_ledger_change_or_new_cost_review_supersedes_old_quote(self):
        self.submit()
        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "trade_type": "buy",
            "trade_date": "2026-07-15",
            "shares": 1,
            "unit_price": 1,
            "fee": 0,
            "source": "manual",
        }, user_id="user-a")

        ledger_changed = fund_switch_quote_service.get_latest_quote(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=self.now,
        )
        self.assertEqual(ledger_changed["status"], "superseded")
        self.assertFalse(
            ledger_changed["payload"]["decision_gate"]["executable_switch_cost_confirmed"]
        )
        self.assertEqual(
            ledger_changed["payload"]["decision_gate"]["reason"],
            "portfolio_ledger_changed",
        )

        review_changed = fund_switch_quote_service.get_latest_quote(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=self.now,
            expected_review_id="fund_switch_cost_new",
            expected_review_payload_sha256="f" * 64,
        )
        self.assertEqual(review_changed["status"], "superseded")
        self.assertEqual(
            review_changed["payload"]["decision_gate"]["reason"],
            "cost_review_refreshed",
        )

    def test_material_fee_variance_requires_explicit_acknowledgement(self):
        with self.assertRaisesRegex(
            fund_switch_quote_service.QuoteValidationError,
            "明显超出",
        ):
            self.submit(redemption_fee_yuan=50, candidate_entry_fee_yuan=50)

        result = self.submit(
            redemption_fee_yuan=50,
            candidate_entry_fee_yuan=50,
            acknowledged_fee_variance=True,
        )
        comparison = result["payload"]["disclosed_comparison"]
        self.assertTrue(comparison["material_variance"])
        self.assertTrue(comparison["acknowledged_fee_variance"])

    def test_unavailable_candidate_is_recorded_but_cost_gate_stays_closed(self):
        result = self.submit(candidate_purchase_available=False)

        self.assertEqual(result["status"], "confirmed_with_blocker")
        self.assertFalse(result["payload"]["decision_gate"]["executable_switch_cost_confirmed"])
        self.assertEqual(
            result["payload"]["decision_gate"]["reason"],
            "candidate_purchase_unavailable",
        )

    def test_changed_ledger_is_rejected_before_quote_event_is_saved(self):
        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "trade_type": "buy",
            "trade_date": "2026-07-15",
            "shares": 1,
            "unit_price": 1,
            "fee": 0,
            "source": "manual",
        }, user_id="user-a")

        with self.assertRaisesRegex(
            fund_switch_quote_service.CostReviewConflictError,
            "FIFO",
        ):
            self.submit()
        self.assertEqual(storage.list_fund_switch_quote_events(
            holding_id=self.holding_id,
            candidate_code="000002",
            user_id="user-a",
        ), [])

    def test_stale_cross_day_or_hash_mismatched_quote_is_rejected(self):
        with self.assertRaises(fund_switch_quote_service.QuoteValidationError):
            self.submit(quoted_at="2026-07-13T08:00:00+08:00")
        with self.assertRaises(fund_switch_quote_service.CostReviewConflictError):
            self.submit(expected_review_payload_sha256="a" * 64)
        with self.assertRaises(fund_switch_quote_service.HoldingNotFoundError):
            fund_switch_quote_service.submit_fund_switch_quote(
                self.holding_id,
                quote_request(self.review),
                user_id="user-b",
                actor_id="actor-b",
                now=self.now,
            )

    def test_list_returns_only_latest_revision_for_each_candidate(self):
        self.submit()
        self.submit(note="second")

        result = fund_switch_quote_service.list_holding_quotes(
            self.holding_id,
            user_id="user-a",
            now=self.now,
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
