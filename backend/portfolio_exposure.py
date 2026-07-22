# -*- coding: utf-8 -*-
"""Versioned, conservative look-through exposure for portfolio decisions.

The module treats fund disclosures as dated observations. Missing holdings are
never estimated: every risk dimension is represented as a lower/upper interval.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import storage
import portfolio_valuation


SCHEMA_VERSION = "portfolio_exposure_snapshot.v1"
MODEL_VERSION = "conservative_lookthrough_intervals.v1"
MARKET_CLASSIFIER_VERSION = "security_identifier_market.v1"
DEFAULT_MAX_AGE_DAYS = 200
MAX_FUND_SOURCES = 50


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def holdings_sha256(holdings: list[dict[str, Any]]) -> str:
    rows = [
        {
            "asset_type": str(item.get("asset_type") or ""),
            "market": str(item.get("market") or ""),
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or ""),
            "amount": _number(item.get("amount")),
            "updated_at": str(item.get("updated_at") or ""),
        }
        for item in holdings
    ]
    rows.sort(key=lambda item: (item["asset_type"], item["market"], item["code"]))
    return sha256_payload(rows)


def _period_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    iso = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if iso:
        try:
            return dt.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    quarter = re.search(r"(20\d{2}).*?([1-4])\s*(?:季|Q)", text, re.I)
    if quarter:
        year, q = int(quarter.group(1)), int(quarter.group(2))
        month = q * 3
        day = 31 if month in {3, 12} else 30
        return dt.date(year, month, day)
    return None


def _age_days(period: Any, observed_on: dt.date) -> int | None:
    parsed = _period_date(period)
    if parsed is None:
        return None
    return max(0, (observed_on - parsed).days)


def classify_security_market(code: Any, name: Any = "") -> str:
    """Classify only identifiers that are unambiguous enough for risk evidence."""
    value = str(code or "").strip().upper()
    label = str(name or "").upper()
    if re.fullmatch(r"\d{5}", value):
        return "hong_kong"
    if re.fullmatch(r"\d{6}", value) and value.startswith(
        ("00", "30", "60", "68", "83", "87", "43", "92")
    ):
        return "mainland"
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", value):
        return "united_states"
    if any(token in label for token in ("港股", "H股")):
        return "hong_kong"
    if any(token in label for token in ("美股", "NASDAQ", "NYSE")):
        return "united_states"
    return "unknown"


def _holding_market(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"a股", "a", "cn", "mainland", "沪市", "深市", "北交所"}:
        return "mainland"
    if text in {"港股", "hk", "hong_kong", "香港"}:
        return "hong_kong"
    if text in {"美股", "us", "usa", "united_states", "美国"}:
        return "united_states"
    return "unknown"


def _ratio(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0 or number > 100:
        return None
    return number


def _normalize_source(raw: dict[str, Any], observed_on: dt.date, max_age_days: int) -> dict[str, Any]:
    code = str(raw.get("code") or "").strip()
    allocation = raw.get("asset_allocation") or {}
    stock_ratio = _ratio(allocation.get("stock_ratio"))
    stocks = []
    for row in raw.get("stocks") or []:
        ratio = _ratio(row.get("nav_ratio"))
        security_code = str(row.get("code") or "").strip()
        if ratio is None or not security_code:
            continue
        stocks.append({
            "code": security_code,
            "name": str(row.get("name") or ""),
            "ratio": ratio,
            "market": classify_security_market(security_code, row.get("name")),
        })
    industries = []
    for row in raw.get("industries") or []:
        ratio = _ratio(row.get("nav_ratio"))
        name = str(row.get("name") or "").strip()
        if ratio is None or not name:
            continue
        industries.append({"name": name, "ratio": ratio})

    stock_disclosed = sum(item["ratio"] for item in stocks)
    industry_disclosed = sum(item["ratio"] for item in industries)
    conflicts = []
    if stock_disclosed > 100.5:
        conflicts.append("stock_disclosure_exceeds_100")
    if industry_disclosed > 100.5:
        conflicts.append("industry_disclosure_exceeds_100")
    if stock_ratio is not None and stock_disclosed > stock_ratio + 2:
        conflicts.append("stock_disclosure_exceeds_asset_allocation")
    if stock_ratio is not None and industry_disclosed > stock_ratio + 2:
        conflicts.append("industry_disclosure_exceeds_asset_allocation")

    if stock_ratio is not None:
        equity_lower = stock_ratio
        equity_upper = stock_ratio
    else:
        equity_lower = min(100.0, stock_disclosed)
        equity_upper = 100.0
    industry_known = min(industry_disclosed, equity_upper)
    industry_unknown = max(0.0, equity_upper - industry_known)

    asset_period = str(raw.get("asset_period") or "")
    stock_period = str(raw.get("stock_period") or "")
    industry_period = str(raw.get("industry_period") or "")
    equity_period = asset_period if stock_ratio is not None else stock_period
    equity_age = _age_days(equity_period, observed_on)
    industry_age = _age_days(industry_period, observed_on)
    stale_dimensions = []
    if equity_age is None or equity_age > max_age_days:
        stale_dimensions.append("equity")
    if equity_upper > 0.5 and (industry_age is None or industry_age > max_age_days):
        stale_dimensions.append("industry")

    return {
        "code": code,
        "name": str(raw.get("name") or code),
        "source": str(raw.get("source") or "天天基金投资组合 / 东方财富基金档案"),
        "source_url": str(raw.get("source_url") or ""),
        "periods": {
            "asset": asset_period or None,
            "stock": stock_period or None,
            "industry": industry_period or None,
        },
        "ages_days": {"equity": equity_age, "industry": industry_age},
        "asset_allocation": {
            "stock_ratio": stock_ratio,
            "bond_ratio": _ratio(allocation.get("bond_ratio")),
            "cash_ratio": _ratio(allocation.get("cash_ratio")),
        },
        "linked_fund": raw.get("linked_fund") if isinstance(raw.get("linked_fund"), dict) else None,
        "equity_interval": {
            "lower_ratio": _round(equity_lower),
            "upper_ratio": _round(equity_upper),
            "exact": stock_ratio is not None and not conflicts,
        },
        "industry_unknown_ratio": _round(industry_unknown),
        "stocks": stocks,
        "industries": industries,
        "quality": {
            "conflicts": conflicts,
            "stale_dimensions": stale_dimensions,
            "market_unknown_stock_ratio": _round(sum(
                item["ratio"] for item in stocks if item["market"] == "unknown"
            )),
        },
    }


def _aggregate_rows(accumulator: dict[str, dict[str, Any]], total: float) -> list[dict[str, Any]]:
    rows = []
    for key, row in accumulator.items():
        rows.append({
            **row,
            "lower_ratio": _round(row["lower_amount"] / total * 100 if total > 0 else None),
        })
    rows.sort(key=lambda item: item["lower_amount"], reverse=True)
    return rows


def build_exposure_snapshot(
    holdings: list[dict[str, Any]],
    raw_sources: dict[str, dict[str, Any]],
    *,
    target_code: str | None = None,
    failed_sources: list[dict[str, Any]] | None = None,
    profile_version_id: str | None = None,
    observed_on: dt.date | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict[str, Any]:
    observed_on = observed_on or dt.datetime.now(dt.timezone.utc).date()
    max_age_days = max(30, min(730, int(max_age_days)))
    target_code = str(target_code or "").strip() or None
    failed_sources = failed_sources or []
    normalized = {
        code: _normalize_source(source, observed_on, max_age_days)
        for code, source in raw_sources.items()
    }

    amount_complete = bool(holdings)
    total_amount = 0.0
    equity_lower_amount = 0.0
    equity_upper_amount = 0.0
    industry_unknown_amount = 0.0
    market_unknown_amount = 0.0
    industry_acc: dict[str, dict[str, Any]] = {}
    market_acc: dict[str, dict[str, Any]] = {}
    fund_rows = []
    missing_fund_codes = []
    source_conflict_codes = []
    stale_codes = []

    def add_market(market: str, amount: float, code: str, name: str) -> None:
        row = market_acc.setdefault(market, {
            "market": market,
            "lower_amount": 0.0,
            "contributors": [],
        })
        row["lower_amount"] += amount
        if amount > 0:
            row["contributors"].append({"code": code, "name": name, "amount": _round(amount)})

    for holding in holdings:
        amount = _number(holding.get("amount"))
        if amount is None or amount < 0:
            amount_complete = False
            continue
        if amount == 0:
            continue
        total_amount += amount
        asset_type = str(holding.get("asset_type") or "")
        code = str(holding.get("code") or "").strip()
        name = str(holding.get("name") or code)
        if asset_type == "stock":
            equity_lower_amount += amount
            equity_upper_amount += amount
            industry_unknown_amount += amount
            market = _holding_market(holding.get("market"))
            if market == "unknown":
                market = classify_security_market(code, name)
            if market == "unknown":
                market_unknown_amount += amount
            else:
                add_market(market, amount, code, name)
            continue
        if asset_type != "fund":
            equity_upper_amount += amount
            industry_unknown_amount += amount
            market_unknown_amount += amount
            continue

        source = normalized.get(code)
        if source is None:
            missing_fund_codes.append(code)
            equity_upper_amount += amount
            industry_unknown_amount += amount
            market_unknown_amount += amount
            fund_rows.append({
                "code": code,
                "name": name,
                "amount": _round(amount),
                "status": "unavailable",
            })
            continue
        quality = source["quality"]
        if quality["conflicts"]:
            source_conflict_codes.append(code)
        if quality["stale_dimensions"]:
            stale_codes.append(code)
        lower_ratio = source["equity_interval"]["lower_ratio"]
        upper_ratio = source["equity_interval"]["upper_ratio"]
        lower_ratio = 0.0 if lower_ratio is None else lower_ratio
        upper_ratio = 100.0 if upper_ratio is None else upper_ratio
        equity_lower_amount += amount * lower_ratio / 100
        equity_upper_amount += amount * upper_ratio / 100
        industry_unknown_amount += amount * (source["industry_unknown_ratio"] or 0) / 100

        classified_stock_ratio = 0.0
        for stock in source["stocks"]:
            contribution = amount * stock["ratio"] / 100
            if stock["market"] == "unknown":
                continue
            classified_stock_ratio += stock["ratio"]
            add_market(stock["market"], contribution, code, name)
        market_unknown_amount += amount * max(0.0, upper_ratio - classified_stock_ratio) / 100

        for industry in source["industries"]:
            contribution = amount * industry["ratio"] / 100
            row = industry_acc.setdefault(industry["name"], {
                "name": industry["name"],
                "lower_amount": 0.0,
                "contributors": [],
            })
            row["lower_amount"] += contribution
            row["contributors"].append({
                "code": code,
                "name": name,
                "amount": _round(contribution),
                "fund_disclosure_ratio": _round(industry["ratio"]),
            })
        fund_rows.append({
            "code": code,
            "name": source["name"] or name,
            "amount": _round(amount),
            "status": "loaded",
            "periods": source["periods"],
            "equity_interval": source["equity_interval"],
            "industry_unknown_ratio": source["industry_unknown_ratio"],
            "quality": source["quality"],
            "linked_fund": source.get("linked_fund"),
        })

    industries = _aggregate_rows(industry_acc, total_amount)
    for row in industries:
        row["upper_amount"] = _round(row["lower_amount"] + industry_unknown_amount)
        row["upper_ratio"] = _round(
            (row["lower_amount"] + industry_unknown_amount) / total_amount * 100
            if total_amount > 0 else None
        )
        row["lower_amount"] = _round(row["lower_amount"])
        row["contributors"] = sorted(
            row["contributors"], key=lambda item: item["amount"] or 0, reverse=True
        )[:10]
    markets = _aggregate_rows(market_acc, total_amount)
    for row in markets:
        row["upper_amount"] = _round(row["lower_amount"] + market_unknown_amount)
        row["upper_ratio"] = _round(
            (row["lower_amount"] + market_unknown_amount) / total_amount * 100
            if total_amount > 0 else None
        )
        row["lower_amount"] = _round(row["lower_amount"])
        row["contributors"] = sorted(
            row["contributors"], key=lambda item: item["amount"] or 0, reverse=True
        )[:10]

    target = None
    if target_code:
        source = normalized.get(target_code)
        if source:
            target_industry_rows = []
            unknown_ratio = source["industry_unknown_ratio"] or 0.0
            for row in source["industries"]:
                target_industry_rows.append({
                    "name": row["name"],
                    "lower_ratio": _round(row["ratio"]),
                    "upper_ratio": _round(min(100.0, row["ratio"] + unknown_ratio)),
                })
            target = {
                "status": "available",
                "code": target_code,
                "name": source["name"],
                "periods": source["periods"],
                "equity_interval": source["equity_interval"],
                "industry_unknown_ratio": source["industry_unknown_ratio"],
                "industries": target_industry_rows,
                "quality": source["quality"],
                "source": source["source"],
                "source_url": source["source_url"],
                "linked_fund": source.get("linked_fund"),
            }
        else:
            target = {"status": "unavailable", "code": target_code}

    target_unavailable = bool(target_code and (not target or target["status"] != "available"))
    target_stale = bool(target and (target.get("quality") or {}).get("stale_dimensions"))
    target_conflict = bool(target and (target.get("quality") or {}).get("conflicts"))
    reasons = []
    if not holdings:
        reasons.append("没有用户确认持仓，无法形成组合穿透快照。")
    if not amount_complete:
        reasons.append("至少一项持仓缺少有效金额，组合暴露区间不具备决策资格。")
    if missing_fund_codes:
        reasons.append(f"{len(set(missing_fund_codes))} 只持有基金的真实披露不可用。")
    if stale_codes:
        reasons.append(f"{len(set(stale_codes))} 只持有基金的披露超过期限或缺少可解析报告期。")
    if source_conflict_codes:
        reasons.append(f"{len(set(source_conflict_codes))} 只基金的资产配置与持仓披露存在数值冲突。")
    if target_unavailable:
        reasons.append("目标基金真实持仓披露不可用，不能验证新增资金的边际暴露。")
    if target_stale:
        reasons.append("目标基金披露超过决策期限或缺少可解析报告期。")
    if target_conflict:
        reasons.append("目标基金资产配置与持仓披露存在数值冲突。")
    if failed_sources:
        reasons.append(f"{len(failed_sources)} 个真实基金披露请求失败，未使用替代值补齐。")

    eligible = bool(
        holdings
        and amount_complete
        and total_amount > 0
        and not missing_fund_codes
        and not stale_codes
        and not source_conflict_codes
        and not target_unavailable
        and not target_stale
        and not target_conflict
        and not failed_sources
    )
    status = "complete" if eligible else "unavailable" if total_amount <= 0 else "partial"
    industry_max_lower = max([row["lower_ratio"] or 0 for row in industries] + [0.0])
    industry_max_upper = max(
        [row["upper_ratio"] or 0 for row in industries]
        + [industry_unknown_amount / total_amount * 100 if total_amount > 0 else 0.0]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "status": status,
        "evaluated_on": observed_on.isoformat(),
        "profile_version_id": profile_version_id,
        "target_code": target_code,
        "holdings_sha256": holdings_sha256(holdings),
        "source": "用户确认持仓 + 天天基金投资组合 + 东方财富基金资产配置",
        "summary": {
            "holding_count": len(holdings),
            "total_amount": _round(total_amount, 2) if total_amount > 0 else None,
            "equity": {
                "lower_amount": _round(equity_lower_amount, 2),
                "upper_amount": _round(equity_upper_amount, 2),
                "lower_ratio": _round(equity_lower_amount / total_amount * 100 if total_amount > 0 else None),
                "upper_ratio": _round(equity_upper_amount / total_amount * 100 if total_amount > 0 else None),
                "exact": abs(equity_upper_amount - equity_lower_amount) < 0.01,
            },
            "industry": {
                "unknown_equity_amount": _round(industry_unknown_amount, 2),
                "unknown_equity_ratio": _round(industry_unknown_amount / total_amount * 100 if total_amount > 0 else None),
                "max_lower_ratio": _round(industry_max_lower),
                "max_upper_ratio": _round(industry_max_upper),
            },
            "market": {
                "unknown_equity_amount": _round(market_unknown_amount, 2),
                "unknown_equity_ratio": _round(market_unknown_amount / total_amount * 100 if total_amount > 0 else None),
            },
        },
        "funds": fund_rows,
        "industries": industries,
        "markets": markets,
        "target": target,
        "failed_sources": failed_sources,
        "quality": {
            "decision_eligible": eligible,
            "amount_complete": amount_complete,
            "max_age_days": max_age_days,
            "missing_fund_codes": sorted(set(missing_fund_codes)),
            "stale_fund_codes": sorted(set(stale_codes)),
            "conflict_fund_codes": sorted(set(source_conflict_codes)),
            "market_classifier_version": MARKET_CLASSIFIER_VERSION,
            "reasons": reasons,
        },
        "method": {
            "equity": "资产配置股票占比存在时作为上下界；缺失时用已披露股票合计作下界、100% 作上界。",
            "industry": "已披露行业为下界；未分配权益可能全部集中于任一行业，因此加入每个行业最坏上界。",
            "market": "仅按可识别证券代码和用户确认市场归类；无法识别的权益保留为未知，不按基金名称猜测。",
            "freshness": "任一用于决策的基金披露缺少报告期或超过期限时，快照不得放行新增金额。",
        },
        "policy": "快照只使用用户确认金额和真实定期披露；未披露仓位进入风险上界，不使用模拟持仓或名称推断补齐。",
    }


def calculate_exposure_snapshot(
    *,
    target_code: str | None = None,
    profile_version_id: str | None = None,
    user_id: str = "default",
    provider: Callable[[str], dict[str, Any]] | None = None,
    observed_on: dt.date | None = None,
) -> dict[str, Any]:
    holdings, valuation = portfolio_valuation.current_valued_holdings(user_id=user_id)
    target_code = str(target_code or "").strip() or None
    if target_code and not re.fullmatch(r"\d{6}", target_code):
        raise ValueError("目标基金代码需要是 6 位数字")
    codes = sorted({
        str(item.get("code") or "").strip()
        for item in holdings
        if item.get("asset_type") == "fund"
        and (_number(item.get("amount")) or 0) > 0
        and re.fullmatch(r"\d{6}", str(item.get("code") or "").strip())
    } | ({target_code} if target_code else set()))
    if len(codes) > MAX_FUND_SOURCES:
        raise ValueError(f"单个快照最多处理 {MAX_FUND_SOURCES} 只基金，请先清理失效持仓")
    if provider is None:
        import funds
        provider = funds.get_fund_portfolio

    raw_sources: dict[str, dict[str, Any]] = {}
    failed = []

    def load(code: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            value = provider(code)
            if not isinstance(value, dict):
                raise TypeError("基金披露工具返回格式异常")
            return code, value, None
        except Exception as exc:
            return code, None, str(exc)[:240]

    if codes:
        with ThreadPoolExecutor(max_workers=min(4, len(codes)), thread_name_prefix="exposure") as pool:
            for code, value, error in pool.map(load, codes):
                if value is not None:
                    raw_sources[code] = value
                else:
                    failed.append({"code": code, "error": error or "真实披露不可用"})

    max_age_days = int(os.environ.get("PORTFOLIO_EXPOSURE_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS))
    result = build_exposure_snapshot(
        holdings,
        raw_sources,
        target_code=target_code,
        failed_sources=failed,
        profile_version_id=profile_version_id,
        observed_on=observed_on,
        max_age_days=max_age_days,
    )
    result["valuation_binding"] = {
        "snapshot_id": (valuation.get("snapshot") or {}).get("id"),
        "current": (valuation.get("binding") or {}).get("current", False),
        "risk_analysis_eligible": (valuation.get("runtime_gate") or {}).get(
            "risk_analysis_eligible", False
        ),
    }
    if holdings and not result["valuation_binding"]["risk_analysis_eligible"]:
        reasons = (valuation.get("runtime_gate") or {}).get("reasons") or [
            "估值快照缺失、过期或未绑定当前持仓"
        ]
        result["status"] = "partial"
        result["quality"]["decision_eligible"] = False
        result["quality"]["reasons"] = list(dict.fromkeys([
            *(result["quality"].get("reasons") or []),
            *(f"组合估值:{item}" for item in reasons),
        ]))
        result["method"]["valuation"] = (
            "组合暴露金额必须绑定当前不可变人民币估值；门禁失败时结果只供数据修复，不能用于金额决策。"
        )
    return result


def persist_exposure_snapshot(payload: dict[str, Any], *, user_id: str = "default") -> dict[str, Any]:
    saved = storage.save_portfolio_exposure_snapshot(payload, user_id=user_id)
    integrity = storage.verify_portfolio_exposure_snapshot(saved["id"], user_id=user_id)
    return {**payload, "snapshot": saved, "integrity": integrity}


def refresh_exposure_snapshot(
    *,
    target_code: str | None = None,
    profile_version_id: str | None = None,
    user_id: str = "default",
    provider: Callable[[str], dict[str, Any]] | None = None,
    observed_on: dt.date | None = None,
) -> dict[str, Any]:
    payload = calculate_exposure_snapshot(
        target_code=target_code,
        profile_version_id=profile_version_id,
        user_id=user_id,
        provider=provider,
        observed_on=observed_on,
    )
    return persist_exposure_snapshot(payload, user_id=user_id)
