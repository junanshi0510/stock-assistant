# -*- coding: utf-8 -*-
"""
本地持久化(SQLite)
==================
用一个本地文件 stock_assistant.db 存"自选股",让 app 有记忆:
关掉重开、重启后端,你收藏的股票都还在。

零配置:SQLite 是 Python 自带的,不用装数据库、不用起服务,就是一个文件。
文件位置:backend/stock_assistant.db(想清空自选,删掉这个文件即可)。

表结构 watchlist:
    id        自增主键
    market    市场(A股/港股/美股)
    symbol    股票代码
    name      备注名称(可空,前端收藏时带上)
    added_at  收藏时间(ISO 字符串)
    UNIQUE(market, symbol)  同一市场同一代码只存一条
"""

import datetime
import json
import os
import sqlite3
import threading

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_assistant.db")

# sqlite 默认不允许跨线程共用连接;FastAPI 是多线程的,这里加锁串行化访问,
# 简单可靠(自选股读写量很小,锁完全够用)。
_lock = threading.Lock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                market   TEXT NOT NULL,
                symbol   TEXT NOT NULL,
                name     TEXT,
                added_at TEXT NOT NULL,
                UNIQUE(market, symbol)
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                market       TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                score        REAL NOT NULL,
                message      TEXT NOT NULL,
                triggered_at TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS holdings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL DEFAULT 'default',
                asset_type  TEXT NOT NULL,
                market      TEXT,
                code        TEXT NOT NULL,
                name        TEXT,
                amount      REAL,
                cost        REAL,
                yesterday_profit REAL,
                profit      REAL,
                profit_rate REAL,
                shares      REAL,
                source      TEXT,
                raw_text    TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(user_id, asset_type, market, code)
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_profiles (
                user_id          TEXT PRIMARY KEY,
                risk             TEXT NOT NULL,
                horizon          TEXT NOT NULL,
                monthly_budget   REAL,
                max_single_ratio REAL NOT NULL,
                updated_at       TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL DEFAULT 'default',
                asset_type  TEXT NOT NULL,
                market      TEXT,
                code        TEXT NOT NULL,
                name        TEXT,
                trade_type  TEXT NOT NULL,
                trade_date  TEXT NOT NULL,
                shares      REAL NOT NULL,
                unit_price  REAL NOT NULL,
                fee         REAL NOT NULL DEFAULT 0,
                note        TEXT,
                source      TEXT NOT NULL DEFAULT 'manual',
                created_at  TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_imports (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL DEFAULT 'default',
                file_sha256   TEXT NOT NULL,
                filename      TEXT,
                row_count     INTEGER NOT NULL,
                imported_at   TEXT NOT NULL,
                UNIQUE(user_id, file_sha256)
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                  TEXT NOT NULL DEFAULT 'default',
                captured_at              TEXT NOT NULL,
                reason                   TEXT NOT NULL,
                holding_count            INTEGER NOT NULL,
                total_amount             REAL,
                total_profit             REAL,
                total_yesterday_profit   REAL,
                holdings_json            TEXT NOT NULL
            )
            """
        )
        _ensure_column(_conn, "holdings", "yesterday_profit", "REAL")
        _ensure_column(_conn, "portfolio_transactions", "source", "TEXT")
        _conn.commit()
    return _conn


def _ensure_column(conn, table: str, column: str, column_type: str):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row["name"] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def list_watchlist() -> list[dict]:
    """返回全部自选股,最近收藏的排在前面。"""
    with _lock:
        rows = _get_conn().execute(
            "SELECT market, symbol, name, added_at FROM watchlist ORDER BY added_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def add_watch(market: str, symbol: str, name: str = "") -> dict:
    """收藏一只股票。已存在则更新名称(不报错,幂等)。"""
    symbol = symbol.strip()
    name = (name or "").strip()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO watchlist (market, symbol, name, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(market, symbol) DO UPDATE SET name=excluded.name
            """,
            (market, symbol, name, now),
        )
        conn.commit()
    return {"market": market, "symbol": symbol, "name": name, "added_at": now}


def remove_watch(market: str, symbol: str) -> bool:
    """取消收藏。返回是否确实删掉了一条。"""
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM watchlist WHERE market=? AND symbol=?", (market, symbol.strip())
        )
        conn.commit()
        return cur.rowcount > 0


def is_watched(market: str, symbol: str) -> bool:
    with _lock:
        row = _get_conn().execute(
            "SELECT 1 FROM watchlist WHERE market=? AND symbol=?", (market, symbol.strip())
        ).fetchone()
    return row is not None


# ==================== 提醒(打分变化监控)====================

def add_alert(market: str, symbol: str, event_type: str, score: float, message: str):
    """记录一条提醒。event_type: 'bullish'/'bearish'/'neutral'。"""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO alerts (market, symbol, event_type, score, message, triggered_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (market, symbol.strip(), event_type, score, message, now),
        )
        conn.commit()


def list_alerts(limit: int = 50) -> list[dict]:
    """返回最近的提醒,最新的在前。"""
    with _lock:
        rows = _get_conn().execute(
            "SELECT market, symbol, event_type, score, message, triggered_at FROM alerts ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_alerts():
    """清空全部提醒。"""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM alerts")
        conn.commit()


# ==================== Investment profile ====================

_PROFILE_DEFAULTS = {
    "risk": "balanced",
    "horizon": "mid_long",
    "monthly_budget": None,
    "max_single_ratio": 35.0,
}


def get_investment_profile(user_id: str = "default") -> dict:
    """Return saved user constraints without pretending defaults are configured choices."""
    with _lock:
        row = _get_conn().execute(
            """
            SELECT risk, horizon, monthly_budget, max_single_ratio, updated_at
            FROM investment_profiles
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return {**_PROFILE_DEFAULTS, "configured": False, "updated_at": None}
    return {**dict(row), "configured": True}


def save_investment_profile(profile: dict, user_id: str = "default") -> dict:
    """Persist explicit user-selected investment constraints for decision prioritization."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    values = (
        user_id,
        str(profile["risk"]),
        str(profile["horizon"]),
        profile.get("monthly_budget"),
        float(profile["max_single_ratio"]),
        now,
    )
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO investment_profiles (
                user_id, risk, horizon, monthly_budget, max_single_ratio, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                risk=excluded.risk,
                horizon=excluded.horizon,
                monthly_budget=excluded.monthly_budget,
                max_single_ratio=excluded.max_single_ratio,
                updated_at=excluded.updated_at
            """,
            values,
        )
        conn.commit()
    return get_investment_profile(user_id)


# ==================== 我的持仓 ====================

def list_holdings(user_id: str = "default") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT id, user_id, asset_type, market, code, name, amount, cost,
                   yesterday_profit, profit, profit_rate, shares, source,
                   created_at, updated_at
            FROM holdings
            WHERE user_id=?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_holding(item: dict, user_id: str = "default") -> dict:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    asset_type = str(item.get("asset_type") or "").strip()
    market = str(item.get("market") or "").strip()
    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    if not asset_type or not code:
        raise ValueError("持仓类型和代码不能为空")
    values = (
        user_id,
        asset_type,
        market,
        code,
        name,
        item.get("amount"),
        item.get("cost"),
        item.get("yesterday_profit"),
        item.get("profit"),
        item.get("profit_rate"),
        item.get("shares"),
        str(item.get("source") or "manual"),
        str(item.get("raw_text") or ""),
        now,
        now,
    )
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO holdings (
                user_id, asset_type, market, code, name, amount, cost,
                yesterday_profit, profit, profit_rate, shares, source, raw_text,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, asset_type, market, code) DO UPDATE SET
                name=excluded.name,
                amount=excluded.amount,
                cost=excluded.cost,
                yesterday_profit=excluded.yesterday_profit,
                profit=excluded.profit,
                profit_rate=excluded.profit_rate,
                shares=excluded.shares,
                source=excluded.source,
                raw_text=excluded.raw_text,
                updated_at=excluded.updated_at
            """,
            values,
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, user_id, asset_type, market, code, name, amount, cost,
                   yesterday_profit, profit, profit_rate, shares, source,
                   created_at, updated_at
            FROM holdings
            WHERE user_id=? AND asset_type=? AND market=? AND code=?
            """,
            (user_id, asset_type, market, code),
        ).fetchone()
    return dict(row)


def delete_holding(holding_id: int, user_id: str = "default") -> bool:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("DELETE FROM holdings WHERE id=? AND user_id=?", (holding_id, user_id))
        conn.commit()
        return cur.rowcount > 0


# ==================== 交易流水与组合快照 ====================

_TRANSACTION_TYPES = {"buy", "sell", "opening"}


def _as_number(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须是数字") from exc
    if not number > 0:
        raise ValueError(f"{field}必须大于0")
    return number


def list_portfolio_transactions(user_id: str = "default") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT id, user_id, asset_type, market, code, name, trade_type,
                   trade_date, shares, unit_price, fee, note,
                   COALESCE(source, 'manual') AS source, created_at
            FROM portfolio_transactions
            WHERE user_id=?
            ORDER BY trade_date DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _portfolio_transaction_values(item: dict, user_id: str, now: str) -> tuple:
    asset_type = str(item.get("asset_type") or "").strip()
    market = str(item.get("market") or "").strip()
    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    trade_type = str(item.get("trade_type") or "").strip()
    trade_date = str(item.get("trade_date") or "").strip()
    shares = _as_number(item.get("shares"), "交易份额")
    unit_price = _as_number(item.get("unit_price"), "成交单价")
    try:
        fee = float(item.get("fee") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("费用必须是数字") from exc
    if fee < 0:
        raise ValueError("费用不能小于0")
    if not asset_type or not code:
        raise ValueError("交易类型、资产类型和代码不能为空")
    if trade_type not in _TRANSACTION_TYPES:
        raise ValueError("不支持的交易方向")
    if not trade_date:
        raise ValueError("交易日期不能为空")
    return (
        user_id,
        asset_type,
        market,
        code,
        name,
        trade_type,
        trade_date,
        shares,
        unit_price,
        fee,
        str(item.get("note") or "").strip(),
        str(item.get("source") or "manual").strip() or "manual",
        now,
    )


def add_portfolio_transaction(item: dict, user_id: str = "default") -> dict:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    values = _portfolio_transaction_values(item, user_id, now)
    with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            """
            INSERT INTO portfolio_transactions (
                user_id, asset_type, market, code, name, trade_type, trade_date,
                shares, unit_price, fee, note, source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, user_id, asset_type, market, code, name, trade_type,
                   trade_date, shares, unit_price, fee, note,
                   COALESCE(source, 'manual') AS source, created_at
            FROM portfolio_transactions
            WHERE id=?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def portfolio_import_exists(file_sha256: str, user_id: str = "default") -> bool:
    digest = str(file_sha256 or "").strip().lower()
    if not digest:
        return False
    with _lock:
        row = _get_conn().execute(
            "SELECT 1 FROM portfolio_imports WHERE user_id=? AND file_sha256=?",
            (user_id, digest),
        ).fetchone()
    return row is not None


def add_portfolio_transactions(
    items: list[dict],
    *,
    file_sha256: str,
    filename: str = "",
    user_id: str = "default",
) -> list[dict]:
    """Atomically store a confirmed CSV batch and its content hash, never the raw file."""
    if not items:
        raise ValueError("没有可导入的交易流水")
    digest = str(file_sha256 or "").strip().lower()
    if len(digest) != 64:
        raise ValueError("导入文件校验标识无效")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    allowed_sources = {"csv_import", "tiantian_fund_transaction_export"}
    values = []
    for item in items:
        source = str(item.get("source") or "").strip()
        values.append(_portfolio_transaction_values(
            {**item, "source": source if source in allowed_sources else "csv_import"},
            user_id,
            now,
        ))
    with _lock:
        conn = _get_conn()
        if conn.execute(
            "SELECT 1 FROM portfolio_imports WHERE user_id=? AND file_sha256=?",
            (user_id, digest),
        ).fetchone():
            raise ValueError("该账单文件已经导入过，未重复写入交易流水")
        try:
            conn.execute("BEGIN")
            inserted_ids = []
            for value in values:
                cursor = conn.execute(
                    """
                    INSERT INTO portfolio_transactions (
                        user_id, asset_type, market, code, name, trade_type, trade_date,
                        shares, unit_price, fee, note, source, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    value,
                )
                inserted_ids.append(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO portfolio_imports (user_id, file_sha256, filename, row_count, imported_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, digest, str(filename or "")[:255], len(values), now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        placeholders = ",".join("?" for _ in inserted_ids)
        rows = conn.execute(
            f"""
            SELECT id, user_id, asset_type, market, code, name, trade_type,
                   trade_date, shares, unit_price, fee, note,
                   COALESCE(source, 'manual') AS source, created_at
            FROM portfolio_transactions
            WHERE id IN ({placeholders})
            ORDER BY id ASC
            """,
            inserted_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def delete_portfolio_transaction(transaction_id: int, user_id: str = "default") -> bool:
    with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            "DELETE FROM portfolio_transactions WHERE id=? AND user_id=?",
            (transaction_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def _snapshot_number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def create_portfolio_snapshot(items: list[dict], reason: str = "manual", user_id: str = "default") -> dict:
    """Store an auditable point-in-time copy of user-confirmed holding values."""
    compact_items = [
        {
            "asset_type": item.get("asset_type"),
            "market": item.get("market"),
            "code": item.get("code"),
            "name": item.get("name"),
            "amount": _snapshot_number(item.get("amount")),
            "profit": _snapshot_number(item.get("profit")),
            "yesterday_profit": _snapshot_number(item.get("yesterday_profit")),
            "shares": _snapshot_number(item.get("shares")),
        }
        for item in items
    ]
    total_amount = sum(item["amount"] or 0 for item in compact_items)
    total_profit = sum(item["profit"] or 0 for item in compact_items)
    total_yesterday_profit = sum(item["yesterday_profit"] or 0 for item in compact_items)
    captured_at = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            """
            INSERT INTO portfolio_snapshots (
                user_id, captured_at, reason, holding_count, total_amount,
                total_profit, total_yesterday_profit, holdings_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                captured_at,
                str(reason or "manual"),
                len(compact_items),
                total_amount if compact_items else None,
                total_profit if compact_items else None,
                total_yesterday_profit if compact_items else None,
                json.dumps(compact_items, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, captured_at, reason, holding_count, total_amount,
                   total_profit, total_yesterday_profit
            FROM portfolio_snapshots
            WHERE id=?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def list_portfolio_snapshots(
    limit: int = 24,
    user_id: str = "default",
    *,
    include_holdings: bool = False,
) -> list[dict]:
    fields = """
        id, captured_at, reason, holding_count, total_amount,
        total_profit, total_yesterday_profit
    """
    if include_holdings:
        fields += ", holdings_json"
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT {fields}
            FROM portfolio_snapshots
            WHERE user_id=?
            ORDER BY captured_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        ).fetchall()
    items = [dict(row) for row in rows]
    if not include_holdings:
        return items
    for item in items:
        raw_holdings = item.pop("holdings_json", "[]")
        try:
            item["holdings"] = json.loads(raw_holdings or "[]")
        except (TypeError, json.JSONDecodeError):
            item["holdings"] = []
    return items
