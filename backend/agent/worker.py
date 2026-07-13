# -*- coding: utf-8 -*-
"""Single-process durable worker for the first Agent release.

Queued and completed state lives in the repository, not in the thread. A
process restart requeues interrupted work and reuses completed tool evidence.
The PRD keeps Temporal as the production-scale workflow target.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Callable

from .outcome_worker import OutcomeScheduleWorker
from .outcomes import DecisionOutcomeService
from .registry import build_default_registry
from .repository import AgentRepository
from .strategy_governance import StrategyGovernanceService
from .strategy_shadow_outcomes import StrategyShadowOutcomeService
from .strategy_shadow_worker import StrategyShadowOutcomeWorker
from .synthesis import InvestmentSynthesisService
from .workflow import AgentWorkflowRunner


logger = logging.getLogger("agent-worker")


class AgentWorker:
    def __init__(
        self,
        repository: AgentRepository,
        runner: AgentWorkflowRunner,
        *,
        poll_interval: float = 0.75,
        concurrency: int = 1,
        terminal_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.poll_interval = max(0.1, float(poll_interval))
        self.concurrency = max(1, min(4, int(concurrency)))
        self.terminal_callback = terminal_callback
        self.worker_id = f"worker_{uuid.uuid4().hex}"
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._start_lock = threading.Lock()

    def start(self) -> bool:
        with self._start_lock:
            if any(thread.is_alive() for thread in self._threads):
                return False
            recovered = self.repository.recover_interrupted_runs()
            if recovered:
                logger.warning("恢复 %s 个中断的 Agent Run", recovered)
            self._stop.clear()
            self._threads = []
            for index in range(self.concurrency):
                lane_id = self.worker_id if self.concurrency == 1 else f"{self.worker_id}_{index + 1}"
                thread = threading.Thread(
                    target=self._loop,
                    args=(lane_id,),
                    name=f"agent-worker-{index + 1}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)
            logger.info("Agent Worker 已启动:%s concurrency=%s", self.worker_id, self.concurrency)
            return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once(worker_id=worker_id):
                    self._stop.wait(self.poll_interval)
            except Exception:
                logger.exception("Agent Worker 循环异常")
                time.sleep(min(2.0, self.poll_interval * 2))

    def run_once(self, *, worker_id: str | None = None) -> bool:
        lane_id = worker_id or self.worker_id
        run = self.repository.claim_next_run(lane_id)
        if run is None:
            return False
        finished = self.runner.execute(run)
        if self.terminal_callback is not None:
            try:
                self.terminal_callback(finished)
            except Exception:
                logger.exception("Agent Run 终态回调失败:%s", run.get("id"))
        return True


repository = AgentRepository()
strategy_governance = StrategyGovernanceService(repository)
strategy_governance.seed_defaults()
synthesis_service = InvestmentSynthesisService()
registry = build_default_registry(strategy_governance, synthesis_service)
runner = AgentWorkflowRunner(repository, registry)
outcome_service = DecisionOutcomeService(repository, registry)
strategy_shadow_service = StrategyShadowOutcomeService(repository, registry)


def _ensure_terminal_observations(run: dict) -> None:
    try:
        outcome_service.ensure_schedule_for_run(run, actor_id="agent-worker")
    except Exception:
        logger.exception("个人决策 Outcome 调度创建失败:%s", run.get("id"))
    try:
        strategy_shadow_service.ensure_enrollment(run, actor_id="agent-worker")
    except Exception:
        logger.exception("策略 Shadow Outcome 入组失败:%s", run.get("id"))


worker = AgentWorker(
    repository,
    runner,
    poll_interval=float(os.getenv("AGENT_WORKER_POLL_SECONDS", "0.75")),
    concurrency=int(os.getenv("AGENT_WORKER_CONCURRENCY", "2")),
    terminal_callback=_ensure_terminal_observations,
)
outcome_worker = OutcomeScheduleWorker(
    repository,
    outcome_service,
    poll_interval=float(os.getenv("AGENT_OUTCOME_POLL_SECONDS", "30")),
    lease_seconds=int(os.getenv("AGENT_OUTCOME_LEASE_SECONDS", "120")),
)
strategy_shadow_worker = StrategyShadowOutcomeWorker(
    repository,
    strategy_shadow_service,
    poll_interval=float(os.getenv("AGENT_STRATEGY_SHADOW_POLL_SECONDS", "30")),
    lease_seconds=int(os.getenv("AGENT_STRATEGY_SHADOW_LEASE_SECONDS", "120")),
)


def start_worker() -> bool:
    if os.getenv("AGENT_WORKER_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return False
    run_started = worker.start()
    outcome_started = outcome_worker.start()
    strategy_shadow_started = strategy_shadow_worker.start()
    return run_started or outcome_started or strategy_shadow_started
