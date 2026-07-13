# -*- coding: utf-8 -*-
"""Lease-based durable worker for scheduled decision-outcome observations."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from .outcomes import DecisionOutcomeService, OutcomeEvaluationError
from .repository import AgentRepository


logger = logging.getLogger("agent-outcome-worker")


class OutcomeScheduleWorker:
    def __init__(
        self,
        repository: AgentRepository,
        service: DecisionOutcomeService,
        *,
        poll_interval: float = 30.0,
        lease_seconds: int = 120,
    ) -> None:
        self.repository = repository
        self.service = service
        self.poll_interval = max(1.0, float(poll_interval))
        self.lease_seconds = max(60, int(lease_seconds))
        self.worker_id = f"outcome_worker_{uuid.uuid4().hex}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_lock = threading.Lock()

    def start(self) -> bool:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return False
            try:
                backfilled = self.service.backfill_eligible_schedules(limit=100)
                if backfilled:
                    logger.info("已为 %s 个既有方向性 Run 建立结果观察计划", backfilled)
            except Exception:
                logger.exception("既有结果观察计划回填失败")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="agent-outcome-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info("结果观察 Worker 已启动:%s", self.worker_id)
            return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def run_once(self, *, now=None) -> bool:
        schedule = self.repository.claim_due_outcome_schedule(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            now=now,
        )
        if schedule is None:
            return False
        try:
            result = self.service.evaluate_run(
                str(schedule["run_id"]),
                actor_type="system",
                actor_id=self.worker_id,
            )
            evaluation = result["evaluation"]
            self.repository.complete_outcome_schedule(
                str(schedule["id"]),
                self.worker_id,
                provider_as_of=str(evaluation.get("provider_as_of") or ""),
                evidence_id=str(evaluation["evidence_id"]),
                evidence_created=bool(result["created"]),
                now=now,
            )
        except OutcomeEvaluationError as error:
            self._record_failure(schedule, error.code, str(error), error.retryable, now=now)
        except Exception as error:
            logger.exception("结果观察计划执行异常:%s", schedule["id"])
            self._record_failure(
                schedule,
                "OUTCOME_WORKER_FAILED",
                str(error),
                True,
                now=now,
            )
        return True

    def _record_failure(self, schedule, code, message, retryable, *, now=None) -> None:
        try:
            self.repository.fail_outcome_schedule(
                str(schedule["id"]),
                self.worker_id,
                error_code=code,
                error_message=message,
                retryable=retryable,
                now=now,
            )
        except RuntimeError:
            logger.info("结果观察计划租约已由其他操作释放:%s", schedule["id"])

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                handled = self.run_once()
                if not handled:
                    self._stop.wait(self.poll_interval)
            except Exception:
                logger.exception("结果观察 Worker 循环异常")
                time.sleep(min(5.0, self.poll_interval))
