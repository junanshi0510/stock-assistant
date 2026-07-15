# -*- coding: utf-8 -*-
"""Versioned user-lot cost review for a fund replacement candidate."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from typing import Any


DIAGNOSTIC_ID = "fund_switch_cost_review"
DIAGNOSTIC_VERSION = "1.0.0"
MAX_CONFIRMED_NAV_AGE_DAYS = 7
SHARE_RECONCILIATION_TOLERANCE = 0.001


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _interval_match(
    value: float,
    band: dict[str, Any],
    *,
    minimum_field: str,
    maximum_field: str,
) -> bool:
    if band.get("interval_status") != "parsed":
        return False
    minimum = _number(band.get(minimum_field))
    maximum = _number(band.get(maximum_field))
    if minimum is not None:
        if band.get("min_inclusive") is False and value <= minimum:
            return False
        if band.get("min_inclusive") is not False and value < minimum:
            return False
    if maximum is not None:
        if band.get("max_inclusive") is True and value > maximum:
            return False
        if band.get("max_inclusive") is not True and value >= maximum:
            return False
    return minimum is not None or maximum is not None


def _single_band(
    value: float,
    bands: Any,
    *,
    minimum_field: str,
    maximum_field: str,
) -> dict[str, Any] | None:
    if not isinstance(bands, list):
        return None
    matched = [
        band
        for band in bands if isinstance(band, dict)
        if _interval_match(
            value,
            band,
            minimum_field=minimum_field,
            maximum_field=maximum_field,
        )
    ]
    return matched[0] if len(matched) == 1 else None


def _purchase_fee(amount: float, band: dict[str, Any], rate_field: str) -> float | None:
    fixed = _number(band.get("fixed_fee_yuan"))
    if fixed is not None:
        return min(amount, max(0.0, fixed))
    rate = _number(band.get(rate_field))
    if rate is None or rate < 0:
        return None
    return amount - amount / (1 + rate / 100)


def _coverage_months(cost_rate_pct: float | None, annual_excess_pp: float | None) -> float | None:
    if cost_rate_pct is None or annual_excess_pp is None or annual_excess_pp <= 0:
        return None
    return cost_rate_pct / annual_excess_pp * 12


def _evidence_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _blocked(base: dict[str, Any], status: str, label: str, reason: str, requirements: list[str]) -> dict:
    result = {
        **base,
        "status": status,
        "label": label,
        "reason": reason,
        "redemption": None,
        "candidate_entry": None,
        "cost_snapshots": None,
        "historical_cost_hurdle": None,
        "decision_gate": {
            "eligible_for_platform_quote_confirmation": False,
            "cost_snapshot_complete": False,
            "executable_switch_cost_confirmed": False,
            "automatic_switch_allowed": False,
            "reason": status,
            "remaining_requirements": requirements,
        },
    }
    result["evidence_sha256"] = _evidence_hash(result)
    return result


def evaluate_fund_switch_cost(
    holding: dict[str, Any],
    lot_snapshot: dict[str, Any],
    selected_fees: dict[str, Any],
    candidate_fees: dict[str, Any],
    valuation: dict[str, Any],
    durability: dict[str, Any],
    due_diligence: dict[str, Any],
    *,
    candidate_code: str,
    candidate_name: str = "",
    review_on: date | None = None,
) -> dict[str, Any]:
    """Calculate a disclosed-fee snapshot without treating it as a platform quote."""
    review_date = review_on or date.today()
    position = lot_snapshot.get("position") or {}
    lots = lot_snapshot.get("remaining_lots") if isinstance(lot_snapshot.get("remaining_lots"), list) else []
    confirmed_shares = _number(holding.get("shares"))
    open_shares = _number(position.get("open_shares"))
    shares_match = bool(
        confirmed_shares is not None
        and open_shares is not None
        and abs(confirmed_shares - open_shares)
        <= max(1e-8, abs(confirmed_shares) * SHARE_RECONCILIATION_TOLERANCE)
    )
    lot_dates = [_date(item.get("trade_date")) for item in lots]
    valid_lot_dates = [item for item in lot_dates if item is not None]
    coverage = {
        "transaction_count": int(lot_snapshot.get("transaction_count") or 0),
        "remaining_lot_count": len(lots),
        "buy_lot_count": sum(item.get("trade_type") == "buy" for item in lots),
        "opening_lot_count": sum(item.get("trade_type") == "opening" for item in lots),
        "open_shares": _round(open_shares, 8),
        "confirmed_shares": _round(confirmed_shares, 8),
        "shares_match": shares_match,
        "oldest_lot_date": min(valid_lot_dates).isoformat() if valid_lot_dates else None,
        "newest_lot_date": max(valid_lot_dates).isoformat() if valid_lot_dates else None,
        "integrity_issue_count": len(lot_snapshot.get("integrity_issues") or []),
    }
    base = {
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "selected_code": str(holding.get("code") or ""),
        "candidate_code": str(candidate_code or ""),
        "candidate_name": str(candidate_name or ""),
        "review_on": review_date.isoformat(),
        "coverage": coverage,
        "sources": {
            "transaction_lots": "用户录入交易流水 / FIFO 剩余份额",
            "selected_fee_url": selected_fees.get("source_url"),
            "candidate_fee_url": candidate_fees.get("source_url"),
            "valuation_url": valuation.get("source_url"),
        },
        "policy": "仅核算真实流水、最新确认净值和披露费率形成的成本快照；平台最终报价确认前不得执行换仓。",
    }

    if not bool((due_diligence.get("decision_gate") or {}).get("eligible_for_holding_period_cost_review")):
        return _blocked(
            base,
            "blocked_by_due_diligence",
            "上游门禁未通过",
            "候选未通过持续性、费率与披露持仓门禁，不进入用户成本核算。",
            ["先满足候选持续性与替换价值门禁"],
        )
    if not lots or int(lot_snapshot.get("transaction_count") or 0) <= 0:
        return _blocked(
            base,
            "transaction_lots_missing",
            "缺少真实买入批次",
            "交易账本没有该基金的剩余买入批次，系统不会用持仓创建时间代替。",
            ["在交易账本录入真实申购确认日期、份额、净值和费用"],
        )
    if lot_snapshot.get("integrity_issues"):
        return _blocked(
            base,
            "lot_integrity_failed",
            "交易批次完整性失败",
            "该基金存在无法匹配的卖出或交易方向问题，FIFO 剩余份额不可信。",
            ["修复交易账本中的份额完整性问题"],
        )
    if any(item.get("trade_type") != "buy" for item in lots):
        return _blocked(
            base,
            "lot_date_unverified",
            "期初持仓日期不可用于赎回费",
            "剩余份额包含期初持仓；期初登记日不等于真实申购确认日，不能据此选择赎回费档位。",
            ["将期初持仓拆分为带真实申购确认日期的买入批次"],
        )
    if confirmed_shares is None or confirmed_shares <= 0:
        return _blocked(
            base,
            "confirmed_shares_missing",
            "缺少已确认份额",
            "当前持仓没有已确认份额，无法与 FIFO 账本对账。",
            ["在持仓中补充销售平台确认的当前份额"],
        )
    if not shares_match:
        return _blocked(
            base,
            "share_reconciliation_failed",
            "持仓份额与账本不一致",
            "当前确认份额与 FIFO 剩余份额超出 0.1% 容差，不能核算完整赎回成本。",
            ["补录缺失交易，直到账本剩余份额与当前确认份额一致"],
        )

    unit_nav = _number(valuation.get("unit_nav"))
    nav_date = _date(valuation.get("as_of"))
    if unit_nav is None or unit_nav <= 0 or nav_date is None:
        return _blocked(
            base,
            "confirmed_nav_missing",
            "确认净值不可用",
            "当前基金没有可用的最新确认单位净值，停止金额核算。",
            ["等待基金公司披露最新确认净值"],
        )
    nav_age_days = (review_date - nav_date).days
    if nav_age_days < 0 or nav_age_days > MAX_CONFIRMED_NAV_AGE_DAYS:
        return _blocked(
            {**base, "valuation": {"as_of": nav_date.isoformat(), "age_days": nav_age_days}},
            "confirmed_nav_stale",
            "确认净值已过期",
            f"最新确认净值距核算日 {nav_age_days} 天，超过 {MAX_CONFIRMED_NAV_AGE_DAYS} 天门限。",
            ["等待并刷新最新确认净值"],
        )
    if selected_fees.get("status") != "available":
        return _blocked(
            base,
            "selected_fee_schedule_missing",
            "当前基金费率不可用",
            "当前基金真实赎回费率表不可用，停止成本核算。",
            ["恢复当前基金费率档案来源"],
        )
    if candidate_fees.get("status") != "available":
        return _blocked(
            base,
            "candidate_fee_schedule_missing",
            "候选申购费率不可用",
            "候选基金真实申购费率表不可用，停止成本核算。",
            ["恢复候选基金费率档案来源"],
        )

    redemption_bands = (selected_fees.get("redemption") or {}).get("bands") or []
    lot_rows = []
    unmatched_lots = []
    for item in lots:
        trade_date = _date(item.get("trade_date"))
        shares = _number(item.get("shares"))
        if trade_date is None or shares is None or shares <= 0 or trade_date > review_date:
            unmatched_lots.append(item.get("transaction_id"))
            continue
        holding_days = (review_date - trade_date).days
        band = _single_band(
            holding_days,
            redemption_bands,
            minimum_field="min_holding_days",
            maximum_field="max_holding_days",
        )
        rate = _number((band or {}).get("rate_pct"))
        if band is None or rate is None or rate < 0:
            unmatched_lots.append(item.get("transaction_id"))
            continue
        gross = shares * unit_nav
        lot_rows.append({
            "transaction_id": item.get("transaction_id"),
            "confirmation_date": trade_date.isoformat(),
            "holding_days": holding_days,
            "shares": _round(shares, 8),
            "gross_value_yuan": _round(gross),
            "matched_band": band.get("holding_period"),
            "rate_pct": _round(rate, 4),
            "fee_yuan": _round(gross * rate / 100),
        })
    if unmatched_lots or len(lot_rows) != len(lots):
        return _blocked(
            base,
            "redemption_band_unmatched",
            "赎回费档位无法完整匹配",
            "至少一个剩余批次的确认日期或真实费率区间无法精确匹配。",
            ["核对申购确认日期并恢复完整赎回费率区间"],
        )

    gross_value = sum((_number(item.get("shares")) or 0) * unit_nav for item in lots)
    redemption_fee = sum(_number(item.get("fee_yuan")) or 0 for item in lot_rows)
    net_proceeds = max(0.0, gross_value - redemption_fee)
    purchase_bands = (candidate_fees.get("purchase") or {}).get("bands") or []
    purchase_band = _single_band(
        net_proceeds,
        purchase_bands,
        minimum_field="min_amount_yuan",
        maximum_field="max_amount_yuan",
    )
    if purchase_band is None:
        return _blocked(
            base,
            "candidate_entry_band_unmatched",
            "候选申购费档位无法匹配",
            "按赎回后可投入金额无法唯一匹配候选基金的真实申购费率区间。",
            ["恢复候选基金完整申购费率区间并刷新"],
        )

    promotional_entry_fee = _purchase_fee(net_proceeds, purchase_band, "current_rate_pct")
    standard_entry_fee = _purchase_fee(net_proceeds, purchase_band, "source_rate_pct")
    if promotional_entry_fee is None and standard_entry_fee is None:
        return _blocked(
            base,
            "candidate_entry_rate_missing",
            "候选申购费率缺失",
            "候选金额档位没有可计算的标准费率、页面优惠费率或固定费用。",
            ["恢复候选基金完整申购费率披露"],
        )

    def snapshot(entry_fee: float | None, rate_field: str, label: str) -> dict[str, Any] | None:
        if entry_fee is None:
            return None
        total = redemption_fee + entry_fee
        cost_rate = total / gross_value * 100 if gross_value > 0 else None
        return {
            "label": label,
            "candidate_entry_rate_pct": _round(purchase_band.get(rate_field), 4),
            "candidate_entry_fee_yuan": _round(entry_fee),
            "total_switching_cost_yuan": _round(total),
            "total_switching_cost_rate_pct": _round(cost_rate, 4),
        }

    promotional = snapshot(
        promotional_entry_fee,
        "current_rate_pct",
        "费率页平台优惠快照",
    )
    standard = snapshot(
        standard_entry_fee,
        "source_rate_pct",
        "基金标准披露费率快照",
    )
    annual_excess = _number(((durability.get("rolling") or {}).get("12m") or {}).get("median_excess_pp"))
    hurdle = {
        "rolling_12m_median_excess_pp": _round(annual_excess),
        "page_promotional_coverage_months": _round(
            _coverage_months(
                _number((promotional or {}).get("total_switching_cost_rate_pct")),
                annual_excess,
            ),
            1,
        ),
        "standard_fee_coverage_months": _round(
            _coverage_months(
                _number((standard or {}).get("total_switching_cost_rate_pct")),
                annual_excess,
            ),
            1,
        ),
        "method": "用滚动 12 个月历史中位超额按月线性折算覆盖一次性成本，仅表示历史成本门槛，不是未来回本预测。",
    }
    confirmed_amount = _number(holding.get("amount"))
    result = {
        **base,
        "status": "ready_for_platform_quote",
        "label": "披露成本已核算，待平台报价",
        "reason": "FIFO 剩余批次、确认净值和披露费率已完整匹配；销售平台最终报价与在途风险仍未确认。",
        "valuation": {
            "as_of": nav_date.isoformat(),
            "age_days": nav_age_days,
            "unit_nav": _round(unit_nav, 6),
            "nav_based_gross_value_yuan": _round(gross_value),
            "user_confirmed_amount_yuan": _round(confirmed_amount),
            "confirmed_amount_delta_yuan": _round(confirmed_amount - gross_value) if confirmed_amount is not None else None,
        },
        "redemption": {
            "gross_value_yuan": _round(gross_value),
            "disclosed_fee_yuan": _round(redemption_fee),
            "effective_rate_pct": _round(redemption_fee / gross_value * 100 if gross_value > 0 else None, 4),
            "net_proceeds_yuan": _round(net_proceeds),
            "lot_breakdown": lot_rows,
        },
        "candidate_entry": {
            "investable_amount_yuan": _round(net_proceeds),
            "matched_band": purchase_band.get("amount_range"),
            "standard_rate_pct": _round(purchase_band.get("source_rate_pct"), 4),
            "page_promotional_rate_pct": _round(purchase_band.get("current_rate_pct"), 4),
            "fixed_fee_yuan": _round(purchase_band.get("fixed_fee_yuan")),
            "fee_formula": "申购费=申购金额-申购金额/(1+费率)；固定费用按每笔披露。",
        },
        "cost_snapshots": {
            "page_promotional": promotional,
            "standard_disclosed": standard,
        },
        "historical_cost_hurdle": hurdle,
        "decision_gate": {
            "eligible_for_platform_quote_confirmation": True,
            "cost_snapshot_complete": True,
            "executable_switch_cost_confirmed": False,
            "automatic_switch_allowed": False,
            "reason": "platform_quote_and_settlement_pending",
            "remaining_requirements": [
                "在销售平台提交前确认当日赎回费与申购费报价",
                "确认账本日期均为份额申购确认日期",
                "确认赎回到账时间、在途市场波动和额度限制",
            ],
        },
    }
    result["evidence_sha256"] = _evidence_hash(result)
    return result
