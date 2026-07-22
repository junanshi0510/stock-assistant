# -*- coding: utf-8 -*-
"""真实单股行情快照。使用腾讯证券单股行情，不生成替代数据。"""

import re
import time

import requests

import data_fetch
from strategies.asset_level_recurrence import (
    evaluate_stock_level_recurrence,
    unavailable_level_recurrence,
)

_CACHE_TTL = 15
_cache = {}
_HEADERS = {
    "Referer": "https://gu.qq.com/",
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


def _tencent_code(market: str, symbol: str) -> str:
    s = symbol.strip()
    if market == "A股":
        return data_fetch._a_exchange_prefixed_symbol(s)
    if market == "港股":
        return "hk" + s.zfill(5)
    if market == "美股":
        if re.fullmatch(r"\d+\.[A-Za-z.]+", s):
            s = s.split(".", 1)[1]
        else:
            s = s.split(".", 1)[0]
        return "us" + s.upper()
    raise ValueError(f"不支持的市场:{market}")


def _fetch_tencent(code: str) -> list[str]:
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        "https://qt.gtimg.cn/q=" + code,
        headers=_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    response.encoding = "gb18030"
    match = re.search(r'="(.*)";', response.text, re.S)
    if not match:
        raise RuntimeError("腾讯证券行情返回格式异常")
    body = match.group(1)
    if not body:
        raise RuntimeError("腾讯证券行情返回空数据")
    return body.split("~")


def _quote_tencent(market: str, symbol: str, fields: list[str]) -> dict:
    if len(fields) < 46:
        raise RuntimeError(f"{market}行情字段不完整")
    timestamp = fields[30]
    if re.fullmatch(r"\d{14}", timestamp):
        timestamp = (
            f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]} "
            f"{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}"
        )
    else:
        timestamp = timestamp.replace("/", "-")
    volume = _num(fields[36])
    amount = _num(fields[37])
    market_cap = _num(fields[45])
    if market == "A股":
        volume = volume * 100 if volume is not None else None
        amount = amount * 10000 if amount is not None else None
    return {
        "name": fields[1] or symbol.upper(),
        "price": _num(fields[3]),
        "change": _num(fields[31]),
        "change_pct": _num(fields[32]),
        "open": _num(fields[5]),
        "prev_close": _num(fields[4]),
        "high": _num(fields[33]),
        "low": _num(fields[34]),
        "volume": volume,
        "amount": amount,
        "bid": _num(fields[9]),
        "ask": _num(fields[19]),
        "pe": _num(fields[39]),
        "market_cap": market_cap * 100000000 if market_cap is not None else None,
        "as_of": timestamp,
        "delay_note": "腾讯证券美股行情可能存在延迟" if market == "美股" else "",
    }


def get_quote(market: str, symbol: str) -> dict:
    symbol = symbol.strip()
    cache_key = (market, symbol)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    code = _tencent_code(market, symbol)
    fields = _fetch_tencent(code)
    quote = _quote_tencent(market, symbol, fields)

    result = {
        "available": True,
        "market": market,
        "symbol": symbol,
        "source": "腾讯证券单股行情",
        **quote,
    }
    _cache_put(cache_key, result)
    return result


def get_quote_level_history(market: str, symbol: str, months: int = 60) -> dict:
    """Return a live quote plus the last prior unadjusted daily range at that price."""
    quote = get_quote(market, symbol)
    current_price = _num(quote.get("price"))
    if current_price is None:
        recurrence = unavailable_level_recurrence(
            asset_type="stock",
            reason="实时行情源没有返回有效成交价。",
            target_label="实时成交价",
            target_as_of=quote.get("as_of"),
            target_source=quote.get("source"),
        )
        return {**quote, "level_recurrence": recurrence}

    try:
        frame, history_source = data_fetch.get_price_level_history_months(
            market, symbol, months=months
        )
        bars = [
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "low": _num(row["low"]),
                "high": _num(row["high"]),
                "close": _num(row["close"]),
            }
            for _, row in frame.iterrows()
        ]
        recurrence = evaluate_stock_level_recurrence(
            current_price=current_price,
            quote_as_of=str(quote.get("as_of") or ""),
            quote_source=str(quote.get("source") or ""),
            bars=bars,
            history_source=history_source,
            market=market,
            symbol=symbol,
        )
    except Exception as error:
        recurrence = unavailable_level_recurrence(
            asset_type="stock",
            reason=f"真实未复权历史价格当前不可用: {error}",
            target_label="实时成交价",
            target_value=current_price,
            target_as_of=quote.get("as_of"),
            target_source=quote.get("source"),
        )
    return {**quote, "level_recurrence": recurrence}
