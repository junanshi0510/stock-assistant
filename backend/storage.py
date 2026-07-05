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
        _ensure_column(_conn, "holdings", "yesterday_profit", "REAL")
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
