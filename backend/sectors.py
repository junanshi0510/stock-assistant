# -*- coding: utf-8 -*-
"""
A-share sector and concept heat analysis.

Data rules:
- Industry classification: BaoStock official industry classification.
- Stock snapshot: Tencent quote batch endpoint, real market snapshot.
- Profit evidence: BaoStock latest available profit report.
- Concept boards: Eastmoney concept board endpoint. If unavailable, return an
  explicit unavailable state. No generated or fallback concept data.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import baostock as bs
import pandas as pd
import requests


_CACHE_TTL = 300
_CLASS_TTL = 6 * 3600
_cache: dict[tuple, tuple[float, dict]] = {}
_industry_cache: tuple[float, list[dict]] | None = None
_profit_cache: dict[str, tuple[float, dict | None]] = {}

_TX_URL = "https://qt.gtimg.cn/q="
_TX_BATCH_SIZE = 240
_TX_MIN_QUOTES = 800
_EM_CLIST_URLS = [
    "https://79.push2.eastmoney.com/api/qt/clist/get",
]
_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_THS_CONCEPT_HOME = "http://q.10jqka.com.cn/gn/"
_THS_CONCEPT_URL = "http://q.10jqka.com.cn/gn/index/field/addtime/order/desc/page/1/ajax/1/"


def _num(v):
    if v is None:
        return None
    try:
        s = str(v).strip()
        if s in ("", "-", "--", "None", "nan"):
            return None
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _round(v, digits=2):
    return round(v, digits) if v is not None else None


def _bs_to_symbol(code: str) -> str:
    return code.split(".")[-1]


def _symbol_to_bs(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9")):
        return "sh." + s
    if s.startswith(("4", "8")):
        return "bj." + s
    return "sz." + s


def _symbol_to_tx(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9")):
        return "sh" + s
    if s.startswith(("4", "8")):
        return "bj" + s
    return "sz" + s


def _clean_industry_name(raw: str) -> str:
    name = str(raw or "").strip()
    return re.sub(r"^[A-Z]\d{2}", "", name).strip() or name


def _cache_get(key):
    item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_put(key, data):
    _cache[key] = (time.time(), data)


def _query_industry_members() -> list[dict]:
    global _industry_cache
    if _industry_cache and time.time() - _industry_cache[0] < _CLASS_TTL:
        return list(_industry_cache[1])

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {lg.error_msg}")
    try:
        rs = bs.query_stock_industry()
        if rs.error_code != "0":
            raise RuntimeError(f"BaoStock industry query failed: {rs.error_msg}")
        rows = []
        while rs.next():
            data = dict(zip(rs.fields, rs.get_row_data()))
            industry = _clean_industry_name(data.get("industry"))
            symbol = _bs_to_symbol(data.get("code", ""))
            if not industry or not re.match(r"^\d{6}$", symbol):
                continue
            rows.append({
                "symbol": symbol,
                "bs_code": data.get("code"),
                "name": data.get("code_name") or "",
                "industry": industry,
                "classification": data.get("industryClassification") or "",
                "update_date": data.get("updateDate") or "",
            })
    finally:
        bs.logout()

    if not rows:
        raise RuntimeError("BaoStock industry classification returned empty data")
    _industry_cache = (time.time(), rows)
    return list(rows)


def _parse_tencent_quotes(text: str) -> dict[str, dict]:
    out = {}
    for body in re.findall(r'v_[a-z]{2}\d{6}="([^"]*)";', text):
        parts = body.split("~")
        if len(parts) < 46:
            continue
        symbol = parts[2].strip()
        price = _num(parts[3])
        change_pct = _num(parts[32])
        if not symbol or price is None or change_pct is None:
            continue
        amount_wan = _num(parts[37])
        out[symbol] = {
            "symbol": symbol,
            "name": parts[1].strip(),
            "price": price,
            "prev_close": _num(parts[4]),
            "open": _num(parts[5]),
            "change": _num(parts[31]),
            "change_pct": change_pct,
            "high": _num(parts[33]),
            "low": _num(parts[34]),
            "volume": _num(parts[36]),
            "amount": amount_wan * 10000 if amount_wan is not None else None,
            "turnover": _num(parts[38]),
            "pe_ttm": _num(parts[39]),
            "amplitude": _num(parts[43]),
            "market_cap_yi": _num(parts[44]),
            "float_market_cap_yi": _num(parts[45]),
            "quote_time": parts[30],
        }
    return out


def _fetch_tencent_batch(batch: list[str], depth: int = 0) -> tuple[dict[str, dict], list[dict]]:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    q = ",".join(_symbol_to_tx(s) for s in batch)
    last_error = ""
    for attempt in range(2):
        try:
            session = requests.Session()
            session.trust_env = False
            resp = session.get(_TX_URL + q, timeout=8 + depth * 4, headers=headers)
            resp.raise_for_status()
            resp.encoding = "gbk"
            parsed = _parse_tencent_quotes(resp.text)
            if parsed:
                return parsed, []
            last_error = "Tencent quote endpoint returned empty batch"
        except Exception as e:
            last_error = str(e)
            time.sleep(0.35 * (attempt + 1))

    if len(batch) > 40 and depth < 3:
        mid = len(batch) // 2
        left, left_failed = _fetch_tencent_batch(batch[:mid], depth + 1)
        right, right_failed = _fetch_tencent_batch(batch[mid:], depth + 1)
        left.update(right)
        return left, left_failed + right_failed

    return {}, [{
        "count": len(batch),
        "sample": batch[:6],
        "error": last_error[:160],
    }]


def _fetch_tencent_quotes(symbols: list[str]) -> tuple[dict[str, dict], list[dict]]:
    symbols = sorted({s for s in symbols if re.match(r"^\d{6}$", s)})
    if not symbols:
        return {}, []

    quotes: dict[str, dict] = {}
    failures: list[dict] = []
    batches = [symbols[i:i + _TX_BATCH_SIZE] for i in range(0, len(symbols), _TX_BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result, failed in pool.map(_fetch_tencent_batch, batches):
            quotes.update(result)
            failures.extend(failed)
    if len(quotes) < _TX_MIN_QUOTES:
        details = "; ".join(f"{f['count']}只:{f['error']}" for f in failures[:3])
        raise RuntimeError(f"Tencent quote endpoint returned insufficient real data: {len(quotes)} quotes. {details}")
    return quotes, failures


def _latest_quarters():
    cutoff = dt.date.today() - dt.timedelta(days=45)
    year = cutoff.year
    quarter = (cutoff.month - 1) // 3 + 1
    quarter -= 1
    if quarter == 0:
        quarter = 4
        year -= 1
    pairs = []
    for _ in range(8):
        pairs.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return pairs


def _profit_for_symbol(symbol: str):
    cached = _profit_cache.get(symbol)
    if cached and time.time() - cached[0] < _CLASS_TTL:
        return cached[1]

    code = _symbol_to_bs(symbol)
    for year, quarter in _latest_quarters():
        rs = bs.query_profit_data(code=code, year=year, quarter=quarter)
        if rs.error_code != "0":
            continue
        while rs.next():
            row = dict(zip(rs.fields, rs.get_row_data()))
            profit = {
                "as_of": row.get("statDate") or "",
                "pub_date": row.get("pubDate") or "",
                "roe": _round((_num(row.get("roeAvg")) or 0) * 100, 2),
                "net_margin": _round((_num(row.get("npMargin")) or 0) * 100, 2),
                "gross_margin": _round((_num(row.get("gpMargin")) or 0) * 100, 2),
                "net_profit_yi": _round((_num(row.get("netProfit")) or 0) / 100000000, 2),
                "eps_ttm": _round(_num(row.get("epsTTM")), 3),
            }
            _profit_cache[symbol] = (time.time(), profit)
            return profit
    _profit_cache[symbol] = (time.time(), None)
    return None


def _fetch_profit_map(symbols: list[str]) -> dict[str, dict | None]:
    symbols = sorted({s for s in symbols if re.match(r"^\d{6}$", s)})
    if not symbols:
        return {}
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {lg.error_msg}")
    try:
        return {s: _profit_for_symbol(s) for s in symbols}
    finally:
        bs.logout()


def _profit_strong(profit: dict | None) -> bool:
    if not profit:
        return False
    net_profit = profit.get("net_profit_yi")
    eps = profit.get("eps_ttm")
    roe = profit.get("roe")
    net_margin = profit.get("net_margin")
    return (
        net_profit is not None and net_profit > 0
        and eps is not None and eps > 0
        and ((roe is not None and roe >= 3) or (net_margin is not None and net_margin >= 8))
    )


def _classify_stock(quote: dict, profit: dict | None, kind: str, sector_change: float | None):
    pct = quote.get("change_pct")
    turnover = quote.get("turnover")
    pe = quote.get("pe_ttm")
    strong_profit = _profit_strong(profit)
    pe_profit = pe is not None and 0 < pe < 60
    high_turnover = turnover is not None and turnover >= 6
    stretched_pe = pe is None or pe <= 0 or pe >= 80
    profit_supported = strong_profit or pe_profit
    concept_like = kind == "concept" and pct is not None and pct >= 3 and (not profit_supported or high_turnover or stretched_pe)

    reasons = []
    if pct is not None:
        reasons.append(f"今日涨跌幅 {pct:+.2f}%")
    if turnover is not None:
        reasons.append(f"换手率 {turnover:.2f}%")
    if pe is not None and pe > 0:
        reasons.append(f"PE(TTM) {pe:.1f}")
    if profit:
        if profit.get("net_profit_yi") is not None:
            reasons.append(f"最近财报净利润 {profit['net_profit_yi']:.2f} 亿")
        if profit.get("roe") is not None:
            reasons.append(f"ROE {profit['roe']:.2f}%")
    else:
        if pe_profit:
            reasons.append("PE(TTM)>0，TTM盈利为正")
        else:
            reasons.append("未取到可验证盈利指标")

    if pct is None:
        label = "数据不足"
    elif pct > 0:
        if profit_supported and not stretched_pe:
            label = "盈利支撑上涨"
        elif concept_like:
            label = "偏概念/资金驱动"
        elif sector_change is not None and pct >= sector_change:
            label = "板块热度带动"
        else:
            label = "资金推动上涨"
    elif pct < 0:
        if sector_change is not None and sector_change < 0:
            label = "板块回落拖累"
        elif not strong_profit or stretched_pe:
            label = "盈利/估值压力"
        else:
            label = "个股短线回落"
    else:
        label = "横盘整理"

    return {
        "label": label,
        "profit_supported": bool(profit_supported),
        "concept_hype": bool(concept_like),
        "evidence": reasons[:5],
    }


def _amount_sum(rows):
    vals = [r.get("amount") for r in rows if r.get("amount") is not None]
    return sum(vals) if vals else None


def _avg(rows, key):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _build_industry_heat(sector_limit: int, stock_limit: int):
    members = _query_industry_members()
    quotes, quote_failures = _fetch_tencent_quotes([m["symbol"] for m in members])
    grouped = defaultdict(list)
    update_dates = set()
    for m in members:
        q = quotes.get(m["symbol"])
        if not q:
            continue
        item = {**q, "industry": m["industry"]}
        grouped[m["industry"]].append(item)
        if m.get("update_date"):
            update_dates.add(m["update_date"])

    sectors = []
    for industry, rows in grouped.items():
        if len(rows) < 3:
            continue
        rows.sort(key=lambda x: x.get("change_pct") or -999, reverse=True)
        avg_change = _avg(rows, "change_pct")
        avg_turnover = _avg(rows, "turnover")
        up_count = sum(1 for r in rows if (r.get("change_pct") or 0) > 0)
        down_count = sum(1 for r in rows if (r.get("change_pct") or 0) < 0)
        up_ratio = up_count / len(rows) * 100
        total_amount = _amount_sum(rows)
        heat_score = (avg_change or 0) * 18 + (up_ratio - 50) * 0.55 + (avg_turnover or 0) * 1.2
        sectors.append({
            "name": industry,
            "kind": "industry",
            "stock_count": len(rows),
            "avg_change_pct": _round(avg_change),
            "up_count": up_count,
            "down_count": down_count,
            "up_ratio": _round(up_ratio),
            "total_amount_yi": _round(total_amount / 100000000 if total_amount else None),
            "avg_turnover": _round(avg_turnover),
            "heat_score": _round(heat_score),
            "leaders": rows[:stock_limit],
            "laggards": sorted(rows, key=lambda x: x.get("change_pct") or 999)[:min(5, stock_limit)],
        })
    sectors.sort(key=lambda x: x["heat_score"], reverse=True)
    selected = sectors[:sector_limit]

    profit_symbols = []
    for sec in selected:
        profit_symbols.extend([s["symbol"] for s in sec["leaders"][:1]])
    profit_symbols = list(dict.fromkeys(profit_symbols))[:10]
    profits = _fetch_profit_map(profit_symbols)

    for sec in selected:
        for key in ("leaders", "laggards"):
            enriched = []
            for stock in sec[key]:
                profit = profits.get(stock["symbol"])
                enriched.append({
                    **stock,
                    "profit": profit,
                    "driver": _classify_stock(stock, profit, "industry", sec.get("avg_change_pct")),
                })
            sec[key] = enriched

    quote_times = [q.get("quote_time") for q in quotes.values() if q.get("quote_time")]
    return {
        "available": True,
        "source": "BaoStock 行业分类 + 腾讯证券批量行情 + BaoStock 财务盈利指标",
        "classification_date": max(update_dates) if update_dates else "",
        "quote_time": max(quote_times) if quote_times else "",
        "sector_count": len(sectors),
        "stock_count": len(quotes),
        "classification_stock_count": len(members),
        "quote_missing_count": max(0, len(members) - len(quotes)),
        "quote_failed_batches": quote_failures[:8],
        "items": selected,
    }


def _eastmoney_get(url: str, params: dict):
    session = requests.Session()
    session.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/center/boardlist.html",
    }
    resp = session.get(url, params=params, headers=headers, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    diff = (data.get("data") or {}).get("diff")
    if not diff:
        raise RuntimeError("Eastmoney returned empty concept board data")
    return diff


def _concept_boards(limit: int):
    errors = []
    eastmoney = _concept_boards_eastmoney(limit)
    if eastmoney.get("available"):
        return eastmoney
    errors.extend(eastmoney.get("errors") or [eastmoney.get("error", "")])

    ths = _concept_boards_ths(limit)
    if ths.get("available"):
        ths["fallback_reason"] = "东方财富概念涨跌榜当前不可用，已切换到同花顺真实概念时间表"
        ths["upstream_errors"] = errors
        return ths
    errors.extend(ths.get("errors") or [ths.get("error", "")])

    return {
        "available": False,
        "source": "东方财富概念板块中心 / 同花顺概念中心",
        "mode": "unavailable",
        "error": "真实概念板块源当前均不可用: " + " ; ".join([str(e) for e in errors if e][:4]),
        "errors": errors[:6],
        "items": [],
    }


def _concept_boards_eastmoney(limit: int):
    params = {
        "pn": "1", "pz": str(max(10, limit)), "po": "1", "np": "1",
        "ut": _EM_UT, "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:90 t:3 f:!50",
        "fields": "f2,f3,f4,f8,f12,f14,f20,f104,f105,f128,f136",
    }
    errors = []
    for url in _EM_CLIST_URLS:
        try:
            rows = _eastmoney_get(url, params)
            out = []
            for it in rows[:limit]:
                out.append({
                    "name": it.get("f14") or "",
                    "code": it.get("f12") or "",
                    "price": _num(it.get("f2")),
                    "change_pct": _num(it.get("f3")),
                    "turnover": _num(it.get("f8")),
                    "market_cap_yi": _round((_num(it.get("f20")) or 0) / 100000000),
                    "up_count": int(_num(it.get("f104")) or 0),
                    "down_count": int(_num(it.get("f105")) or 0),
                    "leader": it.get("f128") or "",
                    "leader_change_pct": _num(it.get("f136")),
                })
            return {
                "available": True,
                "source": "东方财富概念板块中心",
                "mode": "heat",
                "items": out,
            }
        except Exception as e:
            errors.append(f"东方财富 {url}: {str(e)[:180]}")
    return {
        "available": False,
        "source": "东方财富概念板块中心",
        "mode": "heat",
        "error": "; ".join(errors),
        "errors": errors,
        "items": [],
    }


def _concept_boards_ths(limit: int):
    session = requests.Session()
    session.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://q.10jqka.com.cn/gn/",
    }
    try:
        home_resp = session.get(_THS_CONCEPT_HOME, headers=headers, timeout=8)
        home_resp.raise_for_status()
        try:
            resp = session.get(_THS_CONCEPT_URL, headers=headers, timeout=8)
            if resp.status_code == 401:
                session.get(_THS_CONCEPT_HOME, headers=headers, timeout=8)
                resp = session.get(_THS_CONCEPT_URL, headers=headers, timeout=8)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            html = home_resp.text
        tables = pd.read_html(StringIO(html))
        if not tables:
            raise RuntimeError("同花顺概念中心未返回表格")
        df = tables[0].head(limit).copy()
        items = []
        for idx, row in df.iterrows():
            leader = str(row.get("龙头股") or "").strip()
            if leader in ("", "--", "nan", "None"):
                leader = ""
            items.append({
                "name": str(row.get("概念名称") or "").strip(),
                "code": f"ths-{idx}",
                "date": str(row.get("日期") or "").strip(),
                "event": str(row.get("驱动事件") or "").strip(),
                "leader": leader,
                "stock_count": int(_num(row.get("成分股数量")) or 0),
                "change_pct": None,
                "up_count": None,
                "down_count": None,
                "leader_change_pct": None,
            })
        items = [it for it in items if it["name"]]
        if not items:
            raise RuntimeError("同花顺概念中心表格为空")
        return {
            "available": True,
            "source": "同花顺概念中心",
            "mode": "timeline",
            "items": items,
        }
    except Exception as e:
        msg = f"同花顺概念中心: {str(e)[:180]}"
        return {
            "available": False,
            "source": "同花顺概念中心",
            "mode": "timeline",
            "error": msg,
            "errors": [msg],
            "items": [],
        }


def get_sector_analysis(market: str = "A股", sector_limit: int = 12,
                        stock_limit: int = 8, include_concepts: bool = True) -> dict:
    market_text = str(market or "").strip()
    if market_text != "A股" and not market_text.upper().startswith("A"):
        raise ValueError("板块热度当前仅支持 A股；港股/美股需要接入对应专业行业分类源")
    market = "A股"
    sector_limit = max(5, min(30, int(sector_limit)))
    stock_limit = max(3, min(15, int(stock_limit)))
    key = ("sectors", market, sector_limit, stock_limit, include_concepts)
    cached = _cache_get(key)
    if cached:
        return cached

    industries = _build_industry_heat(sector_limit, stock_limit)
    concepts = _concept_boards(10) if include_concepts else {
        "available": False,
        "source": "东方财富概念板块中心",
        "error": "本次请求未启用概念板块",
        "items": [],
    }
    result = {
        "market": market,
        "as_of": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "industries": industries,
        "concepts": concepts,
        "method": {
            "heat_score": "行业热度 = 平均涨跌幅、上涨占比、平均换手率的加权评分",
            "driver": "个股归因基于真实涨跌幅、换手率、PE(TTM)与 BaoStock 最近财报盈利指标",
            "no_fallback": "概念板块源不可用时直接标注不可用，不使用假数据替代",
        },
    }
    _cache_put(key, result)
    return result
