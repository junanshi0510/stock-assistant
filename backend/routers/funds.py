# -*- coding: utf-8 -*-
"""Fund discovery, research, comparison, and replacement endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import funds as funds_mod
from market_data_gateway import MarketDataGatewayError, execute_market_operation


router = APIRouter(tags=["基金"])


class FundCompareRequest(BaseModel):
    codes: list[str]
    months: int = 36


def _call_fund_service(error_prefix: str, operation_name: str, **kwargs):
    """Map service failures consistently while preserving public API semantics."""
    try:
        return execute_market_operation(operation_name, kwargs)
    except MarketDataGatewayError as error:
        suffix = f" [job_id={error.job_id}]" if error.job_id else ""
        raise HTTPException(
            status_code=error.status_code,
            detail=f"{error_prefix}:{error}{suffix}",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"{error_prefix}:{error}") from error


@router.get("/api/funds/hot")
def get_hot_funds(
    category: str = Query("all"),
    limit: int = Query(30, ge=5, le=100),
    sort: str = Query("1y", pattern="^(1y|ytd|6m|3m|1m|1w)$"),
    include_categories: bool = Query(False),
):
    return _call_fund_service(
        "真实基金排行数据获取失败",
        "fund.hot",
        category=category,
        limit=limit,
        sort=sort,
        include_categories=include_categories,
    )


@router.get("/api/funds/categories")
def get_fund_categories():
    return _call_fund_service("真实基金分类热度数据获取失败", "fund.categories")


@router.get("/api/funds/opportunities")
def fund_opportunities(
    risk: str = Query("balanced", pattern="^(stable|balanced|aggressive)$"),
    limit: int = Query(5, ge=3, le=10),
):
    return _call_fund_service(
        "真实基金机会数据获取失败",
        "fund.opportunities",
        risk=risk,
        limit=limit,
    )


@router.get("/api/funds/search")
def search_funds(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
):
    return _call_fund_service(
        "真实基金搜索数据获取失败",
        "fund.search",
        keyword=keyword,
        limit=limit,
    )


@router.get("/api/funds/analyze")
def analyze_fund(
    code: str = Query(..., min_length=6, max_length=6),
    months: int = Query(36, ge=6, le=120),
):
    return _call_fund_service(
        "真实基金净值数据获取失败",
        "fund.analyze",
        code=code,
        months=months,
    )


@router.get("/api/funds/portfolio")
def fund_portfolio(
    code: str = Query(..., min_length=6, max_length=6),
    year: str | None = Query(None, pattern="^$|^20\\d{2}$"),
):
    return _call_fund_service(
        "真实基金持仓数据获取失败",
        "fund.portfolio",
        code=code,
        year=year or None,
    )


@router.get("/api/funds/estimate")
def fund_estimate(code: str = Query(..., min_length=6, max_length=6)):
    return _call_fund_service(
        "真实基金估值数据获取失败",
        "fund.estimate",
        code=code,
    )


@router.get("/api/funds/disclosure-changes")
def fund_disclosure_changes(
    code: str = Query(..., min_length=6, max_length=6),
    year: str | None = Query(None, pattern="^$|^20\\d{2}$"),
):
    return _call_fund_service(
        "真实基金披露变化数据获取失败",
        "fund.disclosure_changes",
        code=code,
        year=year or None,
    )


@router.get("/api/funds/peers")
def fund_peers(
    code: str = Query(..., min_length=6, max_length=6),
    sort: str = Query("1y", pattern="^(1y|ytd|6m|3m|1m|1w)$"),
    limit: int = Query(1000, ge=100, le=3000),
):
    return _call_fund_service(
        "真实基金同类排行数据获取失败",
        "fund.peers",
        code=code,
        sort=sort,
        limit=limit,
    )


@router.get("/api/funds/peer-persistence")
def fund_peer_persistence(code: str = Query(..., min_length=6, max_length=6)):
    return _call_fund_service(
        "真实基金同类持续性数据获取失败",
        "fund.peer_persistence",
        code=code,
    )


@router.get("/api/funds/alternatives")
def fund_alternatives(
    code: str = Query(..., min_length=6, max_length=6),
    sort: str = Query("1y", pattern="^(1y|ytd|6m|3m|1m|1w)$"),
    limit: int = Query(5, ge=3, le=8),
    months: int = Query(36, ge=6, le=120),
):
    return _call_fund_service(
        "真实基金替代品数据获取失败",
        "fund.alternatives",
        code=code,
        sort=sort,
        limit=limit,
        months=months,
    )


@router.get("/api/funds/dividends")
def fund_dividends(code: str = Query(..., min_length=6, max_length=6)):
    return _call_fund_service("真实基金分红数据获取失败", "fund.dividends", code=code)


@router.post("/api/funds/compare")
def fund_compare(req: FundCompareRequest):
    return _call_fund_service(
        "真实基金对比数据获取失败",
        "fund.compare",
        codes=req.codes,
        months=req.months,
    )


@router.post("/api/funds/overlap")
def fund_overlap(req: FundCompareRequest):
    return _call_fund_service(
        "真实基金持仓重合度数据获取失败",
        "fund.overlap",
        codes=req.codes,
    )
