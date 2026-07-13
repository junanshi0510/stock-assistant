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
from .workflow import AgentWorkflowRunner


logger = logging.getLogger("agent-worker")


class AgentWorker:
    def __init__(
        self,
        repository: AgentRepository,
        runner: AgentWorkflowRunner,
        *,
        poll_interval: float = 0.75,
        terminal_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.poll_interval = max(0.1, float(poll_interval))
        self.terminal_callback = terminal_callback
        self.worker_id = f"worker_{uuid.uuid4().hex}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_lock = threading.Lock()

    def start(self) -> bool:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return False
            recovered = self.repository.recover_interrupted_runs()
            if recovered:
                logger.warning("恢复 %s 个中断的 Agent Run", recovered)
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="agent-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info("Agent Worker 已启动:%s", self.worker_id)
            return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.poll_interval)
            except Exception:
                logger.exception("Agent Worker 循环异常")
                time.sleep(min(2.0, self.poll_interval * 2))

    def run_once(self) -> bool:
        run = self.repository.claim_next_run(self.worker_id)
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
registry = build_default_registry(strategy_governance)
runner = AgentWorkflowRunner(repository, registry)
outcome_service = DecisionOutcomeService(repository, registry)


def _ensure_outcome_schedule(run: dict) -> None:
    outcome_service.ensure_schedule_for_run(run, actor_id="agent-worker")


worker = AgentWorker(
    repository,
    runner,
    poll_interval=float(os.getenv("AGENT_WORKER_POLL_SECONDS", "0.75")),
    terminal_callback=_ensure_outcome_schedule,
)
outcome_worker = OutcomeScheduleWorker(
    repository,
    outcome_service,
    poll_interval=float(os.getenv("AGENT_OUTCOME_POLL_SECONDS", "30")),
    lease_seconds=int(os.getenv("AGENT_OUTCOME_LEASE_SECONDS", "120")),
)


def start_worker() -> bool:
    if os.getenv("AGENT_WORKER_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return False
    run_started = worker.start()
    outcome_started = outcome_worker.start()
    return run_started or outcome_started
