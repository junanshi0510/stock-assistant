# -*- coding: utf-8 -*-
"""Multi-horizon Alpha capital routing with core/satellite governance.

The router turns only forward-qualified, calibrated Alpha facts into a
research-only model allocation. It deliberately separates signal generation
from portfolio construction, keeps an explicit cash target, and never creates
orders, shorts, leverage, or a return promise.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any, Callable

import alpha_forecast_service
import portfolio_exposure
import storage
from alpha_capital_repository import (
    AlphaCapitalConflict,
    AlphaCapitalRepository,
    repository as mandate_repository,
    sha256_payload,
)


SCHEMA_VERSION = "alpha_capital_route.v1"
EVIDENCE_SCHEMA_VERSION = "alpha_capital_evidence.v1"
ENGINE_VERSION = "multi_horizon_alpha_capital_router@1.0.0"
ALPHA_PILOT_CAP_PCT = 2.0
MIN_DIRECTIONAL_EDGE = 0.05
MAX_CANDIDATE_MODEL_WEIGHT_PCT = 20.0
REBALANCE_DRIFT_THRESHOLD_PCT = 10.0

HORIZON_WEIGHTS = {
    "stock": {5: 0.25, 20: 0.35, 60: 0.40},
    "fund": {20: 0.20, 60: 0.30, 120: 0.50},
}
CORE_HORIZONS = {
    "stock": {60},
    "fund": {60, 120},
}
SLEEVE_POLICY = {
    "short": {"satellite_pct": 60.0, "core_pct": 40.0},
    "mid_long": {"satellite_pct": 30.0, "core_pct": 70.0},
    "long": {"satellite_pct": 15.0, "core_pct": 85.0},
}
MARKET_PERMISSION = {
    "A股": "mainland",
    "港股": "hong_kong",
    "美股": "united_states",
    "基金": "mainland",
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


def _round(value: Any, digits: int = 4) -> float | None:
    parsed = _number(value)
    return round(parsed, digits) if parsed is not None else None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, float(value)))


def _date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _asset_key(item: dict[str, Any]) -> str:
    return ":".join(
        (
            str(item.get("asset_type") or ""),
            str(item.get("market") or ""),
            str(item.get("symbol") or item.get("code") or ""),
        )
    )


def _profile_ready(
    profile: dict[str, Any],
    *,
    current: dt.datetime,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not profile.get("configured"):
        reasons.append("investment_policy_not_configured")
    if not (profile.get("governance_integrity") or {}).get("verified"):
        reasons.append("investment_policy_integrity_failed")
    if not profile.get("profile_version_id"):
        reasons.append("investment_policy_version_missing")
    review_due = _datetime(profile.get("review_due_at"))
    if review_due is not None and review_due < current:
        reasons.append("investment_policy_review_overdue")
    if str(profile.get("horizon") or "") not in SLEEVE_POLICY:
        reasons.append("investment_horizon_unsupported")
    return not reasons, reasons


def _forward_quality(
    scorecard: dict[str, Any],
    horizon: int,
) -> dict[str, Any] | None:
    row = next(
        (
            item
            for item in scorecard.get("horizons") or []
            if int(item.get("horizon_sessions") or 0) == horizon
        ),
        None,
    )
    if not row or not row.get("decision_eligible"):
        return None
    outcomes = max(0.0, _number(row.get("outcome_count"), 0.0) or 0.0)
    run_dates = max(
        0.0, _number(row.get("run_date_count"), 0.0) or 0.0
    )
    symbols = max(
        0.0, _number(row.get("symbol_count"), 0.0) or 0.0
    )
    skill = max(
        0.0, _number(row.get("brier_skill_score"), 0.0) or 0.0
    )
    parsed_ece = _number(
        row.get("expected_calibration_error"), 1.0
    )
    ece = max(0.0, parsed_ece if parsed_ece is not None else 1.0)
    positive_run_rate = _clamp(
        _number(row.get("positive_run_rate"), 0.0) or 0.0
    )
    factors = {
        "outcomes": _clamp(outcomes / 60.0),
        "run_dates": _clamp(run_dates / 12.0),
        "symbols": _clamp(symbols / 8.0),
        "skill": _clamp(skill / 0.10),
        "calibration": _clamp(1.0 - ece / 0.12),
        "run_stability": positive_run_rate,
    }
    reliability = (
        0.25 * factors["outcomes"]
        + 0.15 * factors["run_dates"]
        + 0.10 * factors["symbols"]
        + 0.25 * factors["skill"]
        + 0.15 * factors["calibration"]
        + 0.10 * factors["run_stability"]
    )
    return {
        "horizon_sessions": horizon,
        "outcome_count": int(outcomes),
        "run_date_count": int(run_dates),
        "symbol_count": int(symbols),
        "brier_skill_score": _round(skill, 6),
        "expected_calibration_error": _round(ece, 6),
        "high_low_return_spread_pct": _round(
            row.get("high_low_return_spread_pct"), 6
        ),
        "positive_run_rate": _round(positive_run_rate, 6),
        "reliability": _round(reliability, 6),
        "reliability_factors": {
            key: _round(value, 6) for key, value in factors.items()
        },
    }


def _program_records(
    program: dict[str, Any],
    *,
    held_keys: set[str],
    allowed_permissions: set[str],
    current: dt.datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    program_id = str(program.get("id") or "")
    policy = program.get("policy") or {}
    asset_type = str(program.get("asset_type") or policy.get("asset_type") or "")
    market = str(program.get("market") or policy.get("market") or "")
    run = program.get("latest_run") or {}
    reasons: list[str] = []
    if program.get("status") != "active":
        reasons.append("program_not_active")
    if not (program.get("integrity") or {}).get("verified"):
        reasons.append("program_integrity_failed")
    if run.get("status") not in {"succeeded", "partial"}:
        reasons.append("completed_run_missing")
    if not (run.get("integrity") or {}).get("verified"):
        reasons.append("run_integrity_failed")
    if asset_type not in HORIZON_WEIGHTS:
        reasons.append("asset_type_unsupported")
    expected_horizons = set(HORIZON_WEIGHTS.get(asset_type) or {})
    if set(int(item) for item in policy.get("horizons") or []) != expected_horizons:
        reasons.append("horizon_contract_changed")
    as_of_date = _date(run.get("as_of_date"))
    cadence = max(1, int(_number(policy.get("cadence_days"), 7) or 7))
    minimum_freshness = 45 if asset_type == "fund" else 14
    stale_after_days = max(minimum_freshness, cadence * 2)
    age_days = (
        (current.date() - as_of_date).days
        if as_of_date is not None
        else None
    )
    if age_days is None or age_days < 0 or age_days > stale_after_days:
        reasons.append("latest_run_stale")
    permission = MARKET_PERMISSION.get(market)
    market_allowed = bool(
        permission is not None and permission in allowed_permissions
    )
    result = run.get("result") or {}
    scorecard = program.get("forward_scorecard") or {}
    snapshot = {
        "program_id": program_id,
        "program_name": program.get("name"),
        "asset_type": asset_type,
        "market": market,
        "status": program.get("status"),
        "policy_sha256": program.get("policy_sha256"),
        "run_id": run.get("id"),
        "run_status": run.get("status"),
        "run_as_of_date": run.get("as_of_date"),
        "run_completed_at": run.get("completed_at"),
        "run_result_sha256": run.get("result_sha256"),
        "age_days": age_days,
        "stale_after_days": stale_after_days,
        "market_permission": permission,
        "market_allowed": market_allowed,
        "eligible": not reasons,
        "exclusion_reasons": reasons,
        "forward_scorecard": scorecard,
    }
    if reasons:
        return [], snapshot

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    weights = HORIZON_WEIGHTS[asset_type]
    for forecast in result.get("forecasts") or []:
        horizon = int(_number(forecast.get("horizon_sessions"), 0) or 0)
        if horizon not in weights:
            continue
        if not (
            forecast.get("historical_gate_passed")
            and forecast.get("decision_eligible")
            and forecast.get("decision_source_eligible")
        ):
            continue
        quality = _forward_quality(scorecard, horizon)
        probability = _number(forecast.get("published_probability"))
        base_rate = _number(forecast.get("base_rate"))
        if quality is None or probability is None or base_rate is None:
            continue
        edge = probability - base_rate
        reliability = float(quality["reliability"])
        symbol = str(forecast.get("symbol") or "")
        if not symbol:
            continue
        by_symbol[symbol].append(
            {
                "forecast_id": forecast.get("forecast_id"),
                "horizon_sessions": horizon,
                "weight": weights[horizon],
                "probability": _round(probability, 6),
                "base_rate": _round(base_rate, 6),
                "raw_edge": _round(edge, 6),
                "effective_edge": _round(
                    edge * reliability, 6
                ),
                "direction": (
                    "positive"
                    if edge >= MIN_DIRECTIONAL_EDGE
                    else "negative"
                    if edge <= -MIN_DIRECTIONAL_EDGE
                    else "neutral"
                ),
                "stance": forecast.get("stance"),
                "as_of_date": forecast.get("as_of_date"),
                "eligible_after": forecast.get("eligible_after"),
                "source_tier": (
                    forecast.get("source_evidence") or {}
                ).get("provider_tier"),
                "quality": quality,
            }
        )

    records: list[dict[str, Any]] = []
    name_by_symbol = {
        str(item.get("symbol") or ""): str(
            item.get("name") or item.get("symbol") or ""
        )
        for item in policy.get("symbols") or []
    }
    for symbol, facts in sorted(by_symbol.items()):
        facts.sort(key=lambda item: item["horizon_sessions"])
        positive = [item for item in facts if item["direction"] == "positive"]
        negative = [item for item in facts if item["direction"] == "negative"]
        if positive and negative:
            state = "conflict"
            label = "长短周期方向冲突"
        elif len(positive) >= 2:
            state = "supportive"
            label = "多周期正向共识"
        elif len(negative) >= 2:
            state = "defensive"
            label = "多周期负向共识"
        elif len(facts) < 2:
            state = "collecting"
            label = "仅一个合格周期"
        else:
            state = "neutral"
            label = "方向强度不足"
        denominator = sum(float(item["weight"]) for item in facts)
        weighted_edge = (
            sum(
                float(item["weight"])
                * float(item["effective_edge"])
                for item in facts
            )
            / denominator
            if denominator > 0
            else 0.0
        )
        weighted_raw_edge = (
            sum(
                float(item["weight"]) * float(item["raw_edge"])
                for item in facts
            )
            / denominator
            if denominator > 0
            else 0.0
        )
        weighted_reliability = (
            sum(
                float(item["weight"])
                * float(item["quality"]["reliability"])
                for item in facts
            )
            / denominator
            if denominator > 0
            else 0.0
        )
        tactical_contribution = sum(
            float(item["weight"])
            * max(0.0, float(item["effective_edge"]))
            for item in facts
            if item["horizon_sessions"] not in CORE_HORIZONS[asset_type]
        )
        core_contribution = sum(
            float(item["weight"])
            * max(0.0, float(item["effective_edge"]))
            for item in facts
            if item["horizon_sessions"] in CORE_HORIZONS[asset_type]
        )
        sleeve = (
            "core"
            if core_contribution >= tactical_contribution
            else "satellite"
        )
        key = f"{asset_type}:{market}:{symbol}"
        records.append(
            {
                "key": key,
                "asset_type": asset_type,
                "market": market,
                "symbol": symbol,
                "name": name_by_symbol.get(symbol) or symbol,
                "program_id": program_id,
                "program_name": program.get("name"),
                "run_id": run.get("id"),
                "run_as_of_date": run.get("as_of_date"),
                "run_result_sha256": run.get("result_sha256"),
                "state": state,
                "label": label,
                "sleeve": sleeve,
                "eligible_horizon_count": len(facts),
                "weighted_raw_edge": _round(weighted_raw_edge, 6),
                "weighted_effective_edge": _round(weighted_edge, 6),
                "weighted_reliability": _round(
                    weighted_reliability, 6
                ),
                "market_allowed": market_allowed,
                "market_permission": permission,
                "held": key in held_keys,
                "capital_bridge_state": (
                    "held_fund_top_up_candidate"
                    if asset_type == "fund" and key in held_keys
                    else "new_fund_due_diligence_only"
                    if asset_type == "fund"
                    else "stock_candidate"
                ),
                "horizons": facts,
                "duplicate_programs_excluded": [],
                "model_target_weight_pct": 0.0,
                "execution_authorized": False,
            }
        )
    snapshot["record_count"] = len(records)
    return records, snapshot


def _canonicalize_records(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["key"]].append(row)
    selected: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for key, candidates in sorted(grouped.items()):
        ordered = sorted(
            candidates,
            key=lambda item: (
                -int(item.get("eligible_horizon_count") or 0),
                -float(item.get("weighted_reliability") or 0),
                -abs(float(item.get("weighted_effective_edge") or 0)),
                -(
                    _date(item.get("run_as_of_date")).toordinal()
                    if _date(item.get("run_as_of_date"))
                    else 0
                ),
                str(item.get("program_id") or ""),
            ),
        )
        canonical = dict(ordered[0])
        duplicates = [
            {
                "program_id": item.get("program_id"),
                "program_name": item.get("program_name"),
                "run_id": item.get("run_id"),
                "state": item.get("state"),
                "weighted_effective_edge": item.get(
                    "weighted_effective_edge"
                ),
                "reason": "same_asset_model_family_not_independent",
            }
            for item in ordered[1:]
        ]
        directional_states = {
            str(item.get("state"))
            for item in ordered
            if item.get("state") in {"supportive", "defensive", "conflict"}
        }
        if (
            "conflict" in directional_states
            or {"supportive", "defensive"}.issubset(directional_states)
        ):
            canonical["state"] = "conflict"
            canonical["label"] = "重复项目方向冲突·弃权"
            canonical["model_target_weight_pct"] = 0.0
            canonical["duplicate_direction_conflict"] = True
        canonical["duplicate_programs_excluded"] = duplicates
        selected.append(canonical)
        exclusions.extend(
            [{"key": key, **item} for item in duplicates]
        )
    return selected, exclusions


def _allocate_with_cap(
    rows: list[dict[str, Any]],
    budget_pct: float,
) -> dict[str, float]:
    allocations = {row["key"]: 0.0 for row in rows}
    remaining = max(0.0, float(budget_pct))
    active = list(rows)
    for _ in range(len(rows) + 1):
        if not active or remaining <= 1e-9:
            break
        score_total = sum(
            max(1e-12, float(item["allocation_score"]))
            for item in active
        )
        capped: list[dict[str, Any]] = []
        proposals: dict[str, float] = {}
        for item in active:
            key = item["key"]
            share = (
                remaining
                * max(1e-12, float(item["allocation_score"]))
                / score_total
            )
            room = (
                MAX_CANDIDATE_MODEL_WEIGHT_PCT - allocations[key]
            )
            proposals[key] = share
            if share >= room - 1e-9:
                capped.append(item)
        if not capped:
            for item in active:
                allocations[item["key"]] += proposals[item["key"]]
            remaining = 0.0
            break
        for item in capped:
            key = item["key"]
            room = max(
                0.0,
                MAX_CANDIDATE_MODEL_WEIGHT_PCT - allocations[key],
            )
            allocations[key] += room
            remaining -= room
        capped_keys = {item["key"] for item in capped}
        active = [item for item in active if item["key"] not in capped_keys]
    return {
        key: round(max(0.0, value), 4)
        for key, value in allocations.items()
    }


def _route_drift(
    current_candidates: list[dict[str, Any]],
    previous_result: dict[str, Any] | None,
) -> dict[str, Any]:
    current = {
        item["key"]: float(item.get("model_target_weight_pct") or 0)
        for item in current_candidates
        if float(item.get("model_target_weight_pct") or 0) > 0
    }
    current["CASH"] = max(0.0, 100.0 - sum(current.values()))
    if not previous_result:
        return {
            "state": "baseline",
            "rebalance_review_required": False,
            "one_way_turnover_pct": 0.0,
            "entries": sorted(key for key in current if key != "CASH"),
            "exits": [],
            "threshold_pct": REBALANCE_DRIFT_THRESHOLD_PCT,
            "notice": "首个模型路线只建立基线，不产生调仓指令。",
        }
    previous = {
        item["key"]: float(item.get("model_target_weight_pct") or 0)
        for item in previous_result.get("candidates") or []
        if float(item.get("model_target_weight_pct") or 0) > 0
    }
    previous["CASH"] = max(0.0, 100.0 - sum(previous.values()))
    keys = set(current) | set(previous)
    turnover = 0.5 * sum(
        abs(current.get(key, 0.0) - previous.get(key, 0.0))
        for key in keys
    )
    entries = sorted(
        key
        for key in current
        if key != "CASH"
        and current.get(key, 0.0) > 0
        and previous.get(key, 0.0) <= 0
    )
    exits = sorted(
        key
        for key in previous
        if key != "CASH"
        and previous.get(key, 0.0) > 0
        and current.get(key, 0.0) <= 0
    )
    review = bool(
        turnover >= REBALANCE_DRIFT_THRESHOLD_PCT
        or entries
        or exits
    )
    return {
        "state": "review" if review else "within_band",
        "rebalance_review_required": review,
        "one_way_turnover_pct": round(turnover, 4),
        "entries": entries,
        "exits": exits,
        "threshold_pct": REBALANCE_DRIFT_THRESHOLD_PCT,
        "notice": (
            "漂移仅触发人工复核；系统不会自动生成交易。"
            if review
            else "目标变化位于固定漂移带内，继续观察。"
        ),
    }


def compose_alpha_capital_route(
    *,
    overview: dict[str, Any],
    profile: dict[str, Any],
    holdings: list[dict[str, Any]],
    previous_result: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = _now(now)
    profile_is_ready, profile_reasons = _profile_ready(
        profile, current=current
    )
    permissions = set(profile.get("allowed_fund_markets") or [])
    held_keys = {
        _asset_key(
            {
                "asset_type": item.get("asset_type"),
                "market": item.get("market"),
                "symbol": item.get("code"),
            }
        )
        for item in holdings
    }
    all_records: list[dict[str, Any]] = []
    program_snapshots: list[dict[str, Any]] = []
    for program in sorted(
        overview.get("programs") or [],
        key=lambda item: str(item.get("id") or ""),
    ):
        records, snapshot = _program_records(
            program,
            held_keys=held_keys,
            allowed_permissions=permissions,
            current=current,
        )
        all_records.extend(records)
        program_snapshots.append(snapshot)
    canonical, duplicate_exclusions = _canonicalize_records(all_records)
    supportive = [
        item
        for item in canonical
        if item["state"] == "supportive"
        and item.get("market_allowed")
        and float(item.get("weighted_effective_edge") or 0) > 0
    ]
    for item in supportive:
        item["allocation_score"] = round(
            max(1e-9, float(item["weighted_effective_edge"])),
            8,
        )

    breadth = len(supportive)
    breadth_cap = 0.0 if breadth <= 0 else 35.0 if breadth == 1 else 60.0 if breadth == 2 else 100.0
    sleeve_policy = SLEEVE_POLICY.get(
        str(profile.get("horizon") or ""), SLEEVE_POLICY["mid_long"]
    )
    allocations: dict[str, float] = {}
    if profile_is_ready:
        for sleeve, policy_key in (
            ("core", "core_pct"),
            ("satellite", "satellite_pct"),
        ):
            rows = [item for item in supportive if item["sleeve"] == sleeve]
            sleeve_budget = (
                breadth_cap * float(sleeve_policy[policy_key]) / 100.0
            )
            allocations.update(
                _allocate_with_cap(rows, sleeve_budget)
            )
    for item in canonical:
        item["model_target_weight_pct"] = allocations.get(
            item["key"], 0.0
        )
        item["allocation_score"] = _round(
            item.get("allocation_score"), 8
        )
        item["candidate_rank"] = None
    candidates = [
        item
        for item in canonical
        if float(item.get("model_target_weight_pct") or 0) > 0
    ]
    candidates.sort(
        key=lambda item: (
            -float(item.get("model_target_weight_pct") or 0),
            -float(item.get("allocation_score") or 0),
            item["key"],
        )
    )
    for index, item in enumerate(candidates, start=1):
        item["candidate_rank"] = index
    vetoes = [
        item
        for item in canonical
        if item["state"] in {"defensive", "conflict"}
    ]
    watchlist = [
        item
        for item in canonical
        if item["state"] in {"neutral", "collecting"}
        or (
            item["state"] == "supportive"
            and not item.get("market_allowed")
        )
    ]
    invested_pct = round(
        sum(
            float(item.get("model_target_weight_pct") or 0)
            for item in candidates
        ),
        4,
    )
    cash_pct = round(max(0.0, 100.0 - invested_pct), 4)
    eligible_program_count = sum(
        bool(item.get("eligible")) for item in program_snapshots
    )
    eligible_record_count = len(canonical)
    if not profile_is_ready:
        status = "blocked"
    elif not eligible_program_count or not eligible_record_count:
        status = "collecting"
    elif invested_pct <= 0:
        status = "abstained"
    else:
        status = "paper_ready"

    gates = [
        {
            "code": "active_investment_policy",
            "label": "有效投资政策",
            "status": "pass" if profile_is_ready else "block",
            "detail": (
                f"已绑定政策 {profile.get('profile_version_id')}"
                if profile_is_ready
                else "、".join(profile_reasons)
            ),
        },
        {
            "code": "fresh_forward_qualified_programs",
            "label": "前瞻合格且新鲜的项目",
            "status": "pass" if eligible_program_count else "watch",
            "detail": (
                f"{eligible_program_count}/{len(program_snapshots)} 个项目可进入路由"
            ),
        },
        {
            "code": "multi_horizon_consensus",
            "label": "多周期方向共识",
            "status": "pass" if supportive else "watch",
            "detail": (
                f"{len(supportive)} 个正向候选，{len(vetoes)} 个负向或冲突否决"
            ),
        },
        {
            "code": "duplicate_model_independence",
            "label": "重复模型去重",
            "status": "pass",
            "detail": (
                f"{len(duplicate_exclusions)} 个同资产同模型族重复结果未重复计票"
            ),
        },
        {
            "code": "cash_and_candidate_caps",
            "label": "现金与集中度边界",
            "status": "pass",
            "detail": (
                f"模型投入 {invested_pct:.1f}%，现金 {cash_pct:.1f}%；"
                f"单候选不超过 {MAX_CANDIDATE_MODEL_WEIGHT_PCT:.0f}%"
            ),
        },
    ]
    if status == "paper_ready":
        primary_action = {
            "code": "freeze_research_mandate",
            "label": "冻结研究资金路线",
            "headline": (
                f"{len(candidates)} 个候选形成核心-卫星路线，"
                f"模型保留现金 {cash_pct:.1f}%"
            ),
            "description": (
                "冻结后才可被全组合资金引擎读取；最终金额仍受 2% Alpha "
                "试投上限、投资政策、持仓风险和压力测试约束。"
            ),
        }
    elif status == "abstained":
        primary_action = {
            "code": "hold_cash",
            "label": "保留现金",
            "headline": "合格概率尚未形成可投资的多周期正向共识",
            "description": "负向与冲突结果只否决新增，不做空、不反向下注。",
        }
    elif status == "collecting":
        primary_action = {
            "code": "collect_forward_outcomes",
            "label": "继续积累真实前瞻结果",
            "headline": "尚无新鲜且通过真实前瞻门禁的完整概率项目",
            "description": "不会用历史回测、Shadow 概率或过期运行代替真实前瞻资格。",
        }
    else:
        primary_action = {
            "code": "repair_investment_policy",
            "label": "先修复投资政策",
            "headline": "核心-卫星资金路线被政策门禁阻断",
            "description": "需要有效、完整且未过复核期的投资政策。",
        }

    evidence_cutoffs = [
        str(item.get("run_completed_at") or item.get("run_as_of_date") or "")
        for item in program_snapshots
    ]
    evidence_cutoff_at = (
        max(value for value in evidence_cutoffs if value)
        if any(evidence_cutoffs)
        else None
    )
    compact_holdings = sorted(
        [
            {
                "id": item.get("id"),
                "asset_type": item.get("asset_type"),
                "market": item.get("market") or "",
                "code": item.get("code") or "",
                "amount": _round(item.get("amount"), 2),
                "updated_at": item.get("updated_at"),
            }
            for item in holdings
        ],
        key=lambda item: (
            str(item.get("asset_type") or ""),
            str(item.get("market") or ""),
            str(item.get("code") or ""),
            str(item.get("id") or ""),
        ),
    )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "alpha_forecast_engine_version": overview.get("engine_version"),
        "profile": {
            key: profile.get(key)
            for key in (
                "configured",
                "profile_version_id",
                "version_no",
                "payload_sha256",
                "horizon",
                "risk",
                "monthly_budget",
                "max_single_ratio",
                "max_equity_ratio",
                "max_industry_ratio",
                "allowed_fund_markets",
                "accept_fx_risk",
                "review_due_at",
            )
        }
        | {
            "governance_verified": (
                profile.get("governance_integrity") or {}
            ).get("verified")
        },
        "holdings_sha256": portfolio_exposure.holdings_sha256(
            holdings
        ),
        "holdings": compact_holdings,
        "programs": program_snapshots,
        "canonical_signal_records": canonical,
        "duplicate_exclusions": duplicate_exclusions,
        "engine_policy": {
            "horizon_weights": {
                asset_type: {
                    str(horizon): weight
                    for horizon, weight in sorted(weights.items())
                }
                for asset_type, weights in HORIZON_WEIGHTS.items()
            },
            "core_horizons": {
                key: sorted(value)
                for key, value in CORE_HORIZONS.items()
            },
            "sleeve_policy": SLEEVE_POLICY,
            "minimum_directional_edge": MIN_DIRECTIONAL_EDGE,
            "alpha_pilot_cap_pct": ALPHA_PILOT_CAP_PCT,
            "maximum_candidate_model_weight_pct": (
                MAX_CANDIDATE_MODEL_WEIGHT_PCT
            ),
            "breadth_caps_pct": {
                "one_candidate": 35,
                "two_candidates": 60,
                "three_or_more": 100,
            },
            "rebalance_drift_threshold_pct": (
                REBALANCE_DRIFT_THRESHOLD_PCT
            ),
            "allocation_parameter_search": False,
        },
    }
    evidence_sha256 = sha256_payload(evidence)
    drift = _route_drift(candidates, previous_result)
    result = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": _iso(current),
        "evidence_cutoff_at": evidence_cutoff_at,
        "evidence_sha256": evidence_sha256,
        "profile_version_id": (
            profile.get("profile_version_id")
            if profile_is_ready
            else None
        ),
        "status": status,
        "primary_action": primary_action,
        "summary": {
            "active_program_count": sum(
                item.get("status") == "active"
                for item in program_snapshots
            ),
            "eligible_program_count": eligible_program_count,
            "eligible_asset_count": eligible_record_count,
            "supportive_candidate_count": len(supportive),
            "allocated_candidate_count": len(candidates),
            "veto_count": len(vetoes),
            "watch_count": len(watchlist),
            "duplicate_program_exclusion_count": len(
                duplicate_exclusions
            ),
            "breadth_cap_pct": breadth_cap,
            "model_invested_pct": invested_pct,
            "model_cash_pct": cash_pct,
            "alpha_pilot_cap_pct_of_portfolio": ALPHA_PILOT_CAP_PCT,
        },
        "sleeves": {
            "policy_horizon": profile.get("horizon"),
            "core_target_pct": sleeve_policy["core_pct"],
            "satellite_target_pct": sleeve_policy["satellite_pct"],
            "core_allocated_pct": round(
                sum(
                    float(item["model_target_weight_pct"])
                    for item in candidates
                    if item["sleeve"] == "core"
                ),
                4,
            ),
            "satellite_allocated_pct": round(
                sum(
                    float(item["model_target_weight_pct"])
                    for item in candidates
                    if item["sleeve"] == "satellite"
                ),
                4,
            ),
            "cash_pct": cash_pct,
        },
        "gates": gates,
        "candidates": candidates,
        "vetoes": vetoes,
        "watchlist": watchlist,
        "programs": program_snapshots,
        "duplicate_exclusions": duplicate_exclusions,
        "drift": drift,
        "methodology": {
            "signal_target": (
                "各预测自身冻结基准胜率之上的概率边际，不与 50% 硬比较"
            ),
            "reliability_shrinkage": (
                "只使用真实前瞻样本量、运行批次、资产覆盖、Brier Skill、"
                "ECE 与逐批稳定率的固定权重收缩；不搜索参数"
            ),
            "consensus": (
                "至少两个合格周期同向且原始概率边际达到 5 个百分点；"
                "正负周期并存时弃权"
            ),
            "model_family_independence": (
                "同资产的同模型族重复项目只保留一份规范证据，不能重复投票；"
                "方向不一致时升级为冲突否决"
            ),
            "portfolio_construction": (
                "按投资期限分配核心/卫星预算，按收缩后边际排序，执行固定广度、"
                "单候选和现金上限；未分配部分不强制再分配"
            ),
            "drift": (
                "候选加现金计算单边换手；达到 10% 或发生进出名单只触发人工复核"
            ),
        },
        "boundaries": {
            "execution_authorized": False,
            "automatic_order_creation": False,
            "short_selling": False,
            "leverage": False,
            "return_guaranteed": False,
            "new_fund_purchase_authorized": False,
            "fund_bridge": (
                "新基金只进入尽调清单；只有已持有且穿透暴露仍有效的基金，"
                "才可能在全组合资金引擎中获得追加研究额度"
            ),
            "notice": (
                "模型权重不是订单或收益预测。冻结路线后，全组合引擎仍会按"
                "投资政策、持仓行动、风险容量、压力测试和现金核对再次缩减或否决。"
            ),
        },
    }
    return result, evidence


def _load_inputs(
    *,
    tenant_id: str,
    user_id: str,
    overview: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    overview_loader: Callable[[], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    loaded_overview = overview or (
        overview_loader()
        if overview_loader
        else alpha_forecast_service.overview(
            tenant_id=tenant_id,
            user_id=user_id,
            limit=100,
        )
    )
    loaded_profile = (
        profile
        if profile is not None
        else storage.get_investment_profile(user_id=user_id)
    )
    loaded_holdings = (
        holdings
        if holdings is not None
        else storage.list_holdings(user_id=user_id)
    )
    return loaded_overview, loaded_profile, loaded_holdings


def current_alpha_capital_route(
    *,
    tenant_id: str,
    user_id: str,
    now: dt.datetime | None = None,
    repo: AlphaCapitalRepository = mandate_repository,
    overview: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    overview_loader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    loaded_overview, loaded_profile, loaded_holdings = _load_inputs(
        tenant_id=tenant_id,
        user_id=user_id,
        overview=overview,
        profile=profile,
        holdings=holdings,
        overview_loader=overview_loader,
    )
    latest = repo.latest_mandate(
        tenant_id=tenant_id, user_id=user_id
    )
    result, _evidence = compose_alpha_capital_route(
        overview=loaded_overview,
        profile=loaded_profile,
        holdings=loaded_holdings,
        previous_result=(latest or {}).get("result"),
        now=now,
    )
    binding_current = bool(
        latest
        and (latest.get("integrity") or {}).get("verified")
        and latest.get("engine_version") == ENGINE_VERSION
        and latest.get("evidence_sha256") == result["evidence_sha256"]
        and latest.get("profile_version_id")
        == result.get("profile_version_id")
    )
    return {
        **result,
        "persistence": {
            "latest_mandate": (
                {
                    key: latest.get(key)
                    for key in (
                        "id",
                        "status",
                        "profile_version_id",
                        "evidence_cutoff_at",
                        "evidence_sha256",
                        "result_sha256",
                        "created_at",
                    )
                }
                if latest
                else None
            ),
            "binding_current": binding_current,
            "current_mandate_eligible_for_capital": bool(
                binding_current
                and latest.get("status") in {"paper_ready", "abstained"}
            )
            if latest
            else False,
        },
    }


def freeze_alpha_capital_route(
    *,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    expected_evidence_sha256: str,
    now: dt.datetime | None = None,
    repo: AlphaCapitalRepository = mandate_repository,
    overview: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    overview_loader: Callable[[], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool]:
    loaded_overview, loaded_profile, loaded_holdings = _load_inputs(
        tenant_id=tenant_id,
        user_id=user_id,
        overview=overview,
        profile=profile,
        holdings=holdings,
        overview_loader=overview_loader,
    )
    previous = repo.latest_mandate(
        tenant_id=tenant_id, user_id=user_id
    )
    result, evidence = compose_alpha_capital_route(
        overview=loaded_overview,
        profile=loaded_profile,
        holdings=loaded_holdings,
        previous_result=(previous or {}).get("result"),
        now=now,
    )
    if result["evidence_sha256"] != str(expected_evidence_sha256):
        raise AlphaCapitalConflict(
            "Alpha 证据已变化，请刷新后重新确认资金路线"
        )
    if result["status"] not in {"paper_ready", "abstained"}:
        raise AlphaCapitalConflict(
            "只有可研究路线或现金/否决路线可以冻结；请先补齐前瞻证据和投资政策"
        )
    return repo.create_mandate(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        engine_version=ENGINE_VERSION,
        status=result["status"],
        profile_version_id=result.get("profile_version_id"),
        evidence_cutoff_at=result.get("evidence_cutoff_at"),
        evidence=evidence,
        result=result,
    )


def current_verified_mandate(
    *,
    tenant_id: str,
    user_id: str,
    now: dt.datetime | None = None,
    repo: AlphaCapitalRepository = mandate_repository,
    overview: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    overview_loader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_route = current_alpha_capital_route(
        tenant_id=tenant_id,
        user_id=user_id,
        now=now,
        repo=repo,
        overview=overview,
        profile=profile,
        holdings=holdings,
        overview_loader=overview_loader,
    )
    latest_ref = (current_route.get("persistence") or {}).get(
        "latest_mandate"
    )
    current = bool(
        (current_route.get("persistence") or {}).get("binding_current")
    )
    latest = (
        repo.get_mandate(
            str(latest_ref["id"]),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if latest_ref
        else None
    )
    eligible = bool(
        current
        and latest
        and latest.get("status") in {"paper_ready", "abstained"}
        and (latest.get("integrity") or {}).get("verified")
    )
    return {
        "schema_version": "alpha_capital_current_mandate.v1",
        "current": current,
        "capital_eligible": eligible,
        "mandate": latest if eligible else None,
        "latest_mandate": latest_ref,
        "current_evidence_sha256": current_route.get(
            "evidence_sha256"
        ),
        "reason": (
            None
            if eligible
            else "alpha_capital_mandate_missing_or_not_current"
        ),
    }


def list_mandates(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 30,
    repo: AlphaCapitalRepository = mandate_repository,
) -> list[dict[str, Any]]:
    return repo.list_mandates(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
