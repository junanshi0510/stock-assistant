# -*- coding: utf-8 -*-
"""Lease-based worker for opt-in portfolio decision checks."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable

import decision_center
import storage


logger = logging.getLogger("decision-check-worker")


class DecisionCheckWorker:
    def __init__(
        self,
        run_check: Callable[..., dict] | None = None,
        *,
        store=storage,
        poll_interval: float = 30.0,
        lease_seconds: int = 120,
    ) -> None:
        self.run_check = run_check or decision_center.build_decision_center
        self.store = store
        self.poll_interval = max(1.0, float(poll_interval))
        self.lease_seconds = max(60, int(lease_seconds))
        self.worker_id = f"decision_check_worker_{uuid.uuid4().hex}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_lock = threading.Lock()

    def start(self) -> bool:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="decision-check-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info("定时持仓检查 Worker 已启动:%s", self.worker_id)
            return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def run_once(self, *, now=None) -> bool:
        schedule = self.store.claim_due_decision_check(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            now=now,
        )
        if schedule is None:
            return False

        try:
            result = self.run_check(user_id=str(schedule["user_id"]))
        except Exception as error:
            logger.exception("定时持仓检查执行异常:%s", schedule["id"])
            self._record_failure(
                schedule,
                "DECISION_CHECK_EXECUTION_FAILED",
                str(error),
                now=now,
            )
            return True

        try:
            unavailable_count = int((result.get("summary") or {}).get("unavailable_count") or 0)
            inbox = result.get("task_inbox") or {}
            open_count = int((inbox.get("summary") or {}).get("open_count") or 0)
            result_status = "partial" if unavailable_count > 0 else "succeeded"
            self.store.complete_decision_check(
                str(schedule["id"]),
                self.worker_id,
                result_status=result_status,
                open_count=open_count,
                unavailable_count=unavailable_count,
                now=now,
            )
        except self.store.DecisionCheckLeaseError:
            logger.info("定时持仓检查租约已释放:%s", schedule["id"])
        except Exception as error:
            logger.exception("定时持仓检查完成状态写入失败:%s", schedule["id"])
            self._record_failure(
                schedule,
                "DECISION_CHECK_COMPLETION_FAILED",
                str(error),
                now=now,
            )
        return True

    def _record_failure(self, schedule: dict, code: str, message: str, *, now=None) -> None:
        try:
            self.store.fail_decision_check(
                str(schedule["id"]),
                self.worker_id,
                error_code=code,
                error_message=message,
                retryable=True,
                now=now,
            )
        except self.store.DecisionCheckLeaseError:
            logger.info("定时持仓检查失败状态未写入，租约已释放:%s", schedule["id"])

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                handled = self.run_once()
                if not handled:
                    self._stop.wait(self.poll_interval)
            except Exception:
                logger.exception("定时持仓检查 Worker 循环异常")
                time.sleep(min(5.0, self.poll_interval))


worker = DecisionCheckWorker(
    poll_interval=float(os.getenv("DECISION_CHECK_POLL_SECONDS", "30")),
    lease_seconds=int(os.getenv("DECISION_CHECK_LEASE_SECONDS", "120")),
)


def start_worker() -> bool:
    if os.getenv("DECISION_CHECK_WORKER_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
    }:
        return False
    return worker.start()


def stop_worker() -> None:
    worker.stop()
