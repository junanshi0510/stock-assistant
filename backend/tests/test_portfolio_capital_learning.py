# -*- coding: utf-8 -*-
"""Capital-plan execution learning must be append-only and decision-useful."""

from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from migrations import portfolio_capital_learning_v1  # noqa: E402
import portfolio_capital_learning_service as service  # noqa: E402
from background_jobs import BackgroundJobRepository  # noqa: E402
from portfolio_capital_learning_repository import (  # noqa: E402
    PortfolioCapitalLearningConflictError,
    PortfolioCapitalLearningRepository,
)
from portfolio_capital_repository import (  # noqa: E402
    PortfolioCapitalRepository,
)


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str], dict] = {}

    def create_job(self, **kwargs):
        key = (kwargs["user_id"], kwargs["idempotency_key"])
        if key in self.jobs:
            return self.jobs[key], False
        item = {
            "id": f"job-{len(self.jobs) + 1}",
            "job_type": kwargs["job_type"],
            "queue_name": kwargs["queue_name"],
            "payload": kwargs["payload"],
        }
        self.jobs[key] = item
        return item, True


def ready_plan(
    repository: PortfolioCapitalRepository,
    *,
    nonce: str = "one",
    amount: float = 1_000,
) -> dict:
    evidence = {
        "schema_version": "portfolio_capital_evidence.v1",
        "engine_version": "whole_portfolio_next_best_action.v4",
        "bindings": {
            "profile_version_id": None,
            "valuation_snapshot_id": None,
            "action_report_id": None,
            "exposure_snapshot_id": None,
        },
        "nonce": nonce,
    }
    result = {
        "schema_version": "portfolio_capital_decision.v1",
        "engine_version": "whole_portfolio_next_best_action.v4",
        "decision_date": "2026-01-02",
        "status": "ready",
        "primary_action": {
            "code": "limited_manual_pilot",
            "headline": "冻结测试计划",
        },
        "capital": {
            "policy_monthly_budget_cny": 10_000,
            "planned_deployment_cny": amount,
        },
        "candidate_actions": [
            {
                "market": "A股",
                "symbol": "600519",
                "name": "贵州茅台",
                "planned_amount_cny": amount,
            }
        ],
        "investment_committee": {
            "market_regime": {
                "status": "risk_on",
                "label": "偏强",
                "risk_budget_multiplier": 1,
            }
        },
        "data_lineage": {"regime_snapshot_id": "regime-1"},
    }
    item, created = repository.create_plan(
        tenant_id="public",
        user_id="owner",
        actor_id="owner",
        engine_version="whole_portfolio_next_best_action.v4",
        status="ready",
        decision_date="2026-01-02",
        evidence=evidence,
        result=result,
    )
    assert created
    return item


def transaction(
    transaction_id: int = 1,
    *,
    code: str = "600519",
    trade_date: str = "2026-01-05",
    unit_price: float = 112.5,
) -> dict:
    return {
        "id": transaction_id,
        "user_id": "owner",
        "asset_type": "stock",
        "market": "A股",
        "code": code,
        "name": "贵州茅台" if code == "600519" else code,
        "trade_type": "buy",
        "trade_date": trade_date,
        "shares": 10,
        "unit_price": unit_price,
        "fee": 5,
        "note": "",
        "source": "manual",
        "created_at": f"{trade_date}T09:30:00",
    }


class PortfolioCapitalLearningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(
            Path(self.tempdir.name) / "capital-learning.sqlite3"
        )
        self.plans = PortfolioCapitalRepository(self.database)
        self.learning = PortfolioCapitalLearningRepository(
            self.database
        )
        self.plan = ready_plan(self.plans)
        self.rows = [transaction()]

    def tearDown(self):
        self.tempdir.cleanup()

    def create_event(
        self,
        *,
        rows: list[dict] | None = None,
        confirmations: list[dict] | None = None,
        previous_hash: str | None = None,
    ):
        active_rows = rows if rows is not None else self.rows
        requested = confirmations or [
            {"transaction_id": 1, "settled_amount_cny": 1_000}
        ]
        return service.create_execution_event(
            self.plan["id"],
            transactions=requested,
            acknowledged=True,
            expected_previous_event_hash=previous_hash,
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: active_rows,
            now=dt.datetime(
                2026, 1, 5, 10, 0, tzinfo=dt.timezone.utc
            ),
        )

    def test_non_ready_plan_does_not_enter_execution_lifecycle(self):
        self.assertEqual(
            service._execution_lifecycle(
                None, plan_status="blocked"
            ),
            "not_applicable",
        )

    def test_real_transaction_is_bound_verified_and_budget_consumed(self):
        event, created = self.create_event()

        self.assertTrue(created)
        self.assertEqual(event["status"], "reconciled")
        self.assertTrue(event["verification"]["verified"])
        self.assertEqual(
            event["result"]["plan_coverage_pct"], 100
        )
        context = service.get_plan_execution_context(
            self.plan["id"],
            tenant_id="public",
            user_id="owner",
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: self.rows,
        )
        self.assertEqual(
            context["lifecycle_status"], "reconciled"
        )
        self.assertTrue(
            context["eligible_transactions"][0][
                "already_bound_to_plan"
            ]
        )
        month = service.monthly_execution_summary(
            tenant_id="public",
            user_id="owner",
            as_of=dt.date(2026, 1, 20),
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: self.rows,
        )
        self.assertEqual(
            month["confirmed_settled_amount_cny"], 1_000
        )
        self.assertIsNone(month["blocking_reason"])

    def test_append_only_event_cannot_remove_or_rewrite_prior_trade(self):
        first, _ = self.create_event()

        with self.assertRaises(
            PortfolioCapitalLearningConflictError
        ):
            self.create_event(
                confirmations=[
                    {
                        "transaction_id": 1,
                        "settled_amount_cny": 900,
                    }
                ],
                previous_hash=first["event_hash"],
            )

        second_row = transaction(
            2, trade_date="2026-01-06", unit_price=113
        )
        second, created = self.create_event(
            rows=[*self.rows, second_row],
            confirmations=[
                {"transaction_id": 1, "settled_amount_cny": 1_000},
                {"transaction_id": 2, "settled_amount_cny": 200},
            ],
            previous_hash=first["event_hash"],
        )
        self.assertTrue(created)
        self.assertEqual(second["event_no"], 2)
        self.assertEqual(
            second["previous_event_hash"], first["event_hash"]
        )
        self.assertTrue(second["verification"]["chain"]["verified"])

        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE portfolio_capital_execution_events
                    SET settled_amount_cny=0 WHERE id=?
                    """,
                    (first["id"],),
                )
        finally:
            connection.close()

    def test_one_transaction_cannot_fund_two_plans(self):
        self.create_event()
        other = ready_plan(self.plans, nonce="two")

        with self.assertRaises(
            PortfolioCapitalLearningConflictError
        ):
            service.create_execution_event(
                other["id"],
                transactions=[
                    {
                        "transaction_id": 1,
                        "settled_amount_cny": 1_000,
                    }
                ],
                acknowledged=True,
                expected_previous_event_hash=None,
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                plan_repo=self.plans,
                learning_repo=self.learning,
                transaction_loader=lambda **_: self.rows,
            )

    def test_deviation_review_clears_gate_without_rewriting_facts(self):
        deviated, _ = self.create_event(
            confirmations=[
                {
                    "transaction_id": 1,
                    "settled_amount_cny": 1_200,
                }
            ]
        )
        self.assertEqual(deviated["status"], "deviated")

        reviewed, created = service.review_execution_deviation(
            self.plan["id"],
            note="已核对券商成交与资金变化，接受本次超出冻结金额的执行偏差。",
            acknowledged=True,
            expected_previous_event_hash=deviated["event_hash"],
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: self.rows,
            now=dt.datetime(
                2026, 1, 6, 10, 0, tzinfo=dt.timezone.utc
            ),
        )

        self.assertTrue(created)
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual(
            reviewed["settled_amount_cny"],
            deviated["settled_amount_cny"],
        )
        self.assertEqual(reviewed["event_no"], 2)
        self.assertTrue(reviewed["verification"]["chain"]["verified"])
        month = service.monthly_execution_summary(
            tenant_id="public",
            user_id="owner",
            as_of=dt.date(2026, 1, 20),
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: self.rows,
        )
        self.assertEqual(
            month["confirmed_settled_amount_cny"], 1_200
        )
        self.assertIsNone(month["blocking_reason"])

    def test_deleted_ledger_row_fails_integrity_without_releasing_budget(self):
        self.create_event()
        context = service.get_plan_execution_context(
            self.plan["id"],
            tenant_id="public",
            user_id="owner",
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: [],
        )

        self.assertEqual(
            context["lifecycle_status"], "integrity_failed"
        )
        self.assertEqual(
            context["execution_verification"][
                "missing_transaction_ids"
            ],
            [1],
        )
        month = service.monthly_execution_summary(
            tenant_id="public",
            user_id="owner",
            as_of=dt.date(2026, 1, 20),
            plan_repo=self.plans,
            learning_repo=self.learning,
            transaction_loader=lambda **_: [],
        )
        self.assertEqual(
            month["confirmed_settled_amount_cny"], 1_000
        )

    def test_exact_horizon_outcome_separates_selection_and_execution(self):
        frame_dates = pd.bdate_range("2025-12-01", periods=180)
        symbol_frame = pd.DataFrame(
            {
                "date": frame_dates,
                "close": [
                    100 + index * 0.5
                    for index in range(len(frame_dates))
                ],
            }
        )
        benchmark_frame = pd.DataFrame(
            {
                "date": frame_dates,
                "close": [
                    100 + index * 0.1
                    for index in range(len(frame_dates))
                ],
            }
        )
        symbol_frame.attrs["source"] = "test-professional-feed"
        benchmark_frame.attrs["source"] = "test-professional-feed"
        actual_entry = float(
            symbol_frame.loc[
                symbol_frame["date"] == pd.Timestamp("2026-01-05"),
                "close",
            ].iloc[0]
        )
        self.rows = [transaction(unit_price=actual_entry)]
        event, _ = self.create_event()

        def history_loader(
            market, symbol, months, *, fetch_months=None
        ):
            return (
                benchmark_frame.copy()
                if symbol == "510300"
                else symbol_frame.copy()
            )

        outcome, created = service.refresh_plan_outcome(
            self.plan["id"],
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            execution_event_id=event["id"],
            plan_repo=self.plans,
            learning_repo=self.learning,
            history_loader=history_loader,
            now=dt.datetime(
                2026, 7, 20, 8, 0, tzinfo=dt.timezone.utc
            ),
        )

        self.assertTrue(created)
        self.assertEqual(outcome["status"], "complete")
        self.assertTrue(outcome["integrity"]["verified"])
        horizons = outcome["result"]["horizons"]
        self.assertEqual(
            [item["trading_days"] for item in horizons],
            [5, 20, 60],
        )
        self.assertTrue(
            all(item["status"] == "complete" for item in horizons)
        )
        self.assertTrue(
            all(item["implementation_gap_pct"] is not None for item in horizons)
        )
        scorecard = service.build_learning_scorecard(
            tenant_id="public",
            user_id="owner",
            learning_repo=self.learning,
        )
        self.assertEqual(scorecard["status"], "collecting")
        self.assertEqual(
            next(
                item
                for item in scorecard["horizons"]
                if item["trading_days"] == 20
            )["mature_plan_count"],
            1,
        )

    def test_scheduler_dispatch_is_daily_idempotent(self):
        self.create_event()
        jobs = FakeJobRepository()
        dispatched = []

        first = service.dispatch_due_outcomes(
            now=dt.datetime(
                2026, 1, 7, 8, 0, tzinfo=dt.timezone.utc
            ),
            learning_repo=self.learning,
            jobs=jobs,
            enqueue=lambda job, _repo: dispatched.append(
                job["id"]
            )
            or job["id"],
        )
        second = service.dispatch_due_outcomes(
            now=dt.datetime(
                2026, 1, 7, 12, 0, tzinfo=dt.timezone.utc
            ),
            learning_repo=self.learning,
            jobs=jobs,
            enqueue=lambda job, _repo: job["id"],
        )

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["deduplicated"], 1)
        self.assertEqual(dispatched, ["job-1"])
        payload = next(iter(jobs.jobs.values()))["payload"]
        self.assertEqual(
            payload["operation"], "portfolio.capital_outcome"
        )

    def test_manual_outcome_refresh_is_durable_non_blocking_and_scoped(self):
        event, _ = self.create_event()
        jobs = BackgroundJobRepository(self.database)
        embedded = []

        with patch.object(
            service, "uses_celery_queue", return_value=False
        ):
            accepted = service.queue_plan_outcome_refresh(
                self.plan["id"],
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                plan_repo=self.plans,
                learning_repo=self.learning,
                transaction_loader=lambda **_: self.rows,
                jobs=jobs,
                embedded_dispatch=lambda job_id, target: embedded.append(
                    (job_id, target)
                ),
            )

        self.assertEqual(accepted["status"], "queued")
        self.assertEqual(accepted["dispatch_state"], "embedded")
        self.assertEqual(
            accepted["execution_event_id"], event["id"]
        )
        self.assertEqual(
            embedded, [(accepted["job_id"], self.database)]
        )

        queued = service.get_plan_outcome_refresh_job(
            accepted["job_id"],
            tenant_id="public",
            user_id="owner",
            jobs=jobs,
        )
        self.assertEqual(queued["status"], "queued")
        self.assertTrue(queued["audit"]["verified"])
        with self.assertRaises(
            service.PortfolioCapitalOutcomeJobNotFoundError
        ):
            service.get_plan_outcome_refresh_job(
                accepted["job_id"],
                tenant_id="public",
                user_id="other",
                jobs=jobs,
            )

        with patch(
            "market_data_operations.execute_operation",
            return_value={"created": True, "outcome_id": "outcome-1"},
        ):
            completed = service.execute_embedded_plan_outcome_job(
                accepted["job_id"],
                self.database,
            )
        self.assertEqual(completed["status"], "succeeded")
        finished = service.get_plan_outcome_refresh_job(
            accepted["job_id"],
            tenant_id="public",
            user_id="owner",
            jobs=jobs,
        )
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(
            finished["result"]["outcome_id"], "outcome-1"
        )
        self.assertTrue(finished["audit"]["verified"])

    def test_postgres_migration_is_versioned_and_immutable(self):
        ddl = portfolio_capital_learning_v1.POSTGRES_DDL
        guards = portfolio_capital_learning_v1.POSTGRES_GUARDS
        source = inspect.getsource(
            portfolio_capital_learning_v1
            .install_portfolio_capital_learning_schema
        )

        self.assertEqual(
            portfolio_capital_learning_v1.MIGRATION_ID,
            "portfolio-capital-learning.v1",
        )
        for table in (
            "portfolio_capital_execution_events",
            "portfolio_capital_transaction_bindings",
            "portfolio_capital_outcome_snapshots",
        ):
            self.assertIn(
                f"CREATE TABLE IF NOT EXISTS {table}", ddl
            )
            self.assertIn(
                table,
                portfolio_capital_learning_v1.IMMUTABLE_TABLES,
            )
        self.assertIn("stock_assistant_reject_mutation", guards)
        self.assertIn("'reviewed'", ddl)
        self.assertIn("platform_schema_migrations", source)


if __name__ == "__main__":
    unittest.main()
