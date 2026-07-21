# -*- coding: utf-8 -*-
"""Market, stock research, screening, and sector endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import data_fetch
from market_data_gateway import MarketDataGatewayError, execute_market_operation


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


def _validate_market(market: str):
    if market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{market}")


def _call_market_operation(operation: str, payload: dict, error_prefix: str):
    try:
        return execute_market_operation(operation, payload)
    except MarketDataGatewayError as error:
        suffix = f" [job_id={error.job_id}]" if error.job_id else ""
        raise HTTPException(
            status_code=error.status_code,
            detail=f"{error_prefix}:{error}{suffix}",
        ) from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"{error_prefix}:{error}") from error


@router.get("/api/markets")
def get_markets():
    return {"markets": data_fetch.MARKETS}


@router.get("/api/presets")
def get_presets():
    return {"presets": PRESETS}


@router.get("/api/search_us")
def search_us(keyword: str = Query(..., min_length=1)):
    return _call_market_operation(
        "market.search_us", {"keyword": keyword}, "美股代码搜索失败"
    )


@router.get("/api/analyze")
def analyze_stock(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(12, ge=6, le=36),
):
    _validate_market(market)
    return _call_market_operation(
        "market.analyze",
        {"market": market, "symbol": symbol, "months": months},
        "真实行情分析失败",
    )


@router.get("/api/backtest")
def run_backtest(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    horizon: int = Query(20, ge=3, le=60),
):
    _validate_market(market)
    return _call_market_operation(
        "market.backtest",
        {"market": market, "symbol": symbol, "horizon": horizon},
        "真实回测数据获取失败",
    )


@router.get("/api/fundamentals")
def fundamentals(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    _validate_market(market)
    return _call_market_operation(
        "market.fundamentals",
        {"market": market, "symbol": symbol},
        "真实基本面获取失败",
    )


@router.get("/api/quote")
def quote(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    _validate_market(market)
    return _call_market_operation(
        "market.quote",
        {"market": market, "symbol": symbol},
        "真实行情快照获取失败",
    )


@router.get("/api/quote/level-history")
def quote_level_history(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(60, ge=6, le=120),
):
    _validate_market(market)
    return _call_market_operation(
        "market.quote_level_history",
        {"market": market, "symbol": symbol, "months": months},
        "实时价位历史到达分析失败",
    )


@router.get("/api/ml")
def ml(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    horizon: int = Query(10, ge=3, le=30),
):
    _validate_market(market)
    return _call_market_operation(
        "market.ml",
        {"market": market, "symbol": symbol, "horizon": horizon},
        "模型计算失败",
    )


@router.get("/api/news")
def news(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    _validate_market(market)
    return _call_market_operation(
        "market.news",
        {"market": market, "symbol": symbol},
        "真实新闻情绪获取失败",
    )


@router.get("/api/compare")
def compare(
    market: str = Query(...),
    symbol: str = Query(..., min_length=1),
    months: int = Query(12, ge=6, le=36),
):
    _validate_market(market)
    return _call_market_operation(
        "market.compare",
        {"market": market, "symbol": symbol, "months": months},
        "对比分析失败",
    )


@router.post("/api/scan")
def scan(req: ScanRequest):
    _validate_market(req.market)
    symbols = [symbol.strip() for symbol in req.symbols if symbol.strip()][:40]
    if not symbols:
        raise HTTPException(status_code=400, detail="股票列表为空")
    months = max(6, min(36, req.months))
    return _call_market_operation(
        "market.scan",
        {"market": req.market, "symbols": symbols, "months": months},
        "真实批量扫描失败",
    )


@router.post("/api/multi_compare")
def multi_compare(req: MultiCompareRequest):
    _validate_market(req.market)
    return _call_market_operation(
        "market.multi_compare",
        {
            "market": req.market,
            "symbols": req.symbols,
            "months": req.months,
            "include_fundamentals": req.include_fundamentals,
        },
        "多股对比失败",
    )


@router.get("/api/hot")
def get_hot(
    market: str = Query(...),
    period: str = Query("1d", pattern="^(1d|7d|30d)$"),
    type: str = Query("gainers", pattern="^(gainers|losers|active)$"),
    limit: int = Query(50, ge=10, le=50),
):
    _validate_market(market)
    return _call_market_operation(
        "market.hot",
        {"market": market, "period": period, "type": type, "limit": limit},
        "真实热门榜源当前不可用",
    )


@router.get("/api/sectors")
def get_sectors(
    market: str = Query("A股"),
    sector_limit: int = Query(12, ge=5, le=30),
    stock_limit: int = Query(8, ge=3, le=15),
    include_concepts: bool = Query(True),
):
    return _call_market_operation(
        "market.sectors",
        {
            "market": market,
            "sector_limit": sector_limit,
            "stock_limit": stock_limit,
            "include_concepts": include_concepts,
        },
        "真实板块数据获取失败",
    )


@router.get("/api/market/daily")
def market_daily(
    risk: str = Query("balanced", pattern="^(stable|balanced|aggressive)$"),
    fund_limit: int = Query(4, ge=3, le=8),
):
    return _call_market_operation(
        "market.daily",
        {"risk": risk, "fund_limit": fund_limit},
        "真实市场机会日报获取失败",
    )
