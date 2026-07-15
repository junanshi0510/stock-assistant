# -*- coding: utf-8 -*-
"""Validate and audit user-confirmed sales-platform fund switch quotes."""

from __future__ import annotations

import copy
import datetime as dt
import math
from typing import Any

import portfolio_review
import storage
from strategies.fund_switch_cost import build_lot_binding


QUOTE_SCHEMA_VERSION = "fund_switch_platform_quote.v1"
QUOTE_VALID_HOURS = 24
MAX_FUTURE_SKEW_MINUTES = 5
MAX_SETTLEMENT_DAYS = 30
MATERIAL_VARIANCE_RATE = 0.002
MATERIAL_VARIANCE_MIN_YUAN = 10.0
CHINA_TIMEZONE = dt.timezone(dt.timedelta(hours=8))


class HoldingNotFoundError(LookupError):
    pass


class CostReviewNotFoundError(LookupError):
    pass


class CostReviewConflictError(RuntimeError):
    pass


class QuoteValidationError(ValueError):
    pass


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo is not None else None


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _holding(holding_id: int, user_id: str) -> dict:
    item = next(
        (
            row
            for row in storage.list_holdings(user_id=user_id)
            if int(row.get("id") or 0) == int(holding_id)
        ),
        None,
    )
    if item is None:
        raise HoldingNotFoundError("持仓不存在或不属于当前用户")
    if item.get("asset_type") != "fund":
        raise QuoteValidationError("只有基金持仓可以确认替换报价")
    return item


def _disclosed_cost_range(review_payload: dict) -> tuple[float | None, float | None]:
    snapshots = review_payload.get("cost_snapshots") or {}
    values = []
    for key in ("page_promotional", "standard_disclosed"):
        value = _number((snapshots.get(key) or {}).get("total_switching_cost_yuan"))
        if value is not None and value >= 0:
            values.append(value)
    return (min(values), max(values)) if values else (None, None)


def _coverage_months(cost_rate_pct: float, annual_excess_pp: float | None) -> float | None:
    if annual_excess_pp is None or annual_excess_pp <= 0:
        return None
    return cost_rate_pct / annual_excess_pp * 12


def _current_portfolio_binding(review: dict, user_id: str) -> dict:
    payload = review.get("payload") or {}
    holding_id = int(review.get("holding_id") or 0)
    selected_code = str(review.get("selected_code") or "")
    holding = next(
        (
            item
            for item in storage.list_holdings(user_id=user_id)
            if int(item.get("id") or 0) == holding_id
        ),
        None,
    )
    if holding is None:
        return {"current": False, "reason": "holding_removed", "payload_sha256": None}
    if (
        holding.get("asset_type") != "fund"
        or str(holding.get("code") or "") != selected_code
    ):
        return {"current": False, "reason": "holding_identity_changed", "payload_sha256": None}
    lot_snapshot = portfolio_review.remaining_lot_snapshot(
        "fund",
        selected_code,
        user_id=user_id,
    )
    current = build_lot_binding(lot_snapshot, holding.get("shares"))
    expected = payload.get("ledger_binding") or {}
    matches = bool(
        expected.get("schema_version") == current.get("schema_version")
        and expected.get("payload_sha256") == current.get("payload_sha256")
    )
    return {
        "current": matches,
        "reason": None if matches else "portfolio_ledger_changed",
        "payload_sha256": current.get("payload_sha256"),
        "expected_payload_sha256": expected.get("payload_sha256"),
    }


def submit_fund_switch_quote(
    holding_id: int,
    request: dict[str, Any],
    *,
    user_id: str = "default",
    actor_id: str = "default",
    now: dt.datetime | None = None,
) -> dict:
    """Persist one user-observed quote after all freshness and variance gates."""
    current = now or _utc_now()
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    current = current.astimezone(dt.timezone.utc)
    holding = _holding(holding_id, user_id)
    review_id = str(request.get("review_id") or "").strip()
    expected_hash = str(request.get("expected_review_payload_sha256") or "").strip()
    review = storage.get_fund_switch_cost_review(review_id, user_id=user_id)
    if review is None:
        raise CostReviewNotFoundError("成本快照不存在或不属于当前用户")
    integrity = storage.verify_fund_switch_cost_review(review_id, user_id=user_id)
    if not integrity.get("verified"):
        raise CostReviewConflictError("成本快照完整性校验失败，请重新读取真实候选")
    if int(review.get("holding_id") or 0) != int(holding_id):
        raise CostReviewConflictError("成本快照与当前持仓不匹配")
    if str(review.get("payload_sha256") or "") != expected_hash:
        raise CostReviewConflictError("页面成本快照已经变化，请刷新后重新核对报价")
    review_payload = review.get("payload") or {}
    if str(review_payload.get("selected_code") or "") != str(holding.get("code") or ""):
        raise CostReviewConflictError("持仓基金代码已变化，请重新核算")
    gate = review_payload.get("decision_gate") or {}
    if (
        review_payload.get("status") != "ready_for_platform_quote"
        or not gate.get("eligible_for_platform_quote_confirmation")
        or not gate.get("cost_snapshot_complete")
    ):
        raise CostReviewConflictError("该成本快照尚未通过平台报价确认门禁")
    portfolio_binding = _current_portfolio_binding(review, user_id)
    if not portfolio_binding.get("current"):
        raise CostReviewConflictError("持仓份额或 FIFO 交易账本已变化，请重新核算")

    platform_name = str(request.get("platform_name") or "").strip()
    if len(platform_name) < 2 or len(platform_name) > 80:
        raise QuoteValidationError("销售平台名称需为 2 至 80 个字符")
    if not bool(request.get("acknowledged_platform_quote")):
        raise QuoteValidationError("必须确认费用来自销售平台本次真实报价")
    quoted_at = _datetime(request.get("quoted_at"))
    if quoted_at is None:
        raise QuoteValidationError("平台报价时间必须包含时区")
    quoted_utc = quoted_at.astimezone(dt.timezone.utc)
    age = current - quoted_utc
    if age < -dt.timedelta(minutes=MAX_FUTURE_SKEW_MINUTES):
        raise QuoteValidationError("平台报价时间不能晚于服务器当前时间")
    if age > dt.timedelta(hours=QUOTE_VALID_HOURS):
        raise QuoteValidationError("平台报价已超过 24 小时，请重新获取真实报价")
    review_on = _date(review_payload.get("review_on"))
    platform_quote_date = quoted_at.astimezone(CHINA_TIMEZONE).date()
    if review_on is None or review_on != platform_quote_date:
        raise CostReviewConflictError("披露成本核算日与平台报价日期不同，请重新核算")

    arrival_date = _date(request.get("expected_redemption_arrival_date"))
    if arrival_date is None:
        raise QuoteValidationError("必须填写平台显示的预计赎回到账日期")
    settlement_days = (arrival_date - platform_quote_date).days
    if settlement_days < 0:
        raise QuoteValidationError("预计到账日期不能早于报价日期")
    if settlement_days > MAX_SETTLEMENT_DAYS:
        raise QuoteValidationError("预计到账时间超过 30 天，请核对平台信息")

    redemption_fee = _number(request.get("redemption_fee_yuan"))
    entry_fee = _number(request.get("candidate_entry_fee_yuan"))
    if redemption_fee is None or entry_fee is None or redemption_fee < 0 or entry_fee < 0:
        raise QuoteValidationError("赎回费和候选申购费必须是非负有限金额")
    gross_value = _number(
        ((review_payload.get("redemption") or {}).get("gross_value_yuan"))
    )
    if gross_value is None or gross_value <= 0:
        raise CostReviewConflictError("成本快照缺少可校验的赎回总额")
    total_cost = redemption_fee + entry_fee
    if redemption_fee > gross_value or entry_fee > gross_value or total_cost > gross_value:
        raise QuoteValidationError("平台费用不能超过本次赎回总额")
    cost_rate = total_cost / gross_value * 100

    disclosed_low, disclosed_high = _disclosed_cost_range(review_payload)
    if disclosed_low is None or disclosed_high is None:
        raise CostReviewConflictError("成本快照缺少披露费用区间")
    if total_cost < disclosed_low:
        variance_direction = "below_disclosed_range"
        variance_amount = disclosed_low - total_cost
    elif total_cost > disclosed_high:
        variance_direction = "above_disclosed_range"
        variance_amount = total_cost - disclosed_high
    else:
        variance_direction = "within_disclosed_range"
        variance_amount = 0.0
    tolerance = max(MATERIAL_VARIANCE_MIN_YUAN, gross_value * MATERIAL_VARIANCE_RATE)
    material_variance = variance_amount > tolerance
    variance_acknowledged = bool(request.get("acknowledged_fee_variance"))
    if material_variance and not variance_acknowledged:
        raise QuoteValidationError(
            "平台报价明显超出披露成本区间，确认差异后才能保存"
        )

    annual_excess = _number(
        ((review_payload.get("historical_cost_hurdle") or {}).get(
            "rolling_12m_median_excess_pp"
        ))
    )
    candidate_available = bool(request.get("candidate_purchase_available"))
    executable_cost_confirmed = bool(
        candidate_available
        and (not material_variance or variance_acknowledged)
    )
    quote_expires_at = quoted_utc + dt.timedelta(hours=QUOTE_VALID_HOURS)
    payload = {
        "schema_version": QUOTE_SCHEMA_VERSION,
        "review_id": review_id,
        "review_payload_sha256": expected_hash,
        "holding_id": int(holding_id),
        "selected_code": str(review.get("selected_code") or ""),
        "candidate_code": str(review.get("candidate_code") or ""),
        "platform_quote": {
            "platform_name": platform_name,
            "quoted_at": quoted_at.isoformat(timespec="seconds"),
            "quote_expires_at": quote_expires_at.isoformat(timespec="seconds"),
            "acknowledged_as_platform_quote": True,
            "source": "用户从销售平台本次交易确认页录入",
        },
        "confirmed_cost": {
            "pricing_status": "platform_pretrade_quote_not_final_settlement",
            "redemption_fee_yuan": _round(redemption_fee),
            "candidate_entry_fee_yuan": _round(entry_fee),
            "total_switching_cost_yuan": _round(total_cost),
            "total_switching_cost_rate_pct": _round(cost_rate, 4),
            "gross_value_yuan": _round(gross_value),
        },
        "disclosed_comparison": {
            "disclosed_low_yuan": _round(disclosed_low),
            "disclosed_high_yuan": _round(disclosed_high),
            "variance_direction": variance_direction,
            "variance_amount_yuan": _round(variance_amount),
            "material_variance_threshold_yuan": _round(tolerance),
            "material_variance": material_variance,
            "acknowledged_fee_variance": variance_acknowledged,
        },
        "settlement": {
            "expected_redemption_arrival_date": arrival_date.isoformat(),
            "cash_gap_days": settlement_days,
            "candidate_purchase_available": candidate_available,
        },
        "historical_cost_hurdle": {
            "rolling_12m_median_excess_pp": _round(annual_excess),
            "confirmed_cost_coverage_months": _round(
                _coverage_months(cost_rate, annual_excess),
                1,
            ),
            "method": "按历史滚动十二个月中位超额线性折算，仅衡量历史成本门槛，不预测回本。",
        },
        "decision_gate": {
            "platform_quote_confirmed": True,
            "quote_current": True,
            "cost_review_integrity_verified": True,
            "fee_variance_acknowledged_or_within_tolerance": bool(
                not material_variance or variance_acknowledged
            ),
            "settlement_date_confirmed": True,
            "candidate_purchase_available": candidate_available,
            "executable_switch_cost_confirmed": executable_cost_confirmed,
            "automatic_switch_allowed": False,
            "reason": (
                "cost_evidence_confirmed"
                if executable_cost_confirmed
                else "candidate_purchase_unavailable"
            ),
        },
        "note": str(request.get("note") or "").strip()[:300],
        "policy": "该记录确认销售平台本次提交前报价与预计到账证据，不冒充最终清算费用，也不构成收益承诺、换仓建议或自动交易指令。",
        "quoted_at": quoted_at.isoformat(timespec="seconds"),
    }
    event = storage.append_fund_switch_quote_event(
        review_id,
        payload,
        actor_id=actor_id,
        user_id=user_id,
    )
    return decorate_quote_event(event, user_id=user_id, now=current)


def decorate_quote_event(
    event: dict,
    *,
    user_id: str,
    now: dt.datetime | None = None,
    audit: dict | None = None,
    expected_review_id: str | None = None,
    expected_review_payload_sha256: str | None = None,
) -> dict:
    current = (now or _utc_now()).astimezone(dt.timezone.utc)
    payload = copy.deepcopy(event.get("payload") or {})
    quote = payload.get("platform_quote") or {}
    expires_at = _datetime(quote.get("quote_expires_at"))
    quoted_at = _datetime(quote.get("quoted_at"))
    quote_current = bool(
        quoted_at is not None
        and expires_at is not None
        and quoted_at.astimezone(dt.timezone.utc)
        <= current + dt.timedelta(minutes=MAX_FUTURE_SKEW_MINUTES)
        and current <= expires_at.astimezone(dt.timezone.utc)
    )
    review_id = str(event.get("review_id") or "")
    review = storage.get_fund_switch_cost_review(review_id, user_id=user_id)
    review_integrity = storage.verify_fund_switch_cost_review(
        review_id,
        user_id=user_id,
    )
    portfolio_binding = (
        _current_portfolio_binding(review, user_id)
        if review is not None and review_integrity.get("verified")
        else {"current": False, "reason": "cost_review_unavailable", "payload_sha256": None}
    )
    expected_review_current = bool(
        (expected_review_id is None or review_id == str(expected_review_id))
        and (
            expected_review_payload_sha256 is None
            or str((event.get("payload") or {}).get("review_payload_sha256") or "")
            == str(expected_review_payload_sha256)
        )
    )
    if audit is None:
        audit = storage.verify_fund_switch_quote_audit(
            int(event.get("holding_id") or 0),
            str(event.get("candidate_code") or ""),
            user_id=user_id,
        )
    integrity_verified = bool(
        event.get("integrity_verified")
        and review_integrity.get("verified")
        and audit.get("verified")
    )
    stored_gate = payload.get("decision_gate") or {}
    executable = bool(
        integrity_verified
        and quote_current
        and portfolio_binding.get("current")
        and expected_review_current
        and stored_gate.get("executable_switch_cost_confirmed")
    )
    if not integrity_verified:
        status = "integrity_failed"
        reason = "quote_or_review_integrity_failed"
    elif not expected_review_current:
        status = "superseded"
        reason = "cost_review_refreshed"
    elif not portfolio_binding.get("current"):
        status = "superseded"
        reason = str(portfolio_binding.get("reason") or "portfolio_ledger_changed")
    elif not quote_current:
        status = "expired"
        reason = "platform_quote_expired"
    elif not stored_gate.get("candidate_purchase_available"):
        status = "confirmed_with_blocker"
        reason = "candidate_purchase_unavailable"
    else:
        status = "confirmed_current"
        reason = "cost_evidence_confirmed"
    payload["decision_gate"] = {
        **stored_gate,
        "quote_current": quote_current,
        "cost_review_integrity_verified": bool(review_integrity.get("verified")),
        "cost_review_current": bool(
            portfolio_binding.get("current") and expected_review_current
        ),
        "quote_audit_chain_verified": bool(audit.get("verified")),
        "executable_switch_cost_confirmed": executable,
        "automatic_switch_allowed": False,
        "reason": reason,
    }
    return {
        "id": event.get("id"),
        "revision": event.get("sequence_no"),
        "review_id": event.get("review_id"),
        "holding_id": event.get("holding_id"),
        "selected_code": event.get("selected_code"),
        "candidate_code": event.get("candidate_code"),
        "status": status,
        "payload": payload,
        "integrity": {
            "verified": integrity_verified,
            "event_payload_verified": bool(event.get("integrity_verified")),
            "cost_review_verified": bool(review_integrity.get("verified")),
            "audit_chain_verified": bool(audit.get("verified")),
            "portfolio_binding_current": bool(portfolio_binding.get("current")),
            "portfolio_binding_reason": portfolio_binding.get("reason"),
            "expected_review_current": expected_review_current,
            "event_hash": event.get("event_hash"),
            "previous_hash": event.get("previous_hash"),
        },
        "created_at": event.get("created_at"),
    }


def get_latest_quote(
    holding_id: int,
    candidate_code: str,
    *,
    user_id: str = "default",
    now: dt.datetime | None = None,
    expected_review_id: str | None = None,
    expected_review_payload_sha256: str | None = None,
) -> dict | None:
    rows = storage.list_fund_switch_quote_events(
        holding_id=int(holding_id),
        candidate_code=str(candidate_code),
        user_id=user_id,
        limit=1,
    )
    if not rows:
        return None
    audit = storage.verify_fund_switch_quote_audit(
        int(holding_id),
        str(candidate_code),
        user_id=user_id,
    )
    return decorate_quote_event(
        rows[0],
        user_id=user_id,
        now=now,
        audit=audit,
        expected_review_id=expected_review_id,
        expected_review_payload_sha256=expected_review_payload_sha256,
    )


def list_holding_quotes(
    holding_id: int,
    *,
    user_id: str = "default",
    now: dt.datetime | None = None,
) -> dict:
    holding = _holding(holding_id, user_id)
    rows = storage.list_latest_fund_switch_quotes(
        user_id=user_id,
        holding_id=int(holding_id),
    )
    items = [decorate_quote_event(item, user_id=user_id, now=now) for item in rows]
    return {
        "schema_version": "fund_switch_quote_list.v1",
        "holding_id": int(holding_id),
        "selected_code": str(holding.get("code") or ""),
        "items": items,
        "count": len(items),
        "current_count": sum(item.get("status") == "confirmed_current" for item in items),
        "policy": "仅展示用户确认的真实平台报价；过期报价自动失去成本执行资格。",
    }


def get_quote_audit(
    holding_id: int,
    candidate_code: str,
    *,
    user_id: str = "default",
) -> dict:
    _holding(holding_id, user_id)
    audit = storage.verify_fund_switch_quote_audit(
        int(holding_id),
        str(candidate_code),
        user_id=user_id,
    )
    events = storage.list_fund_switch_quote_events(
        holding_id=int(holding_id),
        candidate_code=str(candidate_code),
        user_id=user_id,
        limit=100,
    )
    return {
        **audit,
        "events": [
            {
                "id": item.get("id"),
                "revision": item.get("sequence_no"),
                "review_id": item.get("review_id"),
                "quoted_at": item.get("quoted_at"),
                "payload_sha256": item.get("payload_sha256"),
                "event_hash": item.get("event_hash"),
                "previous_hash": item.get("previous_hash"),
                "created_at": item.get("created_at"),
            }
            for item in events
        ],
    }


def agent_quote_summary(
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
        for row in storage.list_latest_fund_switch_quotes(user_id=user_id)
        if int(row.get("holding_id") or 0) in holding_ids
    ]
    if target_code:
        rows = [row for row in rows if str(row.get("selected_code") or "") == target_code]
    items = [decorate_quote_event(row, user_id=user_id, now=now) for row in rows[:12]]
    return {
        "status": "available" if items else "not_recorded",
        "count": len(items),
        "items": [
            {
                "selected_code": item.get("selected_code"),
                "candidate_code": item.get("candidate_code"),
                "status": item.get("status"),
                "quoted_at": ((item.get("payload") or {}).get("platform_quote") or {}).get("quoted_at"),
                "quote_expires_at": ((item.get("payload") or {}).get("platform_quote") or {}).get("quote_expires_at"),
                "total_switching_cost_yuan": ((item.get("payload") or {}).get("confirmed_cost") or {}).get("total_switching_cost_yuan"),
                "total_switching_cost_rate_pct": ((item.get("payload") or {}).get("confirmed_cost") or {}).get("total_switching_cost_rate_pct"),
                "cash_gap_days": ((item.get("payload") or {}).get("settlement") or {}).get("cash_gap_days"),
                "historical_cost_coverage_months": ((item.get("payload") or {}).get("historical_cost_hurdle") or {}).get("confirmed_cost_coverage_months"),
                "executable_switch_cost_confirmed": ((item.get("payload") or {}).get("decision_gate") or {}).get("executable_switch_cost_confirmed"),
                "integrity_verified": (item.get("integrity") or {}).get("verified"),
                "portfolio_binding_current": (item.get("integrity") or {}).get("portfolio_binding_current"),
            }
            for item in items
        ],
        "policy": "仅当报价当前有效、成本快照和审计链均通过时，模型才可把成本视为已确认；仍不得生成自动交易命令。",
    }
