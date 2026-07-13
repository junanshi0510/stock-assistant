# -*- coding: utf-8 -*-
"""User-confirmed holdings, watchlist, OCR import, and monitoring endpoints."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

import analysis
import data_fetch
import decision_center
import holdings_import
import holdings as holdings_mod
import monitor
import portfolio_review
import storage
import transaction_import


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


class InvestmentProfileRequest(BaseModel):
    risk: str = "balanced"
    horizon: str = "mid_long"
    monthly_budget: float | None = Field(default=None, ge=0)
    max_single_ratio: float = Field(default=35, ge=10, le=80)
    allowed_fund_markets: list[
        Literal["mainland", "hong_kong", "united_states", "global"]
    ] = Field(default_factory=lambda: ["mainland"], min_length=1, max_length=4)
    accept_fx_risk: bool = False


class PortfolioTransactionRequest(BaseModel):
    asset_type: str
    market: str = ""
    code: str
    name: str = ""
    trade_type: str
    trade_date: date
    shares: float = Field(gt=0)
    unit_price: float = Field(gt=0)
    fee: float = Field(default=0, ge=0)
    note: str = Field(default="", max_length=300)
    source: str = Field(default="manual", max_length=80)


class PortfolioSnapshotRequest(BaseModel):
    reason: str = Field(default="manual", max_length=80)


class PortfolioTransactionImportRequest(BaseModel):
    items: list[PortfolioTransactionRequest] = Field(min_length=1, max_length=1500)
    file_sha256: str = Field(min_length=64, max_length=64)
    filename: str = Field(default="", max_length=255)


_PROFILE_RISKS = {"stable", "balanced", "aggressive"}
_PROFILE_HORIZONS = {"short", "mid_long", "long"}


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


@router.get("/api/holdings/exposure")
def get_holdings_exposure(max_funds: int = Query(6, ge=1, le=10)):
    return holdings_mod.fund_lookthrough_exposure(max_funds=max_funds)


@router.get("/api/investment-profile")
def get_investment_profile():
    return storage.get_investment_profile()


@router.put("/api/investment-profile")
def update_investment_profile(req: InvestmentProfileRequest):
    if req.risk not in _PROFILE_RISKS:
        raise HTTPException(status_code=400, detail="不支持的风险偏好")
    if req.horizon not in _PROFILE_HORIZONS:
        raise HTTPException(status_code=400, detail="不支持的投资期限")
    return storage.save_investment_profile(req.model_dump())


@router.get("/api/decision-center")
def get_decision_center():
    try:
        return decision_center.build_decision_center()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实投资决策中心生成失败:{error}")


@router.get("/api/portfolio/transactions")
def get_portfolio_transactions():
    return portfolio_review.list_transactions()


@router.post("/api/portfolio/transactions")
def create_portfolio_transaction(req: PortfolioTransactionRequest):
    try:
        return portfolio_review.create_transaction(req.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"交易流水保存失败:{error}")


@router.post("/api/portfolio/transactions/parse-csv")
async def preview_portfolio_transaction_csv(
    file: UploadFile = File(...),
    asset_type: str = Form("fund"),
    market: str = Form("基金"),
):
    filename = file.filename or ""
    if not filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(status_code=400, detail="请上传 CSV 或 XLSX 格式的交易账单")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="账单文件为空")
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="交易账单不能超过 4MB")
    try:
        return transaction_import.parse_transaction_file(
            data,
            filename=filename,
            default_asset_type=asset_type,
            default_market=market,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"交易账单预览失败:{error}")


@router.post("/api/portfolio/transactions/import-csv")
def import_portfolio_transaction_csv(req: PortfolioTransactionImportRequest):
    try:
        return portfolio_review.create_transactions_from_csv(
            [item.model_dump(mode="json") for item in req.items],
            file_sha256=req.file_sha256,
            filename=req.filename,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"交易账单导入失败:{error}")


@router.delete("/api/portfolio/transactions/{transaction_id}")
def delete_portfolio_transaction(transaction_id: int):
    return {"ok": portfolio_review.delete_transaction(transaction_id)}


@router.get("/api/portfolio/ledger")
def get_portfolio_ledger():
    try:
        return portfolio_review.ledger_overview()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"交易成本复盘失败:{error}")


@router.get("/api/portfolio/performance")
def get_portfolio_performance():
    try:
        return portfolio_review.cashflow_performance()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"现金流收益复盘失败:{error}")


@router.get("/api/portfolio/behavior")
def get_portfolio_behavior():
    try:
        return portfolio_review.trade_behavior_review()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"交易行为复盘失败:{error}")


@router.get("/api/portfolio/attribution")
def get_portfolio_attribution():
    try:
        return portfolio_review.snapshot_attribution()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"区间持仓归因失败:{error}")


@router.get("/api/portfolio/rebalance")
def get_portfolio_rebalance():
    try:
        return portfolio_review.rebalance_review()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"组合再平衡复盘失败:{error}")


@router.get("/api/portfolio/snapshots")
def get_portfolio_snapshots(limit: int = Query(24, ge=1, le=120)):
    return portfolio_review.list_snapshots(limit=limit)


@router.post("/api/portfolio/snapshots")
def create_portfolio_snapshot(req: PortfolioSnapshotRequest):
    try:
        return portfolio_review.create_snapshot(reason=req.reason)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"组合快照保存失败:{error}")


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


@router.post("/api/holdings/parse-file")
async def preview_holdings_file(file: UploadFile = File(...)):
    filename = file.filename or ""
    content_type = (file.content_type or "").lower()
    is_csv = filename.lower().endswith(".csv") or content_type in {"text/csv", "application/csv"}
    is_xlsx = filename.lower().endswith(".xlsx") or content_type in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/xlsx",
    }
    if not (is_csv or is_xlsx):
        raise HTTPException(status_code=400, detail="请上传 CSV 或 XLSX 格式的持仓账单")
    if not filename.lower().endswith((".csv", ".xlsx")):
        filename = f"{filename or 'holdings'}.{ 'xlsx' if is_xlsx else 'csv' }"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="持仓账单为空")
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="持仓账单不能超过 4MB")
    try:
        return holdings_import.parse_holdings_file(data, filename=filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"持仓账单预览失败:{error}")


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
