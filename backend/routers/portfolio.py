# -*- coding: utf-8 -*-
"""User-confirmed holdings, watchlist, OCR import, and monitoring endpoints."""

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

import analysis
import data_fetch
import holdings as holdings_mod
import monitor
import storage


router = APIRouter(tags=["我的组合"])


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


class WatchRequest(BaseModel):
    market: str
    symbol: str
    name: str = ""


@router.get("/api/watchlist")
def get_watchlist():
    """Return all saved symbols with independently calculated current scores."""
    items = storage.list_watchlist()

    def enrich(item):
        result = dict(item)
        try:
            dataframe = data_fetch.get_history_months(item["market"], item["symbol"], 12, fetch_months=12)
            result.update(analysis.score_only(dataframe))
        except Exception as error:
            result["error"] = str(error)[:80]
        return result

    if items:
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(enrich, items))
    return {"items": items, "count": len(items)}


@router.post("/api/watchlist")
def add_watchlist(req: WatchRequest):
    if req.market not in data_fetch.MARKETS:
        raise HTTPException(status_code=400, detail=f"不支持的市场:{req.market}")
    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="股票代码为空")
    return storage.add_watch(req.market, req.symbol, req.name)


@router.delete("/api/watchlist")
def delete_watchlist(market: str = Query(...), symbol: str = Query(..., min_length=1)):
    return {"removed": storage.remove_watch(market, symbol)}


@router.get("/api/holdings")
def get_holdings():
    return holdings_mod.list_holdings()


@router.get("/api/holdings/insights")
def get_holdings_insights(max_funds: int = Query(6, ge=2, le=10)):
    try:
        return holdings_mod.holdings_insights(max_funds=max_funds)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实持仓组合体检失败:{error}")


@router.post("/api/holdings")
def save_holdings(req: HoldingBulkRequest):
    try:
        return holdings_mod.save_holdings([item.model_dump() for item in req.items])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"持仓保存失败:{error}")


@router.delete("/api/holdings/{holding_id}")
def delete_holding(holding_id: int):
    return {"ok": holdings_mod.delete_holding(holding_id)}


@router.post("/api/holdings/parse-text")
def parse_holdings_text(req: HoldingTextRequest):
    return holdings_mod.parse_holdings_text(req.text)


@router.post("/api/holdings/ocr-upload")
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
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实 OCR 识别失败:{error}")


@router.get("/api/alerts")
def get_alerts(limit: int = Query(50, ge=1, le=200)):
    return {"alerts": storage.list_alerts(limit)}


@router.delete("/api/alerts")
def clear_alerts():
    storage.clear_alerts()
    return {"cleared": True}


@router.post("/api/alerts/scan")
def trigger_scan():
    monitor.trigger_scan_now()
    return {"scanned": True}
