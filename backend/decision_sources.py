# -*- coding: utf-8 -*-
"""Normalize durable research engines into one decision-center contract.

The module never turns a research score into an order.  It only exposes the
latest persisted evidence, its integrity/validation state, and the next review
step that can safely move the evidence forward.
"""

from __future__ import annotations

from typing import Any


def _action(
    action_id: str,
    priority: str,
    category: str,
    title: str,
    detail: str,
    evidence: list[str],
    target: str,
    action_label: str,
    source: str,
    *,
    evidence_status: str,
    validation_state: str,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "priority": priority,
        "category": category,
        "title": title,
        "detail": detail,
        "evidence": [str(item) for item in evidence if item],
        "target": target,
        "action_label": action_label,
        "source": source,
        "evidence_status": evidence_status,
        "validation_state": validation_state,
        "execution_authorized": False,
    }


def _source(
    source_id: str,
    label: str,
    target: str,
    *,
    status: str,
    ready: bool = False,
    evidence_status: str = "missing",
    validation_state: str = "not_started",
    latest_run_id: str | None = None,
    as_of: str | None = None,
    summary: str = "尚无持久化研究结果",
    error: str | None = None,
) -> dict[str, Any]:
    result = {
        "id": source_id,
        "label": label,
        "target": target,
        "status": status,
        "ready": bool(ready),
        "evidence_status": evidence_status,
        "validation_state": validation_state,
        "latest_run_id": latest_run_id,
        "as_of": as_of,
        "summary": summary,
    }
    if error:
        result["error"] = str(error)[:240]
    return result


def _opportunity_snapshot(user_id: str, repository: Any) -> tuple[dict, list[dict]]:
    runs = repository.list_runs(user_id=user_id, limit=10)
    successful = next(
        (item for item in runs if item.get("status") in {"succeeded", "partial"}),
        None,
    )
    latest = runs[0] if runs else None
    if successful is None:
        if latest and latest.get("status") == "failed":
            action = _action(
                f"opportunity-retry-{latest['id']}",
                "medium",
                "研究证据",
                "机会扫描失败，先修复数据缺口再重跑",
                "本次运行没有形成可验证候选，失败不能被解释为没有机会。",
                [latest.get("error_message") or "运行失败", f"Run：{latest['id']}"],
                "opportunities",
                "查看失败运行",
                "机会工厂不可变运行",
                evidence_status="unavailable",
                validation_state="blocked",
            )
            return _source(
                "opportunity",
                "机会工厂",
                "opportunities",
                status="failed",
                latest_run_id=latest.get("id"),
                as_of=latest.get("completed_at") or latest.get("created_at"),
                evidence_status="unavailable",
                validation_state="blocked",
                summary="最近一次扫描失败，未形成候选结论",
            ), [action]
        status = "running" if latest else "empty"
        return _source(
            "opportunity",
            "机会工厂",
            "opportunities",
            status=status,
            latest_run_id=(latest or {}).get("id"),
            as_of=(latest or {}).get("created_at"),
            summary="扫描正在运行" if latest else "尚未运行跨市场机会扫描",
        ), []

    run = repository.get_run(successful["id"], user_id=user_id, include_events=False)
    result = (run or {}).get("result") or {}
    verified = bool(run and run.get("result_verified"))
    funnel = result.get("funnel") or {}
    positions = ((result.get("portfolio") or {}).get("positions") or [])
    evidence_status = (
        "verified"
        if verified and run.get("status") == "succeeded"
        else "partial"
        if verified
        else "invalid"
    )
    ready = bool(verified and result and run.get("status") == "succeeded")
    baskets = repository.list_paper_baskets(user_id=user_id, limit=100)
    basket = next((item for item in baskets if item.get("run_id") == run.get("id")), None)
    actions: list[dict] = []
    validation_state = "not_required" if not positions else "paper_pending"
    if positions and basket is None:
        actions.append(_action(
            f"opportunity-freeze-{run['id']}",
            "medium" if run.get("status") == "partial" else "normal",
            "前瞻验证",
            "把最新候选冻结为纸面组合",
            "固定候选、入选价格和权重后再观察未来表现；这一步不会进入真实账户。",
            [
                f"入选持仓：{len(positions)} 只",
                f"已评估：{int(funnel.get('evaluated') or 0)} 只",
                f"不可用：{int(funnel.get('unavailable') or 0)} 只",
                "结果哈希已验证" if verified else "结果完整性未通过",
            ],
            "opportunities",
            "冻结纸面组合",
            "机会工厂运行 + 真实复权行情",
            evidence_status=evidence_status,
            validation_state="paper_pending",
        ))
    elif basket is not None and not basket.get("latest_observation"):
        validation_state = "paper_frozen"
        actions.append(_action(
            f"opportunity-observe-{basket['id']}",
            "normal",
            "前瞻验证",
            "为纸面组合记录首个真实观察点",
            "纸面组合已经冻结，但还没有前瞻结果；记录观察后才能区分历史拟合和后续表现。",
            [f"纸面组合：{basket['id']}", f"冻结持仓：{len(positions)} 只"],
            "opportunities",
            "记录纸面观察",
            "不可变纸面组合 + 真实复权行情",
            evidence_status=evidence_status,
            validation_state="paper_frozen",
        ))
    elif basket is not None:
        observation = basket.get("latest_observation") or {}
        payload = observation.get("payload") or {}
        validation_state = "paper_tracking"
        if not observation.get("payload_verified") or payload.get("status") == "partial":
            validation_state = "paper_incomplete"
            actions.append(_action(
                f"opportunity-observation-gap-{observation.get('id') or basket['id']}",
                "medium",
                "前瞻验证",
                "纸面组合观察不完整，需要补齐失败标的",
                "当前结果只覆盖成功返回的真实行情，不能把部分覆盖的收益当成完整组合表现。",
                [
                    f"覆盖权重：{payload.get('covered_position_weight_pct', '-')}%",
                    f"失败标的：{int(payload.get('failed_count') or 0)} 只",
                ],
                "opportunities",
                "复核纸面组合",
                "不可变纸面组合观察",
                evidence_status="partial",
                validation_state="paper_tracking",
            ))

    summary = (
        f"评估 {int(funnel.get('evaluated') or 0)} 只，合格 "
        f"{int(funnel.get('qualified') or 0)} 只，纸面持仓 {len(positions)} 只"
    )
    return _source(
        "opportunity",
        "机会工厂",
        "opportunities",
        status=str(run.get("status") or "partial"),
        ready=ready,
        evidence_status=evidence_status,
        validation_state=validation_state,
        latest_run_id=run.get("id"),
        as_of=run.get("completed_at") or result.get("generated_at"),
        summary=summary,
    ), actions


def _agent_snapshot(
    user_id: str,
    tenant_id: str,
    repository: Any,
) -> tuple[dict, list[dict]]:
    runs, _ = repository.list_runs(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=10,
    )
    research_run = next(
        (
            item
            for item in runs
            if item.get("status") in {"completed", "partial"} and item.get("result")
        ),
        None,
    )
    latest = runs[0] if runs else None
    if research_run is None:
        if latest and latest.get("status") in {"failed", "abstained"}:
            action = _action(
                f"agent-retry-{latest['id']}",
                "medium",
                "研究证据",
                "Agent 未形成可复核结论",
                "查看缺失 Evidence 或主动弃权原因，修复后再运行；失败结果不会生成方向建议。",
                [latest.get("error_message") or f"运行状态：{latest.get('status')}"],
                "agent",
                "查看 Agent 运行",
                "Agent Run 与审计事件",
                evidence_status="unavailable",
                validation_state="blocked",
            )
            return _source(
                "agent",
                "投资 Agent",
                "agent",
                status=str(latest.get("status")),
                latest_run_id=latest.get("id"),
                as_of=latest.get("completed_at") or latest.get("created_at"),
                evidence_status="unavailable",
                validation_state="blocked",
                summary="最近运行未形成研究结论",
            ), [action]
        return _source(
            "agent",
            "投资 Agent",
            "agent",
            status="running" if latest else "empty",
            latest_run_id=(latest or {}).get("id"),
            as_of=(latest or {}).get("created_at"),
            summary="研究正在运行" if latest else "尚无 Agent 深度研究",
        ), []

    integrity = repository.verify_run_evidence_integrity(research_run["id"])
    result = research_run.get("result") or {}
    conclusion = result.get("conclusion") or {}
    decision = (result.get("personalized_decision") or {}).get("decision") or {}
    verified = bool(integrity.get("verified"))
    complete = bool(
        research_run.get("status") == "completed"
        and conclusion.get("status") == "research_ready"
        and verified
    )
    evidence_status = "verified" if complete else "partial" if verified else "invalid"
    code = str((result.get("fund") or {}).get("code") or (research_run.get("input") or {}).get("code") or "-")
    action_code = str(decision.get("action") or conclusion.get("personal_action") or "research_only")
    actions = [_action(
        f"agent-review-{research_run['id']}",
        "normal" if complete else "medium",
        "Agent 研判",
        f"复核 Agent 对 {code} 的最新结论",
        "先核对引用 Evidence、个人政策门禁和结论适用范围，再决定是否写入自己的计划；系统不会代替你下单。",
        [
            conclusion.get("headline") or "已形成结构化研究结果",
            f"个人动作口径：{action_code}",
            f"Evidence：{'完整性已验证' if verified else '完整性未通过'}",
        ],
        "agent",
        "打开 Agent 结论",
        "Agent Run + Evidence + 审计哈希链",
        evidence_status=evidence_status,
        validation_state="decision_review_pending",
    )]
    return _source(
        "agent",
        "投资 Agent",
        "agent",
        status=str(research_run.get("status")),
        ready=complete,
        evidence_status=evidence_status,
        validation_state="decision_review_pending",
        latest_run_id=research_run.get("id"),
        as_of=research_run.get("completed_at") or research_run.get("updated_at"),
        summary=f"{code} · {conclusion.get('status') or '研究结果待复核'}",
    ), actions


def _twin_snapshot(
    user_id: str,
    tenant_id: str,
    repository: Any,
) -> tuple[dict, list[dict]]:
    runs = repository.list_runs(tenant_id=tenant_id, user_id=user_id, limit=1)
    if not runs:
        return _source(
            "twin",
            "组合情景实验室",
            "twin",
            status="empty",
            summary="尚无组合压力测试",
        ), []
    summary_run = runs[0]
    run = repository.get_run(
        summary_run["id"],
        tenant_id=tenant_id,
        user_id=user_id,
        include_evidence=True,
    )
    if run is None:
        raise RuntimeError("组合情景运行已不存在")
    result = run.get("result") or {}
    integrity = run.get("integrity") or {}
    decision_gate = result.get("decision_gate") or {}
    current = result.get("current") or {}
    budget = current.get("risk_budget") or {}
    repair = result.get("repair_plan") or {}
    verified = bool(integrity.get("verified"))
    eligible = bool(decision_gate.get("decision_eligible"))
    breached = bool(budget.get("breached"))
    evidence_status = "verified" if verified and eligible else "partial" if verified else "invalid"
    actions: list[dict] = []
    if breached:
        actions.append(_action(
            f"twin-risk-budget-{run['id']}",
            "high",
            "组合压力",
            "压力情景已突破你的亏损预算",
            "先复核冲击假设和暴露区间，再比较降险草案；草案只用于研究，不会自动执行。",
            [
                f"预算使用率：{budget.get('utilization_pct', '-')}%",
                f"情景最坏损失：{budget.get('worst_loss_amount', '-')} ",
                f"建议转现金名义额：{repair.get('total_shift_to_cash', 0)}",
            ],
            "twin",
            "打开情景实验室",
            "不可变组合孪生运行",
            evidence_status=evidence_status,
            validation_state="scenario_review_pending",
        ))
    elif not eligible or not verified:
        actions.append(_action(
            f"twin-evidence-gap-{run['id']}",
            "medium",
            "组合压力",
            "压力测试存在证据门禁缺口",
            "当前结果可用于理解情景，但不能据此形成调仓结论；请先补齐持仓、穿透或投资政策证据。",
            list(decision_gate.get("reasons") or ["运行完整性或决策门禁未通过"]),
            "twin",
            "复核证据缺口",
            "不可变组合孪生运行",
            evidence_status=evidence_status,
            validation_state="blocked",
        ))
    return _source(
        "twin",
        "组合情景实验室",
        "twin",
        status=str(run.get("status") or result.get("status") or "partial"),
        ready=bool(verified and eligible and result),
        evidence_status=evidence_status,
        validation_state="scenario_review_pending" if breached else "scenario_reviewed",
        latest_run_id=run.get("id"),
        as_of=run.get("created_at"),
        summary=(
            f"预算使用率 {budget.get('utilization_pct', '-')}% · "
            f"{'已破线' if breached else '未破线'}"
        ),
    ), actions


def build_research_snapshot(
    *,
    user_id: str = "default",
    tenant_id: str = "public",
    opportunity_repo: Any | None = None,
    agent_repo: Any | None = None,
    twin_repo: Any | None = None,
) -> dict[str, Any]:
    """Read the latest durable outputs without allowing one source to hide another."""
    def load_opportunity() -> tuple[dict, list[dict]]:
        repository = opportunity_repo
        if repository is None:
            from opportunity_repository import repository as default_repository

            repository = default_repository
        return _opportunity_snapshot(user_id, repository)

    def load_agent() -> tuple[dict, list[dict]]:
        repository = agent_repo
        if repository is None:
            from agent.repository import AgentRepository

            repository = AgentRepository()
        return _agent_snapshot(user_id, tenant_id, repository)

    def load_twin() -> tuple[dict, list[dict]]:
        repository = twin_repo
        if repository is None:
            from portfolio_twin_repository import PortfolioTwinRepository

            repository = PortfolioTwinRepository()
        return _twin_snapshot(user_id, tenant_id, repository)

    loaders = (
        ("opportunity", "机会工厂", "opportunities", load_opportunity),
        ("agent", "投资 Agent", "agent", load_agent),
        ("twin", "组合情景实验室", "twin", load_twin),
    )
    sources: list[dict] = []
    actions: list[dict] = []
    errors: list[dict] = []
    for source_id, label, target, loader in loaders:
        try:
            source, source_actions = loader()
            sources.append(source)
            actions.extend(source_actions)
        except Exception as error:
            message = str(error)[:240]
            sources.append(_source(
                source_id,
                label,
                target,
                status="unavailable",
                evidence_status="unavailable",
                validation_state="blocked",
                summary="持久化研究结果读取失败",
                error=message,
            ))
            errors.append({"scope": label, "error": message})

    ready_count = sum(bool(item.get("ready")) for item in sources)
    partial_count = sum(
        item.get("evidence_status") == "partial" for item in sources
    )
    unavailable_count = sum(item.get("status") == "unavailable" for item in sources)
    paper_tracking_count = sum(item.get("validation_state") == "paper_tracking" for item in sources)
    paper_pending_count = sum(
        item.get("validation_state") in {
            "paper_pending",
            "paper_frozen",
            "paper_incomplete",
        }
        for item in sources
    )
    status = (
        "unavailable"
        if unavailable_count == len(sources)
        else "partial"
        if unavailable_count
        else "available"
    )
    return {
        "schema_version": "decision_research_sources.v1",
        "status": status,
        "sources": sources,
        "actions": actions,
        "errors": errors,
        "resolution_evidence_complete": unavailable_count == 0,
        "summary": {
            "source_count": len(sources),
            "ready_source_count": ready_count,
            "partial_source_count": partial_count,
            "unavailable_source_count": unavailable_count,
            "paper_tracking_count": paper_tracking_count,
            "paper_pending_count": paper_pending_count,
        },
    }
