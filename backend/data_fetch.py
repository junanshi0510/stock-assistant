# -*- coding: utf-8 -*-
"""
数据抓取模块
------------
负责从 akshare 抓取 A股 / 港股 / 美股 的历史日线行情。
对外只暴露两个函数:
    - get_history(market, symbol, start, end)  抓取某只股票的历史行情
    - search_us_symbol(keyword)                根据美股代码/名称查找 akshare 内部代码

所有返回的 DataFrame 都被统一成下面这套英文列名,方便后续分析:
    date(日期), open(开盘), close(收盘), high(最高), low(最低), volume(成交量)
"""

import os
import contextlib
import io

# ── 代理策略:国内数据源直连,海外数据源走系统代理 ──────────────────────
# 国内源(东方财富/新浪/Tushare)在【国内服务器】,本应直连;但 Python 的 requests
# 会自动读取 Windows 系统代理(如 Clash 的 127.0.0.1:7897)把国内请求塞进 VPN,
# 导致 "Unable to connect to proxy" 报错。
# 这里只对国内域名设置 NO_PROXY(绕过代理直连),海外源(Polygon/AlphaVantage)
# 不在列表里,仍按系统代理走 —— 在国内访问海外源往往正需要代理。
# (BaoStock 走自有 socket 协议,不读代理环境变量,不受影响。)
os.environ["NO_PROXY"] = (
    "eastmoney.com,sina.com.cn,sina.com,sinajs.cn,"
    "tushare.pro,waditu.com,baostock.com,"
    "sse.com.cn,szse.cn,127.0.0.1,localhost"
)
os.environ["no_proxy"] = os.environ["NO_PROXY"]
# ──────────────────────────────────────────────────────────────────

import functools
import json
import re
import time
import datetime
import threading
import pandas as pd

# pandas 3.0 默认用 pyarrow 字符串(其正则引擎不支持 \u 转义),会让 akshare
# 的部分接口(如个股新闻)解析报错。关掉它、回到兼容性更好的 object 字符串。
try:
    pd.set_option("future.infer_string", False)
except Exception:
    pass

import requests
import akshare as ak

import config


class SourceUnavailable(Exception):
    """数据源未配置(如缺少 API Key),应直接跳过、不重试。"""


# BaoStock 用单条 socket 连接,非线程安全;批量并发扫描时用锁串行化它的调用。
_bs_lock = threading.Lock()

# 行情结果的内存缓存(批量扫描 / 反复查询时避免重复抓取)。
_CACHE_TTL = 600  # 秒
_cache = {}
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_put(key, df):
    with _cache_lock:
        _cache[key] = (time.time(), df)


def _retry(func, *args, attempts: int = 3, delay: float = 1.5, **kwargs):
    """对网络类调用做简单重试,缓解偶发的连接抖动;未配置的源不重试。"""
    last = None
    for i in range(attempts):
        try:
            return func(*args, **kwargs)
        except SourceUnavailable:
            raise
        except Exception as e:  # 网络异常等
            last = e
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    raise last

# akshare 返回的中文列名 -> 我们统一使用的英文列名
_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}

# 支持的市场标识
MARKETS = ["A股", "港股", "美股"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """把 akshare 的原始 DataFrame 统一成标准列名 + 类型。"""
    if df is None or df.empty:
        raise ValueError("没有取到数据,请检查股票代码是否正确,或稍后重试。")

    # 有的数据源(如新浪)把日期放在索引里,先还原成普通列
    if "date" not in df.columns and "日期" not in df.columns:
        df = df.reset_index()

    df = df.rename(columns=_COLUMN_MAP)
    keep = ["date", "open", "close", "high", "low", "volume"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise ValueError(f"数据缺少必要的列: {missing}")

    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    return df


@functools.lru_cache(maxsize=1)
def _us_spot_table() -> pd.DataFrame:
    """美股代码映射表。优先用新浪真实美股列表,避免东财全市场表偶发卡死。"""
    rows = []
    url = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_CategoryService.getList"
    for page in range(1, 4):  # 新浪每页最多约 20 条;搜索框保持轻量,避免冷启动卡住
        resp = requests.get(url, params={
            "page": page,
            "num": 20,
            "sort": "marketvalue",
            "asc": 0,
            "market": "",
            "id": "",
        }, timeout=12)
        resp.raise_for_status()
        m = re.search(r"var\s+x=\((.*)\);\s*$", resp.text, re.S)
        if not m:
            break
        payload = json.loads(m.group(1))
        data = payload.get("data") or []
        if not data:
            break
        for it in data:
            symbol = str(it.get("symbol") or "").upper()
            if not symbol:
                continue
            rows.append({
                "代码": symbol,
                "名称": it.get("cname") or it.get("name") or symbol,
            })
    if rows:
        return pd.DataFrame(rows).drop_duplicates("代码").reset_index(drop=True)

    # 真实备选源;如果也失败,由调用方抛错。
    table = ak.stock_us_spot_em()
    out = table[["代码", "名称"]].copy()
    out["代码"] = out["代码"].astype(str).str.split(".").str[-1].str.upper()
    return out


def search_us_symbol(keyword: str) -> pd.DataFrame:
    """
    根据关键词(如 AAPL / TSLA / 苹果)在美股代码表里查找。
    返回包含 '代码'(ticker,如 AAPL) 和 '名称' 的若干行。
    """
    kw = keyword.strip().upper()
    # 用户已经输入明显 ticker 时,无需全市场搜索;直接允许使用该代码。
    # 名称只在真实列表命中时补充,否则保留为代码本身。
    if re.fullmatch(r"[A-Z.]{1,8}", kw):
        try:
            table = _us_spot_table()
            mask = table["代码"].astype(str).str.upper().eq(kw)
            if mask.any():
                return table.loc[mask, ["代码", "名称"]].head(20).reset_index(drop=True)
        except Exception:
            pass
        return pd.DataFrame([{"代码": kw, "名称": kw}])

    table = _us_spot_table()
    mask = (
        table["名称"].astype(str).str.upper().str.contains(kw, na=False)
        | table["代码"].astype(str).str.upper().str.contains(kw, na=False)
    )
    return table.loc[mask, ["代码", "名称"]].head(20).reset_index(drop=True)


def _resolve_us_code(symbol: str) -> str:
    """
    把用户输入的美股代码(如 AAPL)解析成 akshare 需要的内部代码(如 105.AAPL)。
    如果用户已经传入带前缀的代码,则原样返回。
    """
    if "." in symbol and symbol.split(".")[0].isdigit():
        return symbol  # 已经是 105.AAPL 这种格式
    hits = search_us_symbol(symbol)
    # 优先精确匹配 ticker
    for _, row in hits.iterrows():
        code = str(row["代码"])
        if code.split(".")[-1].upper() == symbol.strip().upper():
            return code
    if not hits.empty:
        return str(hits.iloc[0]["代码"])
    raise ValueError(f"找不到美股代码: {symbol}")


def _sina_a_symbol(code: str) -> str:
    """A股代码转新浪格式:6/9 开头→sh,4/8 开头→bj(北交所),其余→sz。"""
    code = code.strip()
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def _ymd_dash(s: str) -> str:
    """20240101 -> 2024-01-01"""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


# ========== 专业数据源 ==========

# —— BaoStock(A股,免费免 token,自有 socket 协议)——
_bs_logged_in = False

def _bs():
    global _bs_logged_in
    import baostock as bs
    if not _bs_logged_in:
        bs.login()
        _bs_logged_in = True
    return bs

def _src_a_baostock(symbol, start, end):
    prefix = ("sh." if symbol.startswith(("6", "9"))
              else "bj." if symbol.startswith(("4", "8")) else "sz.")
    today = datetime.date.today().strftime("%Y-%m-%d")
    e = min(_ymd_dash(end), today)
    # 单连接非线程安全,串行化 login + 查询
    with _bs_lock:
        bs = _bs()
        rs = bs.query_history_k_data_plus(
            prefix + symbol, "date,open,high,low,close,volume",
            start_date=_ymd_dash(start), end_date=e, frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock 查询失败: {rs.error_msg}")
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def _src_a_tencent(symbol, start, end):
    """腾讯证券 A股前复权日线。真实行情源,带 timeout,作为 A股默认快路。"""
    tx_symbol = _sina_a_symbol(symbol)  # sh600519 / sz000001 / bjxxxxxx
    today = datetime.date.today().strftime("%Y%m%d")
    e = min(end, today)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_zh_a_hist_tx(
            symbol=tx_symbol,
            start_date=start,
            end_date=e,
            adjust="qfq",
            timeout=12,
        )
    if df is None or df.empty:
        raise ValueError("腾讯证券返回空数据")
    df = df.copy()
    if "amount" in df.columns and "volume" not in df.columns:
        # 腾讯源返回成交额而不是成交量。价格/收益/图表完全真实;
        # 技术评分中的量能因子用成交额做代理。
        df["volume"] = df["amount"]
    return df


# —— Tushare Pro(A股/港股,需 token)——
def _tushare():
    if not config.TUSHARE_TOKEN:
        raise SourceUnavailable("未配置 TUSHARE_TOKEN")
    import tushare as ts
    return ts, ts.pro_api(config.TUSHARE_TOKEN)

def _src_a_tushare(symbol, start, end):
    ts, _pro = _tushare()
    suffix = (".SH" if symbol.startswith(("6", "9"))
              else ".BJ" if symbol.startswith(("4", "8")) else ".SZ")
    df = ts.pro_bar(ts_code=symbol + suffix, adj="qfq",
                    start_date=start, end_date=end, freq="D")
    if df is None or df.empty:
        raise RuntimeError("Tushare 返回空")
    return df.rename(columns={"trade_date": "date", "vol": "volume"})

def _src_hk_tushare(symbol, start, end):
    _ts, pro = _tushare()
    df = pro.hk_daily(ts_code=symbol.zfill(5) + ".HK",
                      start_date=start, end_date=end)
    if df is None or df.empty:
        raise RuntimeError("Tushare 港股返回空")
    return df.rename(columns={"trade_date": "date", "vol": "volume"})


# —— Polygon.io(美股,需 API Key)——
def _src_us_polygon(symbol, start, end):
    if not config.POLYGON_API_KEY:
        raise SourceUnavailable("未配置 POLYGON_API_KEY")
    url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}"
           f"/range/1/day/{_ymd_dash(start)}/{_ymd_dash(end)}")
    r = requests.get(url, params={"adjusted": "true", "sort": "asc",
                                  "limit": 50000, "apiKey": config.POLYGON_API_KEY},
                     timeout=15)
    r.raise_for_status()
    js = r.json()
    results = js.get("results") or []
    if not results:
        raise RuntimeError(f"Polygon 无数据 (status={js.get('status')})")
    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["t"], unit="ms")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                            "c": "close", "v": "volume"})
    return df[["date", "open", "high", "low", "close", "volume"]]


# —— Alpha Vantage(美股,需 API Key;免费档为不复权日线)——
def _src_us_alphavantage(symbol, start, end):
    if not config.ALPHAVANTAGE_API_KEY:
        raise SourceUnavailable("未配置 ALPHAVANTAGE_API_KEY")
    r = requests.get("https://www.alphavantage.co/query", params={
        "function": "TIME_SERIES_DAILY", "symbol": symbol.upper(),
        "outputsize": "full", "apikey": config.ALPHAVANTAGE_API_KEY}, timeout=20)
    r.raise_for_status()
    js = r.json()
    series = js.get("Time Series (Daily)")
    if not series:
        msg = js.get("Note") or js.get("Information") or js.get("Error Message") or "无数据"
        raise RuntimeError(f"Alpha Vantage: {msg}")
    recs = [{"date": d, "open": v["1. open"], "high": v["2. high"],
             "low": v["3. low"], "close": v["4. close"], "volume": v["5. volume"]}
            for d, v in series.items()]
    return pd.DataFrame(recs)


# ========== 免费备用源(akshare:东方财富 / 新浪)==========

def _src_a_em(symbol, start, end):
    return ak.stock_zh_a_hist(symbol=symbol, period="daily",
                              start_date=start, end_date=end, adjust="qfq")

def _src_a_sina(symbol, start, end):
    return ak.stock_zh_a_daily(symbol=_sina_a_symbol(symbol),
                               start_date=start, end_date=end, adjust="qfq")

def _src_hk_em(symbol, start, end):
    return ak.stock_hk_hist(symbol=symbol, period="daily",
                            start_date=start, end_date=end, adjust="qfq")

def _src_hk_sina(symbol, start, end):
    return ak.stock_hk_daily(symbol=symbol, adjust="qfq")

def _src_us_em(symbol, start, end):
    return ak.stock_us_hist(symbol=_resolve_us_code(symbol), period="daily",
                            start_date=start, end_date=end, adjust="qfq")

def _src_us_sina(symbol, start, end):
    return ak.stock_us_daily(symbol=symbol.upper(), adjust="qfq")


# 当前成交价必须与未复权历史价格比较，否则分红、拆股会造成虚假的价位命中。
def _src_a_tushare_raw(symbol, start, end):
    ts, _pro = _tushare()
    suffix = (".SH" if symbol.startswith(("6", "9"))
              else ".BJ" if symbol.startswith(("4", "8")) else ".SZ")
    df = ts.pro_bar(ts_code=symbol + suffix, adj=None,
                    start_date=start, end_date=end, freq="D")
    if df is None or df.empty:
        raise RuntimeError("Tushare 返回空")
    return df.rename(columns={"trade_date": "date", "vol": "volume"})


def _src_a_tencent_raw(symbol, start, end):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_zh_a_hist_tx(
            symbol=_sina_a_symbol(symbol),
            start_date=start,
            end_date=min(end, datetime.date.today().strftime("%Y%m%d")),
            adjust="",
            timeout=12,
        )
    if df is None or df.empty:
        raise RuntimeError("腾讯证券返回空")
    df = df.copy()
    if "amount" in df.columns and "volume" not in df.columns:
        df["volume"] = df["amount"]
    return df


def _src_a_baostock_raw(symbol, start, end):
    prefix = ("sh." if symbol.startswith(("6", "9"))
              else "bj." if symbol.startswith(("4", "8")) else "sz.")
    with _bs_lock:
        bs = _bs()
        rs = bs.query_history_k_data_plus(
            prefix + symbol,
            "date,open,high,low,close,volume",
            start_date=_ymd_dash(start),
            end_date=min(_ymd_dash(end), datetime.date.today().strftime("%Y-%m-%d")),
            frequency="d",
            adjustflag="3",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock 查询失败: {rs.error_msg}")
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def _src_a_em_raw(symbol, start, end):
    return ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=start, end_date=end, adjust=""
    )


def _src_hk_em_raw(symbol, start, end):
    return ak.stock_hk_hist(
        symbol=symbol, period="daily", start_date=start, end_date=end, adjust=""
    )


def _src_us_polygon_raw(symbol, start, end):
    if not config.POLYGON_API_KEY:
        raise SourceUnavailable("未配置 POLYGON_API_KEY")
    url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}"
           f"/range/1/day/{_ymd_dash(start)}/{_ymd_dash(end)}")
    response = requests.get(
        url,
        params={"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": config.POLYGON_API_KEY},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("results") or []
    if not rows:
        raise RuntimeError(f"Polygon 无数据 (status={payload.get('status')})")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["t"], unit="ms")
    return df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})


def _src_us_em_raw(symbol, start, end):
    return ak.stock_us_hist(
        symbol=_resolve_us_code(symbol), period="daily", start_date=start, end_date=end, adjust=""
    )


# 每个市场的数据源优先级:专业源在前,免费备用源在后,自动降级。
_SOURCES = {
    "A股": [("Tushare", _src_a_tushare), ("腾讯证券", _src_a_tencent), ("BaoStock", _src_a_baostock),
            ("东方财富", _src_a_em), ("新浪", _src_a_sina)],
    "港股": [("Tushare", _src_hk_tushare),
            ("东方财富", _src_hk_em), ("新浪", _src_hk_sina)],
    "美股": [("Polygon", _src_us_polygon), ("AlphaVantage", _src_us_alphavantage),
            ("东方财富", _src_us_em), ("新浪", _src_us_sina)],
}

_PRICE_LEVEL_SOURCES = {
    "A股": [
        ("Tushare 未复权日线", _src_a_tushare_raw),
        ("腾讯证券未复权日线", _src_a_tencent_raw),
        ("BaoStock 未复权日线", _src_a_baostock_raw),
        ("东方财富未复权日线", _src_a_em_raw),
    ],
    "港股": [
        ("Tushare 港股日线", _src_hk_tushare),
        ("东方财富港股未复权日线", _src_hk_em_raw),
    ],
    "美股": [
        ("Polygon 未复权日线", _src_us_polygon_raw),
        ("Alpha Vantage 未复权日线", _src_us_alphavantage),
        ("东方财富美股未复权日线", _src_us_em_raw),
    ],
}


def _filter_dates(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s, e = pd.to_datetime(start), pd.to_datetime(end)
    return df[(df["date"] >= s) & (df["date"] <= e)].reset_index(drop=True)


def get_history(market: str, symbol: str, start: str = "20230101",
                end: str = "20500101") -> pd.DataFrame:
    """
    抓取某只股票的历史日线行情(前复权)。

    多数据源容错:依次尝试 东方财富 → 新浪,任一源成功即返回;
    全部失败时抛出汇总错误。

    参数:
        market: "A股" / "港股" / "美股"
        symbol: 股票代码
                - A股:  6 位代码,如 600519、000001
                - 港股:  5 位代码,如 00700
                - 美股:  ticker,如 AAPL、TSLA
        start / end: 日期字符串,格式 YYYYMMDD

    返回: 标准列名的 DataFrame。
    """
    symbol = symbol.strip()
    if market not in _SOURCES:
        raise ValueError(f"不支持的市场: {market}(可选: {MARKETS})")

    cache_key = (market, symbol, start, end)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.copy()

    errors = []
    for name, src in _SOURCES[market]:
        try:
            raw = _retry(src, symbol, start, end, attempts=2)
            df = _filter_dates(_normalize(raw), start, end)
            if not df.empty:
                _cache_put(cache_key, df)
                return df.copy()
            errors.append(f"{name}: 返回空数据")
        except SourceUnavailable:
            continue  # 未配置的源(缺 Key)静默跳过,不计入失败原因
        except Exception as e:
            errors.append(f"{name}: {str(e)[:80]}")
    if errors:
        raise ValueError("数据获取失败,请检查代码是否正确:\n" + "\n".join(errors))
    raise ValueError("未取到数据(可用数据源均无此代码的数据,请检查代码)。")


def get_history_months(market: str, symbol: str, months: int,
                       fetch_months: int = 60) -> pd.DataFrame:
    """
    按"月数"取行情,但内部【始终抓取较宽的固定窗口并缓存】,再切片返回。
    这样 analyze(短)、ml/backtest(长)对同一只股票只触发【一次】网络抓取,
    其余命中缓存 —— 显著加快分析页(尤其美股)的整体响应。
    """
    end = datetime.date.today()
    fetch_start = end - datetime.timedelta(days=fetch_months * 31)
    full = get_history(market, symbol,
                       fetch_start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if months >= fetch_months:
        return full
    cutoff = pd.to_datetime(end - datetime.timedelta(days=months * 31))
    sliced = full[full["date"] >= cutoff].reset_index(drop=True)
    return sliced if not sliced.empty else full


def get_price_level_history_months(
    market: str,
    symbol: str,
    months: int = 60,
) -> tuple[pd.DataFrame, str]:
    """Fetch unadjusted daily bars for comparing with a current live price."""
    symbol = str(symbol or "").strip()
    if market not in _PRICE_LEVEL_SOURCES:
        raise ValueError(f"不支持的市场: {market}(可选: {MARKETS})")
    months = max(6, min(120, int(months)))
    end = datetime.date.today()
    start = end - datetime.timedelta(days=months * 31)
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    cache_key = ("price_level_raw", market, symbol, start_text, end_text)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached["frame"].copy(), cached["source"]

    errors = []
    for source_name, source in _PRICE_LEVEL_SOURCES[market]:
        try:
            raw = _retry(source, symbol, start_text, end_text, attempts=2)
            frame = _filter_dates(_normalize(raw), start_text, end_text)
            if frame.empty:
                errors.append(f"{source_name}: 返回空数据")
                continue
            _cache_put(cache_key, {"frame": frame.copy(), "source": source_name})
            return frame, source_name
        except SourceUnavailable:
            continue
        except Exception as error:
            errors.append(f"{source_name}: {str(error)[:100]}")
    if errors:
        raise ValueError("未复权历史价格获取失败:\n" + "\n".join(errors))
    raise ValueError("没有已配置且可用的未复权历史价格源。")
