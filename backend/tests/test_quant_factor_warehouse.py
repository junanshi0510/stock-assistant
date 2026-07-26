# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from migrations import quant_factor_warehouse_v1  # noqa: E402
from quant_factor_repository import QuantFactorRepository  # noqa: E402
import quant_factor_warehouse_service as service  # noqa: E402
import quant_selection_service  # noqa: E402


class FakeTushare:
    def __init__(self, daily: pd.DataFrame | None = None):
        self.daily = daily if daily is not None else pd.DataFrame()
        self.daily_calls = []

    def daily_basic(self, **kwargs):
        self.daily_calls.append(kwargs)
        return self.daily.copy()

    def fina_indicator(self, **_kwargs):
        return pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "ann_date": "20240403",
                    "end_date": "20231231",
                    "roe": 31.2,
                    "grossprofit_margin": 91.4,
                    "ocf_to_or": 38.8,
                    "debt_to_assets": 20.1,
                    "update_flag": "0",
                }
            ]
        )


def daily_frame(
    trade_date: str = "20250102",
    *,
    conflict: bool = False,
) -> pd.DataFrame:
    rows = []
    for index, symbol in enumerate(
        ("600519.SH", "300750.SZ", "601318.SH", "600036.SH")
    ):
        rows.append(
            {
                "ts_code": symbol,
                "trade_date": trade_date,
                "close": 10 + index,
                "turnover_rate_f": 1.1 + index,
                "pe_ttm": 11 + index,
                "pb": 1.2 + index / 10,
                "dv_ttm": 2.0,
                "total_mv": 100_000 + index,
                "circ_mv": 90_000 + index,
            }
        )
    if conflict:
        rows.append({**rows[0], "pe_ttm": 99.0})
    return pd.DataFrame(rows)


class QuantFactorWarehouseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = str(
            Path(self.temporary.name) / "factor-warehouse.sqlite3"
        )
        self.repository = QuantFactorRepository(self.database_path)

    def tearDown(self):
        self.temporary.cleanup()

    def _daily_run(self, target: str = "2025-01-02"):
        run, created = self.repository.create_sync_run(
            {
                "schema_version": "quant_factor_sync_request.v1",
                "dataset": "valuation_daily",
                "provider": "tushare",
                "mode": "historical_backfill",
                "plan_id": None,
                "target_date": target,
                "target_symbol": None,
                "period_start": None,
                "period_end": None,
            },
            actor_id="admin-1",
        )
        self.assertTrue(created)
        return run

    def test_daily_sync_is_content_addressed_and_replayable(self):
        run = self._daily_run()
        fake = FakeTushare(daily_frame())
        completed = service.execute_sync_run(
            run["id"],
            actor_id="worker-1",
            repo=self.repository,
            pro=fake,
        )

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["stats"]["inserted_rows"], 4)
        self.assertEqual(completed["stats"]["malformed_rows"], 0)
        self.assertTrue(completed["integrity"]["event_chain_verified"])
        self.assertEqual(
            fake.daily_calls[0]["trade_date"],
            "20250102",
        )
        stats = self.repository.dataset_stats()["valuation_daily"]
        self.assertEqual(stats["row_count"], 4)
        self.assertEqual(stats["symbol_count"], 4)
        self.assertEqual(stats["conflict_key_count"], 0)

        inputs, evidence = service.load_point_in_time_fundamentals(
            ["600519", "300750", "601318", "600036"],
            history_months=36,
            required_factors={"value"},
            end_date=dt.date(2025, 1, 2),
            repo=self.repository,
        )
        self.assertEqual(len(inputs), 4)
        self.assertTrue(evidence["point_in_time_verified"])
        self.assertEqual(evidence["research_provider_call_count"], 0)
        self.assertEqual(len(evidence["snapshot_sha256"]), 64)

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE quant_factor_daily_observations
                    SET pe_ttm=1 WHERE symbol='600519'
                    """
                )
        finally:
            connection.close()

    def test_conflicting_same_day_provider_rows_are_excluded(self):
        run = self._daily_run()
        completed = service.execute_sync_run(
            run["id"],
            actor_id="worker-1",
            repo=self.repository,
            pro=FakeTushare(daily_frame(conflict=True)),
        )
        self.assertEqual(completed["status"], "partial")
        self.assertEqual(completed["stats"]["conflict_keys"], 1)

        inputs, evidence = service.load_point_in_time_fundamentals(
            ["600519", "300750", "601318", "600036"],
            history_months=36,
            required_factors={"value"},
            end_date=dt.date(2025, 1, 2),
            repo=self.repository,
        )
        self.assertNotIn("600519", inputs)
        self.assertEqual(len(inputs), 3)
        self.assertFalse(evidence["point_in_time_verified"])
        self.assertEqual(evidence["ambiguous_revision_count"], 1)

    def test_financial_rows_use_announcement_date(self):
        run, _ = self.repository.create_sync_run(
            {
                "schema_version": "quant_factor_sync_request.v1",
                "dataset": "financial_indicator",
                "provider": "tushare",
                "mode": "historical_backfill",
                "plan_id": None,
                "target_date": None,
                "target_symbol": "600519",
                "period_start": "2023-01-01",
                "period_end": "2024-12-31",
            },
            actor_id="admin-1",
        )
        completed = service.execute_sync_run(
            run["id"],
            actor_id="worker-1",
            repo=self.repository,
            pro=FakeTushare(),
        )
        self.assertEqual(completed["stats"]["inserted_rows"], 1)
        rows = self.repository.load_financial_rows(
            ["600519"],
            start_date="2023-01-01",
            end_date="2025-01-01",
        )
        self.assertEqual(rows[0]["announcement_date"], "2024-04-03")
        self.assertEqual(rows[0]["report_end_date"], "2023-12-31")

    def test_plan_lifecycle_and_progress_are_durable(self):
        result = service.create_backfill_plan(
            {
                "dataset": "valuation_daily",
                "start_date": "2025-01-01",
                "end_date": "2025-01-03",
            },
            actor_id="admin-1",
            repo=self.repository,
            auto_dispatch=False,
        )
        plan = result["item"]
        self.assertEqual(plan["progress"]["target_count"], 3)
        self.assertEqual(plan["progress"]["pending_target_count"], 3)

        paused = service.transition_backfill_plan(
            plan["id"],
            "pause",
            repo=self.repository,
        )
        self.assertEqual(paused["status"], "paused")
        resumed = service.transition_backfill_plan(
            plan["id"],
            "resume",
            repo=self.repository,
        )
        self.assertEqual(resumed["status"], "active")
        cancelled = service.transition_backfill_plan(
            plan["id"],
            "cancel",
            repo=self.repository,
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(Exception):
            service.transition_backfill_plan(
                plan["id"],
                "resume",
                repo=self.repository,
            )

    def test_plan_creation_survives_initial_queue_outage(self):
        with patch.object(
            service,
            "schedule_due_sync",
            side_effect=RuntimeError("redis unavailable"),
        ):
            result = service.create_backfill_plan(
                {
                    "dataset": "valuation_daily",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-03",
                },
                actor_id="admin-1",
                repo=self.repository,
            )
        self.assertEqual(
            result["dispatch"]["status"],
            "initial_dispatch_failed",
        )
        persisted = self.repository.get_plan(result["item"]["id"])
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["status"], "active")

    def test_scheduler_dispatches_only_a_durable_run_identifier(self):
        with (
            patch.object(service.config, "TUSHARE_TOKEN", "configured"),
            patch.object(
                service,
                "_dispatch_sync_run",
                return_value={
                    "dispatched": True,
                    "mode": "celery",
                    "task_id": "task-1",
                },
            ) as dispatch,
        ):
            result = service.schedule_due_sync(
                actor_id="scheduler",
                repo=self.repository,
                now=dt.datetime(
                    2025,
                    1,
                    6,
                    20,
                    tzinfo=service.CHINA_TIMEZONE,
                ),
            )
        self.assertEqual(result["status"], "dispatched")
        dispatched_run = dispatch.call_args.args[0]
        self.assertEqual(set(dispatched_run["request"]) >= {
            "dataset",
            "target_date",
        }, True)
        self.assertNotIn("TUSHARE_TOKEN", repr(dispatch.call_args))

    def test_scheduler_redispatches_stale_queued_run(self):
        run = self._daily_run()
        future = dt.datetime.now(
            dt.timezone.utc
        ) + dt.timedelta(seconds=61)
        with (
            patch.object(service.config, "TUSHARE_TOKEN", "configured"),
            patch.dict(
                os.environ,
                {"QUANT_FACTOR_QUEUE_REDISPATCH_SECONDS": "60"},
            ),
            patch.object(
                service,
                "_dispatch_sync_run",
                return_value={
                    "dispatched": True,
                    "mode": "celery",
                    "task_id": "task-recovered",
                },
            ) as dispatch,
        ):
            result = service.schedule_due_sync(
                actor_id="scheduler",
                repo=self.repository,
                now=future,
            )
        self.assertEqual(result["status"], "redispatched_queued")
        self.assertEqual(result["run_id"], run["id"])
        self.assertEqual(dispatch.call_args.args[0]["id"], run["id"])

    def test_provider_failure_is_redacted_before_persistence(self):
        class FailingTushare:
            def daily_basic(self, **_kwargs):
                raise RuntimeError(
                    "api_key=top-secret token:another-secret unavailable"
                )

        run = self._daily_run()
        with self.assertRaises(RuntimeError) as raised:
            service.execute_sync_run(
                run["id"],
                actor_id="worker-1",
                repo=self.repository,
                pro=FailingTushare(),
            )
        item = self.repository.get_sync_run(run["id"])
        self.assertEqual(item["status"], "failed")
        self.assertNotIn("top-secret", item["error_message"])
        self.assertNotIn("another-secret", item["error_message"])
        self.assertNotIn("top-secret", str(raised.exception))

    def test_expired_worker_lease_is_recovered_and_hash_chained(self):
        run = self._daily_run()
        with patch.dict(
            os.environ,
            {"QUANT_FACTOR_SYNC_LEASE_SECONDS": "360"},
        ):
            claimed = self.repository.claim_sync_run(
                run["id"],
                actor_id="worker-lost",
            )
        lease = dt.datetime.fromisoformat(claimed["lease_expires_at"])
        recovered = self.repository.recover_stale_syncs(
            actor_id="scheduler",
            now=lease + dt.timedelta(seconds=1),
        )
        self.assertEqual(recovered, [run["id"]])
        item = self.repository.get_sync_run(run["id"])
        self.assertEqual(item["status"], "failed")
        self.assertEqual(
            item["error_code"],
            "QUANT_FACTOR_WORKER_LEASE_EXPIRED",
        )
        self.assertTrue(item["integrity"]["event_chain_verified"])

    def test_expired_worker_cannot_persist_late_provider_response(self):
        run = self._daily_run()
        with patch.dict(
            os.environ,
            {"QUANT_FACTOR_SYNC_LEASE_SECONDS": "360"},
        ):
            claimed = self.repository.claim_sync_run(
                run["id"],
                actor_id="worker-lost",
            )
        lease = dt.datetime.fromisoformat(claimed["lease_expires_at"])
        self.repository.recover_stale_syncs(
            actor_id="scheduler",
            now=lease + dt.timedelta(seconds=1),
        )

        with self.assertRaises(Exception):
            self.repository.save_daily_observations(
                run["id"],
                [
                    {
                        "symbol": "600519",
                        "ts_code": "600519.SH",
                        "trade_date": "2025-01-02",
                        "pe_ttm": 20,
                        "pb": 5,
                        "payload": {
                            "ts_code": "600519.SH",
                            "trade_date": "20250102",
                            "pe_ttm": 20,
                            "pb": 5,
                        },
                    }
                ],
                expected_attempt=int(claimed["attempt_count"]),
                provider="tushare",
                capture_mode="historical_backfill",
                retrieved_at="2025-01-02T10:00:00.000+00:00",
            )
        self.assertEqual(
            self.repository.dataset_stats()["valuation_daily"][
                "row_count"
            ],
            0,
        )

    def test_policy_defaults_to_warehouse_only(self):
        policy = quant_selection_service.normalize_policy(
            {
                "market": "A股",
                "universe_mode": "frozen_symbols",
                "symbols": [
                    {"symbol": symbol, "name": symbol}
                    for symbol in (
                        "600519",
                        "300750",
                        "601318",
                        "600036",
                        "000858",
                        "000333",
                    )
                ],
                "factor_weights": {
                    "momentum": 0,
                    "trend_quality": 0,
                    "low_volatility": 0,
                    "liquidity": 0,
                    "fundamental_quality": 0,
                    "value": 100,
                },
            }
        )
        self.assertEqual(policy["factor_data_mode"], "warehouse_only")

    def test_postgres_migration_declares_all_warehouse_tables(self):
        ddl = quant_factor_warehouse_v1.POSTGRES_DDL
        for table in (
            "quant_factor_backfill_plans",
            "quant_factor_sync_runs",
            "quant_factor_sync_events",
            "quant_factor_daily_observations",
            "quant_factor_financial_observations",
        ):
            self.assertIn(table, ddl)


if __name__ == "__main__":
    unittest.main()
