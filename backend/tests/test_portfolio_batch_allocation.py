# -*- coding: utf-8 -*-

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.batches import summarize_batch  # noqa: E402
from agent.batch_allocations import (  # noqa: E402
    BatchAllocationConflictError,
    create_batch_allocation,
)
from agent.repository import AgentRepository  # noqa: E402
from strategies.portfolio_batch_allocation import (  # noqa: E402
    evaluate_portfolio_batch_allocation,
)


def _basis(
    code: str,
    *,
    capacity: float = 10_000,
    equity_current: float = 3_000,
    equity_target: float = 60,
    equity_limit: float = 80,
    industry_current: float = 500,
    industry_target: float = 20,
    industry_limit: float = 50,
    holdings_hash: str = "b" * 64,
) -> dict:
    return {
        "target_code": code,
        "portfolio_total_amount": 10_000,
        "portfolio_holdings_sha256": holdings_hash,
        "profile_version_id": "ips_v1",
        "profile_payload_sha256": "c" * 64,
        "exposure_snapshot_id": f"exposure_{code}",
        "exposure_snapshot_sha256": "d" * 64,
        "single_fund_capacity_yuan": capacity,
        "aggregate_candidate_capacity_yuan": capacity,
        "equity": {
            "current_upper_amount_yuan": equity_current,
            "target_upper_ratio_pct": equity_target,
            "limit_ratio_pct": equity_limit,
        },
        "industry": {
            "current_known_lower_amounts_yuan": {"信息技术": industry_current},
            "current_unknown_equity_amount_yuan": 0,
            "target_known_lower_ratios_pct": {"信息技术": industry_target},
            "target_unknown_ratio_pct": 0,
            "limit_ratio_pct": industry_limit,
        },
    }


def _result(
    code: str,
    volatility: float | None,
    *,
    eligible: bool = True,
    capacity: float = 10_000,
    holding_code: str | None = None,
    **basis_overrides,
) -> dict:
    facts = [] if volatility is None else [
        {"label": "年化波动", "value": volatility, "unit": "%"},
    ]
    holding = holding_code or f"holding_{code}"
    return {
        "fund": {"code": code, "name": f"测试基金{code}", "as_of": "2026-07-15"},
        "facts": facts,
        "conclusion": {"role": "组合候选", "risk_band": "平衡型", "timing_label": "等待复核"},
        "strategy": {"decision": "hold_review", "confidence": {"level": "medium"}},
        "market_profile": {"market": {"primary": "mainland", "label": "中国内地"}},
        "market_intelligence": {
            "status": "available",
            "holding_pulse": {
                "items": [{
                    "code": holding,
                    "name": holding,
                    "market": "mainland",
                    "nav_ratio": 10,
                }],
            },
            "news": {"count": 0},
        },
        "ai_synthesis": {"status": "unavailable", "reason_code": "not_requested"},
        "personalized_decision": {
            "decision": {
                "action": "batch_allocation_pending" if eligible else "hold_no_add",
                "rationale": "通过单基金门禁" if eligible else "单基金门禁未放行新增投入",
            },
            "batch_allocation": {
                "scope": "portfolio_batch",
                "eligible": eligible,
                "pre_allocation_action": "consider_tranche" if eligible else "hold_no_add",
                "tranche_count": 5 if eligible else None,
                "basis": _basis(code, capacity=capacity, **basis_overrides),
            },
        },
    }


def _batch(results: list[dict], *, budget: float = 3_000) -> dict:
    return {
        "id": "batch_test",
        "intent": "fund_deep_research",
        "input_hash": "a" * 64,
        "input": {
            "planned_amount": budget,
            "acknowledged_available_cash": True,
            "include_portfolio_context": True,
            "profile_version_id": "ips_v1",
        },
        "items": [
            {
                "sequence_no": index,
                "code": result["fund"]["code"],
                "run": {
                    "id": f"run_{index}",
                    "status": "completed",
                    "result": result,
                },
            }
            for index, result in enumerate(results, start=1)
        ],
    }


def _overlap(codes: list[str], pairs: list[dict] | None = None) -> dict:
    return {"covered_codes": codes, "pairs": pairs or []}


class PortfolioBatchAllocationTests(unittest.TestCase):
    def _evaluate(self, batch: dict, overlap: dict) -> dict:
        return evaluate_portfolio_batch_allocation(
            batch,
            overlap,
            generated_at="2026-07-15T00:00:00+00:00",
        )

    def test_one_total_budget_is_inverse_volatility_weighted(self):
        batch = _batch([_result("000001", 10), _result("000002", 20)])
        result = self._evaluate(batch, _overlap(["000001", "000002"]))

        self.assertEqual(result["status"], "ready")
        amounts = {
            item["code"]: item["allocated_amount_yuan"]
            for item in result["allocation"]["items"]
        }
        self.assertEqual(amounts, {"000001": 2_000, "000002": 1_000})
        self.assertEqual(result["budget"]["allocated_total_yuan"], 3_000)
        self.assertLessEqual(
            result["budget"]["allocated_total_yuan"],
            result["budget"]["requested_total_yuan"],
        )
        self.assertFalse(result["decision_gate"]["automatic_order_allowed"])

    def test_observed_holding_overlap_reduces_risk_weight(self):
        codes = ["000001", "000002", "000003"]
        batch = _batch([_result(code, 10) for code in codes])
        result = self._evaluate(batch, _overlap(codes, [{
            "left_code": "000001",
            "right_code": "000002",
            "overlap_lower_bound_pct": 40,
        }]))

        amounts = {
            item["code"]: item["allocated_amount_yuan"]
            for item in result["allocation"]["items"]
        }
        self.assertGreater(amounts["000003"], amounts["000001"])
        self.assertEqual(amounts["000001"], amounts["000002"])
        self.assertEqual(len(result["aggregate_constraints"]["high_overlap_pairs"]), 1)

    def test_candidate_capacity_is_never_exceeded(self):
        batch = _batch([
            _result("000001", 10, capacity=500),
            _result("000002", 20),
        ])
        result = self._evaluate(batch, _overlap(["000001", "000002"]))
        amounts = {
            item["code"]: item["allocated_amount_yuan"]
            for item in result["allocation"]["items"]
        }

        self.assertEqual(amounts["000001"], 500)
        self.assertEqual(amounts["000002"], 2_500)

    def test_joint_equity_limit_scales_the_entire_vector(self):
        results = [
            _result("000001", 10, equity_current=7_900, equity_target=100),
            _result("000002", 20, equity_current=7_900, equity_target=100),
        ]
        result = self._evaluate(_batch(results), _overlap(["000001", "000002"]))

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["budget"]["allocated_total_yuan"], 499.99)
        self.assertLessEqual(result["budget"]["allocated_total_yuan"], 500)
        self.assertAlmostEqual(result["allocation"]["constraint_scale"], 1 / 6, places=7)
        self.assertEqual(result["budget"]["unallocated_total_yuan"], 2_500.01)

    def test_joint_industry_limit_scales_the_entire_vector(self):
        results = [
            _result("000001", 10, industry_current=4_900, industry_target=100),
            _result("000002", 20, industry_current=4_900, industry_target=100),
        ]
        result = self._evaluate(_batch(results), _overlap(["000001", "000002"]))

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["budget"]["allocated_total_yuan"], 199.99)
        self.assertLessEqual(result["budget"]["allocated_total_yuan"], 200)
        technology = next(
            item for item in result["aggregate_constraints"]["industries"]
            if item["industry"] == "信息技术"
        )
        self.assertAlmostEqual(technology["scale"], 1 / 15, places=7)

    def test_missing_real_volatility_blocks_allocation(self):
        batch = _batch([_result("000001", None), _result("000002", 20)])
        result = self._evaluate(batch, _overlap(["000001", "000002"]))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["budget"]["allocated_total_yuan"], 0)
        self.assertEqual(
            next(item for item in result["gates"] if item["code"] == "child_evidence_complete")["status"],
            "block",
        )

    def test_mismatched_holdings_binding_blocks_allocation(self):
        batch = _batch([
            _result("000001", 10),
            _result("000002", 20, holdings_hash="e" * 64),
        ])
        result = self._evaluate(batch, _overlap(["000001", "000002"]))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            next(item for item in result["gates"] if item["code"] == "portfolio_bindings_consistent")["status"],
            "block",
        )

    def test_missing_disclosed_holding_coverage_blocks_allocation(self):
        batch = _batch([_result("000001", 10), _result("000002", 20)])
        result = self._evaluate(batch, _overlap(["000001"]))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            next(item for item in result["gates"] if item["code"] == "overlap_coverage_complete")["status"],
            "block",
        )

    def test_no_individually_eligible_candidate_blocks_allocation(self):
        batch = _batch([
            _result("000001", 10, eligible=False),
            _result("000002", 20, eligible=False),
        ])
        result = self._evaluate(batch, _overlap(["000001", "000002"]))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["allocation"]["eligible_count"], 0)
        self.assertEqual(result["budget"]["allocated_total_yuan"], 0)


class PortfolioBatchAllocationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent.db"
        self.repository = AgentRepository(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _completed_batch(self) -> dict:
        batch, _ = self.repository.create_batch(
            "fund_deep_research",
            {
                "codes": ["000001", "000002"],
                "planned_amount": 3_000,
                "acknowledged_available_cash": True,
                "include_portfolio_context": True,
                "profile_version_id": "ips_v1",
                "question": "比较真实证据并统一分配本批次总预算。",
            },
            user_id="user_one",
            profile_version_id="ips_v1",
        )
        for item, volatility in zip(batch["items"], [10, 20], strict=True):
            self.assertEqual(item["run"]["input"]["allocation_scope"], "portfolio_batch")
            self.repository.finish_run(
                item["run"]["id"],
                status="completed",
                result=_result(item["code"], volatility),
            )
        return self.repository.get_batch(batch["id"])

    def _payload(self, batch: dict) -> dict:
        summary = summarize_batch(batch)
        return evaluate_portfolio_batch_allocation(
            batch,
            summary["holding_overlap"],
            generated_at="2026-07-15T00:00:00+00:00",
        )

    def test_event_is_hash_verified_idempotent_and_immutable(self):
        batch = self._completed_batch()
        payload = self._payload(batch)

        event, created = self.repository.create_batch_allocation_event(
            batch["id"], payload, user_id="user_one", actor_id="actor_one"
        )
        repeated, repeated_created = self.repository.create_batch_allocation_event(
            batch["id"], payload, user_id="user_one", actor_id="actor_one"
        )

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(event["id"], repeated["id"])
        self.assertTrue(self.repository.get_batch(batch["id"])["allocation_event"]["integrity_verified"])
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE agent_batch_allocation_events SET actor_id='changed' WHERE id=?",
                    (event["id"],),
                )
        with self.repository._connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM agent_batch_allocation_events WHERE id=?",
                    (event["id"],),
                )

    def test_application_service_returns_allocation_in_batch_summary(self):
        batch = self._completed_batch()
        event, created = create_batch_allocation(
            self.repository,
            batch,
            expected_batch_input_hash=batch["input_hash"],
            user_id="user_one",
            actor_id="actor_one",
        )

        summary = summarize_batch(self.repository.get_batch(batch["id"]))
        self.assertTrue(created)
        self.assertTrue(event["integrity_verified"])
        self.assertEqual(summary["allocation"]["status"], "ready")
        self.assertEqual(summary["allocation"]["budget"]["allocated_total_yuan"], 3_000)
        self.assertTrue(all(item["portfolio_allocation"] for item in summary["items"]))

        repeated, repeated_created = create_batch_allocation(
            self.repository,
            self.repository.get_batch(batch["id"]),
            expected_batch_input_hash=batch["input_hash"],
            user_id="user_one",
            actor_id="actor_one",
        )
        self.assertFalse(repeated_created)
        self.assertEqual(repeated["id"], event["id"])

    def test_application_service_rejects_stale_expected_input_hash(self):
        batch = self._completed_batch()
        with self.assertRaises(BatchAllocationConflictError):
            create_batch_allocation(
                self.repository,
                batch,
                expected_batch_input_hash="f" * 64,
                user_id="user_one",
                actor_id="actor_one",
            )

    def test_event_rejects_other_user_and_a_different_snapshot(self):
        batch = self._completed_batch()
        payload = self._payload(batch)
        with self.assertRaises(KeyError):
            self.repository.create_batch_allocation_event(
                batch["id"], payload, user_id="user_two", actor_id="actor_two"
            )

        self.repository.create_batch_allocation_event(
            batch["id"], payload, user_id="user_one", actor_id="actor_one"
        )
        changed = copy.deepcopy(payload)
        changed["generated_at"] = "2026-07-15T00:00:01+00:00"
        with self.assertRaises(ValueError):
            self.repository.create_batch_allocation_event(
                batch["id"], changed, user_id="user_one", actor_id="actor_one"
            )

    def test_event_rejects_stale_child_result_binding(self):
        batch = self._completed_batch()
        payload = self._payload(batch)
        first_run = batch["items"][0]["run"]
        changed = _result("000001", 11)
        with self.repository._connect() as connection:
            connection.execute(
                "UPDATE agent_runs SET result_json=? WHERE id=?",
                (
                    json.dumps(
                        changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    first_run["id"],
                ),
            )

        with self.assertRaises(ValueError):
            self.repository.create_batch_allocation_event(
                batch["id"], payload, user_id="user_one", actor_id="actor_one"
            )


if __name__ == "__main__":
    unittest.main()
