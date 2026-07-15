# -*- coding: utf-8 -*-
"""Batch live-level recurrence for user-confirmed holdings.

The batch layer only coordinates existing real quote, estimate, and history
providers. It never substitutes a confirmed NAV, prior close, or synthetic
series when a provider is unavailable.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import funds
import quotes
from strategies.asset_level_recurrence import unavailable_level_recurrence


SCHEMA_VERSION = "holding_level_recurrence.v1"
MATCHED_STATUSES = {"reached", "reached_exact", "crossed_between"}


def _holding_key(item: dict[str, Any]) -> str:
    return ":".join(
        [
            str(item.get("asset_type") or "").strip(),
            str(item.get("market") or "").strip(),
            str(item.get("code") or "").strip(),
        ]
    )


def _unavailable(item: dict[str, Any], reason: str) -> dict[str, Any]:
    asset_type = str(item.get("asset_type") or "").strip()
    is_fund = asset_type == "fund"
    return unavailable_level_recurrence(
        asset_type=asset_type or "unknown",
        reason=reason,
        target_label="盘中估算净值" if is_fund else "实时成交价",
    )


def _row(
    item: dict[str, Any],
    *,
    stock_months: int,
    stock_provider: Callable[[str, str, int], dict[str, Any]],
    fund_provider: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    asset_type = str(item.get("asset_type") or "").strip()
    market = str(item.get("market") or "").strip()
    code = str(item.get("code") or "").strip()
    identity = {
        "holding_id": item.get("id"),
        "key": _holding_key(item),
        "asset_type": asset_type,
        "market": market,
        "code": code,
        "name": str(item.get("name") or "").strip(),
    }

    try:
        if asset_type == "fund":
            response = fund_provider(code)
            recurrence = response.get("level_recurrence") if isinstance(response, dict) else None
            if not isinstance(recurrence, dict):
                reason = (
                    response.get("reason")
                    if isinstance(response, dict)
                    else "真实基金估值源没有返回结构化结果。"
                )
                recurrence = _unavailable(item, str(reason or "真实基金估值源没有返回历史到达结果。"))
        elif asset_type == "stock":
            response = stock_provider(market, code, stock_months)
            recurrence = response.get("level_recurrence") if isinstance(response, dict) else None
            if not isinstance(recurrence, dict):
                recurrence = _unavailable(item, "真实股票行情源没有返回历史到达结果。")
        else:
            recurrence = _unavailable(item, f"不支持的持仓类型: {asset_type or '空'}")
    except Exception as error:
        label = "基金盘中估值" if asset_type == "fund" else "股票实时价位"
        recurrence = _unavailable(item, f"真实{label}历史到达分析失败: {error}")

    return {**identity, "recurrence": recurrence}


def build_holding_level_recurrence(
    items: list[dict[str, Any]],
    *,
    stock_months: int = 60,
    max_workers: int = 6,
    stock_provider: Callable[[str, str, int], dict[str, Any]] | None = None,
    fund_provider: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate every holding while preserving input order and partial failures."""
    stock_months = max(6, min(120, int(stock_months)))
    workers = max(1, min(8, int(max_workers)))
    stock_provider = stock_provider or (
        lambda market, code, months: quotes.get_quote_level_history(
            market, code, months=months
        )
    )
    fund_provider = fund_provider or funds.get_fund_estimate

    def load(item: dict[str, Any]) -> dict[str, Any]:
        return _row(
            item,
            stock_months=stock_months,
            stock_provider=stock_provider,
            fund_provider=fund_provider,
        )

    if items:
        with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
            rows = list(pool.map(load, items))
    else:
        rows = []

    statuses = [str((item.get("recurrence") or {}).get("status") or "unavailable") for item in rows]
    unavailable_count = sum(status == "unavailable" for status in statuses)
    matched_count = sum(status in MATCHED_STATUSES for status in statuses)
    not_found_count = sum(status == "not_found_in_coverage" for status in statuses)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "items": rows,
        "summary": {
            "holding_count": len(rows),
            "available_count": len(rows) - unavailable_count,
            "matched_count": matched_count,
            "not_found_count": not_found_count,
            "unavailable_count": unavailable_count,
        },
        "coverage": {
            "stock_history_months": stock_months,
            "fund_history_months": 120,
        },
        "policy": (
            "股票使用实时成交价与未复权历史日线，基金使用盘中估算净值与确认净值历史。"
            "单项真实来源不可用时只返回该项不可用，不使用昨收、确认净值或模拟数据替代。"
        ),
    }
