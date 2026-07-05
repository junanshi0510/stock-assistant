# -*- coding: utf-8 -*-
"""真实单股行情快照。当前使用新浪单股行情接口,不生成兜底数据。"""

import re
import time

import requests

import data_fetch

_CACHE_TTL = 15
_cache = {}
_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0",
}


def _cache_get(key):
    item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_put(key, value):
    _cache[key] = (time.time(), value)


def _num(v):
    try:
        if v in ("", None):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _sina_code(market: str, symbol: str) -> str:
    s = symbol.strip()
    if market == "A股":
        return data_fetch._sina_a_symbol(s)
    if market == "港股":
        return "rt_hk" + s.zfill(5)
    if market == "美股":
        return "gb_" + s.lower()
    raise ValueError(f"不支持的市场:{market}")


def _fetch_sina(code: str) -> list[str]:
    url = "https://hq.sinajs.cn/list=" + code
    r = requests.get(url, headers=_HEADERS, timeout=10)
    r.raise_for_status()
    r.encoding = "gb18030"
    m = re.search(r'="(.*)";', r.text, re.S)
    if not m:
        raise RuntimeError("新浪行情返回格式异常")
    body = m.group(1)
    if not body:
        raise RuntimeError("新浪行情返回空数据")
    return body.split(",")


def _quote_a(symbol: str, fields: list[str]) -> dict:
    if len(fields) < 32:
        raise RuntimeError("A股行情字段不完整")
    name = fields[0]
    open_ = _num(fields[1])
    prev_close = _num(fields[2])
    price = _num(fields[3])
    high = _num(fields[4])
    low = _num(fields[5])
    volume = _num(fields[8])
    amount = _num(fields[9])
    change = price - prev_close if price is not None and prev_close else None
    pct = change / prev_close * 100 if change is not None and prev_close else None
    return {
        "name": name,
        "price": price,
        "change": round(change, 3) if change is not None else None,
        "change_pct": round(pct, 3) if pct is not None else None,
        "open": open_,
        "prev_close": prev_close,
        "high": high,
        "low": low,
        "volume": volume,
        "amount": amount,
        "bid": _num(fields[6]),
        "ask": _num(fields[7]),
        "as_of": f"{fields[30]} {fields[31]}",
        "delay_note": "",
    }


def _quote_hk(symbol: str, fields: list[str]) -> dict:
    if len(fields) < 19:
        raise RuntimeError("港股行情字段不完整")
    return {
        "name": fields[1] or fields[0],
        "price": _num(fields[6]),
        "change": _num(fields[7]),
        "change_pct": _num(fields[8]),
        "open": _num(fields[2]),
        "prev_close": _num(fields[3]),
        "high": _num(fields[4]),
        "low": _num(fields[5]),
        "volume": _num(fields[12]),
        "amount": _num(fields[11]),
        "bid": _num(fields[9]),
        "ask": _num(fields[10]),
        "pe": _num(fields[13]),
        "market_cap": _num(fields[14]),
        "as_of": f"{fields[17]} {fields[18]}",
        "delay_note": "",
    }


def _quote_us(symbol: str, fields: list[str]) -> dict:
    if len(fields) < 13:
        raise RuntimeError("美股行情字段不完整")
    return {
        "name": fields[0] or symbol.upper(),
        "price": _num(fields[1]),
        "change": _num(fields[4]),
        "change_pct": _num(fields[2]),
        "open": _num(fields[5]),
        "high": _num(fields[6]),
        "low": _num(fields[7]),
        "volume": _num(fields[10]),
        "avg_volume": _num(fields[11]),
        "market_cap": _num(fields[12]),
        "pe": _num(fields[13]) if len(fields) > 13 else None,
        "as_of": fields[3],
        "delay_note": "新浪美股行情通常延迟约15分钟",
    }


def get_quote(market: str, symbol: str) -> dict:
    symbol = symbol.strip()
    cache_key = (market, symbol)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    code = _sina_code(market, symbol)
    fields = _fetch_sina(code)
    if market == "A股":
        quote = _quote_a(symbol, fields)
    elif market == "港股":
        quote = _quote_hk(symbol, fields)
    elif market == "美股":
        quote = _quote_us(symbol, fields)
    else:
        raise ValueError(f"不支持的市场:{market}")

    result = {
        "available": True,
        "market": market,
        "symbol": symbol,
        "source": "新浪财经单股行情",
        **quote,
    }
    _cache_put(cache_key, result)
    return result
