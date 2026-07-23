# -*- coding: utf-8 -*-
"""Whole-portfolio next-best-action and capital allocation engine.

The engine combines immutable account facts, an active Investment Policy
Statement, the current holdings action report, portfolio exposure intervals,
and forward opportunity scorecards. It can authorize only a bounded manual
research pilot. It never creates orders, share quantities, or a return promise.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable

import opportunity_committee_service
import opportunity_profit_service
import portfolio_action_report
import portfolio_decision_twin
import portfolio_exposure
import portfolio_valuation
import storage
from opportunity_profit_repository import (
    OpportunityProfitRepository,
    repository as opportunity_profit_repository,
)
from portfolio_capital_repository import (
    PortfolioCapitalRepository,
    repository as capital_repository,
    sha256_payload,
)


SCHEMA_VERSION = "portfolio_capital_decision.v1"
ENGINE_VERSION = "whole_portfolio_next_best_action.v2"
HARD_GLOBAL_PILOT_CAP_PCT = 5.0
MAX_STRATEGIES = 3
MAX_CANDIDATES = 12

MARKET_PERMISSION = {
    "A股": "mainland",
    "港股": "hong_kong",
    "美股": "united_states",
}
ACTION_LABELS = {
    "data_required": "补齐数据后再决策",
    "reduce_review": "暂停新增，复核降仓",
    "pause_add": "暂停加仓",
    "risk_review": "风险复核",
    "thesis_review": "持有逻辑复核",
    "hold_review": "保持并按计划复核",
}
HARD_EXISTING_ACTIONS = {
    "data_required",
    "reduce_review",
    "risk_review",
    "thesis_review",
}


def _now(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return _now(value).isoformat(timespec="seconds")


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _gate(
    code: str,
    label: str,
    status: str,
    detail: str,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "detail": detail,
        "source": source,
    }


def _holding_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("market") or ""),
        str(item.get("code") or item.get("symbol") or ""),
    )


def _compact_holdings(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "id": item.get("id"),
            "asset_type": item.get("asset_type"),
            "market": item.get("market") or "",
            "code": item.get("code") or "",
            "name": item.get("name") or item.get("code") or "",
            "amount_cny": _round(item.get("amount")),
            "shares": _round(item.get("shares"), 8),
            "valuation_snapshot_id": item.get("valuation_snapshot_id"),
            "valuation_method": item.get("valuation_method"),
            "valuation_price_as_of": item.get("valuation_price_as_of"),
            "source": item.get("source") or "",
            "updated_at": item.get("updated_at"),
        }
        for item in holdings
    ]
    rows.sort(
        key=lambda item: (
            str(item["asset_type"]),
            str(item["market"]),
            str(item["code"]),
            str(item["id"]),
        )
    )
    return rows


def _load_current_exposure(
    *,
    user_id: str,
    holdings: list[dict[str, Any]],
    profile: dict[str, Any],
    valuation_snapshot_id: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    expected_holdings = portfolio_exposure.holdings_sha256(holdings)
    reasons: list[str] = []
    snapshots = storage.list_portfolio_exposure_snapshots(
        user_id=user_id,
        limit=20,
    )
    for summary in snapshots:
        if summary.get("target_code"):
            continue
        item = storage.get_portfolio_exposure_snapshot(
            str(summary["id"]),
            user_id=user_id,
            include_payload=True,
        )
        payload = (item or {}).get("payload")
        if not isinstance(payload, dict):
            continue
        integrity = storage.verify_portfolio_exposure_snapshot(
            str(summary["id"]), user_id=user_id
        )
        checks = {
            "integrity": bool(integrity.get("verified")),
            "holdings": payload.get("holdings_sha256")
            == expected_holdings,
            "profile": payload.get("profile_version_id")
            == profile.get("profile_version_id"),
            "quality": bool(
                (payload.get("quality") or {}).get("decision_eligible")
            ),
            "valuation": (
                not holdings
                or (
                    (payload.get("valuation_binding") or {}).get(
                        "snapshot_id"
                    )
                    == valuation_snapshot_id
                    and bool(
                        (payload.get("valuation_binding") or {}).get(
                            "risk_analysis_eligible"
                        )
                    )
                )
            ),
        }
        if all(checks.values()):
            return {
                **payload,
                "snapshot": {
                    key: item.get(key)
                    for key in (
                        "id",
                        "schema_version",
                        "holdings_sha256",
                        "profile_version_id",
                        "status",
                        "payload_sha256",
                        "created_at",
                    )
                },
                "integrity": integrity,
            }, []
        reasons.extend(
            code
            for code, passed in checks.items()
            if not passed
        )
    if not snapshots:
        reasons.append("exposure_snapshot_missing")
    return None, _unique(reasons)


def _primary_horizon(scorecard: dict[str, Any]) -> dict[str, Any]:
    primary = int(
        ((scorecard.get("policy") or {}).get("values") or {}).get(
            "primary_horizon"
        )
        or 20
    )
    return next(
        (
            item
            for item in scorecard.get("horizons") or []
            if int(item.get("horizon_trading_days") or 0) == primary
        ),
        {},
    )


def _strategy_evidence(
    profit_lab: dict[str, Any],
    *,
    profit_repo: OpportunityProfitRepository,
    user_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = opportunity_committee_service.strategy_evidence_rows(
        profit_lab,
        profit_repo=profit_repo,
        user_id=user_id,
    )
    eligible = [
        item
        for item in evidence
        if (
            item.get("capital_eligible")
            and item.get("capital_plan_status") == "available"
            and item.get("scorecard_current")
            and item.get("basket_id")
        )
    ]
    return evidence, eligible


def _existing_actions(
    holdings: list[dict[str, Any]],
    report: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_rows = (report or {}).get("holdings") or []
    by_key = {_holding_key(item): item for item in source_rows}
    actions = []
    critical = []
    for holding in holdings:
        row = by_key.get(_holding_key(holding)) or {}
        decision = row.get("decision") or {}
        action = str(decision.get("action") or "data_required")
        item = {
            "holding_id": holding.get("id"),
            "market": holding.get("market") or "",
            "code": holding.get("code") or "",
            "name": holding.get("name") or holding.get("code") or "",
            "current_amount_cny": _round(holding.get("amount")),
            "current_ratio_pct": row.get("allocation_ratio"),
            "action": action,
            "label": decision.get("label")
            or ACTION_LABELS.get(action, action),
            "rationale": decision.get("rationale")
            or "当前行动报告没有可用结论。",
            "review_amount_cny": decision.get("review_amount"),
            "blockers": decision.get("blockers") or [],
            "execution_authorized": False,
        }
        actions.append(item)
        if action in HARD_EXISTING_ACTIONS:
            critical.append(item)
    order = {
        "data_required": 0,
        "reduce_review": 1,
        "risk_review": 2,
        "thesis_review": 3,
        "pause_add": 4,
        "hold_review": 5,
    }
    actions.sort(
        key=lambda item: (
            order.get(item["action"], 99),
            -(_number(item.get("current_amount_cny")) or 0),
        )
    )
    return actions, critical


def _candidate_desires(
    strategies: list[dict[str, Any]],
    *,
    global_budget: float,
    allowed_markets: set[str],
    current_actions: dict[tuple[str, str], str],
    committee: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not strategies or global_budget <= 0:
        return []
    committee_candidates = {
        (str(item.get("market") or ""), str(item.get("symbol") or "")): item
        for item in (committee or {}).get("candidate_consensus") or []
    }
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for strategy in strategies:
        plan = strategy.get("live_capital_plan") or {}
        committee_weight = _number(
            strategy.get("committee_weight_pct")
        )
        strategy_share = (
            global_budget * committee_weight / 100
            if committee_weight is not None
            else global_budget / len(strategies)
        )
        strategy_budget = min(
            strategy_share,
            _number(plan.get("planned_budget_cny"), 0.0) or 0.0,
            _number(plan.get("pilot_cap_cny"), 0.0) or 0.0,
        )
        positions = [
            item
            for item in plan.get("positions") or []
            if (_number(item.get("source_weight_pct")) or 0) > 0
        ]
        denominator = sum(
            _number(item.get("source_weight_pct")) or 0
            for item in positions
            if str(item.get("market") or "") in allowed_markets
        )
        if strategy_budget <= 0 or denominator <= 0:
            continue
        for position in positions:
            market = str(position.get("market") or "")
            symbol = str(position.get("symbol") or "")
            key = (market, symbol)
            weight = _number(position.get("source_weight_pct")) or 0.0
            desired = (
                strategy_budget * weight / denominator
                if market in allowed_markets
                else 0.0
            )
            row = candidates.setdefault(
                key,
                {
                    "market": market,
                    "symbol": symbol,
                    "name": position.get("name") or symbol,
                    "desired_amount_cny": 0.0,
                    "sources": [],
                    "blockers": [],
                },
            )
            row["desired_amount_cny"] += desired
            row["sources"].append(
                {
                    "strategy_id": strategy.get("strategy_id"),
                    "strategy_name": strategy.get("strategy_name"),
                    "scorecard_id": strategy.get("scorecard_id"),
                    "basket_id": strategy.get("basket_id"),
                    "primary_horizon_trading_days": strategy.get(
                        "primary_horizon_trading_days"
                    ),
                    "mature_cohort_count": strategy.get(
                        "mature_cohort_count"
                    ),
                    "mean_net_excess_return_pct": strategy.get(
                        "mean_net_excess_return_pct"
                    ),
                    "positive_excess_rate_pct": strategy.get(
                        "positive_excess_rate_pct"
                    ),
                    "familywise_ci95": strategy.get("familywise_ci95"),
                    "worst_cohort_drawdown_pct": strategy.get(
                        "worst_cohort_drawdown_pct"
                    ),
                    "committee_weight_pct": strategy.get(
                        "committee_weight_pct"
                    ),
                    "unique_contribution_pct": strategy.get(
                        "unique_contribution_pct"
                    ),
                    "recent_decay": strategy.get("recent_decay"),
                    "source_weight_pct": position.get(
                        "source_weight_pct"
                    ),
                }
            )
            if market not in allowed_markets:
                row["blockers"].append("market_not_allowed_by_policy")
            current_action = current_actions.get(key)
            if current_action and current_action != "hold_review":
                row["blockers"].append(
                    f"existing_holding_action:{current_action}"
                )
    result = []
    for item in candidates.values():
        key = (item["market"], item["symbol"])
        committee_item = committee_candidates.get(key) or {}
        committee_target = _number(
            committee_item.get("model_target_weight_pct")
        )
        if committee_target is not None:
            item["desired_amount_cny"] = min(
                float(item["desired_amount_cny"]),
                global_budget * committee_target / 100,
            )
        item.update(
            {
                "committee_rank": committee_item.get(
                    "committee_rank"
                ),
                "committee_relative_view": committee_item.get(
                    "relative_view"
                ),
                "committee_view_label": committee_item.get(
                    "view_label"
                ),
                "committee_agreement_pct": committee_item.get(
                    "agreement_pct"
                ),
                "committee_support_count": committee_item.get(
                    "support_count"
                ),
                "committee_model_target_weight_pct": (
                    committee_target
                ),
                "calibrated_probability": False,
            }
        )
        result.append(item)
    result.sort(
        key=lambda item: (
            -float(item["desired_amount_cny"]),
            item["market"],
            item["symbol"],
        )
    )
    return result[:MAX_CANDIDATES]


def _append_planned_cash(
    holdings: list[dict[str, Any]], amount: float
) -> list[dict[str, Any]]:
    result = [dict(item) for item in holdings]
    if amount > 0:
        result.append(
            {
                "id": "planned_monthly_cash",
                "asset_type": "cash",
                "market": "",
                "code": "PLANNED_CASH",
                "name": "本月计划新增资金",
                "amount": round(amount, 2),
                "source": "active_investment_policy",
            }
        )
    return result


def _proposed_holdings(
    holdings: list[dict[str, Any]],
    allocations: dict[tuple[str, str], float],
    *,
    monthly_budget: float,
    names: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    result = [dict(item) for item in holdings]
    by_key = {_holding_key(item): item for item in result}
    allocated = 0.0
    for index, (key, amount) in enumerate(
        sorted(allocations.items()), start=1
    ):
        if amount <= 0:
            continue
        allocated += amount
        existing = by_key.get(key)
        if existing and str(existing.get("asset_type")) == "stock":
            existing["amount"] = round(
                (_number(existing.get("amount")) or 0) + amount,
                2,
            )
            continue
        market, symbol = key
        result.append(
            {
                "id": f"candidate_{index}_{market}_{symbol}",
                "asset_type": "stock",
                "market": market,
                "code": symbol,
                "name": names.get(key) or symbol,
                "amount": round(amount, 2),
                "source": "frozen_forward_opportunity_basket",
            }
        )
    reserve = max(0.0, monthly_budget - allocated)
    return _append_planned_cash(result, reserve)


def _stress_matrix(
    *,
    holdings: list[dict[str, Any]],
    exposure: dict[str, Any],
    profile: dict[str, Any],
    monthly_budget: float,
    allocations: dict[tuple[str, str], float],
    names: dict[tuple[str, str], str],
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    baseline_holdings = _append_planned_cash(holdings, monthly_budget)
    proposed_holdings = _proposed_holdings(
        holdings,
        allocations,
        monthly_budget=monthly_budget,
        names=names,
    )
    rows: list[dict[str, Any]] = []
    baseline_blocks: set[str] = set()
    proposed_blocks: set[str] = set()
    for preset in portfolio_decision_twin.scenario_presets():
        baseline = portfolio_decision_twin.evaluate_static_portfolio(
            holdings=baseline_holdings,
            exposure=exposure,
            profile=profile,
            scenario=preset,
        )
        proposed = portfolio_decision_twin.evaluate_static_portfolio(
            holdings=proposed_holdings,
            exposure=exposure,
            profile=profile,
            scenario=preset,
        )
        baseline_portfolio = baseline["portfolio"]
        proposed_portfolio = proposed["portfolio"]
        baseline_gate_blocks = [
            str(item.get("code"))
            for item in baseline_portfolio.get("policy_gates") or []
            if item.get("status") == "block"
        ]
        proposed_gate_blocks = [
            str(item.get("code"))
            for item in proposed_portfolio.get("policy_gates") or []
            if item.get("status") == "block"
        ]
        baseline_blocks.update(baseline_gate_blocks)
        proposed_blocks.update(proposed_gate_blocks)
        current_loss = max(
            0.0,
            -float(
                (baseline_portfolio.get("pnl_interval") or {}).get(
                    "lower_amount"
                )
                or 0
            ),
        )
        proposed_loss = max(
            0.0,
            -float(
                (proposed_portfolio.get("pnl_interval") or {}).get(
                    "lower_amount"
                )
                or 0
            ),
        )
        rows.append(
            {
                "scenario_id": preset.get("id"),
                "scenario_name": preset.get("name"),
                "assumption_type": preset.get("assumption_type"),
                "historical_calibration": False,
                "current_worst_loss_cny": round(current_loss, 2),
                "current_worst_loss_pct": _round(
                    (baseline_portfolio.get("pnl_interval") or {}).get(
                        "lower_pct"
                    )
                ),
                "proposed_worst_loss_cny": round(proposed_loss, 2),
                "proposed_worst_loss_pct": _round(
                    (proposed_portfolio.get("pnl_interval") or {}).get(
                        "lower_pct"
                    )
                ),
                "incremental_worst_loss_cny": round(
                    proposed_loss - current_loss, 2
                ),
                "risk_budget_utilization_pct": _round(
                    (
                        proposed_portfolio.get("risk_budget") or {}
                    ).get("utilization_pct")
                ),
                "current_gate_blocks": baseline_gate_blocks,
                "proposed_gate_blocks": proposed_gate_blocks,
                "policy_passed": not proposed_gate_blocks,
            }
        )
    return rows, baseline_blocks, proposed_blocks


def _scale_allocations(
    allocations: dict[tuple[str, str], float],
    factor: float,
) -> dict[tuple[str, str], float]:
    return {
        key: round(max(0.0, amount * factor), 2)
        for key, amount in allocations.items()
    }


def _action_report_evidence(
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    if not report:
        return {"available": False}
    return {
        "available": True,
        "report": {
            key: (report.get("report") or {}).get(key)
            for key in (
                "id",
                "schema_version",
                "ruleset_version",
                "holdings_sha256",
                "theses_sha256",
                "profile_version_id",
                "status",
                "payload_sha256",
                "created_at",
            )
        },
        "binding": report.get("binding"),
        "integrity": report.get("integrity"),
        "status": report.get("status"),
        "readiness": report.get("readiness"),
        "summary": report.get("summary"),
        "holdings": [
            {
                "id": item.get("id"),
                "asset_type": item.get("asset_type"),
                "market": item.get("market"),
                "code": item.get("code"),
                "amount": item.get("amount"),
                "allocation_ratio": item.get("allocation_ratio"),
                "decision": item.get("decision"),
                "thesis_review": item.get("thesis_review"),
            }
            for item in report.get("holdings") or []
        ],
    }


def _assemble(
    *,
    user_id: str,
    tenant_id: str = "public",
    now: dt.datetime | None = None,
    holdings_valuation_loader: Callable[
        [], tuple[list[dict[str, Any]], dict[str, Any]]
    ]
    | None = None,
    profile_loader: Callable[[], dict[str, Any]] | None = None,
    action_report_loader: Callable[[], dict[str, Any] | None]
    | None = None,
    exposure_loader: Callable[
        [list[dict[str, Any]], dict[str, Any], str | None],
        tuple[dict[str, Any] | None, list[str]],
    ]
    | None = None,
    profit_lab_loader: Callable[[], dict[str, Any]] | None = None,
    profit_repo: OpportunityProfitRepository = opportunity_profit_repository,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = _now(now)
    if holdings_valuation_loader is None:
        holdings_valuation_loader = (
            lambda: portfolio_valuation.current_valued_holdings(
                user_id=user_id,
                tenant_id=tenant_id,
            )
        )
    profile_loader = profile_loader or (
        lambda: storage.get_investment_profile(user_id=user_id)
    )
    action_report_loader = action_report_loader or (
        lambda: portfolio_action_report.load_latest_action_report(
            user_id=user_id
        )
    )
    profit_lab_loader = profit_lab_loader or (
        lambda: opportunity_profit_service.profit_lab_overview(
            user_id=user_id
        )
    )

    holdings, valuation = holdings_valuation_loader()
    profile = profile_loader()
    report = action_report_loader()
    valuation_snapshot = valuation.get("snapshot") or {}
    valuation_payload = valuation_snapshot.get("payload") or {}
    valuation_id = valuation_snapshot.get("id")
    if exposure_loader is None:
        exposure, exposure_reasons = _load_current_exposure(
            user_id=user_id,
            holdings=holdings,
            profile=profile,
            valuation_snapshot_id=valuation_id,
        )
    else:
        exposure, exposure_reasons = exposure_loader(
            holdings, profile, valuation_id
        )
    profit_lab = profit_lab_loader()
    strategy_rows, gate_eligible_strategies = _strategy_evidence(
        profit_lab,
        profit_repo=profit_repo,
        user_id=user_id,
    )
    committee, committee_evidence = (
        opportunity_committee_service.compose_committee(
            strategy_rows,
            now=current,
        )
    )
    committee_by_strategy = {
        str(item.get("strategy_id") or ""): item
        for item in committee.get("strategies") or []
    }
    eligible_strategies = []
    for strategy in gate_eligible_strategies:
        committee_row = committee_by_strategy.get(
            str(strategy.get("strategy_id") or "")
        ) or {}
        committee_weight = _number(
            committee_row.get("committee_weight_pct"), 0.0
        ) or 0.0
        if committee_weight <= 0:
            continue
        eligible_strategies.append(
            {
                **strategy,
                "committee_weight_pct": round(
                    committee_weight, 4
                ),
                "committee_state": committee_row.get(
                    "committee_state"
                ),
                "unique_contribution_pct": committee_row.get(
                    "unique_contribution_pct"
                ),
                "recent_decay": committee_row.get("recent_decay"),
                "committee_reasons": committee_row.get(
                    "committee_reasons"
                )
                or [],
            }
        )
    existing_actions, critical_actions = _existing_actions(
        holdings, report
    )

    total_value = (
        _number((valuation_payload.get("summary") or {}).get("total_value"))
        or sum(_number(item.get("amount")) or 0 for item in holdings)
    )
    monthly_budget = (
        _number(profile.get("monthly_budget"), 0.0) or 0.0
    )
    allowed_markets = {
        market
        for market, permission in MARKET_PERMISSION.items()
        if permission in set(profile.get("allowed_fund_markets") or [])
    }

    gates: list[dict[str, Any]] = []
    hard_blockers: list[str] = []
    profile_ready = bool(
        profile.get("configured")
        and (profile.get("governance_integrity") or {}).get("verified")
    )
    gates.append(
        _gate(
            "active_investment_policy",
            "投资政策",
            "pass" if profile_ready else "block",
            (
                f"已绑定政策 {profile.get('profile_version_id')}"
                if profile_ready
                else "需要激活未过期且完整性通过的投资政策"
            ),
            source="不可变投资政策版本库",
        )
    )
    if not profile_ready:
        hard_blockers.append("investment_policy_not_active")

    holdings_ready = bool(holdings and total_value > 0)
    gates.append(
        _gate(
            "confirmed_holdings",
            "真实持仓",
            "pass" if holdings_ready else "block",
            (
                f"{len(holdings)} 项持仓，可信总额 ¥{total_value:,.2f}"
                if holdings_ready
                else "需要先导入并确认真实持仓金额"
            ),
            source="用户持仓数据库",
        )
    )
    if not holdings_ready:
        hard_blockers.append("confirmed_holdings_missing")

    valuation_gate = valuation.get("runtime_gate") or {}
    valuation_ready = bool(
        valuation.get("status") == "available"
        and (valuation.get("binding") or {}).get("current")
        and valuation_gate.get("trade_amount_eligible")
    )
    gates.append(
        _gate(
            "current_trade_valuation",
            "可信估值",
            "pass" if valuation_ready else "block",
            (
                f"估值快照 {valuation_id} 已通过金额门禁"
                if valuation_ready
                else "；".join(
                    str(item)
                    for item in valuation_gate.get("reasons")
                    or ["需要刷新当前持仓的可信估值"]
                )
            ),
            source="不可变人民币估值快照",
        )
    )
    if not valuation_ready:
        hard_blockers.append("portfolio_valuation_not_trade_eligible")

    report_ready = bool(
        report
        and (report.get("binding") or {}).get("current")
        and (report.get("integrity") or {}).get("verified")
        and report.get("status") != "blocked"
    )
    gates.append(
        _gate(
            "current_action_report",
            "持仓行动报告",
            "pass" if report_ready else "block",
            (
                f"行动报告 {(report.get('report') or {}).get('id')} 当前有效"
                if report_ready
                else "需要生成与当前持仓、估值和投资政策绑定的行动报告"
            ),
            source="持仓行动中心",
        )
    )
    if not report_ready:
        hard_blockers.append("portfolio_action_report_not_current")
    if critical_actions:
        codes = "、".join(
            item.get("code") or "-"
            for item in critical_actions[:4]
        )
        gates.append(
            _gate(
                "existing_position_priority",
                "已有仓位优先级",
                "block",
                f"{len(critical_actions)} 项已有仓位需先处理：{codes}",
                source="当前持仓行动报告",
            )
        )
        hard_blockers.append("existing_position_review_required")
    else:
        gates.append(
            _gate(
                "existing_position_priority",
                "已有仓位优先级",
                "pass",
                "没有数据缺口、超限、风险或持有逻辑复核抢占新增资金",
                source="当前持仓行动报告",
            )
        )

    exposure_ready = exposure is not None
    gates.append(
        _gate(
            "current_exposure_snapshot",
            "组合风险底图",
            "pass" if exposure_ready else "block",
            (
                f"穿透快照 {(exposure or {}).get('snapshot', {}).get('id')} 已绑定"
                if exposure_ready
                else "需要刷新与当前持仓、估值和投资政策绑定的穿透风险快照"
            ),
            source="不可变组合穿透快照",
        )
    )
    if not exposure_ready:
        hard_blockers.append("portfolio_exposure_not_current")

    if monthly_budget > 0:
        gates.append(
            _gate(
                "monthly_new_capital",
                "月度新增资金",
                "pass",
                f"用户确认月度预算 ¥{monthly_budget:,.2f}",
                source="已激活投资政策",
            )
        )
    else:
        gates.append(
            _gate(
                "monthly_new_capital",
                "月度新增资金",
                "watch",
                "月度预算为 0，本次只输出已有仓位动作",
                source="已激活投资政策",
            )
        )

    live_eligible_count = sum(
        1 for item in strategy_rows if item.get("capital_eligible")
    )
    if eligible_strategies:
        gates.append(
            _gate(
                "forward_profit_evidence",
                "前瞻收益证据",
                "pass",
                (
                    f"{len(eligible_strategies)} 个策略同时通过前瞻门禁、"
                    "多重检验校正与不可变记分卡绑定"
                ),
                source="策略收益实验室",
            )
        )
    else:
        detail = (
            f"{live_eligible_count} 个策略已通过实时门禁，但需要先冻结当前记分卡"
            if live_eligible_count
            else "尚无策略通过独立前瞻批次、成本后超额、回撤与置信区间门禁"
        )
        gates.append(
            _gate(
                "forward_profit_evidence",
                "前瞻收益证据",
                "watch",
                detail,
                source="策略收益实验室",
            )
        )
    committee_status = str(committee.get("status") or "collecting")
    committee_summary = committee.get("summary") or {}
    if eligible_strategies:
        gates.append(
            _gate(
                "adaptive_strategy_committee",
                "策略投资委员会",
                (
                    "pass"
                    if committee_status == "active"
                    else "watch"
                ),
                (
                    f"{len(eligible_strategies)} 个策略袖套入选，"
                    f"模型投入 {float(committee_summary.get('candidate_model_invested_pct') or 0):.1f}%，"
                    f"保留现金 {float(committee_summary.get('cash_reserve_pct') or 0):.1f}%；"
                    f"状态 {committee_status}"
                ),
                source=opportunity_committee_service.ENGINE_VERSION,
            )
        )
    else:
        gates.append(
            _gate(
                "adaptive_strategy_committee",
                "策略投资委员会",
                "watch",
                "没有策略通过委员会的前瞻、完整性、失效和独立贡献检查",
                source=opportunity_committee_service.ENGINE_VERSION,
            )
        )

    global_pilot_pct = min(
        HARD_GLOBAL_PILOT_CAP_PCT,
        max(
            [
                _number(item.get("maximum_manual_pilot_pct")) or 0
                for item in eligible_strategies
            ]
            + [0.0]
        ),
    )
    global_pilot_cap = min(
        monthly_budget,
        total_value * global_pilot_pct / 100,
    )
    current_action_map = {
        (item["market"], item["code"]): item["action"]
        for item in existing_actions
    }
    candidate_rows = _candidate_desires(
        eligible_strategies,
        global_budget=global_pilot_cap,
        allowed_markets=allowed_markets,
        current_actions=current_action_map,
        committee=committee,
    )

    baseline_allocation: dict[str, Any] = {}
    baseline_matrix: list[dict[str, Any]] = []
    baseline_blocks: set[str] = set()
    if holdings_ready and exposure_ready and profile_ready:
        try:
            baseline_matrix, baseline_blocks, _ = _stress_matrix(
                holdings=holdings,
                exposure=exposure or {},
                profile=profile,
                monthly_budget=monthly_budget,
                allocations={},
                names={},
            )
            first_preset = portfolio_decision_twin.scenario_presets()[0]
            baseline_static = (
                portfolio_decision_twin.evaluate_static_portfolio(
                    holdings=_append_planned_cash(
                        holdings, monthly_budget
                    ),
                    exposure=exposure or {},
                    profile=profile,
                    scenario=first_preset,
                )
            )
            baseline_allocation = (
                baseline_static.get("portfolio") or {}
            ).get("allocation") or {}
        except (TypeError, ValueError) as error:
            hard_blockers.append("portfolio_stress_model_unavailable")
            gates.append(
                _gate(
                    "portfolio_stress_model",
                    "组合压力测试",
                    "block",
                    f"压力模型无法使用当前证据：{str(error)[:180]}",
                    source=portfolio_decision_twin.METHOD_VERSION,
                )
            )

    post_total = total_value + monthly_budget
    equity_upper_amount = _number(
        baseline_allocation.get("equity_upper_amount")
    )
    if equity_upper_amount is None:
        equity_upper_amount = (
            post_total
            * (
                _number(
                    baseline_allocation.get("equity_upper_ratio")
                )
                or 0
            )
            / 100
        )
    industry_upper_amount = _number(
        baseline_allocation.get("industry_max_upper_amount")
    )
    if industry_upper_amount is None:
        industry_upper_amount = (
            post_total
            * (
                _number(
                    baseline_allocation.get(
                        "industry_max_upper_ratio"
                    )
                )
                or 0
            )
            / 100
        )
    equity_room = max(
        0.0,
        post_total
        * (_number(profile.get("max_equity_ratio")) or 0)
        / 100
        - equity_upper_amount,
    )
    industry_room = max(
        0.0,
        post_total
        * (_number(profile.get("max_industry_ratio")) or 0)
        / 100
        - industry_upper_amount,
    )
    conservative_risk_room = min(
        global_pilot_cap,
        equity_room,
        industry_room,
    )

    valuation_positions = valuation_payload.get("positions") or []
    existing_amounts: dict[tuple[str, str], float] = {}
    for item in valuation_positions:
        key = _holding_key(item)
        existing_amounts[key] = existing_amounts.get(key, 0.0) + (
            _number(item.get("base_value")) or 0
        )
    single_limit = (
        post_total
        * (_number(profile.get("max_single_ratio")) or 0)
        / 100
    )
    desired_total = sum(
        float(item["desired_amount_cny"]) for item in candidate_rows
    )
    desired_scale = (
        min(1.0, conservative_risk_room / desired_total)
        if desired_total > 0
        else 0.0
    )
    allocations: dict[tuple[str, str], float] = {}
    for candidate in candidate_rows:
        key = (candidate["market"], candidate["symbol"])
        existing_amount = existing_amounts.get(key, 0.0)
        single_room = max(0.0, single_limit - existing_amount)
        planned = min(
            float(candidate["desired_amount_cny"]) * desired_scale,
            single_room,
        )
        if candidate.get("blockers"):
            planned = 0.0
        planned = round(max(0.0, planned), 2)
        allocations[key] = planned
        candidate["existing_amount_cny"] = round(existing_amount, 2)
        candidate["single_position_room_cny"] = round(single_room, 2)
        candidate["planned_amount_cny"] = planned

    names = {
        (item["market"], item["symbol"]): item["name"]
        for item in candidate_rows
    }
    stress_rows = baseline_matrix
    proposed_blocks: set[str] = set()
    risk_scale = 1.0
    hard_blockers = _unique(hard_blockers)
    if hard_blockers:
        allocations = _scale_allocations(allocations, 0.0)
        for candidate in candidate_rows:
            candidate["planned_amount_cny"] = 0.0
            candidate["blockers"] = _unique(
                [
                    *(candidate.get("blockers") or []),
                    "portfolio_decision_gate_blocked",
                ]
            )
    if baseline_blocks:
        hard_blockers.append("current_portfolio_outside_policy")
        allocations = _scale_allocations(allocations, 0.0)
        for candidate in candidate_rows:
            candidate["planned_amount_cny"] = 0.0
            candidate["blockers"] = _unique(
                [
                    *(candidate.get("blockers") or []),
                    "current_portfolio_outside_policy",
                ]
            )
    elif (
        not hard_blockers
        and exposure_ready
        and any(amount > 0 for amount in allocations.values())
    ):
        stress_rows, _, proposed_blocks = _stress_matrix(
            holdings=holdings,
            exposure=exposure or {},
            profile=profile,
            monthly_budget=monthly_budget,
            allocations=allocations,
            names=names,
        )
        if proposed_blocks:
            low = 0.0
            high = 1.0
            best_rows = baseline_matrix
            for _ in range(24):
                middle = (low + high) / 2
                trial = _scale_allocations(allocations, middle)
                trial_rows, _, trial_blocks = _stress_matrix(
                    holdings=holdings,
                    exposure=exposure or {},
                    profile=profile,
                    monthly_budget=monthly_budget,
                    allocations=trial,
                    names=names,
                )
                if trial_blocks:
                    high = middle
                else:
                    low = middle
                    best_rows = trial_rows
            risk_scale = low
            allocations = _scale_allocations(allocations, risk_scale)
            stress_rows = best_rows
            proposed_blocks = set()
            for candidate in candidate_rows:
                key = (candidate["market"], candidate["symbol"])
                candidate["planned_amount_cny"] = allocations.get(
                    key, 0.0
                )
                if risk_scale < 0.999:
                    candidate["blockers"] = _unique(
                        [
                            *(candidate.get("blockers") or []),
                            "scaled_by_portfolio_stress_gate",
                        ]
                    )
    elif exposure_ready and holdings_ready and profile_ready:
        stress_rows, _, proposed_blocks = _stress_matrix(
            holdings=holdings,
            exposure=exposure or {},
            profile=profile,
            monthly_budget=monthly_budget,
            allocations={},
            names=names,
        )

    if baseline_blocks:
        gates.append(
            _gate(
                "whole_portfolio_policy",
                "全组合政策边界",
                "block",
                "当前组合在保守压力模型中已触发："
                + "、".join(sorted(baseline_blocks)),
                source=portfolio_decision_twin.METHOD_VERSION,
            )
        )
    elif stress_rows:
        gates.append(
            _gate(
                "whole_portfolio_policy",
                "全组合政策边界",
                "pass" if not proposed_blocks else "block",
                (
                    "计划后单品、权益、行业与情景亏损预算均通过"
                    if not proposed_blocks
                    else "计划后仍触发："
                    + "、".join(sorted(proposed_blocks))
                ),
                source=portfolio_decision_twin.METHOD_VERSION,
            )
        )

    allocated = round(sum(allocations.values()), 2)
    reserve = round(max(0.0, monthly_budget - allocated), 2)
    for candidate in candidate_rows:
        key = (candidate["market"], candidate["symbol"])
        amount = allocations.get(key, 0.0)
        post_amount = (
            _number(candidate.get("existing_amount_cny")) or 0
        ) + amount
        candidate.update(
            {
                "planned_amount_cny": round(amount, 2),
                "post_amount_cny": round(post_amount, 2),
                "post_ratio_pct": _round(
                    post_amount / post_total * 100
                    if post_total > 0
                    else None
                ),
                "action": (
                    "limited_manual_pilot"
                    if amount > 0
                    else "observe"
                ),
                "label": (
                    "限额人工试投"
                    if amount > 0
                    else "继续观察，不投入"
                ),
                "manual_review_required": True,
                "execution_authorized": False,
                "evidence_interpretation": (
                    "历史冻结后的前瞻成本后超额、策略独立贡献与"
                    "候选共识；不是该股票未来收益概率。"
                ),
            }
        )

    hard_blockers = _unique(hard_blockers)
    if hard_blockers:
        status = "blocked"
    elif allocated > 0:
        status = "ready"
    else:
        status = "watch"

    if status == "ready":
        primary_action = {
            "code": "limited_manual_pilot",
            "label": "限额人工试投",
            "headline": f"本期最多试投 ¥{allocated:,.2f}，保留 ¥{reserve:,.2f}",
            "description": (
                f"{len([item for item in candidate_rows if item['planned_amount_cny'] > 0])} "
                "只候选通过前瞻、策略委员会、组合与投资政策门禁；"
                "金额是研究上限，不是订单。"
            ),
        }
    elif critical_actions:
        first = critical_actions[0]
        primary_action = {
            "code": first["action"],
            "label": first["label"],
            "headline": f"先处理 {first['name']}：{first['label']}",
            "description": first["rationale"],
        }
    elif hard_blockers:
        first_gate = next(
            (
                item
                for item in gates
                if item.get("status") == "block"
            ),
            None,
        )
        primary_action = {
            "code": "complete_evidence",
            "label": "先补齐关键证据",
            "headline": (first_gate or {}).get("label")
            or "资金决策暂不可用",
            "description": (first_gate or {}).get("detail")
            or "关键证据门禁未通过。",
        }
    elif monthly_budget <= 0:
        primary_action = {
            "code": "no_new_capital",
            "label": "本期不新增",
            "headline": "月度新增预算为 0，维持已有仓位纪律",
            "description": "如需新增资金，请先更新并重新激活投资政策。",
        }
    elif not eligible_strategies:
        primary_action = {
            "code": "hold_cash_collect_evidence",
            "label": "保留资金，继续验证",
            "headline": f"本期 ¥{monthly_budget:,.2f} 暂不投入",
            "description": (
                "没有策略同时通过独立前瞻样本、成本后超额、"
                "置信区间、多重检验与不可变记分卡门禁。"
            ),
        }
    else:
        primary_action = {
            "code": "hold_cash_no_risk_room",
            "label": "保留资金，风险空间不足",
            "headline": f"本期 ¥{monthly_budget:,.2f} 暂不投入",
            "description": (
                "候选虽有前瞻资格，但单品、权益、行业或压力预算"
                "没有可用新增空间。"
            ),
        }

    cutoff_values = [
        str(valuation_snapshot.get("created_at") or ""),
        str((report or {}).get("as_of") or ""),
        str(
            ((exposure or {}).get("snapshot") or {}).get(
                "created_at"
            )
            or ""
        ),
        *(
            str(item.get("evidence_cutoff_at") or "")
            for item in strategy_rows
        ),
    ]
    evidence_cutoff = max(cutoff_values) if any(cutoff_values) else None
    bindings = {
        "profile_version_id": (
            profile.get("profile_version_id") if profile_ready else None
        ),
        "valuation_snapshot_id": valuation_id,
        "action_report_id": (
            (report.get("report") or {}).get("id") if report else None
        ),
        "exposure_snapshot_id": (
            ((exposure or {}).get("snapshot") or {}).get("id")
        ),
        "scorecard_ids": sorted(
            str(item.get("scorecard_id"))
            for item in eligible_strategies
            if item.get("scorecard_id")
        ),
        "basket_ids": sorted(
            str(item.get("basket_id"))
            for item in eligible_strategies
            if item.get("basket_id")
        ),
        "committee_evidence_sha256": sha256_payload(
            committee_evidence
        ),
    }
    evidence = {
        "schema_version": "portfolio_capital_evidence.v1",
        "engine_version": ENGINE_VERSION,
        "bindings": bindings,
        "holdings": _compact_holdings(holdings),
        "profile": {
            key: profile.get(key)
            for key in (
                "configured",
                "profile_version_id",
                "version_no",
                "payload_sha256",
                "risk",
                "horizon",
                "experience_level",
                "primary_objective",
                "monthly_budget",
                "max_single_ratio",
                "max_equity_ratio",
                "max_industry_ratio",
                "max_drawdown_pct",
                "allowed_fund_markets",
                "accept_fx_risk",
                "review_due_at",
                "integrity_verified",
            )
        }
        | {
            "governance_verified": (
                profile.get("governance_integrity") or {}
            ).get("verified")
        },
        "valuation": {
            "snapshot": {
                key: valuation_snapshot.get(key)
                for key in (
                    "id",
                    "schema_version",
                    "method_version",
                    "holdings_sha256",
                    "status",
                    "fresh_until",
                    "payload_sha256",
                    "created_at",
                )
            },
            "binding": valuation.get("binding"),
            "runtime_gate": valuation.get("runtime_gate"),
            "summary": valuation_payload.get("summary"),
            "coverage": valuation_payload.get("coverage"),
            "positions": valuation_payload.get("positions") or [],
        },
        "action_report": _action_report_evidence(report),
        "exposure": (
            {
                "snapshot": exposure.get("snapshot"),
                "integrity": exposure.get("integrity"),
                "schema_version": exposure.get("schema_version"),
                "model_version": exposure.get("model_version"),
                "holdings_sha256": exposure.get("holdings_sha256"),
                "profile_version_id": exposure.get(
                    "profile_version_id"
                ),
                "status": exposure.get("status"),
                "summary": exposure.get("summary"),
                "quality": exposure.get("quality"),
                "funds": exposure.get("funds") or [],
                "industries": exposure.get("industries") or [],
                "markets": exposure.get("markets") or [],
                "valuation_binding": exposure.get(
                    "valuation_binding"
                ),
            }
            if exposure
            else {
                "available": False,
                "reasons": exposure_reasons,
            }
        ),
        "opportunity_strategies": strategy_rows,
        "opportunity_committee": committee,
        "engine_policy": {
            "hard_global_pilot_cap_pct": HARD_GLOBAL_PILOT_CAP_PCT,
            "maximum_strategy_count": MAX_STRATEGIES,
            "maximum_candidate_count": MAX_CANDIDATES,
            "candidate_industry_treatment": (
                "候选股票行业未知时，全部新增金额按同一最坏行业桶占用容量"
            ),
            "strategy_allocation": (
                "通过门禁的策略由投资委员会以等权为锚，按独立贡献"
                "窄幅倾斜；三期连续失效停用，高冗余和单策略场景"
                "主动保留现金，再按冻结纸面组合权重分配候选"
            ),
        },
    }

    result = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(current),
        "decision_date": current.date().isoformat(),
        "evidence_cutoff_at": evidence_cutoff,
        "status": status,
        "primary_action": primary_action,
        "capital": {
            "base_currency": "CNY",
            "portfolio_value_cny": round(total_value, 2),
            "monthly_new_capital_cny": round(monthly_budget, 2),
            "post_plan_total_cny": round(post_total, 2),
            "global_pilot_cap_pct": round(global_pilot_pct, 2),
            "global_pilot_cap_cny": round(global_pilot_cap, 2),
            "equity_capacity_cny": round(equity_room, 2),
            "conservative_industry_capacity_cny": round(
                industry_room, 2
            ),
            "risk_capacity_cny": round(
                conservative_risk_room, 2
            ),
            "planned_deployment_cny": allocated,
            "planned_cash_reserve_cny": reserve,
            "deployment_ratio_of_monthly_budget_pct": _round(
                allocated / monthly_budget * 100
                if monthly_budget > 0
                else 0
            ),
            "risk_scaling_factor": round(risk_scale, 4),
            "cash_source_confirmed": False,
            "cash_source_notice": (
                "月度预算来自投资政策，不代表券商账户实时可用现金；"
                "执行前必须人工核对。"
            ),
        },
        "gates": gates,
        "blocking_reasons": hard_blockers,
        "existing_position_actions": existing_actions,
        "candidate_actions": candidate_rows,
        "strategy_evidence": strategy_rows,
        "investment_committee": committee,
        "stress_matrix": stress_rows,
        "data_quality": {
            "profile_current": profile_ready,
            "valuation_current": valuation_ready,
            "action_report_current": report_ready,
            "exposure_current": exposure_ready,
            "eligible_strategy_count": len(eligible_strategies),
            "live_capital_eligible_strategy_count": live_eligible_count,
            "committee_status": committee_status,
            "committee_selected_strategy_count": len(
                eligible_strategies
            ),
            "committee_evidence_sha256": bindings[
                "committee_evidence_sha256"
            ],
            "critical_existing_action_count": len(critical_actions),
            "exposure_reasons": exposure_reasons,
        },
        "data_lineage": bindings
        | {
            "holdings_sha256": portfolio_exposure.holdings_sha256(
                holdings
            ),
            "evidence_cutoff_at": evidence_cutoff,
        },
        "methodology": {
            "decision_order": (
                "事实完整性 → 已有仓位风险/纪律 → 前瞻策略资格 → "
                "策略失效/冗余/共识委员会 → 月度预算 → "
                "单品/权益/行业容量 → 全组合压力情景"
            ),
            "profit_evidence": (
                "只使用冻结后独立前瞻批次的成本后相对基准结果；"
                "要求置信区间、多重检验、回撤与命中率同时通过。"
            ),
            "allocation": (
                "多个合格策略以等权为锚，按可验证独立贡献窄幅倾斜；"
                "单策略、重复押注和单一候选过度集中会转为现金，"
                "最终金额继续受当前组合和投资政策的更严格边界约束。"
            ),
            "stress": (
                "使用当前暴露区间和说明性市场冲击比较新增资金前后；"
                "不是历史重演概率，也不是未来行情预测。"
            ),
        },
        "boundaries": {
            "execution_authorized": False,
            "automatic_order_creation": False,
            "share_quantity_provided": False,
            "return_guaranteed": False,
            "notice": (
                "本功能提高决策的一致性、证据质量和风险可控性，"
                "不能保证盈利。任何金额都是人工研究上限，不是买卖指令。"
            ),
        },
    }
    return result, evidence


def build_capital_decision(**kwargs) -> dict[str, Any]:
    result, evidence = _assemble(**kwargs)
    return {
        **result,
        "evidence_sha256": sha256_payload(evidence),
    }


def current_capital_decision(
    *,
    user_id: str,
    tenant_id: str = "public",
    plan_repo: PortfolioCapitalRepository = capital_repository,
    **kwargs,
) -> dict[str, Any]:
    result, evidence = _assemble(
        user_id=user_id,
        tenant_id=tenant_id,
        **kwargs,
    )
    evidence_sha = sha256_payload(evidence)
    latest = plan_repo.latest_plan(
        tenant_id=tenant_id, user_id=user_id
    )
    return {
        **result,
        "evidence_sha256": evidence_sha,
        "persistence": {
            "latest_plan": (
                {
                    key: latest.get(key)
                    for key in (
                        "id",
                        "status",
                        "decision_date",
                        "evidence_sha256",
                        "result_sha256",
                        "created_at",
                    )
                }
                if latest
                else None
            ),
            "binding_current": bool(
                latest
                and (latest.get("integrity") or {}).get("verified")
                and latest.get("engine_version") == ENGINE_VERSION
                and latest.get("evidence_sha256") == evidence_sha
            ),
        },
    }


def freeze_capital_decision(
    *,
    user_id: str,
    tenant_id: str,
    actor_id: str,
    plan_repo: PortfolioCapitalRepository = capital_repository,
    **kwargs,
) -> tuple[dict[str, Any], bool]:
    result, evidence = _assemble(
        user_id=user_id,
        tenant_id=tenant_id,
        **kwargs,
    )
    return plan_repo.create_plan(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        status=str(result["status"]),
        decision_date=str(result["decision_date"]),
        evidence=evidence,
        result=result,
    )


def list_plan_summaries(
    *,
    user_id: str,
    tenant_id: str,
    limit: int = 30,
    plan_repo: PortfolioCapitalRepository = capital_repository,
) -> dict[str, Any]:
    items = plan_repo.list_plans(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    public = []
    for item in items:
        result = item.get("result") or {}
        public.append(
            {
                key: item.get(key)
                for key in (
                    "id",
                    "status",
                    "decision_date",
                    "profile_version_id",
                    "valuation_snapshot_id",
                    "action_report_id",
                    "exposure_snapshot_id",
                    "evidence_sha256",
                    "result_sha256",
                    "created_at",
                )
            }
            | {
                "primary_action": result.get("primary_action"),
                "capital": result.get("capital"),
                "data_lineage": result.get("data_lineage"),
            }
        )
    return {"items": public, "count": len(public)}
