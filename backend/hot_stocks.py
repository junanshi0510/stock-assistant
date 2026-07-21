# -*- coding: utf-8 -*-
"""
热门股票 / 涨跌幅榜抓取
======================
提供各市场的热门股票榜单：A股/港股使用东方财富公开榜单，美股使用
Yahoo Finance 预定义筛选器与批量 Spark 日线。不使用新浪作为榜单回退源；
请求失败时优先返回最近一次成功缓存，并明确标注陈旧状态。

榜单类型(type):
    gainers  涨幅榜(涨得最快)
    losers   跌幅榜(跌得最多)
    active   成交活跃(成交额最大)

周期(period):
    1d   当日:东财全市场【服务端排序】,快且完整
    7d   近 7 个交易日
    30d  近 30 个交易日
         (7d/30d 东财榜单没有现成的多日排序字段,所以先取"成交额最大"的活跃股
          作为候选池,再对候选池逐只抓日K算真实 N 日涨幅后排序 —— 因此这两个周期
          是"活跃股范围内"的排名,不是全市场,已在前端如实标注。)

返回结构统一为:
    {"symbol","name","price","change_pct","volume","secid"}
其中 secid = "{市场号}.{代码}",可直接喂给东财K线/详情接口。
"""

from concurrent.futures import ThreadPoolExecutor
import copy
import datetime
import threading
import time

import requests
import data_fetch

_TIMEOUT = 10
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_YAHOO_SCREENER_URL = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
_YAHOO_SPARK_URL = "https://query2.finance.yahoo.com/v7/finance/spark"
_YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
_CACHE_TTL = {"1d": 60, "7d": 300, "30d": 600}
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

# 各市场的东财板块过滤串(fs)
_MARKET_FS = {
    "A股": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 深主板+创业板+沪主板+科创板
    "港股": "m:116,m:113,m:114,m:115",             # 港股主板/创业板等
    "美股": "m:105,m:106,m:107",                    # 美股各交易所
}

# 周期 -> 交易日数(近似:一周约 5 个交易日,一月约 20 个)
_PERIOD_DAYS = {"7d": 7, "30d": 30}
_VALID_TYPES = ("gainers", "losers", "active")


def get_hot_stocks(market: str, period: str = "1d",
                   type_: str = "gainers", limit: int = 20) -> dict:
    """
    获取热门股票榜单。

    Returns:
        {"market","period","type","scope","items":[...],"count"}
        scope 说明该榜单是"全市场"还是"活跃股范围内"。
    """
    if market not in _MARKET_FS:
        raise ValueError(f"不支持的市场:{market}")
    if type_ not in _VALID_TYPES:
        raise ValueError(f"不支持的类型:{type_}")
    if period not in ("1d", "7d", "30d"):
        raise ValueError(f"不支持的周期:{period}")

    limit = max(5, min(50, limit))
    cache_key = (market, period, type_, limit)
    with _cache_lock:
        cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL[period]:
        return copy.deepcopy(cached[1])

    try:
        if period == "1d":
            items = _hot_1d(market, type_, limit)
            scope = "全市场"
        else:
            items = _hot_multiday(market, type_, _PERIOD_DAYS[period], limit)
            scope = "活跃股范围内"
    except Exception:
        if cached:
            stale = copy.deepcopy(cached[1])
            stale["stale"] = True
            stale["warning"] = "实时榜单暂不可用，当前展示最近一次成功缓存。"
            return stale
        raise

    result = {
        "market": market,
        "period": period,
        "type": type_,
        "scope": scope,
        "source": "Yahoo Finance" if market == "美股" else "东方财富",
        "retrieved_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "stale": False,
        "methodology": (
            ("Yahoo Finance 美股预定义筛选器" if market == "美股" else "东方财富全市场服务端实时排序")
            if period == "1d"
            else (
                "Yahoo Finance 成交活跃候选池内，按批量日K计算区间涨跌"
                if market == "美股"
                else "东方财富成交活跃候选池内，按真实日K计算区间涨跌"
            )
        ),
        "items": items,
        "count": len(items),
    }
    with _cache_lock:
        _cache[cache_key] = (time.time(), copy.deepcopy(result))
    return result


def _clist(market: str, fid: str, po: int, limit: int) -> list[dict]:
    """调用东财榜单接口并规整字段。fid=排序字段,po=1 降序/0 升序。"""
    params = {
        "pn": 1, "pz": limit, "po": po, "np": 1, "ut": _UT,
        "fltt": 2, "invt": 2, "fid": fid, "fs": _MARKET_FS[market],
        "fields": "f2,f3,f5,f6,f12,f13,f14",
    }
    resp = requests.get(_CLIST_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    diff = (data.get("data") or {}).get("diff")
    if not diff:
        raise Exception("东方财富榜单返回空数据")

    out = []
    for it in diff[:limit]:
        code = it.get("f12", "")
        mkt = it.get("f13", "")
        out.append({
            "symbol": code,
            "name": it.get("f14", ""),
            "price": _num(it.get("f2")),
            "change_pct": _num(it.get("f3")),
            "volume": _num(it.get("f5")),
            "secid": f"{mkt}.{code}",
        })
    return out


def _hot_1d(market: str, type_: str, limit: int) -> list[dict]:
    """当日榜单:服务端排序,全市场。"""
    if market == "美股":
        return _yahoo_us_1d(type_, limit)
    if type_ == "gainers":
        return _clist(market, "f3", 1, limit)
    if type_ == "losers":
        return _clist(market, "f3", 0, limit)
    return _clist(market, "f6", 1, limit)


def _hot_multiday(market: str, type_: str, days: int, limit: int) -> list[dict]:
    """
    多日榜单:先取成交额最大的活跃候选池(~50 只),再并行抓日K算 N 日涨幅后排序。
    active 类型直接返回按成交额排序的候选池;gainers/losers 按 N 日涨幅排序。
    """
    candidates = _hot_1d(market, "active", max(20, min(50, limit * 3)))

    if market == "美股":
        enriched = _yahoo_us_period_returns(candidates, days)
    else:
        def enrich(it):
            try:
                it = dict(it)
                it["change_pct"] = _n_day_return(market, it["symbol"], days)
                return it
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool_exec:
            enriched = [x for x in pool_exec.map(enrich, candidates) if x is not None]

    # 成交活跃榜保持成交额排名，但 change_pct 必须改成所选区间的真实涨跌，
    # 否则前端“近7日/30日涨跌”标题会错误展示当日涨跌。
    if type_ == "active":
        return enriched[:limit]

    reverse = (type_ == "gainers")
    enriched.sort(key=lambda x: x["change_pct"], reverse=reverse)
    return enriched[:limit]


def _yahoo_us_1d(type_: str, limit: int) -> list[dict]:
    screener = {
        "gainers": "day_gainers",
        "losers": "day_losers",
        "active": "most_actives",
    }[type_]
    response = requests.get(
        _YAHOO_SCREENER_URL,
        params={"formatted": "false", "scrIds": screener, "count": limit, "start": 0},
        headers=_YAHOO_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    finance = response.json().get("finance") or {}
    results = finance.get("result") or []
    quotes = (results[0].get("quotes") or []) if results else []
    if not quotes:
        raise RuntimeError("Yahoo Finance 美股榜单返回空数据")
    return [{
        "symbol": str(item.get("symbol") or "").upper(),
        "name": item.get("shortName") or item.get("longName") or "",
        "price": _num(item.get("regularMarketPrice")),
        "change_pct": _num(item.get("regularMarketChangePercent")),
        "volume": _num(item.get("regularMarketVolume")),
        "secid": str(item.get("symbol") or "").upper(),
    } for item in quotes[:limit] if item.get("symbol")]


def _yahoo_us_period_returns(candidates: list[dict], days: int) -> list[dict]:
    """用一次 Yahoo Spark 批量请求计算美股候选池多日涨跌。"""
    symbols = [str(item.get("symbol") or "").upper() for item in candidates]
    results = _yahoo_spark_results(symbols, days)
    returns = {}
    for result in results:
        symbol = str(result.get("symbol") or "").upper()
        responses = result.get("response") or []
        quote_sets = (((responses[0].get("indicators") or {}).get("quote") or [{}]) if responses else [{}])
        closes = [value for value in (quote_sets[0].get("close") or []) if value is not None]
        if len(closes) >= 2:
            window = closes[-(days + 1):]
            returns[symbol] = round((window[-1] / window[0] - 1) * 100, 2)
    enriched = []
    for item in candidates:
        symbol = str(item.get("symbol") or "").upper()
        if symbol not in returns:
            continue
        row = dict(item)
        row["change_pct"] = returns[symbol]
        enriched.append(row)
    return enriched


def _yahoo_spark_results(symbols: list[str], days: int) -> list[dict]:
    """批次含无效临时代码时自动二分隔离，避免一只坏代码拖垮整榜。"""
    if not symbols:
        return []
    response = requests.get(
        _YAHOO_SPARK_URL,
        params={
            "symbols": ",".join(symbols),
            "range": "1mo" if days <= 7 else "3mo",
            "interval": "1d",
        },
        headers=_YAHOO_HEADERS,
        timeout=_TIMEOUT,
    )
    if response.ok:
        spark = response.json().get("spark") or {}
        return spark.get("result") or []
    if len(symbols) == 1:
        return []
    middle = len(symbols) // 2
    return (
        _yahoo_spark_results(symbols[:middle], days)
        + _yahoo_spark_results(symbols[middle:], days)
    )


def _n_day_return(market: str, symbol: str, days: int) -> float:
    """用真实历史K线算近 N 个交易日涨幅(%)。"""
    df = data_fetch.get_history_months(market, symbol, 3, fetch_months=3)
    closes = df["close"].astype(float).dropna().tail(days + 1).tolist()
    if len(closes) < 2:
        raise Exception("K线数据不足")
    return round((closes[-1] / closes[0] - 1) * 100, 2)


def _num(v):
    """东财偶尔返回 '-' 表示无数据,统一转成 None。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
