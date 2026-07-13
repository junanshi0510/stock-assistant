# -*- coding: utf-8 -*-

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.repository import AgentRepository  # noqa: E402
from agent.strategy_governance import (  # noqa: E402
    CONDITIONED_FORWARD_MANIFEST,
    StrategyGovernanceService,
)


def _released_manifest(*, canary_percent=25, allowed_users=None):
    required = [
        "independent_out_of_sample_period",
        "non_overlapping_evaluation_windows",
        "investable_peer_benchmark",
        "transaction_cost_model",
        "minimum_shadow_outcomes",
        "independent_strategy_review",
    ]
    return {
        "schema_version": "strategy_manifest.v1",
        "strategy_id": "test_released_strategy",
        "strategy_version": "2.0.0",
        "name": "测试已验证策略",
        "strategy_kind": "alpha_signal",
        "owner_id": "strategy-owner",
        "applicability": {
            "asset_types": ["fund"],
            "markets": ["mainland"],
            "frequency": "confirmed_nav_date",
            "user_scenarios": ["personalized_fund_decision"],
        },
        "required_release_checks": required,
        "release_checks": [
            {
                "code": code,
                "status": "pass",
                "detail": f"测试发布证据:{code}",
                "evidence_ref": f"evaluation-report:{code}",
            }
            for code in required
        ],
        "canary": {
            "percent": canary_percent,
            "allowed_user_ids": allowed_users or [],
        },
        "known_limitations": ["test_only"],
    }


def _runtime_payload(strategy_id, strategy_version, *, user_id="user-a", market="mainland"):
    return {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "asset_type": "fund",
        "market": market,
        "user_scenario": "personalized_fund_decision",
        "user_id": user_id,
    }


class StrategyGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")
        self.service = StrategyGovernanceService(self.repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _register_active(self, manifest):
        strategy_id = manifest["strategy_id"]
        strategy_version = manifest["strategy_version"]
        self.repository.register_strategy_version(manifest, initial_status="shadow")
        self.service.transition(
            strategy_id,
            strategy_version,
            expected_status="shadow",
            target_status="canary",
            actor_role="reviewer",
            actor_id="release-reviewer-a",
            reason="完整发布检查通过并进入确定性灰度阶段",
        )
        self.service.transition(
            strategy_id,
            strategy_version,
            expected_status="canary",
            target_status="active",
            actor_role="reviewer",
            actor_id="release-reviewer-b",
            reason="灰度观察达到要求并批准正式生产发布",
        )

    def test_default_strategy_is_immutable_shadow_and_cannot_drive_money(self):
        seeded = self.service.seed_defaults()
        self.assertTrue(seeded[0]["created"])
        result = self.service.evaluate_runtime_use(
            _runtime_payload("fund_conditioned_forward_return", "1.0.0")
        )

        self.assertEqual(result["strategy"]["status"], "shadow")
        self.assertTrue(result["strategy"]["registered"])
        self.assertTrue(result["release"]["manifest_integrity_verified"])
        self.assertTrue(result["release"]["audit_chain"]["verified"])
        self.assertEqual(result["release"]["required_check_count"], 6)
        self.assertEqual(result["release"]["passed_check_count"], 0)
        self.assertFalse(result["release"]["release_ready"])
        self.assertTrue(result["execution"]["calculation_allowed"])
        self.assertFalse(result["execution"]["decision_use_allowed"])
        self.assertEqual(result["execution"]["mode"], "shadow")

    def test_seed_is_idempotent_but_same_version_content_change_is_rejected(self):
        self.service.seed_defaults()
        second = self.service.seed_defaults()
        self.assertFalse(second[0]["created"])

        changed = copy.deepcopy(CONDITIONED_FORWARD_MANIFEST)
        changed["name"] = "被修改的同版本策略"
        with self.assertRaisesRegex(ValueError, "哈希不同"):
            self.repository.register_strategy_version(changed, initial_status="shadow")
        released = _released_manifest()
        with self.assertRaisesRegex(ValueError, "只能以 draft 或迁移期 shadow"):
            self.repository.register_strategy_version(released, initial_status="active")

    def test_unregistered_or_scope_mismatched_strategy_fails_closed(self):
        missing = self.service.evaluate_runtime_use(
            _runtime_payload("not_registered", "9.9.9")
        )
        self.assertFalse(missing["execution"]["calculation_allowed"])
        self.assertFalse(missing["execution"]["decision_use_allowed"])
        self.assertEqual(missing["execution"]["reason_code"], "strategy_version_unregistered")

        manifest = _released_manifest()
        self._register_active(manifest)
        mismatch = self.service.evaluate_runtime_use(
            _runtime_payload("test_released_strategy", "2.0.0", market="hong_kong")
        )
        self.assertFalse(mismatch["scope"]["applicable"])
        self.assertFalse(mismatch["execution"]["decision_use_allowed"])
        self.assertEqual(mismatch["execution"]["reason_code"], "strategy_scope_mismatch")

    def test_active_release_requires_all_checks_and_independent_reviewer(self):
        manifest = _released_manifest(allowed_users=["user-a"])
        self.repository.register_strategy_version(manifest, initial_status="draft")
        self.service.transition(
            "test_released_strategy",
            "2.0.0",
            expected_status="draft",
            target_status="review",
            actor_role="owner",
            actor_id="strategy-owner",
            reason="提交完整策略评测材料进入独立评审",
        )
        self.service.transition(
            "test_released_strategy",
            "2.0.0",
            expected_status="review",
            target_status="shadow",
            actor_role="reviewer",
            actor_id="reviewer-a",
            reason="独立复核通过后进入影子观察阶段",
        )
        with self.assertRaisesRegex(PermissionError, "不能审批自己"):
            self.service.transition(
                "test_released_strategy",
                "2.0.0",
                expected_status="shadow",
                target_status="canary",
                actor_role="reviewer",
                actor_id="strategy-owner",
                reason="负责人不能批准自己的生产灰度发布",
            )
        self.service.transition(
            "test_released_strategy",
            "2.0.0",
            expected_status="shadow",
            target_status="canary",
            actor_role="reviewer",
            actor_id="reviewer-a",
            reason="样本外评测通过并批准确定性灰度发布",
        )
        canary = self.service.evaluate_runtime_use(
            _runtime_payload("test_released_strategy", "2.0.0", user_id="user-a")
        )
        self.assertTrue(canary["canary"]["selected"])
        self.assertTrue(canary["execution"]["decision_use_allowed"])

        self.service.transition(
            "test_released_strategy",
            "2.0.0",
            expected_status="canary",
            target_status="active",
            actor_role="reviewer",
            actor_id="reviewer-b",
            reason="灰度结果满足门槛并批准全量生产发布",
        )
        active = self.service.evaluate_runtime_use(
            _runtime_payload("test_released_strategy", "2.0.0", user_id="outside-canary")
        )
        self.assertTrue(active["release"]["release_ready"])
        self.assertTrue(active["execution"]["decision_use_allowed"])
        self.assertEqual(active["execution"]["mode"], "active")

    def test_failed_release_checks_cannot_enter_canary(self):
        manifest = copy.deepcopy(CONDITIONED_FORWARD_MANIFEST)
        manifest["strategy_id"] = "failed_release_strategy"
        manifest["strategy_version"] = "1.0.0"
        manifest["canary"] = {"percent": 10, "allowed_user_ids": []}
        self.repository.register_strategy_version(manifest, initial_status="shadow")

        with self.assertRaisesRegex(ValueError, "发布检查未全部通过"):
            self.service.transition(
                "failed_release_strategy",
                "1.0.0",
                expected_status="shadow",
                target_status="canary",
                actor_role="reviewer",
                actor_id="reviewer-a",
                reason="未满足发布门槛时必须拒绝生产灰度",
            )

    def test_operator_pause_is_immediate_and_audited(self):
        manifest = _released_manifest()
        self._register_active(manifest)
        self.service.transition(
            "test_released_strategy",
            "2.0.0",
            expected_status="active",
            target_status="paused",
            actor_role="operator",
            actor_id="oncall-a",
            reason="线上表现异常触发人工紧急暂停开关",
        )
        result = self.service.evaluate_runtime_use(
            _runtime_payload("test_released_strategy", "2.0.0")
        )
        self.assertFalse(result["execution"]["calculation_allowed"])
        self.assertFalse(result["execution"]["decision_use_allowed"])
        self.assertEqual(result["execution"]["reason_code"], "strategy_paused")
        audit = self.repository.verify_strategy_audit_chain(
            "test_released_strategy", "2.0.0"
        )
        self.assertTrue(audit["verified"])
        self.assertEqual(audit["event_count"], 4)

    def test_manifest_or_audit_tampering_blocks_runtime_use(self):
        manifest = _released_manifest()
        self._register_active(manifest)
        with self.repository._connect() as connection:
            changed = copy.deepcopy(manifest)
            changed["name"] = "tampered"
            connection.execute(
                """
                UPDATE agent_strategy_versions SET manifest_json=?
                WHERE strategy_id=? AND strategy_version=?
                """,
                (
                    json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "test_released_strategy",
                    "2.0.0",
                ),
            )
        manifest_failure = self.service.evaluate_runtime_use(
            _runtime_payload("test_released_strategy", "2.0.0")
        )
        self.assertEqual(
            manifest_failure["execution"]["reason_code"],
            "strategy_manifest_integrity_failed",
        )
        self.assertFalse(manifest_failure["execution"]["decision_use_allowed"])

        second = _released_manifest()
        second["strategy_id"] = "audit_tampered_strategy"
        self._register_active(second)
        with self.repository._connect() as connection:
            connection.execute(
                """
                UPDATE agent_strategy_audit_events SET details_json='{"tampered":true}'
                WHERE strategy_id=? AND strategy_version=? AND sequence_no=1
                """,
                ("audit_tampered_strategy", "2.0.0"),
            )
        audit_failure = self.service.evaluate_runtime_use(
            _runtime_payload("audit_tampered_strategy", "2.0.0")
        )
        self.assertEqual(
            audit_failure["execution"]["reason_code"],
            "strategy_audit_chain_failed",
        )
        self.assertFalse(audit_failure["execution"]["decision_use_allowed"])

        third = _released_manifest()
        third["strategy_id"] = "status_tampered_strategy"
        self.repository.register_strategy_version(third, initial_status="shadow")
        with self.repository._connect() as connection:
            connection.execute(
                """
                UPDATE agent_strategy_versions SET status='active'
                WHERE strategy_id=? AND strategy_version=?
                """,
                ("status_tampered_strategy", "2.0.0"),
            )
        state_failure = self.service.evaluate_runtime_use(
            _runtime_payload("status_tampered_strategy", "2.0.0")
        )
        self.assertFalse(state_failure["release"]["registry_state_verified"])
        self.assertEqual(
            state_failure["execution"]["reason_code"],
            "strategy_audit_chain_failed",
        )
        self.assertFalse(state_failure["execution"]["decision_use_allowed"])


if __name__ == "__main__":
    unittest.main()
