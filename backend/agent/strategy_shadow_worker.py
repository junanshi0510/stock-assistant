# -*- coding: utf-8 -*-
"""Lease-based worker for version-bound strategy Shadow outcomes."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from .repository import AgentRepository
from .strategy_shadow_outcomes import (
    StrategyShadowOutcomeError,
    StrategyShadowOutcomeService,
)


logger = logging.getLogger("agent-strategy-shadow-worker")


class StrategyShadowOutcomeWorker:
    def __init__(
        self,
        repository: AgentRepository,
        service: StrategyShadowOutcomeService,
        *,
        poll_interval: float = 30.0,
        lease_seconds: int = 120,
    ) -> None:
        self.repository = repository
        self.service = service
        self.poll_interval = max(1.0, float(poll_interval))
        self.lease_seconds = max(60, int(lease_seconds))
        self.worker_id = f"strategy_shadow_worker_{uuid.uuid4().hex}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_lock = threading.Lock()

    def start(self) -> bool:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return False
            try:
                backfilled = self.service.backfill_eligible_enrollments(limit=1000)
                if backfilled:
                    logger.info("已为 %s 个既有策略信号建立 Shadow 入组记录", backfilled)
            except Exception:
                logger.exception("既有策略 Shadow 入组回填失败")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="agent-strategy-shadow-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info("策略 Shadow Outcome Worker 已启动:%s", self.worker_id)
            return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def run_once(self, *, now=None) -> bool:
        enrollment = self.repository.claim_due_strategy_shadow_enrollment(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            now=now,
        )
        if enrollment is None:
            return False
        try:
            result = self.service.evaluate_enrollment(
                enrollment,
                actor_id=self.worker_id,
            )
            outcome = result["outcome"]
            if result["status"] == "pending":
                progress = outcome.get("progress") or {}
                self.repository.mark_strategy_shadow_pending(
                    str(enrollment["id"]),
                    self.worker_id,
                    provider_as_of=str(outcome.get("provider_as_of") or ""),
                    available_observations=int(progress.get("available_observations") or 0),
                    now=now,
                )
            else:
                evidence = result["evidence"]
                self.repository.complete_strategy_shadow_enrollment(
                    str(enrollment["id"]),
                    self.worker_id,
                    provider_as_of=str(outcome.get("provider_as_of") or ""),
                    observed_as_of=str((outcome.get("observed") or {}).get("as_of") or ""),
                    evidence_id=str(evidence["id"]),
                    evidence_created=bool(result["created"]),
                    now=now,
                )
        except StrategyShadowOutcomeError as error:
            self._record_failure(
                enrollment,
                error.code,
                str(error),
                error.retryable,
                now=now,
            )
        except Exception as error:
            logger.exception("策略 Shadow 观测执行异常:%s", enrollment["id"])
            self._record_failure(
                enrollment,
                "STRATEGY_SHADOW_WORKER_FAILED",
                str(error),
                True,
                now=now,
            )
        return True

    def _record_failure(self, enrollment, code, message, retryable, *, now=None) -> None:
        try:
            self.repository.fail_strategy_shadow_enrollment(
                str(enrollment["id"]),
                self.worker_id,
                error_code=code,
                error_message=message,
                retryable=retryable,
                now=now,
            )
        except RuntimeError:
            logger.info("策略 Shadow 观测租约已由其他操作释放:%s", enrollment["id"])

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                handled = self.run_once()
                if not handled:
                    self._stop.wait(self.poll_interval)
            except Exception:
                logger.exception("策略 Shadow Outcome Worker 循环异常")
                time.sleep(min(5.0, self.poll_interval))
