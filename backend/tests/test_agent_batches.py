# -*- coding: utf-8 -*-

import tempfile
import threading
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.batches import summarize_batch
from agent.repository import AgentQueueCapacityError, AgentRepository
from agent.worker import AgentWorker
from routers import agent as agent_router


def _result(code: str, *, holding_code: str = "00700", model: bool = True) -> dict:
    return {
        "fund": {
            "code": code,
            "name": f"测试基金{code}",
            "as_of": "2026-07-10",
            "unit_nav": 1.2345,
            "trend_state": "震荡观察",
        },
        "conclusion": {
            "role": "卫星进攻仓",
            "risk_band": "进攻型",
            "timing_label": "暂缓观察",
        },
        "facts": [
            {"label": "近 1 年收益", "value": 12.3, "unit": "%"},
            {"label": "当前回撤", "value": -8.4, "unit": "%"},
        ],
        "strategy": {"decision": "hold_review", "confidence": {"level": "low"}},
        "market_profile": {
            "market": {"primary": "hong_kong", "label": "中国香港", "cross_border": True},
        },
        "market_intelligence": {
            "status": "available",
            "market": {"primary": "hong_kong", "label": "中国香港"},
            "holding_pulse": {
                "items": [
                    {
                        "code": holding_code,
                        "name": "腾讯控股",
                        "market": "hong_kong",
                        "nav_ratio": 8.5 if code == "013403" else 6.2,
                    },
                ],
            },
            "news": {"count": 2},
        },
        "ai_synthesis": (
            {
                "status": "available",
                "synthesis": {"action": "hold_review", "confidence": "low"},
            }
            if model
            else {"status": "unavailable", "reason_code": "model_not_configured"}
        ),
        "personalized_decision": None,
    }


class _ConcurrentRunner:
    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.two_started = threading.Event()

    def execute(self, run: dict) -> dict:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= 2:
                self.two_started.set()
        self.two_started.wait(1.5)
        finished = self.repository.finish_run(
            run["id"],
            status="completed",
            result=_result(run["input"]["code"]),
        )
        with self.lock:
            self.active -= 1
        return finished


class AgentBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_batch(self, *, key: str = "batch-key"):
        return self.repository.create_batch(
            "fund_deep_research",
            {
                "codes": ["013403", "014089"],
                "months": 60,
                "include_market_intelligence": True,
                "include_ai_synthesis": True,
                "include_portfolio_context": False,
                "question": "比较两只基金的真实风险和持仓重合度。",
            },
            idempotency_key=key,
        )

    def test_batch_creation_is_atomic_and_idempotent(self):
        batch, created = self._create_batch()
        repeated, repeated_created = self._create_batch()

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(batch["id"], repeated["id"])
        self.assertEqual([item["code"] for item in batch["items"]], ["013403", "014089"])
        self.assertTrue(all(item["run"]["input"]["batch_id"] == batch["id"] for item in batch["items"]))
        self.assertEqual(self.repository.count_active_runs(), 2)
        for item in batch["items"]:
            events = self.repository.list_audit_events(item["run"]["id"])
            self.assertEqual(events[0]["details"]["batch_id"], batch["id"])

    def test_batch_capacity_is_rechecked_inside_creation_transaction(self):
        batch, created = self.repository.create_batch(
            "fund_deep_research",
            {"codes": ["013403", "014089"], "question": "第一批真实基金研究任务。"},
            idempotency_key="capacity-first",
            max_active_runs=2,
        )
        self.assertTrue(created)
        repeated, repeated_created = self.repository.create_batch(
            "fund_deep_research",
            {"codes": ["013403", "014089"], "question": "第一批真实基金研究任务。"},
            idempotency_key="capacity-first",
            max_active_runs=2,
        )
        self.assertFalse(repeated_created)
        self.assertEqual(repeated["id"], batch["id"])

        with self.assertRaises(AgentQueueCapacityError):
            self.repository.create_batch(
                "fund_deep_research",
                {"codes": ["001056", "040046"], "question": "第二批真实基金研究任务。"},
                idempotency_key="capacity-second",
                max_active_runs=2,
            )
        self.assertEqual(len(self.repository.list_batches(tenant_id="public", user_id="anonymous")), 1)

    def test_batch_summary_uses_terminal_runs_and_disclosed_overlap_lower_bound(self):
        batch, _ = self._create_batch()
        first, second = batch["items"]
        self.repository.finish_run(first["run"]["id"], status="completed", result=_result("013403"))
        self.repository.finish_run(
            second["run"]["id"],
            status="partial",
            result=_result("014089", model=False),
        )

        summary = summarize_batch(self.repository.get_batch(batch["id"]))
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["progress"]["terminal"], 2)
        self.assertEqual(summary["summary"]["model_available"], 1)
        overlap = summary["holding_overlap"]
        self.assertEqual(overlap["status"], "available")
        self.assertEqual(overlap["pairs"][0]["shared_holding_count"], 1)
        self.assertAlmostEqual(overlap["pairs"][0]["overlap_lower_bound_pct"], 6.2)
        self.assertIn("不推断", overlap["policy"])

    def test_batch_request_rejects_duplicates(self):
        with self.assertRaises(ValidationError):
            agent_router.CreateAgentBatchRequest(codes=["013403", "013403"])

    def test_batch_api_pins_profile_and_returns_aggregate(self):
        profile = {"configured": True, "profile_version_id": "ips_batch_v1"}
        with (
            patch.object(agent_router, "repository", self.repository),
            patch.object(agent_router, "start_worker"),
            patch.object(agent_router.storage, "get_investment_profile", return_value=profile),
        ):
            response = agent_router.create_agent_batch(
                agent_router.CreateAgentBatchRequest(codes=["013403", "014089"]),
                idempotency_key="api-batch-key",
            )

        self.assertTrue(response["created"])
        self.assertEqual(response["batch"]["progress"]["total"], 2)
        stored = self.repository.get_batch(response["batch"]["id"])
        self.assertTrue(all(item["run"]["profile_version_id"] == "ips_batch_v1" for item in stored["items"]))

    def test_worker_pool_executes_two_batch_children_concurrently(self):
        batch, _ = self._create_batch()
        runner = _ConcurrentRunner(self.repository)
        worker = AgentWorker(
            self.repository,
            runner,
            poll_interval=0.01,
            concurrency=2,
        )
        worker.start()
        try:
            self.assertTrue(runner.two_started.wait(2.0))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                current = self.repository.get_batch(batch["id"])
                if all(item["run"]["status"] == "completed" for item in current["items"]):
                    break
                time.sleep(0.02)
            self.assertGreaterEqual(runner.max_active, 2)
            self.assertTrue(all(
                item["run"]["status"] == "completed"
                for item in self.repository.get_batch(batch["id"])["items"]
            ))
        finally:
            worker.stop()


if __name__ == "__main__":
    unittest.main()
