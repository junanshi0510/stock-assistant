# -*- coding: utf-8 -*-
"""Deterministic workflows built from versioned, evidence-producing tools."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

import portfolio_exposure

from .registry import ToolDefinition, ToolRegistry
from .repository import AgentRepository, STEP_REUSABLE_STATUSES
from .synthesis import build_synthesis_context


SUPPORTED_INTENTS = {"fund_deep_research"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.{digits}f}"


@dataclass(frozen=True)
class WorkflowStep:
    key: str
    tool_name: str
    tool_version: str
    required: bool
    input_payload: dict[str, Any]
    runtime_input_payload: dict[str, Any] | None = None


class RequiredToolError(RuntimeError):
    pass


class ToolTimeoutError(TimeoutError):
    pass


class RunCancelledError(RuntimeError):
    pass


class AgentWorkflowRunner:
    def __init__(self, repository: AgentRepository, registry: ToolRegistry) -> None:
        self.repository = repository
        self.registry = registry

    def execute(self, run: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run["id"])
        try:
            if run.get("intent") not in SUPPORTED_INTENTS:
                return self.repository.finish_run(
                    run_id,
                    status="failed",
                    result=None,
                    error_code="UNSUPPORTED_INTENT",
                    error_message=f"暂不支持的 Agent 意图:{run.get('intent')}",
                )
            return self._execute_fund_research(run)
        except RunCancelledError:
            return self.repository.finish_run(run_id, status="cancelled", result=None)
        except RequiredToolError as error:
            return self.repository.finish_run(
                run_id,
                status="failed",
                result=None,
                error_code="REQUIRED_TOOL_FAILED",
                error_message=str(error),
            )
        except Exception as error:
            return self.repository.finish_run(
                run_id,
                status="failed",
                result=None,
                error_code="WORKFLOW_FAILED",
                error_message=str(error),
            )

    def _invoke_tool(
        self,
        run_id: str,
        definition: ToolDefinition,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"agent-{definition.name.replace('.', '-')}",
        )
        future = executor.submit(definition.handler, dict(input_payload))
        deadline = time.monotonic() + max(0.01, float(definition.timeout_seconds))
        try:
            while True:
                if self.repository.is_cancel_requested(run_id):
                    future.cancel()
                    raise RunCancelledError("Agent Run 已请求取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    future.cancel()
                    raise ToolTimeoutError(
                        f"工具超过 {definition.timeout_seconds} 秒执行时限: "
                        f"{definition.name}@{definition.version}"
                    )
                done, _ = wait({future}, timeout=min(0.25, remaining))
                if done:
                    return future.result()
        finally:
            # Persistence happens only after this method returns, so a provider
            # request that is still unwinding cannot write late evidence.
            executor.shutdown(wait=False, cancel_futures=True)

    def _fund_steps(self, payload: dict[str, Any]) -> list[WorkflowStep]:
        code = str(payload["code"])
        months = int(payload.get("months") or 60)
        user_id = str(payload.get("_user_id") or "default")
        steps = [WorkflowStep(
            key="fund_analysis",
            tool_name="fund.analysis.get",
            tool_version="1.0.0",
            required=True,
            input_payload={"code": code, "months": months},
        ), WorkflowStep(
            key="fund_market_profile",
            tool_name="fund.market_profile.get",
            tool_version="1.0.0",
            required=True,
            input_payload={"code": code},
        )]
        if payload.get("include_market_intelligence", False):
            steps.append(WorkflowStep(
                key="fund_intelligence",
                tool_name="fund.intelligence.get",
                tool_version="1.0.0",
                required=False,
                input_payload={
                    "code": code,
                    "holding_limit": int(payload.get("intelligence_holding_limit") or 4),
                    "news_per_holding": int(payload.get("news_per_holding") or 3),
                },
            ))
        if payload.get("include_portfolio_context", True):
            steps.append(WorkflowStep(
                key="portfolio_context",
                tool_name="portfolio.context.get",
                tool_version="1.0.0",
                required=True,
                input_payload={
                    "code": code,
                    "profile_version_id": payload.get("profile_version_id"),
                    "user_id": user_id,
                },
            ))
            steps.append(WorkflowStep(
                key="portfolio_exposure",
                tool_name="portfolio.exposure.snapshot",
                tool_version="1.0.0",
                required=True,
                input_payload={
                    "code": code,
                    "profile_version_id": payload.get("profile_version_id"),
                    "user_id": user_id,
                },
            ))
        if payload.get("include_estimate", False):
            steps.append(WorkflowStep(
                key="fund_estimate",
                tool_name="fund.estimate.get",
                tool_version="1.0.0",
                required=False,
                input_payload={"code": code},
            ))
        if payload.get("include_disclosure_changes", True):
            steps.append(WorkflowStep(
                key="fund_disclosure_changes",
                tool_name="fund.disclosure_changes.get",
                tool_version="1.0.0",
                required=False,
                input_payload={"code": code},
            ))
        steps.append(WorkflowStep(
            key="fund_peer_persistence",
            tool_name="fund.peer_persistence.get",
            tool_version="1.0.0",
            required=False,
            input_payload={"code": code},
        ))
        if payload.get("include_alternatives", True):
            steps.append(WorkflowStep(
                key="fund_alternatives",
                tool_name="fund.alternatives.get",
                tool_version="1.0.0",
                required=False,
                input_payload={
                    "code": code,
                    "months": months,
                    "sort": "1y",
                    "limit": int(payload.get("alternative_limit") or 5),
                },
            ))
        return steps

    @staticmethod
    def _quality_status(payload: dict[str, Any]) -> str:
        declared = str(payload.get("status") or "").lower()
        if declared == "unavailable":
            return "unavailable"
        if declared in {"partial", "insufficient", "insufficient_data"} or payload.get("failed"):
            return "partial"
        return "complete"

    @staticmethod
    def _as_of(payload: dict[str, Any], step_key: str) -> str | None:
        if payload.get("as_of"):
            return str(payload["as_of"])
        if step_key == "portfolio_exposure":
            return str(payload.get("evaluated_on") or "") or None
        if step_key == "strategy_governance":
            return str(payload.get("evaluated_at") or "") or None
        if step_key == "fund_estimate":
            estimate = payload.get("estimate") or {}
            confirmed = payload.get("confirmed") or {}
            return str(estimate.get("time") or confirmed.get("date") or "") or None
        if step_key == "fund_disclosure_changes":
            latest = payload.get("latest") or {}
            return str(
                latest.get("stock_period")
                or latest.get("industry_period")
                or latest.get("year")
                or ""
            ) or None
        if step_key in {"fund_intelligence", "ai_synthesis"}:
            return str(payload.get("as_of") or payload.get("generated_at") or "") or None
        return None

    def _execute_tool_step(
        self,
        run_id: str,
        sequence_no: int,
        step: WorkflowStep,
        code: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        existing = self.repository.get_step(run_id, step.key)
        if existing and existing.get("status") in STEP_REUSABLE_STATUSES and existing.get("evidence_id"):
            if (
                existing.get("tool_name") != step.tool_name
                or existing.get("tool_version") != step.tool_version
                or (existing.get("input") or {}) != step.input_payload
            ):
                raise RequiredToolError(
                    f"已持久化步骤契约与当前工作流不一致，拒绝跨版本复用:"
                    f"{step.key}"
                )
            evidence = self.repository.get_evidence(run_id, existing["evidence_id"], include_payload=True)
            if evidence:
                return evidence.get("payload") or {}, evidence, None

        definition: ToolDefinition = self.registry.get(step.tool_name, step.tool_version)
        if definition.risk_level not in {"R0", "R1"}:
            raise RequiredToolError(
                f"当前基金工作流只允许 R0/R1 只读或确定性工具:{definition.name}@{definition.version}"
            )
        persisted_step = self.repository.start_step(
            run_id,
            step_key=step.key,
            sequence_no=sequence_no,
            tool_name=definition.name,
            tool_version=definition.version,
            required=step.required,
            input_payload=step.input_payload,
        )
        try:
            output = self._invoke_tool(
                run_id,
                definition,
                step.runtime_input_payload or step.input_payload,
            )
            if not isinstance(output, dict):
                raise TypeError("工具必须返回结构化对象")
            if step.key == "portfolio_exposure" and not (output.get("snapshot") or {}).get("id"):
                if self.repository.is_cancel_requested(run_id):
                    raise RunCancelledError("Agent Run 已请求取消")
                output = portfolio_exposure.persist_exposure_snapshot(
                    output,
                    user_id=str(step.input_payload.get("user_id") or "default"),
                )
            quality = self._quality_status(output)
            step_status = "succeeded" if quality == "complete" else "partial"
            evidence = self.repository.complete_step_with_evidence(
                run_id,
                persisted_step["id"],
                status=step_status,
                payload=output,
                evidence_type=(
                    "user_confirmed"
                    if step.key == "portfolio_context"
                    else "model_inference"
                    if step.key == "ai_synthesis"
                    else "governance"
                    if step.key == "strategy_governance"
                    else "calculation"
                    if step.key in {"fund_analysis", "personalized_decision", "portfolio_exposure"}
                    else "provider_normalized"
                ),
                subject_type="portfolio" if step.key in {"portfolio_context", "portfolio_exposure"} else "fund",
                subject_id=(
                    str(step.input_payload.get("user_id") or "default")
                    if step.key in {"portfolio_context", "portfolio_exposure"}
                    else code
                ),
                provider=str(output.get("source") or definition.name),
                source_url=str(output.get("source_url") or "") or None,
                as_of=self._as_of(output, step.key),
                quality_status=quality,
                schema_version=str(output.get("schema_version") or "1.0.0"),
            )
            unavailable = None
            if quality != "complete":
                unavailable = {
                    "step": step.key,
                    "tool": definition.name,
                    "status": quality,
                    "reason": str(
                        output.get("reason")
                        or (output.get("reasons") or ["该真实数据仅部分可用"])[0]
                    ),
                    "evidence_id": evidence["id"],
                }
            return output, evidence, unavailable
        except RunCancelledError as error:
            self.repository.cancel_step(run_id, persisted_step["id"], reason=str(error))
            raise
        except Exception as error:
            if isinstance(error, ToolTimeoutError):
                error_code = "TOOL_TIMEOUT"
            elif isinstance(error, ValueError):
                error_code = "TOOL_INVALID_INPUT"
            else:
                error_code = "TOOL_EXECUTION_FAILED"
            self.repository.fail_step(
                run_id,
                persisted_step["id"],
                error_code=error_code,
                error_message=str(error),
            )
            failure = {
                "step": step.key,
                "tool": definition.name,
                "status": "failed",
                "reason": str(error)[:300],
                "evidence_id": None,
            }
            if step.required:
                raise RequiredToolError(f"必需真实数据工具失败:{definition.name}: {error}") from error
            return None, None, failure

    def _add_metric_claim(
        self,
        run_id: str,
        *,
        claim_key: str,
        label: str,
        value: Any,
        unit: str,
        evidence_id: str,
        digits: int = 2,
    ) -> dict[str, Any] | None:
        number = _number(value)
        if number is None:
            return None
        suffix = unit if unit != "%" else "%"
        claim = self.repository.add_claim(
            run_id,
            claim_key=claim_key,
            claim_type="fact_numeric",
            claim_text=f"{label}为 {_fmt_number(number, digits)}{suffix}",
            value={"label": label, "value": number, "unit": unit},
            evidence_id=evidence_id,
        )
        return {
            "claim_id": claim["id"],
            "label": label,
            "value": number,
            "unit": unit,
            "evidence_id": evidence_id,
        }

    def _build_fund_result(
        self,
        run_id: str,
        input_payload: dict[str, Any],
        outputs: dict[str, dict[str, Any]],
        evidence: dict[str, dict[str, Any]],
        unavailable: list[dict[str, Any]],
    ) -> dict[str, Any]:
        analysis = outputs["fund_analysis"]
        analysis_evidence = evidence["fund_analysis"]
        code = str(analysis.get("code") or input_payload["code"])
        name = str(analysis.get("name") or code)
        latest = analysis.get("latest") or {}
        metrics = analysis.get("metrics") or {}
        timing = analysis.get("timing") or {}
        conditioned_forward = analysis.get("conditioned_forward") or {}
        playbook = analysis.get("playbook") or {}
        role = playbook.get("role") or {}

        metric_specs = [
            ("latest_nav", "最新确认单位净值", latest.get("unit_nav"), "", 4),
            ("return_3m", "近 3 月收益", metrics.get("return_3m"), "%", 2),
            ("return_1y", "近 1 年收益", metrics.get("return_1y"), "%", 2),
            ("annual_volatility", "年化波动", metrics.get("annual_volatility"), "%", 2),
            ("max_drawdown", "样本最大回撤", metrics.get("max_drawdown"), "%", 2),
            ("current_drawdown", "当前回撤", metrics.get("current_drawdown"), "%", 2),
            ("timing_score", "投入节奏评分", timing.get("score"), "分", 0),
        ]
        facts = []
        for claim_key, label, value, unit, digits in metric_specs:
            item = self._add_metric_claim(
                run_id,
                claim_key=claim_key,
                label=label,
                value=value,
                unit=unit,
                evidence_id=analysis_evidence["id"],
                digits=digits,
            )
            if item:
                facts.append(item)

        strategy_result = None
        if conditioned_forward:
            strategy_result = dict(conditioned_forward)
            strategy_result["evidence_id"] = analysis_evidence["id"]
            governance_payload = outputs.get("strategy_governance") or {}
            governance_evidence = evidence.get("strategy_governance") or {}
            if governance_payload:
                strategy_result["governance"] = {
                    **governance_payload,
                    "evidence_id": governance_evidence.get("id"),
                }
            strategy_result["evidence_ids"] = [
                item for item in (
                    analysis_evidence["id"],
                    governance_evidence.get("id"),
                ) if item
            ]
            primary_horizon = conditioned_forward.get("primary_horizon")
            primary = next(
                (
                    item
                    for item in (conditioned_forward.get("horizons") or [])
                    if item.get("horizon") == primary_horizon and item.get("status") == "available"
                ),
                None,
            )
            if primary:
                horizon_label = {"3m": "3 个月", "6m": "6 个月", "12m": "12 个月"}.get(
                    str(primary_horizon), str(primary_horizon)
                )
                analog = primary.get("analog") or {}
                for claim_key, label, value in (
                    (
                        f"conditioned_{primary_horizon}_positive_rate",
                        f"历史相似条件后 {horizon_label}正收益比例",
                        analog.get("positive_rate"),
                    ),
                    (
                        f"conditioned_{primary_horizon}_median_return",
                        f"历史相似条件后 {horizon_label}中位收益",
                        analog.get("median_return"),
                    ),
                ):
                    item = self._add_metric_claim(
                        run_id,
                        claim_key=claim_key,
                        label=label,
                        value=value,
                        unit="%",
                        evidence_id=analysis_evidence["id"],
                    )
                    if item:
                        facts.append(item)

        personalized_decision = None
        market_profile = None
        market_payload = outputs.get("fund_market_profile") or {}
        market_evidence = evidence.get("fund_market_profile") or {}
        if market_payload:
            market_profile = dict(market_payload)
            market_profile["evidence_id"] = market_evidence.get("id")
            market_profile["evidence_ids"] = (
                [market_evidence["id"]] if market_evidence.get("id") else []
            )
        decision_payload = outputs.get("personalized_decision") or {}
        decision_evidence = evidence.get("personalized_decision") or {}
        if decision_payload:
            personalized_decision = dict(decision_payload)
            personalized_decision["evidence_id"] = decision_evidence.get("id")
            personalized_decision["evidence_ids"] = [
                item for item in (
                    (evidence.get("fund_analysis") or {}).get("id"),
                    (evidence.get("fund_market_profile") or {}).get("id"),
                    (evidence.get("portfolio_context") or {}).get("id"),
                    (evidence.get("portfolio_exposure") or {}).get("id"),
                    (evidence.get("strategy_governance") or {}).get("id"),
                    decision_evidence.get("id"),
                ) if item
            ]
            for claim_key, label, value, unit in (
                (
                    "personal_current_ratio",
                    "目标基金当前仓位",
                    (decision_payload.get("portfolio") or {}).get("current_ratio"),
                    "%",
                ),
                (
                    "personal_max_single_ratio",
                    "你的单品仓位上限",
                    (decision_payload.get("portfolio") or {}).get("max_single_ratio"),
                    "%",
                ),
                (
                    "personal_allowed_amount",
                    "本轮上限内可用总金额",
                    (decision_payload.get("budget") or {}).get("allowed_full_amount"),
                    "元",
                ),
                (
                    "personal_first_tranche",
                    "首批观察金额",
                    (decision_payload.get("budget") or {}).get("first_tranche_amount"),
                    "元",
                ),
                (
                    "portfolio_equity_upper_ratio",
                    "组合权益暴露最坏上界",
                    ((decision_payload.get("portfolio_exposure") or {}).get("equity") or {}).get("current_upper_ratio"),
                    "%",
                ),
                (
                    "portfolio_industry_upper_ratio",
                    "组合单行业暴露最坏上界",
                    ((decision_payload.get("portfolio_exposure") or {}).get("industry") or {}).get("current_max_upper_ratio"),
                    "%",
                ),
            ):
                if not decision_evidence.get("id"):
                    continue
                item = self._add_metric_claim(
                    run_id,
                    claim_key=claim_key,
                    label=label,
                    value=value,
                    unit=unit,
                    evidence_id=decision_evidence["id"],
                )
                if item:
                    facts.append(item)

        estimate_payload = outputs.get("fund_estimate") or {}
        estimate_result = None
        level_recurrence_result = None
        if estimate_payload:
            estimate_data = estimate_payload.get("estimate") or {}
            confirmed_data = estimate_payload.get("confirmed") or {}
            estimate_evidence_id = (evidence.get("fund_estimate") or {}).get("id")
            estimate_result = {
                "status": estimate_payload.get("status") or "unavailable",
                "confirmed_date": confirmed_data.get("date"),
                "confirmed_nav": confirmed_data.get("unit_nav"),
                "estimate_time": estimate_data.get("time"),
                "estimate_nav": estimate_data.get("unit_nav"),
                "estimate_change_pct": estimate_data.get("change_pct"),
                "policy": estimate_payload.get("policy"),
                "evidence_id": estimate_evidence_id,
            }
            level_recurrence = estimate_payload.get("level_recurrence") or {}
            if level_recurrence:
                level_recurrence_result = dict(level_recurrence)
                level_recurrence_result["evidence_id"] = estimate_evidence_id
                level_recurrence_result["evidence_ids"] = (
                    [estimate_evidence_id] if estimate_evidence_id else []
                )
            if estimate_payload.get("status") == "available" and estimate_result["evidence_id"]:
                estimate_claim = self._add_metric_claim(
                    run_id,
                    claim_key="intraday_estimate_change",
                    label="第三方盘中估算涨跌",
                    value=estimate_data.get("change_pct"),
                    unit="%",
                    evidence_id=estimate_result["evidence_id"],
                )
                if estimate_claim:
                    facts.append(estimate_claim)

        disclosure_payload = outputs.get("fund_disclosure_changes") or {}
        disclosure_result = None
        if disclosure_payload:
            summary = disclosure_payload.get("summary") or {}
            disclosure_result = {
                "status": disclosure_payload.get("status") or "unavailable",
                "latest": disclosure_payload.get("latest"),
                "previous": disclosure_payload.get("previous"),
                "top10_stock_ratio_change": summary.get("top10_stock_ratio_change"),
                "added_stock_count": summary.get("added_stock_count"),
                "removed_stock_count": summary.get("removed_stock_count"),
                "industry_focus_changed": summary.get("industry_focus_changed"),
                "policy": disclosure_payload.get("policy"),
                "evidence_id": (evidence.get("fund_disclosure_changes") or {}).get("id"),
            }

        alternatives_payload = outputs.get("fund_alternatives") or {}
        alternative_result = []
        if alternatives_payload:
            alternative_evidence_id = (evidence.get("fund_alternatives") or {}).get("id")
            for row in (alternatives_payload.get("alternatives") or [])[:5]:
                alternative_result.append({
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "score": row.get("score"),
                    "label": row.get("label"),
                    "trend_state": row.get("trend_state"),
                    "metrics": row.get("metrics") or {},
                    "fee": row.get("fee") or {},
                    "durability": row.get("durability") or {},
                    "due_diligence": row.get("due_diligence") or {},
                    "advantages": row.get("advantages") or [],
                    "cautions": row.get("cautions") or [],
                    "evidence_id": alternative_evidence_id,
                })

        peer_persistence_payload = outputs.get("fund_peer_persistence") or {}
        peer_persistence_result = None
        if peer_persistence_payload:
            peer_persistence_evidence_id = (
                evidence.get("fund_peer_persistence") or {}
            ).get("id")
            peer_persistence_result = {
                **peer_persistence_payload,
                "evidence_id": peer_persistence_evidence_id,
                "evidence_ids": (
                    [peer_persistence_evidence_id]
                    if peer_persistence_evidence_id
                    else []
                ),
            }

        intelligence_payload = outputs.get("fund_intelligence") or {}
        intelligence_result = None
        if intelligence_payload:
            intelligence_evidence_id = (evidence.get("fund_intelligence") or {}).get("id")
            news = intelligence_payload.get("news") or {}
            intelligence_result = {
                "status": intelligence_payload.get("status") or "unavailable",
                "as_of": intelligence_payload.get("as_of"),
                "market": intelligence_payload.get("market") or {},
                "portfolio_disclosure": intelligence_payload.get("portfolio_disclosure") or {},
                "holding_pulse": intelligence_payload.get("holding_pulse") or {},
                "sector_pulse": intelligence_payload.get("sector_pulse") or {},
                "news": {
                    "count": news.get("count") or 0,
                    "covered_holding_count": news.get("covered_holding_count") or 0,
                    "selected_holding_count": news.get("selected_holding_count") or 0,
                    "publishers": news.get("publishers") or [],
                    "interpretation_policy": news.get("interpretation_policy"),
                },
                "quality": intelligence_payload.get("quality") or {},
                "failed": intelligence_payload.get("failed") or [],
                "policy": intelligence_payload.get("policy"),
                "evidence_id": intelligence_evidence_id,
                "evidence_ids": [intelligence_evidence_id] if intelligence_evidence_id else [],
            }

        synthesis_payload = outputs.get("ai_synthesis") or {}
        ai_synthesis_result = None
        if synthesis_payload:
            synthesis_evidence_id = (evidence.get("ai_synthesis") or {}).get("id")
            ai_synthesis_result = {
                **synthesis_payload,
                "evidence_id": synthesis_evidence_id,
                "evidence_ids": [
                    item
                    for item in (
                        synthesis_evidence_id,
                        *((synthesis_payload.get("synthesis") or {}).get("all_evidence_ids") or []),
                    )
                    if item
                ],
            }

        role_label = role.get("label") or "观察仓"
        timing_label = timing.get("label") or analysis.get("trend_state") or "等待更多数据"
        if personalized_decision:
            personal_decision = personalized_decision.get("decision") or {}
            headline = (
                f"{name} 个人决策为「{personal_decision.get('label') or '等待复核'}」。"
                f"{personal_decision.get('rationale') or ''}"
            )
        else:
            headline = f"{name} 当前研究定位为「{role_label}」，投入节奏为「{timing_label}」。"
        if unavailable:
            headline += " 部分真实数据暂不可用，结论范围已收窄。"

        return {
            "schema_version": "fund_deep_research.v6",
            "generated_at": _now(),
            "intent": "fund_deep_research",
            "scope": {
                "personalized": personalized_decision is not None,
                "model_synthesized": bool(
                    ai_synthesis_result and ai_synthesis_result.get("status") == "available"
                ),
                "model_private_context_used": bool(
                    ((ai_synthesis_result or {}).get("provider") or {}).get("private_context_used")
                ),
                "statement": (
                    "本轮读取用户已确认持仓、已保存投资约束和持久化策略发布状态；只有已发布策略才可能进入确定性金额门禁。模型仅解释已持久化 Evidence，不读取未导入资产，不自动下单。"
                    if personalized_decision is not None
                    else "本轮只分析公共基金数据；模型仅解释已持久化 Evidence，未读取用户持仓，不生成个性化仓位或交易指令。"
                ),
            },
            "fund": {
                "code": code,
                "name": name,
                "as_of": analysis.get("as_of"),
                "unit_nav": latest.get("unit_nav"),
                "sample_count": analysis.get("sample_count"),
                "trend_state": analysis.get("trend_state"),
            },
            "conclusion": {
                "status": "research_ready" if not unavailable else "research_partial",
                "headline": headline,
                "role": role_label,
                "role_reason": role.get("reason"),
                "risk_band": role.get("risk_band"),
                "minimum_holding_period": role.get("minimum_holding_period"),
                "timing_label": timing_label,
                "timing_score": timing.get("score"),
                "personal_action": ((personalized_decision or {}).get("decision") or {}).get("action"),
            },
            "facts": facts,
            "strategy": strategy_result,
            "market_profile": market_profile,
            "market_intelligence": intelligence_result,
            "personalized_decision": personalized_decision,
            "ai_synthesis": ai_synthesis_result,
            "level_recurrence": level_recurrence_result,
            "risk_review": {
                "red_flags": playbook.get("red_flags") or [],
                "entry_rules": playbook.get("entry_rules") or [],
                "exit_rules": playbook.get("exit_rules") or [],
                "evidence_id": analysis_evidence["id"],
            },
            "estimate": estimate_result,
            "disclosure_changes": disclosure_result,
            "peer_persistence": peer_persistence_result,
            "alternatives": alternative_result,
            "next_actions": playbook.get("execution_steps") or [],
            "unavailable": unavailable,
            "evidence_refs": [
                {
                    "evidence_id": item["id"],
                    "tool_step": key,
                    "provider": item.get("provider"),
                    "as_of": item.get("as_of"),
                    "quality_status": item.get("quality_status"),
                    "payload_sha256": item.get("payload_sha256"),
                }
                for key, item in evidence.items()
            ],
            "policy": "大模型研判不等于收益承诺；精确数值、仓位和动作由确定性门禁控制，任何新增投入都应结合真实组合与后续结果复盘。",
        }

    def _execute_fund_research(self, run: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run["id"])
        payload = dict(run.get("input") or {})
        payload["_user_id"] = str(run.get("user_id") or "default")
        code = str(payload["code"])
        outputs: dict[str, dict[str, Any]] = {}
        evidence: dict[str, dict[str, Any]] = {}
        unavailable: list[dict[str, Any]] = []

        research_steps = self._fund_steps(payload)
        for index, step in enumerate(research_steps, start=1):
            if self.repository.is_cancel_requested(run_id):
                return self.repository.finish_run(run_id, status="cancelled", result=None)
            output, evidence_item, failure = self._execute_tool_step(
                run_id,
                index,
                step,
                code,
            )
            if output is not None:
                outputs[step.key] = output
                if step.key == "portfolio_exposure":
                    snapshot_id = str((output.get("snapshot") or {}).get("id") or "")
                    snapshot_hash = str((output.get("snapshot") or {}).get("payload_sha256") or "")
                    integrity = output.get("integrity") or {}
                    if (
                        not snapshot_id
                        or not integrity.get("verified")
                        or str(integrity.get("payload_sha256") or "") != snapshot_hash
                    ):
                        raise RequiredToolError("组合穿透快照缺少可验证的持久化哈希")
                    self.repository.bind_exposure_snapshot(run_id, snapshot_id)
            if evidence_item is not None:
                evidence[step.key] = evidence_item
            if failure is not None:
                unavailable.append(failure)

        conditioned_forward = (outputs.get("fund_analysis") or {}).get("conditioned_forward") or {}
        strategy_id = str(conditioned_forward.get("strategy_id") or "")
        strategy_version = str(conditioned_forward.get("strategy_version") or "")
        if not strategy_id or not strategy_version:
            raise RequiredToolError("基金策略结果缺少精确策略 ID 或版本，默认拒绝继续决策")
        market_primary = str(
            ((outputs.get("fund_market_profile") or {}).get("market") or {}).get("primary")
            or "unknown_cross_border"
        )
        governance_step = WorkflowStep(
            key="strategy_governance",
            tool_name="strategy.release.check",
            tool_version="1.0.0",
            required=True,
            input_payload={
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "asset_type": "fund",
                "market": market_primary,
                "user_scenario": (
                    "personalized_fund_decision"
                    if payload.get("include_portfolio_context", True)
                    else "fund_research"
                ),
                "user_id": str(run.get("user_id") or "anonymous"),
            },
        )
        output, evidence_item, failure = self._execute_tool_step(
            run_id,
            len(research_steps) + 1,
            governance_step,
            code,
        )
        if output is not None:
            outputs[governance_step.key] = output
        if evidence_item is not None:
            evidence[governance_step.key] = evidence_item
        if failure is not None:
            unavailable.append(failure)

        if "fund_analysis" not in outputs or "fund_analysis" not in evidence:
            raise RequiredToolError("基金核心分析没有形成可验证 Evidence")

        if (
            "portfolio_context" in outputs
            and "portfolio_context" in evidence
            and "portfolio_exposure" in outputs
            and "portfolio_exposure" in evidence
            and "fund_market_profile" in outputs
            and "fund_market_profile" in evidence
        ):
            source_evidence_ids = [
                evidence["fund_analysis"]["id"],
                evidence["fund_market_profile"]["id"],
                evidence["portfolio_context"]["id"],
                evidence["portfolio_exposure"]["id"],
                evidence["strategy_governance"]["id"],
            ]
            decision_step = WorkflowStep(
                key="personalized_decision",
                tool_name="fund.personalized_decision.evaluate",
                tool_version="1.3.0",
                required=True,
                input_payload={
                    "code": code,
                    "planned_amount": payload.get("planned_amount"),
                    "input_evidence_ids": source_evidence_ids,
                },
                runtime_input_payload={
                    "analysis": outputs["fund_analysis"],
                    "market_profile": outputs["fund_market_profile"],
                    "context": outputs["portfolio_context"],
                    "exposure": outputs["portfolio_exposure"],
                    "strategy_governance": outputs["strategy_governance"],
                    "planned_amount": payload.get("planned_amount"),
                    "input_evidence_ids": source_evidence_ids,
                },
            )
            output, evidence_item, failure = self._execute_tool_step(
                run_id,
                len(research_steps) + 2,
                decision_step,
                code,
            )
            if output is not None:
                outputs[decision_step.key] = output
            if evidence_item is not None:
                evidence[decision_step.key] = evidence_item
            if failure is not None:
                unavailable.append(failure)

        if payload.get("include_ai_synthesis", False):
            model_context = build_synthesis_context(payload, outputs, evidence)
            canonical_context = json.dumps(
                model_context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            context_sha256 = hashlib.sha256(canonical_context.encode("utf-8")).hexdigest()
            model_evidence_ids = sorted(
                str(item.get("id"))
                for item in evidence.values()
                if item.get("id")
            )
            question = str(payload.get("question") or "").strip()
            synthesis_step = WorkflowStep(
                key="ai_synthesis",
                tool_name="llm.fund_decision.synthesize",
                tool_version="1.0.0",
                required=False,
                input_payload={
                    "code": code,
                    "context_sha256": context_sha256,
                    "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
                    "input_evidence_ids": model_evidence_ids,
                    "private_context_requested": bool(
                        payload.get("include_portfolio_context", True)
                    ),
                },
                runtime_input_payload={
                    "context": model_context,
                    "context_sha256": context_sha256,
                },
            )
            output, evidence_item, failure = self._execute_tool_step(
                run_id,
                len(research_steps) + 3,
                synthesis_step,
                code,
            )
            if output is not None:
                outputs[synthesis_step.key] = output
            if evidence_item is not None:
                evidence[synthesis_step.key] = evidence_item
            if failure is not None:
                unavailable.append(failure)

        if self.repository.is_cancel_requested(run_id):
            return self.repository.finish_run(run_id, status="cancelled", result=None)
        result = self._build_fund_result(run_id, payload, outputs, evidence, unavailable)
        if self.repository.is_cancel_requested(run_id):
            return self.repository.finish_run(run_id, status="cancelled", result=None)
        final_status = "partial" if unavailable else "completed"
        return self.repository.finish_run(run_id, status=final_status, result=result)
