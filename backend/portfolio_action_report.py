# -*- coding: utf-8 -*-
"""Auditable, real-data-only portfolio action reports.

The report orders risk-control and evidence-completion work. It deliberately
does not convert short-term fund performance into a buy or sell instruction.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable

import holdings as holdings_mod
import holding_thesis
import portfolio_review
import portfolio_valuation
import storage
from portfolio_exposure import sha256_payload


SCHEMA_VERSION = "portfolio_action_report.v2"
RULESET_VERSION = "portfolio_action_rules.v3"
MAX_FUNDS = 8

_ACTION_ORDER = {
    "data_required": 0,
    "reduce_review": 1,
    "pause_add": 2,
    "risk_review": 3,
    "thesis_review": 4,
    "hold_review": 5,
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def action_holdings_sha256(items: list[dict[str, Any]]) -> str:
    """Hash every holding field that can change an action conclusion."""
    rows = [
        {
            "asset_type": str(item.get("asset_type") or ""),
            "market": str(item.get("market") or ""),
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or ""),
            "amount": _number(item.get("amount")),
            "cost": _number(item.get("cost")),
            "yesterday_profit": _number(item.get("yesterday_profit")),
            "profit": _number(item.get("profit")),
            "profit_rate": _number(item.get("profit_rate")),
            "shares": _number(item.get("shares")),
            "source": str(item.get("source") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }
        for item in items
    ]
    rows.sort(key=lambda row: (row["asset_type"], row["market"], row["code"]))
    return sha256_payload(rows)


def _asset_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("asset_type") or ""), str(item.get("code") or "")


def _thesis_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("asset_type") or ""),
        str(item.get("market") or ""),
        str(item.get("code") or ""),
    )


def _safe_call(provider: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
    try:
        value = provider()
        if not isinstance(value, dict):
            raise TypeError("工具返回格式不是对象")
        return value, None
    except Exception as exc:
        return {}, str(exc)[:240]


def _safe_list_call(provider: Callable[[], list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], str | None]:
    try:
        value = provider()
        if not isinstance(value, list):
            raise TypeError("工具返回格式不是列表")
        return value, None
    except Exception as exc:
        return [], str(exc)[:240]


def _evidence(label: str, value: Any, source: str) -> dict[str, Any]:
    return {"label": label, "value": value, "source": source}


def _decision(
    action: str,
    label: str,
    rationale: str,
    *,
    amount: float | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "label": label,
        "rationale": rationale,
        "review_amount": _round(amount),
        "blockers": blockers or [],
    }


def _strategy_step(
    step_id: str,
    priority: str,
    title: str,
    instruction: str,
    why: str,
    evidence: list[dict[str, Any]],
    target_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "priority": priority,
        "title": title,
        "instruction": instruction,
        "why": why,
        "target_codes": target_codes or [],
        "evidence": evidence,
    }


def build_action_report(
    *,
    max_funds: int = MAX_FUNDS,
    user_id: str = "default",
    holdings_provider: Callable[[], list[dict[str, Any]]] | None = None,
    profile_provider: Callable[[], dict[str, Any]] | None = None,
    insights_provider: Callable[[int], dict[str, Any]] | None = None,
    ledger_provider: Callable[[], dict[str, Any]] | None = None,
    performance_provider: Callable[[], dict[str, Any]] | None = None,
    rebalance_provider: Callable[[], dict[str, Any]] | None = None,
    theses_provider: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic report from confirmed holdings and real providers."""
    max_funds = max(2, min(MAX_FUNDS, int(max_funds)))
    uses_runtime_valuation = holdings_provider is None
    profile_provider = profile_provider or (lambda: storage.get_investment_profile(user_id=user_id))
    ledger_provider = ledger_provider or (lambda: portfolio_review.ledger_overview(user_id=user_id))
    performance_provider = performance_provider or (
        lambda: portfolio_review.cashflow_performance(user_id=user_id)
    )
    rebalance_provider = rebalance_provider or (
        lambda: portfolio_review.rebalance_review(user_id=user_id)
    )
    theses_provider = theses_provider or (lambda: holding_thesis.latest_theses(user_id=user_id))

    if uses_runtime_valuation:
        items, valuation = portfolio_valuation.current_valued_holdings(user_id=user_id)
    else:
        items = holdings_provider()
        valuation = {
            "status": "not_requested",
            "snapshot": None,
            "binding": {"current": True},
            "runtime_gate": {"risk_analysis_eligible": True},
        }
    if insights_provider is None:
        def insights_provider(limit: int) -> dict[str, Any]:
            raw = holdings_mod.holdings_insights(limit, user_id=user_id)
            return portfolio_valuation.overlay_insights_with_valuation(raw, valuation)

    profile = profile_provider()
    holdings_hash = action_holdings_sha256(items)
    insights, insights_error = _safe_call(lambda: insights_provider(max_funds))
    ledger, ledger_error = _safe_call(ledger_provider)
    performance, performance_error = _safe_call(performance_provider)
    rebalance, rebalance_error = _safe_call(rebalance_provider)
    theses, theses_error = _safe_list_call(theses_provider)
    relevant_theses = holding_thesis.theses_for_holdings(theses, items)
    theses_hash = holding_thesis.theses_sha256(relevant_theses)

    total_amount = sum(max(0.0, _number(item.get("amount")) or 0.0) for item in items)
    valuation_snapshot = valuation.get("snapshot") or {}
    valuation_gate = valuation.get("runtime_gate") or {}
    valuation_required = bool(uses_runtime_valuation and items)
    valuation_eligible = bool(
        not valuation_required
        or (
            valuation.get("status") == "available"
            and (valuation.get("binding") or {}).get("current")
            and valuation_gate.get("risk_analysis_eligible")
        )
    )
    amount_complete = bool(items) and all(
        _number(item.get("amount")) is not None and (_number(item.get("amount")) or 0) > 0
        for item in items
    )
    profile_configured = bool(profile.get("configured"))
    profile_version_id = profile.get("profile_version_id") if profile_configured else None

    allocation_by_key = {
        _asset_key(row): row for row in insights.get("allocation") or []
    }
    trend_by_code = {
        str(row.get("code") or ""): row for row in insights.get("fund_trends") or []
    }
    trend_errors = {
        str(row.get("code") or ""): row.get("error")
        for row in insights.get("fund_errors") or []
        if row.get("code")
    }
    rebalance_by_key = {
        _asset_key(row): row for row in rebalance.get("allocations") or []
    }
    ledger_by_key = {
        _asset_key(row): row for row in ledger.get("positions") or []
    }
    thesis_by_key = {
        _thesis_key(row): row
        for row in relevant_theses
        if row.get("state") == "active"
    }

    overlap = insights.get("overlap") or {}
    overlap_error = insights.get("overlap_error")
    pairwise = overlap.get("pairwise") or []
    overlap_by_code: dict[str, list[dict[str, Any]]] = {}
    for pair in pairwise:
        for code, peer_code, peer_name in (
            (pair.get("fund_a"), pair.get("fund_b"), pair.get("fund_b_name")),
            (pair.get("fund_b"), pair.get("fund_a"), pair.get("fund_a_name")),
        ):
            if not code:
                continue
            overlap_by_code.setdefault(str(code), []).append({
                "peer_code": str(peer_code or ""),
                "peer_name": peer_name or "",
                "level": pair.get("level"),
                "stock_overlap_weight": pair.get("stock_overlap_weight"),
                "industry_overlap_weight": pair.get("industry_overlap_weight"),
                "common_stock_count": pair.get("common_stock_count"),
                "common_stocks": pair.get("common_stocks") or [],
                "common_industries": pair.get("common_industries") or [],
            })
    for rows in overlap_by_code.values():
        rows.sort(
            key=lambda row: max(
                _number(row.get("stock_overlap_weight")) or 0,
                _number(row.get("industry_overlap_weight")) or 0,
            ),
            reverse=True,
        )

    holding_rows = []
    for item in items:
        key = _asset_key(item)
        code = str(item.get("code") or "")
        amount = _number(item.get("amount"))
        allocation = allocation_by_key.get(key) or {}
        ratio = _number(allocation.get("ratio"))
        if ratio is None and amount is not None and total_amount > 0:
            ratio = amount / total_amount * 100
        trend = trend_by_code.get(code)
        rebalance_row = rebalance_by_key.get(key) or {}
        ledger_row = ledger_by_key.get(key)
        thesis_record = thesis_by_key.get(_thesis_key(item))
        overlap_rows = overlap_by_code.get(code) or []
        high_overlap = [
            row for row in overlap_rows if row.get("level") in {"高度重合", "中度重合"}
        ]
        excess_amount = _number(rebalance_row.get("excess_amount")) or 0
        historical_drawdown = _number((trend or {}).get("max_drawdown"))
        current_drawdown = _number((trend or {}).get("current_drawdown"))
        drawdown_limit = _number(profile.get("max_drawdown_pct")) if profile_configured else None
        thesis_assessment = (
            {
                "status": "source_unavailable",
                "label": "持有逻辑数据源不可用",
                "review_due": False,
                "breaches": [],
                "evidence": [],
                "error": theses_error,
            }
            if theses_error
            else holding_thesis.evaluate_thesis(
                thesis_record,
                holding=item,
                trend=trend,
            )
        )

        amount_from_valuation = bool(item.get("valuation_snapshot_id"))
        evidence = [
            _evidence(
                "当前人民币估值" if amount_from_valuation else "已确认金额",
                _round(amount),
                "不可变组合估值快照" if amount_from_valuation else "用户确认持仓",
            ),
            _evidence(
                "组合占比",
                _round(ratio),
                "不可变组合估值快照" if amount_from_valuation else "用户确认持仓金额",
            ),
            _evidence("累计收益", _round(_number(item.get("profit"))), "用户确认持仓"),
            _evidence("累计收益率", _round(_number(item.get("profit_rate"))), "用户确认持仓"),
        ]
        if trend:
            evidence.extend([
                _evidence("基金净值日期", trend.get("as_of"), trend.get("source") or "基金真实净值"),
                _evidence("近3月收益", trend.get("return_3m"), trend.get("source") or "基金真实净值"),
                _evidence("当前回撤", trend.get("current_drawdown"), trend.get("source") or "基金真实净值"),
            ])
        if thesis_record:
            thesis_payload = thesis_record.get("payload") or {}
            evidence.extend([
                _evidence("持有逻辑版本", thesis_record.get("version_no"), "不可变持有逻辑版本库"),
                _evidence("组合角色", thesis_payload.get("role_label"), "用户确认持有逻辑"),
                _evidence("下次复核日期", thesis_payload.get("review_date"), "用户确认持有逻辑"),
            ])

        blockers = []
        if amount is None or amount <= 0:
            decision = _decision(
                "data_required",
                "补全金额后再决策",
                "缺少有效持仓金额，系统拒绝计算仓位和调整额度。",
                blockers=["holding_amount_missing"],
            )
        elif valuation_required and not valuation_eligible:
            decision = _decision(
                "data_required",
                "刷新可信估值后再复盘仓位",
                "当前价格、净值、汇率、时效或持仓绑定门禁未通过，系统不会继续使用旧金额生成仓位动作。",
                blockers=["portfolio_valuation_not_current"],
            )
        elif not profile_configured:
            decision = _decision(
                "data_required",
                "先激活投资政策",
                "没有经过确认的单品、权益和行业上限，系统不会使用默认值替你做决定。",
                blockers=["investment_policy_not_active"],
            )
        elif excess_amount > 0:
            decision = _decision(
                "reduce_review",
                "暂停新增，复核降仓",
                "当前仓位超过你已激活的单品上限；先核对赎回费、税费和持有逻辑，再决定是否分批降低。",
                amount=excess_amount,
            )
        elif high_overlap:
            decision = _decision(
                "pause_add",
                "暂停新增，先做去重",
                "真实定期报告显示与现有基金存在中高重复暴露，同时加仓不会带来同等程度的分散。",
            )
        elif (
            drawdown_limit is not None
            and historical_drawdown is not None
            and abs(historical_drawdown) > drawdown_limit
        ):
            decision = _decision(
                "risk_review",
                "风险不匹配，优先复核",
                "该基金历史最大回撤超过你确认的承受上限；在完成风险复核前不扩大仓位。",
            )
        elif item.get("asset_type") == "fund" and not trend:
            error = trend_errors.get(code) or "真实基金趋势证据不可用"
            blockers.append("fund_market_evidence_unavailable")
            decision = _decision(
                "data_required",
                "真实数据恢复前不操作",
                "净值或风险证据未成功返回，系统不会用估算数据替代。",
                blockers=blockers,
            )
            evidence.append(_evidence("数据缺口", error, "真实基金数据源"))
        elif thesis_assessment.get("status") == "source_unavailable":
            decision = _decision(
                "data_required",
                "持有逻辑数据恢复前不操作",
                "持有逻辑版本库未成功返回，系统无法确认原计划或风险边界。",
                blockers=["holding_thesis_source_unavailable"],
            )
            evidence.append(_evidence("数据缺口", theses_error, "持有逻辑版本库"))
        elif thesis_assessment.get("status") == "unavailable":
            decision = _decision(
                "data_required",
                "持有逻辑完整性失败",
                "持有逻辑版本或哈希校验失败，修复前不使用其中的风险边界。",
                blockers=["holding_thesis_integrity_failed"],
            )
        elif thesis_assessment.get("status") == "missing":
            decision = _decision(
                "thesis_review",
                "补充持有逻辑与退出纪律",
                "尚未记录为什么持有、计划多久以及何时退出；先建立计划，再讨论新增资金。",
                blockers=["holding_thesis_missing"],
            )
        elif thesis_assessment.get("status") == "risk_limit_breached":
            decision = _decision(
                "thesis_review",
                "纪律边界触发，立即复核",
                "当前真实证据已触及你预先确认的亏损或回撤边界；这要求复核原逻辑，不等于自动卖出。",
                blockers=[
                    str(breach.get("code"))
                    for breach in thesis_assessment.get("breaches") or []
                ],
            )
        elif thesis_assessment.get("status") == "review_due":
            decision = _decision(
                "thesis_review",
                "持有逻辑到期复核",
                "已经到达你设定的复核日期；应按原加仓和退出条件逐项核对，不能因当前盈亏跳过。",
            )
        elif current_drawdown is not None and current_drawdown <= -10:
            decision = _decision(
                "thesis_review",
                "保持仓位，复核持有逻辑",
                "回撤本身不是卖出或补仓信号；先核对投资期限、同类表现和原始买入理由。",
            )
        else:
            decision = _decision(
                "hold_review",
                "保持仓位，按计划复核",
                "当前未触发单品上限、重复暴露或风险承受门禁；这不是收益预测，也不自动放行新增资金。",
            )

        holding_rows.append({
            "id": item.get("id"),
            "asset_type": item.get("asset_type"),
            "market": item.get("market") or "",
            "code": code,
            "name": item.get("name") or code,
            "amount": _round(amount),
            "cost": _round(_number(item.get("cost"))),
            "yesterday_profit": _round(_number(item.get("yesterday_profit"))),
            "profit": _round(_number(item.get("profit"))),
            "profit_rate": _round(_number(item.get("profit_rate"))),
            "shares": _round(_number(item.get("shares")), 6),
            "allocation_ratio": _round(ratio),
            "source": item.get("source") or "",
            "updated_at": item.get("updated_at"),
            "decision": decision,
            "rebalance": rebalance_row or None,
            "trend": trend,
            "ledger": ledger_row,
            "thesis": ({
                "id": thesis_record.get("id"),
                "version_no": thesis_record.get("version_no"),
                "payload_sha256": thesis_record.get("payload_sha256"),
                "integrity_verified": thesis_record.get("integrity_verified"),
                "payload": thesis_record.get("payload") or {},
            } if thesis_record else None),
            "thesis_review": thesis_assessment,
            "overlap": overlap_rows,
            "evidence": evidence,
        })

    holding_rows.sort(
        key=lambda row: (
            _ACTION_ORDER.get((row.get("decision") or {}).get("action"), 99),
            -(_number(row.get("amount")) or 0),
        )
    )

    steps: list[dict[str, Any]] = []
    if not items:
        steps.append(_strategy_step(
            "confirm-holdings", "high", "先确认真实持仓",
            "导入并逐项核对当前金额、累计收益和份额。",
            "没有组合事实时，任何仓位或盈利策略都不可验证。",
            [_evidence("已确认持仓", 0, "用户持仓数据库")],
        ))
    elif not amount_complete:
        missing = [row.get("code") for row in items if (_number(row.get("amount")) or 0) <= 0]
        steps.append(_strategy_step(
            "complete-amounts", "high", "补齐持仓金额",
            "先补齐缺失金额，再生成新的行动报告。",
            "不完整金额会扭曲集中度、收益贡献和调整额度。",
            [_evidence("缺失金额数量", len(missing), "用户确认持仓")],
            [str(code) for code in missing if code],
        ))
    if items and not profile_configured:
        steps.append(_strategy_step(
            "activate-policy", "high", "激活个人投资政策",
            "确认单品、权益、行业上限和最大可承受回撤。",
            "只有用户确认的边界才能决定何时暂停新增或复核减仓。",
            [_evidence("投资政策状态", "未激活", "投资政策版本库")],
        ))

    over_limit_rows = [
        row for row in holding_rows if (row.get("decision") or {}).get("action") == "reduce_review"
    ]
    for row in over_limit_rows:
        steps.append(_strategy_step(
            f"reduce-review-{row['code']}", "high", f"复核 {row['name']} 的超限仓位",
            f"停止新增；复核约 {row['decision']['review_amount'] or 0:,.2f} 的超限部分，并在确认费用后决定是否分批调整。",
            "先降低无意集中，能减少单一产品回撤对组合长期复利的破坏。",
            [
                _evidence("当前占比", row.get("allocation_ratio"), "用户确认持仓"),
                _evidence("单品上限", (row.get("rebalance") or {}).get("max_single_ratio"), "已激活投资政策"),
            ],
            [row["code"]],
        ))

    breached_thesis_rows = [
        row for row in holding_rows
        if (row.get("thesis_review") or {}).get("status") == "risk_limit_breached"
    ]
    for row in breached_thesis_rows[:4]:
        breaches = (row.get("thesis_review") or {}).get("breaches") or []
        steps.append(_strategy_step(
            f"thesis-risk-review-{row['code']}",
            "high",
            f"按预设纪律复核 {row['name']}",
            "逐条检查原持有逻辑、真实风险证据和退出条件；确认逻辑是否仍成立，再决定保持、暂停新增或调整。",
            "风险边界是在买入前或情绪稳定时设定的复核触发器，用它约束临场冲动，但不将阈值直接转换为卖出指令。",
            [
                _evidence(item.get("label") or item.get("code"), item.get("actual"), item.get("source") or "持有逻辑复核")
                for item in breaches
            ],
            [row["code"]],
        ))

    high_pairs = [pair for pair in pairwise if pair.get("level") in {"高度重合", "中度重合"}]
    for pair in high_pairs[:4]:
        a = str(pair.get("fund_a") or "")
        b = str(pair.get("fund_b") or "")
        steps.append(_strategy_step(
            f"deduplicate-{a}-{b}", "medium", f"复核 {a} 与 {b} 的重复暴露",
            "暂停同时新增；比较费用、跟踪目标、经理稳定性和同类长期表现后，只保留各自明确的组合角色。",
            "减少重复持股和行业暴露，能让新增资金真正改善分散，而不是只增加基金数量。",
            [
                _evidence("共同持股重合", pair.get("stock_overlap_weight"), "基金定期报告"),
                _evidence("共同产业重合", pair.get("industry_overlap_weight"), "基金定期报告"),
            ],
            [a, b],
        ))

    missing_thesis_rows = [
        row for row in holding_rows
        if (row.get("thesis_review") or {}).get("status") == "missing"
    ]
    if missing_thesis_rows:
        steps.append(_strategy_step(
            "complete-holding-theses",
            "medium",
            "补齐持有逻辑与退出纪律",
            "为每只持仓确认组合角色、计划期限、复核日期、最大可接受亏损与回撤、加仓条件和退出条件。",
            "没有事先定义的持有和退出规则，盈利后容易过度集中，亏损后也容易把情绪当成策略。",
            [_evidence("缺少持有逻辑", len(missing_thesis_rows), "不可变持有逻辑版本库")],
            [row["code"] for row in missing_thesis_rows[:8]],
        ))

    due_thesis_rows = [
        row for row in holding_rows
        if (row.get("thesis_review") or {}).get("status") == "review_due"
    ]
    if due_thesis_rows:
        steps.append(_strategy_step(
            "review-due-theses",
            "medium",
            "完成到期的持有逻辑复核",
            "按保存的加仓与退出条件逐项核对，并将确认后的新计划保存为下一版本。",
            "定期复核用于判断目标、期限或风险承受能力是否变化，而不是根据热门板块追涨切换。",
            [_evidence("到期复核数量", len(due_thesis_rows), "用户确认复核日期")],
            [row["code"] for row in due_thesis_rows[:8]],
        ))

    ledger_summary = ledger.get("summary") or {}
    if items and not ledger_error and int(ledger_summary.get("transaction_count") or 0) == 0:
        steps.append(_strategy_step(
            "complete-ledger", "medium", "补录交易流水或期初持仓",
            "录入买入、卖出、份额、成交价和费用，再判断策略是否真的提高税费后收益。",
            "只看账户累计收益无法区分市场上涨、追加投入和策略本身的贡献。",
            [_evidence("已录入交易", 0, "交易流水")],
        ))
    elif int(ledger_summary.get("integrity_issue_count") or 0) > 0:
        steps.append(_strategy_step(
            "repair-ledger", "high", "修复交易流水份额缺口",
            "补录缺失的买入或期初仓位，修复后重新生成报告。",
            "份额不闭合时，成本、已实现收益和税费后表现都不能用于决策。",
            [_evidence("份额缺口", ledger_summary.get("integrity_issue_count"), "交易流水")],
        ))

    total_profit = _number((insights.get("summary") or {}).get("total_profit"))
    if total_profit is not None and total_profit < 0:
        losing = [row for row in holding_rows if (_number(row.get("profit")) or 0) < 0]
        losing.sort(key=lambda row: _number(row.get("profit")) or 0)
        steps.append(_strategy_step(
            "review-loss-drivers", "medium", "先拆解亏损来源，不盲目补仓",
            "逐项核对亏损是否来自不匹配的市场、重复暴露、超限仓位或原持有逻辑失效。",
            "亏损率不是未来收益率；先处理可控制的组合结构，再讨论择时。",
            [_evidence("组合累计收益", _round(total_profit), "用户确认持仓")],
            [row["code"] for row in losing[:3]],
        ))

    if items and not steps:
        steps.append(_strategy_step(
            "maintain-discipline", "normal", "维持现有仓位纪律",
            "不因单日涨跌交易；在下一次投入前刷新报告，并只研究未超限、低重复且风险匹配的方向。",
            "降低无效换手和追涨杀跌，有助于保留长期复利，但不保证获得正收益。",
            [_evidence("已触发高优先级问题", 0, RULESET_VERSION)],
        ))

    fund_codes = sorted({
        str(item.get("code") or "")
        for item in items
        if item.get("asset_type") == "fund" and item.get("code")
    })[:max_funds]
    expected_pairs = len(fund_codes) * (len(fund_codes) - 1) // 2
    actual_pairs = len(pairwise)
    source_errors = [
        {"scope": "组合体检", "error": insights_error},
        {"scope": "基金重合度", "error": overlap_error},
        {"scope": "交易账本", "error": ledger_error},
        {"scope": "现金流收益", "error": performance_error},
        {"scope": "再平衡", "error": rebalance_error},
        {"scope": "持有逻辑", "error": theses_error},
    ]
    source_errors.extend(
        {"scope": row.get("code") or "基金", "error": row.get("error")}
        for row in insights.get("fund_errors") or []
    )
    if valuation_required and not valuation_eligible:
        source_errors.append({
            "scope": "组合估值",
            "error": "；".join(
                str(item)
                for item in valuation_gate.get("reasons")
                or ["估值快照缺失、过期或未绑定当前持仓"]
            ),
        })
    source_errors = [row for row in source_errors if row.get("error")]

    blocked = not items or not amount_complete or not profile_configured or not valuation_eligible
    partial = bool(
        source_errors
        or (expected_pairs > 0 and actual_pairs < expected_pairs)
        or (overlap.get("failed") or [])
    )
    status = "blocked" if blocked else "partial" if partial else "reviewable"
    as_of_values = [str(item.get("updated_at") or "") for item in items if item.get("updated_at")]
    as_of_values.extend(
        str(row.get("as_of")) for row in trend_by_code.values() if row.get("as_of")
    )
    if valuation_snapshot.get("created_at"):
        as_of_values.append(str(valuation_snapshot["created_at"]))
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    return {
        "schema_version": SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "generated_at": generated_at,
        "as_of": max(as_of_values) if as_of_values else None,
        "status": status,
        "holdings_sha256": holdings_hash,
        "valuation_required": valuation_required,
        "valuation_snapshot_id": valuation_snapshot.get("id"),
        "theses_sha256": theses_hash,
        "profile_version_id": profile_version_id,
        "policy": "报告只排序风险控制与证据补全动作，不承诺盈利，不把短期涨跌直接转换为买卖指令。",
        "objective": "提高费用后、风险调整后的长期盈利概率，同时限制单一产品和重复暴露造成的不可控损失。",
        "summary": {
            "holding_count": len(items),
            "total_amount": _round(total_amount) if total_amount > 0 else None,
            "total_profit": _round(total_profit),
            "weighted_profit_rate": (insights.get("summary") or {}).get("weighted_profit_rate"),
            "top1_ratio": (insights.get("summary") or {}).get("top1_ratio"),
            "top3_ratio": (insights.get("summary") or {}).get("top3_ratio"),
            "high_priority_count": sum(step["priority"] == "high" for step in steps),
            "medium_priority_count": sum(step["priority"] == "medium" for step in steps),
            "high_overlap_pair_count": len(high_pairs),
            "thesis_active_count": None if theses_error else len(thesis_by_key),
            "thesis_missing_count": None if theses_error else len(missing_thesis_rows),
            "thesis_review_due_count": len(due_thesis_rows),
            "thesis_breach_count": len(breached_thesis_rows),
        },
        "readiness": {
            "status": status,
            "amount_complete": amount_complete,
            "valuation_eligible": valuation_eligible,
            "valuation_snapshot_id": valuation_snapshot.get("id"),
            "profile_configured": profile_configured,
            "thesis_status": "unavailable" if theses_error else (
                "complete" if not missing_thesis_rows else "incomplete"
            ),
            "thesis_active_count": None if theses_error else len(thesis_by_key),
            "thesis_missing_count": None if theses_error else len(missing_thesis_rows),
            "ledger_status": "unavailable" if ledger_error else (
                "incomplete" if int(ledger_summary.get("transaction_count") or 0) == 0 else "available"
            ),
            "performance_status": "unavailable" if performance_error else performance.get("status") or "unavailable",
            "fund_trend_requested": len(fund_codes),
            "fund_trend_available": len(trend_by_code),
            "overlap_expected_pairs": expected_pairs,
            "overlap_available_pairs": actual_pairs,
            "source_errors": source_errors,
        },
        "strategy": {
            "title": "提高盈利概率的操作顺序",
            "steps": steps[:10],
            "guardrails": [
                "不因单日涨跌或浮亏比例自动买卖。",
                "新增资金必须先通过已激活投资政策、真实数据和重复暴露检查。",
                "用户填写的自由文本加仓和退出条件只供人工核对，不冒充机器已验证信号。",
                "减仓金额只表示相对用户上限的超出部分，执行前仍需核对赎回费、税费和流动性。",
                "真实来源失败时停止对应结论，不使用模拟数据或替代值补齐。",
            ],
        },
        "holdings": holding_rows,
        "overlap": {
            "status": "unavailable" if overlap_error else "partial" if overlap.get("failed") else "available" if overlap else "not_applicable",
            "source": overlap.get("source"),
            "source_url": overlap.get("source_url"),
            "summary": overlap.get("summary") or {},
            "funds": overlap.get("funds") or [],
            "pairs": pairwise,
            "shared_stocks": overlap.get("shared_stocks") or [],
            "shared_industries": overlap.get("shared_industries") or [],
            "failed": overlap.get("failed") or [],
            "error": overlap_error,
            "method": overlap.get("method") or {},
        },
        "portfolio_evidence": {
            "profile": {
                "configured": profile_configured,
                "version_id": profile_version_id,
                "max_single_ratio": profile.get("max_single_ratio") if profile_configured else None,
                "max_equity_ratio": profile.get("max_equity_ratio") if profile_configured else None,
                "max_industry_ratio": profile.get("max_industry_ratio") if profile_configured else None,
                "max_drawdown_pct": profile.get("max_drawdown_pct") if profile_configured else None,
            },
            "ledger_summary": ledger_summary,
            "performance": {
                "status": performance.get("status") or "unavailable",
                "summary": performance.get("summary") or {},
                "reasons": performance.get("reasons") or [],
            },
            "holding_theses": {
                "schema_version": holding_thesis.SCHEMA_VERSION,
                "snapshot_sha256": theses_hash,
                "active_count": None if theses_error else len(thesis_by_key),
                "missing_count": None if theses_error else len(missing_thesis_rows),
            },
        },
        "method": {
            "allocation": "生产默认使用当前不可变人民币估值；门禁不通过时停止仓位动作，不按名称或模拟价格补齐。",
            "overlap": "共同持股和行业重合只来自基金定期报告；披露期会随每只基金一并展示。",
            "action_order": "数据完整性 > 用户风险边界 > 单品超限 > 中高重复暴露 > 预设持有纪律 > 回撤复核 > 保持纪律。",
            "profitability": "通过减少无意集中、重复暴露、无效换手和收益口径错误来提高长期盈利概率，而不是预测必然上涨。",
            "thesis": "结构化阈值使用用户确认持仓和基金真实净值核验；自由文本条件始终标记为人工核对。",
        },
    }


def persist_action_report(payload: dict[str, Any], *, user_id: str = "default") -> dict[str, Any]:
    saved = storage.save_portfolio_action_report(payload, user_id=user_id)
    integrity = storage.verify_portfolio_action_report(saved["id"], user_id=user_id)
    reasons = []
    if payload.get("valuation_required") and not (payload.get("readiness") or {}).get(
        "valuation_eligible"
    ):
        reasons.append("portfolio_valuation_not_current")
    return {
        **payload,
        "report": saved,
        "integrity": integrity,
        "binding": {"current": not reasons, "reasons": reasons},
    }


def refresh_action_report(*, max_funds: int = MAX_FUNDS, user_id: str = "default") -> dict[str, Any]:
    payload = build_action_report(max_funds=max_funds, user_id=user_id)
    return persist_action_report(payload, user_id=user_id)


def load_action_report(report_id: str, *, user_id: str = "default") -> dict[str, Any] | None:
    item = storage.get_portfolio_action_report(report_id, user_id=user_id, include_payload=True)
    if not item or not isinstance(item.get("payload"), dict):
        return None
    integrity = storage.verify_portfolio_action_report(report_id, user_id=user_id)
    current_holdings, current_valuation = portfolio_valuation.current_valued_holdings(
        user_id=user_id
    )
    current_holdings_hash = action_holdings_sha256(current_holdings)
    current_theses_hash = holding_thesis.theses_sha256(holding_thesis.theses_for_holdings(
        holding_thesis.latest_theses(user_id=user_id),
        current_holdings,
    ))
    profile = storage.get_investment_profile(user_id=user_id)
    current_profile_version_id = profile.get("profile_version_id") if profile.get("configured") else None
    reasons = []
    if item.get("holdings_sha256") != current_holdings_hash:
        reasons.append("holdings_changed")
    report_payload = item.get("payload") or {}
    if report_payload.get("valuation_required"):
        current_gate = current_valuation.get("runtime_gate") or {}
        current_snapshot_id = (current_valuation.get("snapshot") or {}).get("id")
        if not current_gate.get("risk_analysis_eligible"):
            reasons.append("portfolio_valuation_not_current")
        elif report_payload.get("valuation_snapshot_id") != current_snapshot_id:
            reasons.append("portfolio_valuation_changed")
    if item.get("theses_sha256") != current_theses_hash:
        reasons.append("holding_theses_changed")
    if item.get("profile_version_id") != current_profile_version_id:
        reasons.append("investment_policy_changed")
    if item.get("schema_version") != SCHEMA_VERSION or item.get("ruleset_version") != RULESET_VERSION:
        reasons.append("report_rules_changed")
    if not integrity.get("verified"):
        reasons.append("integrity_failed")
    return {
        **item["payload"],
        "report": {key: value for key, value in item.items() if key != "payload"},
        "integrity": integrity,
        "binding": {"current": not reasons, "reasons": reasons},
    }


def load_latest_action_report(*, user_id: str = "default") -> dict[str, Any] | None:
    reports = storage.list_portfolio_action_reports(user_id=user_id, limit=1)
    if not reports:
        return None
    return load_action_report(reports[0]["id"], user_id=user_id)
