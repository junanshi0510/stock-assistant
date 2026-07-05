# -*- coding: utf-8 -*-
"""
热门股票 / 涨跌幅榜抓取
======================
提供各市场的热门股票榜单,三个市场统一走【东方财富网】的公开榜单接口:
免费、无需 key、无明显限流,且项目已为东财域名配好 NO_PROXY 直连(见 data_fetch.py)。

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
import json
import re

import requests
import data_fetch

_TIMEOUT = 10
_CLIST_URL = "http://push2.eastmoney.com/api/qt/clist/get"
_KLINE_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_SINA_A_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_SINA_HK_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHKStockData"
_SINA_US_URL = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_CategoryService.getList"

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

    if period == "1d":
        items = _hot_1d(market, type_, limit)
        scope = "全市场"
    else:
        items = _hot_multiday(market, type_, _PERIOD_DAYS[period], limit)
        scope = "活跃股范围内"

    return {"market": market, "period": period, "type": type_,
            "scope": scope, "items": items, "count": len(items)}


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
    try:
        return _sina_1d(market, type_, limit)
    except Exception:
        # 东财仅作为真实备选源;不会返回任何精选/假数据。
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
    pool = _hot_1d(market, "active", max(20, min(50, limit * 3)))  # 真实活跃股候选池
    if type_ == "active":
        return pool[:limit]

    def enrich(it):
        try:
            it = dict(it)
            it["change_pct"] = _n_day_return(market, it["symbol"], days)
            return it
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool_exec:
        enriched = [x for x in pool_exec.map(enrich, pool) if x is not None]

    reverse = (type_ == "gainers")
    enriched.sort(key=lambda x: x["change_pct"], reverse=reverse)
    return enriched[:limit]


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


def _sina_1d(market: str, type_: str, limit: int) -> list[dict]:
    if market == "A股":
        return _sina_a_1d(type_, limit)
    if market == "港股":
        return _sina_hk_1d(type_, limit)
    if market == "美股":
        return _sina_us_1d(type_, limit)
    raise ValueError(f"不支持的市场:{market}")


def _sina_sort(type_: str) -> tuple[str, str]:
    if type_ == "gainers":
        return "changepercent", "0"
    if type_ == "losers":
        return "changepercent", "1"
    return "amount", "0"


def _sina_a_1d(type_: str, limit: int) -> list[dict]:
    sort, asc = _sina_sort(type_)
    params = {
        "page": 1,
        "num": limit,
        "sort": sort,
        "asc": asc,
        "node": "hs_a",
        "symbol": "",
        "_s_r_a": "page",
    }
    resp = requests.get(_SINA_A_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError("新浪A股榜单返回空数据")
    return [{
        "symbol": str(it.get("code") or "").strip(),
        "name": it.get("name") or "",
        "price": _num(it.get("trade")),
        "change_pct": _num(it.get("changepercent")),
        "volume": _num(it.get("volume")),
        "secid": it.get("symbol") or "",
    } for it in data[:limit]]


def _sina_hk_1d(type_: str, limit: int) -> list[dict]:
    sort, asc = _sina_sort(type_)
    params = {
        "page": 1,
        "num": limit,
        "sort": sort,
        "asc": asc,
        "node": "qbgg_hk",
        "_s_r_a": "page",
    }
    resp = requests.get(_SINA_HK_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError("新浪港股榜单返回空数据")
    return [{
        "symbol": str(it.get("symbol") or "").zfill(5),
        "name": it.get("name") or "",
        "price": _num(it.get("lasttrade")),
        "change_pct": _num(it.get("changepercent")),
        "volume": _num(it.get("volume")),
        "secid": str(it.get("symbol") or "").zfill(5),
    } for it in data[:limit]]


def _sina_us_1d(type_: str, limit: int) -> list[dict]:
    if type_ == "gainers":
        sort, asc = "chg", "0"
    elif type_ == "losers":
        sort, asc = "chg", "1"
    else:
        sort, asc = "volume", "0"
    params = {
        "page": 1,
        "num": limit,
        "sort": sort,
        "asc": asc,
        "market": "",
        "id": "",
    }
    resp = requests.get(_SINA_US_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    m = re.search(r"var\s+x=\((.*)\);\s*$", resp.text, re.S)
    if not m:
        raise RuntimeError("新浪美股榜单 JSONP 解析失败")
    payload = json.loads(m.group(1))
    data = payload.get("data") or []
    if not data:
        raise RuntimeError("新浪美股榜单返回空数据")
    return [{
        "symbol": str(it.get("symbol") or "").upper(),
        "name": it.get("cname") or it.get("name") or "",
        "price": _num(it.get("price")),
        "change_pct": _num(it.get("chg")),
        "volume": _num(it.get("volume")),
        "secid": str(it.get("symbol") or "").upper(),
    } for it in data[:limit]]
