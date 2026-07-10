# -*- coding: utf-8 -*-
"""
后端 API 服务(FastAPI)
========================
把数据抓取 / 打分 / 回测包装成 JSON 接口,供 React 前端调用。

启动方式(在 backend 目录):
    uvicorn main:app --reload --port 8000

接口一览:
    GET /api/markets                              支持的市场列表
    GET /api/presets                              预设股票池(批量扫描用)
    GET /api/search_us?keyword=AAPL               美股代码查找
    GET /api/analyze?market=&symbol=&months=      抓数据 + 多因子打分 + K线序列
    GET /api/backtest?market=&symbol=&horizon=    信号历史准确率回测
    POST /api/scan  {market, symbols:[...], months}  批量扫描并按打分排序
"""

import datetime as dt
import math
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import data_fetch
import analysis
import backtest as backtest_mod
import fundamentals as fundamentals_mod
import ml_model
import sentiment as sentiment_mod
import compare as compare_mod
import multi_compare as multi_compare_mod
import quotes as quotes_mod
import storage
import monitor
import hot_stocks
import sectors as sectors_mod
import funds as funds_mod
import holdings as holdings_mod
import market_daily as market_daily_mod

app = FastAPI(title="金融投资助手 API", version="2.0")

_allowed_origins = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 预设股票池(带名称,方便非技术用户一键扫描)
PRESETS = {
    "A股": [
        {"symbol": "600519", "name": "贵州茅台"}, {"symbol": "000858", "name": "五粮液"},
        {"symbol": "601318", "name": "中国平安"}, {"symbol": "600036", "name": "招商银行"},
        {"symbol": "000001", "name": "平安银行"}, {"symbol": "600900", "name": "长江电力"},
        {"symbol": "002594", "name": "比亚迪"}, {"symbol": "300750", "name": "宁德时代"},
        {"symbol": "600276", "name": "恒瑞医药"}, {"symbol": "000333", "name": "美的集团"},
    ],
    "港股": [
        {"symbol": "00700", "name": "腾讯控股"}, {"symbol": "09988", "name": "阿里巴巴"},
        {"symbol": "03690", "name": "美团"}, {"symbol": "01810", "name": "小米集团"},
        {"symbol": "00939", "name": "建设银行"}, {"symbol": "02318", "name": "中国平安"},
        {"symbol": "00941", "name": "中国移动"}, {"symbol": "01024", "name": "快手"},
    ],
    "美股": [
        {"symbol": "AAPL", "name": "苹果"}, {"symbol": "MSFT", "name": "微软"},
        {"symbol": "NVDA", "name": "英伟达"}, {"symbol": "GOOGL", "name": "谷歌"},
        {"symbol": "AMZN", "name": "亚马逊"}, {"symbol": "META", "name": "Meta"},
        {"symbol": "TSLA", "name": "特斯拉"}, {"symbol": "AMD", "name": "AMD"},
    ],
}


def _safe(x):
    """把 NaN / Inf 转成 None,保证 JSON 合法。"""
    if x is None:
        return None
    try:
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return x


def _date_range(months: int):
    end = dt.date.today()
    start = end - dt.timedelta(days=int(months * 31))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


@app.get("/api/markets")
def get_markets():
    return {"markets": data_fetch.MARKETS}


@app.get("/api/presets")
def get_presets():
    return {"presets": PRESETS}


@app.get("/api/search_us")
def search_us(keyword: str = Query(..., min_length=1)):
    try:
        hits = data_fetch.search_us_symbol(keyword)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"查找失败:{e}")
    return {"results": hits.to_dict(orient="records")}


@app.get("/api/analyze")
def analyze(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(12, ge=6, le=36),
):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")

    try:
        raw = data_fetch.get_history_months(market, symbol, months)
        result = analysis.score(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"数据获取失败:{e}")

    df = result["df"]
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "open": _safe(row["open"]), "high": _safe(row["high"]),
            "low": _safe(row["low"]), "close": _safe(row["close"]),
            "volume": _safe(row["volume"]),
            "ma5": _safe(row["ma5"]), "ma20": _safe(row["ma20"]), "ma60": _safe(row["ma60"]),
            "boll_up": _safe(row["boll_up"]), "boll_low": _safe(row["boll_low"]),
        })

    return {
        "market": market, "symbol": symbol,
        "score": result["score"],
        "probability": result["probability"],
        "direction": result["direction"],
        "reasons": [{"name": r[0], "delta": r[1], "detail": r[2]} for r in result["reasons"]],
        "indicators": {k: _safe(v) for k, v in result["indicators"].items()},
        "candles": candles,
    }


@app.get("/api/backtest")
def run_backtest(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    horizon: int = Query(20, ge=3, le=60),
):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")
    try:
        raw = data_fetch.get_history_months(market, symbol, 48)  # 约 4 年
        result = backtest_mod.backtest(raw, horizon=horizon)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"数据获取失败:{e}")
    result.update({"market": market, "symbol": symbol})
    return result


@app.get("/api/fundamentals")
def fundamentals(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")
    try:
        return fundamentals_mod.get_fundamentals(market, symbol)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"基本面获取失败:{e}")


@app.get("/api/quote")
def quote(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")
    try:
        return quotes_mod.get_quote(market, symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"行情快照获取失败:{e}")


@app.get("/api/ml")
def ml(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    horizon: int = Query(10, ge=3, le=30),
):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")
    try:
        raw = data_fetch.get_history_months(market, symbol, 60)  # 约 5 年训练
        return ml_model.predict(raw, horizon=horizon)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"模型计算失败:{e}")


@app.get("/api/news")
def news(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")
    try:
        return sentiment_mod.get_sentiment(market, symbol)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"新闻情绪获取失败:{e}")


@app.get("/api/compare")
def compare(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(12, ge=6, le=36),
):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")
    try:
        return compare_mod.compare(market, symbol, months)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"对比分析失败:{e}")


class ScanRequest(BaseModel):
    market: str
    symbols: list[str]
    months: int = 12


class MultiCompareRequest(BaseModel):
    market: str
    symbols: list[str]
    months: int = 12
    include_fundamentals: bool = False


class FundCompareRequest(BaseModel):
    codes: list[str]
    months: int = 36


class HoldingRequest(BaseModel):
    asset_type: str
    market: str = ""
    code: str
    name: str = ""
    amount: float | None = None
    cost: float | None = None
    yesterday_profit: float | None = None
    profit: float | None = None
    profit_rate: float | None = None
    shares: float | None = None
    source: str = "manual"
    raw_text: str = ""


class HoldingBulkRequest(BaseModel):
    items: list[HoldingRequest]


class HoldingTextRequest(BaseModel):
    text: str


@app.post("/api/scan")
def scan(req: ScanRequest):
    if req.market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{req.market}")
    symbols = [s.strip() for s in req.symbols if s.strip()][:40]  # 上限 40 只
    if not symbols:
        raise HTTPException(status_code=400, detail="股票列表为空")

    months = max(6, min(36, req.months))

    if req.market == "A股" and len(symbols) >= 3:
        try:
            mc = multi_compare_mod.compare_many(req.market, symbols, months)
            ok = [{
                "symbol": r["symbol"],
                "score": r["score"],
                "probability": r["probability"],
                "direction": r["direction"],
                "close": r["end_price"],
            } for r in mc["metrics"]]
            ok.sort(key=lambda x: x["score"], reverse=True)
            return {"market": req.market, "results": ok, "failed": mc.get("failed", []),
                    "count": len(ok), "failed_count": mc.get("failed_count", 0)}
        except Exception:
            # 快路失败时回退到通用数据源链,保证功能可用。
            pass

    def one(sym):
        try:
            # 扫描只需打分,抓所需窗口即可(不加宽,保持快)
            df = data_fetch.get_history_months(req.market, sym, months, fetch_months=months)
            r = analysis.score_only(df)
            return {"symbol": sym, **r}
        except Exception as e:
            return {"symbol": sym, "error": str(e)[:80]}

    # 并行抓取(BaoStock 内部已用锁串行化,海外源可并行加速)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, symbols))

    ok = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    ok.sort(key=lambda x: x["score"], reverse=True)
    return {"market": req.market, "results": ok, "failed": failed,
            "count": len(ok), "failed_count": len(failed)}


@app.post("/api/multi_compare")
def multi_compare(req: MultiCompareRequest):
    if req.market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{req.market}")
    try:
        return multi_compare_mod.compare_many(
            req.market,
            req.symbols,
            req.months,
            include_fundamentals=req.include_fundamentals,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"多股对比失败:{e}")


# ==================== 自选股(本地持久化)====================

class WatchRequest(BaseModel):
    market: str
    symbol: str
    name: str = ""


@app.get("/api/watchlist")
def get_watchlist():
    """返回全部自选股,并为每只并行计算当前打分(抓不到的用 error 标注,不影响其它)。"""
    items = storage.list_watchlist()

    def enrich(it):
        out = dict(it)
        try:
            df = data_fetch.get_history_months(it["market"], it["symbol"], 12, fetch_months=12)
            out.update(analysis.score_only(df))
        except Exception as e:
            out["error"] = str(e)[:80]
        return out

    if items:
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(enrich, items))
    return {"items": items, "count": len(items)}


@app.post("/api/watchlist")
def add_watchlist(req: WatchRequest):
    if req.market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{req.market}")
    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="股票代码为空")
    return storage.add_watch(req.market, req.symbol, req.name)


@app.delete("/api/watchlist")
def delete_watchlist(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    removed = storage.remove_watch(market, symbol)
    return {"removed": removed}


# ==================== 我的持仓 / 截图导入 ====================

@app.get("/api/holdings")
def get_holdings():
    return holdings_mod.list_holdings()


@app.get("/api/holdings/insights")
def get_holdings_insights(max_funds: int = Query(6, ge=2, le=10)):
    try:
        return holdings_mod.holdings_insights(max_funds=max_funds)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实持仓组合体检失败:{e}")


@app.post("/api/holdings")
def save_holdings(req: HoldingBulkRequest):
    try:
        return holdings_mod.save_holdings([item.model_dump() for item in req.items])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"持仓保存失败:{e}")


@app.delete("/api/holdings/{holding_id}")
def delete_holding(holding_id: int):
    return {"ok": holdings_mod.delete_holding(holding_id)}


@app.post("/api/holdings/parse-text")
def parse_holdings_text(req: HoldingTextRequest):
    return holdings_mod.parse_holdings_text(req.text)


@app.post("/api/holdings/ocr-upload")
async def upload_holding_screenshot(file: UploadFile = File(...)):
    content_type = file.content_type or ""
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="图片为空")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过 8MB")
    try:
        return holdings_mod.recognize_image(data, content_type)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实 OCR 识别失败:{e}")


# ==================== 提醒(打分变化监控)====================

@app.get("/api/alerts")
def get_alerts(limit: int = Query(50, ge=1, le=200)):
    """返回最近的提醒,最新的在前。"""
    return {"alerts": storage.list_alerts(limit)}


@app.delete("/api/alerts")
def clear_alerts():
    """清空全部提醒(标记为已读)。"""
    storage.clear_alerts()
    return {"cleared": True}


@app.post("/api/alerts/scan")
def trigger_scan():
    """手动触发一次扫描(同步,可能要几秒)。用于测试或用户主动刷新。"""
    monitor.trigger_scan_now()
    return {"scanned": True}


# ==================== 热门股/涨跌幅榜 ====================

@app.get("/api/hot")
def get_hot(
    market: str = Query(...),
    period: str = Query("1d", regex="^(1d|7d|30d)$"),
    type: str = Query("gainers", regex="^(gainers|losers|active)$"),
    limit: int = Query(50, ge=10, le=100),
):
    """
    获取热门股票/涨跌幅榜。

    Args:
        market: 市场(A股/港股/美股)
        period: 周期(1d当日/7d/30d,当前只支持1d)
        type: 类型(gainers涨幅榜/losers跌幅榜/active成交活跃)
        limit: 返回数量(10-100,默认50)
    """
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")

    try:
        return hot_stocks.get_hot_stocks(market, period, type, limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实热门榜源当前不可用:{e}")


# ==================== 应用启动时自动启动监控 ====================
# 每小时扫描一次自选股,打分穿越档位时写提醒(daemon 线程,随主程序退出)
@app.get("/api/sectors")
def get_sectors(
    market: str = Query("A股"),
    sector_limit: int = Query(12, ge=5, le=30),
    stock_limit: int = Query(8, ge=3, le=15),
    include_concepts: bool = Query(True),
):
    try:
        return sectors_mod.get_sector_analysis(
            market=market,
            sector_limit=sector_limit,
            stock_limit=stock_limit,
            include_concepts=include_concepts,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实板块数据获取失败:{e}")


@app.get("/api/funds/hot")
def get_hot_funds(
    category: str = Query("all"),
    limit: int = Query(30, ge=5, le=100),
    sort: str = Query("1y", regex="^(1y|ytd|6m|3m|1m|1w)$"),
    include_categories: bool = Query(False),
):
    try:
        return funds_mod.get_hot_funds(
            category=category,
            limit=limit,
            sort=sort,
            include_categories=include_categories,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金排行数据获取失败:{e}")


@app.get("/api/funds/categories")
def get_fund_categories():
    try:
        return funds_mod.get_fund_categories()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金分类热度数据获取失败:{e}")


@app.get("/api/funds/opportunities")
def fund_opportunities(
    risk: str = Query("balanced", regex="^(stable|balanced|aggressive)$"),
    limit: int = Query(5, ge=3, le=10),
):
    try:
        return funds_mod.get_fund_opportunities(risk=risk, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金机会数据获取失败:{e}")


@app.get("/api/market/daily")
def market_daily(
    risk: str = Query("balanced", regex="^(stable|balanced|aggressive)$"),
    fund_limit: int = Query(4, ge=3, le=8),
):
    try:
        return market_daily_mod.get_market_daily(risk=risk, fund_limit=fund_limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实市场机会日报获取失败:{e}")


@app.get("/api/funds/search")
def search_funds(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
):
    try:
        return funds_mod.search_funds(keyword=keyword, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金搜索数据获取失败:{e}")


@app.get("/api/funds/analyze")
def analyze_fund(
    code: str = Query(..., min_length=6, max_length=6),
    months: int = Query(36, ge=6, le=120),
):
    try:
        return funds_mod.analyze_fund(code=code, months=months)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金净值数据获取失败:{e}")


@app.get("/api/funds/portfolio")
def fund_portfolio(
    code: str = Query(..., min_length=6, max_length=6),
    year: str | None = Query(None, regex="^$|^20\\d{2}$"),
):
    try:
        return funds_mod.get_fund_portfolio(code=code, year=year or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金持仓数据获取失败:{e}")


@app.get("/api/funds/peers")
def fund_peers(
    code: str = Query(..., min_length=6, max_length=6),
    sort: str = Query("1y", regex="^(1y|ytd|6m|3m|1m|1w)$"),
    limit: int = Query(1000, ge=100, le=3000),
):
    try:
        return funds_mod.get_fund_peers(code=code, sort=sort, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金同类排行数据获取失败:{e}")


@app.get("/api/funds/alternatives")
def fund_alternatives(
    code: str = Query(..., min_length=6, max_length=6),
    sort: str = Query("1y", regex="^(1y|ytd|6m|3m|1m|1w)$"),
    limit: int = Query(5, ge=3, le=8),
    months: int = Query(36, ge=6, le=120),
):
    try:
        return funds_mod.get_fund_alternatives(code=code, sort=sort, limit=limit, months=months)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金替代品数据获取失败:{e}")


@app.get("/api/funds/dividends")
def fund_dividends(code: str = Query(..., min_length=6, max_length=6)):
    try:
        return funds_mod.get_fund_dividends(code=code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金分红数据获取失败:{e}")


@app.post("/api/funds/compare")
def fund_compare(req: FundCompareRequest):
    try:
        return funds_mod.compare_funds(codes=req.codes, months=req.months)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金对比数据获取失败:{e}")


@app.post("/api/funds/overlap")
def fund_overlap(req: FundCompareRequest):
    try:
        return funds_mod.analyze_fund_overlap(codes=req.codes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"真实基金持仓重合度数据获取失败:{e}")


monitor.start_monitor(interval_seconds=3600)  # 3600秒 = 1小时
