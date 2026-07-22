# -*- coding: utf-8 -*-
"""Allowlisted real-market operations executed by market-data workers."""

from __future__ import annotations

import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import analysis
import backtest as backtest_mod
import compare as compare_mod
import data_fetch
import fundamentals as fundamentals_mod
import funds as funds_mod
import hot_stocks
import market_daily as market_daily_mod
import ml_model
import multi_compare as multi_compare_mod
import quotes as quotes_mod
import sectors as sectors_mod
import sentiment as sentiment_mod
import fund_switch_cost_service
import holding_level_recurrence
import holdings as holdings_mod
import storage


OperationHandler = Callable[[dict[str, Any]], dict[str, Any]]


class MarketDataOperationError(RuntimeError):
    pass


class MarketDataOperationClientError(MarketDataOperationError):
    def __init__(self, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.http_status = int(http_status)


def _safe_number(value: Any) -> Any:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return None if math.isnan(number) or math.isinf(number) else number


def json_ready(value: Any) -> Any:
    """Convert provider and dataframe scalar values into strict JSON values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_ready(item())
        except (TypeError, ValueError):
            pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except (TypeError, ValueError):
            pass
    return value


def _fund(function: Callable[..., dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    return function(**payload)


def _search_us(payload: dict[str, Any]) -> dict[str, Any]:
    hits = data_fetch.search_us_symbol(str(payload["keyword"]))
    return {"results": hits.to_dict(orient="records")}


def _analyze_stock(payload: dict[str, Any]) -> dict[str, Any]:
    market = str(payload["market"])
    symbol = str(payload["symbol"])
    months = int(payload["months"])
    raw = data_fetch.get_history_months(market, symbol, months)
    result = analysis.score(raw)
    candles = []
    for _, row in result["df"].iterrows():
        candles.append(
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "open": _safe_number(row["open"]),
                "high": _safe_number(row["high"]),
                "low": _safe_number(row["low"]),
                "close": _safe_number(row["close"]),
                "volume": _safe_number(row["volume"]),
                "ma5": _safe_number(row["ma5"]),
                "ma20": _safe_number(row["ma20"]),
                "ma60": _safe_number(row["ma60"]),
                "boll_up": _safe_number(row["boll_up"]),
                "boll_low": _safe_number(row["boll_low"]),
            }
        )
    return {
        "market": market,
        "symbol": symbol,
        "score": result["score"],
        "probability": result["probability"],
        "direction": result["direction"],
        "reasons": [
            {"name": reason[0], "delta": reason[1], "detail": reason[2]}
            for reason in result["reasons"]
        ],
        "indicators": {
            key: _safe_number(value) for key, value in result["indicators"].items()
        },
        "candles": candles,
    }


def _backtest(payload: dict[str, Any]) -> dict[str, Any]:
    market = str(payload["market"])
    symbol = str(payload["symbol"])
    raw = data_fetch.get_history_months(market, symbol, 48)
    result = backtest_mod.backtest(
        raw,
        horizon=int(payload["horizon"]),
        entry_score=float(payload.get("entry_score", 65)),
        stop_atr=float(payload.get("stop_atr", 2)),
        target_atr=float(payload.get("target_atr", 3)),
        commission_bps=float(payload.get("commission_bps", 5)),
        slippage_bps=float(payload.get("slippage_bps", 5)),
        sell_tax_bps=float(payload.get("sell_tax_bps", 0)),
        risk_per_trade_pct=float(payload.get("risk_per_trade_pct", 1)),
        max_position_pct=float(payload.get("max_position_pct", 30)),
    )
    result.update({"market": market, "symbol": symbol})
    return result


def _fundamentals(payload: dict[str, Any]) -> dict[str, Any]:
    return fundamentals_mod.get_fundamentals(
        str(payload["market"]), str(payload["symbol"])
    )


def _quote(payload: dict[str, Any]) -> dict[str, Any]:
    return quotes_mod.get_quote(str(payload["market"]), str(payload["symbol"]))


def _quote_level_history(payload: dict[str, Any]) -> dict[str, Any]:
    return quotes_mod.get_quote_level_history(
        str(payload["market"]),
        str(payload["symbol"]),
        months=int(payload["months"]),
    )


def _ml(payload: dict[str, Any]) -> dict[str, Any]:
    raw = data_fetch.get_history_months(
        str(payload["market"]), str(payload["symbol"]), 60
    )
    return ml_model.predict(raw, horizon=int(payload["horizon"]))


def _news(payload: dict[str, Any]) -> dict[str, Any]:
    return sentiment_mod.get_sentiment(
        str(payload["market"]), str(payload["symbol"])
    )


def _compare(payload: dict[str, Any]) -> dict[str, Any]:
    return compare_mod.compare(
        str(payload["market"]), str(payload["symbol"]), int(payload["months"])
    )


def _scan(payload: dict[str, Any]) -> dict[str, Any]:
    market = str(payload["market"])
    symbols = [str(symbol).strip() for symbol in payload["symbols"] if str(symbol).strip()][
        :40
    ]
    if not symbols:
        raise ValueError("stock symbol list is empty")
    months = max(6, min(36, int(payload["months"])))

    if market == "A\u80a1" and len(symbols) >= 3:
        try:
            comparison = multi_compare_mod.compare_many(market, symbols, months)
            results = [
                {
                    "symbol": row["symbol"],
                    "score": row["score"],
                    "probability": row["probability"],
                    "direction": row["direction"],
                    "close": row["end_price"],
                }
                for row in comparison["metrics"]
            ]
            results.sort(key=lambda row: row["score"], reverse=True)
            return {
                "market": market,
                "results": results,
                "failed": comparison.get("failed", []),
                "count": len(results),
                "failed_count": comparison.get("failed_count", 0),
            }
        except Exception:
            # The alternate path still retrieves and scores only real market data.
            pass

    def score_symbol(symbol: str) -> dict[str, Any]:
        try:
            dataframe = data_fetch.get_history_months(
                market, symbol, months, fetch_months=months
            )
            return {"symbol": symbol, **analysis.score_only(dataframe)}
        except Exception as error:
            return {"symbol": symbol, "error": str(error)[:80]}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(score_symbol, symbols))
    succeeded = [row for row in results if "error" not in row]
    failed = [row for row in results if "error" in row]
    succeeded.sort(key=lambda row: row["score"], reverse=True)
    return {
        "market": market,
        "results": succeeded,
        "failed": failed,
        "count": len(succeeded),
        "failed_count": len(failed),
    }


def _multi_compare(payload: dict[str, Any]) -> dict[str, Any]:
    return multi_compare_mod.compare_many(
        str(payload["market"]),
        [str(symbol) for symbol in payload["symbols"]],
        int(payload["months"]),
        include_fundamentals=bool(payload.get("include_fundamentals", False)),
    )


def _hot(payload: dict[str, Any]) -> dict[str, Any]:
    return hot_stocks.get_hot_stocks(
        str(payload["market"]),
        str(payload["period"]),
        str(payload["type"]),
        int(payload["limit"]),
    )


def _sectors(payload: dict[str, Any]) -> dict[str, Any]:
    return sectors_mod.get_sector_analysis(
        market=str(payload["market"]),
        sector_limit=int(payload["sector_limit"]),
        stock_limit=int(payload["stock_limit"]),
        include_concepts=bool(payload["include_concepts"]),
    )


def _market_daily(payload: dict[str, Any]) -> dict[str, Any]:
    return market_daily_mod.get_market_daily(
        risk=str(payload["risk"]), fund_limit=int(payload["fund_limit"])
    )


def _portfolio_user(payload: dict[str, Any]) -> str:
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id or len(user_id) > 128:
        raise ValueError("portfolio user scope is missing or invalid")
    return user_id


def _portfolio_watchlist(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = _portfolio_user(payload)
    items = storage.list_watchlist(user_id=user_id)

    def enrich(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        try:
            dataframe = data_fetch.get_history_months(
                item["market"], item["symbol"], 12, fetch_months=12
            )
            result.update(analysis.score_only(dataframe))
        except Exception as error:
            result["error"] = str(error)[:80]
        return result

    if items:
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(enrich, items))
    return {"items": items, "count": len(items)}


def _portfolio_level_recurrence(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = _portfolio_user(payload)
    items = storage.list_holdings(user_id=user_id)
    return holding_level_recurrence.build_holding_level_recurrence(
        items,
        stock_months=int(payload["months"]),
        max_workers=6,
    )


def _portfolio_insights(payload: dict[str, Any]) -> dict[str, Any]:
    return holdings_mod.holdings_insights(
        max_funds=int(payload["max_funds"]),
        user_id=_portfolio_user(payload),
    )


def _portfolio_fund_alternatives(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return fund_switch_cost_service.get_holding_fund_alternatives(
            int(payload["holding_id"]),
            sort=str(payload["sort"]),
            limit=int(payload["limit"]),
            months=int(payload["months"]),
            user_id=_portfolio_user(payload),
        )
    except fund_switch_cost_service.HoldingNotFoundError as error:
        raise MarketDataOperationClientError(str(error), http_status=404) from error


def _portfolio_exposure(payload: dict[str, Any]) -> dict[str, Any]:
    return holdings_mod.fund_lookthrough_exposure(
        max_funds=int(payload["max_funds"]),
        user_id=_portfolio_user(payload),
    )


_OPERATIONS: dict[str, OperationHandler] = {
    "fund.hot": lambda payload: _fund(funds_mod.get_hot_funds, payload),
    "fund.categories": lambda payload: _fund(funds_mod.get_fund_categories, payload),
    "fund.opportunities": lambda payload: _fund(
        funds_mod.get_fund_opportunities, payload
    ),
    "fund.search": lambda payload: _fund(funds_mod.search_funds, payload),
    "fund.analyze": lambda payload: _fund(funds_mod.analyze_fund, payload),
    "fund.portfolio": lambda payload: _fund(funds_mod.get_fund_portfolio, payload),
    "fund.estimate": lambda payload: _fund(funds_mod.get_fund_estimate, payload),
    "fund.disclosure_changes": lambda payload: _fund(
        funds_mod.get_fund_disclosure_changes, payload
    ),
    "fund.peers": lambda payload: _fund(funds_mod.get_fund_peers, payload),
    "fund.peer_persistence": lambda payload: _fund(
        funds_mod.get_fund_peer_persistence, payload
    ),
    "fund.alternatives": lambda payload: _fund(
        funds_mod.get_fund_alternatives, payload
    ),
    "fund.dividends": lambda payload: _fund(funds_mod.get_fund_dividends, payload),
    "fund.compare": lambda payload: _fund(funds_mod.compare_funds, payload),
    "fund.overlap": lambda payload: _fund(funds_mod.analyze_fund_overlap, payload),
    "market.search_us": _search_us,
    "market.analyze": _analyze_stock,
    "market.backtest": _backtest,
    "market.fundamentals": _fundamentals,
    "market.quote": _quote,
    "market.quote_level_history": _quote_level_history,
    "market.ml": _ml,
    "market.news": _news,
    "market.compare": _compare,
    "market.scan": _scan,
    "market.multi_compare": _multi_compare,
    "market.hot": _hot,
    "market.sectors": _sectors,
    "market.daily": _market_daily,
    "portfolio.watchlist": _portfolio_watchlist,
    "portfolio.level_recurrence": _portfolio_level_recurrence,
    "portfolio.insights": _portfolio_insights,
    "portfolio.fund_alternatives": _portfolio_fund_alternatives,
    "portfolio.exposure": _portfolio_exposure,
}


def execute_operation(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    handler = _OPERATIONS.get(str(operation))
    if handler is None:
        raise MarketDataOperationError(f"operation is not allowlisted: {operation}")
    result = handler(dict(payload))
    if not isinstance(result, dict):
        raise MarketDataOperationError("market operation must return an object")
    return json_ready(result)


def allowed_operations() -> tuple[str, ...]:
    return tuple(sorted(_OPERATIONS))
