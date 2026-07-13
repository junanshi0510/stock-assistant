# -*- coding: utf-8 -*-
"""Shared decision-outcome evaluation service for HTTP and scheduled workers."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from strategies.fund_decision_outcome import ACTIONABLE_DECISION_ACTIONS

from .registry import ToolRegistry
from .repository import AgentRepository, RUN_TERMINAL_STATUSES


class OutcomeEvaluationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        http_status: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = bool(retryable)
        self.http_status = int(http_status)


class DecisionOutcomeService:
    TOOL_NAME = "fund.decision_outcome.get"
    TOOL_VERSION = "1.0.0"

    def __init__(self, repository: AgentRepository, registry: ToolRegistry) -> None:
        self.repository = repository
        self.registry = registry

    @staticmethod
    def decision_baseline(run: dict[str, Any]) -> dict[str, Any]:
        result = run.get("result") or {}
        fund = result.get("fund") or {}
        baseline_nav = fund.get("unit_nav")
        if baseline_nav is None:
            claim = next(
                (
                    item
                    for item in run.get("claims") or []
                    if item.get("claim_key") == "latest_nav"
                ),
                None,
            )
            baseline_nav = ((claim or {}).get("value") or {}).get("value")
        action = ((result.get("personalized_decision") or {}).get("decision") or {}).get(
            "action"
        )
        return {
            "code": str(fund.get("code") or (run.get("input") or {}).get("code") or ""),
            "name": fund.get("name"),
            "baseline_as_of": str(fund.get("as_of") or ""),
            "baseline_nav": baseline_nav,
            "action": str(action or "research_only"),
        }

    def eligibility(self, run: dict[str, Any] | None) -> dict[str, Any]:
        if run is None:
            return {"eligible": False, "reason": "run_not_found", "action": None}
        if run.get("intent") != "fund_deep_research":
            return {"eligible": False, "reason": "unsupported_intent", "action": None}
        if run.get("status") not in RUN_TERMINAL_STATUSES or not run.get("result"):
            return {"eligible": False, "reason": "run_not_terminal", "action": None}
        baseline = self.decision_baseline(run)
        if not re.fullmatch(r"\d{6}", baseline["code"]):
            return {
                "eligible": False,
                "reason": "missing_fund_code",
                "action": baseline["action"],
            }
        if not baseline["baseline_as_of"] or baseline["baseline_nav"] is None:
            return {
                "eligible": False,
                "reason": "missing_confirmed_nav_baseline",
                "action": baseline["action"],
            }
        if not self.repository.verify_run_evidence_integrity(str(run["id"]))["verified"]:
            return {
                "eligible": False,
                "reason": "source_integrity_failed",
                "action": baseline["action"],
            }
        if baseline["action"] not in ACTIONABLE_DECISION_ACTIONS:
            return {
                "eligible": False,
                "reason": "decision_not_directional",
                "action": baseline["action"],
            }
        return {"eligible": True, "reason": None, "action": baseline["action"]}

    def ensure_schedule_for_run(
        self,
        run: dict[str, Any] | None,
        *,
        interval_hours: int = 24,
        actor_id: str = "agent-runtime-v1",
    ) -> tuple[dict[str, Any] | None, bool]:
        eligibility = self.eligibility(run)
        if not eligibility["eligible"]:
            return None, False
        assert run is not None
        return self.repository.ensure_outcome_schedule(
            str(run["id"]),
            interval_hours=interval_hours,
            actor_type="system",
            actor_id=actor_id,
        )

    def backfill_eligible_schedules(self, *, limit: int = 100) -> int:
        created_count = 0
        for candidate in self.repository.list_unscheduled_terminal_runs(
            actions=tuple(sorted(ACTIONABLE_DECISION_ACTIONS)),
            limit=limit,
        ):
            run = self.repository.get_run(str(candidate["id"]))
            schedule, created = self.ensure_schedule_for_run(
                run,
                actor_id="outcome-schedule-backfill",
            )
            if schedule is not None and created:
                created_count += 1
        return created_count

    def _validated_run(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        run = self.repository.get_run(run_id)
        if run is None:
            raise OutcomeEvaluationError(
                "RUN_NOT_FOUND", "Agent Run 不存在", retryable=False, http_status=404
            )
        if run.get("status") not in RUN_TERMINAL_STATUSES or not run.get("result"):
            raise OutcomeEvaluationError(
                "RUN_NOT_TERMINAL",
                "只有已形成研究结果的终态 Run 可以评估",
                retryable=False,
                http_status=409,
            )
        if run.get("intent") != "fund_deep_research":
            raise OutcomeEvaluationError(
                "UNSUPPORTED_INTENT",
                "当前只支持基金深度研究 Run 的结果评估",
                retryable=False,
                http_status=409,
            )
        integrity = self.repository.verify_run_evidence_integrity(run_id)
        if not integrity["verified"]:
            raise OutcomeEvaluationError(
                "SOURCE_INTEGRITY_FAILED",
                "原 Run 的 Evidence 或审计链校验失败，已拒绝评估",
                retryable=False,
                http_status=409,
            )
        baseline = self.decision_baseline(run)
        if not re.fullmatch(r"\d{6}", baseline["code"]):
            raise OutcomeEvaluationError(
                "INVALID_FUND_CODE",
                "原 Run 缺少有效基金代码",
                retryable=False,
                http_status=409,
            )
        if not baseline["baseline_as_of"] or baseline["baseline_nav"] is None:
            raise OutcomeEvaluationError(
                "MISSING_BASELINE",
                "原 Run 缺少不可变的确认净值基线",
                retryable=False,
                http_status=409,
            )
        return run, baseline, integrity

    def _invoke_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        definition = self.registry.get(self.TOOL_NAME, self.TOOL_VERSION)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-outcome-evaluation")
        future = executor.submit(definition.handler, dict(payload))
        try:
            return future.result(timeout=float(definition.timeout_seconds))
        except FutureTimeoutError as error:
            future.cancel()
            raise OutcomeEvaluationError(
                "OUTCOME_TOOL_TIMEOUT",
                "真实确认净值评估超过执行时限",
                retryable=True,
                http_status=504,
            ) from error
        except OutcomeEvaluationError:
            raise
        except ValueError as error:
            raise OutcomeEvaluationError(
                "INVALID_OUTCOME_INPUT",
                str(error),
                retryable=False,
                http_status=409,
            ) from error
        except Exception as error:
            raise OutcomeEvaluationError(
                "OUTCOME_PROVIDER_FAILED",
                f"真实确认净值结果评估失败:{error}",
                retryable=True,
                http_status=502,
            ) from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def evaluate_run(
        self,
        run_id: str,
        *,
        actor_type: str = "user",
        actor_id: str = "anonymous",
    ) -> dict[str, Any]:
        run, baseline, integrity = self._validated_run(run_id)
        source_audit = self.repository.verify_audit_chain(run_id)
        outcome = self._invoke_tool(baseline)
        outcome["source_run"] = {
            "run_id": run_id,
            "completed_at": run.get("completed_at"),
            "result_schema_version": (run.get("result") or {}).get("schema_version"),
            "evidence_count": integrity.get("evidence_count"),
            "audit_chain_head": source_audit.get("chain_head"),
        }
        as_of = str(outcome.get("provider_as_of") or baseline["baseline_as_of"])
        evidence, created = self.repository.add_post_run_evidence(
            run_id,
            evidence_type="outcome_observation",
            subject_type="fund",
            subject_id=baseline["code"],
            provider=str(outcome.get("source") or self.TOOL_NAME),
            source_url=str(outcome.get("source_url") or "") or None,
            as_of=as_of,
            schema_version=str(outcome.get("evaluator_version") or self.TOOL_VERSION),
            quality_status="complete",
            payload=outcome,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        persisted_outcome = evidence.get("payload") or {}
        return {
            "created": created,
            "evaluation": {
                **persisted_outcome,
                "evidence_id": evidence["id"],
                "payload_sha256": evidence["payload_sha256"],
                "integrity_verified": evidence.get("integrity_verified"),
                "created_at": evidence["created_at"],
            },
        }
