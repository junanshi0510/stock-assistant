# -*- coding: utf-8 -*-
"""Market, stock research, screening, and sector endpoints."""

import math
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import analysis
import backtest as backtest_mod
import compare as compare_mod
import data_fetch
import fundamentals as fundamentals_mod
import hot_stocks
import market_daily as market_daily_mod
import ml_model
import multi_compare as multi_compare_mod
import quotes as quotes_mod
import sectors as sectors_mod
import sentiment as sentiment_mod


router = APIRouter(tags=["市场与股票"])


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


class ScanRequest(BaseModel):
    market: str
    symbols: list[str]
    months: int = 12


class MultiCompareRequest(BaseModel):
    market: str
    symbols: list[str]
    months: int = 12
    include_fundamentals: bool = False


def _safe(value):
    """Convert NaN and infinity to None so JSON output remains valid."""
    if value is None:
        return None
    try:
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return value


def _validate_market(market: str):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")


@router.get("/api/markets")
def get_markets():
    return {"markets": data_fetch.MARKETS}


@router.get("/api/presets")
def get_presets():
    return {"presets": PRESETS}


@router.get("/api/search_us")
def search_us(keyword: str = Query(..., min_length=1)):
    try:
        hits = data_fetch.search_us_symbol(keyword)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"查找失败:{error}")
    return {"results": hits.to_dict(orient="records")}


@router.get("/api/analyze")
def analyze_stock(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(12, ge=6, le=36),
):
    _validate_market(market)
    try:
        raw = data_fetch.get_history_months(market, symbol, months)
        result = analysis.score(raw)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"数据获取失败:{error}")

    candles = []
    for _, row in result["df"].iterrows():
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
        "reasons": [{"name": reason[0], "delta": reason[1], "detail": reason[2]} for reason in result["reasons"]],
        "indicators": {key: _safe(value) for key, value in result["indicators"].items()},
        "candles": candles,
    }


@router.get("/api/backtest")
def run_backtest(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    horizon: int = Query(20, ge=3, le=60),
):
    _validate_market(market)
    try:
        raw = data_fetch.get_history_months(market, symbol, 48)
        result = backtest_mod.backtest(raw, horizon=horizon)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"数据获取失败:{error}")
    result.update({"market": market, "symbol": symbol})
    return result


@router.get("/api/fundamentals")
def fundamentals(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    _validate_market(market)
    try:
        return fundamentals_mod.get_fundamentals(market, symbol)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"基本面获取失败:{error}")


@router.get("/api/quote")
def quote(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    _validate_market(market)
    try:
        return quotes_mod.get_quote(market, symbol)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"行情快照获取失败:{error}")


@router.get("/api/quote/level-history")
def quote_level_history(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(60, ge=6, le=120),
):
    _validate_market(market)
    try:
        return quotes_mod.get_quote_level_history(market, symbol, months=months)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"实时价位历史到达分析失败:{error}")


@router.get("/api/ml")
def ml(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    horizon: int = Query(10, ge=3, le=30),
):
    _validate_market(market)
    try:
        raw = data_fetch.get_history_months(market, symbol, 60)
        return ml_model.predict(raw, horizon=horizon)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"模型计算失败:{error}")


@router.get("/api/news")
def news(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    _validate_market(market)
    try:
        return sentiment_mod.get_sentiment(market, symbol)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"新闻情绪获取失败:{error}")


@router.get("/api/compare")
def compare(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(12, ge=6, le=36),
):
    _validate_market(market)
    try:
        return compare_mod.compare(market, symbol, months)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"对比分析失败:{error}")


@router.post("/api/scan")
def scan(req: ScanRequest):
    _validate_market(req.market)
    symbols = [symbol.strip() for symbol in req.symbols if symbol.strip()][:40]
    if not symbols:
        raise HTTPException(status_code=400, detail="股票列表为空")
    months = max(6, min(36, req.months))

    if req.market == "A股" and len(symbols) >= 3:
        try:
            comparison = multi_compare_mod.compare_many(req.market, symbols, months)
            results = [{
                "symbol": row["symbol"],
                "score": row["score"],
                "probability": row["probability"],
                "direction": row["direction"],
                "close": row["end_price"],
            } for row in comparison["metrics"]]
            results.sort(key=lambda row: row["score"], reverse=True)
            return {
                "market": req.market,
                "results": results,
                "failed": comparison.get("failed", []),
                "count": len(results),
                "failed_count": comparison.get("failed_count", 0),
            }
        except Exception:
            # This is a real-data retrieval retry path; it never fabricates a result.
            pass

    def score_symbol(symbol: str):
        try:
            dataframe = data_fetch.get_history_months(req.market, symbol, months, fetch_months=months)
            return {"symbol": symbol, **analysis.score_only(dataframe)}
        except Exception as error:
            return {"symbol": symbol, "error": str(error)[:80]}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(score_symbol, symbols))

    succeeded = [row for row in results if "error" not in row]
    failed = [row for row in results if "error" in row]
    succeeded.sort(key=lambda row: row["score"], reverse=True)
    return {
        "market": req.market,
        "results": succeeded,
        "failed": failed,
        "count": len(succeeded),
        "failed_count": len(failed),
    }


@router.post("/api/multi_compare")
def multi_compare(req: MultiCompareRequest):
    _validate_market(req.market)
    try:
        return multi_compare_mod.compare_many(
            req.market,
            req.symbols,
            req.months,
            include_fundamentals=req.include_fundamentals,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"多股对比失败:{error}")


@router.get("/api/hot")
def get_hot(
    market: str = Query(...),
    period: str = Query("1d", pattern="^(1d|7d|30d)$"),
    type: str = Query("gainers", pattern="^(gainers|losers|active)$"),
    limit: int = Query(50, ge=10, le=100),
):
    _validate_market(market)
    try:
        return hot_stocks.get_hot_stocks(market, period, type, limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实热门榜源当前不可用:{error}")


@router.get("/api/sectors")
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
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实板块数据获取失败:{error}")


@router.get("/api/market/daily")
def market_daily(
    risk: str = Query("balanced", pattern="^(stable|balanced|aggressive)$"),
    fund_limit: int = Query(4, ge=3, le=8),
):
    try:
        return market_daily_mod.get_market_daily(risk=risk, fund_limit=fund_limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实市场机会日报获取失败:{error}")
