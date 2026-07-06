# -*- coding: utf-8 -*-
"""
Real fund ranking and trend analysis.

Sources:
- Eastmoney fund ranking: https://fund.eastmoney.com/data/fundranking.html
- Tiantian Fund net value history: https://fundf10.eastmoney.com/

No synthetic rows are generated. If a source is unavailable the caller gets an
explicit error instead of fabricated fallback data.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pandas as pd
import requests
import akshare as ak
from akshare.utils import demjson


_CACHE_TTL = 300
_cache: dict[tuple, tuple[float, dict]] = {}

_RANK_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
_PROFILE_URL = "https://fundgz.1234567.com.cn/js/{code}.js"
_TREND_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_SEARCH_URL = "https://fund.eastmoney.com/js/fundcode_search.js"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://fund.eastmoney.com/data/fundranking.html",
}
_NAV_HEADERS = {
    **_HEADERS,
    "Referer": "https://fundf10.eastmoney.com/",
}

_CATEGORY_MAP = {
    "all": ("全部", "all"),
    "stock": ("股票型", "gp"),
    "hybrid": ("混合型", "hh"),
    "bond": ("债券型", "zq"),
    "index": ("指数型", "zs"),
    "qdii": ("QDII", "qdii"),
    "lof": ("LOF", "lof"),
    "fof": ("FOF", "fof"),
}
_CATEGORY_ALIAS = {
    "全部": "all",
    "股票型": "stock",
    "混合型": "hybrid",
    "债券型": "bond",
    "指数型": "index",
    "QDII": "qdii",
    "LOF": "lof",
    "FOF": "fof",
}
_SORT_MAP = {
    "1y": "1nzf",
    "ytd": "jnzf",
    "6m": "6yzf",
    "3m": "3yzf",
    "1m": "1yzf",
    "1w": "1zzf",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def _cache_get(key):
    item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_put(key, value):
    _cache[key] = (time.time(), value)


def _num(v):
    if v is None:
        return None
    try:
        s = str(v).strip().replace("%", "")
        if s in ("", "-", "--", "None", "null", "nan"):
            return None
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _round(v, digits=2):
    return round(v, digits) if v is not None else None


def _cell(row: dict, *names):
    for name in names:
        if name in row:
            value = row.get(name)
            if value is not None and str(value) != "nan":
                return value
    return None


def _one_year_before(today: dt.date) -> dt.date:
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def _norm_category(category: str) -> str:
    key = str(category or "all").strip()
    if key in _CATEGORY_ALIAS:
        key = _CATEGORY_ALIAS[key]
    if key not in _CATEGORY_MAP:
        raise ValueError(f"不支持的基金分类:{category}")
    return key


def _fetch_detail_js(code: str) -> str:
    cache_key = ("fund_detail_js", code)
    cached = _cache_get(cache_key)
    if cached:
        return cached["text"]
    resp = _session().get(
        _TREND_URL.format(code=code),
        params={"v": str(time.time())},
        headers={**_NAV_HEADERS, "Referer": f"https://fund.eastmoney.com/{code}.html"},
        timeout=18,
    )
    resp.raise_for_status()
    _cache_put(cache_key, {"text": resp.text})
    return resp.text


def _extract_var(text: str, name: str):
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(.*?);\s*/\*", text, re.S)
    if not match:
        match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(.*?);\s*var\s+", text, re.S)
    if not match:
        match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(.*?);\s*$", text, re.S)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        try:
            return demjson.decode(raw)
        except Exception:
            return raw.strip("\"'")


def _date_from_ms(value) -> str:
    num = _num(value)
    if num is None:
        return ""
    try:
        return dt.datetime.utcfromtimestamp(num / 1000).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _fund_fact_sheet(code: str) -> dict:
    try:
        text = _fetch_detail_js(code)
    except Exception:
        return {}

    name_match = re.search(r'var\s+fS_name\s*=\s*"([^"]*)"', text)
    source_rate = _extract_var(text, "fund_sourceRate")
    rate = _extract_var(text, "fund_Rate")
    asset = _extract_var(text, "Data_assetAllocation") or {}
    scale = _extract_var(text, "Data_fluctuationScale") or {}
    managers = _extract_var(text, "Data_currentFundManager") or []
    buy_redeem = _extract_var(text, "Data_buySedemption") or {}
    performance = _extract_var(text, "Data_performanceEvaluation") or {}
    similar_percent = _extract_var(text, "Data_rateInSimilarPersent") or []
    grand_total = _extract_var(text, "Data_grandTotal") or []

    asset_latest = {}
    categories = asset.get("categories") or []
    series = asset.get("series") or []
    if categories and series:
        idx = len(categories) - 1
        asset_latest["date"] = categories[idx]
        for item in series:
            values = item.get("data") or []
            if idx < len(values):
                name = item.get("name") or ""
                key = (
                    "stock_ratio" if "股票" in name else
                    "bond_ratio" if "债券" in name else
                    "cash_ratio" if "现金" in name else
                    "net_asset_yi" if "净资产" in name else
                    name
                )
                asset_latest[key] = _round(_num(values[idx]), 2)

    scale_latest = {}
    scale_categories = scale.get("categories") or []
    scale_series = scale.get("series") or []
    scale_rows = []
    if scale_categories and scale_series:
        for idx, item in enumerate(scale_series):
            if idx < len(scale_categories):
                scale_rows.append({
                    "date": scale_categories[idx],
                    "scale_yi": _round(_num(item.get("y")), 2),
                    "mom": item.get("mom") or "",
                })
        idx = len(scale_categories) - 1
        item = scale_series[idx] if idx < len(scale_series) else {}
        scale_latest = {
            "date": scale_categories[idx],
            "scale_yi": _round(_num(item.get("y")), 2),
            "mom": item.get("mom") or "",
        }

    flow_rows = []
    flow_summary = {}
    if isinstance(buy_redeem, dict):
        flow_categories = buy_redeem.get("categories") or []
        flow_series = buy_redeem.get("series") or []
        series_map = {item.get("name"): item.get("data") or [] for item in flow_series}
        for idx, date in enumerate(flow_categories):
            subscribe = _num((series_map.get("期间申购") or [None] * (idx + 1))[idx] if idx < len(series_map.get("期间申购") or []) else None)
            redeem = _num((series_map.get("期间赎回") or [None] * (idx + 1))[idx] if idx < len(series_map.get("期间赎回") or []) else None)
            shares = _num((series_map.get("总份额") or [None] * (idx + 1))[idx] if idx < len(series_map.get("总份额") or []) else None)
            flow_rows.append({
                "date": date,
                "subscribe_yi": _round(subscribe),
                "redeem_yi": _round(redeem),
                "net_subscribe_yi": _round((subscribe or 0) - (redeem or 0)) if subscribe is not None or redeem is not None else None,
                "total_share_yi": _round(shares),
            })
        if flow_rows:
            latest_flow = flow_rows[-1]
            net_values = [r["net_subscribe_yi"] for r in flow_rows if r["net_subscribe_yi"] is not None]
            total_net = _round(sum(net_values)) if net_values else None
            latest_net = latest_flow.get("net_subscribe_yi")
            if latest_net is not None and latest_net < -1:
                pressure = "净赎回压力"
            elif latest_net is not None and latest_net > 1:
                pressure = "净申购流入"
            else:
                pressure = "申赎平衡"
            if total_net is not None and total_net < -5:
                pressure = "持续净赎回"
            elif total_net is not None and total_net > 5:
                pressure = "持续净申购"
            flow_summary = {
                "latest_date": latest_flow.get("date"),
                "latest_subscribe_yi": latest_flow.get("subscribe_yi"),
                "latest_redeem_yi": latest_flow.get("redeem_yi"),
                "latest_net_subscribe_yi": latest_net,
                "latest_total_share_yi": latest_flow.get("total_share_yi"),
                "total_net_subscribe_yi": total_net,
                "pressure": pressure,
            }

    manager_rows = []
    for m in managers[:3] if isinstance(managers, list) else []:
        power = m.get("power") or {}
        profit = m.get("profit") or {}
        power_categories = power.get("categories") or []
        power_values = power.get("data") or []
        score_breakdown = []
        for idx, label in enumerate(power_categories):
            value = _num(power_values[idx]) if idx < len(power_values) else None
            score_breakdown.append({
                "label": label,
                "score": _round(value),
            })
        profit_categories = profit.get("categories") or []
        profit_values = (((profit.get("series") or [{}])[0].get("data") or []))
        profit_map = {}
        for idx, label in enumerate(profit_categories):
            value = None
            if idx < len(profit_values):
                value = _num(profit_values[idx].get("y"))
            profit_map[str(label)] = _round(value)
        tenure_return = profit_map.get("任期收益")
        peer_return = profit_map.get("同类平均")
        hs300_return = profit_map.get("沪深300")
        excess_peer = _round(tenure_return - peer_return) if tenure_return is not None and peer_return is not None else None
        excess_hs300 = _round(tenure_return - hs300_return) if tenure_return is not None and hs300_return is not None else None
        valid_scores = [r for r in score_breakdown if r.get("score") is not None]
        strengths = sorted(valid_scores, key=lambda r: r["score"], reverse=True)[:2]
        weaknesses = sorted(valid_scores, key=lambda r: r["score"])[:2]
        if excess_peer is not None and excess_peer >= 20 and (power.get("avr") and _num(power.get("avr")) >= 80):
            manager_label = "任期超额突出"
        elif excess_peer is not None and excess_peer > 0:
            manager_label = "任期跑赢同类"
        elif excess_peer is not None and excess_peer < -10:
            manager_label = "任期落后同类"
        else:
            manager_label = "任期中性"
        manager_rows.append({
            "id": str(m.get("id") or ""),
            "name": m.get("name") or "",
            "star": _num(m.get("star")),
            "work_time": m.get("workTime") or "",
            "fund_size": m.get("fundSize") or "",
            "score": _round(_num(power.get("avr"))),
            "score_date": power.get("jzrq") or "",
            "tenure_return": tenure_return,
            "tenure_peer_avg": peer_return,
            "tenure_hs300": hs300_return,
            "excess_vs_peer": excess_peer,
            "excess_vs_hs300": excess_hs300,
            "score_breakdown": score_breakdown,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "label": manager_label,
        })

    stage_returns = {
        "1m": _num(_extract_var(text, "syl_1y")),
        "3m": _num(_extract_var(text, "syl_3y")),
        "6m": _num(_extract_var(text, "syl_6y")),
        "1y": _num(_extract_var(text, "syl_1n")),
    }

    perf_scores = []
    if isinstance(performance, dict):
        perf_categories = performance.get("categories") or []
        perf_values = performance.get("data") or []
        perf_desc = performance.get("dsc") or []
        for idx, label in enumerate(perf_categories):
            score = _num(perf_values[idx]) if idx < len(perf_values) else None
            desc = perf_desc[idx] if idx < len(perf_desc) else ""
            perf_scores.append({
                "label": label,
                "score": _round(score),
                "description": re.sub(r"<.*?>", "", str(desc or "")),
            })
    perf_valid = [r for r in perf_scores if r.get("score") is not None]
    perf_avg = _round(_num(performance.get("avr"))) if isinstance(performance, dict) else None
    if perf_avg is not None and perf_avg >= 80:
        perf_label = "能力评分较强"
    elif perf_avg is not None and perf_avg >= 65:
        perf_label = "能力评分中等"
    elif perf_avg is not None:
        perf_label = "能力评分偏弱"
    else:
        perf_label = "暂无评分"
    performance_evaluation = {
        "avg_score": perf_avg,
        "label": perf_label,
        "scores": perf_scores,
        "strengths": sorted(perf_valid, key=lambda r: r["score"], reverse=True)[:2],
        "weaknesses": sorted(perf_valid, key=lambda r: r["score"])[:2],
    }

    percentile_rows = []
    if isinstance(similar_percent, list):
        for item in similar_percent[-180:]:
            if not isinstance(item, list) or len(item) < 2:
                continue
            value = _num(item[1])
            if value is None:
                continue
            percentile_rows.append({
                "date": _date_from_ms(item[0]),
                "value": _round(value),
            })
    percentile_values = [r["value"] for r in percentile_rows if r.get("value") is not None]
    latest_percentile = percentile_values[-1] if percentile_values else None
    avg_20 = statistics.fmean(percentile_values[-20:]) if len(percentile_values) >= 20 else None
    avg_120 = statistics.fmean(percentile_values[-120:]) if len(percentile_values) >= 120 else None
    change_20 = latest_percentile - percentile_values[-20] if len(percentile_values) >= 20 and latest_percentile is not None else None
    if change_20 is not None and change_20 >= 10:
        percentile_label = "同类百分位上行"
    elif change_20 is not None and change_20 <= -10:
        percentile_label = "同类百分位回落"
    elif latest_percentile is not None:
        percentile_label = "同类百分位平稳"
    else:
        percentile_label = "暂无同类走势"
    similar_percentile_profile = {
        "latest": _round(latest_percentile),
        "avg_20": _round(avg_20),
        "avg_120": _round(avg_120),
        "change_20": _round(change_20),
        "label": percentile_label,
        "rows": percentile_rows[-60:],
        "method": "来自东方财富同类百分位序列，数值仅表示该页面披露的同类相对位置，不等同于未来收益预测。",
    }

    benchmark_series = []
    if isinstance(grand_total, list):
        for item in grand_total[:5]:
            if not isinstance(item, dict):
                continue
            points = []
            for point in item.get("data") or []:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                value = _num(point[1])
                if value is None:
                    continue
                points.append({
                    "date": _date_from_ms(point[0]),
                    "return": _round(value),
                })
            if points:
                benchmark_series.append({
                    "name": item.get("name") or "",
                    "start_date": points[0]["date"],
                    "end_date": points[-1]["date"],
                    "latest_return": points[-1]["return"],
                    "points": points[-90:],
                })
    base_return = benchmark_series[0]["latest_return"] if benchmark_series else None
    for item in benchmark_series:
        item["fund_excess"] = _round(base_return - item["latest_return"]) if base_return is not None and item.get("latest_return") is not None else None
    benchmark_comparison = {
        "series": benchmark_series,
        "as_of": benchmark_series[0]["end_date"] if benchmark_series else "",
        "method": "累计收益对比来自东方财富基金详情页 Data_grandTotal 序列，区间以页面序列起止日期为准。",
    }

    return {
        "source": "东方财富基金详情页",
        "source_url": f"https://fund.eastmoney.com/{code}.html",
        "name": name_match.group(1) if name_match else "",
        "fee": {
            "source_rate": _num(source_rate),
            "current_rate": _num(rate),
        },
        "asset_latest": asset_latest,
        "scale_latest": scale_latest,
        "scale_rows": scale_rows[-8:],
        "managers": manager_rows,
        "stage_returns": stage_returns,
        "performance_evaluation": performance_evaluation,
        "similar_percentile": similar_percentile_profile,
        "benchmark_comparison": benchmark_comparison,
        "buy_redeem": {
            "categories": buy_redeem.get("categories", [])[-4:] if isinstance(buy_redeem, dict) else [],
            "series": buy_redeem.get("series", []) if isinstance(buy_redeem, dict) else [],
        },
        "flow_rows": flow_rows[-8:],
        "flow_summary": flow_summary,
    }


def _rank_row(parts: list[str], rank: int, category_key: str) -> dict:
    ret_1w = _num(parts[7] if len(parts) > 7 else None)
    ret_1m = _num(parts[8] if len(parts) > 8 else None)
    ret_3m = _num(parts[9] if len(parts) > 9 else None)
    ret_6m = _num(parts[10] if len(parts) > 10 else None)
    ret_1y = _num(parts[11] if len(parts) > 11 else None)
    ytd = _num(parts[14] if len(parts) > 14 else None)
    score_parts = [
        (ret_1m, 0.22),
        (ret_3m, 0.34),
        (ret_6m, 0.22),
        (ret_1y, 0.16),
        (ytd, 0.06),
    ]
    hot_score = sum((v or 0) * w for v, w in score_parts)
    if (ret_1m or 0) > 8 and (ret_3m or 0) > 20:
        trend = "短中期强势"
    elif (ret_3m or 0) > 0 and (ret_1y or 0) > 0:
        trend = "趋势向上"
    elif (ret_1m or 0) < 0 and (ret_3m or 0) < 0:
        trend = "弱势回撤"
    else:
        trend = "震荡观察"
    return {
        "rank": rank,
        "code": parts[0] if len(parts) > 0 else "",
        "name": parts[1] if len(parts) > 1 else "",
        "category": _CATEGORY_MAP[category_key][0],
        "date": parts[3] if len(parts) > 3 else "",
        "unit_nav": _num(parts[4] if len(parts) > 4 else None),
        "acc_nav": _num(parts[5] if len(parts) > 5 else None),
        "daily_return": _num(parts[6] if len(parts) > 6 else None),
        "return_1w": ret_1w,
        "return_1m": ret_1m,
        "return_3m": ret_3m,
        "return_6m": ret_6m,
        "return_1y": ret_1y,
        "return_2y": _num(parts[12] if len(parts) > 12 else None),
        "return_3y": _num(parts[13] if len(parts) > 13 else None),
        "return_ytd": ytd,
        "return_since_start": _num(parts[15] if len(parts) > 15 else None),
        "start_date": parts[16] if len(parts) > 16 else "",
        "scale_yi": _num(parts[18] if len(parts) > 18 else None),
        "fee": parts[20] if len(parts) > 20 else "",
        "hot_score": _round(hot_score),
        "trend": trend,
    }


def _fetch_rank(category: str = "all", limit: int = 30, sort: str = "1y") -> dict:
    category_key = _norm_category(category)
    sort_key = _SORT_MAP.get(sort, "1nzf")
    limit = max(5, min(3000, int(limit)))
    cache_key = ("fund_rank", category_key, limit, sort_key)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    today = dt.date.today()
    last_year = _one_year_before(today)
    params = {
        "op": "ph",
        "dt": "kf",
        "ft": _CATEGORY_MAP[category_key][1],
        "rs": "",
        "gs": "0",
        "sc": sort_key,
        "st": "desc",
        "sd": last_year.isoformat(),
        "ed": today.isoformat(),
        "qdii": "",
        "tabSubtype": ",,,,,",
        "pi": "1",
        "pn": str(limit),
        "dx": "1",
        "v": str(time.time()),
    }
    resp = _session().get(_RANK_URL, params=params, headers=_HEADERS, timeout=18)
    resp.raise_for_status()
    text = resp.text
    start = text.find("{")
    if start < 0:
        raise RuntimeError("Eastmoney fund ranking returned an invalid payload")
    payload = demjson.decode(text[start:-1])
    datas = payload.get("datas") or []
    if not datas:
        raise RuntimeError("Eastmoney fund ranking returned empty data")
    rows = []
    for idx, raw in enumerate(datas[:limit], start=1):
        parts = str(raw).split(",")
        if len(parts) >= 12 and parts[0]:
            rows.append(_rank_row(parts, idx, category_key))
    if not rows:
        raise RuntimeError("Eastmoney fund ranking rows could not be parsed")
    result = {
        "source": "东方财富基金排行",
        "source_url": "https://fund.eastmoney.com/data/fundranking.html",
        "category": category_key,
        "category_name": _CATEGORY_MAP[category_key][0],
        "sort": sort,
        "as_of": rows[0].get("date") or today.isoformat(),
        "items": rows,
        "count": len(rows),
    }
    _cache_put(cache_key, result)
    return result


def _mean(values):
    nums = [v for v in values if v is not None]
    return statistics.fmean(nums) if nums else None


def _category_overview() -> list[dict]:
    cache_key = ("fund_category_overview",)
    cached = _cache_get(cache_key)
    if cached:
        return cached["items"]

    keys = ["stock", "hybrid", "bond", "index", "qdii", "fof"]

    def one(key):
        data = _fetch_rank(key, 12, "1y")
        items = data["items"]
        avg_1m = _mean([r["return_1m"] for r in items])
        avg_3m = _mean([r["return_3m"] for r in items])
        avg_1y = _mean([r["return_1y"] for r in items])
        best = items[0] if items else None
        momentum = "偏热" if (avg_1m or 0) > 5 and (avg_3m or 0) > 15 else "正常"
        if (avg_1m or 0) < 0 and (avg_3m or 0) < 0:
            momentum = "降温"
        return {
            "category": key,
            "name": _CATEGORY_MAP[key][0],
            "avg_1m": _round(avg_1m),
            "avg_3m": _round(avg_3m),
            "avg_1y": _round(avg_1y),
            "leader_code": best["code"] if best else "",
            "leader_name": best["name"] if best else "",
            "leader_return_1y": best["return_1y"] if best else None,
            "heat": momentum,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(one, keys))
    _cache_put(cache_key, {"items": items})
    return items


def get_fund_categories() -> dict:
    return {
        "source": "东方财富基金排行",
        "source_url": "https://fund.eastmoney.com/data/fundranking.html",
        "items": _category_overview(),
    }


def get_hot_funds(category: str = "all", limit: int = 30, sort: str = "1y", include_categories: bool = False) -> dict:
    rank = _fetch_rank(category, limit, sort)
    if include_categories:
        rank["categories"] = _category_overview()
    else:
        rank["categories"] = []
    rank["method"] = {
        "ranking": "Eastmoney open fund ranking; sorted by the selected return window.",
        "analysis": "Trend labels in the hot list use only disclosed return windows from the source.",
    }
    return rank


_OPPORTUNITY_BUCKETS = [
    {
        "key": "core",
        "name": "稳健底仓",
        "category": "bond",
        "sort": "1y",
        "profile": "优先债券型基金，关注近 1 年正收益、近 1/3 月不明显转弱，适合作为低波动底仓候选。",
    },
    {
        "key": "balanced",
        "name": "均衡配置",
        "category": "hybrid",
        "sort": "1y",
        "profile": "优先混合型基金，兼顾近 3/6 月趋势和近 1 年表现，适合做权益中枢配置候选。",
    },
    {
        "key": "growth",
        "name": "进攻成长",
        "category": "stock",
        "sort": "6m",
        "profile": "优先股票型基金，关注 3/6 月趋势和 1 年延续性，波动通常更高，只适合小比例进攻仓。",
    },
    {
        "key": "index",
        "name": "指数工具",
        "category": "index",
        "sort": "6m",
        "profile": "优先指数型基金，适合作为行业或宽基工具，需结合估值和仓位控制使用。",
    },
    {
        "key": "overseas",
        "name": "海外分散",
        "category": "qdii",
        "sort": "1y",
        "profile": "优先 QDII 基金，用于降低单一 A 股市场暴露，需注意汇率、额度和海外市场波动。",
    },
]


def _score_opportunity(row: dict, bucket_key: str, risk: str) -> tuple[float, list[str], list[str]]:
    r1m = row.get("return_1m")
    r3m = row.get("return_3m")
    r6m = row.get("return_6m")
    r1y = row.get("return_1y")
    ytd = row.get("return_ytd")
    scale = row.get("scale_yi")
    score = 50.0
    reasons = []
    cautions = []

    if r1y is not None:
        score += 14 if r1y > 8 else 6 if r1y > 0 else -12
        reasons.append(f"近1年{_round(r1y)}%")
    if r6m is not None:
        score += 12 if r6m > 6 else 4 if r6m > 0 else -8
    if r3m is not None:
        score += 14 if r3m > 4 else 5 if r3m > 0 else -10
        reasons.append(f"近3月{_round(r3m)}%")
    if r1m is not None:
        score += 8 if 0 <= r1m <= 8 else -8 if r1m < -3 else -5 if r1m > 18 else 2
    if ytd is not None:
        score += 5 if ytd > 0 else -4
    if scale is not None:
        score += 8 if 5 <= scale <= 200 else 2 if scale > 200 else -8 if scale < 1 else 0
        if scale < 1:
            cautions.append("规模偏小")
    if r1m is not None and r1m > 18:
        cautions.append("近1月涨幅过快，避免追高")
    if r3m is not None and r1m is not None and r3m > 15 and r1m < 0:
        cautions.append("短期开始降温")

    if bucket_key == "core":
        score += 10
        if r1m is not None and r1m < -1:
            score -= 12
            cautions.append("债基短期回撤")
    if bucket_key in ("growth", "index"):
        score += 8 if risk == "aggressive" else -6 if risk == "stable" else 0
    if bucket_key == "overseas":
        score += 4 if risk != "stable" else -4
        cautions.append("注意汇率和海外市场波动")

    if not cautions:
        cautions.append("仍需看具体持仓、费率和回撤")
    label = "重点关注" if score >= 76 else "可以观察" if score >= 62 else "谨慎观察"
    return max(0, min(100, score)), reasons[:3], cautions[:3] + [label]


def _opportunity_candidate(row: dict, bucket: dict, risk: str) -> dict:
    score, reasons, cautions = _score_opportunity(row, bucket["key"], risk)
    return {
        "bucket": bucket["key"],
        "bucket_name": bucket["name"],
        "code": row.get("code"),
        "name": row.get("name"),
        "category": row.get("category"),
        "rank": row.get("rank"),
        "date": row.get("date"),
        "unit_nav": row.get("unit_nav"),
        "daily_return": row.get("daily_return"),
        "return_1m": row.get("return_1m"),
        "return_3m": row.get("return_3m"),
        "return_6m": row.get("return_6m"),
        "return_1y": row.get("return_1y"),
        "return_ytd": row.get("return_ytd"),
        "scale_yi": row.get("scale_yi"),
        "fee": row.get("fee"),
        "trend": row.get("trend"),
        "opportunity_score": _round(score),
        "reasons": reasons,
        "cautions": cautions,
    }


def get_fund_opportunities(risk: str = "balanced", limit: int = 5) -> dict:
    risk = str(risk or "balanced").strip()
    if risk not in ("stable", "balanced", "aggressive"):
        raise ValueError(f"不支持的风险偏好:{risk}")
    limit = max(3, min(10, int(limit)))
    buckets = []
    failed = []
    seen = set()
    for bucket in _OPPORTUNITY_BUCKETS:
        try:
            rank = _fetch_rank(bucket["category"], 80, bucket["sort"])
            rows = []
            for row in rank.get("items", []):
                code = row.get("code")
                if not code or code in seen:
                    continue
                candidate = _opportunity_candidate(row, bucket, risk)
                if candidate["opportunity_score"] >= 52:
                    rows.append(candidate)
            rows.sort(key=lambda x: (x["opportunity_score"], x.get("scale_yi") or 0), reverse=True)
            selected = rows[:limit]
            for row in selected:
                seen.add(row["code"])
            buckets.append({
                "key": bucket["key"],
                "name": bucket["name"],
                "profile": bucket["profile"],
                "category": bucket["category"],
                "sort": bucket["sort"],
                "as_of": rank.get("as_of"),
                "items": selected,
            })
        except Exception as exc:
            failed.append({"bucket": bucket["key"], "name": bucket["name"], "error": str(exc)[:180]})
    if not buckets:
        raise RuntimeError("真实基金机会数据当前不可用")
    all_items = [item for bucket in buckets for item in bucket["items"]]
    all_items.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return {
        "source": "东方财富基金排行",
        "source_url": "https://fund.eastmoney.com/data/fundranking.html",
        "risk": risk,
        "limit": limit,
        "as_of": next((b.get("as_of") for b in buckets if b.get("as_of")), ""),
        "buckets": buckets,
        "top_items": all_items[:min(12, len(all_items))],
        "failed": failed,
        "method": {
            "screening": "只使用东方财富基金排行披露的收益窗口、分类、规模和费率字段，不生成模拟数据。",
            "score": "机会分综合近1月、近3月、近6月、近1年、今年来、规模和风险偏好；高分代表更值得进一步研究，不代表买入建议。",
            "next_step": "点进单只基金后，应继续查看真实净值回撤、波动、基金持仓和同类排名。",
        },
        "risk_note": "基金有波动和本金亏损风险；榜单收益代表历史表现，不保证未来收益。",
    }


def search_funds(keyword: str, limit: int = 20) -> dict:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("基金搜索关键词不能为空")
    limit = max(1, min(50, int(limit)))
    cache_key = ("fund_search_list",)
    cached = _cache_get(cache_key)
    if cached:
        rows = cached["rows"]
    else:
        resp = _session().get(
            _SEARCH_URL,
            params={"v": str(time.time())},
            headers={**_HEADERS, "Referer": "https://fund.eastmoney.com/"},
            timeout=18,
        )
        resp.raise_for_status()
        match = re.search(r"var\s+r\s*=\s*(\[.*\]);?\s*$", resp.text, re.S)
        if not match:
            raise RuntimeError("Eastmoney fund search returned an invalid payload")
        raw_rows = json.loads(match.group(1))
        rows = [{
            "code": str(r[0]),
            "abbr": str(r[1] or ""),
            "name": str(r[2] or ""),
            "type": str(r[3] or ""),
            "pinyin": str(r[4] or ""),
        } for r in raw_rows if len(r) >= 5]
        _cache_put(cache_key, {"rows": rows})

    kw = keyword.upper()
    hits = []
    for row in rows:
        if (
            kw in row["code"].upper()
            or kw in row["name"].upper()
            or kw in row["abbr"].upper()
            or kw in row["pinyin"].upper()
            or kw in row["type"].upper()
        ):
            score = 0
            if row["code"] == keyword:
                score += 100
            if row["code"].startswith(keyword):
                score += 40
            if row["name"] == keyword:
                score += 80
            if keyword in row["name"]:
                score += 30
            if row["abbr"].upper().startswith(kw) or row["pinyin"].upper().startswith(kw):
                score += 20
            hits.append({**row, "match_score": score})
    hits.sort(key=lambda r: (r["match_score"], r["code"]), reverse=True)
    return {
        "source": "东方财富基金代码搜索库",
        "source_url": "https://fund.eastmoney.com/js/fundcode_search.js",
        "keyword": keyword,
        "items": hits[:limit],
        "count": len(hits[:limit]),
    }


def _fund_search_one(code: str) -> dict | None:
    try:
        result = search_funds(code, limit=10)
    except Exception:
        return None
    for item in result.get("items", []):
        if item.get("code") == code:
            return item
    return result.get("items", [None])[0] if result.get("items") else None


def _category_from_fund_type(fund_type: str) -> str:
    text = str(fund_type or "").upper()
    if "QDII" in text:
        return "qdii"
    if "FOF" in text:
        return "fof"
    if "LOF" in text:
        return "lof"
    if "债" in text or "货币" in text:
        return "bond"
    if "指数" in text or "ETF" in text:
        return "index"
    if "股票" in text:
        return "stock"
    if "混合" in text:
        return "hybrid"
    return "all"


def get_fund_peers(code: str, sort: str = "1y", limit: int = 1000) -> dict:
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("基金代码需要是 6 位数字")
    if sort not in _SORT_MAP:
        raise ValueError(f"不支持的同类排序窗口:{sort}")
    info = _fund_search_one(code) or {}
    category = _category_from_fund_type(info.get("type") or "")
    limit = max(100, min(3000, int(limit)))
    rank = _fetch_rank(category, limit, sort)
    items = rank.get("items") or []
    selected_idx = next((i for i, row in enumerate(items) if row.get("code") == code), None)
    selected = items[selected_idx] if selected_idx is not None else None
    if selected_idx is not None:
        peer_rank = selected_idx + 1
        sample_count = len(items)
        beat_ratio = (sample_count - peer_rank) / sample_count * 100 if sample_count else None
        top_percentile = peer_rank / sample_count * 100 if sample_count else None
        start = max(0, selected_idx - 4)
        end = min(len(items), selected_idx + 5)
        neighbors = items[start:end]
        position_label = (
            "同类前10%" if top_percentile is not None and top_percentile <= 10 else
            "同类前25%" if top_percentile is not None and top_percentile <= 25 else
            "同类中游" if top_percentile is not None and top_percentile <= 60 else
            "同类偏后"
        )
    else:
        peer_rank = None
        sample_count = len(items)
        beat_ratio = None
        top_percentile = None
        neighbors = []
        position_label = f"未进入当前同类前{sample_count}名"

    return {
        "source": rank.get("source"),
        "source_url": rank.get("source_url"),
        "code": code,
        "name": (selected or info or {}).get("name") or "",
        "fund_type": info.get("type") or "",
        "category": category,
        "category_name": rank.get("category_name"),
        "sort": sort,
        "as_of": rank.get("as_of"),
        "rank": peer_rank,
        "sample_count": sample_count,
        "beat_ratio": _round(beat_ratio),
        "top_percentile": _round(top_percentile),
        "position_label": position_label,
        "selected": selected,
        "leaders": items[:10],
        "neighbors": neighbors,
        "method": {
            "ranking": "使用东方财富同类型开放式基金排行，按所选收益窗口降序。",
            "limit_note": f"当前最多检查同类前 {limit} 只基金；若未进入样本，显示未进入当前样本榜单。",
        },
    }


def get_fund_dividends(code: str) -> dict:
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("基金代码需要是 6 位数字")
    cache_key = ("fund_dividends", code)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    url = f"https://fundf10.eastmoney.com/fhsp_{code}.html"
    resp = _session().get(
        url,
        headers={**_NAV_HEADERS, "Referer": "https://fundf10.eastmoney.com/"},
        timeout=18,
    )
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    dividend_table = None
    split_table = None
    for table in tables:
        cols = [str(c) for c in table.columns]
        if {"年份", "权益登记日", "除息日", "每份分红", "分红发放日"}.issubset(set(cols)):
            dividend_table = table
        if {"年份", "拆分折算日", "拆分类型", "拆分折算比例"}.issubset(set(cols)):
            split_table = table

    dividends = []
    if dividend_table is not None and not dividend_table.empty:
        for _, row in dividend_table.iterrows():
            d = row.to_dict()
            if "暂无分红信息" in "".join(str(v) for v in d.values()):
                continue
            text = str(d.get("每份分红") or "")
            amount_match = re.search(r"([0-9.]+)", text)
            amount = _num(amount_match.group(1)) if amount_match else None
            dividends.append({
                "year": str(d.get("年份") or ""),
                "record_date": str(d.get("权益登记日") or ""),
                "ex_dividend_date": str(d.get("除息日") or ""),
                "cash_per_share": _round(amount, 4),
                "cash_text": text,
                "payment_date": str(d.get("分红发放日") or ""),
            })

    splits = []
    if split_table is not None and not split_table.empty:
        for _, row in split_table.iterrows():
            d = row.to_dict()
            if "暂无拆分信息" in "".join(str(v) for v in d.values()):
                continue
            splits.append({
                "year": str(d.get("年份") or ""),
                "date": str(d.get("拆分折算日") or ""),
                "type": str(d.get("拆分类型") or ""),
                "ratio": str(d.get("拆分折算比例") or ""),
            })

    dividends.sort(key=lambda r: r.get("ex_dividend_date") or "", reverse=True)
    total_cash = _round(sum((r.get("cash_per_share") or 0) for r in dividends), 4)
    latest = dividends[0] if dividends else None
    years = sorted({r["year"] for r in dividends if r.get("year")}, reverse=True)
    if not dividends:
        label = "暂无分红"
        note = "该基金分红页面显示暂无分红信息。"
    elif len(dividends) >= 10:
        label = "高频分红"
        note = "历史分红次数较多，更偏现金分配特征。"
    elif latest and latest.get("ex_dividend_date", "")[:4] >= str(dt.date.today().year - 1):
        label = "近期有分红"
        note = "近两年存在分红记录，可继续关注分红稳定性。"
    else:
        label = "历史分红"
        note = "历史有分红记录，但近期分红频率不高。"

    result = {
        "source": "天天基金分红送配详情",
        "source_url": url,
        "code": code,
        "dividends": dividends[:50],
        "splits": splits[:20],
        "summary": {
            "dividend_count": len(dividends),
            "split_count": len(splits),
            "total_cash_per_share": total_cash,
            "latest": latest,
            "years": years[:10],
            "label": label,
            "note": note,
        },
        "method": {
            "note": "分红记录来自基金分红送配页面；分红会影响单位净值跳变，累计净值更适合观察长期总回报。",
        },
    }
    _cache_put(cache_key, result)
    return result


def _year_candidates(year: str | None = None) -> list[str]:
    today_year = dt.date.today().year
    if year:
        first = int(year)
    else:
        first = today_year
    return [str(y) for y in range(first, first - 5, -1) if y >= 2000]


def _latest_quarter_rows(df: pd.DataFrame, quarter_col: str) -> tuple[pd.DataFrame, str]:
    if df is None or df.empty or quarter_col not in df.columns:
        return pd.DataFrame(), ""

    def key(value):
        text = str(value)
        y = re.search(r"(20\d{2})", text)
        q = re.search(r"([1-4])\s*季", text)
        if y and q:
            return int(y.group(1)) * 10 + int(q.group(1))
        d = pd.to_datetime(text[:10], errors="coerce")
        if not pd.isna(d):
            return int(d.year) * 10 + int((d.month - 1) // 3 + 1)
        return -1

    labels = list(df[quarter_col].dropna().astype(str).unique())
    if not labels:
        return pd.DataFrame(), ""
    latest_label = max(labels, key=key)
    return df[df[quarter_col].astype(str) == latest_label].copy(), latest_label


def get_fund_portfolio(code: str, year: str | None = None) -> dict:
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise ValueError("基金代码需要是 6 位数字")
    cache_key = ("fund_portfolio", code, year or "latest")
    cached = _cache_get(cache_key)
    if cached:
        return cached

    profile = {}
    try:
        profile = _fetch_profile(code)
    except Exception:
        profile = {}

    stock_df = pd.DataFrame()
    bond_df = pd.DataFrame()
    industry_df = pd.DataFrame()
    used_year = ""
    errors = []

    for y in _year_candidates(year):
        try:
            stock_df = ak.fund_portfolio_hold_em(symbol=code, date=y)
        except Exception as e:
            errors.append(f"{y}股票持仓:{e}")
            stock_df = pd.DataFrame()
        try:
            bond_df = ak.fund_portfolio_bond_hold_em(symbol=code, date=y)
        except Exception:
            bond_df = pd.DataFrame()
        try:
            industry_df = ak.fund_portfolio_industry_allocation_em(symbol=code, date=y)
        except Exception:
            industry_df = pd.DataFrame()
        if not stock_df.empty or not bond_df.empty or not industry_df.empty:
            used_year = y
            break

    if not used_year:
        raise RuntimeError("未取到基金持仓或行业配置真实数据")

    stock_latest, stock_period = _latest_quarter_rows(stock_df, "季度")
    bond_latest, bond_period = _latest_quarter_rows(bond_df, "季度")
    if not industry_df.empty and "截止时间" in industry_df.columns:
        industry_period = str(max(industry_df["截止时间"].astype(str)))
        industry_latest = industry_df[industry_df["截止时间"].astype(str) == industry_period].copy()
    else:
        industry_period = ""
        industry_latest = pd.DataFrame()

    stocks = []
    for _, row in stock_latest.head(20).iterrows():
        d = row.to_dict()
        stocks.append({
            "code": str(_cell(d, "股票代码") or ""),
            "name": str(_cell(d, "股票名称") or ""),
            "nav_ratio": _round(_num(_cell(d, "占净值比例"))),
            "shares_wan": _round(_num(_cell(d, "持股数"))),
            "market_value_wan": _round(_num(_cell(d, "持仓市值"))),
        })

    bonds = []
    for _, row in bond_latest.head(10).iterrows():
        d = row.to_dict()
        bonds.append({
            "code": str(_cell(d, "债券代码") or ""),
            "name": str(_cell(d, "债券名称") or ""),
            "nav_ratio": _round(_num(_cell(d, "占净值比例"))),
            "market_value_wan": _round(_num(_cell(d, "持仓市值"))),
        })

    industries = []
    for _, row in industry_latest.head(12).iterrows():
        d = row.to_dict()
        industries.append({
            "name": str(_cell(d, "行业类别") or ""),
            "nav_ratio": _round(_num(_cell(d, "占净值比例"))),
            "market_value": _round(_num(_cell(d, "市值"))),
        })
    industries = [r for r in industries if r["name"] and r["nav_ratio"] is not None]
    industries.sort(key=lambda r: r["nav_ratio"], reverse=True)

    top10_ratio = _round(sum(r["nav_ratio"] or 0 for r in stocks[:10]))
    top3_ratio = _round(sum(r["nav_ratio"] or 0 for r in stocks[:3]))
    industry_top = industries[0] if industries else None
    concentration = "持仓集中" if (top10_ratio or 0) >= 60 else "适中" if (top10_ratio or 0) >= 35 else "较分散"
    style_note = "权益仓位较高" if (top10_ratio or 0) >= 45 else "权益暴露有限或持仓披露较少"
    if industry_top and industry_top["nav_ratio"] >= 50:
        style_note += f"，行业集中在{industry_top['name']}"

    result = {
        "source": "天天基金投资组合 / 东方财富基金档案",
        "source_url": f"https://fundf10.eastmoney.com/ccmx_{code}.html",
        "code": code,
        "name": profile.get("name") or "",
        "year": used_year,
        "stock_period": stock_period,
        "bond_period": bond_period,
        "industry_period": industry_period,
        "stocks": stocks,
        "bonds": bonds,
        "industries": industries,
        "summary": {
            "top3_stock_ratio": top3_ratio,
            "top10_stock_ratio": top10_ratio,
            "stock_count": len(stocks),
            "bond_count": len(bonds),
            "industry_count": len(industries),
            "concentration": concentration,
            "style_note": style_note,
        },
        "method": {
            "note": "持仓来自基金定期报告披露，通常滞后于实时净值，不代表当前实时持仓。",
            "year_selection": "优先取指定年份或当前年份；无披露时向前寻找最近有真实披露的年份。",
        },
    }
    _cache_put(cache_key, result)
    return result


def compare_funds(codes: list[str], months: int = 36) -> dict:
    clean_codes = []
    for code in codes:
        c = str(code or "").strip()
        if re.fullmatch(r"\d{6}", c) and c not in clean_codes:
            clean_codes.append(c)
    if len(clean_codes) < 2:
        raise ValueError("至少需要 2 只基金代码")
    clean_codes = clean_codes[:8]
    months = max(6, min(120, int(months)))

    def one(code):
        data = analyze_fund(code, months)
        return {
            "code": code,
            "name": data.get("name") or "",
            "trend_state": data.get("trend_state"),
            "latest_nav": data.get("latest", {}).get("unit_nav"),
            "as_of": data.get("as_of"),
            "metrics": data.get("metrics", {}),
            "nav": data.get("nav", []),
        }

    with ThreadPoolExecutor(max_workers=min(4, len(clean_codes))) as pool:
        items = list(pool.map(one, clean_codes))

    frames = []
    for item in items:
        df = pd.DataFrame(item["nav"])
        if df.empty:
            continue
        df = df[["date", "unit_nav"]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df[item["code"]] = pd.to_numeric(df["unit_nav"], errors="coerce")
        frames.append(df[["date", item["code"]]])
    if not frames:
        raise RuntimeError("基金净值对比数据为空")

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="inner")
    if len(merged) < 20:
        raise RuntimeError("基金净值共同交易日样本不足")
    merged = merged.sort_values("date").reset_index(drop=True)
    rebased = merged.copy()
    for code in clean_codes:
        if code in rebased.columns:
            base = _num(rebased.loc[0, code])
            rebased[code] = rebased[code].apply(lambda v: (v / base * 100) if base and _num(v) is not None else None)

    returns = merged.set_index("date").pct_change().dropna()
    corr = returns.corr().round(3).where(pd.notna(returns.corr()), None)
    rows = []
    for item in items:
        m = item["metrics"]
        rows.append({
            "code": item["code"],
            "name": item["name"],
            "trend_state": item["trend_state"],
            "latest_nav": item["latest_nav"],
            "as_of": item["as_of"],
            "return_1m": m.get("return_1m"),
            "return_3m": m.get("return_3m"),
            "return_6m": m.get("return_6m"),
            "return_1y": m.get("return_1y"),
            "annual_volatility": m.get("annual_volatility"),
            "max_drawdown": m.get("max_drawdown"),
            "current_drawdown": m.get("current_drawdown"),
            "dca_score": m.get("dca_score"),
        })
    leaders = {
        "best_3m": max(rows, key=lambda r: r["return_3m"] if r["return_3m"] is not None else -999),
        "best_1y": max(rows, key=lambda r: r["return_1y"] if r["return_1y"] is not None else -999),
        "lowest_vol": min(rows, key=lambda r: r["annual_volatility"] if r["annual_volatility"] is not None else 999),
        "shallowest_drawdown": max(rows, key=lambda r: r["max_drawdown"] if r["max_drawdown"] is not None else -999),
    }
    return {
        "source": "东方财富基金净值走势 / 天天基金历史净值",
        "codes": clean_codes,
        "months": months,
        "items": rows,
        "leaders": leaders,
        "rebased": rebased.tail(360).assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "correlation": {
            "symbols": list(corr.columns),
            "matrix": corr.reset_index().rename(columns={"index": "code"}).to_dict(orient="records"),
        },
        "method": {
            "rebased": "共同净值日期上以首日=100重算，便于横向比较。",
            "correlation": "相关性使用共同日期的日净值收益率计算。",
        },
    }


def analyze_fund_overlap(codes: list[str], year: str | None = None) -> dict:
    clean_codes = []
    for code in codes:
        c = str(code or "").strip()
        if re.fullmatch(r"\d{6}", c) and c not in clean_codes:
            clean_codes.append(c)
    if len(clean_codes) < 2:
        raise ValueError("至少需要 2 只基金代码")
    clean_codes = clean_codes[:8]

    def load_one(code):
        try:
            return get_fund_portfolio(code, year=year), None
        except Exception as e:
            return None, {"code": code, "error": str(e)[:120]}

    with ThreadPoolExecutor(max_workers=min(4, len(clean_codes))) as pool:
        loaded = list(pool.map(load_one, clean_codes))
    portfolios = [p for p, err in loaded if p]
    failed = [err for p, err in loaded if err]
    if len(portfolios) < 2:
        raise RuntimeError("可用于重合度分析的真实持仓数据不足 2 只基金")

    fund_summaries = []
    stock_maps = {}
    industry_maps = {}
    for p in portfolios:
        stock_map = {
            s["code"]: {
                "code": s["code"],
                "name": s["name"],
                "ratio": _num(s.get("nav_ratio")) or 0,
            }
            for s in p.get("stocks", [])
            if s.get("code")
        }
        industry_map = {
            i["name"]: _num(i.get("nav_ratio")) or 0
            for i in p.get("industries", [])
            if i.get("name")
        }
        stock_maps[p["code"]] = stock_map
        industry_maps[p["code"]] = industry_map
        fund_summaries.append({
            "code": p["code"],
            "name": p.get("name") or "",
            "year": p.get("year"),
            "stock_period": p.get("stock_period"),
            "industry_period": p.get("industry_period"),
            "top10_stock_ratio": p.get("summary", {}).get("top10_stock_ratio"),
            "concentration": p.get("summary", {}).get("concentration"),
            "stock_count": len(stock_map),
            "industry_count": len(industry_map),
        })

    pairwise = []
    shared_stock_acc: dict[str, dict] = {}
    shared_industry_acc: dict[str, dict] = {}
    for i in range(len(portfolios)):
        for j in range(i + 1, len(portfolios)):
            a = portfolios[i]["code"]
            b = portfolios[j]["code"]
            a_stocks = stock_maps[a]
            b_stocks = stock_maps[b]
            common_codes = sorted(set(a_stocks) & set(b_stocks))
            common_stocks = []
            overlap_weight = 0.0
            for scode in common_codes:
                ar = a_stocks[scode]["ratio"]
                br = b_stocks[scode]["ratio"]
                min_ratio = min(ar, br)
                overlap_weight += min_ratio
                common = {
                    "code": scode,
                    "name": a_stocks[scode]["name"] or b_stocks[scode]["name"],
                    "fund_a_ratio": _round(ar),
                    "fund_b_ratio": _round(br),
                    "min_ratio": _round(min_ratio),
                }
                common_stocks.append(common)
                cur = shared_stock_acc.setdefault(scode, {
                    "code": scode,
                    "name": common["name"],
                    "funds": set(),
                    "max_ratio": 0.0,
                    "sum_ratio": 0.0,
                })
                cur["funds"].update([a, b])
                cur["max_ratio"] = max(cur["max_ratio"], ar, br)
                cur["sum_ratio"] += ar + br

            a_ind = industry_maps[a]
            b_ind = industry_maps[b]
            common_industries = []
            industry_overlap = 0.0
            for name in sorted(set(a_ind) & set(b_ind)):
                ar = a_ind[name]
                br = b_ind[name]
                min_ratio = min(ar, br)
                if min_ratio <= 0:
                    continue
                industry_overlap += min_ratio
                common_industries.append({
                    "name": name,
                    "fund_a_ratio": _round(ar),
                    "fund_b_ratio": _round(br),
                    "min_ratio": _round(min_ratio),
                })
                cur = shared_industry_acc.setdefault(name, {
                    "name": name,
                    "funds": set(),
                    "max_ratio": 0.0,
                    "sum_ratio": 0.0,
                })
                cur["funds"].update([a, b])
                cur["max_ratio"] = max(cur["max_ratio"], ar, br)
                cur["sum_ratio"] += ar + br

            common_stocks.sort(key=lambda r: r["min_ratio"] or 0, reverse=True)
            common_industries.sort(key=lambda r: r["min_ratio"] or 0, reverse=True)
            if overlap_weight >= 20 or industry_overlap >= 70:
                level = "高度重合"
            elif overlap_weight >= 8 or industry_overlap >= 45:
                level = "中度重合"
            elif overlap_weight > 0 or industry_overlap >= 20:
                level = "低度重合"
            else:
                level = "重合较低"
            pairwise.append({
                "fund_a": a,
                "fund_b": b,
                "fund_a_name": portfolios[i].get("name") or "",
                "fund_b_name": portfolios[j].get("name") or "",
                "common_stock_count": len(common_stocks),
                "stock_overlap_weight": _round(overlap_weight),
                "industry_overlap_weight": _round(industry_overlap),
                "level": level,
                "common_stocks": common_stocks[:12],
                "common_industries": common_industries[:8],
            })

    shared_stocks = []
    for item in shared_stock_acc.values():
        shared_stocks.append({
            "code": item["code"],
            "name": item["name"],
            "fund_count": len(item["funds"]),
            "funds": sorted(item["funds"]),
            "max_ratio": _round(item["max_ratio"]),
            "sum_ratio": _round(item["sum_ratio"]),
        })
    shared_stocks.sort(key=lambda r: (r["fund_count"], r["sum_ratio"] or 0), reverse=True)

    shared_industries = []
    for item in shared_industry_acc.values():
        shared_industries.append({
            "name": item["name"],
            "fund_count": len(item["funds"]),
            "funds": sorted(item["funds"]),
            "max_ratio": _round(item["max_ratio"]),
            "sum_ratio": _round(item["sum_ratio"]),
        })
    shared_industries.sort(key=lambda r: (r["fund_count"], r["sum_ratio"] or 0), reverse=True)

    avg_stock_overlap = _mean([p["stock_overlap_weight"] for p in pairwise])
    avg_industry_overlap = _mean([p["industry_overlap_weight"] for p in pairwise])
    high_pairs = [p for p in pairwise if p["level"] in ("高度重合", "中度重合")]
    if high_pairs:
        conclusion = "组合存在明显重复暴露，继续加仓前应确认这些基金是否承担相同角色。"
    elif shared_industries and (avg_industry_overlap or 0) >= 35:
        conclusion = "个股重合不高，但行业暴露有相似处，适合关注风格集中风险。"
    else:
        conclusion = "披露持仓层面的重合度相对可控，但仍需结合净值相关性和基金经理风格判断。"

    return {
        "source": "天天基金投资组合 / 东方财富基金档案",
        "source_url": "https://fundf10.eastmoney.com/",
        "codes": [p["code"] for p in portfolios],
        "failed": failed,
        "funds": fund_summaries,
        "pairwise": pairwise,
        "shared_stocks": shared_stocks[:20],
        "shared_industries": shared_industries[:12],
        "summary": {
            "fund_count": len(portfolios),
            "pair_count": len(pairwise),
            "avg_stock_overlap_weight": _round(avg_stock_overlap),
            "avg_industry_overlap_weight": _round(avg_industry_overlap),
            "high_overlap_pair_count": len(high_pairs),
            "conclusion": conclusion,
        },
        "method": {
            "stock_overlap": "每对基金共同持股按两只基金占净值比例的较小值求和。",
            "industry_overlap": "共同披露行业按两只基金行业占净值比例的较小值求和。",
            "note": "重合度使用基金定期报告披露持仓，数据通常滞后，不代表实时持仓。",
        },
    }


def _fetch_profile(code: str) -> dict:
    cache_key = ("fund_profile", code)
    cached = _cache_get(cache_key)
    if cached:
        return cached
    resp = _session().get(
        _PROFILE_URL.format(code=code),
        params={"rt": str(time.time())},
        headers=_NAV_HEADERS,
        timeout=12,
    )
    resp.raise_for_status()
    match = re.search(r"jsonpgz\((.*)\);?\s*$", resp.text)
    if not match:
        return {}
    data = json.loads(match.group(1))
    profile = {
        "code": data.get("fundcode") or code,
        "name": data.get("name") or "",
        "estimate_date": data.get("gztime") or data.get("jzrq") or "",
        "estimate_nav": _num(data.get("gsz")),
        "estimate_return": _num(data.get("gszzl")),
    }
    _cache_put(cache_key, profile)
    return profile


def _fetch_nav_history(code: str, months: int = 36) -> pd.DataFrame:
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise ValueError("基金代码需要是 6 位数字")
    months = max(6, min(120, int(months)))
    cache_key = ("fund_nav", code, months)
    cached = _cache_get(cache_key)
    if cached:
        return pd.DataFrame(cached["rows"])

    try:
        text = _fetch_detail_js(code)
        nav_match = re.search(r"var Data_netWorthTrend = (\[.*?\]);", text)
        acc_match = re.search(r"var Data_ACWorthTrend = (\[.*?\]);", text)
        if not nav_match:
            raise RuntimeError("Eastmoney net worth trend payload missing")
        nav_items = json.loads(nav_match.group(1))
        acc_items = json.loads(acc_match.group(1)) if acc_match else []
        acc_map = {int(x[0]): _num(x[1]) for x in acc_items if isinstance(x, list) and len(x) >= 2}
        rows = []
        for item in nav_items:
            ts = int(item.get("x"))
            date = pd.to_datetime(ts, unit="ms").strftime("%Y-%m-%d")
            rows.append({
                "date": date,
                "unit_nav": _num(item.get("y")),
                "acc_nav": acc_map.get(ts),
                "daily_return": _num(item.get("equityReturn")),
                "subscribe_status": "",
                "redeem_status": "",
            })
        rows = [r for r in rows if r["unit_nav"] is not None]
        rows.sort(key=lambda r: r["date"])
        if rows:
            latest_date = pd.to_datetime(rows[-1]["date"])
            cutoff = latest_date - pd.DateOffset(months=months)
            rows = [r for r in rows if pd.to_datetime(r["date"]) >= cutoff]
        if len(rows) >= 20:
            _cache_put(cache_key, {"rows": rows})
            return pd.DataFrame(rows)
    except Exception:
        pass

    target_rows = max(120, min(1200, months * 24 + 40))
    page_size = 20
    items = []
    session = _session()
    for page in range(1, math.ceil(target_rows / page_size) + 2):
        params = {
            "fundCode": code,
            "pageIndex": str(page),
            "pageSize": str(page_size),
        }
        resp = session.get(_NAV_URL, params=params, headers=_NAV_HEADERS, timeout=18)
        resp.raise_for_status()
        data = resp.json().get("Data") or {}
        page_items = data.get("LSJZList") or []
        if not page_items:
            break
        items.extend(page_items)
        if len(items) >= target_rows:
            break
    if not items:
        raise RuntimeError("天天基金历史净值返回为空")
    rows = []
    for item in items:
        date = pd.to_datetime(item.get("FSRQ"), errors="coerce")
        nav = _num(item.get("DWJZ"))
        acc_nav = _num(item.get("LJJZ"))
        daily = _num(item.get("JZZZL"))
        if pd.isna(date) or nav is None:
            continue
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "unit_nav": nav,
            "acc_nav": acc_nav,
            "daily_return": daily,
            "subscribe_status": item.get("SGZT") or "",
            "redeem_status": item.get("SHZT") or "",
        })
    if len(rows) < 20:
        raise RuntimeError("天天基金历史净值有效样本不足")
    rows.sort(key=lambda r: r["date"])
    _cache_put(cache_key, {"rows": rows})
    return pd.DataFrame(rows)


def _period_return(df: pd.DataFrame, days: int):
    latest_date = pd.to_datetime(df.iloc[-1]["date"])
    target = latest_date - pd.Timedelta(days=days)
    base = df[pd.to_datetime(df["date"]) >= target]
    if base.empty:
        base = df
    base_nav = _num(base.iloc[0]["unit_nav"])
    latest_nav = _num(df.iloc[-1]["unit_nav"])
    if base_nav is None or latest_nav is None or base_nav <= 0:
        return None
    return (latest_nav / base_nav - 1) * 100


def _max_drawdown(values: list[float]) -> tuple[float | None, int | None]:
    if not values:
        return None, None
    peak = values[0]
    peak_idx = 0
    max_dd = 0.0
    max_days = 0
    for idx, value in enumerate(values):
        if value > peak:
            peak = value
            peak_idx = idx
        if peak > 0:
            dd = value / peak - 1
            if dd < max_dd:
                max_dd = dd
                max_days = idx - peak_idx
    return max_dd * 100, max_days


def _drawdown_recovery_profile(points: list[tuple[str, float]]) -> dict:
    if not points:
        return {
            "latest_high_date": "",
            "days_since_high": None,
            "episode_count": 0,
            "recovered_count": 0,
            "recovery_rate": None,
            "avg_recovery_days": None,
            "max_recovery_days": None,
            "open_drawdown_days": None,
            "open_drawdown_depth": None,
            "deep_drawdown_count_5": 0,
            "deep_drawdown_count_10": 0,
            "deep_drawdown_count_20": 0,
            "label": "数据不足",
            "episodes": [],
        }

    peak_idx = 0
    peak_date, peak_nav = points[0]
    active = None
    episodes = []

    for idx, (date, nav) in enumerate(points):
        if nav >= peak_nav:
            if active:
                trough_idx = active["trough_idx"]
                episodes.append({
                    "peak_date": active["peak_date"],
                    "trough_date": active["trough_date"],
                    "recovery_date": date,
                    "depth": active["depth"],
                    "drawdown_days": trough_idx - active["peak_idx"],
                    "recovery_days": idx - trough_idx,
                    "total_days": idx - active["peak_idx"],
                    "recovered": True,
                })
                active = None
            peak_idx = idx
            peak_date = date
            peak_nav = nav
            continue

        depth = nav / peak_nav - 1 if peak_nav > 0 else 0
        if not active:
            active = {
                "peak_idx": peak_idx,
                "peak_date": peak_date,
                "trough_idx": idx,
                "trough_date": date,
                "depth": depth,
            }
        elif depth < active["depth"]:
            active["trough_idx"] = idx
            active["trough_date"] = date
            active["depth"] = depth

    open_drawdown_days = None
    open_drawdown_depth = None
    if active:
        last_idx = len(points) - 1
        episodes.append({
            "peak_date": active["peak_date"],
            "trough_date": active["trough_date"],
            "recovery_date": "",
            "depth": active["depth"],
            "drawdown_days": active["trough_idx"] - active["peak_idx"],
            "recovery_days": None,
            "total_days": last_idx - active["peak_idx"],
            "recovered": False,
        })
        open_drawdown_days = last_idx - active["peak_idx"]
        open_drawdown_depth = (points[-1][1] / peak_nav - 1) * 100 if peak_nav > 0 else None

    recovered = [e for e in episodes if e["recovered"] and e["recovery_days"] is not None]
    recovery_days = [e["recovery_days"] for e in recovered]
    episode_count = len(episodes)
    recovered_count = len(recovered)
    recovery_rate = recovered_count / episode_count * 100 if episode_count else None
    avg_recovery_days = statistics.fmean(recovery_days) if recovery_days else None
    max_recovery_days = max(recovery_days) if recovery_days else None
    days_since_high = len(points) - 1 - peak_idx

    if open_drawdown_depth is not None and open_drawdown_depth <= -20:
        label = "深度回撤中"
    elif open_drawdown_depth is not None and open_drawdown_depth <= -10:
        label = "回撤较深"
    elif days_since_high <= 20:
        label = "接近新高"
    elif avg_recovery_days is not None and avg_recovery_days <= 45 and (recovery_rate or 0) >= 60:
        label = "修复较快"
    elif (open_drawdown_days or 0) >= 120 or (avg_recovery_days is not None and avg_recovery_days >= 120):
        label = "修复偏慢"
    else:
        label = "修复中性"

    ranked = sorted(episodes, key=lambda e: e["depth"])[:5]
    formatted = []
    for e in ranked:
        formatted.append({
            "peak_date": e["peak_date"],
            "trough_date": e["trough_date"],
            "recovery_date": e["recovery_date"],
            "depth": _round(e["depth"] * 100),
            "drawdown_days": e["drawdown_days"],
            "recovery_days": e["recovery_days"],
            "total_days": e["total_days"],
            "recovered": e["recovered"],
        })

    depths = [e["depth"] * 100 for e in episodes]
    return {
        "latest_high_date": peak_date,
        "days_since_high": days_since_high,
        "episode_count": episode_count,
        "recovered_count": recovered_count,
        "recovery_rate": _round(recovery_rate),
        "avg_recovery_days": _round(avg_recovery_days),
        "max_recovery_days": max_recovery_days,
        "open_drawdown_days": open_drawdown_days,
        "open_drawdown_depth": _round(open_drawdown_depth),
        "deep_drawdown_count_5": sum(1 for d in depths if d <= -5),
        "deep_drawdown_count_10": sum(1 for d in depths if d <= -10),
        "deep_drawdown_count_20": sum(1 for d in depths if d <= -20),
        "label": label,
        "episodes": formatted,
    }


def _calendar_return_profile(df: pd.DataFrame) -> dict:
    tmp = df.copy()
    tmp["date_dt"] = pd.to_datetime(tmp["date"])
    tmp["unit_nav_num"] = tmp["unit_nav"].apply(_num)
    tmp = tmp.dropna(subset=["unit_nav_num"]).sort_values("date_dt")
    if tmp.empty:
        return {
            "years": [],
            "recent_months": [],
            "month_stats": [],
            "summary": {
                "positive_year_ratio": None,
                "best_year": None,
                "worst_year": None,
                "best_month": None,
                "worst_month": None,
            },
        }

    year_rows = []
    for year, group in tmp.groupby(tmp["date_dt"].dt.year):
        if len(group) < 2:
            continue
        first = float(group.iloc[0]["unit_nav_num"])
        last = float(group.iloc[-1]["unit_nav_num"])
        if first <= 0:
            continue
        year_rows.append({
            "year": int(year),
            "start_date": str(group.iloc[0]["date"]),
            "end_date": str(group.iloc[-1]["date"]),
            "return": _round((last / first - 1) * 100),
            "sample_count": int(len(group)),
        })

    month_rows = []
    for period, group in tmp.groupby(tmp["date_dt"].dt.to_period("M")):
        if len(group) < 2:
            continue
        first = float(group.iloc[0]["unit_nav_num"])
        last = float(group.iloc[-1]["unit_nav_num"])
        if first <= 0:
            continue
        month_rows.append({
            "month": str(period),
            "year": int(period.year),
            "calendar_month": int(period.month),
            "start_date": str(group.iloc[0]["date"]),
            "end_date": str(group.iloc[-1]["date"]),
            "return": _round((last / first - 1) * 100),
        })

    month_stats = []
    for month in range(1, 13):
        vals = [r["return"] for r in month_rows if r["calendar_month"] == month and r["return"] is not None]
        if not vals:
            continue
        month_stats.append({
            "month": month,
            "count": len(vals),
            "avg_return": _round(statistics.fmean(vals)),
            "win_rate": _round(sum(1 for v in vals if v > 0) / len(vals) * 100),
            "best": _round(max(vals)),
            "worst": _round(min(vals)),
        })

    best_year = max(year_rows, key=lambda r: r["return"]) if year_rows else None
    worst_year = min(year_rows, key=lambda r: r["return"]) if year_rows else None
    best_month = max(month_rows, key=lambda r: r["return"]) if month_rows else None
    worst_month = min(month_rows, key=lambda r: r["return"]) if month_rows else None
    positive_year_ratio = (
        sum(1 for r in year_rows if r["return"] and r["return"] > 0) / len(year_rows) * 100
        if year_rows else None
    )

    return {
        "years": year_rows[-10:],
        "recent_months": month_rows[-18:],
        "month_stats": month_stats,
        "summary": {
            "positive_year_ratio": _round(positive_year_ratio),
            "positive_years": sum(1 for r in year_rows if r["return"] and r["return"] > 0),
            "negative_years": sum(1 for r in year_rows if r["return"] is not None and r["return"] <= 0),
            "best_year": best_year,
            "worst_year": worst_year,
            "best_month": best_month,
            "worst_month": worst_month,
        },
    }


def _monthly_positive_ratio(df: pd.DataFrame):
    tmp = df.copy()
    tmp["date_dt"] = pd.to_datetime(tmp["date"])
    tmp["month"] = tmp["date_dt"].dt.to_period("M")
    returns = []
    for _, g in tmp.groupby("month"):
        if len(g) < 2:
            continue
        first = _num(g.iloc[0]["unit_nav"])
        last = _num(g.iloc[-1]["unit_nav"])
        if first and last:
            returns.append(last / first - 1)
    if not returns:
        return None
    return sum(1 for r in returns if r > 0) / len(returns) * 100


def _infer_style(name: str) -> dict:
    checks = [
        ("QDII", ["QDII", "全球", "海外", "纳斯达克", "标普", "恒生"]),
        ("债券/固收", ["债", "固收", "短融", "货币"]),
        ("指数/ETF联接", ["指数", "ETF", "联接", "增强"]),
        ("红利/价值", ["红利", "价值", "低波", "股息"]),
        ("科技成长", ["科技", "人工智能", "芯片", "半导体", "数字"]),
        ("医药医疗", ["医药", "医疗", "生物"]),
        ("新能源", ["新能源", "光伏", "电池", "电动车"]),
        ("资源周期", ["黄金", "有色", "煤炭", "资源", "周期"]),
    ]
    hits = [label for label, words in checks if any(w in name for w in words)]
    return {
        "labels": hits or ["主动/均衡"],
        "basis": "根据基金简称关键词推断，需结合基金合同和持仓进一步确认。",
    }


def _analysis_text(metrics: dict, style: dict) -> list[dict]:
    ret_3m = metrics.get("return_3m")
    ret_1y = metrics.get("return_1y")
    cur_dd = metrics.get("current_drawdown")
    max_dd = metrics.get("max_drawdown")
    vol = metrics.get("annual_volatility")
    notes = []
    if ret_3m is not None and ret_1y is not None:
        if ret_3m > 20 and ret_1y > 30:
            notes.append({"title": "趋势判断", "text": "近 3 个月和近 1 年同时强势，资金偏好较集中；更适合等待回撤或分批进入，而不是一次性追高。"})
        elif ret_3m > 0 and ret_1y < 0:
            notes.append({"title": "趋势判断", "text": "短期转强但一年维度仍未修复，属于反转观察区，重点看净值能否站稳中期均线。"})
        elif ret_3m < 0 and ret_1y < 0:
            notes.append({"title": "趋势判断", "text": "短中期都偏弱，暂时更像风险释放阶段；需要等回撤收敛和月度胜率改善。"})
        else:
            notes.append({"title": "趋势判断", "text": "表现处在震荡区间，优势不在爆发力，而在等待更明确的风格轮动信号。"})
    if cur_dd is not None and max_dd is not None:
        if cur_dd > -3:
            notes.append({"title": "位置感", "text": "当前净值接近阶段高位，趋势好但追涨风险也更高。"})
        elif cur_dd < -15:
            notes.append({"title": "位置感", "text": "当前回撤较深，如果长期逻辑没有破坏，更适合作为定投观察对象而非短线交易对象。"})
        else:
            notes.append({"title": "位置感", "text": "当前有一定回撤但未到极端区，适合把买入节奏和最大承受回撤绑定。"})
    if vol is not None:
        if vol > 35:
            notes.append({"title": "持有体验", "text": "年化波动偏高，净值弹性强，但持有过程会比较颠簸，仓位需要更克制。"})
        elif vol < 12:
            notes.append({"title": "持有体验", "text": "波动偏低，收益弹性通常有限，更适合承担组合稳定器角色。"})
        else:
            notes.append({"title": "持有体验", "text": "波动处在中等区间，适合用固定节奏复盘，而不是每天看净值。"})
    notes.append({"title": "风格线索", "text": f"名称线索显示偏向: {'、'.join(style['labels'])}。这只是简称推断，真实风格仍要看定期报告持仓。"})
    return notes


def _percentile_rank(values: list[float], current: float | None) -> float | None:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v)) and not math.isinf(float(v))]
    if not clean or current is None:
        return None
    return sum(1 for v in clean if v <= current) / len(clean) * 100


def _rolling_return_profile(nav: list[float], windows: list[tuple[str, int]]) -> list[dict]:
    rows = []
    for label, days in windows:
        if len(nav) <= days:
            rows.append({
                "label": label,
                "days": days,
                "current_return": None,
                "historical_percentile": None,
                "avg_return": None,
                "positive_ratio": None,
                "sample_count": 0,
            })
            continue
        values = []
        for idx in range(days, len(nav)):
            base = nav[idx - days]
            latest = nav[idx]
            if base and base > 0:
                values.append((latest / base - 1) * 100)
        current = values[-1] if values else None
        rows.append({
            "label": label,
            "days": days,
            "current_return": _round(current),
            "historical_percentile": _round(_percentile_rank(values, current)),
            "avg_return": _round(statistics.fmean(values)) if values else None,
            "positive_ratio": _round(sum(1 for v in values if v > 0) / len(values) * 100) if values else None,
            "sample_count": len(values),
        })
    return rows


def _fund_timing_profile(df: pd.DataFrame, metrics: dict, recovery_profile: dict) -> dict:
    points = []
    for _, row in df.iterrows():
        value = _num(row["unit_nav"])
        if value is not None:
            points.append((str(row["date"]), value))
    nav = [v for _, v in points]
    if len(nav) < 60:
        return {
            "score": None,
            "label": "样本不足",
            "summary": "真实净值样本不足，暂不生成买入节奏分析。",
            "signals": [],
            "rolling_returns": [],
            "zones": {},
            "method": "仅使用真实披露单位净值计算；样本不足时不推断。",
        }

    latest_nav = nav[-1]
    latest_date = points[-1][0]
    high_nav = max(nav)
    high_idx = max(idx for idx, value in enumerate(nav) if value == high_nav)
    high_date = points[high_idx][0]
    current_dd = (latest_nav / high_nav - 1) * 100 if high_nav > 0 else None

    drawdown_depths = []
    peak = nav[0]
    for value in nav:
        peak = max(peak, value)
        if peak > 0:
            drawdown_depths.append(max(0.0, (1 - value / peak) * 100))
    current_depth = abs(current_dd) if current_dd is not None else None
    drawdown_percentile = _percentile_rank(drawdown_depths, current_depth)

    ma20 = statistics.fmean(nav[-20:]) if len(nav) >= 20 else None
    ma60 = statistics.fmean(nav[-60:]) if len(nav) >= 60 else None
    ma120 = statistics.fmean(nav[-120:]) if len(nav) >= 120 else None
    ret_20 = _period_return(df, 30)
    ret_60 = _period_return(df, 90)
    ret_120 = _period_return(df, 180)
    rolling_returns = _rolling_return_profile(nav, [("近1月", 20), ("近3月", 60), ("近6月", 120)])

    score = 50
    signals = []
    if drawdown_percentile is not None:
        if drawdown_percentile >= 75 and (current_dd or 0) > -30:
            score += 18
            signals.append({"name": "回撤位置", "level": "positive", "text": "当前回撤深度高于大多数历史样本，价格位置更适合分批观察。"})
        elif drawdown_percentile <= 25 and (current_dd or 0) > -3:
            score -= 14
            signals.append({"name": "回撤位置", "level": "negative", "text": "当前接近阶段高位，追涨的容错率较低。"})
        else:
            signals.append({"name": "回撤位置", "level": "neutral", "text": "当前回撤处在历史中间区间，主要看趋势能否确认。"})

    if latest_nav and ma20 and ma60:
        if latest_nav > ma20 > ma60:
            score += 14
            signals.append({"name": "均线结构", "level": "positive", "text": "最新净值站上 20/60 日均值，短中期结构偏强。"})
        elif latest_nav < ma20 < ma60:
            score -= 16
            signals.append({"name": "均线结构", "level": "negative", "text": "最新净值低于 20/60 日均值，短中期结构偏弱。"})
        else:
            signals.append({"name": "均线结构", "level": "neutral", "text": "均线结构尚未形成明确方向。"})

    if ret_20 is not None and ret_60 is not None:
        if ret_20 > 0 and ret_60 > 0:
            score += 10
            signals.append({"name": "动量", "level": "positive", "text": "近 1 月和近 3 月收益同时为正，短期资金反馈较好。"})
        elif ret_20 < 0 and ret_60 < 0:
            score -= 12
            signals.append({"name": "动量", "level": "negative", "text": "近 1 月和近 3 月收益同时为负，仍在弱势释放阶段。"})

    annual_vol = metrics.get("annual_volatility")
    max_dd = metrics.get("max_drawdown")
    if annual_vol is not None and annual_vol > 45:
        score -= 10
        signals.append({"name": "波动", "level": "negative", "text": "年化波动较高，仓位和买入节奏需要更保守。"})
    elif annual_vol is not None and annual_vol < 15:
        score += 5
        signals.append({"name": "波动", "level": "positive", "text": "历史波动较低，更适合承担组合稳定器角色。"})
    if max_dd is not None and max_dd < -40:
        score -= 8
        signals.append({"name": "极端风险", "level": "negative", "text": "历史最大回撤较深，需要预留更长持有周期。"})

    if ret_20 is not None and ret_20 > 12 and (current_dd or 0) > -2:
        score -= 14
        signals.append({"name": "短期过热", "level": "negative", "text": "近 1 月涨幅较快且接近高位，立即重仓的性价比下降。"})

    score = int(max(0, min(100, round(score))))
    if score >= 75:
        label = "适合分批关注"
        summary = "位置和趋势条件较好，可以用分批节奏观察，不适合一次性重仓。"
    elif score >= 60:
        label = "小额定投观察"
        summary = "部分条件成立，但仍需等待更多确认，适合小额或定投方式跟踪。"
    elif score >= 45:
        label = "等待确认"
        summary = "当前没有明显优势，优先等待回撤、趋势或同类排名进一步改善。"
    else:
        label = "暂缓观察"
        summary = "弱势或风险信号较多，当前更适合等待风险释放。"

    zones = {
        "latest_date": latest_date,
        "latest_nav": _round(latest_nav, 4),
        "high_date": high_date,
        "high_nav": _round(high_nav, 4),
        "current_drawdown": _round(current_dd),
        "drawdown_percentile": _round(drawdown_percentile),
        "near_high_nav": _round(high_nav * 0.98, 4),
        "normal_pullback_nav": _round(high_nav * 0.92, 4),
        "deep_pullback_nav": _round(high_nav * 0.85, 4),
        "ma20": _round(ma20, 4),
        "ma60": _round(ma60, 4),
        "ma120": _round(ma120, 4),
    }
    actions = [
        {"title": "现在", "text": summary},
        {"title": "加仓观察条件", "text": "优先看净值重新站稳 20/60 日均值，同时近 1 月收益不再继续走弱。"},
        {"title": "风险控制条件", "text": "若跌破 60 日均值且近 3 月收益转负，应降低买入频率或暂停新增。"},
    ]
    if current_dd is not None and current_dd > -3:
        actions.append({"title": "追高约束", "text": "接近历史高位时，新增资金更适合等待普通回撤区再分批。"})
    elif current_dd is not None and current_dd < -15:
        actions.append({"title": "深回撤约束", "text": "深回撤不是自动买入信号，需要确认基金风格、持仓和同类排名没有同步恶化。"})

    return {
        "score": score,
        "label": label,
        "summary": summary,
        "signals": signals,
        "actions": actions,
        "rolling_returns": rolling_returns,
        "zones": zones,
        "momentum": {
            "return_1m": _round(ret_20),
            "return_3m": _round(ret_60),
            "return_6m": _round(ret_120),
            "latest_above_ma20": bool(latest_nav > ma20) if ma20 else None,
            "latest_above_ma60": bool(latest_nav > ma60) if ma60 else None,
            "open_drawdown_days": recovery_profile.get("open_drawdown_days"),
            "open_drawdown_depth": recovery_profile.get("open_drawdown_depth"),
        },
        "method": "买入节奏只使用真实披露单位净值、历史回撤、滚动收益和均线结构计算；不使用模拟行情，不保证未来收益。",
    }


def analyze_fund(code: str, months: int = 36) -> dict:
    months = max(6, min(120, int(months)))
    df = _fetch_nav_history(code, months)
    nav_points = []
    for _, row in df.iterrows():
        value = _num(row["unit_nav"])
        if value is not None:
            nav_points.append((str(row["date"]), value))
    nav = [v for _, v in nav_points]
    latest = df.iloc[-1]
    first = df.iloc[0]
    latest_nav = _num(latest["unit_nav"])
    first_nav = _num(first["unit_nav"])
    total_return = (latest_nav / first_nav - 1) * 100 if latest_nav and first_nav else None
    returns = pd.Series(nav).pct_change().dropna()
    annual_vol = float(returns.std() * math.sqrt(252) * 100) if len(returns) >= 20 else None
    win_rate = float((returns > 0).mean() * 100) if len(returns) else None
    max_dd, max_dd_days = _max_drawdown(nav)
    recovery_profile = _drawdown_recovery_profile(nav_points)
    calendar_returns = _calendar_return_profile(df)
    high_nav = max(nav)
    current_dd = (latest_nav / high_nav - 1) * 100 if latest_nav and high_nav else None
    ma20 = statistics.fmean(nav[-20:]) if len(nav) >= 20 else None
    ma60 = statistics.fmean(nav[-60:]) if len(nav) >= 60 else None
    ret_1m = _period_return(df, 30)
    ret_3m = _period_return(df, 90)
    ret_6m = _period_return(df, 180)
    ret_1y = _period_return(df, 365)
    positive_month_ratio = _monthly_positive_ratio(df)
    best_day = float(returns.max() * 100) if len(returns) else None
    worst_day = float(returns.min() * 100) if len(returns) else None

    if latest_nav and ma20 and ma60 and latest_nav > ma20 > ma60 and (ret_3m or 0) > 0:
        trend_state = "强趋势上行"
    elif latest_nav and ma60 and latest_nav > ma60 and (current_dd or 0) < -5:
        trend_state = "上行中回撤"
    elif (ret_3m or 0) > 0 and (current_dd or 0) < -10:
        trend_state = "回撤修复"
    elif (ret_3m or 0) < 0 and (ret_1m or 0) < 0:
        trend_state = "弱势下行"
    else:
        trend_state = "震荡观察"

    dca_score = 50
    if ret_1y is not None:
        dca_score += 12 if ret_1y > 10 else -8 if ret_1y < -10 else 0
    if current_dd is not None:
        dca_score += 14 if -18 <= current_dd <= -5 else -8 if current_dd > -2 else 4 if current_dd < -25 else 0
    if annual_vol is not None:
        dca_score += 10 if 12 <= annual_vol <= 32 else -10 if annual_vol > 45 else 2
    if positive_month_ratio is not None:
        dca_score += 8 if positive_month_ratio >= 55 else -6 if positive_month_ratio < 40 else 0
    dca_score = int(max(0, min(100, round(dca_score))))
    dca_label = "适合分批观察" if dca_score >= 70 else "小仓位试探" if dca_score >= 50 else "暂缓观察"

    profile = {}
    fact_sheet = _fund_fact_sheet(str(code))
    rank_name = ""
    try:
        profile = _fetch_profile(str(code))
        rank_name = profile.get("name") or fact_sheet.get("name") or ""
    except Exception:
        profile = {}
    try:
        rank = _fetch_rank("all", 100, "1y")
        match = next((r for r in rank["items"] if r["code"] == str(code)), None)
        if match and not rank_name:
            rank_name = match["name"]
    except Exception:
        pass

    style = _infer_style(rank_name)
    metrics = {
        "total_return": _round(total_return),
        "return_1m": _round(ret_1m),
        "return_3m": _round(ret_3m),
        "return_6m": _round(ret_6m),
        "return_1y": _round(ret_1y),
        "annual_volatility": _round(annual_vol),
        "win_rate": _round(win_rate),
        "positive_month_ratio": _round(positive_month_ratio),
        "max_drawdown": _round(max_dd),
        "max_drawdown_days": max_dd_days,
        "current_drawdown": _round(current_dd),
        "days_since_high": recovery_profile.get("days_since_high"),
        "avg_recovery_days": recovery_profile.get("avg_recovery_days"),
        "recovery_rate": recovery_profile.get("recovery_rate"),
        "drawdown_episode_count": recovery_profile.get("episode_count"),
        "positive_year_ratio": calendar_returns.get("summary", {}).get("positive_year_ratio"),
        "best_year_return": (
            calendar_returns.get("summary", {}).get("best_year") or {}
        ).get("return"),
        "worst_year_return": (
            calendar_returns.get("summary", {}).get("worst_year") or {}
        ).get("return"),
        "best_day": _round(best_day),
        "worst_day": _round(worst_day),
        "ma20": _round(ma20, 4),
        "ma60": _round(ma60, 4),
        "dca_score": dca_score,
        "dca_label": dca_label,
    }
    timing = _fund_timing_profile(df, metrics, recovery_profile)
    return {
        "source": "东方财富基金净值走势 / 天天基金历史净值",
        "source_url": f"https://fund.eastmoney.com/{code}.html",
        "code": str(code),
        "name": rank_name,
        "profile": profile,
        "fact_sheet": fact_sheet,
        "as_of": str(latest["date"]),
        "months": months,
        "sample_count": len(df),
        "latest": {
            "date": str(latest["date"]),
            "unit_nav": latest_nav,
            "acc_nav": _num(latest["acc_nav"]),
            "daily_return": _num(latest["daily_return"]),
            "subscribe_status": latest.get("subscribe_status") or "",
            "redeem_status": latest.get("redeem_status") or "",
        },
        "trend_state": trend_state,
        "style": style,
        "metrics": metrics,
        "timing": timing,
        "drawdown_recovery": recovery_profile,
        "calendar_returns": calendar_returns,
        "insights": _analysis_text(metrics, style),
        "nav": df.tail(360).to_dict(orient="records"),
        "method": {
            "returns": "Period returns are calculated from historical unit NAV.",
            "risk": "Drawdown and annualized volatility use actual disclosed NAV history.",
            "note": "This is risk analysis, not a guaranteed prediction or investment advice.",
        },
    }
