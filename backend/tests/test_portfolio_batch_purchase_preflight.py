# -*- coding: utf-8 -*-
"""Batch purchase preflight must bind real facts, constraints and immutable audit."""

import copy
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_exposure  # noqa: E402
from agent import batch_purchase_preflight as preflight_service  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from strategies.portfolio_batch_purchase_preflight import (  # noqa: E402
    evaluate_portfolio_batch_purchase_preflight,
)


NOW = dt.datetime(2026, 7, 15, 8, 0, tzinfo=dt.timezone.utc)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _allocation(*, holdings_hash: str = "b" * 64) -> dict:
    return {
        "schema_version": "portfolio_batch_allocation.v1",
        "strategy_id": "portfolio_batch_allocation",
        "strategy_version": "1.0.0",
        "status": "ready",
        "bindings": {
            "batch_id": "batch_test",
            "batch_input_sha256": "a" * 64,
            "run_set_sha256": "d" * 64,
            "profile_version_id": "ips_v1",
            "profile_payload_sha256": "c" * 64,
            "portfolio_holdings_sha256": holdings_hash,
        },
        "budget": {
            "requested_total_yuan": 3_000,
            "allocated_total_yuan": 3_000,
            "unallocated_total_yuan": 0,
        },
        "allocation": {
            "items": [
                {"code": "000001", "name": "基金一", "allocated_amount_yuan": 2_000},
                {"code": "000002", "name": "基金二", "allocated_amount_yuan": 1_000},
            ],
        },
        "decision_gate": {"manual_allocation_review_ready": True},
    }


def _profile(*, markets=None, accept_fx=False, max_single=60, equity=80, industry=50):
    return {
        "configured": True,
        "integrity_verified": True,
        "review_required": False,
        "governance_integrity": {"verified": True},
        "profile_version_id": "ips_v1",
        "payload_sha256": "c" * 64,
        "allowed_fund_markets": markets or ["mainland"],
        "accept_fx_risk": accept_fx,
        "max_single_ratio": max_single,
        "max_equity_ratio": equity,
        "max_industry_ratio": industry,
    }


def _market(*, primary="mainland", permission="mainland", fx=False):
    return {
        "resolution_status": "identified",
        "source": "真实基金市场元数据测试源",
        "source_url": "https://example.test/market",
        "market": {
            "primary": primary,
            "label": primary,
            "required_permissions": [permission],
            "currency_risk": fx,
        },
    }


def _quotes(*, first_amount=2_000, first_fee=10, first_status="available", first_limit=None):
    rows = []
    for code, amount, fee in (
        ("000001", first_amount, first_fee),
        ("000002", 1_000, 5),
    ):
        rows.append({
            "code": code,
            "platform_name": "真实销售平台",
            "quoted_at": "2026-07-15T07:00:00+00:00",
            "currency": "CNY",
            "purchase_status": first_status if code == "000001" else "available",
            "purchase_limit_yuan": first_limit if code == "000001" else None,
            "expected_confirmation_date": "2026-07-16",
            "order_amount_yuan": amount,
            "entry_fee_yuan": fee,
            "acknowledged_platform_quote": True,
        })
    return rows


def _projected_holdings():
    return [
        {"asset_type": "fund", "code": "000003", "name": "已有基金", "amount": 10_000},
        {"asset_type": "fund", "code": "000001", "name": "基金一", "amount": 1_990},
        {"asset_type": "fund", "code": "000002", "name": "基金二", "amount": 995},
    ]


def _bindings(*, projected_hash="e" * 64):
    return {
        "allocation_integrity_verified": True,
        "holdings_binding_current": True,
        "profile_binding_current": True,
        "projected_holdings_sha256": projected_hash,
    }


def _exposure(bindings, *, eligible=True, equity=40, industry=40):
    return {
        "status": "complete" if eligible else "partial",
        "source": "用户确认持仓 + 真实基金定期披露",
        "evaluated_on": "2026-07-15",
        "profile_version_id": "ips_v1",
        "holdings_sha256": bindings["projected_holdings_sha256"],
        "summary": {
            "equity": {"upper_ratio": equity},
            "industry": {"max_upper_ratio": industry},
        },
        "quality": {"decision_eligible": eligible, "reasons": [] if eligible else ["披露缺失"]},
        "failed_sources": [] if eligible else [{"code": "000002", "error": "真实披露不可用"}],
        "funds": [],
    }


class PortfolioBatchPurchasePreflightStrategyTests(unittest.TestCase):
    def _evaluate(self, **overrides):
        bindings = overrides.pop("bindings", _bindings())
        return evaluate_portfolio_batch_purchase_preflight(
            overrides.pop("allocation", _allocation()),
            overrides.pop("quotes", _quotes()),
            profile=overrides.pop("profile", _profile()),
            market_profiles=overrides.pop("market_profiles", {
                "000001": _market(),
                "000002": _market(),
            }),
            projected_holdings=overrides.pop("projected_holdings", _projected_holdings()),
            projected_exposure=overrides.pop("projected_exposure", _exposure(bindings)),
            bindings=bindings,
            generated_at=overrides.pop("generated_at", NOW.isoformat()),
        )

    def test_complete_real_facts_are_ready_only_for_manual_review(self):
        result = self._evaluate()

        self.assertEqual(result["status"], "ready_for_manual_purchase_review")
        self.assertEqual(result["cashflow"]["proposed_order_total_yuan"], 3_000)
        self.assertEqual(result["cashflow"]["confirmed_entry_fee_total_yuan"], 15)
        self.assertTrue(result["decision_gate"]["manual_purchase_review_ready"])
        self.assertFalse(result["decision_gate"]["execution_authorized"])
        self.assertFalse(result["decision_gate"]["automatic_purchase_allowed"])
        self.assertFalse(result["decision_gate"]["order_submitted"])

    def test_amount_above_allocation_and_budget_is_blocked(self):
        result = self._evaluate(quotes=_quotes(first_amount=2_000.03))

        self.assertEqual(result["status"], "purchase_preflight_blocked")
        self.assertEqual(
            next(gate for gate in result["gates"] if gate["code"] == "one_cash_budget")["status"],
            "block",
        )
        self.assertTrue(any(
            reason.startswith("拟申购金额超过组合分配")
            for reason in result["quotes"][0]["reasons"]
        ))

    def test_limited_purchase_requires_a_sufficient_real_limit(self):
        missing = self._evaluate(quotes=_quotes(first_status="limited", first_limit=None))
        insufficient = self._evaluate(quotes=_quotes(first_status="limited", first_limit=1_000))
        sufficient = self._evaluate(quotes=_quotes(first_status="limited", first_limit=2_000))

        self.assertEqual(missing["status"], "purchase_preflight_blocked")
        self.assertEqual(insufficient["status"], "purchase_preflight_blocked")
        self.assertEqual(sufficient["status"], "ready_for_manual_purchase_review")

    def test_missing_fee_and_expired_quote_are_not_filled_with_defaults(self):
        no_fee = _quotes(first_fee=None)
        missing = self._evaluate(quotes=no_fee)
        expired = self._evaluate(generated_at="2026-07-16T08:00:01+00:00")

        self.assertEqual(missing["quotes"][0]["entry_fee_yuan"], None)
        self.assertEqual(missing["status"], "purchase_preflight_blocked")
        self.assertEqual(expired["status"], "purchase_preflight_blocked")
        self.assertEqual(
            next(gate for gate in expired["gates"] if gate["code"] == "quote_freshness")["status"],
            "block",
        )

    def test_cross_border_fund_requires_permission_and_fx_consent(self):
        markets = {
            "000001": _market(primary="hong_kong", permission="hong_kong", fx=True),
            "000002": _market(),
        }
        blocked = self._evaluate(market_profiles=markets)
        allowed = self._evaluate(
            market_profiles=markets,
            profile=_profile(markets=["mainland", "hong_kong"], accept_fx=True),
        )

        self.assertEqual(blocked["status"], "purchase_preflight_blocked")
        self.assertEqual(allowed["status"], "ready_for_manual_purchase_review")

    def test_projected_single_equity_and_industry_limits_are_enforced(self):
        concentrated = self._evaluate(profile=_profile(max_single=10))
        bindings = _bindings()
        exposed = self._evaluate(
            bindings=bindings,
            projected_exposure=_exposure(bindings, equity=90, industry=60),
        )

        self.assertEqual(concentrated["status"], "purchase_preflight_blocked")
        self.assertEqual(exposed["status"], "purchase_preflight_blocked")
        blocked_codes = {gate["code"] for gate in exposed["gates"] if gate["status"] == "block"}
        self.assertTrue({"equity_limit", "industry_limit"}.issubset(blocked_codes))

    def test_incomplete_real_disclosure_blocks_projection(self):
        bindings = _bindings()
        result = self._evaluate(
            bindings=bindings,
            projected_exposure=_exposure(bindings, eligible=False),
        )

        self.assertEqual(result["status"], "purchase_preflight_blocked")
        self.assertEqual(
            next(gate for gate in result["gates"] if gate["code"] == "projected_exposure")["status"],
            "block",
        )


def _source(code: str) -> dict:
    return {
        "source": "真实基金披露测试源",
        "source_url": f"https://example.test/fund/{code}",
        "code": code,
        "name": f"基金{code}",
        "asset_period": "2026-06-30",
        "stock_period": "2026-06-30",
        "industry_period": "2026-06-30",
        "asset_allocation": {"stock_ratio": 40, "bond_ratio": 60},
        "stocks": [],
        "industries": [{"name": "信息技术", "ratio": 40}],
    }


class BatchPurchasePreflightPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")
        self.initial_holdings = [
            {"asset_type": "fund", "code": "000003", "name": "已有基金", "amount": 10_000},
        ]
        self.profile = _profile()
        self.batch, self.allocation_event = self._create_batch_and_allocation()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_batch_and_allocation(self):
        initial_hash = portfolio_exposure.holdings_sha256(self.initial_holdings)
        batch, _ = self.repository.create_batch(
            "fund_deep_research",
            {
                "codes": ["000001", "000002"],
                "planned_amount": 3_000,
                "acknowledged_available_cash": True,
                "include_portfolio_context": True,
                "question": "比较真实证据并统一复核本批次基金申购。",
            },
            user_id="user_one",
            profile_version_id="ips_v1",
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
                "result_sha256": hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest(),
            })
        allocation = _allocation(holdings_hash=initial_hash)
        allocation["bindings"] = {
            **allocation["bindings"],
            "batch_id": completed["id"],
            "batch_input_sha256": completed["input_hash"],
            "run_set": run_set,
            "run_set_sha256": hashlib.sha256(_canonical(run_set).encode("utf-8")).hexdigest(),
        }
        event, _ = self.repository.create_batch_allocation_event(
            completed["id"], allocation, user_id="user_one", actor_id="actor_one"
        )
        return self.repository.get_batch(completed["id"]), event

    def _request(self, *, fee=10, previous_hash=None):
        quotes = _quotes(first_fee=fee)
        for quote in quotes:
            quote.pop("acknowledged_platform_quote")
        return {
            "expected_allocation_event_id": self.allocation_event["id"],
            "expected_allocation_event_hash": self.allocation_event["event_hash"],
            "expected_previous_event_hash": previous_hash,
            "acknowledged_platform_quotes": True,
            "quotes": quotes,
        }

    def _create(self, request=None, *, disclosure_provider=_source):
        with (
            patch.object(
                preflight_service.storage,
                "list_holdings",
                return_value=copy.deepcopy(self.initial_holdings),
            ),
            patch.object(
                preflight_service.storage,
                "get_investment_profile",
                return_value=copy.deepcopy(self.profile),
            ),
        ):
            return preflight_service.create_batch_purchase_preflight(
                self.repository,
                self.repository.get_batch(self.batch["id"]),
                request or self._request(),
                user_id="user_one",
                actor_id="actor_one",
                now=NOW,
                market_profile_provider=lambda _code: _market(),
                disclosure_provider=disclosure_provider,
            )

    def test_service_uses_real_disclosures_and_creates_a_ready_event(self):
        event, created = self._create()
        payload = event["payload"]

        self.assertTrue(created)
        self.assertTrue(event["integrity_verified"])
        self.assertEqual(payload["status"], "ready_for_manual_purchase_review")
        self.assertEqual(set(payload["bindings"]["fund_disclosure_sha256"]), {
            "000001", "000002", "000003",
        })
        self.assertFalse(payload["decision_gate"]["automatic_purchase_allowed"])

    def test_request_is_idempotent_and_revisions_are_hash_chained(self):
        first, created = self._create()
        duplicate, duplicate_created = self._create()
        second_request = self._request(fee=12, previous_hash=first["event_hash"])
        second, second_created = self._create(second_request)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], first["id"])
        self.assertTrue(second_created)
        self.assertEqual(second["sequence_no"], 2)
        self.assertEqual(second["previous_hash"], first["event_hash"])
        audit = self.repository.verify_batch_purchase_preflight_audit(
            self.batch["id"], user_id="user_one"
        )
        self.assertTrue(audit["verified"])
        self.assertEqual(audit["event_count"], 2)

    def test_stale_revision_and_other_user_are_rejected(self):
        first, _ = self._create()
        with self.assertRaises(preflight_service.BatchPurchasePreflightConflictError):
            self._create(self._request(fee=12, previous_hash=None))
        with self.assertRaises(KeyError):
            self.repository.append_batch_purchase_preflight_event(
                self.batch["id"],
                first["payload"],
                user_id="user_two",
                actor_id="actor_two",
                expected_previous_event_hash=first["event_hash"],
            )

    def test_events_cannot_be_updated_or_deleted(self):
        event, _ = self._create()
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE agent_batch_purchase_preflight_events SET actor_id='changed' WHERE id=?",
                    (event["id"],),
                )
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM agent_batch_purchase_preflight_events WHERE id=?",
                    (event["id"],),
                )

    def test_disclosure_failure_is_recorded_and_blocks_without_fallback(self):
        def failing_source(code):
            if code == "000002":
                raise RuntimeError("真实披露源不可用")
            return _source(code)

        event, _ = self._create(disclosure_provider=failing_source)

        self.assertEqual(event["payload"]["status"], "purchase_preflight_blocked")
        failed = event["payload"]["portfolio_projection"]["failed_sources"]
        self.assertEqual(failed[0]["code"], "000002")

    def test_dynamic_view_expires_and_is_superseded_by_holdings_change(self):
        self._create()
        stored = self.repository.get_batch(self.batch["id"])
        with (
            patch.object(preflight_service.storage, "list_holdings", return_value=self.initial_holdings),
            patch.object(preflight_service.storage, "get_investment_profile", return_value=self.profile),
        ):
            current = preflight_service.decorate_batch_purchase_preflight(
                self.repository, stored, user_id="user_one", now=NOW
            )
            expired = preflight_service.decorate_batch_purchase_preflight(
                self.repository,
                stored,
                user_id="user_one",
                now=NOW + dt.timedelta(hours=25),
            )
        changed_holdings = [*self.initial_holdings, {
            "asset_type": "fund", "code": "000004", "name": "新增持仓", "amount": 100,
        }]
        with (
            patch.object(preflight_service.storage, "list_holdings", return_value=changed_holdings),
            patch.object(preflight_service.storage, "get_investment_profile", return_value=self.profile),
        ):
            superseded = preflight_service.decorate_batch_purchase_preflight(
                self.repository, stored, user_id="user_one", now=NOW
            )

        self.assertEqual(current["status"], "ready_for_manual_purchase_review")
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(superseded["status"], "superseded")
        self.assertFalse(expired["decision_gate"]["manual_purchase_review_ready"])
        self.assertFalse(superseded["decision_gate"]["execution_authorized"])

    def test_same_platform_facts_can_be_rechecked_from_the_latest_revision(self):
        first, _ = self._create()
        refreshed_request = self._request(previous_hash=first["event_hash"])
        second, created = self._create(refreshed_request)

        self.assertTrue(created)
        self.assertNotEqual(
            second["payload"]["bindings"]["request_sha256"],
            first["payload"]["bindings"]["request_sha256"],
        )
        self.assertEqual(second["previous_hash"], first["event_hash"])


if __name__ == "__main__":
    unittest.main()
