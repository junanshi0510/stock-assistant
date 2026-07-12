# -*- coding: utf-8 -*-

import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.registry import ToolDefinition, ToolRegistry  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from agent.workflow import AgentWorkflowRunner  # noqa: E402


def _analysis(_payload):
    return {
        "source": "真实基金净值测试快照",
        "source_url": "https://example.test/fund/001480",
        "code": "001480",
        "name": "测试基金",
        "as_of": "2026-07-10",
        "sample_count": 500,
        "latest": {"unit_nav": 1.2345},
        "metrics": {
            "return_3m": 8.2,
            "return_1y": 16.4,
            "annual_volatility": 22.1,
            "max_drawdown": -18.6,
            "current_drawdown": -4.5,
        },
        "timing": {"score": 63, "label": "小额观察"},
        "trend_state": "震荡观察",
        "playbook": {
            "role": {
                "label": "权益中枢",
                "reason": "真实净值风险收益处于中间区间。",
                "risk_band": "均衡偏波动",
                "minimum_holding_period": "建议按中长期复盘",
            },
            "red_flags": ["历史回撤需要纳入仓位约束。"],
            "entry_rules": [{"level": "小额观察", "rule": "先观察真实净值变化。"}],
            "exit_rules": [{"title": "趋势破坏", "text": "触发后重新研究。"}],
            "execution_steps": [{"step": "1. 先定角色", "action": "先确定组合角色。"}],
        },
    }


def _estimate(_payload):
    return {
        "status": "available",
        "source": "真实盘中估值测试快照",
        "source_url": "https://example.test/fund/001480",
        "code": "001480",
        "confirmed": {"date": "2026-07-10", "unit_nav": 1.2345},
        "estimate": {"time": "2026-07-11 14:30", "unit_nav": 1.22, "change_pct": -1.17},
        "policy": "估值不等于确认净值。",
    }


def _disclosure(_payload):
    return {
        "status": "available",
        "source": "真实定期报告测试快照",
        "source_url": "https://example.test/disclosure/001480",
        "code": "001480",
        "latest": {"year": "2025", "stock_period": "2025-12-31"},
        "previous": {"year": "2024", "stock_period": "2024-12-31"},
        "summary": {
            "top10_stock_ratio_change": 2.1,
            "added_stock_count": 2,
            "removed_stock_count": 1,
            "industry_focus_changed": False,
        },
        "policy": "披露不代表实时持仓。",
    }


def _alternatives(_payload):
    return {
        "source": "真实同类排行测试快照",
        "source_url": "https://example.test/ranking",
        "as_of": "2026-07-10",
        "alternatives": [{
            "code": "000001",
            "name": "候选基金",
            "score": 78,
            "label": "值得继续研究",
            "trend_state": "震荡上行",
            "metrics": {"return_1y": 18.2, "max_drawdown": -14.3},
            "advantages": ["回撤更浅"],
            "cautions": ["仍需检查持仓重合"],
        }],
        "failed": [],
    }


def _registry(
    alternatives_handler=_alternatives,
    analysis_handler=_analysis,
    estimate_handler=_estimate,
    timeout_overrides=None,
):
    registry = ToolRegistry()
    timeout_overrides = timeout_overrides or {}
    for name, timeout, handler in (
        ("fund.analysis.get", 45, analysis_handler),
        ("fund.estimate.get", 20, estimate_handler),
        ("fund.disclosure_changes.get", 45, _disclosure),
        ("fund.alternatives.get", 120, alternatives_handler),
    ):
        registry.register(ToolDefinition(
            name=name,
            version="1.0.0",
            description=name,
            risk_level="R0",
            timeout_seconds=timeout_overrides.get(name, timeout),
            handler=handler,
        ))
    return registry


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, registry=None, **overrides):
        payload = {
            "intent": "fund_deep_research",
            "code": "001480",
            "months": 36,
            "include_estimate": True,
            "include_disclosure_changes": True,
            "include_alternatives": True,
            "alternative_limit": 5,
            **overrides,
        }
        created, is_new = self.repository.create_run("fund_deep_research", payload)
        self.assertTrue(is_new)
        claimed = self.repository.claim_next_run("test-worker")
        self.assertEqual(claimed["id"], created["id"])
        AgentWorkflowRunner(self.repository, registry or _registry()).execute(claimed)
        return self.repository.get_run(created["id"])

    def test_completed_run_persists_claims_evidence_and_hash_chained_audit(self):
        run = self._run()

        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["steps"]), 4)
        self.assertEqual(len(run["evidence"]), 4)
        self.assertGreaterEqual(len(run["claims"]), 7)
        self.assertEqual(run["result"]["fund"]["code"], "001480")
        self.assertEqual(run["result"]["alternatives"][0]["code"], "000001")

        evidence_id = run["result"]["facts"][0]["evidence_id"]
        evidence = self.repository.get_evidence(run["id"], evidence_id, include_payload=True)
        canonical = json.dumps(
            evidence["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.assertEqual(
            evidence["payload_sha256"],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self.assertTrue(evidence["integrity_verified"])

        events = self.repository.list_audit_events(run["id"])
        self.assertGreaterEqual(len(events), 10)
        previous = None
        for event in events:
            self.assertEqual(event["previous_hash"], previous)
            previous = event["event_hash"]
        verification = self.repository.verify_audit_chain(run["id"])
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["event_count"], len(events))
        self.assertEqual(verification["chain_head"], previous)

    def test_optional_real_data_failure_produces_partial_without_fabrication(self):
        def unavailable_alternatives(_payload):
            raise RuntimeError("真实同类排行当前不可用")

        run = self._run(
            registry=_registry(alternatives_handler=unavailable_alternatives),
            include_estimate=False,
            include_disclosure_changes=False,
        )

        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["result"]["alternatives"], [])
        self.assertEqual(run["result"]["unavailable"][0]["tool"], "fund.alternatives.get")
        self.assertIn("真实同类排行当前不可用", run["result"]["unavailable"][0]["reason"])
        failed_step = next(item for item in run["steps"] if item["step_key"] == "fund_alternatives")
        self.assertEqual(failed_step["status"], "failed")

    def test_required_analysis_failure_stops_run(self):
        def unavailable_analysis(_payload):
            raise RuntimeError("真实净值当前不可用")

        run = self._run(registry=_registry(analysis_handler=unavailable_analysis))

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "REQUIRED_TOOL_FAILED")
        self.assertIsNone(run["result"])
        self.assertEqual(len(run["evidence"]), 0)

    def test_optional_tool_timeout_is_partial_and_cannot_write_late_evidence(self):
        def slow_estimate(_payload):
            time.sleep(0.2)
            return _estimate(_payload)

        started = time.monotonic()
        run = self._run(
            registry=_registry(
                estimate_handler=slow_estimate,
                timeout_overrides={"fund.estimate.get": 0.03},
            ),
            include_disclosure_changes=False,
            include_alternatives=False,
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertEqual(run["status"], "partial")
        estimate_step = next(item for item in run["steps"] if item["step_key"] == "fund_estimate")
        self.assertEqual(estimate_step["status"], "failed")
        self.assertEqual(estimate_step["error_code"], "TOOL_TIMEOUT")
        self.assertEqual(len(run["evidence"]), 1)

        time.sleep(0.22)
        self.assertEqual(len(self.repository.get_run(run["id"])["evidence"]), 1)

    def test_running_tool_can_be_cancelled_without_becoming_a_failure(self):
        def slow_analysis(payload):
            time.sleep(0.3)
            return _analysis(payload)

        payload = {
            "intent": "fund_deep_research",
            "code": "001480",
            "months": 36,
            "include_estimate": False,
            "include_disclosure_changes": False,
            "include_alternatives": False,
        }
        created, _ = self.repository.create_run("fund_deep_research", payload)
        claimed = self.repository.claim_next_run("cancel-test-worker")
        runner = AgentWorkflowRunner(
            self.repository,
            _registry(
                analysis_handler=slow_analysis,
                timeout_overrides={"fund.analysis.get": 2},
            ),
        )
        execution = threading.Thread(target=runner.execute, args=(claimed,))
        execution.start()

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            step = self.repository.get_step(created["id"], "fund_analysis")
            if step and step["status"] == "running":
                break
            time.sleep(0.01)
        else:
            self.fail("fund_analysis step did not start")

        self.repository.request_cancel(created["id"])
        execution.join(timeout=1)
        self.assertFalse(execution.is_alive())

        run = self.repository.get_run(created["id"])
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(run["steps"][0]["status"], "cancelled")
        self.assertEqual(run["steps"][0]["error_code"], "TOOL_CANCELLED")
        self.assertEqual(run["evidence"], [])
        event_types = [item["event_type"] for item in self.repository.list_audit_events(run["id"])]
        self.assertIn("tool.call.cancelled", event_types)

    def test_audit_verification_detects_persisted_event_tampering(self):
        run = self._run(
            include_estimate=False,
            include_disclosure_changes=False,
            include_alternatives=False,
        )
        connection = sqlite3.connect(self.repository.db_path)
        try:
            connection.execute(
                """
                UPDATE agent_audit_events
                SET details_json='{"tampered":true}'
                WHERE run_id=? AND sequence_no=2
                """,
                (run["id"],),
            )
            connection.commit()
        finally:
            connection.close()

        verification = self.repository.verify_audit_chain(run["id"])
        self.assertFalse(verification["verified"])
        self.assertEqual(verification["failing_sequence"], 2)

    def test_idempotency_cancel_and_recovery_are_durable(self):
        payload = {"code": "001480", "months": 36}
        first, first_created = self.repository.create_run(
            "fund_deep_research", payload, idempotency_key="same-request"
        )
        second, second_created = self.repository.create_run(
            "fund_deep_research", payload, idempotency_key="same-request"
        )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])

        cancelled = self.repository.request_cancel(first["id"])
        self.assertEqual(cancelled["status"], "cancelled")

        recoverable, _ = self.repository.create_run(
            "fund_deep_research", {"code": "000002", "months": 36}
        )
        claimed = self.repository.claim_next_run("crashed-worker")
        self.assertEqual(claimed["id"], recoverable["id"])
        self.repository.start_step(
            claimed["id"],
            step_key="fund_analysis",
            sequence_no=1,
            tool_name="fund.analysis.get",
            tool_version="1.0.0",
            required=True,
            input_payload={"code": "000002", "months": 36},
        )
        self.assertEqual(self.repository.recover_interrupted_runs(), 1)
        recovered = self.repository.get_run(claimed["id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["steps"][0]["status"], "queued")

        self.repository.request_cancel(recovered["id"])
        cancel_on_restart, _ = self.repository.create_run(
            "fund_deep_research", {"code": "000003", "months": 36}
        )
        claimed = self.repository.claim_next_run("second-crashed-worker")
        self.assertEqual(claimed["id"], cancel_on_restart["id"])
        running_cancel = self.repository.request_cancel(claimed["id"])
        self.assertEqual(running_cancel["status"], "running")
        self.assertTrue(running_cancel["cancel_requested"])
        self.assertEqual(self.repository.recover_interrupted_runs(), 1)
        self.assertEqual(self.repository.get_run(claimed["id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
