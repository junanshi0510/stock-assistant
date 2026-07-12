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

from .registry import build_default_registry
from .repository import AgentRepository
from .workflow import AgentWorkflowRunner


logger = logging.getLogger("agent-worker")


class AgentWorker:
    def __init__(
        self,
        repository: AgentRepository,
        runner: AgentWorkflowRunner,
        *,
        poll_interval: float = 0.75,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.poll_interval = max(0.1, float(poll_interval))
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
                run = self.repository.claim_next_run(self.worker_id)
                if run is None:
                    self._stop.wait(self.poll_interval)
                    continue
                self.runner.execute(run)
            except Exception:
                logger.exception("Agent Worker 循环异常")
                time.sleep(min(2.0, self.poll_interval * 2))


repository = AgentRepository()
registry = build_default_registry()
runner = AgentWorkflowRunner(repository, registry)
worker = AgentWorker(
    repository,
    runner,
    poll_interval=float(os.getenv("AGENT_WORKER_POLL_SECONDS", "0.75")),
)


def start_worker() -> bool:
    if os.getenv("AGENT_WORKER_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return False
    return worker.start()
