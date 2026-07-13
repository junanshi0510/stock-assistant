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
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.registry import ToolDefinition, ToolRegistry  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from agent.workflow import AgentWorkflowRunner  # noqa: E402
from routers import agent as agent_router  # noqa: E402
from strategies.personalized_fund_decision import (  # noqa: E402
    evaluate_personalized_fund_decision,
)


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
        "conditioned_forward": {
            "strategy_id": "fund_conditioned_forward_return",
            "strategy_version": "1.0.0",
            "status": "evaluated",
            "decision": "research",
            "signal": {"direction": "positive", "strength": 28},
            "confidence": {"level": "low", "reasons": ["analog_sample_count:8"]},
            "suitability": {
                "status": "not_evaluated",
                "conflicts": ["user_profile_not_in_scope", "portfolio_exposure_not_in_scope"],
            },
            "condition": {
                "as_of": "2026-07-10",
                "latest_nav": 1.2345,
                "ma60": 1.20,
                "trend": "above_ma60",
                "drawdown_band": "normal_pullback",
                "current_drawdown": -4.5,
                "return_3m": 8.2,
            },
            "primary_horizon": "6m",
            "horizons": [{
                "horizon": "6m",
                "observation_days": 126,
                "status": "available",
                "analog": {
                    "sample_count": 8,
                    "positive_rate": 64.0,
                    "median_return": 7.5,
                    "p25_return": -2.0,
                    "p75_return": 12.0,
                    "worst_return": -9.0,
                    "best_return": 20.0,
                    "sample_start": "2020-01-31",
                    "sample_end": "2025-01-31",
                },
                "baseline": {
                    "sample_count": 30,
                    "positive_rate": 55.0,
                    "median_return": 4.0,
                },
                "edge": {"positive_rate": 9.0, "median_return": 3.5},
            }],
            "coverage": {
                "observation_count": 500,
                "start_date": "2020-01-02",
                "end_date": "2026-07-10",
                "monthly_candidate_count": 30,
            },
            "invalidation_conditions": [
                {"field": "trend", "current": "above_ma60", "invalid_when": "changes"},
            ],
            "thesis": [],
            "counter_evidence": [],
            "risks": ["historical_tail_loss_remains_possible"],
            "next_research_actions": ["apply_user_risk_and_portfolio_constraints"],
            "evidence_ids": [],
            "method": {"sampling": "calendar_month_last_observation"},
            "limitations": ["historical_results_are_not_forecasts"],
        },
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
        "level_recurrence": {
            "metric_id": "asset_level_recurrence",
            "metric_version": "1.0.0",
            "asset_type": "fund",
            "code": "001480",
            "status": "crossed_between",
            "target": {
                "label": "盘中估算净值",
                "value": 1.22,
                "as_of": "2026-07-11 14:30",
                "source": "真实盘中估值测试快照",
            },
            "history": {
                "source": "真实确认净值测试快照",
                "adjustment": "confirmed_unit_nav",
                "granularity": "confirmed_nav_date",
                "observation_count": 500,
                "start_date": "2020-01-02",
                "end_date": "2026-07-10",
            },
            "occurrence": {
                "kind": "crossing_interval",
                "from_date": "2026-06-01",
                "from_value": 1.21,
                "to_date": "2026-06-02",
                "to_value": 1.23,
                "direction": "up",
                "calendar_days_ago": 39,
            },
            "nearest": {"date": "2026-06-01", "value": 1.21, "difference": -0.01},
            "policy": "估值不是确认净值。",
        },
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


def _market_profile(_payload):
    return {
        "status": "available",
        "source": "真实基金市场元数据测试快照",
        "source_url": "https://example.test/fund/001480",
        "resolution_status": "identified",
        "fund": {"code": "001480", "name": "测试基金", "fund_type": "混合型", "is_qdii": False},
        "market": {
            "primary": "mainland",
            "label": "中国内地或未明确跨境",
            "detected_markets": [],
            "required_permissions": ["mainland"],
            "cross_border": False,
            "currency_risk": False,
        },
        "valuation": {
            "confirmed_nav_lag": "以基金管理人确认净值日为准",
            "intraday_estimate_policy": "估值不替代确认净值",
        },
        "benchmark_names": ["沪深300"],
    }


def _portfolio_context(_payload):
    return {
        "status": "available",
        "source": "confirmed_test_portfolio",
        "data_classification": "private_financial",
        "profile": {
            "configured": True,
            "risk": "balanced",
            "horizon": "mid_long",
            "monthly_budget": 1000,
            "max_single_ratio": 35,
            "max_equity_ratio": 90,
            "max_industry_ratio": 50,
            "max_drawdown_pct": 30,
            "allowed_fund_markets": ["mainland"],
            "accept_fx_risk": False,
            "profile_version_id": "ips_test",
        },
        "portfolio": {
            "holding_count": 2,
            "amount_complete": True,
            "total_amount": 10000,
            "holdings_sha256": "b" * 64,
        },
        "target_holding": {
            "exists": True,
            "amount": 1000,
            "ratio": 10,
            "profit": -50,
            "profit_rate": -5,
        },
        "data_gaps": [],
    }


def _portfolio_exposure(_payload):
    return {
        "schema_version": "portfolio_exposure_snapshot.v1",
        "status": "complete",
        "source": "真实基金披露测试快照",
        "evaluated_on": "2026-07-13",
        "profile_version_id": "ips_test",
        "target_code": "001480",
        "holdings_sha256": "b" * 64,
        "summary": {
            "equity": {
                "lower_amount": 5000,
                "upper_amount": 5000,
                "lower_ratio": 50,
                "upper_ratio": 50,
            },
            "industry": {
                "unknown_equity_amount": 500,
                "max_lower_ratio": 15,
                "max_upper_ratio": 20,
            },
        },
        "industries": [{"name": "信息技术", "lower_amount": 1000, "lower_ratio": 10}],
        "target": {
            "status": "available",
            "equity_interval": {"lower_ratio": 80, "upper_ratio": 80, "exact": True},
            "industry_unknown_ratio": 10,
            "industries": [{"name": "信息技术", "lower_ratio": 25, "upper_ratio": 35}],
        },
        "quality": {"decision_eligible": True, "reasons": []},
        "snapshot": {"id": "exposure_test", "payload_sha256": "c" * 64},
        "integrity": {"verified": True, "payload_sha256": "c" * 64},
    }


def _personalized_decision(payload):
    result = evaluate_personalized_fund_decision(
        payload["analysis"],
        payload["context"],
        payload["market_profile"],
        payload["exposure"],
        planned_amount=payload.get("planned_amount"),
    )
    result["input_evidence_ids"] = payload.get("input_evidence_ids") or []
    return result


def _registry(
    alternatives_handler=_alternatives,
    analysis_handler=_analysis,
    estimate_handler=_estimate,
    exposure_handler=_portfolio_exposure,
    timeout_overrides=None,
):
    registry = ToolRegistry()
    timeout_overrides = timeout_overrides or {}
    for name, timeout, handler in (
        ("fund.analysis.get", 45, analysis_handler),
        ("fund.market_profile.get", 25, _market_profile),
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
    registry.register(ToolDefinition(
        name="portfolio.context.get",
        version="1.0.0",
        description="portfolio.context.get",
        risk_level="R1",
        timeout_seconds=5,
        handler=_portfolio_context,
    ))
    registry.register(ToolDefinition(
        name="portfolio.exposure.snapshot",
        version="1.0.0",
        description="portfolio.exposure.snapshot",
        risk_level="R1",
        timeout_seconds=timeout_overrides.get("portfolio.exposure.snapshot", 5),
        handler=exposure_handler,
    ))
    registry.register(ToolDefinition(
        name="fund.personalized_decision.evaluate",
        version="1.2.0",
        description="fund.personalized_decision.evaluate",
        risk_level="R1",
        timeout_seconds=5,
        handler=_personalized_decision,
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
            "include_portfolio_context": True,
            "planned_amount": 1000,
            **overrides,
        }
        created, is_new = self.repository.create_run("fund_deep_research", payload)
        self.assertTrue(is_new)
        claimed = self.repository.claim_next_run("test-worker")
        self.assertEqual(claimed["id"], created["id"])
        AgentWorkflowRunner(self.repository, registry or _registry()).execute(claimed)
        return self.repository.get_run(created["id"])

    def test_new_fund_research_defaults_to_five_year_strategy_window(self):
        request = agent_router.CreateAgentRunRequest(code="001480")
        self.assertEqual(request.months, 60)
        steps = AgentWorkflowRunner(self.repository, _registry())._fund_steps({"code": "001480"})
        self.assertEqual(steps[0].input_payload["months"], 60)
        self.assertEqual(steps[1].key, "fund_market_profile")
        self.assertEqual(steps[2].key, "portfolio_context")
        self.assertEqual(steps[3].key, "portfolio_exposure")

    def test_completed_run_persists_claims_evidence_and_hash_chained_audit(self):
        run = self._run()

        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["steps"]), 8)
        self.assertEqual(len(run["evidence"]), 8)
        self.assertGreaterEqual(len(run["claims"]), 13)
        self.assertEqual(run["result"]["fund"]["code"], "001480")
        self.assertEqual(run["result"]["schema_version"], "fund_deep_research.v3")
        self.assertEqual(run["result"]["alternatives"][0]["code"], "000001")
        self.assertEqual(
            run["result"]["strategy"]["strategy_id"],
            "fund_conditioned_forward_return",
        )
        self.assertEqual(
            run["result"]["strategy"]["evidence_ids"],
            [run["evidence"][0]["id"]],
        )
        self.assertEqual(
            run["result"]["personalized_decision"]["strategy_id"],
            "personalized_fund_decision",
        )
        self.assertEqual(
            run["result"]["personalized_decision"]["decision"]["action"],
            "consider_tranche",
        )
        self.assertEqual(
            len(run["result"]["personalized_decision"]["evidence_ids"]),
            5,
        )
        self.assertEqual(run["exposure_snapshot_id"], "exposure_test")
        estimate_step = next(item for item in run["steps"] if item["step_key"] == "fund_estimate")
        estimate_evidence = next(
            item for item in run["evidence"] if item["step_id"] == estimate_step["id"]
        )
        self.assertEqual(
            run["result"]["level_recurrence"]["evidence_ids"],
            [estimate_evidence["id"]],
        )
        self.assertEqual(run["result"]["level_recurrence"]["metric_version"], "1.0.0")
        self.assertEqual(run["result"]["level_recurrence"]["status"], "crossed_between")
        fact_labels = {item["label"] for item in run["result"]["facts"]}
        self.assertIn("历史相似条件后 6 个月正收益比例", fact_labels)
        self.assertIn("历史相似条件后 6 个月中位收益", fact_labels)

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

    def test_run_integrity_rejects_tampered_exposure_snapshot_binding(self):
        run = self._run()
        self.assertTrue(self.repository.verify_run_evidence_integrity(run["id"])["verified"])

        with self.repository._connect() as connection:
            connection.execute(
                "UPDATE agent_runs SET exposure_snapshot_id='exposure_tampered' WHERE id=?",
                (run["id"],),
            )

        result = self.repository.verify_run_evidence_integrity(run["id"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exposure_snapshot_binding_mismatch")

    def test_required_analysis_failure_stops_run(self):
        def unavailable_analysis(_payload):
            raise RuntimeError("真实净值当前不可用")

        run = self._run(registry=_registry(analysis_handler=unavailable_analysis))

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "REQUIRED_TOOL_FAILED")
        self.assertIsNone(run["result"])
        self.assertEqual(len(run["evidence"]), 0)

    def test_optional_tool_timeout_is_partial_and_cannot_write_late_evidence(self):
        handler_started = threading.Event()
        release_handler = threading.Event()

        def slow_estimate(_payload):
            handler_started.set()
            release_handler.wait(timeout=2)
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

        self.assertTrue(handler_started.is_set())
        self.assertLess(elapsed, 0.5)
        self.assertEqual(run["status"], "partial")
        estimate_step = next(item for item in run["steps"] if item["step_key"] == "fund_estimate")
        self.assertEqual(estimate_step["status"], "failed")
        self.assertEqual(estimate_step["error_code"], "TOOL_TIMEOUT")
        self.assertEqual(len(run["evidence"]), 5)

        release_handler.set()
        time.sleep(0.05)
        self.assertEqual(len(self.repository.get_run(run["id"])["evidence"]), 5)

    def test_exposure_timeout_cannot_persist_a_late_orphan_snapshot(self):
        handler_started = threading.Event()
        release_handler = threading.Event()

        def slow_exposure(payload):
            handler_started.set()
            release_handler.wait(timeout=2)
            result = _portfolio_exposure(payload)
            result.pop("snapshot", None)
            result.pop("integrity", None)
            return result

        with patch("agent.workflow.portfolio_exposure.persist_exposure_snapshot") as persist:
            run = self._run(
                registry=_registry(
                    exposure_handler=slow_exposure,
                    timeout_overrides={"portfolio.exposure.snapshot": 0.03},
                ),
                include_estimate=False,
                include_disclosure_changes=False,
                include_alternatives=False,
            )
            self.assertTrue(handler_started.is_set())
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_code"], "REQUIRED_TOOL_FAILED")
            persist.assert_not_called()

            release_handler.set()
            time.sleep(0.05)
            persist.assert_not_called()

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

    def test_run_history_is_scoped_filtered_and_cursor_paginated(self):
        created_ids = []
        for code in ("000001", "000002", "000003"):
            run, _ = self.repository.create_run(
                "fund_deep_research",
                {"code": code, "months": 36},
                tenant_id="tenant-a",
                user_id="user-a",
            )
            created_ids.append(run["id"])
        foreign, _ = self.repository.create_run(
            "fund_deep_research",
            {"code": "999999", "months": 36},
            tenant_id="tenant-b",
            user_id="user-b",
        )

        first_page, has_more = self.repository.list_runs(
            tenant_id="tenant-a",
            user_id="user-a",
            limit=2,
        )
        self.assertTrue(has_more)
        self.assertEqual(len(first_page), 2)
        self.assertNotIn(foreign["id"], {item["id"] for item in first_page})

        last = first_page[-1]
        second_page, second_has_more = self.repository.list_runs(
            tenant_id="tenant-a",
            user_id="user-a",
            limit=2,
            before=(last["created_at"], last["id"]),
        )
        self.assertFalse(second_has_more)
        combined_ids = {item["id"] for item in first_page + second_page}
        self.assertEqual(combined_ids, set(created_ids))

        self.repository.request_cancel(created_ids[0], actor_id="user-a")
        cancelled_page, _ = self.repository.list_runs(
            tenant_id="tenant-a",
            user_id="user-a",
            limit=10,
            status="cancelled",
        )
        self.assertEqual([item["id"] for item in cancelled_page], [created_ids[0]])

        code_page, _ = self.repository.list_runs(
            tenant_id="tenant-a",
            user_id="user-a",
            limit=10,
            code="000002",
        )
        self.assertEqual([item["input"]["code"] for item in code_page], ["000002"])

    def test_terminal_run_rerun_is_idempotent_and_keeps_parent_audit(self):
        source = self._run(
            include_estimate=False,
            include_disclosure_changes=False,
            include_alternatives=False,
        )
        with patch.object(agent_router, "repository", self.repository), \
             patch.object(agent_router, "start_worker"):
            first = agent_router.rerun_agent_run(
                source["id"],
                idempotency_key="rerun-same-request",
            )
            retry = agent_router.rerun_agent_run(
                source["id"],
                idempotency_key="rerun-same-request",
            )

        self.assertTrue(first["created"])
        self.assertFalse(retry["created"])
        self.assertEqual(first["run"]["id"], retry["run"]["id"])
        self.assertNotEqual(first["run"]["id"], source["id"])
        self.assertEqual(first["run"]["parent_run_id"], source["id"])
        self.assertEqual(first["run"]["input"], source["input"])
        created_event = self.repository.list_audit_events(first["run"]["id"])[0]
        self.assertEqual(created_event["details"]["parent_run_id"], source["id"])

    def test_running_run_cannot_be_rerun(self):
        source, _ = self.repository.create_run(
            "fund_deep_research",
            {"code": "001480", "months": 36},
        )
        with patch.object(agent_router, "repository", self.repository), \
             self.assertRaises(agent_router.HTTPException) as raised:
            agent_router.rerun_agent_run(source["id"], idempotency_key="running-rerun")
        self.assertEqual(raised.exception.status_code, 409)

    def test_rerun_comparison_reports_persisted_metric_and_conclusion_changes(self):
        source = self._run(
            include_estimate=False,
            include_disclosure_changes=False,
            include_alternatives=False,
        )

        def changed_analysis(payload):
            result = _analysis(payload)
            result["as_of"] = "2026-07-11"
            result["metrics"]["return_1y"] = 18.9
            result["timing"] = {"score": 71, "label": "分批投入"}
            result["playbook"]["role"]["risk_band"] = "进取高波动"
            return result

        child, _ = self.repository.create_run(
            source["intent"],
            source["input"],
            tenant_id=source["tenant_id"],
            user_id=source["user_id"],
            parent_run_id=source["id"],
        )
        claimed = self.repository.claim_next_run("comparison-worker")
        AgentWorkflowRunner(
            self.repository,
            _registry(analysis_handler=changed_analysis),
        ).execute(claimed)

        with patch.object(agent_router, "repository", self.repository):
            comparison = agent_router.get_agent_run_comparison(child["id"])

        self.assertEqual(comparison["parent_run_id"], source["id"])
        self.assertEqual(comparison["period"]["previous_as_of"], "2026-07-10")
        self.assertEqual(comparison["period"]["current_as_of"], "2026-07-11")
        one_year = next(item for item in comparison["metrics"] if item["label"] == "近 1 年收益")
        self.assertEqual(one_year["previous"], 16.4)
        self.assertEqual(one_year["current"], 18.9)
        self.assertEqual(one_year["delta"], 2.5)
        self.assertEqual(one_year["direction"], "up")
        risk_band = next(item for item in comparison["dimensions"] if item["key"] == "risk_band")
        self.assertTrue(risk_band["changed"])
        self.assertFalse(comparison["summary"]["stable"])
        self.assertTrue(comparison["integrity"]["current"]["verified"])
        self.assertTrue(comparison["integrity"]["parent"]["verified"])

    def test_rerun_comparison_is_stable_when_saved_results_are_unchanged(self):
        source = self._run(
            include_estimate=False,
            include_disclosure_changes=False,
            include_alternatives=False,
        )
        child, _ = self.repository.create_run(
            source["intent"],
            source["input"],
            parent_run_id=source["id"],
        )
        claimed = self.repository.claim_next_run("stable-comparison-worker")
        AgentWorkflowRunner(self.repository, _registry()).execute(claimed)

        with patch.object(agent_router, "repository", self.repository):
            comparison = agent_router.get_agent_run_comparison(child["id"])

        self.assertTrue(comparison["summary"]["stable"])
        self.assertEqual(comparison["summary"]["metric_changed_count"], 0)
        self.assertEqual(comparison["summary"]["dimension_changed_count"], 0)

    def test_rerun_comparison_rejects_tampered_evidence(self):
        source = self._run(
            include_estimate=False,
            include_disclosure_changes=False,
            include_alternatives=False,
        )
        child, _ = self.repository.create_run(
            source["intent"],
            source["input"],
            parent_run_id=source["id"],
        )
        claimed = self.repository.claim_next_run("tamper-comparison-worker")
        AgentWorkflowRunner(self.repository, _registry()).execute(claimed)

        connection = sqlite3.connect(self.repository.db_path)
        try:
            connection.execute(
                "UPDATE agent_evidence SET payload_json='{}' WHERE run_id=?",
                (source["id"],),
            )
            connection.commit()
        finally:
            connection.close()

        integrity = self.repository.verify_run_evidence_integrity(source["id"])
        self.assertFalse(integrity["verified"])
        self.assertEqual(integrity["reason"], "payload_hash_mismatch")
        with patch.object(agent_router, "repository", self.repository), \
             self.assertRaises(agent_router.HTTPException) as raised:
            agent_router.get_agent_run_comparison(child["id"])
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
