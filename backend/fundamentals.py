# -*- coding: utf-8 -*-
"""
基本面数据与评分
================
- A股:用 BaoStock 取 估值(PE/PB/PS)+ 盈利(ROE/净利率)+ 成长(营收/净利同比)。免费。
- 美股:用 Alpha Vantage 的 OVERVIEW 接口(需免费 Key)。
- 港股:免费数据源有限,暂不支持(返回提示)。

基本面评分(0-100,50 中性)同样透明:每项指标加/减分都列出理由。

⚠️ 基本面适合判断"公司好不好/贵不贵",对中长期更有意义;不预测短期涨跌。
"""

import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config
import data_fetch

_CACHE_TTL = 600
_cache = {}


def _bs_code(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9")):
        return "sh." + s
    if s.startswith(("4", "8")):
        return "bj." + s
    return "sz." + s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clean_value(x):
    if x is None:
        return ""
    s = str(x).strip()
    if s in ("", "False", "nan", "None", "--", "-"):
        return ""
    return s.replace(",", "")


def _pct_value(x):
    s = _clean_value(x).replace("%", "")
    return _num(s)


def _money_value(x):
    """解析同花顺财务摘要里的 1.23亿 / 456万 / 普通数字,统一成元。"""
    s = _clean_value(x)
    if not s:
        return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)(亿|万)?$", s)
    if not m:
        return _num(s)
    n = float(m.group(1))
    unit = m.group(2)
    if unit == "亿":
        return n * 100000000
    if unit == "万":
        return n * 10000
    return n


def _round_or_none(v, digits=2):
    return round(v, digits) if v is not None else None


def _trend_label(values):
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return "数据不足"
    if vals[-1] > vals[0]:
        return "上升"
    if vals[-1] < vals[0]:
        return "下降"
    return "持平"


def _latest_growth_streak(rows_desc, key):
    count = 0
    for i in range(len(rows_desc) - 1):
        cur = rows_desc[i].get(key)
        prev = rows_desc[i + 1].get(key)
        if cur is None or prev is None or cur <= prev:
            break
        count += 1
    return count


def _a_market_code(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9")):
        return "SH" + s
    return "SZ" + s


def _valuation_percentile_a(symbol: str):
    """近五年 PE/PB 历史分位。只返回真实取到的数据,失败项保持为空。"""
    import akshare as ak

    indicators = {
        "pe": ("市盈率(TTM)", "市盈率PE(TTM)", "PE历史分位%"),
        "pb": ("市净率", "市净率PB", "PB历史分位%"),
    }
    out = {
        "window": "近五年",
        "items": {},
        "unavailable": {"ps": "当前真实源未提供稳定的 A股 PS 历史序列"},
    }

    def fetch_one(key, indicator):
        df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator=indicator, period="近五年")
        if df is None or df.empty or "value" not in df.columns:
            raise RuntimeError(f"{indicator}历史估值为空")
        values = []
        for v in df["value"].tolist():
            n = _num(v)
            if n is not None and n > 0:
                values.append(n)
        if not values:
            raise RuntimeError(f"{indicator}历史估值无有效值")
        current = values[-1]
        percentile = sum(1 for v in values if v <= current) / len(values) * 100
        as_of = str(df.iloc[-1].get("date") or "")
        return key, {
            "current": round(current, 2),
            "percentile": round(percentile, 1),
            "sample_size": len(values),
            "as_of": as_of,
        }

    errors = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_map = {
            pool.submit(fetch_one, key, indicator): key
            for key, (indicator, _, _) in indicators.items()
        }
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                result_key, item = fut.result()
                out["items"][result_key] = item
            except Exception as e:
                errors[key] = str(e)[:120]
    if errors:
        out["errors"] = errors
    return out


# ---------------- A股 ----------------

def _bs_rows(rs):
    out = []
    while rs.error_code == "0" and rs.next():
        out.append(rs.get_row_data())
    return out, rs.fields


def _latest_quarter_data(bs, code, query_fn):
    """从当前季度往前找,返回最近一个有数据的季度结果(dict)。"""
    # 财报通常在季度结束后一段时间才披露。直接从当前季度查会大量空跑,
    # 这里按 45 天披露滞后估算最近可能有数据的季度,显著缩短首次请求时间。
    cutoff = dt.date.today() - dt.timedelta(days=45)
    y, q = cutoff.year, (cutoff.month - 1) // 3 + 1
    # 如果 cutoff 还在某个季度中,最近完整季度是前一季。
    q -= 1
    if q == 0:
        q, y = 4, y - 1
    for _ in range(6):
        rs = query_fn(code=code, year=y, quarter=q)
        rows, fields = _bs_rows(rs)
        if rows:
            return dict(zip(fields, rows[0]))
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return {}


def _fundamentals_a(symbol):
    import akshare as ak

    df = ak.stock_financial_abstract_ths(symbol=symbol)
    if df is None or df.empty:
        raise RuntimeError("同花顺财务摘要返回空数据")

    work = df.copy()
    work["报告期"] = work["报告期"].astype(str)
    work = work.sort_values("报告期", ascending=False)
    latest = work.iloc[0]

    annual = work[work["报告期"].str.endswith("-12-31", na=False)].copy()
    if annual.empty:
        annual = work.copy()

    annual_desc = []
    for _, row in annual.iterrows():
        eps = _num(_clean_value(row.get("基本每股收益")))
        cfo_ps = _num(_clean_value(row.get("每股经营现金流")))
        cash_quality = cfo_ps / eps if eps and eps > 0 and cfo_ps is not None else None
        annual_desc.append({
            "period": str(row.get("报告期") or ""),
            "revenue": _money_value(row.get("营业总收入")),
            "profit": _money_value(row.get("净利润")),
            "roe": _pct_value(row.get("净资产收益率")),
            "gross_margin": _pct_value(row.get("销售毛利率")),
            "net_margin": _pct_value(row.get("销售净利率")),
            "debt_ratio": _pct_value(row.get("资产负债率")),
            "cashflow_quality": cash_quality,
        })

    trend_source = list(reversed(annual_desc[:6]))
    trend_rows = [{
        "period": r["period"],
        "revenue_yi": _round_or_none(r["revenue"] / 100000000 if r["revenue"] is not None else None),
        "profit_yi": _round_or_none(r["profit"] / 100000000 if r["profit"] is not None else None),
        "roe": _round_or_none(r["roe"]),
        "gross_margin": _round_or_none(r["gross_margin"]),
        "net_margin": _round_or_none(r["net_margin"]),
        "debt_ratio": _round_or_none(r["debt_ratio"]),
        "cashflow_quality": _round_or_none(r["cashflow_quality"]),
    } for r in trend_source]

    roe = _pct_value(latest.get("净资产收益率"))
    gross_margin = _pct_value(latest.get("销售毛利率"))
    npm = _pct_value(latest.get("销售净利率"))
    debt_ratio = _pct_value(latest.get("资产负债率"))
    yoy_ni = _pct_value(latest.get("净利润同比增长率"))
    yoy_rev = _pct_value(latest.get("营业总收入同比增长率"))
    eps = _num(_clean_value(latest.get("基本每股收益")))
    cfo_ps = _num(_clean_value(latest.get("每股经营现金流")))
    cash_quality = cfo_ps / eps if eps and eps > 0 and cfo_ps is not None else None
    stat_date = str(latest.get("报告期") or "")

    valuation = _valuation_percentile_a(symbol)
    pe = valuation.get("items", {}).get("pe", {}).get("current")
    pb = valuation.get("items", {}).get("pb", {}).get("current")
    ps = None
    pe_pct = valuation.get("items", {}).get("pe", {}).get("percentile")
    pb_pct = valuation.get("items", {}).get("pb", {}).get("percentile")

    revenue_streak = _latest_growth_streak(annual_desc, "revenue")
    profit_streak = _latest_growth_streak(annual_desc, "profit")

    metrics = {
        "市盈率PE(TTM)": round(pe, 2) if pe else None,
        "市净率PB": round(pb, 2) if pb else None,
        "市销率PS": round(ps, 2) if ps else None,
        "PE近五年分位%": _round_or_none(pe_pct, 1),
        "PB近五年分位%": _round_or_none(pb_pct, 1),
        "净资产收益率ROE%": round(roe, 2) if roe is not None else None,
        "毛利率%": round(gross_margin, 2) if gross_margin is not None else None,
        "净利率%": round(npm, 2) if npm is not None else None,
        "资产负债率%": round(debt_ratio, 2) if debt_ratio is not None else None,
        "现金流质量": _round_or_none(cash_quality),
        "营收连续增长年数": revenue_streak,
        "净利润连续增长年数": profit_streak,
        "营收同比%": round(yoy_rev, 2) if yoy_rev is not None else None,
        "净利润同比%": round(yoy_ni, 2) if yoy_ni is not None else None,
    }
    enhanced = {
        "growth_streaks": {
            "revenue_years": revenue_streak,
            "profit_years": profit_streak,
        },
        "trend_summary": {
            "roe": _trend_label([r.get("roe") for r in trend_rows]),
            "gross_margin": _trend_label([r.get("gross_margin") for r in trend_rows]),
            "net_margin": _trend_label([r.get("net_margin") for r in trend_rows]),
            "debt_ratio": _trend_label([r.get("debt_ratio") for r in trend_rows]),
        },
        "trends": trend_rows,
        "valuation_percentiles": valuation,
        "cashflow_quality_note": "现金流质量=每股经营现金流/基本每股收益,用于近似经营现金流对利润的覆盖度",
    }
    return metrics, stat_date, enhanced


# ---------------- 美股 ----------------

def _fundamentals_us(symbol):
    if not config.ALPHAVANTAGE_API_KEY:
        raise PermissionError("美股基本面需要 Alpha Vantage Key,请在 backend/config.py 配置后重试。")
    r = requests.get("https://www.alphavantage.co/query", params={
        "function": "OVERVIEW", "symbol": symbol.upper(),
        "apikey": config.ALPHAVANTAGE_API_KEY}, timeout=20)
    r.raise_for_status()
    js = r.json()
    if not js or "Symbol" not in js:
        msg = js.get("Note") or js.get("Information") or "未取到基本面(可能限频或代码无效)"
        raise RuntimeError(str(msg)[:120])

    def f(key, mul=1):
        v = _num(js.get(key))
        return round(v * mul, 2) if v is not None else None

    metrics = {
        "市盈率PE": f("PERatio"),
        "PEG": f("PEGRatio"),
        "市净率PB": f("PriceToBookRatio"),
        "净资产收益率ROE%": f("ReturnOnEquityTTM", 100),
        "净利率%": f("ProfitMargin", 100),
        "营收同比%": f("QuarterlyRevenueGrowthYOY", 100),
    }
    return metrics, js.get("LatestQuarter")


# ---------------- 评分 ----------------

def _score(metrics):
    """基本面评分(0-100),透明加减分。"""
    points = 50.0
    reasons = []

    def add(name, delta, detail):
        nonlocal points
        points += delta
        reasons.append({"name": name, "delta": round(delta, 1), "detail": detail})

    pe = metrics.get("市盈率PE(TTM)") or metrics.get("市盈率PE")
    roe = metrics.get("净资产收益率ROE%")
    npm = metrics.get("净利率%")
    growth = metrics.get("净利润同比%") or metrics.get("营收同比%")
    pb = metrics.get("市净率PB")
    pe_pct = metrics.get("PE近五年分位%")
    pb_pct = metrics.get("PB近五年分位%")
    gross_margin = metrics.get("毛利率%")
    debt_ratio = metrics.get("资产负债率%")
    cash_quality = metrics.get("现金流质量")
    revenue_streak = metrics.get("营收连续增长年数")
    profit_streak = metrics.get("净利润连续增长年数")

    if pe is not None:
        if pe <= 0:
            add("估值", -12, f"PE={pe},公司处于亏损")
        elif pe < 15:
            add("估值", 12, f"PE={pe},估值偏低")
        elif pe < 30:
            add("估值", 5, f"PE={pe},估值合理")
        elif pe < 60:
            add("估值", -4, f"PE={pe},估值偏高")
        else:
            add("估值", -10, f"PE={pe},估值很高")

    if roe is not None:
        if roe >= 20:
            add("盈利能力", 14, f"ROE={roe}%,非常强")
        elif roe >= 12:
            add("盈利能力", 9, f"ROE={roe}%,良好")
        elif roe >= 5:
            add("盈利能力", 2, f"ROE={roe}%,一般")
        else:
            add("盈利能力", -8, f"ROE={roe}%,偏弱")

    if growth is not None:
        if growth >= 25:
            add("成长性", 12, f"利润/营收同比 +{growth}%,高成长")
        elif growth >= 5:
            add("成长性", 6, f"同比 +{growth}%,稳健增长")
        elif growth >= -5:
            add("成长性", 0, f"同比 {growth}%,基本持平")
        else:
            add("成长性", -10, f"同比 {growth}%,业绩下滑")

    if npm is not None:
        if npm >= 20:
            add("利润率", 6, f"净利率 {npm}%,很高")
        elif npm >= 8:
            add("利润率", 3, f"净利率 {npm}%,健康")
        elif npm < 0:
            add("利润率", -6, f"净利率 {npm}%,亏损")

    if pb is not None and pb > 0:
        if pb < 1.5:
            add("PB", 4, f"PB={pb},账面便宜")
        elif pb > 10:
            add("PB", -4, f"PB={pb},账面很贵")

    if pe_pct is not None:
        if pe_pct <= 25:
            add("PE分位", 5, f"近五年PE分位 {pe_pct}%,历史相对低位")
        elif pe_pct >= 80:
            add("PE分位", -5, f"近五年PE分位 {pe_pct}%,历史相对高位")

    if pb_pct is not None:
        if pb_pct <= 25:
            add("PB分位", 4, f"近五年PB分位 {pb_pct}%,历史相对低位")
        elif pb_pct >= 80:
            add("PB分位", -4, f"近五年PB分位 {pb_pct}%,历史相对高位")

    if gross_margin is not None:
        if gross_margin >= 40:
            add("毛利率", 4, f"毛利率 {gross_margin}%,产品盈利空间较好")
        elif gross_margin < 15:
            add("毛利率", -3, f"毛利率 {gross_margin}%,盈利缓冲较薄")

    if debt_ratio is not None:
        if debt_ratio <= 45:
            add("负债率", 4, f"资产负债率 {debt_ratio}%,财务杠杆较低")
        elif debt_ratio >= 70:
            add("负债率", -6, f"资产负债率 {debt_ratio}%,财务杠杆偏高")

    if cash_quality is not None:
        if cash_quality >= 1:
            add("现金流质量", 5, f"经营现金流/利润约 {cash_quality},利润含金量较好")
        elif cash_quality < 0.5:
            add("现金流质量", -5, f"经营现金流/利润约 {cash_quality},现金回款偏弱")

    if revenue_streak is not None and profit_streak is not None:
        streak = min(revenue_streak, profit_streak)
        if streak >= 3:
            add("连续增长", 5, f"营收与净利润至少连续增长 {streak} 年")
        elif revenue_streak == 0 and profit_streak == 0:
            add("连续增长", -3, "最近年度营收与净利润未形成连续增长")

    total = max(0.0, min(100.0, points))
    if total >= 65:
        rating = "优质"
    elif total >= 50:
        rating = "中性偏好"
    elif total >= 38:
        rating = "一般"
    else:
        rating = "偏弱"
    return round(total, 1), rating, reasons


def get_fundamentals(market: str, symbol: str) -> dict:
    symbol = symbol.strip()
    cache_key = (market, symbol)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    enhanced = None
    if market == "A股":
        metrics, stat_date, enhanced = _fundamentals_a(symbol)
    elif market == "美股":
        metrics, stat_date = _fundamentals_us(symbol)
    elif market == "港股":
        result = {"available": False,
                  "message": "港股暂无免费基本面数据源(可后续接入 Tushare 港股财务)。"}
        _cache[cache_key] = (time.time(), result)
        return result
    else:
        raise ValueError(f"不支持的市场:{market}")

    if not any(v is not None for v in metrics.values()):
        result = {"available": False, "message": "未取到基本面数据。"}
        _cache[cache_key] = (time.time(), result)
        return result

    score, rating, reasons = _score(metrics)
    result = {
        "available": True,
        "market": market, "symbol": symbol,
        "as_of": stat_date,
        "metrics": metrics,
        "score": score, "rating": rating, "reasons": reasons,
    }
    if enhanced:
        result["enhanced"] = enhanced
    _cache[cache_key] = (time.time(), result)
    return result
