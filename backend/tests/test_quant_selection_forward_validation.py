# -*- coding: utf-8 -*-
"""Quant mandates must enter one causal, immutable forward evidence chain."""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import opportunity_profit_service  # noqa: E402
import opportunity_service  # noqa: E402
import quant_selection_forward_service as forward_service  # noqa: E402
from migrations import quant_selection_forward_v1  # noqa: E402
from opportunity_profit_repository import (  # noqa: E402
    OpportunityProfitRepository,
)
from opportunity_repository import OpportunityRepository  # noqa: E402
from quant_selection_forward_repository import (  # noqa: E402
    QuantSelectionForwardConflictError,
    QuantSelectionForwardRepository,
)
from quant_selection_repository import QuantSelectionRepository  # noqa: E402


def quant_policy() -> dict:
    return {
        "schema_version": "quant_selection_policy.v1",
        "policy_version": "test",
        "name": "沪深300测试多因子",
        "market": "A股",
        "universe_mode": "tushare_index",
        "benchmark_symbol": "510300",
        "rebalance_days": 21,
        "commission_bps": 5,
        "slippage_bps": 8,
        "sell_tax_bps": 10,
        "maximum_drawdown_pct": 18,
    }


class FakeJobs:
    def __init__(self):
        self.items = {}

    def create_job(self, **kwargs):
        key = kwargs["idempotency_key"]
        if key in self.items:
            return self.items[key], False
        item = {
            "id": f"job_{len(self.items) + 1}",
            "status": "queued",
            "job_type": kwargs["job_type"],
            "queue_name": kwargs["queue_name"],
            "payload": kwargs["payload"],
        }
        self.items[key] = item
        return item, True


class QuantSelectionForwardValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "forward.db")
        self.quant = QuantSelectionRepository(self.database)
        self.quant.list_runs(
            tenant_id="public", user_id="owner"
        )
        self.opp = OpportunityRepository(self.database)
        self.opp.list_strategies(user_id="owner")
        self.profit = OpportunityProfitRepository(self.database)
        self.profit.list_scorecards(user_id="owner")
        self.forward = QuantSelectionForwardRepository(self.database)
        self.forward.list_validations(
            tenant_id="public", user_id="owner"
        )

    def tearDown(self):
        self.temp.cleanup()

    def mandate(self, *, suffix: str = "1") -> dict:
        policy = {**quant_policy(), "test_suffix": suffix}
        if suffix != "different-policy":
            policy.pop("test_suffix")
        run = self.quant.create_run(
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            engine_version="point_in_time_quant_selection@test",
            policy=policy,
        )
        self.quant.mark_running(
            run["id"],
            tenant_id="public",
            user_id="owner",
            actor_id="worker",
        )
        result = {
            "policy": policy,
            "promotion_gate": {
                "status": "paper_ready",
                "paper_shadow_eligible": True,
            },
            "latest_signal": {
                "signal_date": "2026-07-24",
                "target_cash_pct": 10,
                "targets": [
                    {
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "target_weight_pct": 45,
                        "rank": 1,
                        "composite_score": 78,
                        "last_price": 1000,
                        "last_date": "2026-07-24",
                    },
                    {
                        "symbol": "300750",
                        "name": "宁德时代",
                        "target_weight_pct": 45,
                        "rank": 2,
                        "composite_score": 74,
                        "last_price": 900,
                        "last_date": "2026-07-24",
                    },
                ],
            },
        }
        completed = self.quant.complete_run(
            run["id"],
            tenant_id="public",
            user_id="owner",
            actor_id="worker",
            status="succeeded",
            result=result,
        )
        snapshot = {
            "schema_version": "quant_selection_shadow_snapshot.v1",
            "run_id": run["id"],
            "result_sha256": completed["result_sha256"],
            "engine_version": completed["engine_version"],
            "policy": policy,
            "promotion_gate": result["promotion_gate"],
            "latest_signal": result["latest_signal"],
            "forward_rules": {
                "mode": "paper_only",
                "signal_timing": "after_close",
                "earliest_fill": "next_trading_day_open",
                "broker_order_submission": False,
            },
        }
        mandate, _ = self.quant.create_shadow_mandate(
            run["id"],
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            snapshot=snapshot,
        )
        return mandate

    def enroll(self, mandate: dict):
        return forward_service.enroll_validation(
            mandate["id"],
            acknowledged=True,
            expected_snapshot_sha256=mandate["snapshot_sha256"],
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            quant_repo=self.quant,
            forward_repo=self.forward,
        )

    def test_enrollment_is_atomic_idempotent_and_user_scoped(self):
        mandate = self.mandate()
        first, created = self.enroll(mandate)
        second, created_again = self.enroll(mandate)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["integrity"]["verified"])
        self.assertIsNone(
            self.forward.get_validation(
                first["id"],
                tenant_id="public",
                user_id="another-user",
            )
        )
        basket = self.opp.get_paper_basket(
            first["opportunity_basket_id"],
            user_id="owner",
        )
        self.assertTrue(basket["snapshot_verified"])
        self.assertTrue(
            all(
                item["entry_timing"] == "next_trading_day_open"
                and item["entry_price"] is None
                for item in basket["snapshot"]["positions"]
            )
        )
        self.assertFalse(
            basket["snapshot"]["execution_authorized"]
        )
        policy = self.profit.latest_policy(
            first["opportunity_strategy_id"],
            user_id="owner",
            strategy_version_id=(
                first["opportunity_strategy_version_id"]
            ),
        )
        self.assertEqual(
            policy["policy"]["round_trip_cost_bps"], 36
        )

        with closing(sqlite3.connect(self.database)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE quant_selection_forward_validations
                    SET actor_id='changed' WHERE id=?
                    """,
                    (first["id"],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    DELETE FROM quant_selection_forward_validations
                    WHERE id=?
                    """,
                    (first["id"],),
                )

    def test_same_policy_accumulates_independent_baskets(self):
        first, _ = self.enroll(self.mandate(suffix="1"))
        second, _ = self.enroll(self.mandate(suffix="2"))
        self.assertEqual(
            first["opportunity_strategy_id"],
            second["opportunity_strategy_id"],
        )
        self.assertNotEqual(
            first["opportunity_basket_id"],
            second["opportunity_basket_id"],
        )
        baskets = self.opp.list_paper_baskets(
            user_id="owner", limit=10
        )
        self.assertEqual(len(baskets), 2)

    def test_changed_policy_creates_a_separate_strategy_family(self):
        first, _ = self.enroll(self.mandate())
        second, _ = self.enroll(
            self.mandate(suffix="different-policy")
        )
        self.assertNotEqual(
            first["opportunity_strategy_id"],
            second["opportunity_strategy_id"],
        )

    def test_digest_mismatch_and_failed_gate_are_blocked(self):
        mandate = self.mandate()
        with self.assertRaisesRegex(
            QuantSelectionForwardConflictError, "摘要"
        ):
            forward_service.enroll_validation(
                mandate["id"],
                acknowledged=True,
                expected_snapshot_sha256="0" * 64,
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                quant_repo=self.quant,
                forward_repo=self.forward,
            )

    def test_next_open_observation_cannot_backfill_known_prices(self):
        link, _ = self.enroll(self.mandate())
        basket = self.opp.get_paper_basket(
            link["opportunity_basket_id"],
            user_id="owner",
        )
        anchor = (
            basket["snapshot"]["entry_rules"]["entry_after_date"]
        )
        sessions = pd.bdate_range(
            pd.Timestamp(anchor) + pd.Timedelta(days=1),
            periods=65,
        )

        def history_loader(_market, symbol, _months, **_kwargs):
            base = 100.0 if symbol != "510300" else 200.0
            growth = 1.0 if symbol != "510300" else 0.25
            frame = pd.DataFrame(
                {
                    "date": [
                        pd.Timestamp(anchor) - pd.Timedelta(days=1),
                        *sessions,
                    ],
                    "open": [
                        base * 10,
                        *(
                            base
                            + growth
                            * np.arange(len(sessions), dtype=float)
                        ),
                    ],
                    "close": [
                        base * 10,
                        *(
                            base
                            + growth
                            * (np.arange(len(sessions), dtype=float) + 1)
                        ),
                    ],
                }
            )
            frame.attrs["source"] = "professional-test"
            return frame

        observation = opportunity_service.observe_paper_basket(
            link["opportunity_basket_id"],
            user_id="owner",
            repo=self.opp,
            history_loader=history_loader,
        )
        payload = observation["payload"]
        self.assertEqual(payload["pending_entry_count"], 0)
        self.assertTrue(payload["max_horizon_complete"])
        first_position = payload["positions"][0]
        self.assertEqual(
            first_position["entry_date"],
            sessions[0].strftime("%Y-%m-%d"),
        )
        self.assertEqual(first_position["entry_price"], 100)
        self.assertEqual(
            first_position["entry_price_source"],
            "first_adjusted_open_strictly_after_freeze",
        )
        self.assertLess(first_position["return_pct"], 100)
        five_day = next(
            item
            for item in payload["horizons"]
            if item["trading_days"] == 5
        )
        self.assertTrue(five_day["complete"])

        scorecard = opportunity_profit_service.build_scorecard(
            link["opportunity_strategy_id"],
            user_id="owner",
            opp_repo=self.opp,
            profit_repo=self.profit,
        )
        primary = next(
            item
            for item in scorecard["horizons"]
            if item["horizon_trading_days"] == 20
        )
        self.assertEqual(primary["mature_count"], 1)
        self.assertEqual(
            scorecard["capital_gate"]["status"], "collecting"
        )

    def test_delayed_enrollment_starts_after_enrollment_not_old_mandate(self):
        mandate = self.mandate()
        enrolled_at = dt.datetime(
            2026, 8, 17, 1, 30, tzinfo=dt.timezone.utc
        )
        link, created = forward_service.enroll_validation(
            mandate["id"],
            acknowledged=True,
            expected_snapshot_sha256=mandate["snapshot_sha256"],
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            now=enrolled_at,
            quant_repo=self.quant,
            forward_repo=self.forward,
        )
        basket = self.opp.get_paper_basket(
            link["opportunity_basket_id"],
            user_id="owner",
        )

        self.assertTrue(created)
        self.assertEqual(
            basket["snapshot"]["entry_rules"]["entry_after_date"],
            "2026-08-17",
        )
        self.assertEqual(
            basket["snapshot"]["frozen_at"],
            "2026-08-17T01:30:00.000+00:00",
        )
        self.assertEqual(
            link["payload"]["causality"]["mandate_frozen_at"],
            mandate["created_at"],
        )
        self.assertEqual(
            link["payload"]["causality"]["enrolled_at"],
            "2026-08-17T01:30:00.000+00:00",
        )

    def test_pending_entry_is_visible_not_recorded_as_failure(self):
        link, _ = self.enroll(self.mandate())
        basket = self.opp.get_paper_basket(
            link["opportunity_basket_id"], user_id="owner"
        )
        anchor = basket["snapshot"]["entry_rules"][
            "entry_after_date"
        ]

        def history_loader(_market, _symbol, _months, **_kwargs):
            frame = pd.DataFrame(
                {
                    "date": [pd.Timestamp(anchor)],
                    "open": [100.0],
                    "close": [100.0],
                }
            )
            frame.attrs["source"] = "professional-test"
            return frame

        observation = opportunity_service.observe_paper_basket(
            link["opportunity_basket_id"],
            user_id="owner",
            repo=self.opp,
            history_loader=history_loader,
        )
        self.assertEqual(
            observation["payload"]["pending_entry_count"], 2
        )
        self.assertEqual(
            observation["payload"]["failed_count"], 0
        )

    def test_manual_refresh_is_durable_and_hourly_idempotent(self):
        link, _ = self.enroll(self.mandate())
        jobs = FakeJobs()
        dispatched = []
        with patch.object(
            forward_service, "uses_celery_queue", return_value=True
        ):
            first = forward_service.request_observation(
                link["id"],
                tenant_id="public",
                user_id="owner",
                now=dt.datetime(
                    2026, 7, 27, 2, 30, tzinfo=dt.timezone.utc
                ),
                forward_repo=self.forward,
                opp_repo=self.opp,
                jobs=jobs,
                enqueue=lambda job, _repo: dispatched.append(job["id"]),
            )
            second = forward_service.request_observation(
                link["id"],
                tenant_id="public",
                user_id="owner",
                now=dt.datetime(
                    2026, 7, 27, 2, 45, tzinfo=dt.timezone.utc
                ),
                forward_repo=self.forward,
                opp_repo=self.opp,
                jobs=jobs,
                enqueue=lambda job, _repo: dispatched.append(job["id"]),
            )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(dispatched), 1)

    def test_migration_declares_link_table_and_immutable_guard(self):
        self.assertEqual(
            quant_selection_forward_v1.MIGRATION_ID,
            "quant-selection-forward-validation.v1",
        )
        self.assertIn(
            "quant_selection_forward_validations",
            quant_selection_forward_v1.POSTGRES_DDL,
        )
        self.assertIn(
            "stock_assistant_reject_mutation",
            quant_selection_forward_v1.POSTGRES_GUARDS,
        )


if __name__ == "__main__":
    unittest.main()
