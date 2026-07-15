# -*- coding: utf-8 -*-
"""Real-data application service for batch fund purchase preflight reviews."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import funds
import portfolio_exposure
import storage
from investment_policy import payload_sha256
from strategies.portfolio_batch_purchase_preflight import (
    MAX_CONFIRMATION_DAYS,
    MAX_FUTURE_SKEW_MINUTES,
    QUOTE_VALID_HOURS,
    evaluate_portfolio_batch_purchase_preflight,
)

from .repository import AgentRepository


class BatchPurchasePreflightValidationError(ValueError):
    pass


class BatchPurchasePreflightConflictError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _datetime(value: Any, label: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise BatchPurchasePreflightValidationError(f"{label}格式无效") from error
    if parsed.tzinfo is None:
        raise BatchPurchasePreflightValidationError(f"{label}必须包含时区")
    return parsed


def _date(value: Any, label: str) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as error:
        raise BatchPurchasePreflightValidationError(f"{label}格式无效") from error


def _validated_now(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise BatchPurchasePreflightValidationError("服务时间必须包含时区")
    return current.astimezone(dt.timezone.utc)


def _normalize_quotes(request: dict[str, Any], current: dt.datetime) -> list[dict[str, Any]]:
    raw_quotes = request.get("quotes") or []
    if not isinstance(raw_quotes, list) or not 1 <= len(raw_quotes) <= 8:
        raise BatchPurchasePreflightValidationError("执行前复核需要 1-8 条基金平台报价")
    if request.get("acknowledged_platform_quotes") is not True:
        raise BatchPurchasePreflightValidationError(
            "必须确认全部金额、费用、限购状态和确认日期来自销售平台本次申购页"
        )
    normalized = []
    codes = []
    for index, raw in enumerate(raw_quotes, start=1):
        code = str(raw.get("code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise BatchPurchasePreflightValidationError(f"第 {index} 条基金代码必须为 6 位数字")
        codes.append(code)
        platform_name = str(raw.get("platform_name") or "").strip()
        if not 2 <= len(platform_name) <= 80:
            raise BatchPurchasePreflightValidationError(
                f"{code} 的销售平台名称需为 2-80 个字符"
            )
        quoted_at = _datetime(raw.get("quoted_at"), f"{code} 平台报价时间")
        quoted_utc = quoted_at.astimezone(dt.timezone.utc)
        if quoted_utc > current + dt.timedelta(minutes=MAX_FUTURE_SKEW_MINUTES):
            raise BatchPurchasePreflightValidationError(f"{code} 平台报价时间晚于服务器时间")
        if current - quoted_utc > dt.timedelta(hours=QUOTE_VALID_HOURS):
            raise BatchPurchasePreflightValidationError(f"{code} 平台报价已超过 24 小时")
        order_amount = _number(raw.get("order_amount_yuan"))
        if order_amount is None or order_amount <= 0:
            raise BatchPurchasePreflightValidationError(f"{code} 拟申购金额必须大于 0")
        entry_fee = _number(raw.get("entry_fee_yuan"))
        if entry_fee is not None and (entry_fee < 0 or entry_fee >= order_amount):
            raise BatchPurchasePreflightValidationError(
                f"{code} 实际申购费必须大于等于 0 且小于拟申购金额"
            )
        purchase_status = str(raw.get("purchase_status") or "unknown")
        if purchase_status not in {"available", "limited", "unavailable", "unknown"}:
            raise BatchPurchasePreflightValidationError(f"{code} 当前申购状态无效")
        purchase_limit = _number(raw.get("purchase_limit_yuan"))
        if purchase_limit is not None and purchase_limit <= 0:
            raise BatchPurchasePreflightValidationError(f"{code} 平台限购金额必须大于 0")
        confirmation_date = _date(
            raw.get("expected_confirmation_date"), f"{code} 平台预计确认日期"
        )
        if confirmation_date is not None and not (
            quoted_at.date() <= confirmation_date
            <= quoted_at.date() + dt.timedelta(days=MAX_CONFIRMATION_DAYS)
        ):
            raise BatchPurchasePreflightValidationError(
                f"{code} 预计确认日期必须在报价日起 30 天内"
            )
        normalized.append({
            "code": code,
            "platform_name": platform_name,
            "quoted_at": quoted_at.isoformat(timespec="seconds"),
            "currency": str(raw.get("currency") or "CNY").upper(),
            "purchase_status": purchase_status,
            "purchase_limit_yuan": round(purchase_limit, 2) if purchase_limit is not None else None,
            "expected_confirmation_date": (
                confirmation_date.isoformat() if confirmation_date is not None else None
            ),
            "order_amount_yuan": round(order_amount, 2),
            "entry_fee_yuan": round(entry_fee, 2) if entry_fee is not None else None,
            "acknowledged_platform_quote": True,
        })
    if len(codes) != len(set(codes)):
        raise BatchPurchasePreflightValidationError("同一基金只能提交一条本次平台报价")
    return sorted(normalized, key=lambda item: item["code"])


def _project_holdings(
    holdings: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    allocation_items: dict[str, dict[str, Any]],
    *,
    generated_at: str,
) -> list[dict[str, Any]]:
    projected = copy.deepcopy(holdings)
    for quote in quotes:
        code = quote["code"]
        order_amount = _number(quote.get("order_amount_yuan"))
        entry_fee = _number(quote.get("entry_fee_yuan"))
        net_amount = (
            order_amount - entry_fee
            if order_amount is not None and entry_fee is not None
            and order_amount > entry_fee >= 0
            else None
        )
        existing = next(
            (
                item for item in projected
                if item.get("asset_type") == "fund"
                and str(item.get("code") or "") == code
            ),
            None,
        )
        if existing is None:
            projected.append({
                "id": f"projected-batch:{code}",
                "asset_type": "fund",
                "market": "",
                "code": code,
                "name": allocation_items.get(code, {}).get("name") or code,
                "amount": round(net_amount, 2) if net_amount is not None else None,
                "updated_at": generated_at,
                "source": "agent_batch_purchase_projection",
            })
            continue
        current_amount = _number(existing.get("amount"))
        existing["amount"] = (
            round(current_amount + net_amount, 2)
            if current_amount is not None and current_amount >= 0 and net_amount is not None
            else None
        )
        existing["updated_at"] = generated_at
    return projected


def _load_market_profiles(
    codes: list[str],
    provider: Callable[[str], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    def load(code: str) -> tuple[str, dict[str, Any]]:
        try:
            value = provider(code)
            if not isinstance(value, dict):
                raise TypeError("基金市场画像格式异常")
            return code, copy.deepcopy(value)
        except Exception as error:
            return code, {
                "resolution_status": "unavailable",
                "code": code,
                "market": {
                    "primary": "unknown",
                    "label": "真实基金市场元数据不可用",
                    "required_permissions": [],
                    "currency_risk": None,
                },
                "source": "东方财富基金代码搜索库 + 东方财富基金详情页",
                "error": str(error)[:240],
            }

    if not codes:
        return {}
    with ThreadPoolExecutor(
        max_workers=min(4, len(codes)), thread_name_prefix="batch-preflight-market"
    ) as pool:
        return dict(pool.map(load, codes))


def _load_disclosures(
    holdings: list[dict[str, Any]],
    provider: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], dict[str, str]]:
    codes = sorted({
        str(item.get("code") or "").strip()
        for item in holdings
        if item.get("asset_type") == "fund"
        and (_number(item.get("amount")) or 0) > 0
        and re.fullmatch(r"\d{6}", str(item.get("code") or "").strip())
    })
    if len(codes) > portfolio_exposure.MAX_FUND_SOURCES:
        raise BatchPurchasePreflightValidationError(
            f"一次组合申购复核最多读取 {portfolio_exposure.MAX_FUND_SOURCES} 只基金披露"
        )

    def load(code: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            value = provider(code)
            if not isinstance(value, dict):
                raise TypeError("基金披露工具返回格式异常")
            normalized = copy.deepcopy(value)
            normalized["code"] = code
            return code, normalized, None
        except Exception as error:
            return code, None, str(error)[:240]

    sources: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    if codes:
        with ThreadPoolExecutor(
            max_workers=min(4, len(codes)), thread_name_prefix="batch-preflight-exposure"
        ) as pool:
            for code, value, error in pool.map(load, codes):
                if value is not None:
                    sources[code] = value
                else:
                    failures.append({"code": code, "error": error or "真实披露不可用"})
    hashes = {code: payload_sha256(value) for code, value in sources.items()}
    return sources, failures, hashes


def create_batch_purchase_preflight(
    repository: AgentRepository,
    batch: dict[str, Any],
    request: dict[str, Any],
    *,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
    market_profile_provider: Callable[[str], dict[str, Any]] | None = None,
    disclosure_provider: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool]:
    current = _validated_now(now)
    allocation_event = batch.get("allocation_event")
    if not allocation_event or not allocation_event.get("integrity_verified"):
        raise BatchPurchasePreflightConflictError("组合资金分配快照不存在或完整性失败")
    expected_allocation_id = str(request.get("expected_allocation_event_id") or "")
    expected_allocation_hash = str(request.get("expected_allocation_event_hash") or "")
    if (
        allocation_event.get("id") != expected_allocation_id
        or allocation_event.get("event_hash") != expected_allocation_hash
    ):
        raise BatchPurchasePreflightConflictError("组合资金分配快照已变化，请刷新后重试")
    allocation = allocation_event.get("payload") or {}
    if (
        allocation.get("status") != "ready"
        or not ((allocation.get("decision_gate") or {}).get("manual_allocation_review_ready"))
    ):
        raise BatchPurchasePreflightConflictError("组合资金分配尚未通过确定性门禁")

    quotes = _normalize_quotes(request, current)
    allocation_items = {
        str(item.get("code") or ""): item
        for item in ((allocation.get("allocation") or {}).get("items") or [])
        if (_number(item.get("allocated_amount_yuan")) or 0) > 0.02
    }
    request_codes = {item["code"] for item in quotes}
    if request_codes != set(allocation_items):
        missing = sorted(set(allocation_items) - request_codes)
        extra = sorted(request_codes - set(allocation_items))
        raise BatchPurchasePreflightValidationError(
            f"平台报价必须逐只覆盖所有已分配基金；缺少 {','.join(missing) or '-'}；"
            f"额外 {','.join(extra) or '-'}"
        )

    generated_at = current.isoformat(timespec="seconds")
    holdings = storage.list_holdings(user_id=user_id)
    profile = storage.get_investment_profile(user_id=user_id)
    current_holdings_hash = portfolio_exposure.holdings_sha256(holdings)
    allocation_bindings = allocation.get("bindings") or {}
    profile_current = bool(
        str(profile.get("profile_version_id") or "")
        == str(allocation_bindings.get("profile_version_id") or "")
        and str(profile.get("payload_sha256") or "")
        == str(allocation_bindings.get("profile_payload_sha256") or "")
    )
    holdings_current = bool(
        current_holdings_hash
        == str(allocation_bindings.get("portfolio_holdings_sha256") or "")
    )
    projected_holdings = _project_holdings(
        holdings, quotes, allocation_items, generated_at=generated_at
    )
    projected_holdings_hash = portfolio_exposure.holdings_sha256(projected_holdings)

    market_provider = market_profile_provider or funds.get_fund_market_profile
    disclosure_loader = disclosure_provider or funds.get_fund_portfolio
    codes = sorted(allocation_items)
    market_profiles = _load_market_profiles(codes, market_provider)
    raw_sources, failed_sources, disclosure_hashes = _load_disclosures(
        projected_holdings, disclosure_loader
    )
    max_age_days = int(os.environ.get(
        "PORTFOLIO_EXPOSURE_MAX_AGE_DAYS",
        portfolio_exposure.DEFAULT_MAX_AGE_DAYS,
    ))
    projected_exposure = portfolio_exposure.build_exposure_snapshot(
        projected_holdings,
        raw_sources,
        target_code=None,
        failed_sources=failed_sources,
        profile_version_id=profile.get("profile_version_id"),
        observed_on=current.date(),
        max_age_days=max_age_days,
    )
    request_payload = {
        "expected_allocation_event_id": expected_allocation_id,
        "expected_allocation_event_hash": expected_allocation_hash,
        "expected_previous_event_hash": (
            str(request.get("expected_previous_event_hash"))
            if request.get("expected_previous_event_hash") else None
        ),
        "acknowledged_platform_quotes": True,
        "quotes": quotes,
    }
    request_sha256 = _sha256(request_payload)
    bindings = {
        "batch_id": str(batch.get("id") or ""),
        "batch_input_sha256": str(batch.get("input_hash") or ""),
        "allocation_event_id": str(allocation_event.get("id") or ""),
        "allocation_event_hash": str(allocation_event.get("event_hash") or ""),
        "allocation_payload_sha256": str(allocation_event.get("payload_sha256") or ""),
        "allocation_integrity_verified": True,
        "request_sha256": request_sha256,
        "expected_profile_version_id": str(allocation_bindings.get("profile_version_id") or ""),
        "expected_profile_payload_sha256": str(
            allocation_bindings.get("profile_payload_sha256") or ""
        ),
        "profile_version_id": str(profile.get("profile_version_id") or ""),
        "profile_payload_sha256": str(profile.get("payload_sha256") or ""),
        "profile_binding_current": profile_current,
        "expected_holdings_sha256": str(
            allocation_bindings.get("portfolio_holdings_sha256") or ""
        ),
        "current_holdings_sha256": current_holdings_hash,
        "holdings_binding_current": holdings_current,
        "projected_holdings_sha256": projected_holdings_hash,
        "projected_exposure_sha256": payload_sha256(projected_exposure),
        "market_profile_sha256": {
            code: payload_sha256(value) for code, value in market_profiles.items()
        },
        "fund_disclosure_sha256": disclosure_hashes,
    }
    payload = evaluate_portfolio_batch_purchase_preflight(
        allocation,
        quotes,
        profile=profile,
        market_profiles=market_profiles,
        projected_holdings=projected_holdings,
        projected_exposure=projected_exposure,
        bindings=bindings,
        generated_at=generated_at,
    )
    try:
        return repository.append_batch_purchase_preflight_event(
            str(batch.get("id") or ""),
            payload,
            user_id=user_id,
            actor_id=actor_id,
            expected_previous_event_hash=(
                str(request.get("expected_previous_event_hash"))
                if request.get("expected_previous_event_hash") else None
            ),
        )
    except KeyError as error:
        raise BatchPurchasePreflightConflictError(str(error)) from error
    except ValueError as error:
        raise BatchPurchasePreflightConflictError(str(error)) from error


def decorate_batch_purchase_preflight(
    repository: AgentRepository,
    batch: dict[str, Any],
    *,
    user_id: str,
    now: dt.datetime | None = None,
) -> dict[str, Any] | None:
    event = batch.get("purchase_preflight_event")
    if event is None:
        return None
    current = _validated_now(now)
    audit = repository.verify_batch_purchase_preflight_audit(
        str(batch.get("id") or ""), user_id=user_id
    )
    if not event.get("integrity_verified") or not audit.get("verified"):
        return {
            "status": "integrity_failed",
            "blockers": ["批量申购复核事件未通过内容哈希或审计链校验，报价与金额已停止展示"],
            "decision_gate": {
                "manual_purchase_review_ready": False,
                "execution_authorized": False,
                "automatic_purchase_allowed": False,
            },
            "snapshot": {
                "id": event.get("id"),
                "event_hash": event.get("event_hash"),
                "created_at": event.get("created_at"),
                "integrity_verified": False,
                "audit_chain_verified": bool(audit.get("verified")),
            },
        }

    payload = copy.deepcopy(event.get("payload") or {})
    bindings = payload.get("bindings") or {}
    allocation_event = batch.get("allocation_event") or {}
    profile = storage.get_investment_profile(user_id=user_id)
    holdings = storage.list_holdings(user_id=user_id)
    allocation_current = bool(
        allocation_event.get("integrity_verified")
        and allocation_event.get("id") == bindings.get("allocation_event_id")
        and allocation_event.get("event_hash") == bindings.get("allocation_event_hash")
        and allocation_event.get("payload_sha256") == bindings.get("allocation_payload_sha256")
    )
    profile_current = bool(
        str(profile.get("profile_version_id") or "")
        == str(bindings.get("profile_version_id") or "")
        and str(profile.get("payload_sha256") or "")
        == str(bindings.get("profile_payload_sha256") or "")
    )
    holdings_current = bool(
        portfolio_exposure.holdings_sha256(holdings)
        == bindings.get("current_holdings_sha256")
    )
    expires_at = None
    try:
        expires_at = _datetime(payload.get("quote_expires_at"), "报价失效时间")
    except BatchPurchasePreflightValidationError:
        expires_at = None
    expired = bool(expires_at is None or expires_at.astimezone(dt.timezone.utc) < current)
    current_bindings = bool(allocation_current and profile_current and holdings_current)
    stored_status = str(payload.get("status") or "purchase_preflight_blocked")
    if not current_bindings:
        status = "superseded"
        dynamic_blocker = "组合分配、当前持仓或 IPS 已变化，必须重新读取真实数据并复核"
    elif expired:
        status = "expired"
        dynamic_blocker = "至少一项销售平台报价已超过 24 小时，必须逐只重新确认"
    else:
        status = stored_status
        dynamic_blocker = None
    blockers = list(payload.get("blockers") or [])
    if dynamic_blocker:
        blockers = [dynamic_blocker, *blockers]
    stored_gate = payload.get("decision_gate") or {}
    ready = bool(
        status == "ready_for_manual_purchase_review"
        and current_bindings
        and not expired
        and stored_gate.get("manual_purchase_review_ready")
    )
    payload["status"] = status
    payload["blockers"] = blockers
    payload["decision_gate"] = {
        **stored_gate,
        "manual_purchase_review_ready": ready,
        "execution_authorized": False,
        "automatic_purchase_allowed": False,
        "order_submitted": False,
        "reason": stored_gate.get("reason") if ready else status,
    }
    payload["current_bindings"] = {
        "allocation_current": allocation_current,
        "profile_current": profile_current,
        "holdings_current": holdings_current,
        "all_current": current_bindings,
        "quote_expired": expired,
    }
    payload["snapshot"] = {
        "id": event.get("id"),
        "revision": event.get("sequence_no"),
        "event_hash": event.get("event_hash"),
        "payload_sha256": event.get("payload_sha256"),
        "previous_hash": event.get("previous_hash"),
        "created_at": event.get("created_at"),
        "integrity_verified": True,
        "audit_chain_verified": True,
        "audit_event_count": audit.get("event_count"),
    }
    return payload
