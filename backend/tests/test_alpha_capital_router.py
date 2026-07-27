# -*- coding: utf-8 -*-
"""Alpha capital routing must be calibrated, sparse, and immutable."""

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

import alpha_capital_router as service  # noqa: E402
from alpha_capital_repository import (  # noqa: E402
    AlphaCapitalConflict,
    AlphaCapitalRepository,
)
from migrations import alpha_capital_router_v1  # noqa: E402


NOW = dt.datetime(2026, 7, 26, 8, 0, tzinfo=dt.timezone.utc)


def profile(**overrides):
    value = {
        "configured": True,
        "profile_version_id": "ips_alpha_1",
        "version_no": 1,
        "payload_sha256": "a" * 64,
        "horizon": "mid_long",
        "risk": "balanced",
        "monthly_budget": 10_000,
        "max_single_ratio": 20,
        "max_equity_ratio": 80,
        "max_industry_ratio": 30,
        "allowed_fund_markets": [
            "mainland",
            "hong_kong",
            "united_states",
        ],
        "accept_fx_risk": True,
        "review_due_at": "2027-01-01T00:00:00+00:00",
        "governance_integrity": {"verified": True},
    }
    value.update(overrides)
    return value


def quality_scorecard(horizons):
    return {
        "schema_version": "alpha_forward_scorecard.v1",
        "status": "qualified",
        "horizons": [
            {
                "horizon_sessions": horizon,
                "status": "qualified",
                "decision_eligible": True,
                "outcome_count": 60,
                "run_date_count": 12,
                "symbol_count": 8,
                "brier_skill_score": 0.08,
                "expected_calibration_error": 0.04,
                "high_low_return_spread_pct": 1.8,
                "positive_run_rate": 0.75,
                "checks": [],
            }
            for horizon in horizons
        ],
    }


def program(
    program_id: str,
    *,
    symbol: str = "600519",
    name: str = "贵州茅台",
    asset_type: str = "stock",
    market: str = "A股",
    probabilities: dict[int, float] | None = None,
    base_rate: float = 0.45,
    as_of_date: str = "2026-07-25",
):
    horizons = [5, 20, 60] if asset_type == "stock" else [20, 60, 120]
    probabilities = probabilities or {
        horizons[0]: 0.53,
        horizons[1]: 0.53,
        horizons[2]: 0.66,
    }
    forecasts = [
        {
            "symbol": symbol,
            "name": name,
            "horizon_sessions": horizon,
            "as_of_date": as_of_date,
            "eligible_after": "2026-10-31",
            "published_probability": probabilities[horizon],
            "shadow_calibrated_probability": probabilities[horizon],
            "base_rate": base_rate,
            "stance": "看多候选",
            "historical_gate_passed": True,
            "decision_eligible": True,
            "decision_source_eligible": True,
            "source_evidence": {
                "provider_tier": "professional",
            },
        }
        for horizon in horizons
    ]
    return {
        "id": program_id,
        "name": f"项目 {program_id}",
        "asset_type": asset_type,
        "market": market,
        "status": "active",
        "policy_sha256": (program_id[0] * 64)[:64],
        "policy": {
            "asset_type": asset_type,
            "market": market,
            "symbols": [{"symbol": symbol, "name": name}],
            "horizons": horizons,
            "cadence_days": 7 if asset_type == "stock" else 30,
        },
        "integrity": {"verified": True},
        "forward_scorecard": quality_scorecard(horizons),
        "latest_run": {
            "id": f"run_{program_id}",
            "status": "succeeded",
            "as_of_date": as_of_date,
            "completed_at": f"{as_of_date}T09:00:00+00:00",
            "result_sha256": "b" * 64,
            "integrity": {"verified": True},
            "result": {
                "engine_version": "calibrated_multi_horizon_alpha@1.0.0",
                "model_family": "fixed_logistic",
                "forecasts": forecasts,
            },
        },
    }


def overview(*programs):
    return {
        "schema_version": "alpha_forecast_overview.v1",
        "engine_version": "calibrated_multi_horizon_alpha@1.0.0",
        "programs": list(programs),
    }


class AlphaCapitalRouteTests(unittest.TestCase):
    def test_zero_ece_is_preserved_as_perfect_calibration(self):
        scorecard = quality_scorecard([5])
        scorecard["horizons"][0][
            "expected_calibration_error"
        ] = 0.0

        quality = service._forward_quality(scorecard, 5)

        self.assertIsNotNone(quality)
        self.assertEqual(
            quality["expected_calibration_error"], 0.0
        )
        self.assertEqual(
            quality["reliability_factors"]["calibration"], 1.0
        )

    def test_forward_consensus_becomes_sparse_core_route_with_cash(self):
        result, evidence = service.compose_alpha_capital_route(
            overview=overview(program("alpha")),
            profile=profile(),
            holdings=[],
            now=NOW,
        )

        self.assertEqual(result["status"], "paper_ready")
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["state"], "supportive")
        self.assertEqual(candidate["sleeve"], "core")
        self.assertEqual(candidate["model_target_weight_pct"], 20)
        self.assertEqual(result["summary"]["model_cash_pct"], 80)
        self.assertLess(
            candidate["weighted_effective_edge"],
            candidate["weighted_raw_edge"],
        )
        self.assertEqual(
            result["evidence_sha256"],
            service.sha256_payload(evidence),
        )
        self.assertFalse(
            result["boundaries"]["execution_authorized"]
        )

    def test_negative_consensus_vetoes_without_shorting(self):
        weak = program(
            "weak",
            probabilities={5: 0.35, 20: 0.36, 60: 0.37},
        )
        result, _ = service.compose_alpha_capital_route(
            overview=overview(weak),
            profile=profile(),
            holdings=[],
            now=NOW,
        )

        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["vetoes"][0]["state"], "defensive")
        self.assertEqual(result["summary"]["model_invested_pct"], 0)
        self.assertFalse(result["boundaries"]["short_selling"])

    def test_cross_horizon_conflict_forces_abstention(self):
        conflicted = program(
            "conflict",
            probabilities={5: 0.62, 20: 0.36, 60: 0.63},
        )
        result, _ = service.compose_alpha_capital_route(
            overview=overview(conflicted),
            profile=profile(),
            holdings=[],
            now=NOW,
        )

        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["vetoes"][0]["state"], "conflict")
        self.assertEqual(
            result["vetoes"][0]["model_target_weight_pct"], 0
        )

    def test_duplicate_programs_do_not_vote_twice_and_disagreement_vetoes(self):
        positive = program("positive")
        negative = program(
            "negative",
            probabilities={5: 0.34, 20: 0.35, 60: 0.36},
        )
        result, _ = service.compose_alpha_capital_route(
            overview=overview(positive, negative),
            profile=profile(),
            holdings=[],
            now=NOW,
        )

        self.assertEqual(
            result["summary"]["duplicate_program_exclusion_count"], 1
        )
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["vetoes"][0]["state"], "conflict")
        self.assertTrue(
            result["vetoes"][0]["duplicate_direction_conflict"]
        )

    def test_new_fund_is_modelled_but_cannot_cross_capital_bridge(self):
        fund = program(
            "fund",
            symbol="110011",
            name="易方达优质精选",
            asset_type="fund",
            market="基金",
            probabilities={20: 0.53, 60: 0.56, 120: 0.68},
        )
        result, _ = service.compose_alpha_capital_route(
            overview=overview(fund),
            profile=profile(),
            holdings=[],
            now=NOW,
        )

        self.assertEqual(result["status"], "paper_ready")
        self.assertEqual(
            result["candidates"][0]["capital_bridge_state"],
            "new_fund_due_diligence_only",
        )
        self.assertFalse(
            result["boundaries"]["new_fund_purchase_authorized"]
        )

        held_result, _ = service.compose_alpha_capital_route(
            overview=overview(fund),
            profile=profile(),
            holdings=[
                {
                    "id": 1,
                    "asset_type": "fund",
                    "market": "基金",
                    "code": "110011",
                    "amount": 20_000,
                }
            ],
            now=NOW,
        )
        self.assertEqual(
            held_result["candidates"][0]["capital_bridge_state"],
            "held_fund_top_up_candidate",
        )

    def test_stale_run_and_invalid_policy_fail_closed(self):
        stale = program("stale", as_of_date="2026-06-01")
        collecting, _ = service.compose_alpha_capital_route(
            overview=overview(stale),
            profile=profile(),
            holdings=[],
            now=NOW,
        )
        self.assertEqual(collecting["status"], "collecting")
        self.assertIn(
            "latest_run_stale",
            collecting["programs"][0]["exclusion_reasons"],
        )

        blocked, _ = service.compose_alpha_capital_route(
            overview=overview(program("alpha")),
            profile=profile(
                governance_integrity={"verified": False}
            ),
            holdings=[],
            now=NOW,
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["gates"][0]["status"], "block")

    def test_drift_counts_cash_and_only_requests_manual_review(self):
        first, _ = service.compose_alpha_capital_route(
            overview=overview(program("alpha")),
            profile=profile(),
            holdings=[],
            now=NOW,
        )
        second, _ = service.compose_alpha_capital_route(
            overview=overview(
                program(
                    "other",
                    symbol="000858",
                    name="五粮液",
                )
            ),
            profile=profile(),
            holdings=[],
            previous_result=first,
            now=NOW,
        )

        self.assertTrue(
            second["drift"]["rebalance_review_required"]
        )
        self.assertTrue(second["drift"]["entries"])
        self.assertTrue(second["drift"]["exits"])
        self.assertFalse(
            second["boundaries"]["automatic_order_creation"]
        )


class AlphaCapitalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "alpha-capital.db"
        self.repository = AlphaCapitalRepository(self.database)
        self.overview = overview(program("alpha"))
        self.profile = profile()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_freeze_is_confirmed_idempotent_immutable_and_scoped(self):
        current = service.current_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            now=NOW,
            repo=self.repository,
            overview=self.overview,
            profile=self.profile,
            holdings=[],
        )
        first, created = service.freeze_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            expected_evidence_sha256=current["evidence_sha256"],
            now=NOW,
            repo=self.repository,
            overview=self.overview,
            profile=self.profile,
            holdings=[],
        )
        second, created_again = service.freeze_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            expected_evidence_sha256=current["evidence_sha256"],
            now=NOW,
            repo=self.repository,
            overview=self.overview,
            profile=self.profile,
            holdings=[],
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["integrity"]["verified"])
        tampered_status = {**first, "status": "abstained"}
        self.assertFalse(
            self.repository._integrity(tampered_status)["verified"]
        )
        self.assertFalse(
            self.repository._integrity(tampered_status)[
                "status_binding_verified"
            ]
        )
        self.assertIsNone(
            self.repository.get_mandate(
                first["id"],
                tenant_id="public",
                user_id="other",
            )
        )
        verified = service.current_verified_mandate(
            tenant_id="public",
            user_id="owner",
            now=NOW,
            repo=self.repository,
            overview=self.overview,
            profile=self.profile,
            holdings=[],
        )
        self.assertTrue(verified["capital_eligible"])
        self.assertEqual(verified["mandate"]["id"], first["id"])

        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute(
                    """
                    UPDATE alpha_capital_mandates
                    SET status='blocked' WHERE id=?
                    """,
                    (first["id"],),
                )
        finally:
            connection.close()

    def test_changed_evidence_requires_fresh_confirmation(self):
        current = service.current_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            now=NOW,
            repo=self.repository,
            overview=self.overview,
            profile=self.profile,
            holdings=[],
        )
        changed = overview(
            program(
                "alpha",
                probabilities={5: 0.54, 20: 0.54, 60: 0.69},
            )
        )
        with self.assertRaises(AlphaCapitalConflict):
            service.freeze_alpha_capital_route(
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                expected_evidence_sha256=current["evidence_sha256"],
                now=NOW,
                repo=self.repository,
                overview=changed,
                profile=self.profile,
                holdings=[],
            )

    def test_blocked_or_collecting_route_cannot_be_frozen(self):
        stale_overview = overview(
            program("stale", as_of_date="2026-06-01")
        )
        current = service.current_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            now=NOW,
            repo=self.repository,
            overview=stale_overview,
            profile=self.profile,
            holdings=[],
        )
        self.assertEqual(current["status"], "collecting")

        with self.assertRaises(AlphaCapitalConflict):
            service.freeze_alpha_capital_route(
                tenant_id="public",
                user_id="owner",
                actor_id="owner",
                expected_evidence_sha256=current[
                    "evidence_sha256"
                ],
                now=NOW,
                repo=self.repository,
                overview=stale_overview,
                profile=self.profile,
                holdings=[],
            )
        self.assertEqual(
            self.repository.list_mandates(
                tenant_id="public", user_id="owner"
            ),
            [],
        )

    def test_frozen_abstention_remains_eligible_as_a_capital_veto(self):
        weak_overview = overview(
            program(
                "weak",
                probabilities={5: 0.35, 20: 0.36, 60: 0.37},
            )
        )
        current = service.current_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            now=NOW,
            repo=self.repository,
            overview=weak_overview,
            profile=self.profile,
            holdings=[],
        )
        self.assertEqual(current["status"], "abstained")
        service.freeze_alpha_capital_route(
            tenant_id="public",
            user_id="owner",
            actor_id="owner",
            expected_evidence_sha256=current["evidence_sha256"],
            now=NOW,
            repo=self.repository,
            overview=weak_overview,
            profile=self.profile,
            holdings=[],
        )

        verified = service.current_verified_mandate(
            tenant_id="public",
            user_id="owner",
            now=NOW,
            repo=self.repository,
            overview=weak_overview,
            profile=self.profile,
            holdings=[],
        )
        self.assertTrue(verified["capital_eligible"])
        self.assertEqual(
            verified["mandate"]["result"]["vetoes"][0]["state"],
            "defensive",
        )

    def test_postgres_migration_is_scoped_and_immutable(self):
        ddl = alpha_capital_router_v1.POSTGRES_DDL
        source = inspect.getsource(
            alpha_capital_router_v1.install_alpha_capital_router_schema
        )
        self.assertEqual(
            alpha_capital_router_v1.MIGRATION_ID,
            "alpha-capital-router.v1",
        )
        self.assertIn("tenant_id TEXT NOT NULL", ddl)
        self.assertIn(
            "UNIQUE(tenant_id, user_id, engine_version, evidence_sha256)",
            ddl,
        )
        self.assertIn("BEFORE UPDATE OR DELETE", source)


if __name__ == "__main__":
    unittest.main()
