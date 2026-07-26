# -*- coding: utf-8 -*-
"""Point-in-time fundamental input adapter for A-share quant research.

The adapter deliberately returns raw histories to the pure simulation engine.
It never joins a financial row on report period: availability is defined by
``ann_date`` and valuation availability by ``trade_date``.
"""

from __future__ import annotations

import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd


FINANCIAL_FIELDS = (
    "ts_code,ann_date,end_date,roe,grossprofit_margin,ocf_to_or,"
    "debt_to_assets,update_flag"
)
VALUATION_FIELDS = "ts_code,trade_date,pe_ttm,pb"


def tushare_code(symbol: str) -> str:
    value = str(symbol or "").strip()
    if len(value) != 6 or not value.isdigit():
        raise ValueError(f"无效 A 股代码：{value or '(空)'}")
    if value.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif value.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{value}.{suffix}"


def _clean_financials(frame: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    if frame is None or frame.empty:
        return pd.DataFrame(), {
            "input_rows": 0,
            "usable_rows": 0,
            "malformed_rows": 0,
            "ambiguous_revision_count": 0,
        }
    required = {"ann_date", "end_date"}
    if not required.issubset(frame.columns):
        raise ValueError("fina_indicator 缺少 ann_date 或 end_date")
    value_columns = [
        "roe",
        "grossprofit_margin",
        "ocf_to_or",
        "debt_to_assets",
    ]
    output = frame.copy()
    input_rows = len(output)
    output["ann_date"] = pd.to_datetime(
        output["ann_date"].astype(str),
        format="%Y%m%d",
        errors="coerce",
    ).dt.normalize()
    output["end_date"] = pd.to_datetime(
        output["end_date"].astype(str),
        format="%Y%m%d",
        errors="coerce",
    ).dt.normalize()
    for column in value_columns:
        if column not in output:
            output[column] = float("nan")
        output[column] = pd.to_numeric(output[column], errors="coerce")
    malformed_mask = (
        output["ann_date"].isna()
        | output["end_date"].isna()
        | (
            output["ann_date"].notna()
            & output["end_date"].notna()
            & (output["end_date"] > output["ann_date"])
        )
    )
    malformed = int(malformed_mask.sum())
    output = output.loc[~malformed_mask].copy()

    ambiguous_keys: set[tuple[pd.Timestamp, pd.Timestamp]] = set()
    for key, group in output.groupby(["ann_date", "end_date"]):
        unique_values = group[value_columns].drop_duplicates()
        if len(unique_values) > 1:
            ambiguous_keys.add(key)
    if ambiguous_keys:
        output = output.loc[
            ~output.apply(
                lambda row: (row["ann_date"], row["end_date"])
                in ambiguous_keys,
                axis=1,
            )
        ]
    output = (
        output.drop_duplicates(
            subset=["ann_date", "end_date", *value_columns],
            keep="last",
        )
        .sort_values(["ann_date", "end_date"])
        .reset_index(drop=True)
    )
    return output[
        ["ann_date", "end_date", *value_columns]
    ], {
        "input_rows": input_rows,
        "usable_rows": len(output),
        "malformed_rows": malformed,
        "ambiguous_revision_count": len(ambiguous_keys),
    }


def _clean_valuations(frame: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    if frame is None or frame.empty:
        return pd.DataFrame(), {
            "input_rows": 0,
            "usable_rows": 0,
            "malformed_rows": 0,
        }
    if "trade_date" not in frame.columns:
        raise ValueError("daily_basic 缺少 trade_date")
    output = frame.copy()
    input_rows = len(output)
    output["trade_date"] = pd.to_datetime(
        output["trade_date"].astype(str),
        format="%Y%m%d",
        errors="coerce",
    ).dt.normalize()
    for column in ("pe_ttm", "pb"):
        if column not in output:
            output[column] = float("nan")
        output[column] = pd.to_numeric(output[column], errors="coerce")
        output.loc[
            ~output[column].map(
                lambda value: bool(
                    value is not None
                    and not pd.isna(value)
                    and math.isfinite(float(value))
                )
            ),
            column,
        ] = float("nan")
    malformed = int(output["trade_date"].isna().sum())
    output = (
        output.dropna(subset=["trade_date"])
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    return output[["trade_date", "pe_ttm", "pb"]], {
        "input_rows": input_rows,
        "usable_rows": len(output),
        "malformed_rows": malformed,
    }


def _load_symbol(
    pro,
    symbol: str,
    start_date: dt.date,
    end_date: dt.date,
    required_factors: set[str],
) -> tuple[str, dict[str, pd.DataFrame], dict[str, Any]]:
    code = tushare_code(symbol)
    # Pull one extra reporting year so the first backtest signal can see the
    # latest report that was already public at the research boundary.
    financial_start = start_date - dt.timedelta(days=550)
    financial = (
        pro.fina_indicator(
            ts_code=code,
            start_date=financial_start.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            fields=FINANCIAL_FIELDS,
        )
        if "fundamental_quality" in required_factors
        else pd.DataFrame()
    )
    valuation = (
        pro.daily_basic(
            ts_code=code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            fields=VALUATION_FIELDS,
        )
        if "value" in required_factors
        else pd.DataFrame()
    )
    financials, financial_stats = _clean_financials(financial)
    valuations, valuation_stats = _clean_valuations(valuation)
    required_frames = {
        "fundamental_quality": financials,
        "value": valuations,
    }
    missing = [
        factor
        for factor in sorted(required_factors)
        if required_frames[factor].empty
    ]
    if missing:
        raise ValueError(
            "必需的 point-in-time 因子历史不可用："
            + ", ".join(missing)
        )
    return symbol, {
        "financials": financials,
        "valuations": valuations,
    }, {
        "symbol": symbol,
        "ts_code": code,
        "financials": financial_stats,
        "valuations": valuation_stats,
    }


def load_tushare_point_in_time_fundamentals(
    pro,
    symbols: list[str],
    *,
    history_months: int,
    required_factors: set[str] | None = None,
    end_date: dt.date | None = None,
    max_workers: int = 3,
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, Any]]:
    """Fetch PIT A-share quality and valuation histories with audit evidence."""
    required = set(
        {"fundamental_quality", "value"}
        if required_factors is None
        else required_factors
    )
    required &= {"fundamental_quality", "value"}
    if not required:
        raise ValueError("至少需要一个财务或估值因子")
    end = end_date or dt.date.today()
    start = end - dt.timedelta(days=int(history_months) * 31)
    unique_symbols = sorted({str(symbol) for symbol in symbols if symbol})
    inputs: dict[str, dict[str, pd.DataFrame]] = {}
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    if unique_symbols:
        with ThreadPoolExecutor(
            max_workers=min(max(1, max_workers), len(unique_symbols))
        ) as pool:
            futures = {
                pool.submit(
                    _load_symbol,
                    pro,
                    symbol,
                    start,
                    end,
                    required,
                ): symbol
                for symbol in unique_symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    _, payload, stats = future.result()
                    inputs[symbol] = payload
                    rows.append(stats)
                except Exception as error:
                    failures.append(
                        {"symbol": symbol, "error": str(error)[:300]}
                    )
    rows.sort(key=lambda item: item["symbol"])
    failures.sort(key=lambda item: item["symbol"])
    malformed_rows = sum(
        int((row.get("financials") or {}).get("malformed_rows") or 0)
        + int((row.get("valuations") or {}).get("malformed_rows") or 0)
        for row in rows
    )
    ambiguous = sum(
        int(
            (row.get("financials") or {}).get(
                "ambiguous_revision_count"
            )
            or 0
        )
        for row in rows
    )
    verified = bool(rows) and malformed_rows == 0 and ambiguous == 0
    evidence = {
        "schema_version": "quant_fundamental_evidence.v1",
        "source": "Tushare Pro " + " + ".join(
            endpoint
            for factor, endpoint in (
                ("fundamental_quality", "fina_indicator"),
                ("value", "daily_basic"),
            )
            if factor in required
        ),
        "required_factors": sorted(required),
        "availability_rules": {
            "financials": "ann_date <= signal_date",
            "valuation": "trade_date <= signal_date",
            "report_period_is_not_availability_date": True,
            "same_day_ambiguous_revisions_are_excluded": True,
        },
        "point_in_time_verified": verified,
        "verification_detail": (
            "财务指标按公告日截断、估值按交易日截断，未发现日期缺失或同日歧义修订"
            if verified
            else (
                f"发现日期异常 {malformed_rows} 行、同日歧义修订 {ambiguous} 组；"
                "结果只能保留研究资格"
            )
        ),
        "requested_symbol_count": len(unique_symbols),
        "loaded_symbol_count": len(inputs),
        "failed_symbols": failures,
        "malformed_row_count": malformed_rows,
        "ambiguous_revision_count": ambiguous,
        "assets": rows,
        "history_start": start.isoformat(),
        "history_end": end.isoformat(),
    }
    return inputs, evidence
