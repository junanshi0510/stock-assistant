# -*- coding: utf-8 -*-
"""Orchestrate fail-closed fund replacement pre-trade reviews.

The service binds real platform cashflow, the active investment policy, the
user's holding thesis, current holdings, fund market metadata, and periodic
portfolio disclosures. It never places or authorizes an order.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fund_switch_quote_service
import funds
import holding_thesis
import portfolio_exposure
import storage
from investment_policy import payload_sha256
from strategies.fund_switch_execution import evaluate_fund_switch_execution


class HoldingNotFoundError(LookupError):
    pass


class QuoteNotFoundError(LookupError):
    pass


class ExecutionReviewConflictError(RuntimeError):
    pass


class ExecutionReviewValidationError(ValueError):
    pass


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _holding(holding_id: int, user_id: str) -> tuple[dict, list[dict]]:
    holdings = storage.list_holdings(user_id=user_id)
    item = next(
        (row for row in holdings if int(row.get("id") or 0) == int(holding_id)),
        None,
    )
    if item is None:
        raise HoldingNotFoundError("持仓不存在或不属于当前用户")
    if item.get("asset_type") != "fund":
        raise ExecutionReviewValidationError("只有基金持仓可以进行换仓执行前审查")
    return item, holdings


def _current_thesis(holding: dict, holdings: list[dict], user_id: str) -> dict | None:
    relevant = holding_thesis.theses_for_holdings(
        holding_thesis.latest_theses(user_id=user_id),
        holdings,
    )
    return next(
        (
            item
            for item in relevant
            if int((item.get("payload") or {}).get("holding_id") or 0)
            == int(holding.get("id") or 0)
        ),
        None,
    )


def _project_holdings(
    holdings: list[dict],
    *,
    selected_holding_id: int,
    candidate_code: str,
    candidate_name: str,
    candidate_net_amount: float,
    generated_at: str,
) -> list[dict]:
    projected = [
        copy.deepcopy(item)
        for item in holdings
        if int(item.get("id") or 0) != int(selected_holding_id)
    ]
    candidate = next(
        (
            item
            for item in projected
            if item.get("asset_type") == "fund"
            and str(item.get("code") or "") == candidate_code
        ),
        None,
    )
    if candidate is not None:
        existing = _number(candidate.get("amount"))
        candidate["amount"] = (
            round(existing + candidate_net_amount, 2)
            if existing is not None and existing >= 0
            else None
        )
        candidate["updated_at"] = generated_at
    else:
        projected.append({
            "id": f"projected:{candidate_code}",
            "asset_type": "fund",
            "market": "",
            "code": candidate_code,
            "name": candidate_name or candidate_code,
            "amount": round(candidate_net_amount, 2),
            "updated_at": generated_at,
            "source": "fund_switch_execution_projection",
        })
    return projected


def _load_disclosures(
    projected_holdings: list[dict],
) -> tuple[dict[str, dict], list[dict], dict[str, str]]:
    codes = sorted({
        str(item.get("code") or "").strip()
        for item in projected_holdings
        if item.get("asset_type") == "fund"
        and (_number(item.get("amount")) or 0) > 0
        and re.fullmatch(r"\d{6}", str(item.get("code") or "").strip())
    })
    if len(codes) > portfolio_exposure.MAX_FUND_SOURCES:
        raise ExecutionReviewValidationError(
            f"单次执行审查最多读取 {portfolio_exposure.MAX_FUND_SOURCES} 只基金披露"
        )

    def load(code: str) -> tuple[str, dict | None, str | None]:
        try:
            value = funds.get_fund_portfolio(code)
            if not isinstance(value, dict):
                raise TypeError("基金披露工具返回格式异常")
            normalized = copy.deepcopy(value)
            normalized["code"] = code
            return code, normalized, None
        except Exception as error:
            return code, None, str(error)[:240]

    sources: dict[str, dict] = {}
    failed: list[dict] = []
    if codes:
        with ThreadPoolExecutor(
            max_workers=min(4, len(codes)),
            thread_name_prefix="fund-switch-exposure",
        ) as pool:
            for code, value, error in pool.map(load, codes):
                if value is not None:
                    sources[code] = value
                else:
                    failed.append({"code": code, "error": error or "真实披露不可用"})
    hashes = {code: payload_sha256(value) for code, value in sources.items()}
    return sources, failed, hashes


def _market_profile(candidate_code: str) -> dict:
    try:
        value = funds.get_fund_market_profile(candidate_code)
        if not isinstance(value, dict):
            raise TypeError("基金市场工具返回格式异常")
        return value
    except Exception as error:
        return {
            "resolution_status": "unavailable",
            "code": candidate_code,
            "market": {
                "primary": "unknown",
                "label": "真实基金市场元数据不可用",
                "required_permissions": [],
                "currency_risk": None,
            },
            "valuation": {"confirmed_nav_lag": None},
            "source": "东方财富基金代码搜索库 + 东方财富基金详情页",
            "error": str(error)[:240],
        }


def create_execution_review(
    holding_id: int,
    candidate_code: str,
    request: dict[str, Any],
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict:
    candidate_code = str(candidate_code or "").strip()
    if not re.fullmatch(r"\d{6}", candidate_code):
        raise ExecutionReviewValidationError("候选基金代码必须为 6 位数字")
    current = now or _utc_now()
    if current.tzinfo is None:
        raise ExecutionReviewValidationError("服务时间必须包含时区")
    current = current.astimezone(dt.timezone.utc)
    generated_at = current.isoformat(timespec="seconds")
    holding, holdings = _holding(holding_id, user_id)
    latest_quote = fund_switch_quote_service.get_latest_quote(
        holding_id,
        candidate_code,
        user_id=user_id,
        now=current,
    )
    if latest_quote is None:
        raise QuoteNotFoundError("尚未保存该候选的真实平台报价")
    expected_quote_id = str(request.get("expected_quote_event_id") or "").strip()
    expected_quote_hash = str(request.get("expected_quote_event_hash") or "").strip()
    actual_quote_hash = str((latest_quote.get("integrity") or {}).get("event_hash") or "")
    if latest_quote.get("id") != expected_quote_id or actual_quote_hash != expected_quote_hash:
        raise ExecutionReviewConflictError("平台报价已变化，请刷新后重新进行执行审查")
    if (
        latest_quote.get("status") != "confirmed_current"
        or not (latest_quote.get("integrity") or {}).get("verified")
        or not ((latest_quote.get("payload") or {}).get("decision_gate") or {}).get(
            "executable_switch_cost_confirmed"
        )
    ):
        raise ExecutionReviewConflictError("平台报价已过期、失效或尚未通过真实现金流门禁")

    quote_event = storage.get_fund_switch_quote_event(expected_quote_id, user_id=user_id)
    if quote_event is None or not quote_event.get("integrity_verified"):
        raise ExecutionReviewConflictError("平台报价原始事件不存在或完整性校验失败")
    cost_review = storage.get_fund_switch_cost_review(
        str(latest_quote.get("review_id") or ""),
        user_id=user_id,
    )
    if cost_review is None or not cost_review.get("integrity_verified"):
        raise ExecutionReviewConflictError("报价绑定的成本快照不存在或完整性校验失败")

    profile = storage.get_investment_profile(user_id=user_id)
    thesis = _current_thesis(holding, holdings, user_id)
    market_profile = _market_profile(candidate_code)
    quote_cashflow = (latest_quote.get("payload") or {}).get("cashflow") or {}
    candidate_net = _number(quote_cashflow.get("candidate_net_asset_amount_yuan"))
    if candidate_net is None or candidate_net <= 0:
        raise ExecutionReviewConflictError("平台报价缺少可验证的候选净投资金额")
    candidate_name = str((cost_review.get("payload") or {}).get("candidate_name") or candidate_code)
    projected_holdings = _project_holdings(
        holdings,
        selected_holding_id=int(holding_id),
        candidate_code=candidate_code,
        candidate_name=candidate_name,
        candidate_net_amount=candidate_net,
        generated_at=generated_at,
    )
    projected_holdings_hash = portfolio_exposure.holdings_sha256(projected_holdings)
    raw_sources, failed_sources, disclosure_hashes = _load_disclosures(projected_holdings)
    max_age_days = int(os.environ.get(
        "PORTFOLIO_EXPOSURE_MAX_AGE_DAYS",
        portfolio_exposure.DEFAULT_MAX_AGE_DAYS,
    ))
    projected_exposure = portfolio_exposure.build_exposure_snapshot(
        projected_holdings,
        raw_sources,
        target_code=candidate_code,
        failed_sources=failed_sources,
        profile_version_id=profile.get("profile_version_id"),
        observed_on=current.date(),
        max_age_days=max_age_days,
    )
    bindings = {
        "quote_event_id": expected_quote_id,
        "quote_event_hash": expected_quote_hash,
        "quote_event_payload_sha256": quote_event.get("payload_sha256"),
        "cost_review_id": cost_review.get("id"),
        "cost_review_payload_sha256": cost_review.get("payload_sha256"),
        "cost_review_evidence_sha256": cost_review.get("evidence_sha256"),
        "profile_version_id": str(profile.get("profile_version_id") or ""),
        "profile_payload_sha256": str(profile.get("payload_sha256") or ""),
        "thesis_version_id": str((thesis or {}).get("id") or ""),
        "thesis_payload_sha256": str((thesis or {}).get("payload_sha256") or ""),
        "current_holdings_sha256": portfolio_exposure.holdings_sha256(holdings),
        "projected_holdings_sha256": projected_holdings_hash,
        "market_profile_sha256": payload_sha256(market_profile),
        "projected_exposure_sha256": payload_sha256(projected_exposure),
        "fund_disclosure_sha256": disclosure_hashes,
    }
    payload = evaluate_fund_switch_execution(
        holding=holding,
        cost_review=cost_review,
        quote=latest_quote,
        profile=profile,
        thesis=thesis,
        market_profile=market_profile,
        projected_holdings=projected_holdings,
        projected_exposure=projected_exposure,
        bindings=bindings,
        acknowledged_holding_thesis=bool(request.get("acknowledged_holding_thesis")),
        generated_at=generated_at,
    )
    saved = storage.append_fund_switch_execution_review(
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return decorate_execution_review(saved, user_id=user_id, now=current)


def _binding_state(review: dict, *, user_id: str, now: dt.datetime) -> dict:
    payload = review.get("payload") or {}
    bindings = payload.get("bindings") or {}
    holding, holdings = _holding(int(review.get("holding_id") or 0), user_id)
    latest_quote = fund_switch_quote_service.get_latest_quote(
        int(review.get("holding_id") or 0),
        str(review.get("candidate_code") or ""),
        user_id=user_id,
        now=now,
    )
    current_profile = storage.get_investment_profile(user_id=user_id)
    thesis = _current_thesis(holding, holdings, user_id)
    expected_profile_id = str(bindings.get("profile_version_id") or "")
    expected_thesis_id = str(bindings.get("thesis_version_id") or "")
    profile_current = (
        bool(
            str(current_profile.get("profile_version_id") or "") == expected_profile_id
            and str(current_profile.get("payload_sha256") or "")
            == str(bindings.get("profile_payload_sha256") or "")
            and current_profile.get("configured")
        )
        if expected_profile_id
        else not current_profile.get("profile_version_id")
    )
    thesis_current = (
        bool(
            str((thesis or {}).get("id") or "") == expected_thesis_id
            and str((thesis or {}).get("payload_sha256") or "")
            == str(bindings.get("thesis_payload_sha256") or "")
            and (thesis or {}).get("integrity_verified")
        )
        if expected_thesis_id
        else thesis is None
    )
    return {
        "quote_current": bool(
            latest_quote
            and latest_quote.get("id") == bindings.get("quote_event_id")
            and (latest_quote.get("integrity") or {}).get("event_hash")
            == bindings.get("quote_event_hash")
            and latest_quote.get("status") == "confirmed_current"
            and (latest_quote.get("integrity") or {}).get("verified")
        ),
        "quote_status": (latest_quote or {}).get("status"),
        "profile_current": profile_current,
        "thesis_current": thesis_current,
        "holdings_current": (
            portfolio_exposure.holdings_sha256(holdings)
            == bindings.get("current_holdings_sha256")
        ),
    }


def decorate_execution_review(
    review: dict,
    *,
    user_id: str = "default",
    now: dt.datetime | None = None,
) -> dict:
    current = now or _utc_now()
    current = current.astimezone(dt.timezone.utc)
    payload = copy.deepcopy(review.get("payload") or {})
    audit = storage.verify_fund_switch_execution_audit(
        int(review.get("holding_id") or 0),
        str(review.get("candidate_code") or ""),
        user_id=user_id,
    )
    try:
        bindings = _binding_state(review, user_id=user_id, now=current)
    except HoldingNotFoundError:
        bindings = {
            "quote_current": False,
            "quote_status": "holding_removed",
            "profile_current": False,
            "thesis_current": False,
            "holdings_current": False,
        }
    integrity_verified = bool(review.get("integrity_verified") and audit.get("verified"))
    current_bindings = bool(
        bindings.get("quote_current")
        and bindings.get("profile_current")
        and bindings.get("thesis_current")
        and bindings.get("holdings_current")
    )
    if not integrity_verified:
        status = "integrity_failed"
        reason = audit.get("reason") or "execution_review_integrity_failed"
    elif bindings.get("quote_status") == "expired":
        status = "expired"
        reason = "platform_quote_expired"
    elif not current_bindings:
        status = "superseded"
        reason = next(
            (
                key
                for key in ("quote_current", "profile_current", "thesis_current", "holdings_current")
                if not bindings.get(key)
            ),
            "execution_review_binding_changed",
        )
    else:
        status = str(review.get("status") or payload.get("status") or "unavailable")
        reason = str(((payload.get("decision_gate") or {}).get("reason")) or status)
    stored_gate = payload.get("decision_gate") or {}
    redemption_ready = bool(
        status == "ready_for_redemption_review"
        and current_bindings
        and integrity_verified
        and stored_gate.get("redemption_review_ready")
    )
    payload["decision_gate"] = {
        **stored_gate,
        "redemption_review_ready": redemption_ready,
        "candidate_purchase_ready": False,
        "full_switch_execution_ready": False,
        "execution_authorized": False,
        "automatic_redemption_allowed": False,
        "automatic_purchase_allowed": False,
        "reason": reason,
    }
    return {
        "id": review.get("id"),
        "revision": review.get("sequence_no"),
        "holding_id": review.get("holding_id"),
        "selected_code": review.get("selected_code"),
        "candidate_code": review.get("candidate_code"),
        "status": status,
        "payload": payload,
        "integrity": {
            "verified": integrity_verified,
            "audit_chain_verified": bool(audit.get("verified")),
            "current_bindings": current_bindings,
            **bindings,
            "review_hash": review.get("review_hash"),
            "previous_hash": review.get("previous_hash"),
        },
        "created_at": review.get("created_at"),
    }


def get_latest_execution_review(
    holding_id: int,
    candidate_code: str,
    *,
    user_id: str = "default",
    now: dt.datetime | None = None,
) -> dict | None:
    rows = storage.list_fund_switch_execution_reviews(
        holding_id=int(holding_id),
        candidate_code=str(candidate_code),
        user_id=user_id,
        limit=1,
    )
    if not rows:
        return None
    return decorate_execution_review(rows[0], user_id=user_id, now=now)


def agent_execution_summary(
    user_id: str,
    *,
    target_code: str = "",
    now: dt.datetime | None = None,
) -> dict:
    holding_ids = {
        int(item.get("id") or 0)
        for item in storage.list_holdings(user_id=user_id)
        if item.get("asset_type") == "fund"
    }
    rows = [
        row
        for row in storage.list_latest_fund_switch_execution_reviews(user_id=user_id)
        if int(row.get("holding_id") or 0) in holding_ids
    ]
    if target_code:
        rows = [row for row in rows if str(row.get("selected_code") or "") == target_code]
    items = [decorate_execution_review(row, user_id=user_id, now=now) for row in rows[:12]]
    return {
        "status": "available" if items else "not_recorded",
        "count": len(items),
        "items": [
            {
                "selected_code": item.get("selected_code"),
                "candidate_code": item.get("candidate_code"),
                "status": item.get("status"),
                "generated_at": (item.get("payload") or {}).get("generated_at"),
                "blockers": (item.get("payload") or {}).get("blockers") or [],
                "cashflow": (item.get("payload") or {}).get("cashflow") or {},
                "position_projection": (item.get("payload") or {}).get("position_projection") or {},
                "portfolio_projection": {
                    key: (((item.get("payload") or {}).get("portfolio_projection") or {}).get(key))
                    for key in (
                        "status",
                        "evaluated_on",
                        "equity_upper_ratio_pct",
                        "max_equity_ratio_pct",
                        "industry_max_upper_ratio_pct",
                        "max_industry_ratio_pct",
                        "quality_reasons",
                        "failed_sources",
                    )
                },
                "redemption_review_ready": (
                    ((item.get("payload") or {}).get("decision_gate") or {}).get(
                        "redemption_review_ready"
                    )
                ),
                "candidate_purchase_ready": False,
                "execution_authorized": False,
                "integrity_verified": (item.get("integrity") or {}).get("verified"),
                "current_bindings": (item.get("integrity") or {}).get("current_bindings"),
            }
            for item in items
        ],
        "policy": (
            "只允许模型引用当前、完整且绑定真实证据的赎回复核状态；"
            "候选申购始终等待真实到账和新报价，任何审查都不是下单授权。"
        ),
    }
