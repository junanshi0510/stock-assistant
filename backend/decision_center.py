# -*- coding: utf-8 -*-
"""Position-aware daily decision board built from real data only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime
from time import monotonic
from typing import Any

import holdings as holdings_mod
import market_daily as market_daily_mod
import portfolio_review
import storage


_PRIORITY_ORDER = {"high": 0, "medium": 1, "normal": 2}
_SOURCE_DEADLINE_SECONDS = 18


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number:.2f}%"


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
) -> dict:
    return {
        "id": action_id,
        "priority": priority,
        "category": category,
        "title": title,
        "detail": detail,
        "evidence": [item for item in evidence if item],
        "target": target,
        "action_label": action_label,
        "source": source,
    }


def _portfolio_snapshot() -> dict:
    try:
        data = holdings_mod.holdings_insights(max_funds=6)
        ledger = {}
        rebalance = {}
        performance = {}
        ledger_error = None
        rebalance_error = None
        performance_error = None
        try:
            ledger = portfolio_review.ledger_overview()
        except Exception as error:
            ledger_error = str(error)[:240]
        try:
            rebalance = portfolio_review.rebalance_review()
        except Exception as error:
            rebalance_error = str(error)[:240]
        try:
            performance = portfolio_review.cashflow_performance()
        except Exception as error:
            performance_error = str(error)[:240]
        fund_dates = [row.get("as_of") for row in data.get("fund_trends") or [] if row.get("as_of")]
        return {
            "status": "available",
            "source": data.get("source"),
            "as_of": max(fund_dates) if fund_dates else None,
            "summary": data.get("summary") or {},
            "allocation": data.get("allocation") or [],
            "fund_trends": data.get("fund_trends") or [],
            "fund_errors": data.get("fund_errors") or [],
            "overlap_summary": ((data.get("overlap") or {}).get("summary") or None),
            "overlap_error": data.get("overlap_error"),
            "notes": data.get("notes") or [],
            "ledger_summary": ledger.get("summary") or {},
            "ledger_issues": ledger.get("integrity_issues") or [],
            "ledger_error": ledger_error,
            "rebalance": rebalance,
            "rebalance_error": rebalance_error,
            "performance": performance,
            "performance_error": performance_error,
        }
    except Exception as error:
        return {
            "status": "unavailable",
            "error": str(error)[:240],
            "source": "用户保存持仓 / 基金真实净值与定期报告披露",
            "summary": {},
            "allocation": [],
            "fund_trends": [],
            "fund_errors": [],
            "notes": [],
            "ledger_summary": {},
            "ledger_issues": [],
            "ledger_error": None,
            "rebalance": {},
            "rebalance_error": None,
            "performance": {},
            "performance_error": None,
        }


def _market_snapshot(risk: str) -> dict:
    try:
        data = market_daily_mod.get_market_daily(risk=risk, fund_limit=4)
        return {
            "status": "available",
            "as_of": data.get("as_of"),
            "summary": data.get("summary") or {},
            "risks": data.get("risks") or [],
            "fund_candidates": data.get("fund_candidates") or [],
            "failed": data.get("failed") or [],
            "method": data.get("method") or {},
        }
    except Exception as error:
        return {
            "status": "unavailable",
            "error": str(error)[:240],
            "summary": {},
            "risks": [],
            "fund_candidates": [],
            "failed": [],
            "method": {},
        }


def _timed_out_portfolio_snapshot() -> dict:
    return {
        "status": "unavailable",
        "error": f"真实持仓复盘在 {_SOURCE_DEADLINE_SECONDS} 秒内未返回",
        "source": "用户保存持仓 / 基金真实净值与定期报告披露",
        "summary": {},
        "allocation": [],
        "fund_trends": [],
        "fund_errors": [],
        "notes": [],
        "ledger_summary": {},
        "ledger_issues": [],
        "ledger_error": None,
        "rebalance": {},
        "rebalance_error": None,
        "performance": {},
        "performance_error": None,
    }


def _timed_out_market_snapshot() -> dict:
    return {
        "status": "unavailable",
        "error": f"真实市场日报在 {_SOURCE_DEADLINE_SECONDS} 秒内未返回",
        "summary": {},
        "risks": [],
        "fund_candidates": [],
        "failed": [],
        "method": {},
    }


def _portfolio_actions(profile: dict, portfolio: dict) -> list[dict]:
    actions: list[dict] = []
    if portfolio["status"] != "available":
        actions.append(_action(
            "portfolio-source-unavailable",
            "high",
            "数据可用性",
            "持仓复盘数据暂不可用",
            "无法完成组合风险计算，页面不会用模拟结果替代真实数据。",
            [portfolio.get("error") or "真实持仓或基金数据获取失败"],
            "portfolio",
            "查看组合",
            portfolio.get("source") or "用户保存持仓",
        ))
        return actions

    summary = portfolio.get("summary") or {}
    allocation = portfolio.get("allocation") or []
    holding_count = int(summary.get("holding_count") or 0)
    total_amount = _number(summary.get("total_amount")) or 0
    if holding_count == 0:
        actions.append(_action(
            "import-holdings",
            "high",
            "数据准备",
            "先导入并确认真实持仓",
            "没有已确认的持仓，就无法判断组合集中度、收益贡献或基金暴露。",
            ["当前已确认持仓：0 项"],
            "portfolio",
            "导入持仓",
            "用户确认持仓",
        ))
        return actions

    missing_amounts = [row for row in allocation if (_number(row.get("amount")) or 0) <= 0]
    if total_amount <= 0 or missing_amounts:
        actions.append(_action(
            "complete-holding-amounts",
            "high",
            "数据完整性",
            "补全持仓金额后再判断配置风险",
            "缺少金额的持仓不会被猜测为任何比例，因此组合集中度和收益贡献可能不完整。",
            [f"缺少有效金额：{len(missing_amounts)} 项", f"已计入金额：{total_amount:,.2f}"],
            "portfolio",
            "补全持仓",
            "用户确认持仓",
        ))

    if not profile.get("configured"):
        actions.append(_action(
            "configure-investment-rules",
            "medium",
            "投资约束",
            "设置你的风险偏好和单品上限",
            "在没有用户确认的风险边界前，系统不会把默认数值当成你的投资纪律。",
            ["当前策略约束：未保存"],
            "profile",
            "设置约束",
            "用户投资约束",
        ))
    else:
        top1_ratio = _number(summary.get("top1_ratio"))
        max_single_ratio = _number(profile.get("max_single_ratio"))
        if top1_ratio is not None and max_single_ratio is not None and top1_ratio > max_single_ratio:
            priority = "high" if top1_ratio >= max_single_ratio + 10 else "medium"
            actions.append(_action(
                "single-position-limit",
                priority,
                "组合集中度",
                "第一大持仓超过你设定的单品上限",
                "这是暴露复盘提示，不是买卖指令；先确认集中是主动选择还是无意形成。",
                [f"第一大持仓：{_pct(top1_ratio)}", f"你的单品上限：{_pct(max_single_ratio)}"],
                "portfolio",
                "复盘组合",
                "用户确认持仓 + 用户投资约束",
            ))

    ledger_summary = portfolio.get("ledger_summary") or {}
    if portfolio.get("ledger_error"):
        actions.append(_action(
            "ledger-review-unavailable",
            "normal",
            "数据可用性",
            "成本与交易流水复盘暂不可用",
            "持仓体检仍使用已确认持仓继续进行；成本、已实现收益和份额对账结论已暂停。",
            [portfolio["ledger_error"]],
            "ledger",
            "查看账本",
            "用户录入交易流水 / 本地成本算法",
        ))
    if holding_count > 0 and not portfolio.get("ledger_error") and int(ledger_summary.get("transaction_count") or 0) == 0:
        actions.append(_action(
            "record-transaction-ledger",
            "medium",
            "成本与流水",
            "补录交易流水或期初持仓",
            "当前组合只有市值和累计收益，无法拆分已实现收益、剩余成本和交易费用。",
            ["已录入交易：0 笔", "成本计算不会猜测历史买卖记录"],
            "ledger",
            "录入流水",
            "用户录入交易流水 / 用户确认持仓",
        ))
    if int(ledger_summary.get("integrity_issue_count") or 0) > 0:
        actions.append(_action(
            "fix-transaction-ledger",
            "high",
            "成本与流水",
            "交易流水存在无法匹配的卖出份额",
            "相关资产的成本和已实现收益未完整计算；请先补录对应买入或期初持仓。",
            [f"份额缺口：{ledger_summary.get('integrity_issue_count')} 项"],
            "ledger",
            "修正流水",
            "用户录入交易流水",
        ))

    performance = portfolio.get("performance") or {}
    performance_summary = performance.get("summary") or {}
    if portfolio.get("performance_error"):
        actions.append(_action(
            "cashflow-performance-unavailable",
            "normal",
            "数据可用性",
            "现金流收益复盘暂不可用",
            "组合持仓与成本复盘仍会继续，但资金加权收益率已暂停，避免输出未经验证的收益数字。",
            [portfolio["performance_error"]],
            "ledger",
            "查看收益口径",
            "用户录入交易流水 / 用户确认当前持仓金额",
        ))
    elif int(ledger_summary.get("transaction_count") or 0) > 0 and performance.get("status") != "available":
        actions.append(_action(
            "complete-cashflow-performance",
            "medium",
            "收益口径",
            "补全现金流覆盖后再查看资金加权收益率",
            "当前不会把部分流水的结果当成完整组合收益率；请补录缺失交易或确认剩余仓位金额。",
            [
                f"未覆盖持仓：{performance_summary.get('untracked_holding_count') or 0} 项",
                (performance.get("reasons") or ["现金流数据尚不完整"])[0],
            ],
            "ledger",
            "补全收益口径",
            "用户录入交易流水 / 用户确认当前持仓金额",
        ))

    rebalance = portfolio.get("rebalance") or {}
    if portfolio.get("rebalance_error"):
        actions.append(_action(
            "rebalance-review-unavailable",
            "normal",
            "数据可用性",
            "仓位纪律复盘暂不可用",
            "当前不会生成任何仓位上限结论，避免在缺少可验证计算时给出调整提示。",
            [portfolio["rebalance_error"]],
            "ledger",
            "查看仓位复盘",
            "用户确认持仓 / 用户投资约束",
        ))
    for row in (rebalance.get("allocations") or [])[:10]:
        excess_amount = _number(row.get("excess_amount")) or 0
        if excess_amount <= 0:
            continue
        actions.append(_action(
            f"rebalance-cap-{row.get('asset_type')}-{row.get('code')}",
            "high" if (_number(row.get("current_ratio")) or 0) >= (_number(row.get("max_single_ratio")) or 0) + 10 else "medium",
            "仓位纪律",
            f"{row.get('name') or row.get('code')} 高于你的单品上限",
            "系统只显示超限事实和上限空间，是否调整仍需结合持有逻辑、税费和流动性自行复盘。",
            [
                f"当前占比：{_pct(row.get('current_ratio'))}",
                f"单品上限：{_pct(row.get('max_single_ratio'))}",
                f"相对上限高出：{excess_amount:,.2f}",
            ],
            "ledger",
            "查看仓位复盘",
            "用户确认持仓 + 用户投资约束",
        ))

    top3_ratio = _number(summary.get("top3_ratio"))
    if top3_ratio is not None and top3_ratio >= 75:
        actions.append(_action(
            "top-three-concentration",
            "medium",
            "组合集中度",
            "前三大持仓占比较高",
            "前三大资产会主导组合波动，复盘时应将它们作为一个整体观察。",
            [f"前三大持仓：{_pct(top3_ratio)}", f"组合集中度：{summary.get('concentration_level') or '-'}"],
            "portfolio",
            "查看配置",
            "用户确认持仓",
        ))

    total_profit = _number(summary.get("total_profit"))
    if total_profit is not None and total_profit < 0:
        loss_rows = sorted(
            [row for row in allocation if (_number(row.get("profit")) or 0) < 0],
            key=lambda row: _number(row.get("profit")) or 0,
        )
        leading_loss = loss_rows[0] if loss_rows else None
        actions.append(_action(
            "review-loss-contribution",
            "medium",
            "收益复盘",
            "组合当前累计收益为负，先定位主要亏损来源",
            "先判断亏损来自资产配置、单只产品回撤还是录入数据变化，再决定是否调整。",
            [
                f"累计收益：{total_profit:,.2f}",
                f"主要亏损项：{(leading_loss or {}).get('name') or (leading_loss or {}).get('code') or '-'} {((leading_loss or {}).get('profit') or 0):,.2f}",
            ],
            "portfolio",
            "查看收益贡献",
            "用户确认持仓",
        ))

    for row in portfolio.get("fund_trends") or []:
        current_drawdown = _number(row.get("current_drawdown"))
        holding_ratio = _number(row.get("holding_ratio")) or 0
        if current_drawdown is not None and current_drawdown <= -10 and holding_ratio >= 5:
            name = row.get("name") or row.get("code") or "基金"
            actions.append(_action(
                f"fund-drawdown-{row.get('code')}",
                "medium",
                "基金复盘",
                f"复盘 {name} 的当前回撤",
                "回撤本身不是交易信号；需要结合持有期限、基金风格、同类表现和你的投入计划复盘。",
                [
                    f"持仓占比：{_pct(holding_ratio)}",
                    f"当前回撤：{_pct(current_drawdown)}",
                    f"近 3 月：{_pct(row.get('return_3m'))}",
                ],
                "funds",
                "研究基金",
                row.get("source") or "基金真实净值",
            ))

    overlap_summary = portfolio.get("overlap_summary") or {}
    high_overlap_count = int(overlap_summary.get("high_overlap_pair_count") or 0)
    if high_overlap_count:
        actions.append(_action(
            "fund-overlap",
            "medium",
            "基金暴露",
            "基金持仓存在中高重合组合",
            "多只基金不等于分散；应核对它们是否承担了相同的行业或重仓股暴露。",
            [
                f"中高重合组合：{high_overlap_count} 组",
                f"平均个股重合：{_pct(overlap_summary.get('avg_stock_overlap_weight'))}",
            ],
            "portfolio",
            "查看重合度",
            "基金定期报告披露持仓",
        ))

    fund_errors = portfolio.get("fund_errors") or []
    if fund_errors:
        actions.append(_action(
            "fund-data-unavailable",
            "normal",
            "数据可用性",
            "部分基金真实数据暂不可用",
            "这些基金不会被用估算净值或模拟持仓补齐，恢复数据后再完成复盘。",
            [f"暂不可用基金：{len(fund_errors)} 只"],
            "funds",
            "检查基金",
            "基金真实数据源",
        ))
    return actions


def _market_actions(market: dict) -> list[dict]:
    actions: list[dict] = []
    if market["status"] != "available":
        return [_action(
            "market-source-unavailable",
            "normal",
            "数据可用性",
            "今日市场日报暂不可用",
            "市场层结论已停用，系统不会把历史或模拟内容当作今天的数据。",
            [market.get("error") or "真实市场数据获取失败"],
            "market",
            "查看市场",
            "真实市场数据源",
        )]

    summary = market.get("summary") or {}
    risks = market.get("risks") or []
    if risks:
        lead = risks[0]
        actions.append(_action(
            "market-risk-review",
            "medium",
            "市场环境",
            lead.get("title") or "市场日报提示需要复盘的风险",
            lead.get("text") or "市场日报已标记风险，先核对其与现有持仓的关联。",
            [f"市场风险提示：{len(risks)} 条", f"数据日期：{market.get('as_of') or '-'}"],
            "market",
            "查看市场证据",
            "真实市场日报",
        ))

    top_industry = summary.get("top_industry") or {}
    if top_industry.get("name"):
        actions.append(_action(
            "market-research-queue",
            "normal",
            "市场线索",
            f"将 {top_industry['name']} 加入研究队列",
            "它来自真实市场日报的热度线索，只代表值得研究，不构成买入建议。",
            [f"区间表现：{_pct(top_industry.get('change_pct'))}", f"数据日期：{market.get('as_of') or '-'}"],
            "market",
            "研究板块",
            "真实市场日报",
        ))

    failed = market.get("failed") or []
    if failed:
        actions.append(_action(
            "daily-partial-source-failure",
            "normal",
            "数据可用性",
            "市场日报包含不可用数据源",
            "当前日报仅基于成功返回的真实来源汇总，缺失来源已单独标注。",
            [f"不可用来源：{len(failed)} 个"],
            "market",
            "查看来源状态",
            "真实市场日报",
        ))
    return actions


def build_decision_center() -> dict:
    """Build a daily review queue without inventing prices, holdings, or signals."""
    profile = storage.get_investment_profile()
    pool = ThreadPoolExecutor(max_workers=2)
    portfolio_future = pool.submit(_portfolio_snapshot)
    market_future = pool.submit(_market_snapshot, profile["risk"])
    deadline = monotonic() + _SOURCE_DEADLINE_SECONDS
    try:
        portfolio = portfolio_future.result(timeout=max(0, deadline - monotonic()))
    except TimeoutError:
        portfolio_future.cancel()
        portfolio = _timed_out_portfolio_snapshot()
    try:
        market = market_future.result(timeout=max(0, deadline - monotonic()))
    except TimeoutError:
        market_future.cancel()
        market = _timed_out_market_snapshot()
    finally:
        # Provider calls may still be unwinding after their request timeout. Do not block
        # the HTTP response or replace unavailable data with a synthetic result.
        pool.shutdown(wait=False, cancel_futures=True)

    actions = _portfolio_actions(profile, portfolio) + _market_actions(market)
    if not actions and portfolio.get("status") == "available" and market.get("status") == "available":
        actions.append(_action(
            "no-high-priority-item",
            "normal",
            "日常复盘",
            "当前没有触发高优先级复盘事项",
            "这不代表未来没有风险，只表示已获取的真实数据没有触发本中心的既定复盘规则。",
            [
                f"已确认持仓：{portfolio.get('summary', {}).get('holding_count') or 0} 项",
                f"市场数据日期：{market.get('as_of') or '-'}",
            ],
            "portfolio",
            "查看组合",
            "用户确认持仓 + 真实市场数据",
        ))

    actions.sort(key=lambda item: (_PRIORITY_ORDER[item["priority"]], item["category"], item["title"]))
    unavailable = []
    if portfolio.get("status") != "available":
        unavailable.append({"scope": "持仓复盘", "error": portfolio.get("error")})
    if market.get("status") != "available":
        unavailable.append({"scope": "市场日报", "error": market.get("error")})
    for error in portfolio.get("fund_errors") or []:
        unavailable.append({"scope": error.get("code") or "基金", "error": error.get("error")})
    if portfolio.get("ledger_error"):
        unavailable.append({"scope": "成本与流水", "error": portfolio["ledger_error"]})
    if portfolio.get("rebalance_error"):
        unavailable.append({"scope": "仓位纪律", "error": portfolio["rebalance_error"]})
    if portfolio.get("performance_error"):
        unavailable.append({"scope": "现金流收益", "error": portfolio["performance_error"]})
    for failure in market.get("failed") or []:
        unavailable.append({"scope": failure.get("source") or "市场数据源", "error": failure.get("error") or failure.get("message")})

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy": "行动清单只使用用户确认持仓和已标注的真实来源；它用于风险复盘与研究排序，不提供买卖指令或收益承诺。",
        "profile": profile,
        "portfolio": portfolio,
        "market": market,
        "actions": actions[:12],
        "summary": {
            "high_count": sum(item["priority"] == "high" for item in actions),
            "medium_count": sum(item["priority"] == "medium" for item in actions),
            "normal_count": sum(item["priority"] == "normal" for item in actions),
            "unavailable_count": len(unavailable),
        },
        "unavailable": unavailable,
    }
