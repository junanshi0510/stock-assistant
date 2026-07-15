# -*- coding: utf-8 -*-
"""Post-redemption fund replacement lifecycle stays real, scoped, and auditable."""

import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import fund_switch_execution_service  # noqa: E402
import fund_switch_lifecycle_service  # noqa: E402
import fund_switch_quote_service  # noqa: E402
import holding_thesis  # noqa: E402
import portfolio_review  # noqa: E402
import storage  # noqa: E402
from investment_policy import (  # noqa: E402
    CONSENT_TEXT_SHA256,
    CONSENT_VERSION,
    payload_sha256,
    validate_investment_policy,
)
from strategies.fund_switch_cost import build_lot_binding  # noqa: E402
from tests.test_fund_switch_execution import disclosure, market_profile  # noqa: E402
from tests.test_fund_switch_quote import cost_review, quote_request  # noqa: E402
from tests.test_investment_policy import valid_policy  # noqa: E402


def nav_history(code, points):
    return {
        "code": code,
        "source": "东方财富基金净值走势 / 天天基金历史净值",
        "source_url": f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        "as_of": points[-1][0],
        "points": [
            {"date": date, "unit_nav": nav, "acc_nav": nav}
            for date, nav in points
        ],
    }


def distributions(code, dividends=None, splits=None):
    return {
        "code": code,
        "source": "天天基金分红送配详情",
        "source_url": f"https://fundf10.eastmoney.com/fhsp_{code}.html",
        "dividends": dividends or [],
        "splits": splits or [],
    }


class FundSwitchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = storage._DB_PATH
        self.previous_conn = storage._conn
        with storage._lock:
            storage._conn = None
            storage._DB_PATH = str(Path(self.temp.name) / "fund-switch-lifecycle.db")

        selected = storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "amount": 1000,
            "shares": 1000,
            "source": "manual",
        }, user_id="user-a")
        self.holding_id = int(selected["id"])
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000003",
            "name": "组合内其他基金",
            "amount": 3000,
            "shares": 3000,
            "source": "manual",
        }, user_id="user-a")
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
                "fund", "000001", user_id="user-a"
            ),
            1000,
        )
        review_payload.pop("evidence_sha256", None)
        review_payload["evidence_sha256"] = payload_sha256(review_payload)
        self.cost_review = storage.save_fund_switch_cost_review(
            review_payload,
            self.holding_id,
            user_id="user-a",
        )
        self.review_now = dt.datetime(2026, 7, 15, 0, 30, tzinfo=dt.timezone.utc)
        self.quote = fund_switch_quote_service.submit_fund_switch_quote(
            self.holding_id,
            quote_request(self.cost_review),
            user_id="user-a",
            actor_id="actor-a",
            now=self.review_now,
        )
        self._activate_profile()
        holding_thesis.save_thesis({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "role": "core_growth",
            "thesis_summary": "该基金承担核心权益配置，按真实披露和风险上限持续复核。",
            "expected_holding_months": 36,
            "review_date": "2027-01-15",
            "max_loss_pct": 20,
            "max_drawdown_pct": 25,
            "add_condition": "估值与组合上限均通过后再人工复核",
            "exit_condition": "投资逻辑失效或组合风险上限被触发时人工复核",
        }, user_id="user-a")
        with (
            patch(
                "fund_switch_execution_service.funds.get_fund_market_profile",
                return_value=market_profile(),
            ),
            patch(
                "fund_switch_execution_service.funds.get_fund_portfolio",
                side_effect=self._portfolio_provider,
            ),
        ):
            self.execution_review = fund_switch_execution_service.create_execution_review(
                self.holding_id,
                "000002",
                {
                    "expected_quote_event_id": self.quote["id"],
                    "expected_quote_event_hash": self.quote["integrity"]["event_hash"],
                    "acknowledged_holding_thesis": True,
                },
                user_id="user-a",
                actor_id="actor-a",
                now=self.review_now,
            )

    def tearDown(self):
        with storage._lock:
            if storage._conn is not None:
                storage._conn.close()
            storage._conn = self.previous_conn
            storage._DB_PATH = self.previous_path
        self.temp.cleanup()

    def _activate_profile(self):
        policy = valid_policy()
        validation = validate_investment_policy(policy)
        draft = storage.create_investment_profile_draft(
            policy,
            validation,
            user_id="user-a",
            actor_id="actor-a",
        )
        storage.activate_investment_profile_version(
            draft["id"],
            expected_payload_sha256=validation["payload_sha256"],
            expected_active_version_id=None,
            consent_version=CONSENT_VERSION,
            consent_text_sha256=CONSENT_TEXT_SHA256,
            review_cycle_months=6,
            user_id="user-a",
            actor_id="actor-a",
        )

    def _portfolio_provider(self, code):
        if code == "000002":
            return disclosure(code, 40, "电子")
        if code == "000003":
            return disclosure(code, 10, "银行")
        raise RuntimeError(f"unexpected disclosure:{code}")

    def _sell(self, *, shares=1000, unit_price=1, fee=10):
        return storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "trade_type": "sell",
            "trade_date": "2026-07-15",
            "shares": shares,
            "unit_price": unit_price,
            "fee": fee,
            "source": "manual",
        }, user_id="user-a")

    def _settle(self, transaction=None, **overrides):
        transaction = transaction or self._sell()
        request = {
            "expected_execution_review_id": self.execution_review["id"],
            "expected_execution_review_hash": self.execution_review["integrity"]["review_hash"],
            "redemption_transaction_id": transaction["id"],
            "redemption_submitted_at": "2026-07-15T08:10:00+08:00",
            "settled_on": "2026-07-18",
            "actual_received_yuan": transaction["shares"] * transaction["unit_price"] - transaction["fee"],
            "acknowledged_quote_variance": False,
        }
        request.update(overrides)
        return fund_switch_lifecycle_service.create_redemption_settlement(
            self.holding_id,
            "000002",
            request,
            user_id="user-a",
            actor_id="actor-a",
            now=dt.datetime(2026, 7, 18, 2, 0, tzinfo=dt.timezone.utc),
        )

    def _requote(self, case_id, *, provider=None, **overrides):
        request = {
            "platform_name": "真实销售平台",
            "quoted_at": "2026-07-18T09:00:00+08:00",
            "candidate_order_amount_yuan": 985,
            "candidate_entry_fee_yuan": 5,
            "expected_confirmation_date": "2026-07-18",
            "candidate_purchase_available": True,
            "acknowledged_platform_quote": True,
        }
        request.update(overrides)
        with (
            patch(
                "fund_switch_execution_service.funds.get_fund_market_profile",
                return_value=market_profile(),
            ),
            patch(
                "fund_switch_execution_service.funds.get_fund_portfolio",
                side_effect=provider or self._portfolio_provider,
            ),
        ):
            return fund_switch_lifecycle_service.create_purchase_requote(
                case_id,
                request,
                user_id="user-a",
                actor_id="actor-a",
                now=dt.datetime(2026, 7, 18, 2, 0, tzinfo=dt.timezone.utc),
            )

    def _buy(self, *, shares=980, unit_price=1, fee=5):
        return storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000002",
            "name": "候选基金",
            "trade_type": "buy",
            "trade_date": "2026-07-18",
            "shares": shares,
            "unit_price": unit_price,
            "fee": fee,
            "source": "manual",
        }, user_id="user-a")

    def _record_purchase(self, case, transaction=None, **overrides):
        transaction = transaction or self._buy()
        quote_event = next(
            item for item in reversed(case["events"])
            if item["event_type"] == "purchase_requoted"
        )
        request = {
            "expected_purchase_quote_event_id": quote_event["id"],
            "expected_purchase_quote_event_hash": quote_event["event_hash"],
            "purchase_transaction_id": transaction["id"],
            "purchase_submitted_at": "2026-07-18T09:10:00+08:00",
            "acknowledged_order_variance": False,
        }
        request.update(overrides)
        return fund_switch_lifecycle_service.record_purchase(
            case["case_id"],
            request,
            user_id="user-a",
            actor_id="actor-a",
            now=dt.datetime(2026, 7, 18, 2, 30, tzinfo=dt.timezone.utc),
        )

    def _through_purchase(self):
        settled = self._settle()
        case = self._requote(settled["case"]["case_id"])
        return self._record_purchase(case)

    def _reconcile(self, case):
        storage.delete_holding(self.holding_id, user_id="user-a")
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000002",
            "name": "候选基金",
            "amount": 1078,
            "shares": 980,
            "source": "manual",
        }, user_id="user-a")
        return fund_switch_lifecycle_service.reconcile_holdings(
            case["case_id"],
            user_id="user-a",
            actor_id="actor-a",
            now=dt.datetime(2026, 7, 20, 2, 0, tzinfo=dt.timezone.utc),
        )

    def test_full_lifecycle_reconciles_and_attributes_real_common_date(self):
        purchased = self._through_purchase()
        self.assertEqual(purchased["status"], "purchase_recorded_reconciliation_pending")
        self.assertFalse(purchased["decision_gate"]["execution_authorized"])
        reconciled = self._reconcile(purchased)
        self.assertEqual(reconciled["status"], "completed_attribution_pending")
        self.assertTrue(reconciled["decision_gate"]["holdings_reconciled"])

        def nav_provider(code, months=120):
            if code == "000001":
                return nav_history(code, [("2026-07-15", 1), ("2026-07-20", 1.05)])
            return nav_history(code, [("2026-07-18", 1), ("2026-07-20", 1.10)])

        with (
            patch("fund_switch_lifecycle_service.funds.get_fund_nav_history", side_effect=nav_provider),
            patch("fund_switch_lifecycle_service.funds.get_fund_dividends", side_effect=lambda code: distributions(code)),
        ):
            attributed = fund_switch_lifecycle_service.create_attribution_snapshot(
                reconciled["case_id"],
                user_id="user-a",
                actor_id="actor-a",
                now=dt.datetime(2026, 7, 21, 2, 0, tzinfo=dt.timezone.utc),
            )

        self.assertEqual(attributed["status"], "completed_attribution_available")
        metrics = attributed["attribution"]["metrics"]
        self.assertEqual(metrics["as_of"], "2026-07-20")
        self.assertEqual(metrics["actual_switch_path_value_yuan"], 1083.0)
        self.assertEqual(metrics["no_switch_counterfactual_value_yuan"], 1050.0)
        self.assertEqual(metrics["incremental_value_vs_hold_yuan"], 33.0)
        self.assertEqual(metrics["total_switch_fees_yuan"], 15.0)
        self.assertFalse(attributed["decision_gate"]["execution_authorized"])
        audit = storage.verify_fund_switch_lifecycle_audit(
            attributed["case_id"], user_id="user-a"
        )
        self.assertTrue(audit["verified"])
        self.assertEqual(audit["event_count"], 5)

    def test_settlement_rejects_partial_sale_and_unrelated_ledger_change(self):
        partial = self._sell(shares=900, fee=9)
        context = fund_switch_lifecycle_service.get_candidate_context(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=self.review_now,
        )
        self.assertEqual(context["eligible_redemption_transactions"], [])
        with self.assertRaisesRegex(
            fund_switch_lifecycle_service.LifecycleValidationError,
            "全部确认份额",
        ):
            self._settle(partial, actual_received_yuan=891)

        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "trade_type": "buy",
            "trade_date": "2026-07-14",
            "shares": 1,
            "unit_price": 1,
            "fee": 0,
            "source": "manual",
        }, user_id="user-a")
        full = self._sell()
        with self.assertRaisesRegex(
            fund_switch_lifecycle_service.LifecycleConflictError,
            "除本次赎回外发生变化",
        ):
            self._settle(full)

    def test_settlement_cash_and_material_quote_variance_fail_closed(self):
        sale = self._sell(unit_price=0.9, fee=9)
        with self.assertRaisesRegex(ValueError, "明显偏离"):
            self._settle(sale, actual_received_yuan=891)
        with self.assertRaisesRegex(ValueError, "到账金额"):
            self._settle(
                sale,
                actual_received_yuan=890,
                acknowledged_quote_variance=True,
            )
        result = self._settle(
            sale,
            actual_received_yuan=891,
            acknowledged_quote_variance=True,
        )
        self.assertTrue(result["case"]["settlement"]["material_quote_variance"])

    def test_purchase_requote_blocks_real_source_failure_and_never_prefunds(self):
        settled = self._settle()
        case_id = settled["case"]["case_id"]
        with self.assertRaisesRegex(
            fund_switch_lifecycle_service.LifecycleValidationError,
            "真实到账资金",
        ):
            self._requote(case_id, candidate_order_amount_yuan=991)

        def provider(code):
            if code == "000002":
                raise RuntimeError("provider timeout")
            return self._portfolio_provider(code)

        blocked = self._requote(case_id, provider=provider)
        self.assertEqual(blocked["status"], "purchase_requote_blocked")
        self.assertIn("projected_exposure", [gate["code"] for gate in blocked["gates"] if gate["status"] == "block"])
        self.assertFalse(blocked["decision_gate"]["manual_purchase_review_ready"])

    def test_active_case_blocks_parallel_start_and_failed_case_requires_new_review(self):
        settled = self._settle()
        old_transaction_id = settled["case"]["settlement"]["transaction_id"]
        old_transaction = storage.get_portfolio_transaction(
            old_transaction_id,
            user_id="user-a",
        )
        with self.assertRaisesRegex(
            fund_switch_lifecycle_service.LifecycleConflictError,
            "进行中的替换批次",
        ):
            self._settle(old_transaction)

        storage.delete_portfolio_transaction(old_transaction_id, user_id="user-a")
        failed = fund_switch_lifecycle_service.get_candidate_context(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=self.review_now,
        )
        self.assertEqual(failed["case"]["status"], "integrity_failed")
        self.assertFalse(failed["execution_review_ready"])

        with self.assertRaisesRegex(
            fund_switch_lifecycle_service.LifecycleConflictError,
            "旧替换批次使用过",
        ):
            fund_switch_lifecycle_service.create_redemption_settlement(
                self.holding_id,
                "000002",
                {
                    "expected_execution_review_id": self.execution_review["id"],
                    "expected_execution_review_hash": self.execution_review["integrity"]["review_hash"],
                    "redemption_transaction_id": 999999,
                    "redemption_submitted_at": "2026-07-15T08:10:00+08:00",
                    "settled_on": "2026-07-18",
                    "actual_received_yuan": 990,
                },
                user_id="user-a",
                actor_id="actor-a",
                now=dt.datetime(2026, 7, 18, 2, 0, tzinfo=dt.timezone.utc),
            )

        new_now = dt.datetime(2026, 7, 15, 0, 40, tzinfo=dt.timezone.utc)
        new_quote = fund_switch_quote_service.submit_fund_switch_quote(
            self.holding_id,
            quote_request(
                self.cost_review,
                quoted_at="2026-07-15T08:30:00+08:00",
                note="纠错后重新复核",
            ),
            user_id="user-a",
            actor_id="actor-a",
            now=new_now,
        )
        with (
            patch(
                "fund_switch_execution_service.funds.get_fund_market_profile",
                return_value=market_profile(),
            ),
            patch(
                "fund_switch_execution_service.funds.get_fund_portfolio",
                side_effect=self._portfolio_provider,
            ),
        ):
            new_review = fund_switch_execution_service.create_execution_review(
                self.holding_id,
                "000002",
                {
                    "expected_quote_event_id": new_quote["id"],
                    "expected_quote_event_hash": new_quote["integrity"]["event_hash"],
                    "acknowledged_holding_thesis": True,
                },
                user_id="user-a",
                actor_id="actor-a",
                now=new_now,
            )
        replacement_sale = self._sell()
        context = fund_switch_lifecycle_service.get_candidate_context(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=new_now,
        )
        self.assertTrue(context["can_start_new"])
        self.assertEqual(context["execution_review_id"], new_review["id"])
        self.assertEqual(
            [item["id"] for item in context["eligible_redemption_transactions"]],
            [replacement_sale["id"]],
        )

    def test_purchase_quote_expires_and_holding_change_supersedes(self):
        settled = self._settle()
        ready = self._requote(settled["case"]["case_id"])
        expired = fund_switch_lifecycle_service.decorate_case(
            ready["case_id"],
            user_id="user-a",
            now=dt.datetime(2026, 7, 20, 2, 0, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(expired["status"], "purchase_requote_expired")

        ready = self._requote(ready["case_id"])
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000003",
            "name": "组合内其他基金",
            "amount": 3100,
            "shares": 3000,
            "source": "manual",
        }, user_id="user-a")
        changed = fund_switch_lifecycle_service.decorate_case(
            ready["case_id"],
            user_id="user-a",
            now=dt.datetime(2026, 7, 18, 2, 30, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(changed["status"], "purchase_requote_superseded")

    def test_purchase_record_requires_quote_binding_and_variance_acknowledgement(self):
        settled = self._settle()
        ready = self._requote(settled["case"]["case_id"])
        mismatched = self._buy(shares=900, fee=5)
        with self.assertRaisesRegex(ValueError, "明显偏离"):
            self._record_purchase(ready, mismatched)
        recorded = self._record_purchase(
            ready,
            mismatched,
            acknowledged_order_variance=True,
        )
        self.assertEqual(recorded["purchase"]["actual_cash_used_yuan"], 905.0)
        self.assertTrue(recorded["purchase"]["material_order_variance"])

    def test_reconciliation_requires_confirmed_holdings_and_transaction_delete_invalidates_case(self):
        purchased = self._through_purchase()
        with self.assertRaisesRegex(ValueError, "尚未完全一致"):
            fund_switch_lifecycle_service.reconcile_holdings(
                purchased["case_id"],
                user_id="user-a",
                actor_id="actor-a",
                now=dt.datetime(2026, 7, 20, 2, 0, tzinfo=dt.timezone.utc),
            )
        purchase_id = purchased["purchase"]["transaction_id"]
        storage.delete_portfolio_transaction(purchase_id, user_id="user-a")
        invalid = fund_switch_lifecycle_service.decorate_case(
            purchased["case_id"], user_id="user-a"
        )
        self.assertEqual(invalid["status"], "integrity_failed")
        self.assertFalse(invalid["integrity"]["purchase_transaction_current"])

    def test_attribution_stops_on_untracked_distribution_or_provider_failure(self):
        reconciled = self._reconcile(self._through_purchase())

        def nav_provider(code, months=120):
            if code == "000001":
                return nav_history(code, [("2026-07-15", 1), ("2026-07-20", 1.05)])
            return nav_history(code, [("2026-07-18", 1), ("2026-07-20", 1.10)])

        def dividend_provider(code):
            if code == "000002":
                return distributions(code, dividends=[{
                    "ex_dividend_date": "2026-07-19",
                    "cash_per_share": 0.02,
                }])
            return distributions(code, dividends=[{
                "ex_dividend_date": "2026-07-15",
                "cash_per_share": 0.01,
            }])

        with (
            patch("fund_switch_lifecycle_service.funds.get_fund_nav_history", side_effect=nav_provider),
            patch("fund_switch_lifecycle_service.funds.get_fund_dividends", side_effect=dividend_provider),
        ):
            blocked = fund_switch_lifecycle_service.create_attribution_snapshot(
                reconciled["case_id"], user_id="user-a", actor_id="actor-a"
            )
        self.assertEqual(blocked["status"], "completed_attribution_blocked")
        self.assertTrue(any("分红或拆分" in reason for reason in blocked["attribution"]["reasons"]))
        self.assertEqual(
            blocked["attribution"]["source_corporate_actions"][0]["ex_dividend_date"],
            "2026-07-15",
        )

        with (
            patch("fund_switch_lifecycle_service.funds.get_fund_nav_history", side_effect=RuntimeError("timeout")),
            patch("fund_switch_lifecycle_service.funds.get_fund_dividends", side_effect=lambda code: distributions(code)),
        ):
            unavailable = fund_switch_lifecycle_service.create_attribution_snapshot(
                reconciled["case_id"], user_id="user-a", actor_id="actor-a"
            )
        self.assertEqual(unavailable["status"], "completed_attribution_blocked")
        self.assertTrue(unavailable["attribution"]["source_errors"])

    def test_attribution_refresh_stops_after_reconciled_ledger_changes(self):
        reconciled = self._reconcile(self._through_purchase())
        storage.add_portfolio_transaction({
            "asset_type": "fund",
            "market": "基金",
            "code": "000002",
            "name": "候选基金",
            "trade_type": "buy",
            "trade_date": "2026-07-21",
            "shares": 10,
            "unit_price": 1.1,
            "fee": 0,
            "source": "manual",
        }, user_id="user-a")
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000002",
            "name": "候选基金",
            "amount": 1089,
            "shares": 990,
            "source": "manual",
        }, user_id="user-a")

        changed = fund_switch_lifecycle_service.decorate_case(
            reconciled["case_id"],
            user_id="user-a",
            now=dt.datetime(2026, 7, 22, 2, 0, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(changed["status"], "completed_attribution_blocked")
        self.assertTrue(changed["integrity"]["verified"])
        self.assertFalse(changed["decision_gate"]["attribution_refresh_ready"])
        self.assertTrue(any("候选基金交易账本" in reason for reason in changed["attribution_blockers"]))
        with self.assertRaisesRegex(
            fund_switch_lifecycle_service.LifecycleConflictError,
            "不能继续跟踪",
        ):
            fund_switch_lifecycle_service.create_attribution_snapshot(
                reconciled["case_id"],
                user_id="user-a",
                actor_id="actor-a",
                now=dt.datetime(2026, 7, 22, 2, 0, tzinfo=dt.timezone.utc),
            )

    def test_events_are_immutable_user_scoped_and_agent_summary_is_minimal(self):
        purchased = self._through_purchase()
        events = storage.list_fund_switch_lifecycle_events(
            purchased["case_id"], user_id="user-a"
        )
        self.assertEqual(len(events), 3)
        self.assertEqual(events[1]["previous_hash"], events[0]["event_hash"])
        self.assertEqual(
            storage.list_fund_switch_lifecycle_events(
                purchased["case_id"], user_id="user-b"
            ),
            [],
        )
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE fund_switch_lifecycle_events SET status='changed' WHERE id=?",
                (events[0]["id"],),
            )
        storage._get_conn().rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "DELETE FROM fund_switch_lifecycle_events WHERE id=?",
                (events[0]["id"],),
            )
        storage._get_conn().rollback()
        summary = fund_switch_lifecycle_service.agent_lifecycle_summary(
            "user-a", target_code="000001"
        )
        self.assertEqual(summary["count"], 1)
        candidate_summary = fund_switch_lifecycle_service.agent_lifecycle_summary(
            "user-a", target_code="000002"
        )
        self.assertEqual(candidate_summary["count"], 1)
        unrelated_summary = fund_switch_lifecycle_service.agent_lifecycle_summary(
            "user-a", target_code="000003"
        )
        self.assertEqual(unrelated_summary["count"], 0)
        self.assertFalse(summary["items"][0]["execution_authorized"])
        self.assertNotIn("transaction_id", str(summary))
        self.assertNotIn("platform_name", str(summary))


if __name__ == "__main__":
    unittest.main()
