# -*- coding: utf-8 -*-
"""Versioned holding theses and deterministic discipline checks.

A thesis is user-authored policy, not market evidence. The module only compares
its structured limits with confirmed holding facts and real fund drawdown data;
free-text entry/exit conditions are never treated as machine-verified signals.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import storage
from investment_policy import payload_sha256


SCHEMA_VERSION = "holding_thesis.v1"
ROLES = {
    "core_growth",
    "satellite_growth",
    "defensive",
    "income",
    "diversifier",
    "tactical",
}
ROLE_LABELS = {
    "core_growth": "核心增长",
    "satellite_growth": "卫星增强",
    "defensive": "防守稳定",
    "income": "现金流",
    "diversifier": "分散风险",
    "tactical": "阶段机会",
}


def _text(value: Any, field: str, *, minimum: int = 0, maximum: int = 600) -> str:
    result = str(value or "").strip()
    if len(result) < minimum:
        raise ValueError(f"{field}至少需要{minimum}个字符")
    if len(result) > maximum:
        raise ValueError(f"{field}不能超过{maximum}个字符")
    return result


def _number(value: Any, field: str, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field}必须是数字") from error
    if not math.isfinite(result) or result < low or result > high:
        raise ValueError(f"{field}必须在{low:g}到{high:g}之间")
    return round(result, 2)


def _integer(value: Any, field: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field}必须是整数") from error
    if result < low or result > high:
        raise ValueError(f"{field}必须在{low}到{high}之间")
    return result


def _date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field}必须是有效日期") from error


def normalize_thesis(payload: dict[str, Any], *, today: dt.date | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("持有逻辑必须是对象")
    today = today or dt.date.today()
    role = str(payload.get("role") or "").strip()
    if role not in ROLES:
        raise ValueError("请选择有效的组合角色")
    review_date = _date(payload.get("review_date"), "下次复核日期")
    if review_date < today:
        raise ValueError("下次复核日期不能早于今天")
    if review_date > today + dt.timedelta(days=3660):
        raise ValueError("下次复核日期不能超过十年")
    return {
        "role": role,
        "role_label": ROLE_LABELS[role],
        "thesis_summary": _text(payload.get("thesis_summary"), "买入与持有逻辑", minimum=12),
        "expected_holding_months": _integer(
            payload.get("expected_holding_months"),
            "计划持有月数",
            1,
            240,
        ),
        "review_date": review_date.isoformat(),
        "max_loss_pct": _number(payload.get("max_loss_pct"), "最大可接受持仓亏损", 1, 80),
        "max_drawdown_pct": _number(
            payload.get("max_drawdown_pct"),
            "最大可接受标的回撤",
            1,
            80,
        ),
        "add_condition": _text(payload.get("add_condition"), "新增条件", minimum=6),
        "exit_condition": _text(payload.get("exit_condition"), "退出条件", minimum=6),
    }


def _holding_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("asset_type") or ""),
        str(item.get("market") or ""),
        str(item.get("code") or ""),
    )


def save_thesis(payload: dict[str, Any], *, user_id: str = "default") -> dict[str, Any]:
    normalized = normalize_thesis(payload)
    identity = (
        str(payload.get("asset_type") or "").strip(),
        str(payload.get("market") or "").strip(),
        str(payload.get("code") or "").strip(),
    )
    holding = next(
        (item for item in storage.list_holdings(user_id=user_id) if _holding_key(item) == identity),
        None,
    )
    if holding is None:
        raise ValueError("持仓不存在或市场信息不匹配，不能保存持有逻辑")
    record = storage.append_holding_thesis_version({
        "schema_version": SCHEMA_VERSION,
        "state": "active",
        "asset_type": identity[0],
        "market": identity[1],
        "code": identity[2],
        "name": str(holding.get("name") or identity[2]),
        "holding_id": holding.get("id"),
        "holding_created_at": holding.get("created_at"),
        **normalized,
    }, user_id=user_id)
    return {
        "item": record,
        "verification": storage.verify_holding_thesis_chain(*identity, user_id=user_id),
    }


def latest_theses(*, user_id: str = "default") -> list[dict[str, Any]]:
    items = storage.list_latest_holding_theses(user_id=user_id)
    verified = []
    for item in items:
        chain = storage.verify_holding_thesis_chain(
            str(item.get("asset_type") or ""),
            str(item.get("market") or ""),
            str(item.get("code") or ""),
            user_id=user_id,
        )
        verified.append({
            **item,
            "chain_verification": chain,
            "integrity_verified": bool(
                item.get("integrity_verified") and chain.get("verified")
            ),
        })
    return verified


def theses_for_holdings(
    items: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    holdings_by_key = {_holding_key(item): item for item in holdings}
    relevant = []
    for item in items:
        holding = holdings_by_key.get(_holding_key(item))
        payload = item.get("payload") or {}
        if holding is None or payload.get("holding_id") != holding.get("id"):
            continue
        relevant.append(item)
    return relevant


def theses_sha256(items: list[dict[str, Any]]) -> str:
    snapshot = [
        {
            "asset_type": item.get("asset_type"),
            "market": item.get("market"),
            "code": item.get("code"),
            "version_id": item.get("id"),
            "version_no": item.get("version_no"),
            "state": item.get("state"),
            "payload_sha256": item.get("payload_sha256"),
        }
        for item in items
    ]
    snapshot.sort(key=lambda item: (
        str(item.get("asset_type") or ""),
        str(item.get("market") or ""),
        str(item.get("code") or ""),
    ))
    return payload_sha256(snapshot)


def evaluate_thesis(
    record: dict[str, Any] | None,
    *,
    holding: dict[str, Any],
    trend: dict[str, Any] | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    today = today or dt.date.today()
    if not record or record.get("state") != "active":
        return {
            "status": "missing",
            "label": "未建立持有逻辑",
            "review_due": False,
            "breaches": [],
            "evidence": [],
        }
    payload = record.get("payload") or {}
    if not record.get("integrity_verified") or payload.get("schema_version") != SCHEMA_VERSION:
        return {
            "status": "unavailable",
            "label": "持有逻辑完整性失败",
            "review_due": False,
            "breaches": [],
            "evidence": [],
        }

    profit_rate = holding.get("profit_rate")
    try:
        profit_rate = float(profit_rate) if profit_rate is not None else None
    except (TypeError, ValueError):
        profit_rate = None
    current_drawdown = (trend or {}).get("current_drawdown")
    try:
        current_drawdown = float(current_drawdown) if current_drawdown is not None else None
    except (TypeError, ValueError):
        current_drawdown = None

    try:
        max_loss = float(payload["max_loss_pct"])
        max_drawdown = float(payload["max_drawdown_pct"])
        review_date = _date(payload.get("review_date"), "下次复核日期")
    except (KeyError, TypeError, ValueError):
        return {
            "status": "unavailable",
            "label": "持有逻辑字段校验失败",
            "review_due": False,
            "breaches": [],
            "evidence": [],
        }
    breaches = []
    if profit_rate is not None and profit_rate <= -max_loss:
        breaches.append({
            "code": "holding_loss_limit_reached",
            "label": "持仓亏损边界已触发",
            "actual": round(profit_rate, 2),
            "limit": -max_loss,
            "source": "用户确认持仓",
        })
    if current_drawdown is not None and current_drawdown <= -max_drawdown:
        breaches.append({
            "code": "asset_drawdown_limit_reached",
            "label": "标的回撤边界已触发",
            "actual": round(current_drawdown, 2),
            "limit": -max_drawdown,
            "source": (trend or {}).get("source") or "基金真实净值",
        })
    review_due = review_date <= today
    status = "risk_limit_breached" if breaches else "review_due" if review_due else "on_track"
    label = {
        "risk_limit_breached": "纪律边界触发，立即复核",
        "review_due": "持有逻辑到期复核",
        "on_track": "当前仍在计划内",
    }[status]
    return {
        "status": status,
        "label": label,
        "review_due": review_due,
        "days_until_review": (review_date - today).days,
        "breaches": breaches,
        "evidence": [
            {"label": "确认持仓收益率", "value": profit_rate, "source": "用户确认持仓"},
            {
                "label": "标的当前回撤",
                "value": current_drawdown,
                "source": (trend or {}).get("source") or "基金真实净值",
            },
        ],
        "manual_conditions": {
            "add_condition": payload.get("add_condition"),
            "exit_condition": payload.get("exit_condition"),
            "machine_verified": False,
        },
    }


def list_with_coverage(*, user_id: str = "default") -> dict[str, Any]:
    holdings = storage.list_holdings(user_id=user_id)
    items = theses_for_holdings(latest_theses(user_id=user_id), holdings)
    active = [item for item in items if item.get("state") == "active"]
    verified = sum(bool(item.get("integrity_verified")) for item in active)
    return {
        "items": items,
        "coverage": {
            "holding_count": len(holdings),
            "active_thesis_count": len(active),
            "verified_thesis_count": verified,
            "missing_count": max(0, len(holdings) - len(active)),
        },
        "schema_version": SCHEMA_VERSION,
        "policy": (
            "持有逻辑由用户确认并按版本保存；系统只核验复核日期和结构化风险边界，"
            "不会把自由文本条件冒充为已触发的交易信号。"
        ),
    }
