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
import holding_thesis
import holdings_import
import holdings as holdings_mod
import monitor
import portfolio_exposure
import portfolio_action_report
import portfolio_review
import storage
import transaction_import
from investment_policy import (
    CONSENT_TEXT_SHA256,
    CONSENT_VERSION,
    validate_investment_policy,
)


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
    risk: Literal["stable", "balanced", "aggressive"]
    horizon: Literal["short", "mid_long", "long"]
    experience_level: Literal["beginner", "intermediate", "experienced"]
    primary_objective: Literal["capital_preservation", "balanced_growth", "long_term_growth"]
    monthly_budget: float = Field(ge=0, le=10_000_000)
    max_single_ratio: float = Field(ge=5, le=60)
    max_equity_ratio: float = Field(ge=0, le=100)
    max_industry_ratio: float = Field(ge=5, le=50)
    max_drawdown_pct: float = Field(ge=5, le=50)
    liquidity_reserve_months: float = Field(ge=0, le=36)
    allowed_fund_markets: list[
        Literal["mainland", "hong_kong", "united_states", "global"]
    ] = Field(default_factory=lambda: ["mainland"], min_length=1, max_length=4)
    accept_fx_risk: bool = False
    emergency_fund_confirmed: bool
    review_cycle_months: Literal[6, 12] = 6


class InvestmentProfileActivationRequest(BaseModel):
    acknowledged: bool
    expected_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_active_version_id: str | None = Field(default=None, max_length=80)
    consent_version: str = Field(max_length=80)
    consent_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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


class PortfolioExposureSnapshotRequest(BaseModel):
    target_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class PortfolioActionReportRequest(BaseModel):
    max_funds: int = Field(default=8, ge=2, le=8)


class HoldingThesisRequest(BaseModel):
    asset_type: Literal["fund", "stock"]
    market: str = Field(default="", max_length=40)
    code: str = Field(min_length=1, max_length=32)
    role: Literal[
        "core_growth",
        "satellite_growth",
        "defensive",
        "income",
        "diversifier",
        "tactical",
    ]
    thesis_summary: str = Field(min_length=12, max_length=600)
    expected_holding_months: int = Field(ge=1, le=240)
    review_date: date
    max_loss_pct: float = Field(ge=1, le=80)
    max_drawdown_pct: float = Field(ge=1, le=80)
    add_condition: str = Field(min_length=6, max_length=600)
    exit_condition: str = Field(min_length=6, max_length=600)


class PortfolioTransactionImportRequest(BaseModel):
    items: list[PortfolioTransactionRequest] = Field(min_length=1, max_length=1500)
    file_sha256: str = Field(min_length=64, max_length=64)
    filename: str = Field(default="", max_length=255)


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


@router.post("/api/holdings/exposure-snapshots")
def create_holdings_exposure_snapshot(req: PortfolioExposureSnapshotRequest):
    profile = storage.get_investment_profile()
    profile_version_id = profile.get("profile_version_id") if profile.get("configured") else None
    try:
        return portfolio_exposure.refresh_exposure_snapshot(
            target_code=req.target_code,
            profile_version_id=profile_version_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实组合穿透快照生成失败:{error}") from error


@router.get("/api/holdings/exposure-snapshots")
def get_holdings_exposure_snapshots(
    target_code: str | None = Query(default=None, pattern=r"^\d{6}$"),
    limit: int = Query(default=20, ge=1, le=100),
):
    items = storage.list_portfolio_exposure_snapshots(
        target_code=target_code,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/api/holdings/exposure-snapshots/{snapshot_id}")
def get_holdings_exposure_snapshot(snapshot_id: str):
    item = storage.get_portfolio_exposure_snapshot(snapshot_id)
    if item is None:
        raise HTTPException(status_code=404, detail="组合穿透快照不存在")
    item["integrity"] = storage.verify_portfolio_exposure_snapshot(snapshot_id)
    return item


@router.get("/api/investment-profile")
def get_investment_profile():
    return storage.get_investment_profile()


@router.put("/api/investment-profile")
def update_investment_profile(req: InvestmentProfileRequest):
    validation = validate_investment_policy(req.model_dump())
    draft = storage.create_investment_profile_draft(req.model_dump(), validation)
    return {
        "draft": draft,
        "validation": validation,
        "requires_activation": True,
        "deprecated_transport": "PUT /api/investment-profile now creates a draft only",
    }


@router.post("/api/investment-profile/drafts")
def create_investment_profile_draft(req: InvestmentProfileRequest):
    validation = validate_investment_policy(req.model_dump())
    draft = storage.create_investment_profile_draft(req.model_dump(), validation)
    return {
        "draft": draft,
        "validation": validation,
        "requires_activation": True,
    }


@router.get("/api/investment-profile/versions")
def get_investment_profile_versions(limit: int = Query(default=20, ge=1, le=100)):
    items = [
        {
            "id": item["id"],
            "version_no": item["version_no"],
            "status": item["status"],
            "payload_sha256": item["payload_sha256"],
            "integrity_verified": item.get("integrity_verified"),
            "validation_valid": bool((item.get("validation") or {}).get("valid")),
            "questionnaire_version": item.get("questionnaire_version"),
            "consent_version": item.get("consent_version"),
            "created_at": item.get("created_at"),
            "activated_at": item.get("activated_at"),
            "review_due_at": item.get("review_due_at"),
            "superseded_at": item.get("superseded_at"),
        }
        for item in storage.list_investment_profile_versions(limit=limit)
    ]
    return {"items": items, "count": len(items)}


@router.post("/api/investment-profile/versions/{version_id}/activate")
def activate_investment_profile_version(version_id: str, req: InvestmentProfileActivationRequest):
    if not req.acknowledged:
        raise HTTPException(status_code=409, detail="必须明确确认投资政策条款后才能激活")
    if req.consent_version != CONSENT_VERSION or req.consent_text_sha256 != CONSENT_TEXT_SHA256:
        raise HTTPException(status_code=409, detail="确认条款版本或哈希不匹配")
    version = storage.get_investment_profile_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="投资政策版本不存在")
    try:
        activated = storage.activate_investment_profile_version(
            version_id,
            expected_payload_sha256=req.expected_payload_sha256,
            expected_active_version_id=req.expected_active_version_id,
            consent_version=req.consent_version,
            consent_text_sha256=req.consent_text_sha256,
            review_cycle_months=int(version.get("review_cycle_months") or 0),
        )
    except storage.InvestmentProfileConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    activated_flag = bool(activated.pop("activated", False))
    return {
        "activated": activated_flag,
        "version": activated,
        "profile": storage.get_investment_profile(),
        "audit": storage.verify_investment_profile_integrity(),
    }


@router.get("/api/investment-profile/audit")
def get_investment_profile_audit():
    items = storage.list_investment_profile_audit()
    return {
        "items": items,
        "count": len(items),
        "verification": storage.verify_investment_profile_integrity(),
    }


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


@router.get("/api/portfolio/theses")
def get_holding_theses():
    return holding_thesis.list_with_coverage()


@router.post("/api/portfolio/theses")
def create_holding_thesis(req: HoldingThesisRequest):
    try:
        return holding_thesis.save_thesis(req.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/portfolio/theses/{asset_type}/{code}")
def get_holding_thesis_history(
    asset_type: Literal["fund", "stock"],
    code: str,
    market: str = Query(default="", max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
):
    latest = storage.get_latest_holding_thesis(asset_type, market, code)
    if latest is None:
        raise HTTPException(status_code=404, detail="该持仓尚未建立持有逻辑")
    return {
        "latest": latest,
        "versions": storage.list_holding_thesis_versions(
            asset_type,
            market,
            code,
            limit=limit,
        ),
        "verification": storage.verify_holding_thesis_chain(asset_type, market, code),
    }


@router.get("/api/portfolio/action-reports")
def get_portfolio_action_reports(limit: int = Query(default=20, ge=1, le=100)):
    items = storage.list_portfolio_action_reports(limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/api/portfolio/action-reports")
def create_portfolio_action_report(req: PortfolioActionReportRequest):
    try:
        return portfolio_action_report.refresh_action_report(max_funds=req.max_funds)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"真实持仓行动报告生成失败:{error}") from error


@router.get("/api/portfolio/action-reports/latest")
def get_latest_portfolio_action_report():
    result = portfolio_action_report.load_latest_action_report()
    if result is None:
        return {"status": "not_generated", "report": None, "binding": {"current": False, "reasons": ["report_not_generated"]}}
    return result


@router.get("/api/portfolio/action-reports/{report_id}")
def get_portfolio_action_report(report_id: str):
    result = portfolio_action_report.load_action_report(report_id)
    if result is None:
        raise HTTPException(status_code=404, detail="持仓行动报告不存在")
    return result


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
