# -*- coding: utf-8 -*-
"""Market regime hub must be point-in-time, conservative, and auditable."""

from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import opportunity_regime_service as service  # noqa: E402
from migrations import opportunity_regime_v1  # noqa: E402
from opportunity_regime_repository import (  # noqa: E402
    OpportunityRegimeRepository,
)


NOW = dt.datetime(2026, 7, 23, 9, 0, tzinfo=dt.timezone.utc)


def run_record(
    run_id: str,
    *,
    status: str = "risk_on",
    market: str = "A股",
    observed_at: dt.datetime = NOW,
    annual_vol: float = 22,
    strategy_version_id: str | None = None,
) -> dict:
    return {
        "id": run_id,
        "status": "succeeded",
        "result_verified": True,
        "result_sha256": (run_id[-1:] or "a") * 64,
        "strategy_id": f"strategy_{run_id}",
        "strategy_version_id": (
            strategy_version_id or f"version_{run_id}"
        ),
        "result": {
            "schema_version": "opportunity_run_result.v1",
            "generated_at": observed_at.isoformat(),
            "strategy": {
                "id": f"strategy_{run_id}",
                "version_id": (
                    strategy_version_id or f"version_{run_id}"
                ),
            },
            "market_regimes": [
                {
                    "market": market,
                    "status": status,
                    "label": service.REGIME_LABEL[status],
                    "sample_count": 10,
                    "median_return_3m": (
                        10 if status == "risk_on" else -8
                    ),
                    "positive_breadth_pct": (
                        75 if status == "risk_on" else 30
                    ),
                    "median_annual_vol": annual_vol,
                }
            ],
        },
    }


def basket(
    index: int,
    *,
    regime: str,
    source_in_snapshot: bool = True,
) -> dict:
    snapshot = {
        "run_id": f"run_basket_{index}",
        "run_result_sha256": "f" * 64,
        "strategy": {"id": "alpha", "version_id": "alpha_v1"},
        "frozen_at": (
            dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
            + dt.timedelta(days=index * 35)
        ).isoformat(),
        "positions": [
            {
                "market": "A股",
                "symbol": "600519",
                "weight_pct": 100,
            }
        ],
    }
    if source_in_snapshot:
        snapshot["market_regimes"] = [
            {
                "market": "A股",
                "status": regime,
                "label": service.REGIME_LABEL[regime],
                "sample_count": 10,
                "median_return_3m": (
                    9 if regime == "risk_on" else -7
                ),
                "positive_breadth_pct": (
                    70 if regime == "risk_on" else 32
                ),
                "median_annual_vol": 24,
            }
        ]
    return {
        "id": f"basket_{index}",
        "run_id": f"run_basket_{index}",
        "snapshot_verified": True,
        "snapshot_sha256": str(index).zfill(64),
        "snapshot": snapshot,
    }


def strategy_row(values: list[float]) -> dict:
    return {
        "strategy_id": "alpha",
        "strategy_name": "质量动量",
        "strategy_version_id": "alpha_v1",
        "scorecard_id": "scorecard_alpha",
        "scorecard_sha256": "b" * 64,
        "evidence_cutoff_at": NOW.isoformat(),
        "basket_id": "basket_current",
        "primary_cohorts": [
            {
                "basket_id": f"basket_{index}",
                "run_id": f"run_basket_{index}",
                "frozen_at": (
                    dt.datetime(
                        2025, 1, 1, tzinfo=dt.timezone.utc
                    )
                    + dt.timedelta(days=index * 35)
                ).isoformat(),
                "outcome_date_max": (
                    dt.date(2025, 2, 1)
                    + dt.timedelta(days=index * 35)
                ).isoformat(),
                "net_excess_return_pct": value,
            }
            for index, value in enumerate(values)
        ],
        "live_capital_plan": {
            "positions": [
                {
                    "market": "A股",
                    "symbol": "600519",
                    "source_weight_pct": 100,
                }
            ]
        },
    }


class OpportunityRegimeTests(unittest.TestCase):
    def test_consensus_and_volatility_only_reduce_risk(self):
        states, observations = service.build_market_states(
            [
                run_record("run_a", annual_vol=42),
                run_record("run_b", annual_vol=42),
                run_record(
                    "run_c",
                    status="defensive",
                    market="港股",
                    annual_vol=55,
                ),
                run_record(
                    "run_d",
                    status="defensive",
                    market="港股",
                    annual_vol=55,
                ),
            ],
            now=NOW,
        )
        by_market = {item["market"]: item for item in states}

        self.assertEqual(by_market["A股"]["status"], "risk_on")
        self.assertEqual(
            by_market["A股"]["risk_budget_multiplier"], 0.75
        )
        self.assertEqual(by_market["港股"]["status"], "defensive")
        self.assertEqual(
            by_market["港股"]["risk_budget_multiplier"], 0.60
        )
        self.assertEqual(by_market["美股"]["status"], "insufficient")
        self.assertTrue(
            all(
                item["risk_budget_multiplier"] <= 1
                for item in states
            )
        )
        self.assertEqual(len(observations), 4)

    def test_stale_sources_do_not_masquerade_as_current_state(self):
        old = NOW - dt.timedelta(days=15)
        states, observations = service.build_market_states(
            [run_record("run_old", observed_at=old)],
            now=NOW,
        )
        mainland = next(
            item for item in states if item["market"] == "A股"
        )
        self.assertEqual(mainland["status"], "insufficient")
        self.assertEqual(mainland["source_count"], 0)
        self.assertEqual(mainland["stale_source_count"], 1)
        self.assertEqual(observations, [])

    def test_same_day_reads_keep_identical_evidence_hash(self):
        runs = [run_record("run_a"), run_record("run_b")]
        first, first_evidence = service.compose_regime_hub(
            runs,
            [],
            now=NOW,
        )
        second, second_evidence = service.compose_regime_hub(
            runs,
            [],
            now=NOW + dt.timedelta(hours=9, minutes=42),
        )

        self.assertEqual(first_evidence, second_evidence)
        self.assertEqual(
            first["evidence_sha256"],
            second["evidence_sha256"],
        )

    def test_same_regime_forward_cohorts_can_prefer_with_shrinkage(self):
        values = [1.0, 1.2, 0.9, 1.1, 1.3, 0.8, 1.05, 1.15]
        result, evidence = service.compose_regime_hub(
            [run_record("run_a"), run_record("run_b")],
            [strategy_row(values)],
            baskets=[
                basket(index, regime="risk_on")
                for index in range(len(values))
            ],
            now=NOW,
        )
        fit = result["strategy_fits"][0]

        self.assertEqual(fit["fit_status"], "preferred")
        self.assertEqual(fit["matched_cohort_count"], 8)
        self.assertEqual(fit["allocation_tilt"], 1.1)
        self.assertGreater(fit["mean_excess_ci95"]["lower"], 0)
        self.assertLessEqual(
            result["portfolio_risk_budget"]["multiplier"], 1
        )
        self.assertEqual(
            result["evidence_sha256"],
            service.sha256_payload(evidence),
        )
        self.assertFalse(
            result["boundaries"]["calibrated_probability_provided"]
        )

    def test_three_recent_same_regime_failures_trigger_avoid(self):
        values = [1.5, -0.2, 0.0, -0.4]
        result, _ = service.compose_regime_hub(
            [run_record("run_a"), run_record("run_b")],
            [strategy_row(values)],
            baskets=[
                basket(index, regime="risk_on")
                for index in range(len(values))
            ],
            now=NOW,
        )
        fit = result["strategy_fits"][0]

        self.assertEqual(fit["fit_status"], "avoid")
        self.assertEqual(fit["allocation_tilt"], 0)
        self.assertTrue(fit["recent_three_nonpositive"])
        self.assertEqual(result["summary"]["avoid_strategy_count"], 1)

    def test_other_regimes_are_not_mixed_into_fit_sample(self):
        values = [1.0, 1.1, -4.0, -5.0, 0.9, 1.2]
        baskets = [
            basket(index, regime=("risk_on" if index in {0, 1, 4, 5} else "defensive"))
            for index in range(len(values))
        ]
        result, _ = service.compose_regime_hub(
            [run_record("run_a"), run_record("run_b")],
            [strategy_row(values)],
            baskets=baskets,
            now=NOW,
        )
        fit = result["strategy_fits"][0]

        self.assertEqual(fit["matched_cohort_count"], 4)
        self.assertGreater(fit["mean_net_excess_return_pct"], 0)

    def test_latest_verified_basket_supplies_research_exposure_without_ips(self):
        row = strategy_row([1.0, 1.1, 0.9, 1.2])
        row["live_capital_plan"]["positions"] = []
        result, _ = service.compose_regime_hub(
            [run_record("run_a"), run_record("run_b")],
            [row],
            baskets=[
                basket(index, regime="risk_on")
                for index in range(4)
            ],
            now=NOW,
        )
        current = result["strategy_fits"][0]["current_regime"]

        self.assertEqual(current["status"], "risk_on")
        self.assertEqual(current["coverage_pct"], 100)
        self.assertEqual(
            current["positions_source"],
            "latest_verified_paper_basket",
        )

    def test_legacy_basket_uses_only_exact_bound_run_result(self):
        item = basket(
            1, regime="risk_on", source_in_snapshot=False
        )
        bound = run_record("run_basket_1")
        bound["result_sha256"] = "f" * 64
        classified = service.classify_frozen_basket(
            item, bound_run=bound
        )

        self.assertEqual(classified["source"], "bound_run_result")
        self.assertEqual(classified["status"], "risk_on")

        bound["result_sha256"] = "e" * 64
        rejected = service.classify_frozen_basket(
            item, bound_run=bound
        )
        self.assertEqual(
            rejected["source"], "missing_verified_regime_source"
        )
        self.assertEqual(rejected["status"], "insufficient")

    def test_snapshot_is_deduplicated_immutable_and_user_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "regime.db"
            repository = OpportunityRegimeRepository(database)
            result, evidence = service.compose_regime_hub(
                [run_record("run_a"), run_record("run_b")],
                [],
                now=NOW,
            )
            first, created = repository.create_snapshot(
                user_id="owner",
                actor_id="owner",
                engine_version=service.ENGINE_VERSION,
                status=result["status"],
                evidence_cutoff_at=result["evidence_cutoff_at"],
                evidence=evidence,
                result=result,
            )
            second, created_again = repository.create_snapshot(
                user_id="owner",
                actor_id="owner",
                engine_version=service.ENGINE_VERSION,
                status=result["status"],
                evidence_cutoff_at=result["evidence_cutoff_at"],
                evidence=evidence,
                result=result,
            )

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first["id"], second["id"])
            self.assertTrue(first["integrity"]["verified"])
            self.assertIsNone(
                repository.get_snapshot(
                    first["id"], user_id="another-user"
                )
            )
            connection = sqlite3.connect(database)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(
                        """
                        UPDATE opportunity_regime_snapshots
                        SET status='mixed' WHERE id=?
                        """,
                        (first["id"],),
                    )
            finally:
                connection.close()

    def test_postgres_migration_has_immutable_guard(self):
        ddl = opportunity_regime_v1.POSTGRES_DDL
        source = inspect.getsource(
            opportunity_regime_v1.install_opportunity_regime_schema
        )
        self.assertIn("opportunity_regime_snapshots", ddl)
        self.assertIn("BEFORE UPDATE OR DELETE", source)
        self.assertEqual(
            opportunity_regime_v1.MIGRATION_ID,
            "opportunity-regime-allocation.v1",
        )


if __name__ == "__main__":
    unittest.main()
