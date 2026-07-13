# -*- coding: utf-8 -*-
"""Real fund look-through, market pulse, sector and news intelligence.

No synthetic rows are produced. Missing providers are recorded in `failed` and
the result quality is narrowed accordingly.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

from curl_cffi import requests

import data_fetch  # Applies the project's pandas/provider compatibility policy.
import funds
import quotes
import sectors
import sentiment


_CACHE_TTL_SECONDS = 300
_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    with _cache_lock:
        item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL_SECONDS:
        return item[1]
    return None


def _cache_put(key: tuple[Any, ...], value: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), value)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _num(value)
    return round(number, digits) if number is not None else None


def _clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _market_for_holding(code: str, primary_market: str) -> str | None:
    value = str(code or "").strip().upper()
    if re.fullmatch(r"\d{5}", value):
        return "港股"
    if re.fullmatch(r"\d{6}", value):
        return "A股"
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,12}", value):
        return "美股"
    return {
        "mainland": "A股",
        "hong_kong": "港股",
        "united_states": "美股",
    }.get(primary_market)


def _eastmoney_news(symbol: str, holding_name: str, limit: int) -> list[dict[str, Any]]:
    callback = "jQuery35101792940631092459_1764599530165"
    inner_param = {
        "uid": "",
        "keyword": symbol,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": max(6, min(10, limit * 2)),
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    response = requests.get(
        "https://search-api-web.eastmoney.com/search/jsonp",
        params={
            "cb": callback,
            "param": json.dumps(inner_param, ensure_ascii=False),
            "_": "1764599530176",
        },
        headers={
            "accept": "*/*",
            "accept-language": "en,zh-CN;q=0.9,zh;q=0.8",
            "cache-control": "no-cache",
            "cookie": (
                "qgqp_b_id=652bf4c98a74e210088f372a17d4e27b; "
                "emshistory=%5B%22603777%22%5D; st_sn=2"
            ),
            "pragma": "no-cache",
            "referer": "https://so.eastmoney.com/news/s?keyword=603777",
            "sec-fetch-dest": "script",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "same-site",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
        },
        timeout=8,
    )
    response.raise_for_status()
    raw_text = response.text.strip()
    match = re.fullmatch(
        r"[A-Za-z_$][A-Za-z0-9_$]*\((.*)\)\s*;?",
        raw_text,
        flags=re.S,
    )
    if not match:
        raise ValueError("东方财富新闻接口返回了无法解析的 JSONP")
    payload = json.loads(match.group(1))
    source_rows = ((payload.get("result") or {}).get("cmsArticleWebOld") or [])
    rows = []
    for item in source_rows:
        article_code = _clean_text(item.get("code"), 80)
        rows.append({
            "symbol": symbol,
            "holding_name": holding_name,
            "title": _clean_text(item.get("title"), 180),
            "summary": _clean_text(item.get("content"), 420),
            "published_at": _clean_text(item.get("date"), 40),
            "publisher": _clean_text(item.get("mediaName"), 80),
            "url": (
                f"https://finance.eastmoney.com/a/{article_code}.html"
                if article_code
                else ""
            ),
            "provider": "东方财富个股新闻聚合",
            "provider_sentiment": None,
            "untrusted_external_content": True,
        })
    rows = [item for item in rows if item["title"]]
    rows.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return rows[:limit]


def _alpha_news(symbol: str, holding_name: str, limit: int) -> list[dict[str, Any]]:
    payload = sentiment.get_sentiment("美股", symbol)
    if not payload.get("available"):
        return []
    rows = []
    for item in (payload.get("news") or [])[:limit]:
        rows.append({
            "symbol": symbol,
            "holding_name": holding_name,
            "title": _clean_text(item.get("title"), 180),
            "summary": "",
            "published_at": _clean_text(item.get("time"), 40),
            "publisher": _clean_text(item.get("source"), 80),
            "url": _clean_text(item.get("url"), 500),
            "provider": "Alpha Vantage NEWS_SENTIMENT",
            "provider_sentiment": _num(item.get("score")),
            "untrusted_external_content": True,
        })
    return [item for item in rows if item["title"]]


def _news_for_holding(
    market: str,
    symbol: str,
    holding_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    if market in {"A股", "港股"}:
        return _eastmoney_news(symbol, holding_name, limit)
    if market == "美股":
        return _alpha_news(symbol, holding_name, limit)
    return []


def _holding_snapshot(
    holding: dict[str, Any],
    primary_market: str,
    news_limit: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    code = str(holding.get("code") or "").strip()
    name = str(holding.get("name") or code).strip()
    market = _market_for_holding(code, primary_market)
    failures: list[dict[str, str]] = []
    quote_payload = None
    news_items: list[dict[str, Any]] = []
    if not market:
        failures.append({
            "component": "holding_market",
            "subject": code or name,
            "error": "无法从真实披露代码识别持仓市场",
        })
    else:
        try:
            quote_payload = quotes.get_quote(market, code)
        except Exception as error:
            failures.append({
                "component": "holding_quote",
                "subject": f"{market}:{code}",
                "error": str(error)[:220],
            })
        try:
            news_items = _news_for_holding(market, code, name, news_limit)
            if not news_items:
                failures.append({
                    "component": "holding_news",
                    "subject": f"{market}:{code}",
                    "error": "真实新闻源没有返回近期条目",
                })
        except Exception as error:
            failures.append({
                "component": "holding_news",
                "subject": f"{market}:{code}",
                "error": str(error)[:220],
            })
    return {
        "code": code,
        "name": name,
        "market": market,
        "nav_ratio": _round(holding.get("nav_ratio"), 4),
        "quote": (
            {
                key: quote_payload.get(key)
                for key in (
                    "source",
                    "price",
                    "change_pct",
                    "amount",
                    "pe",
                    "market_cap",
                    "as_of",
                    "delay_note",
                )
            }
            if quote_payload
            else None
        ),
        "news": news_items,
    }, failures


def _sector_snapshot() -> dict[str, Any]:
    payload = sectors.get_sector_analysis(
        market="A股",
        sector_limit=6,
        stock_limit=3,
        include_concepts=True,
    )
    industries = ((payload.get("industries") or {}).get("items") or [])[:6]
    concepts = ((payload.get("concepts") or {}).get("items") or [])[:6]
    return {
        "status": "available",
        "source": payload.get("source"),
        "as_of": payload.get("as_of"),
        "industries": [
            {
                key: row.get(key)
                for key in (
                    "name",
                    "heat_score",
                    "avg_change_pct",
                    "up_ratio",
                    "avg_turnover",
                    "total_amount_yi",
                )
            }
            for row in industries
        ],
        "concepts": [
            {
                key: row.get(key)
                for key in (
                    "name",
                    "change_pct",
                    "up_count",
                    "down_count",
                    "leader",
                    "leader_change_pct",
                    "event",
                    "date",
                )
            }
            for row in concepts
        ],
    }


def _weighted_holding_pulse(items: list[dict[str, Any]]) -> dict[str, Any]:
    disclosed = sum(_num(item.get("nav_ratio")) or 0 for item in items)
    quoted = [item for item in items if (item.get("quote") or {}).get("change_pct") is not None]
    quoted_weight = sum(_num(item.get("nav_ratio")) or 0 for item in quoted)
    weighted_change = None
    advancing_weight = None
    if quoted_weight > 0:
        weighted_change = sum(
            (_num(item.get("nav_ratio")) or 0)
            * (_num((item.get("quote") or {}).get("change_pct")) or 0)
            for item in quoted
        ) / quoted_weight
        advancing_weight = sum(
            _num(item.get("nav_ratio")) or 0
            for item in quoted
            if (_num((item.get("quote") or {}).get("change_pct")) or 0) > 0
        ) / quoted_weight * 100
    return {
        "status": "available" if quoted else "unavailable",
        "disclosed_top_holding_ratio": _round(disclosed),
        "quoted_holding_ratio": _round(quoted_weight),
        "quote_coverage_pct_of_selected": _round(quoted_weight / disclosed * 100) if disclosed else None,
        "weighted_change_pct": _round(weighted_change),
        "advancing_weight_pct": _round(advancing_weight),
        "quoted_count": len(quoted),
        "selected_count": len(items),
    }


def get_fund_intelligence(
    code: str,
    *,
    holding_limit: int = 4,
    news_per_holding: int = 3,
) -> dict[str, Any]:
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("基金代码需要是 6 位数字")
    holding_limit = max(2, min(6, int(holding_limit)))
    news_per_holding = max(1, min(5, int(news_per_holding)))
    key = ("fund_intelligence", code, holding_limit, news_per_holding)
    cached = _cache_get(key)
    if cached:
        return cached

    failed: list[dict[str, str]] = []
    profile = None
    portfolio = None
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fund-intelligence-base")
    futures = {
        pool.submit(funds.get_fund_market_profile, code): "market_profile",
        pool.submit(funds.get_fund_portfolio, code): "fund_portfolio",
    }
    try:
        done, pending = wait(futures, timeout=55)
        for future in done:
            name = futures[future]
            try:
                value = future.result()
                if name == "market_profile":
                    profile = value
                else:
                    portfolio = value
            except Exception as error:
                failed.append({"component": name, "subject": code, "error": str(error)[:220]})
        for future in pending:
            name = futures[future]
            future.cancel()
            failed.append({
                "component": name,
                "subject": code,
                "error": "真实数据源在 55 秒内未返回",
            })
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if not portfolio:
        result = {
            "status": "unavailable",
            "source": "真实基金情报聚合",
            "code": code,
            "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "reason": "基金底层持仓披露不可用，无法形成持仓、行情和新闻上下文。",
            "failed": failed,
            "news": {"items": [], "count": 0, "untrusted_external_content": True},
            "policy": "缺少真实底层披露时不猜测持仓，也不生成新闻或市场替代数据。",
        }
        _cache_put(key, result)
        return result

    primary_market = str(((profile or {}).get("market") or {}).get("primary") or "")
    selected = (portfolio.get("stocks") or [])[:holding_limit]
    holding_items: list[dict[str, Any]] = []
    holding_pool = ThreadPoolExecutor(
        max_workers=max(1, min(4, len(selected))),
        thread_name_prefix="fund-intelligence-holding",
    )
    holding_futures = {
        holding_pool.submit(_holding_snapshot, row, primary_market, news_per_holding): row
        for row in selected
    }
    try:
        done, pending = wait(holding_futures, timeout=28)
        for future in done:
            row = holding_futures[future]
            try:
                item, item_failed = future.result()
                holding_items.append(item)
                failed.extend(item_failed)
            except Exception as error:
                failed.append({
                    "component": "holding_intelligence",
                    "subject": str(row.get("code") or row.get("name") or ""),
                    "error": str(error)[:220],
                })
        for future in pending:
            row = holding_futures[future]
            future.cancel()
            failed.append({
                "component": "holding_intelligence",
                "subject": str(row.get("code") or row.get("name") or ""),
                "error": "持仓行情或新闻在 28 秒内未返回",
            })
    finally:
        holding_pool.shutdown(wait=False, cancel_futures=True)
    selected_order = {str(row.get("code") or ""): index for index, row in enumerate(selected)}
    holding_items.sort(key=lambda item: selected_order.get(str(item.get("code") or ""), 999))

    sector_pulse = {
        "status": "unavailable",
        "reason": "当前仅有 A 股实时行业/概念板块工具；跨境基金使用真实披露行业和底层持仓行情，不猜测海外板块热度。",
        "industries": [],
        "concepts": [],
    }
    if primary_market == "mainland":
        sector_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fund-intelligence-sector")
        future = sector_pool.submit(_sector_snapshot)
        try:
            done, _ = wait({future}, timeout=18)
            if done:
                sector_pulse = future.result()
            else:
                future.cancel()
                failed.append({
                    "component": "sector_pulse",
                    "subject": "A股",
                    "error": "真实板块源在 18 秒内未返回",
                })
        except Exception as error:
            failed.append({"component": "sector_pulse", "subject": "A股", "error": str(error)[:220]})
        finally:
            sector_pool.shutdown(wait=False, cancel_futures=True)

    news_items = [article for item in holding_items for article in (item.get("news") or [])]
    news_items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    news_items = news_items[: max(8, holding_limit * news_per_holding)]
    quote_pulse = _weighted_holding_pulse(holding_items)
    news_covered_symbols = {item.get("symbol") for item in news_items if item.get("symbol")}
    quote_coverage = _num(quote_pulse.get("quote_coverage_pct_of_selected")) or 0
    if holding_items and quote_coverage >= 60 and news_items:
        status = "available"
    else:
        status = "partial"
    result = {
        "status": status,
        "source": "基金定期披露 + 腾讯证券单股行情 + 授权新闻源",
        "source_url": portfolio.get("source_url"),
        "code": code,
        "name": portfolio.get("name") or ((profile or {}).get("fund") or {}).get("name"),
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "market": {
            "primary": primary_market,
            "label": ((profile or {}).get("market") or {}).get("label"),
            "cross_border": ((profile or {}).get("market") or {}).get("cross_border"),
            "currency_risk": ((profile or {}).get("market") or {}).get("currency_risk"),
        },
        "portfolio_disclosure": {
            "source": portfolio.get("source"),
            "source_url": portfolio.get("source_url"),
            "stock_period": portfolio.get("stock_period"),
            "bond_period": portfolio.get("bond_period"),
            "industry_period": portfolio.get("industry_period"),
            "asset_period": portfolio.get("asset_period"),
            "asset_allocation": portfolio.get("asset_allocation") or {},
            "summary": portfolio.get("summary") or {},
            "industries": (portfolio.get("industries") or [])[:8],
        },
        "holding_pulse": {
            **quote_pulse,
            "items": [
                {key: item.get(key) for key in ("code", "name", "market", "nav_ratio", "quote")}
                for item in holding_items
            ],
        },
        "sector_pulse": sector_pulse,
        "news": {
            "items": news_items,
            "count": len(news_items),
            "covered_holding_count": len(news_covered_symbols),
            "selected_holding_count": len(holding_items),
            "publishers": sorted({item.get("publisher") for item in news_items if item.get("publisher")}),
            "untrusted_external_content": True,
            "interpretation_policy": "新闻由大模型按不可信外部数据解释；新闻不单独触发投资动作。",
        },
        "quality": {
            "status": status,
            "selected_holding_count": len(selected),
            "completed_holding_count": len(holding_items),
            "quoted_holding_count": quote_pulse.get("quoted_count"),
            "news_count": len(news_items),
            "failed_component_count": len(failed),
        },
        "failed": failed[:20],
        "method": {
            "lookthrough": "使用基金最新可得定期报告；联接基金先穿透目标 ETF，再按披露比例缩放。",
            "market_pulse": "按所选前列持仓的真实行情和披露占比计算加权脉冲，不等于基金盘中净值。",
            "sector": "内地基金可读取 A 股实时行业/概念；跨境基金只使用披露行业与持仓行情。",
            "news": "A/H 股使用东方财富个股新闻聚合，保留原发布机构；美股仅在配置 Alpha Vantage Key 时使用 NEWS_SENTIMENT。",
        },
        "policy": "定期披露存在滞后；行情与新闻只解释当前风险和催化剂，不把持仓披露冒充实时仓位，不把新闻情绪冒充涨跌预测。",
    }
    _cache_put(key, result)
    return result
