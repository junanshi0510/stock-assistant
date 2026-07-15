# -*- coding: utf-8 -*-
"""Fund switch pre-trade reviews remain fail-closed, scoped, and immutable."""

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
from tests.test_fund_switch_quote import cost_review, quote_request  # noqa: E402
from tests.test_investment_policy import valid_policy  # noqa: E402


def market_profile(code="000002"):
    return {
        "resolution_status": "identified",
        "code": code,
        "name": "候选基金",
        "market": {
            "primary": "mainland",
            "label": "A股基金",
            "required_permissions": ["mainland"],
            "currency_risk": False,
        },
        "valuation": {"confirmed_nav_lag": "以基金管理人确认净值日为准"},
        "source": "东方财富基金代码搜索库 + 东方财富基金详情页",
        "source_url": f"https://fund.eastmoney.com/{code}.html",
    }


def disclosure(code, stock_ratio, industry):
    return {
        "code": code,
        "name": f"基金{code}",
        "source": "天天基金投资组合 / 东方财富基金档案",
        "source_url": f"https://fundf10.eastmoney.com/ccmx_{code}.html",
        "asset_period": "2026-06-30",
        "stock_period": "2026-06-30",
        "industry_period": "2026-06-30",
        "asset_allocation": {
            "stock_ratio": stock_ratio,
            "bond_ratio": 100 - stock_ratio,
            "cash_ratio": 0,
        },
        "stocks": [{
            "code": "600000" if code == "000002" else "600036",
            "name": "真实披露持股",
            "nav_ratio": stock_ratio,
        }],
        "industries": [{"name": industry, "nav_ratio": stock_ratio}],
    }


class FundSwitchExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = storage._DB_PATH
        self.previous_conn = storage._conn
        with storage._lock:
            storage._conn = None
            storage._DB_PATH = str(Path(self.temp.name) / "fund-switch-execution.db")

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
        self.now = dt.datetime(2026, 7, 15, 0, 30, tzinfo=dt.timezone.utc)
        self.quote = fund_switch_quote_service.submit_fund_switch_quote(
            self.holding_id,
            quote_request(self.cost_review),
            user_id="user-a",
            actor_id="actor-a",
            now=self.now,
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

    def _request(self):
        return {
            "expected_quote_event_id": self.quote["id"],
            "expected_quote_event_hash": self.quote["integrity"]["event_hash"],
            "acknowledged_holding_thesis": True,
        }

    def _portfolio_provider(self, code):
        if code == "000002":
            return disclosure(code, 40, "电子")
        if code == "000003":
            return disclosure(code, 10, "银行")
        raise RuntimeError(f"unexpected disclosure:{code}")

    def _create(self, provider=None):
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
            return fund_switch_execution_service.create_execution_review(
                self.holding_id,
                "000002",
                self._request(),
                user_id="user-a",
                actor_id="actor-a",
                now=self.now,
            )

    def test_all_gates_only_release_manual_redemption_review(self):
        result = self._create()

        self.assertEqual(result["status"], "ready_for_redemption_review")
        gate = result["payload"]["decision_gate"]
        self.assertTrue(gate["redemption_review_ready"])
        self.assertFalse(gate["candidate_purchase_ready"])
        self.assertFalse(gate["full_switch_execution_ready"])
        self.assertFalse(gate["execution_authorized"])
        self.assertFalse(gate["automatic_redemption_allowed"])
        self.assertFalse(gate["automatic_purchase_allowed"])
        self.assertTrue(result["integrity"]["verified"])
        self.assertTrue(result["integrity"]["current_bindings"])
        self.assertEqual(
            result["payload"]["manual_stages"][2]["state"],
            "blocked_until_settlement",
        )
        disclosures = result["payload"]["portfolio_projection"]["fund_disclosures"]
        self.assertEqual({item["code"] for item in disclosures}, {"000002", "000003"})
        self.assertTrue(all(item["periods"]["asset"] == "2026-06-30" for item in disclosures))

    def test_absent_policy_and_thesis_are_current_blockers_not_false_supersession(self):
        holding = {
            "id": self.holding_id,
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "amount": 1000,
            "updated_at": "2026-07-15T00:00:00+00:00",
        }
        holdings_hash = fund_switch_execution_service.portfolio_exposure.holdings_sha256(
            [holding]
        )
        review = {
            "holding_id": self.holding_id,
            "candidate_code": "000002",
            "payload": {"bindings": {
                "quote_event_id": "quote-current",
                "quote_event_hash": "a" * 64,
                "profile_version_id": "",
                "profile_payload_sha256": "",
                "thesis_version_id": "",
                "thesis_payload_sha256": "",
                "current_holdings_sha256": holdings_hash,
            }},
        }
        with (
            patch.object(storage, "list_holdings", return_value=[holding]),
            patch.object(
                fund_switch_execution_service.fund_switch_quote_service,
                "get_latest_quote",
                return_value={
                    "id": "quote-current",
                    "status": "confirmed_current",
                    "integrity": {"verified": True, "event_hash": "a" * 64},
                },
            ),
            patch.object(
                storage,
                "get_investment_profile",
                return_value={"configured": False, "profile_version_id": None},
            ),
            patch.object(fund_switch_execution_service, "_current_thesis", return_value=None),
        ):
            state = fund_switch_execution_service._binding_state(
                review,
                user_id="user-a",
                now=self.now,
            )

        self.assertTrue(state["profile_current"])
        self.assertTrue(state["thesis_current"])
        self.assertTrue(state["holdings_current"])

    def test_disclosure_failure_is_persisted_as_blocker_without_fallback(self):
        def provider(code):
            if code == "000002":
                raise RuntimeError("provider timeout")
            return self._portfolio_provider(code)

        result = self._create(provider=provider)

        self.assertEqual(result["status"], "blocked_by_exposure_evidence")
        self.assertFalse(result["payload"]["decision_gate"]["redemption_review_ready"])
        self.assertIn("projected_exposure", result["payload"]["blockers"])
        self.assertEqual(
            result["payload"]["portfolio_projection"]["failed_sources"][0]["code"],
            "000002",
        )

    def test_reviews_are_hash_chained_immutable_and_user_scoped(self):
        first = self._create()
        second = self._create()

        self.assertEqual(second["revision"], 2)
        self.assertEqual(
            second["integrity"]["previous_hash"],
            first["integrity"]["review_hash"],
        )
        audit = storage.verify_fund_switch_execution_audit(
            self.holding_id,
            "000002",
            user_id="user-a",
        )
        self.assertTrue(audit["verified"])
        self.assertIsNone(storage.get_fund_switch_execution_review(
            first["id"], user_id="user-b"
        ))
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE fund_switch_execution_reviews SET status='changed' WHERE id=?",
                (first["id"],),
            )
        storage._get_conn().rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "DELETE FROM fund_switch_execution_reviews WHERE id=?",
                (first["id"],),
            )
        storage._get_conn().rollback()

    def test_holding_change_supersedes_review_without_mutating_history(self):
        created = self._create()
        storage.upsert_holding({
            "asset_type": "fund",
            "market": "基金",
            "code": "000001",
            "name": "当前基金",
            "amount": 1100,
            "shares": 1000,
            "source": "manual",
        }, user_id="user-a")

        result = fund_switch_execution_service.get_latest_execution_review(
            self.holding_id,
            "000002",
            user_id="user-a",
            now=self.now,
        )
        stored = storage.get_fund_switch_execution_review(
            created["id"], user_id="user-a"
        )
        self.assertEqual(result["status"], "superseded")
        self.assertFalse(result["payload"]["decision_gate"]["redemption_review_ready"])
        self.assertEqual(stored["status"], "ready_for_redemption_review")
        self.assertTrue(stored["integrity_verified"])

    def test_agent_summary_exposes_no_thesis_text_or_order_authority(self):
        self._create()
        summary = fund_switch_execution_service.agent_execution_summary(
            "user-a",
            target_code="000001",
            now=self.now,
        )

        self.assertEqual(summary["count"], 1)
        item = summary["items"][0]
        self.assertTrue(item["redemption_review_ready"])
        self.assertFalse(item["candidate_purchase_ready"])
        self.assertFalse(item["execution_authorized"])
        self.assertNotIn("holding_thesis", item)
        self.assertNotIn("exit_condition", str(item))

    def test_stale_quote_hash_is_rejected_before_any_review_is_saved(self):
        request = self._request()
        request["expected_quote_event_hash"] = "f" * 64
        with self.assertRaises(
            fund_switch_execution_service.ExecutionReviewConflictError
        ):
            fund_switch_execution_service.create_execution_review(
                self.holding_id,
                "000002",
                request,
                user_id="user-a",
                actor_id="actor-a",
                now=self.now,
            )
        self.assertEqual(storage.list_fund_switch_execution_reviews(
            holding_id=self.holding_id,
            candidate_code="000002",
            user_id="user-a",
        ), [])


if __name__ == "__main__":
    unittest.main()
