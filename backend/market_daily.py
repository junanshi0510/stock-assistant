# -*- coding: utf-8 -*-
"""
Market opportunity daily report.

This module only aggregates real upstream data already used by the project:
- A-share sector and concept heat from sectors.py
- Eastmoney/Tiantian fund ranking and opportunity data from funds.py
- Real stock hot lists from hot_stocks.py

If a source is unavailable, the response records it in `failed`; it does not
generate synthetic replacement rows.
"""

from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

import funds
import hot_stocks
import sectors


_CACHE_TTL = 300
_cache: dict[tuple, tuple[float, dict]] = {}


def _cache_get(key):
    item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_put(key, value):
    _cache[key] = (time.time(), value)


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits=2):
    n = _num(value)
    return round(n, digits) if n is not None else None


def _pct_text(value):
    n = _num(value)
    return "-" if n is None else f"{n:+.2f}%"


def _safe_result(name: str, fn):
    try:
        return name, fn(), None
    except Exception as exc:
        return name, None, {"source": name, "error": str(exc)[:220]}


def _industry_card(row: dict) -> dict:
    leaders = row.get("leaders") or []
    leader = leaders[0] if leaders else {}
    avg_change = row.get("avg_change_pct")
    up_ratio = row.get("up_ratio")
    evidence = []
    if avg_change is not None:
        evidence.append(f"行业平均涨跌幅 {_pct_text(avg_change)}")
    if up_ratio is not None:
        evidence.append(f"上涨占比 {_pct_text(up_ratio)}")
    if row.get("total_amount_yi") is not None:
        evidence.append(f"成交额 {row['total_amount_yi']:.2f} 亿")
    if leader:
        evidence.append(f"领涨股 {leader.get('name') or leader.get('symbol')} {_pct_text(leader.get('change_pct'))}")

    if (avg_change or 0) >= 2.5 and (up_ratio or 0) >= 70:
        label = "强势扩散"
    elif (avg_change or 0) > 0 and (up_ratio or 0) >= 55:
        label = "偏强轮动"
    elif (avg_change or 0) < 0:
        label = "回落观察"
    else:
        label = "中性观察"

    driver_labels = []
    concept_hype_count = 0
    profit_supported_count = 0
    for item in leaders[:5]:
        driver = item.get("driver") or {}
        if driver.get("label"):
            driver_labels.append(driver["label"])
        if driver.get("concept_hype"):
            concept_hype_count += 1
        if driver.get("profit_supported"):
            profit_supported_count += 1

    if concept_hype_count and concept_hype_count >= profit_supported_count:
        driver_note = "领涨股更偏资金/概念驱动，需要防止短线退潮。"
    elif profit_supported_count:
        driver_note = "部分领涨股存在盈利指标支撑，值得继续核验财报质量。"
    else:
        driver_note = "暂未形成明确盈利支撑结论，继续看量价延续性。"

    return {
        "type": "industry",
        "name": row.get("name") or "",
        "label": label,
        "score": row.get("heat_score"),
        "change_pct": avg_change,
        "up_ratio": up_ratio,
        "turnover": row.get("avg_turnover"),
        "evidence": evidence,
        "driver_note": driver_note,
        "driver_labels": list(dict.fromkeys(driver_labels))[:4],
        "leaders": [{
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "change_pct": item.get("change_pct"),
            "turnover": item.get("turnover"),
            "driver": (item.get("driver") or {}).get("label", ""),
        } for item in leaders[:5]],
    }


def _concept_card(row: dict) -> dict:
    change = row.get("change_pct")
    evidence = []
    if change is not None:
        evidence.append(f"概念涨跌幅 {_pct_text(change)}")
    if row.get("up_count") is not None and row.get("down_count") is not None:
        evidence.append(f"上涨/下跌家数 {row.get('up_count')}/{row.get('down_count')}")
    if row.get("leader"):
        leader_text = str(row.get("leader"))
        if row.get("leader_change_pct") is not None:
            leader_text += f" {_pct_text(row.get('leader_change_pct'))}"
        evidence.append(f"领涨线索 {leader_text}")
    if row.get("event"):
        evidence.append(str(row.get("event"))[:80])

    if change is not None and change >= 4:
        label = "短线高热"
    elif change is not None and change > 0:
        label = "概念活跃"
    elif row.get("event"):
        label = "事件驱动"
    else:
        label = "观察"

    return {
        "type": "concept",
        "name": row.get("name") or "",
        "label": label,
        "score": change,
        "change_pct": change,
        "leader": row.get("leader") or "",
        "leader_change_pct": row.get("leader_change_pct"),
        "event": row.get("event") or "",
        "date": row.get("date") or "",
        "evidence": evidence,
    }


def _fund_category_card(row: dict) -> dict:
    avg_1m = row.get("avg_1m")
    avg_3m = row.get("avg_3m")
    if (avg_1m or 0) >= 6 and (avg_3m or 0) >= 12:
        label = "基金分类偏热"
    elif (avg_3m or 0) > 0:
        label = "基金分类转强"
    elif (avg_1m or 0) < 0 and (avg_3m or 0) < 0:
        label = "基金分类降温"
    else:
        label = "基金分类中性"
    return {
        "type": "fund_category",
        "name": row.get("name") or row.get("category") or "",
        "category": row.get("category"),
        "label": label,
        "score": _round((avg_1m or 0) * 0.45 + (avg_3m or 0) * 0.35 + (row.get("avg_1y") or 0) * 0.2),
        "avg_1m": avg_1m,
        "avg_3m": avg_3m,
        "avg_1y": row.get("avg_1y"),
        "leader_code": row.get("leader_code"),
        "leader_name": row.get("leader_name"),
        "evidence": [
            f"近1月均值 {_pct_text(avg_1m)}",
            f"近3月均值 {_pct_text(avg_3m)}",
            f"领涨基金 {row.get('leader_name') or '-'}",
        ],
    }


def _fund_candidate_card(row: dict) -> dict:
    return {
        "type": "fund_candidate",
        "name": row.get("name") or "",
        "code": row.get("code"),
        "bucket": row.get("bucket_name"),
        "label": row.get("cautions", ["观察"])[-1] if row.get("cautions") else "观察",
        "score": row.get("opportunity_score"),
        "return_1m": row.get("return_1m"),
        "return_3m": row.get("return_3m"),
        "return_1y": row.get("return_1y"),
        "scale_yi": row.get("scale_yi"),
        "evidence": (row.get("reasons") or [])[:3],
        "cautions": (row.get("cautions") or [])[:3],
    }


def _stock_list_payload(data: dict | None, market: str, list_type: str) -> dict:
    data = data or {}
    return {
        "market": market,
        "type": list_type,
        "scope": data.get("scope") or "",
        "items": (data.get("items") or [])[:8],
        "count": data.get("count") or 0,
    }


def _build_risks(industry_cards: list[dict], concept_cards: list[dict], fund_cards: list[dict], hot_losers: list[dict]) -> list[dict]:
    risks = []
    for item in concept_cards[:3]:
        if (item.get("change_pct") or 0) >= 5:
            risks.append({
                "level": "high",
                "title": f"{item['name']} 短线过热",
                "text": "概念涨幅较快，若缺少盈利验证，追涨容错率会下降。",
            })
    for item in industry_cards[:3]:
        if item.get("label") == "强势扩散" and item.get("driver_note", "").startswith("领涨股更偏"):
            risks.append({
                "level": "medium",
                "title": f"{item['name']} 偏概念驱动",
                "text": item.get("driver_note"),
            })
    for item in fund_cards[:5]:
        if (item.get("avg_1m") or 0) >= 8:
            risks.append({
                "level": "medium",
                "title": f"{item['name']} 基金短期涨幅较快",
                "text": "分类近1月平均收益偏高，新增资金更适合分批而不是一次性追入。",
            })
    for item in hot_losers[:3]:
        if (item.get("change_pct") or 0) <= -5:
            risks.append({
                "level": "medium",
                "title": f"{item.get('name') or item.get('symbol')} 跌幅较大",
                "text": f"真实榜单显示当日跌幅 {_pct_text(item.get('change_pct'))}，相关持仓需要检查是否受同类风险拖累。",
            })
    return risks[:8]


def get_market_daily(risk: str = "balanced", fund_limit: int = 4) -> dict:
    risk = str(risk or "balanced").strip()
    if risk not in ("stable", "balanced", "aggressive"):
        raise ValueError(f"不支持的风险偏好:{risk}")
    fund_limit = max(3, min(8, int(fund_limit)))
    cache_key = ("market_daily", risk, fund_limit)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    tasks = {
        "sector_analysis": lambda: sectors.get_sector_analysis("A股", sector_limit=8, stock_limit=5, include_concepts=True),
        "fund_categories": funds.get_fund_categories,
        "fund_opportunities": lambda: funds.get_fund_opportunities(risk=risk, limit=fund_limit),
        "a_gainers": lambda: hot_stocks.get_hot_stocks("A股", "1d", "gainers", 10),
        "a_losers": lambda: hot_stocks.get_hot_stocks("A股", "1d", "losers", 10),
        "hk_gainers": lambda: hot_stocks.get_hot_stocks("港股", "1d", "gainers", 8),
        "us_gainers": lambda: hot_stocks.get_hot_stocks("美股", "1d", "gainers", 8),
    }

    data = {}
    failed = []
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(_safe_result, name, fn) for name, fn in tasks.items()]
        for fut in futures:
            name, payload, error = fut.result()
            if error:
                failed.append(error)
            else:
                data[name] = payload

    sector_data = data.get("sector_analysis") or {}
    industries = ((sector_data.get("industries") or {}).get("items") or [])[:8]
    concepts_data = sector_data.get("concepts") or {}
    concepts = concepts_data.get("items") or []
    if concepts_data and not concepts_data.get("available", True):
        failed.append({"source": concepts_data.get("source", "concept_boards"), "error": concepts_data.get("error", "真实概念板块源不可用")})

    industry_cards = [_industry_card(row) for row in industries]
    concept_cards = [_concept_card(row) for row in concepts[:8]]
    fund_category_cards = [_fund_category_card(row) for row in (data.get("fund_categories", {}).get("items") or [])]
    fund_category_cards.sort(key=lambda x: x.get("score") if x.get("score") is not None else -999, reverse=True)

    fund_opp = data.get("fund_opportunities") or {}
    fund_candidates = [_fund_candidate_card(row) for row in (fund_opp.get("top_items") or [])[:10]]

    hot_losers = (data.get("a_losers") or {}).get("items") or []
    risks = _build_risks(industry_cards, concept_cards, fund_category_cards, hot_losers)

    top_industry = industry_cards[0] if industry_cards else None
    top_concept = concept_cards[0] if concept_cards else None
    top_fund_category = fund_category_cards[0] if fund_category_cards else None
    top_candidate = fund_candidates[0] if fund_candidates else None
    headline_parts = []
    if top_industry:
        headline_parts.append(f"A股热度集中在 {top_industry['name']}")
    if top_fund_category:
        headline_parts.append(f"基金分类关注 {top_fund_category['name']}")
    if top_candidate:
        headline_parts.append(f"候选基金 {top_candidate.get('code')} {top_candidate.get('name')}")
    headline = "；".join(headline_parts) if headline_parts else "真实市场源暂未形成可聚合机会"

    opportunity_count = len(industry_cards) + len(concept_cards) + len(fund_candidates)
    if top_industry and (top_industry.get("change_pct") or 0) >= 2:
        temperature = "偏热"
    elif top_industry and (top_industry.get("change_pct") or 0) < 0:
        temperature = "偏冷"
    else:
        temperature = "中性"

    if opportunity_count == 0 and len(failed) == len(tasks):
        raise RuntimeError("真实市场机会日报数据当前均不可用")

    result = {
        "source": "真实市场机会日报聚合",
        "source_detail": [
            "A股行业/概念热度",
            "东方财富/天天基金基金排行",
            "真实股票热门榜",
        ],
        "risk": risk,
        "as_of": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "headline": headline,
            "temperature": temperature,
            "opportunity_count": opportunity_count,
            "risk_count": len(risks),
            "failed_count": len(failed),
            "top_industry": top_industry,
            "top_concept": top_concept,
            "top_fund_category": top_fund_category,
        },
        "industries": industry_cards,
        "concepts": concept_cards,
        "fund_categories": fund_category_cards[:8],
        "fund_candidates": fund_candidates,
        "hot_stocks": [
            _stock_list_payload(data.get("a_gainers"), "A股", "gainers"),
            _stock_list_payload(data.get("a_losers"), "A股", "losers"),
            _stock_list_payload(data.get("hk_gainers"), "港股", "gainers"),
            _stock_list_payload(data.get("us_gainers"), "美股", "gainers"),
        ],
        "risks": risks,
        "failed": failed[:10],
        "method": {
            "aggregation": "日报只聚合真实上游返回的数据；部分源不可用时记录 failed，不生成替代假数据。",
            "opportunity": "机会排序来自真实涨跌幅、上涨占比、基金分类收益和基金机会评分。",
            "risk": "风险提示基于真实短期涨幅、跌幅、概念热度和基金分类涨幅，不代表未来预测。",
        },
    }
    _cache_put(cache_key, result)
    return result
