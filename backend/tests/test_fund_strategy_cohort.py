# -*- coding: utf-8 -*-
"""Strategy Shadow Cohorts are deterministic, versioned, and fail closed."""

import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for candidate in (PROJECT_ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from backend.tests.test_agent_runtime import _analysis, _market_profile  # noqa: E402
from strategies.fund_strategy_cohort import (  # noqa: E402
    COHORT_TAXONOMY_VERSION,
    build_strategy_shadow_cohort,
    classify_fund_asset_class,
)


def _enrollment():
    return {
        "id": "shadow_test",
        "run_id": "run_test",
        "strategy_id": "fund_conditioned_forward_return",
        "strategy_version": "1.0.0",
        "manifest_sha256": "f" * 64,
        "signal_snapshot_sha256": "e" * 64,
        "signal_evidence_id": "ev_signal",
        "fund_code": "001480",
        "baseline_as_of": "2026-07-10",
        "signal_direction": "positive",
        "horizon": "6m",
        "observation_days": 126,
        "signal_snapshot": {"fund": {"market": "mainland"}},
    }


def _evidence(evidence_id, payload, payload_hash):
    return {
        "id": evidence_id,
        "run_id": "run_test",
        "payload_sha256": payload_hash,
        "payload": payload,
        "integrity_verified": True,
    }


def _build(enrollment=None, market=None, analysis=None):
    return build_strategy_shadow_cohort(
        enrollment=enrollment or _enrollment(),
        market_profile_evidence=_evidence(
            "ev_market",
            market or _market_profile({}),
            "a" * 64,
        ),
        signal_evidence=_evidence(
            "ev_signal",
            analysis or _analysis({}),
            "b" * 64,
        ),
    )


class FundStrategyCohortTests(unittest.TestCase):
    def test_mainland_mixed_cohort_is_release_eligible(self):
        cohort = _build()

        self.assertEqual(cohort["taxonomy"]["version"], COHORT_TAXONOMY_VERSION)
        self.assertEqual(cohort["dimensions"]["market"]["primary"], "mainland")
        self.assertEqual(cohort["dimensions"]["asset_class"]["primary"], "mixed")
        self.assertEqual(cohort["dimensions"]["vehicle"]["type"], "domestic")
        self.assertEqual(cohort["dimensions"]["signal_regime"]["trend"], "above_ma60")
        self.assertTrue(cohort["release_classification"]["eligible"])
        self.assertEqual(
            cohort["keys"]["release_cohort"],
            "horizon=6m|market=mainland|asset=mixed|vehicle=domestic",
        )

    def test_hong_kong_qdii_equity_is_a_distinct_cohort(self):
        enrollment = _enrollment()
        enrollment["signal_snapshot"]["fund"]["market"] = "hong_kong"
        market = copy.deepcopy(_market_profile({}))
        market["fund"]["fund_type"] = "指数型-海外股票"
        market["fund"]["is_qdii"] = True
        market["market"].update({
            "primary": "hong_kong",
            "detected_markets": ["hong_kong"],
            "cross_border": True,
            "currency_risk": True,
        })
        cohort = _build(enrollment=enrollment, market=market)

        self.assertEqual(cohort["dimensions"]["asset_class"]["primary"], "equity")
        self.assertEqual(cohort["dimensions"]["vehicle"]["type"], "qdii")
        self.assertIn("market=hong_kong", cohort["keys"]["release_cohort"])
        self.assertIn("vehicle=qdii", cohort["keys"]["release_cohort"])

    def test_cross_border_mixed_key_freezes_detected_market_set(self):
        enrollment = _enrollment()
        enrollment["signal_snapshot"]["fund"]["market"] = "cross_border_mixed"
        market = copy.deepcopy(_market_profile({}))
        market["fund"]["fund_type"] = "股票型-QDII"
        market["fund"]["is_qdii"] = True
        market["market"].update({
            "primary": "cross_border_mixed",
            "detected_markets": ["united_states", "hong_kong"],
            "cross_border": True,
            "currency_risk": True,
        })
        cohort = _build(enrollment=enrollment, market=market)

        self.assertEqual(
            cohort["dimensions"]["market"]["bucket"],
            "cross_border_mixed[hong_kong+united_states]",
        )
        self.assertTrue(cohort["release_classification"]["eligible"])

    def test_unresolved_qdii_is_retained_but_not_release_eligible(self):
        enrollment = _enrollment()
        enrollment["signal_snapshot"]["fund"]["market"] = "unknown_cross_border"
        market = copy.deepcopy(_market_profile({}))
        market["resolution_status"] = "insufficient"
        market["fund"].update({"fund_type": "QDII", "is_qdii": True})
        market["market"].update({
            "primary": "unknown_cross_border",
            "detected_markets": [],
            "cross_border": True,
            "currency_risk": True,
        })
        cohort = _build(enrollment=enrollment, market=market)

        self.assertFalse(cohort["release_classification"]["eligible"])
        self.assertIn(
            "underlying_cross_border_market_unknown",
            cohort["release_classification"]["reasons"],
        )
        self.assertIn(
            "asset_class_unknown",
            cohort["release_classification"]["reasons"],
        )

    def test_signal_horizon_or_direction_mismatch_is_rejected(self):
        enrollment = _enrollment()
        enrollment["signal_direction"] = "negative"
        with self.assertRaisesRegex(ValueError, "方向、周期或基线"):
            _build(enrollment=enrollment)

    def test_asset_classifier_does_not_guess_plain_unknown_type(self):
        self.assertEqual(classify_fund_asset_class("债券指数型")["primary"], "fixed_income")
        self.assertEqual(classify_fund_asset_class("商品-黄金")["primary"], "commodity")
        self.assertEqual(classify_fund_asset_class("未分类")["primary"], "unknown")


if __name__ == "__main__":
    unittest.main()
