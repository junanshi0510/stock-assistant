# -*- coding: utf-8 -*-
"""Strategy committee must diversify, suspend decay, and remain auditable."""

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

import opportunity_committee_service as service  # noqa: E402
from migrations import opportunity_committee_v1  # noqa: E402
from opportunity_committee_repository import (  # noqa: E402
    OpportunityCommitteeRepository,
)


def strategy_row(
    strategy_id: str,
    *,
    positions: list[tuple[str, float]],
    lower: float = 0.5,
    cohort_returns: list[float] | None = None,
) -> dict:
    returns = cohort_returns or [1.0, 1.5, 0.8, 1.2, 1.1, 0.9]
    cohorts = [
        {
            "basket_id": f"{strategy_id}_basket_{index}",
            "run_id": f"{strategy_id}_run_{index}",
            "frozen_at": (
                dt.datetime(
                    2026,
                    1,
                    1,
                    tzinfo=dt.timezone.utc,
                )
                + dt.timedelta(days=index * 30)
            ).isoformat(),
            "outcome_date_max": (
                dt.date(2026, 1, 31)
                + dt.timedelta(days=index * 30)
            ).isoformat(),
            "net_excess_return_pct": value,
            "net_return_pct": value + 0.5,
            "benchmark_return_pct": 0.5,
            "cohort_max_drawdown_pct": 4,
            "position_coverage_pct": 100,
        }
        for index, value in enumerate(returns)
    ]
    return {
        "strategy_id": strategy_id,
        "strategy_name": f"策略 {strategy_id}",
        "strategy_version_id": f"{strategy_id}_v1",
        "strategy_version_no": 1,
        "definition_sha256": "a" * 64,
        "profit_policy_id": f"{strategy_id}_policy",
        "profit_policy_version_no": 1,
        "scorecard_id": f"{strategy_id}_scorecard",
        "scorecard_sha256": "b" * 64,
        "scorecard_current": True,
        "evidence_cutoff_at": "2026-07-01T00:00:00+00:00",
        "capital_gate_status": "limited_manual_pilot",
        "capital_eligible": True,
        "capital_plan_status": "available",
        "basket_id": f"{strategy_id}_basket_current",
        "primary_horizon_trading_days": 20,
        "mature_cohort_count": len(cohorts),
        "mean_net_excess_return_pct": sum(returns) / len(returns),
        "positive_excess_rate_pct": (
            sum(1 for value in returns if value > 0) / len(returns) * 100
        ),
        "mean_excess_ci95": {"lower": lower, "upper": 2.0},
        "familywise_ci95": {
            "lower": lower,
            "upper": 2.2,
            "strategy_family_size": 3,
        },
        "worst_cohort_drawdown_pct": 6,
        "maximum_manual_pilot_pct": 5,
        "minimum_mature_baskets": 6,
        "primary_cohorts": cohorts,
        "live_capital_plan": {
            "status": "available",
            "basket_id": f"{strategy_id}_basket_current",
            "pilot_cap_pct": 5,
            "pilot_cap_cny": 5_000,
            "planned_budget_cny": 5_000,
            "positions": [
                {
                    "market": "A股",
                    "symbol": symbol,
                    "name": symbol,
                    "source_weight_pct": weight,
                }
                for symbol, weight in positions
            ],
            "reasons": [],
        },
        "reasons": [],
    }


class OpportunityCommitteeTests(unittest.TestCase):
    def test_single_strategy_and_candidate_caps_hold_cash(self):
        result, evidence = service.compose_committee(
            [
                strategy_row(
                    "alpha",
                    positions=[("600519", 60), ("000858", 40)],
                )
            ],
            now=dt.datetime(
                2026, 7, 23, tzinfo=dt.timezone.utc
            ),
        )

        self.assertEqual(result["status"], "concentrated")
        self.assertEqual(
            result["summary"]["committee_investable_pct"], 50
        )
        self.assertEqual(
            result["strategies"][0]["committee_weight_pct"], 50
        )
        candidates = {
            item["symbol"]: item
            for item in result["candidate_consensus"]
        }
        self.assertEqual(
            candidates["600519"]["model_target_weight_pct"], 25
        )
        self.assertTrue(candidates["600519"]["candidate_cap_applied"])
        self.assertEqual(
            candidates["000858"]["model_target_weight_pct"], 20
        )
        self.assertEqual(result["summary"]["cash_reserve_pct"], 55)
        self.assertEqual(
            result["evidence_sha256"],
            service.sha256_payload(evidence),
        )
        self.assertFalse(
            result["boundaries"]["calibrated_probability_provided"]
        )

    def test_duplicate_strategies_reserve_cash_and_reward_unique_sleeve(self):
        result, _ = service.compose_committee(
            [
                strategy_row(
                    "duplicate_a",
                    positions=[("600519", 60), ("000858", 40)],
                ),
                strategy_row(
                    "duplicate_b",
                    positions=[("600519", 60), ("000858", 40)],
                ),
                strategy_row(
                    "diverse",
                    positions=[("300750", 50), ("601318", 50)],
                    cohort_returns=[
                        1.4,
                        0.6,
                        1.3,
                        0.7,
                        1.2,
                        0.8,
                    ],
                ),
            ]
        )

        weights = {
            item["strategy_id"]: item["committee_weight_pct"]
            for item in result["strategies"]
        }
        self.assertGreater(
            weights["diverse"], weights["duplicate_a"]
        )
        duplicate_pair = next(
            item
            for item in result["redundancy_matrix"]
            if {
                item["first_strategy_id"],
                item["second_strategy_id"],
            }
            == {"duplicate_a", "duplicate_b"}
        )
        self.assertEqual(
            duplicate_pair["current_position_overlap_pct"], 100
        )
        self.assertLessEqual(max(weights.values()), 50)

    def test_three_recent_nonpositive_cohorts_trigger_kill_switch(self):
        result, _ = service.compose_committee(
            [
                strategy_row(
                    "decayed",
                    positions=[("600519", 60), ("000858", 40)],
                    cohort_returns=[1.2, 1.0, 0.8, -0.1, 0.0, -0.5],
                )
            ]
        )

        self.assertEqual(result["status"], "degraded")
        row = result["strategies"][0]
        self.assertEqual(row["committee_state"], "suspended")
        self.assertEqual(row["committee_weight_pct"], 0)
        self.assertTrue(
            row["recent_decay"]["three_consecutive_nonpositive"]
        )
        self.assertEqual(result["candidate_consensus"], [])

    def test_drift_band_ignores_small_target_changes(self):
        initial, _ = service.compose_committee(
            [
                strategy_row(
                    "alpha",
                    positions=[("600519", 50), ("000858", 50)],
                )
            ]
        )
        current, _ = service.compose_committee(
            [
                strategy_row(
                    "alpha",
                    positions=[("600519", 52), ("000858", 48)],
                )
            ],
            previous_result=initial,
        )

        self.assertEqual(current["drift"]["state"], "within_band")
        self.assertFalse(
            current["drift"]["rebalance_required"]
        )
        self.assertLess(
            current["drift"]["candidate_one_way_turnover_pct"],
            service.REBALANCE_DRIFT_THRESHOLD_PCT,
        )

    def test_mandate_is_deduplicated_immutable_and_user_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "committee.db"
            repository = OpportunityCommitteeRepository(database)
            result, evidence = service.compose_committee(
                [
                    strategy_row(
                        "alpha",
                        positions=[("600519", 50), ("000858", 50)],
                    )
                ]
            )
            first, created = repository.create_mandate(
                user_id="owner",
                actor_id="owner",
                engine_version=service.ENGINE_VERSION,
                status=result["status"],
                evidence_cutoff_at=result["evidence_cutoff_at"],
                evidence=evidence,
                result=result,
            )
            second, created_again = repository.create_mandate(
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
                repository.get_mandate(
                    first["id"], user_id="another-user"
                )
            )
            connection = sqlite3.connect(database)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(
                        """
                        UPDATE opportunity_committee_mandates
                        SET status='active' WHERE id=?
                        """,
                        (first["id"],),
                    )
            finally:
                connection.close()

    def test_postgres_migration_has_immutable_guard(self):
        ddl = opportunity_committee_v1.POSTGRES_DDL
        source = inspect.getsource(
            opportunity_committee_v1.install_opportunity_committee_schema
        )
        self.assertIn("opportunity_committee_mandates", ddl)
        self.assertIn("BEFORE UPDATE OR DELETE", source)
        self.assertEqual(
            opportunity_committee_v1.MIGRATION_ID,
            "opportunity-investment-committee.v1",
        )


if __name__ == "__main__":
    unittest.main()
