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
import hashlib
import json
import os
import sqlite3
import threading
import uuid

from investment_policy import (
    CONSENT_TEXT_SHA256,
    CONSENT_VERSION,
    canonical_json,
    payload_sha256,
)

_DB_PATH = (
    os.getenv("STOCK_ASSISTANT_DB_PATH")
    or os.getenv("AGENT_DB_PATH")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_assistant.db")
)

# sqlite 默认不允许跨线程共用连接;FastAPI 是多线程的,这里加锁串行化访问,
# 简单可靠(自选股读写量很小,锁完全够用)。
_lock = threading.RLock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=30000")
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
            CREATE TABLE IF NOT EXISTS storage_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at   TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_watchlist (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  TEXT NOT NULL,
                market   TEXT NOT NULL,
                symbol   TEXT NOT NULL,
                name     TEXT,
                added_at TEXT NOT NULL,
                UNIQUE(user_id, market, symbol)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_watchlist_recent
            ON user_watchlist(user_id, added_at DESC)
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_user_alerts_recent
            ON user_alerts(user_id, triggered_at DESC)
            """
        )
        scoped_migration = "scoped-watchlist-alerts.v1"
        if not _conn.execute(
            "SELECT 1 FROM storage_schema_migrations WHERE migration_id=?",
            (scoped_migration,),
        ).fetchone():
            _conn.execute(
                """
                INSERT OR IGNORE INTO user_watchlist(user_id, market, symbol, name, added_at)
                SELECT 'default', market, symbol, name, added_at FROM watchlist
                """
            )
            _conn.execute(
                """
                INSERT INTO user_alerts(user_id, market, symbol, event_type, score, message, triggered_at)
                SELECT 'default', market, symbol, event_type, score, message, triggered_at FROM alerts
                """
            )
            _conn.execute(
                "INSERT INTO storage_schema_migrations(migration_id, applied_at) VALUES (?, ?)",
                (scoped_migration, datetime.datetime.now(datetime.timezone.utc).isoformat()),
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
                allowed_fund_markets TEXT NOT NULL DEFAULT '["mainland"]',
                accept_fx_risk   INTEGER NOT NULL DEFAULT 0,
                updated_at       TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_profile_versions (
                id                    TEXT PRIMARY KEY,
                user_id               TEXT NOT NULL,
                version_no            INTEGER NOT NULL,
                status                TEXT NOT NULL,
                payload_json          TEXT NOT NULL,
                payload_sha256        TEXT NOT NULL,
                validation_json       TEXT NOT NULL,
                questionnaire_version TEXT NOT NULL,
                consent_version       TEXT,
                consent_text_sha256   TEXT,
                created_at            TEXT NOT NULL,
                activated_at          TEXT,
                review_due_at         TEXT,
                superseded_at         TEXT,
                UNIQUE(user_id, version_no)
            )
            """
        )
        _conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_investment_profile_active
            ON investment_profile_versions(user_id)
            WHERE status='active'
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_investment_profile_history
            ON investment_profile_versions(user_id, version_no DESC)
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_investment_profile_payload_immutable
            BEFORE UPDATE OF user_id, version_no, payload_json, payload_sha256,
                             validation_json, questionnaire_version, created_at
            ON investment_profile_versions
            BEGIN
                SELECT RAISE(ABORT, 'investment profile version payload is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_investment_profile_status_transition
            BEFORE UPDATE OF status ON investment_profile_versions
            WHEN NOT (
                NEW.status=OLD.status
                OR (OLD.status='draft' AND NEW.status='active')
                OR (OLD.status='active' AND NEW.status='superseded')
            )
            BEGIN
                SELECT RAISE(ABORT, 'invalid investment profile status transition');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_investment_profile_activation_immutable
            BEFORE UPDATE OF consent_version, consent_text_sha256, activated_at, review_due_at
            ON investment_profile_versions
            WHEN OLD.status!='draft'
            BEGIN
                SELECT RAISE(ABORT, 'investment profile activation metadata is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_profile_audit_events (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                version_id     TEXT,
                sequence_no    INTEGER NOT NULL,
                event_type     TEXT NOT NULL,
                actor_id       TEXT NOT NULL,
                details_json   TEXT NOT NULL,
                previous_hash  TEXT,
                event_hash     TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                UNIQUE(user_id, sequence_no)
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
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_exposure_snapshots (
                id                 TEXT PRIMARY KEY,
                user_id            TEXT NOT NULL DEFAULT 'default',
                schema_version     TEXT NOT NULL,
                holdings_sha256    TEXT NOT NULL,
                target_code        TEXT,
                profile_version_id TEXT,
                status             TEXT NOT NULL,
                payload_json       TEXT NOT NULL,
                payload_sha256     TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                UNIQUE(user_id, payload_sha256)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_exposure_history
            ON portfolio_exposure_snapshots(user_id, created_at DESC)
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_exposure_target
            ON portfolio_exposure_snapshots(user_id, target_code, created_at DESC)
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_portfolio_exposure_immutable
            BEFORE UPDATE ON portfolio_exposure_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'portfolio exposure snapshot is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_action_reports (
                id                 TEXT PRIMARY KEY,
                user_id            TEXT NOT NULL DEFAULT 'default',
                schema_version     TEXT NOT NULL,
                ruleset_version    TEXT NOT NULL,
                holdings_sha256    TEXT NOT NULL,
                theses_sha256      TEXT,
                profile_version_id TEXT,
                status             TEXT NOT NULL,
                payload_json       TEXT NOT NULL,
                payload_sha256     TEXT NOT NULL,
                created_at         TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS holding_thesis_versions (
                id                      TEXT PRIMARY KEY,
                user_id                 TEXT NOT NULL DEFAULT 'default',
                asset_type              TEXT NOT NULL,
                market                  TEXT NOT NULL,
                code                    TEXT NOT NULL,
                version_no              INTEGER NOT NULL,
                schema_version          TEXT NOT NULL,
                state                   TEXT NOT NULL,
                payload_json            TEXT NOT NULL,
                payload_sha256          TEXT NOT NULL,
                previous_version_id     TEXT,
                previous_payload_sha256 TEXT,
                created_at              TEXT NOT NULL,
                UNIQUE(user_id, asset_type, market, code, version_no)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_holding_thesis_latest
            ON holding_thesis_versions(
                user_id, asset_type, market, code, version_no DESC
            )
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_holding_thesis_immutable
            BEFORE UPDATE ON holding_thesis_versions
            BEGIN
                SELECT RAISE(ABORT, 'holding thesis version is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_holding_thesis_no_delete
            BEFORE DELETE ON holding_thesis_versions
            BEGIN
                SELECT RAISE(ABORT, 'holding thesis history cannot be deleted');
            END
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_action_report_history
            ON portfolio_action_reports(user_id, created_at DESC)
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_portfolio_action_report_immutable
            BEFORE UPDATE ON portfolio_action_reports
            BEGIN
                SELECT RAISE(ABORT, 'portfolio action report is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_tasks (
                id                TEXT PRIMARY KEY,
                user_id           TEXT NOT NULL,
                action_key        TEXT NOT NULL,
                fingerprint       TEXT NOT NULL,
                revision          INTEGER NOT NULL DEFAULT 1,
                status            TEXT NOT NULL
                                  CHECK(status IN ('open', 'snoozed', 'acknowledged', 'resolved')),
                priority          TEXT NOT NULL
                                  CHECK(priority IN ('high', 'medium', 'normal')),
                category          TEXT NOT NULL,
                title             TEXT NOT NULL,
                detail            TEXT NOT NULL,
                evidence_json     TEXT NOT NULL,
                target            TEXT NOT NULL,
                action_label      TEXT NOT NULL,
                source            TEXT NOT NULL,
                first_seen_at     TEXT NOT NULL,
                last_seen_at      TEXT NOT NULL,
                acknowledged_at   TEXT,
                snoozed_until     TEXT,
                resolved_at       TEXT,
                UNIQUE(user_id, action_key)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_tasks_inbox
            ON decision_tasks(user_id, status, priority, last_seen_at DESC)
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_task_events (
                id             TEXT PRIMARY KEY,
                task_id        TEXT NOT NULL,
                user_id        TEXT NOT NULL,
                sequence_no    INTEGER NOT NULL,
                event_type     TEXT NOT NULL,
                actor_id       TEXT NOT NULL,
                details_json   TEXT NOT NULL,
                previous_hash  TEXT,
                event_hash     TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                UNIQUE(task_id, sequence_no),
                FOREIGN KEY(task_id) REFERENCES decision_tasks(id)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_task_events_history
            ON decision_task_events(user_id, task_id, sequence_no)
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_decision_task_events_no_update
            BEFORE UPDATE ON decision_task_events
            BEGIN
                SELECT RAISE(ABORT, 'decision task events are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_decision_task_events_no_delete
            BEFORE DELETE ON decision_task_events
            BEGIN
                SELECT RAISE(ABORT, 'decision task events are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_check_schedules (
                id                    TEXT PRIMARY KEY,
                user_id               TEXT NOT NULL UNIQUE,
                status                TEXT NOT NULL
                                      CHECK(status IN ('active', 'paused')),
                interval_hours        INTEGER NOT NULL
                                      CHECK(interval_hours IN (24, 72, 168)),
                revision              INTEGER NOT NULL DEFAULT 1,
                next_run_at           TEXT,
                last_started_at       TEXT,
                last_finished_at      TEXT,
                last_success_at       TEXT,
                last_result_status    TEXT
                                      CHECK(last_result_status IN ('succeeded', 'partial', 'failed')),
                last_open_count       INTEGER,
                last_unavailable_count INTEGER,
                attempt_count         INTEGER NOT NULL DEFAULT 0,
                consecutive_failures  INTEGER NOT NULL DEFAULT 0,
                last_error_code       TEXT,
                last_error_message    TEXT,
                lease_owner           TEXT,
                lease_expires_at      TEXT,
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_check_schedule_due
            ON decision_check_schedules(status, next_run_at, lease_expires_at)
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_check_events (
                id             TEXT PRIMARY KEY,
                schedule_id    TEXT NOT NULL,
                user_id        TEXT NOT NULL,
                sequence_no    INTEGER NOT NULL,
                event_type     TEXT NOT NULL,
                actor_id       TEXT NOT NULL,
                details_json   TEXT NOT NULL,
                previous_hash  TEXT,
                event_hash     TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                UNIQUE(user_id, sequence_no),
                FOREIGN KEY(schedule_id) REFERENCES decision_check_schedules(id)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_decision_check_events_history
            ON decision_check_events(user_id, sequence_no)
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_decision_check_events_no_update
            BEFORE UPDATE ON decision_check_events
            BEGIN
                SELECT RAISE(ABORT, 'decision check events are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_decision_check_events_no_delete
            BEFORE DELETE ON decision_check_events
            BEGIN
                SELECT RAISE(ABORT, 'decision check events are immutable');
            END
            """
        )
        _ensure_column(_conn, "holdings", "yesterday_profit", "REAL")
        _ensure_column(_conn, "portfolio_transactions", "source", "TEXT")
        _ensure_column(_conn, "portfolio_action_reports", "theses_sha256", "TEXT")
        _ensure_column(
            _conn,
            "investment_profiles",
            "allowed_fund_markets",
            "TEXT NOT NULL DEFAULT '[\"mainland\"]'",
        )
        _ensure_column(
            _conn,
            "investment_profiles",
            "accept_fx_risk",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _migrate_legacy_investment_profiles(_conn)
        _conn.commit()
    return _conn


def _ensure_column(conn, table: str, column: str, column_type: str):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row["name"] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def list_watchlist(user_id: str = "default") -> list[dict]:
    """返回全部自选股,最近收藏的排在前面。"""
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT market, symbol, name, added_at
            FROM user_watchlist WHERE user_id=? ORDER BY added_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_watchlist() -> list[dict]:
    """Return all scoped rows for the background monitor only."""
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT user_id, market, symbol, name, added_at
            FROM user_watchlist ORDER BY user_id, added_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def add_watch(market: str, symbol: str, name: str = "", user_id: str = "default") -> dict:
    """收藏一只股票。已存在则更新名称(不报错,幂等)。"""
    symbol = symbol.strip()
    name = (name or "").strip()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO user_watchlist (user_id, market, symbol, name, added_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, market, symbol) DO UPDATE SET name=excluded.name
            """,
            (user_id, market, symbol, name, now),
        )
        conn.commit()
    return {"market": market, "symbol": symbol, "name": name, "added_at": now}


def remove_watch(market: str, symbol: str, user_id: str = "default") -> bool:
    """取消收藏。返回是否确实删掉了一条。"""
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM user_watchlist WHERE user_id=? AND market=? AND symbol=?",
            (user_id, market, symbol.strip()),
        )
        conn.commit()
        return cur.rowcount > 0


def is_watched(market: str, symbol: str, user_id: str = "default") -> bool:
    with _lock:
        row = _get_conn().execute(
            "SELECT 1 FROM user_watchlist WHERE user_id=? AND market=? AND symbol=?",
            (user_id, market, symbol.strip()),
        ).fetchone()
    return row is not None


# ==================== 提醒(打分变化监控)====================

def add_alert(
    market: str,
    symbol: str,
    event_type: str,
    score: float,
    message: str,
    user_id: str = "default",
):
    """记录一条提醒。event_type: 'bullish'/'bearish'/'neutral'。"""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO user_alerts (
                user_id, market, symbol, event_type, score, message, triggered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, market, symbol.strip(), event_type, score, message, now),
        )
        conn.commit()


def list_alerts(limit: int = 50, user_id: str = "default") -> list[dict]:
    """返回最近的提醒,最新的在前。"""
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT market, symbol, event_type, score, message, triggered_at
            FROM user_alerts WHERE user_id=? ORDER BY triggered_at DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_alerts(user_id: str = "default"):
    """清空全部提醒。"""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM user_alerts WHERE user_id=?", (user_id,))
        conn.commit()


# ==================== Investment profile ====================

_PROFILE_DEFAULTS = {
    "risk": "balanced",
    "horizon": "mid_long",
    "experience_level": "beginner",
    "primary_objective": "balanced_growth",
    "monthly_budget": 0.0,
    "max_single_ratio": 35.0,
    "max_equity_ratio": 70.0,
    "max_industry_ratio": 30.0,
    "max_drawdown_pct": 25.0,
    "liquidity_reserve_months": 3.0,
    "allowed_fund_markets": ["mainland"],
    "accept_fx_risk": False,
    "emergency_fund_confirmed": False,
    "review_cycle_months": 6,
}


class InvestmentProfileConflictError(RuntimeError):
    pass


def _profile_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _decode_json(value, default):
    try:
        return json.loads(value) if value not in (None, "") else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _profile_version_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    payload = _decode_json(item.pop("payload_json", None), {})
    validation = _decode_json(item.pop("validation_json", None), {})
    item["payload"] = payload
    item["validation"] = validation
    item["integrity_verified"] = payload_sha256(payload) == item.get("payload_sha256")
    return item


def _append_profile_audit(conn, user_id: str, version_id: str | None, event_type: str,
                          details: dict, actor_id: str) -> dict:
    previous = conn.execute(
        """
        SELECT sequence_no, event_hash
        FROM investment_profile_audit_events
        WHERE user_id=?
        ORDER BY sequence_no DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    sequence_no = int(previous["sequence_no"] if previous else 0) + 1
    previous_hash = previous["event_hash"] if previous else None
    event_id = f"ips_audit_{uuid.uuid4().hex}"
    created_at = _profile_now()
    canonical = {
        "id": event_id,
        "user_id": user_id,
        "version_id": version_id,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "actor_id": actor_id,
        "details": details,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    event_hash = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO investment_profile_audit_events (
            id, user_id, version_id, sequence_no, event_type, actor_id,
            details_json, previous_hash, event_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            user_id,
            version_id,
            sequence_no,
            event_type,
            actor_id,
            canonical_json(details),
            previous_hash,
            event_hash,
            created_at,
        ),
    )
    return {**canonical, "event_hash": event_hash}


def _migrate_legacy_investment_profiles(conn) -> None:
    rows = conn.execute(
        """
        SELECT p.*
        FROM investment_profiles AS p
        LEFT JOIN investment_profile_versions AS v ON v.user_id=p.user_id
        WHERE v.id IS NULL
        """
    ).fetchall()
    for row in rows:
        raw = dict(row)
        markets = _decode_json(raw.get("allowed_fund_markets"), ["mainland"])
        payload = {
            **_PROFILE_DEFAULTS,
            "schema_version": "legacy_investment_profile.v1",
            "questionnaire_version": "legacy_unversioned",
            "risk": raw.get("risk"),
            "horizon": raw.get("horizon"),
            "monthly_budget": raw.get("monthly_budget"),
            "max_single_ratio": raw.get("max_single_ratio"),
            "allowed_fund_markets": markets if isinstance(markets, list) else ["mainland"],
            "accept_fx_risk": bool(raw.get("accept_fx_risk")),
            "legacy_incomplete": True,
        }
        validation = {
            "valid": False,
            "errors": [{
                "field": "questionnaire_version",
                "code": "legacy_reconfirmation_required",
                "message": "旧档案缺少版本化适当性问卷和完整流动性约束，需要重新确认",
            }],
            "warnings": [],
        }
        version_id = f"ips_{uuid.uuid4().hex}"
        created_at = str(raw.get("updated_at") or _profile_now())
        conn.execute(
            """
            INSERT INTO investment_profile_versions (
                id, user_id, version_no, status, payload_json, payload_sha256,
                validation_json, questionnaire_version, consent_version,
                consent_text_sha256, created_at, activated_at, review_due_at
            ) VALUES (?, ?, 1, 'active', ?, ?, ?, 'legacy_unversioned',
                      'legacy-profile-migration.v1', NULL, ?, ?, ?)
            """,
            (
                version_id,
                raw["user_id"],
                canonical_json(payload),
                payload_sha256(payload),
                canonical_json(validation),
                created_at,
                created_at,
                created_at,
            ),
        )
        _append_profile_audit(
            conn,
            raw["user_id"],
            version_id,
            "profile.legacy_migrated",
            {"payload_sha256": payload_sha256(payload), "configured": False},
            "storage-migration",
        )


def get_investment_profile(user_id: str = "default") -> dict:
    """Return only the active, valid, non-expired user-confirmed policy as configured."""
    with _lock:
        row = _get_conn().execute(
            """
            SELECT * FROM investment_profile_versions
            WHERE user_id=? AND status='active'
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return {
            **_PROFILE_DEFAULTS,
            "configured": False,
            "profile_version_id": None,
            "version_no": None,
            "status": None,
            "updated_at": None,
            "activated_at": None,
            "review_due_at": None,
            "review_required": False,
            "integrity_verified": None,
            "validation": {"valid": False, "errors": [], "warnings": []},
        }
    version = _profile_version_from_row(row)
    review_due_at = version.get("review_due_at")
    try:
        review_due = datetime.datetime.fromisoformat(str(review_due_at).replace("Z", "+00:00"))
        if review_due.tzinfo is None:
            review_due = review_due.replace(tzinfo=datetime.timezone.utc)
        review_required = review_due <= datetime.datetime.now(datetime.timezone.utc)
    except (TypeError, ValueError):
        review_required = True
    validation = version.get("validation") or {}
    governance = verify_investment_profile_integrity(user_id)
    configured = bool(
        version.get("integrity_verified")
        and validation.get("valid")
        and not review_required
        and version.get("consent_version") == CONSENT_VERSION
        and version.get("consent_text_sha256") == CONSENT_TEXT_SHA256
        and governance.get("verified")
    )
    return {
        **_PROFILE_DEFAULTS,
        **(version.get("payload") or {}),
        "configured": configured,
        "profile_version_id": version["id"],
        "version_no": version["version_no"],
        "status": version["status"],
        "payload_sha256": version["payload_sha256"],
        "integrity_verified": version["integrity_verified"],
        "validation": validation,
        "activated_at": version.get("activated_at"),
        "updated_at": version.get("activated_at"),
        "review_due_at": review_due_at,
        "review_required": review_required,
        "consent_version": version.get("consent_version"),
        "governance_integrity": governance,
    }


def get_investment_profile_version(version_id: str, user_id: str = "default") -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM investment_profile_versions WHERE id=? AND user_id=?",
            (version_id, user_id),
        ).fetchone()
    version = _profile_version_from_row(row)
    if version is None:
        return None
    validation = version.get("validation") or {}
    governance = verify_investment_profile_integrity(user_id)
    return {
        **_PROFILE_DEFAULTS,
        **(version.get("payload") or {}),
        "configured": bool(
            version.get("integrity_verified")
            and validation.get("valid")
            and version.get("activated_at")
            and version.get("consent_version") == CONSENT_VERSION
            and version.get("consent_text_sha256") == CONSENT_TEXT_SHA256
            and governance.get("verified")
        ),
        "profile_version_id": version["id"],
        "version_no": version["version_no"],
        "status": version["status"],
        "payload_sha256": version["payload_sha256"],
        "integrity_verified": version["integrity_verified"],
        "validation": validation,
        "activated_at": version.get("activated_at"),
        "updated_at": version.get("activated_at"),
        "review_due_at": version.get("review_due_at"),
        "consent_version": version.get("consent_version"),
        "governance_integrity": governance,
    }


def create_investment_profile_draft(profile: dict, validation: dict,
                                    user_id: str = "default", actor_id: str = "default") -> dict:
    normalized = validation.get("normalized") or profile
    digest = payload_sha256(normalized)
    if digest != validation.get("payload_sha256"):
        raise ValueError("投资政策草稿哈希与校验结果不一致")
    now = _profile_now()
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT * FROM investment_profile_versions
                WHERE user_id=? AND status='draft' AND payload_sha256=?
                ORDER BY version_no DESC LIMIT 1
                """,
                (user_id, digest),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return {**_profile_version_from_row(existing), "created": False}
            next_version = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS value FROM investment_profile_versions WHERE user_id=?",
                (user_id,),
            ).fetchone()["value"]
            version_id = f"ips_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO investment_profile_versions (
                    id, user_id, version_no, status, payload_json, payload_sha256,
                    validation_json, questionnaire_version, created_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    user_id,
                    next_version,
                    canonical_json(normalized),
                    digest,
                    canonical_json({
                        "valid": bool(validation.get("valid")),
                        "errors": validation.get("errors") or [],
                        "warnings": validation.get("warnings") or [],
                    }),
                    str(validation.get("questionnaire_version") or ""),
                    now,
                ),
            )
            _append_profile_audit(
                conn,
                user_id,
                version_id,
                "profile.draft_created",
                {
                    "version_no": next_version,
                    "payload_sha256": digest,
                    "valid": bool(validation.get("valid")),
                },
                actor_id,
            )
            row = conn.execute(
                "SELECT * FROM investment_profile_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {**_profile_version_from_row(row), "created": True}


def activate_investment_profile_version(
    version_id: str,
    *,
    expected_payload_sha256: str,
    expected_active_version_id: str | None,
    consent_version: str,
    consent_text_sha256: str,
    review_cycle_months: int,
    user_id: str = "default",
    actor_id: str = "default",
) -> dict:
    if consent_version != CONSENT_VERSION or consent_text_sha256 != CONSENT_TEXT_SHA256:
        raise InvestmentProfileConflictError("确认条款版本或哈希不匹配")
    if int(review_cycle_months) not in {6, 12}:
        raise InvestmentProfileConflictError("复核周期只能是 6 或 12 个月")
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now = now_dt.isoformat(timespec="milliseconds")
    review_due_at = (
        now_dt + datetime.timedelta(days=30 * int(review_cycle_months))
    ).isoformat(timespec="milliseconds")
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            target = conn.execute(
                "SELECT * FROM investment_profile_versions WHERE id=? AND user_id=?",
                (version_id, user_id),
            ).fetchone()
            if target is None:
                raise KeyError(f"投资政策版本不存在:{version_id}")
            current = conn.execute(
                "SELECT * FROM investment_profile_versions WHERE user_id=? AND status='active'",
                (user_id,),
            ).fetchone()
            current_id = current["id"] if current else None
            if target["status"] == "active" and target["payload_sha256"] == expected_payload_sha256:
                conn.commit()
                return {**_profile_version_from_row(target), "activated": False}
            if target["status"] != "draft":
                raise InvestmentProfileConflictError("只有草稿版本可以激活")
            if target["payload_sha256"] != expected_payload_sha256:
                raise InvestmentProfileConflictError("草稿载荷哈希已变化，拒绝激活")
            if current_id != expected_active_version_id:
                raise InvestmentProfileConflictError(
                    f"生效版本已变化，预期 {expected_active_version_id or '-'}，当前 {current_id or '-'}"
                )
            validation = _decode_json(target["validation_json"], {})
            if not validation.get("valid"):
                raise InvestmentProfileConflictError("草稿未通过适当性校验，不能激活")
            payload = _decode_json(target["payload_json"], {})
            if int(payload.get("review_cycle_months") or 0) != int(review_cycle_months):
                raise InvestmentProfileConflictError("复核周期与草稿载荷不一致")

            if current is not None:
                conn.execute(
                    """
                    UPDATE investment_profile_versions
                    SET status='superseded', superseded_at=?
                    WHERE id=? AND status='active'
                    """,
                    (now, current_id),
                )
                _append_profile_audit(
                    conn,
                    user_id,
                    current_id,
                    "profile.version_superseded",
                    {"superseded_by": version_id},
                    actor_id,
                )
            conn.execute(
                """
                UPDATE investment_profile_versions
                SET status='active', consent_version=?, consent_text_sha256=?,
                    activated_at=?, review_due_at=?
                WHERE id=? AND status='draft'
                """,
                (consent_version, consent_text_sha256, now, review_due_at, version_id),
            )
            conn.execute(
                """
                INSERT INTO investment_profiles (
                    user_id, risk, horizon, monthly_budget, max_single_ratio,
                    allowed_fund_markets, accept_fx_risk, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    risk=excluded.risk,
                    horizon=excluded.horizon,
                    monthly_budget=excluded.monthly_budget,
                    max_single_ratio=excluded.max_single_ratio,
                    allowed_fund_markets=excluded.allowed_fund_markets,
                    accept_fx_risk=excluded.accept_fx_risk,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    payload["risk"],
                    payload["horizon"],
                    payload["monthly_budget"],
                    payload["max_single_ratio"],
                    json.dumps(payload["allowed_fund_markets"], ensure_ascii=True),
                    1 if payload.get("accept_fx_risk") else 0,
                    now,
                ),
            )
            _append_profile_audit(
                conn,
                user_id,
                version_id,
                "profile.version_activated",
                {
                    "payload_sha256": expected_payload_sha256,
                    "consent_version": consent_version,
                    "consent_text_sha256": consent_text_sha256,
                    "acknowledged": True,
                    "review_due_at": review_due_at,
                    "superseded_version_id": current_id,
                },
                actor_id,
            )
            activated = conn.execute(
                "SELECT * FROM investment_profile_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {**_profile_version_from_row(activated), "activated": True}


def save_investment_profile(profile: dict, user_id: str = "default") -> dict:
    """Compatibility path: create a draft only; activation always requires explicit consent."""
    from investment_policy import validate_investment_policy

    validation = validate_investment_policy(profile)
    draft = create_investment_profile_draft(profile, validation, user_id=user_id)
    return {
        **draft,
        **(draft.get("payload") or {}),
        "requires_activation": True,
    }


def list_investment_profile_versions(user_id: str = "default", limit: int = 20) -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM investment_profile_versions
            WHERE user_id=?
            ORDER BY version_no DESC
            LIMIT ?
            """,
            (user_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [_profile_version_from_row(row) for row in rows]


def list_investment_profile_audit(user_id: str = "default") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM investment_profile_audit_events
            WHERE user_id=? ORDER BY sequence_no
            """,
            (user_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = _decode_json(item.pop("details_json", None), {})
        items.append(item)
    return items


def verify_investment_profile_audit(user_id: str = "default") -> dict:
    items = list_investment_profile_audit(user_id)
    previous_hash = None
    for expected_sequence, item in enumerate(items, start=1):
        canonical = {
            "id": item["id"],
            "user_id": item["user_id"],
            "version_id": item["version_id"],
            "sequence_no": item["sequence_no"],
            "event_type": item["event_type"],
            "actor_id": item["actor_id"],
            "details": item["details"],
            "previous_hash": item["previous_hash"],
            "created_at": item["created_at"],
        }
        calculated = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
        if (
            int(item["sequence_no"]) != expected_sequence
            or item["previous_hash"] != previous_hash
            or item["event_hash"] != calculated
        ):
            return {
                "verified": False,
                "event_count": len(items),
                "failing_sequence": item["sequence_no"],
                "chain_head": previous_hash,
            }
        previous_hash = item["event_hash"]
    return {
        "verified": True,
        "event_count": len(items),
        "failing_sequence": None,
        "chain_head": previous_hash,
    }


def verify_investment_profile_integrity(user_id: str = "default") -> dict:
    audit = verify_investment_profile_audit(user_id)
    with _lock:
        rows = _get_conn().execute(
            "SELECT * FROM investment_profile_versions WHERE user_id=? ORDER BY version_no DESC",
            (user_id,),
        ).fetchall()
    versions = [_profile_version_from_row(row) for row in rows]
    events = list_investment_profile_audit(user_id)
    created_ids = {
        item.get("version_id")
        for item in events
        if item.get("event_type") in {"profile.draft_created", "profile.legacy_migrated"}
    }
    activated_ids = {
        item.get("version_id")
        for item in events
        if item.get("event_type") in {"profile.version_activated", "profile.legacy_migrated"}
    }
    active_versions = [item for item in versions if item.get("status") == "active"]
    failing_version_id = None
    reason = None
    for version in versions:
        version_id = version["id"]
        if not version.get("integrity_verified"):
            failing_version_id, reason = version_id, "payload_hash_invalid"
            break
        if version_id not in created_ids:
            failing_version_id, reason = version_id, "missing_creation_audit"
            break
        if version.get("status") in {"active", "superseded"} and version_id not in activated_ids:
            failing_version_id, reason = version_id, "missing_activation_audit"
            break
    if len(active_versions) > 1 and reason is None:
        failing_version_id, reason = active_versions[1]["id"], "multiple_active_versions"
    verified = bool(audit["verified"] and reason is None)
    return {
        "verified": verified,
        "version_count": len(versions),
        "active_version_id": active_versions[0]["id"] if len(active_versions) == 1 else None,
        "audit_event_count": audit["event_count"],
        "audit_chain_head": audit["chain_head"],
        "failing_version_id": failing_version_id,
        "reason": reason if audit["verified"] else "audit_chain_invalid",
    }


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


def _exposure_payload_sha256(payload: dict) -> tuple[str, str]:
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def save_portfolio_exposure_snapshot(payload: dict, user_id: str = "default") -> dict:
    """Persist an immutable, content-addressed exposure observation."""
    if not isinstance(payload, dict):
        raise TypeError("portfolio exposure payload must be an object")
    schema_version = str(payload.get("schema_version") or "")
    holdings_hash = str(payload.get("holdings_sha256") or "")
    if not schema_version or len(holdings_hash) != 64:
        raise ValueError("portfolio exposure payload metadata is incomplete")
    payload_json, digest = _exposure_payload_sha256(payload)
    snapshot_id = f"exposure_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        existing = conn.execute(
            """
            SELECT id, user_id, schema_version, holdings_sha256, target_code,
                   profile_version_id, status, payload_sha256, created_at
            FROM portfolio_exposure_snapshots
            WHERE user_id=? AND payload_sha256=?
            """,
            (user_id, digest),
        ).fetchone()
        if existing:
            item = dict(existing)
            item["deduplicated"] = True
            return item
        conn.execute(
            """
            INSERT INTO portfolio_exposure_snapshots (
                id, user_id, schema_version, holdings_sha256, target_code,
                profile_version_id, status, payload_json, payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                user_id,
                schema_version,
                holdings_hash,
                payload.get("target_code"),
                payload.get("profile_version_id"),
                str(payload.get("status") or "partial"),
                payload_json,
                digest,
                created_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, user_id, schema_version, holdings_sha256, target_code,
                   profile_version_id, status, payload_sha256, created_at
            FROM portfolio_exposure_snapshots WHERE id=?
            """,
            (snapshot_id,),
        ).fetchone()
    item = dict(row)
    item["deduplicated"] = False
    return item


def get_portfolio_exposure_snapshot(
    snapshot_id: str,
    user_id: str = "default",
    *,
    include_payload: bool = True,
) -> dict | None:
    fields = """
        id, user_id, schema_version, holdings_sha256, target_code,
        profile_version_id, status, payload_sha256, created_at
    """
    if include_payload:
        fields += ", payload_json"
    with _lock:
        row = _get_conn().execute(
            f"SELECT {fields} FROM portfolio_exposure_snapshots WHERE id=? AND user_id=?",
            (snapshot_id, user_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    if include_payload:
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            item["payload"] = None
    return item


def list_portfolio_exposure_snapshots(
    user_id: str = "default",
    *,
    target_code: str | None = None,
    limit: int = 20,
) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    where = "user_id=?"
    params: list = [user_id]
    if target_code:
        where += " AND target_code=?"
        params.append(str(target_code))
    params.append(limit)
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT id, user_id, schema_version, holdings_sha256, target_code,
                   profile_version_id, status, payload_sha256, created_at
            FROM portfolio_exposure_snapshots
            WHERE {where}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def verify_portfolio_exposure_snapshot(snapshot_id: str, user_id: str = "default") -> dict:
    item = get_portfolio_exposure_snapshot(snapshot_id, user_id=user_id, include_payload=True)
    if not item or not isinstance(item.get("payload"), dict):
        return {"verified": False, "snapshot_id": snapshot_id, "reason": "snapshot_not_found_or_invalid"}
    _, digest = _exposure_payload_sha256(item["payload"])
    reason = None
    if digest != item.get("payload_sha256"):
        reason = "payload_hash_mismatch"
    elif item["payload"].get("holdings_sha256") != item.get("holdings_sha256"):
        reason = "holdings_hash_mismatch"
    elif item["payload"].get("schema_version") != item.get("schema_version"):
        reason = "schema_version_mismatch"
    elif item["payload"].get("target_code") != item.get("target_code"):
        reason = "target_code_mismatch"
    elif item["payload"].get("profile_version_id") != item.get("profile_version_id"):
        reason = "profile_version_mismatch"
    return {
        "verified": reason is None,
        "snapshot_id": snapshot_id,
        "payload_sha256": digest,
        "reason": reason,
    }


# ==================== 持有逻辑版本 ====================

def _holding_thesis_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    payload = _decode_json(item.pop("payload_json", None), None)
    item["payload"] = payload
    item["integrity_verified"] = bool(
        isinstance(payload, dict)
        and payload_sha256(payload) == item.get("payload_sha256")
    )
    return item


def append_holding_thesis_version(payload: dict, user_id: str = "default") -> dict:
    """Append one immutable holding-thesis revision and link it to its predecessor."""
    if not isinstance(payload, dict):
        raise TypeError("holding thesis payload must be an object")
    asset_type = str(payload.get("asset_type") or "").strip()
    market = str(payload.get("market") or "").strip()
    code = str(payload.get("code") or "").strip()
    schema_version = str(payload.get("schema_version") or "").strip()
    state = str(payload.get("state") or "").strip()
    if not asset_type or not code or not schema_version or state not in {"active", "archived"}:
        raise ValueError("holding thesis metadata is incomplete")
    normalized_payload = dict(payload)
    normalized_payload.update({
        "asset_type": asset_type,
        "market": market,
        "code": code,
        "schema_version": schema_version,
        "state": state,
    })
    payload_json = canonical_json(normalized_payload)
    digest = payload_sha256(normalized_payload)
    version_id = f"thesis_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        previous = conn.execute(
            """
            SELECT id, version_no, payload_sha256
            FROM holding_thesis_versions
            WHERE user_id=? AND asset_type=? AND market=? AND code=?
            ORDER BY version_no DESC
            LIMIT 1
            """,
            (user_id, asset_type, market, code),
        ).fetchone()
        version_no = int(previous["version_no"] if previous else 0) + 1
        conn.execute(
            """
            INSERT INTO holding_thesis_versions (
                id, user_id, asset_type, market, code, version_no,
                schema_version, state, payload_json, payload_sha256,
                previous_version_id, previous_payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                user_id,
                asset_type,
                market,
                code,
                version_no,
                schema_version,
                state,
                payload_json,
                digest,
                previous["id"] if previous else None,
                previous["payload_sha256"] if previous else None,
                created_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM holding_thesis_versions WHERE id=? AND user_id=?",
            (version_id, user_id),
        ).fetchone()
    return _holding_thesis_from_row(row)


def get_latest_holding_thesis(
    asset_type: str,
    market: str,
    code: str,
    user_id: str = "default",
) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            """
            SELECT * FROM holding_thesis_versions
            WHERE user_id=? AND asset_type=? AND market=? AND code=?
            ORDER BY version_no DESC
            LIMIT 1
            """,
            (user_id, str(asset_type), str(market), str(code)),
        ).fetchone()
    return _holding_thesis_from_row(row)


def list_latest_holding_theses(user_id: str = "default") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT current.*
            FROM holding_thesis_versions AS current
            WHERE current.user_id=?
              AND current.version_no=(
                  SELECT MAX(candidate.version_no)
                  FROM holding_thesis_versions AS candidate
                  WHERE candidate.user_id=current.user_id
                    AND candidate.asset_type=current.asset_type
                    AND candidate.market=current.market
                    AND candidate.code=current.code
              )
            ORDER BY current.created_at DESC, current.rowid DESC
            """,
            (user_id,),
        ).fetchall()
    return [_holding_thesis_from_row(row) for row in rows]


def list_holding_thesis_versions(
    asset_type: str,
    market: str,
    code: str,
    user_id: str = "default",
    *,
    limit: int = 50,
) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM holding_thesis_versions
            WHERE user_id=? AND asset_type=? AND market=? AND code=?
            ORDER BY version_no DESC
            LIMIT ?
            """,
            (user_id, str(asset_type), str(market), str(code), limit),
        ).fetchall()
    return [_holding_thesis_from_row(row) for row in rows]


def verify_holding_thesis_chain(
    asset_type: str,
    market: str,
    code: str,
    user_id: str = "default",
) -> dict:
    # Verification must cover the whole append-only chain. The paginated API is
    # intentionally capped, but using it here would misclassify revision 101 as
    # a broken first link because its predecessor is outside the page.
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM holding_thesis_versions
            WHERE user_id=? AND asset_type=? AND market=? AND code=?
            ORDER BY version_no ASC
            """,
            (user_id, str(asset_type), str(market), str(code)),
        ).fetchall()
    versions = [_holding_thesis_from_row(row) for row in rows]
    previous = None
    reason = None
    failing_version_id = None
    for expected_no, item in enumerate(versions, start=1):
        payload = item.get("payload")
        identity_matches = bool(
            isinstance(payload, dict)
            and payload.get("asset_type") == item.get("asset_type")
            and payload.get("market") == item.get("market")
            and payload.get("code") == item.get("code")
            and payload.get("schema_version") == item.get("schema_version")
            and payload.get("state") == item.get("state")
        )
        if item.get("version_no") != expected_no:
            reason = "version_sequence_invalid"
        elif not item.get("integrity_verified"):
            reason = "payload_hash_invalid"
        elif not identity_matches:
            reason = "payload_identity_invalid"
        elif previous is None and (
            item.get("previous_version_id") is not None
            or item.get("previous_payload_sha256") is not None
        ):
            reason = "unexpected_previous_version"
        elif previous is not None and (
            item.get("previous_version_id") != previous.get("id")
            or item.get("previous_payload_sha256") != previous.get("payload_sha256")
        ):
            reason = "previous_version_mismatch"
        if reason:
            failing_version_id = item.get("id")
            break
        previous = item
    return {
        "verified": reason is None,
        "asset_type": str(asset_type),
        "market": str(market),
        "code": str(code),
        "version_count": len(versions),
        "chain_head": previous.get("payload_sha256") if previous else None,
        "failing_version_id": failing_version_id,
        "reason": reason,
    }


# ==================== 持仓行动报告 ====================

def save_portfolio_action_report(payload: dict, user_id: str = "default") -> dict:
    """Persist an immutable portfolio decision review for later audit."""
    if not isinstance(payload, dict):
        raise TypeError("portfolio action report payload must be an object")
    schema_version = str(payload.get("schema_version") or "")
    ruleset_version = str(payload.get("ruleset_version") or "")
    holdings_hash = str(payload.get("holdings_sha256") or "")
    theses_hash = str(payload.get("theses_sha256") or "")
    if (
        not schema_version
        or not ruleset_version
        or len(holdings_hash) != 64
        or len(theses_hash) != 64
    ):
        raise ValueError("portfolio action report metadata is incomplete")
    payload_json, digest = _exposure_payload_sha256(payload)
    report_id = f"portfolio_action_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO portfolio_action_reports (
                id, user_id, schema_version, ruleset_version, holdings_sha256, theses_sha256,
                profile_version_id, status, payload_json, payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                user_id,
                schema_version,
                ruleset_version,
                holdings_hash,
                theses_hash,
                payload.get("profile_version_id"),
                str(payload.get("status") or "partial"),
                payload_json,
                digest,
                created_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, user_id, schema_version, ruleset_version, holdings_sha256,
                   theses_sha256, profile_version_id, status, payload_sha256, created_at
            FROM portfolio_action_reports WHERE id=?
            """,
            (report_id,),
        ).fetchone()
    return dict(row)


def get_portfolio_action_report(
    report_id: str,
    user_id: str = "default",
    *,
    include_payload: bool = True,
) -> dict | None:
    fields = """
        id, user_id, schema_version, ruleset_version, holdings_sha256,
        theses_sha256, profile_version_id, status, payload_sha256, created_at
    """
    if include_payload:
        fields += ", payload_json"
    with _lock:
        row = _get_conn().execute(
            f"SELECT {fields} FROM portfolio_action_reports WHERE id=? AND user_id=?",
            (report_id, user_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    if include_payload:
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            item["payload"] = None
    return item


def list_portfolio_action_reports(
    user_id: str = "default",
    *,
    limit: int = 20,
) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT id, user_id, schema_version, ruleset_version, holdings_sha256,
                   theses_sha256, profile_version_id, status, payload_sha256, created_at
            FROM portfolio_action_reports
            WHERE user_id=?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def verify_portfolio_action_report(report_id: str, user_id: str = "default") -> dict:
    item = get_portfolio_action_report(report_id, user_id=user_id, include_payload=True)
    if not item or not isinstance(item.get("payload"), dict):
        return {"verified": False, "report_id": report_id, "reason": "report_not_found_or_invalid"}
    _, digest = _exposure_payload_sha256(item["payload"])
    reason = None
    if digest != item.get("payload_sha256"):
        reason = "payload_hash_mismatch"
    elif item["payload"].get("holdings_sha256") != item.get("holdings_sha256"):
        reason = "holdings_hash_mismatch"
    elif item["payload"].get("theses_sha256") != item.get("theses_sha256"):
        reason = "theses_hash_mismatch"
    elif item["payload"].get("schema_version") != item.get("schema_version"):
        reason = "schema_version_mismatch"
    elif item["payload"].get("ruleset_version") != item.get("ruleset_version"):
        reason = "ruleset_version_mismatch"
    elif item["payload"].get("profile_version_id") != item.get("profile_version_id"):
        reason = "profile_version_mismatch"
    return {
        "verified": reason is None,
        "report_id": report_id,
        "payload_sha256": digest,
        "reason": reason,
    }


# ==================== 投资任务收件箱 ====================

_DECISION_TASK_STATUSES = {"open", "snoozed", "acknowledged", "resolved"}
_DECISION_TASK_PRIORITIES = {"high", "medium", "normal"}
_DECISION_TASK_SNOOZE_LIMITS = {"high": 24, "medium": 72, "normal": 168}


class DecisionTaskConflictError(RuntimeError):
    pass


class DecisionTaskValidationError(ValueError):
    pass


def _decision_task_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _decision_task_payload(action: dict) -> dict:
    action_key = str(action.get("id") or "").strip()
    priority = str(action.get("priority") or "normal").strip()
    if not action_key:
        raise DecisionTaskValidationError("投资任务缺少稳定 action id")
    if priority not in _DECISION_TASK_PRIORITIES:
        raise DecisionTaskValidationError(f"投资任务优先级无效:{priority}")
    evidence = action.get("evidence") or []
    if not isinstance(evidence, list):
        raise DecisionTaskValidationError("投资任务 Evidence 必须是数组")
    return {
        "action_key": action_key,
        "priority": priority,
        "category": str(action.get("category") or "待复盘").strip()[:80],
        "title": str(action.get("title") or action_key).strip()[:240],
        "detail": str(action.get("detail") or "").strip()[:2000],
        "evidence": [str(item).strip()[:500] for item in evidence if str(item).strip()][:20],
        "target": str(action.get("target") or "portfolio").strip()[:80],
        "action_label": str(action.get("action_label") or "查看详情").strip()[:80],
        "source": str(action.get("source") or "未标注来源").strip()[:500],
    }


def _decision_task_fingerprint(payload: dict) -> str:
    # 数值证据会随行情刷新，但同一个风险条件不应每天重新打开。只有任务语义、
    # 优先级或去向变化时才产生新 revision；条件消失/重现由同步状态机处理。
    identity = {
        "action_key": payload["action_key"],
        "priority": payload["priority"],
        "category": payload["category"],
        "title": payload["title"],
        "target": payload["target"],
        "source": payload["source"],
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _decision_task_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["evidence"] = _decode_json(item.pop("evidence_json", None), [])
    return item


def _append_decision_task_event(
    conn,
    *,
    task_id: str,
    user_id: str,
    event_type: str,
    actor_id: str,
    details: dict,
    created_at: str,
) -> dict:
    previous = conn.execute(
        """
        SELECT sequence_no, event_hash
        FROM decision_task_events
        WHERE task_id=? AND user_id=?
        ORDER BY sequence_no DESC
        LIMIT 1
        """,
        (task_id, user_id),
    ).fetchone()
    sequence_no = int(previous["sequence_no"] if previous else 0) + 1
    previous_hash = previous["event_hash"] if previous else None
    event_id = f"decision_task_event_{uuid.uuid4().hex}"
    canonical = {
        "id": event_id,
        "task_id": task_id,
        "user_id": user_id,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "actor_id": actor_id,
        "details": details,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    event_hash = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO decision_task_events (
            id, task_id, user_id, sequence_no, event_type, actor_id,
            details_json, previous_hash, event_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            task_id,
            user_id,
            sequence_no,
            event_type,
            actor_id,
            canonical_json(details),
            previous_hash,
            event_hash,
            created_at,
        ),
    )
    return {**canonical, "event_hash": event_hash}


def _decision_task_summary(conn, user_id: str) -> dict:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS value
        FROM decision_tasks
        WHERE user_id=?
        GROUP BY status
        """,
        (user_id,),
    ).fetchall()
    counts = {status: 0 for status in _DECISION_TASK_STATUSES}
    counts.update({str(row["status"]): int(row["value"]) for row in rows})
    return {
        "open_count": counts["open"],
        "snoozed_count": counts["snoozed"],
        "acknowledged_count": counts["acknowledged"],
        "resolved_count": counts["resolved"],
        "active_count": counts["open"] + counts["snoozed"] + counts["acknowledged"],
    }


def _open_elapsed_snoozes(conn, user_id: str, now: str, actor_id: str) -> None:
    rows = conn.execute(
        """
        SELECT * FROM decision_tasks
        WHERE user_id=? AND status='snoozed' AND snoozed_until IS NOT NULL AND snoozed_until<=?
        """,
        (user_id, now),
    ).fetchall()
    for row in rows:
        revision = int(row["revision"]) + 1
        conn.execute(
            """
            UPDATE decision_tasks
            SET status='open', revision=?, snoozed_until=NULL
            WHERE id=? AND user_id=?
            """,
            (revision, row["id"], user_id),
        )
        _append_decision_task_event(
            conn,
            task_id=row["id"],
            user_id=user_id,
            event_type="task.snooze_elapsed",
            actor_id=actor_id,
            details={"from_status": "snoozed", "to_status": "open", "revision": revision},
            created_at=now,
        )


def sync_decision_tasks(
    actions: list[dict],
    user_id: str = "default",
    *,
    actor_id: str = "decision-engine",
    observed_at: str | None = None,
    resolve_absent: bool = True,
) -> dict:
    """Synchronize deterministic decision conditions into a durable user inbox."""
    now = observed_at or _decision_task_now()
    normalized = []
    seen_keys = set()
    for action in actions:
        if str(action.get("id") or "") == "no-high-priority-item":
            continue
        payload = _decision_task_payload(action)
        if payload["action_key"] in seen_keys:
            raise DecisionTaskValidationError(f"投资任务 action id 重复:{payload['action_key']}")
        payload["fingerprint"] = _decision_task_fingerprint(payload)
        seen_keys.add(payload["action_key"])
        normalized.append(payload)

    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            _open_elapsed_snoozes(conn, user_id, now, actor_id)
            rows = conn.execute(
                "SELECT * FROM decision_tasks WHERE user_id=?",
                (user_id,),
            ).fetchall()
            existing = {str(row["action_key"]): row for row in rows}

            for payload in normalized:
                row = existing.get(payload["action_key"])
                if row is None:
                    task_id = f"decision_task_{uuid.uuid4().hex}"
                    conn.execute(
                        """
                        INSERT INTO decision_tasks (
                            id, user_id, action_key, fingerprint, revision, status, priority,
                            category, title, detail, evidence_json, target, action_label, source,
                            first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, 1, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            user_id,
                            payload["action_key"],
                            payload["fingerprint"],
                            payload["priority"],
                            payload["category"],
                            payload["title"],
                            payload["detail"],
                            canonical_json(payload["evidence"]),
                            payload["target"],
                            payload["action_label"],
                            payload["source"],
                            now,
                            now,
                        ),
                    )
                    _append_decision_task_event(
                        conn,
                        task_id=task_id,
                        user_id=user_id,
                        event_type="task.created",
                        actor_id=actor_id,
                        details={
                            "action_key": payload["action_key"],
                            "fingerprint": payload["fingerprint"],
                            "priority": payload["priority"],
                            "revision": 1,
                        },
                        created_at=now,
                    )
                    continue

                fingerprint_changed = row["fingerprint"] != payload["fingerprint"]
                reopened = row["status"] == "resolved"
                revision = int(row["revision"]) + (1 if fingerprint_changed or reopened else 0)
                status = "open" if fingerprint_changed or reopened else row["status"]
                conn.execute(
                    """
                    UPDATE decision_tasks
                    SET fingerprint=?, revision=?, status=?, priority=?, category=?, title=?,
                        detail=?, evidence_json=?, target=?, action_label=?, source=?, last_seen_at=?,
                        acknowledged_at=?, snoozed_until=?, resolved_at=?
                    WHERE id=? AND user_id=?
                    """,
                    (
                        payload["fingerprint"],
                        revision,
                        status,
                        payload["priority"],
                        payload["category"],
                        payload["title"],
                        payload["detail"],
                        canonical_json(payload["evidence"]),
                        payload["target"],
                        payload["action_label"],
                        payload["source"],
                        now,
                        None if fingerprint_changed or reopened else row["acknowledged_at"],
                        None if fingerprint_changed or reopened else row["snoozed_until"],
                        None if reopened else row["resolved_at"],
                        row["id"],
                        user_id,
                    ),
                )
                if fingerprint_changed or reopened:
                    _append_decision_task_event(
                        conn,
                        task_id=row["id"],
                        user_id=user_id,
                        event_type="task.reopened" if reopened else "task.changed",
                        actor_id=actor_id,
                        details={
                            "from_status": row["status"],
                            "to_status": "open",
                            "fingerprint_changed": fingerprint_changed,
                            "previous_fingerprint": row["fingerprint"],
                            "fingerprint": payload["fingerprint"],
                            "priority": payload["priority"],
                            "revision": revision,
                        },
                        created_at=now,
                    )

            if resolve_absent:
                for action_key, row in existing.items():
                    if action_key in seen_keys or row["status"] == "resolved":
                        continue
                    revision = int(row["revision"]) + 1
                    conn.execute(
                        """
                        UPDATE decision_tasks
                        SET status='resolved', revision=?, snoozed_until=NULL, resolved_at=?
                        WHERE id=? AND user_id=?
                        """,
                        (revision, now, row["id"], user_id),
                    )
                    _append_decision_task_event(
                        conn,
                        task_id=row["id"],
                        user_id=user_id,
                        event_type="task.auto_resolved",
                        actor_id=actor_id,
                        details={
                            "from_status": row["status"],
                            "to_status": "resolved",
                            "reason": "trigger_condition_absent",
                            "revision": revision,
                        },
                        created_at=now,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        rows = conn.execute(
            "SELECT * FROM decision_tasks WHERE user_id=? AND action_key IN ({})".format(
                ",".join("?" for _ in seen_keys) or "''"
            ),
            (user_id, *seen_keys),
        ).fetchall()
        by_key = {str(row["action_key"]): _decision_task_from_row(row) for row in rows}
        items = [by_key[item["action_key"]] for item in normalized if item["action_key"] in by_key]
        summary = _decision_task_summary(conn, user_id)
    return {
        "status": "available",
        "generated_at": now,
        "items": items,
        "summary": summary,
        "resolution_deferred": not resolve_absent,
    }


def list_decision_tasks(
    user_id: str = "default",
    *,
    status: str | None = None,
    include_resolved: bool = False,
    limit: int = 50,
) -> dict:
    if status is not None and status not in _DECISION_TASK_STATUSES:
        raise DecisionTaskValidationError(f"投资任务状态无效:{status}")
    limit = max(1, min(200, int(limit)))
    now = _decision_task_now()
    clauses = ["user_id=?"]
    params: list = [user_id]
    if status:
        clauses.append("status=?")
        params.append(status)
    elif not include_resolved:
        clauses.append("status!='resolved'")
    params.append(limit)
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            _open_elapsed_snoozes(conn, user_id, now, "decision-task-scheduler")
            rows = conn.execute(
                f"""
                SELECT * FROM decision_tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE status WHEN 'open' THEN 0 WHEN 'snoozed' THEN 1
                                WHEN 'acknowledged' THEN 2 ELSE 3 END,
                    CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    last_seen_at DESC, id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            summary = _decision_task_summary(conn, user_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "items": [_decision_task_from_row(row) for row in rows],
        "count": len(rows),
        "summary": summary,
        "generated_at": now,
    }


def update_decision_task(
    task_id: str,
    next_status: str,
    expected_revision: int,
    user_id: str = "default",
    *,
    actor_id: str = "user",
    snooze_hours: int | None = None,
) -> dict | None:
    if next_status not in {"open", "snoozed", "acknowledged"}:
        raise DecisionTaskValidationError("用户只能重新打开、确认已知晓或稍后处理任务")
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now = now_dt.isoformat(timespec="milliseconds")
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            _open_elapsed_snoozes(conn, user_id, now, "decision-task-scheduler")
            row = conn.execute(
                "SELECT * FROM decision_tasks WHERE id=? AND user_id=?",
                (task_id, user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            if int(row["revision"]) != int(expected_revision):
                raise DecisionTaskConflictError("投资任务已被刷新，请重新加载后再操作")
            if row["status"] == "resolved":
                raise DecisionTaskConflictError("该触发条件已经由真实数据自动解决，不能手工重新打开")

            snoozed_until = row["snoozed_until"]
            acknowledged_at = row["acknowledged_at"]
            if next_status == "snoozed":
                hours = int(snooze_hours or 0)
                maximum = _DECISION_TASK_SNOOZE_LIMITS[str(row["priority"])]
                if hours < 1 or hours > maximum:
                    raise DecisionTaskValidationError(
                        f"{row['priority']} 优先级任务最多可稍后 {maximum} 小时"
                    )
                snoozed_until = (now_dt + datetime.timedelta(hours=hours)).isoformat(timespec="milliseconds")
            elif next_status == "acknowledged":
                snoozed_until = None
                acknowledged_at = acknowledged_at or now
            else:
                snoozed_until = None
                acknowledged_at = None

            unchanged = (
                row["status"] == next_status
                and row["snoozed_until"] == snoozed_until
                and row["acknowledged_at"] == acknowledged_at
            )
            if not unchanged:
                revision = int(row["revision"]) + 1
                conn.execute(
                    """
                    UPDATE decision_tasks
                    SET status=?, revision=?, acknowledged_at=?, snoozed_until=?
                    WHERE id=? AND user_id=?
                    """,
                    (next_status, revision, acknowledged_at, snoozed_until, task_id, user_id),
                )
                _append_decision_task_event(
                    conn,
                    task_id=task_id,
                    user_id=user_id,
                    event_type={
                        "open": "task.user_reopened",
                        "snoozed": "task.snoozed",
                        "acknowledged": "task.acknowledged",
                    }[next_status],
                    actor_id=actor_id,
                    details={
                        "from_status": row["status"],
                        "to_status": next_status,
                        "snoozed_until": snoozed_until,
                        "revision": revision,
                    },
                    created_at=now,
                )
            result = conn.execute(
                "SELECT * FROM decision_tasks WHERE id=? AND user_id=?",
                (task_id, user_id),
            ).fetchone()
            summary = _decision_task_summary(conn, user_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"task": _decision_task_from_row(result), "summary": summary}


def list_decision_task_events(task_id: str, user_id: str = "default") -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM decision_task_events
            WHERE task_id=? AND user_id=?
            ORDER BY sequence_no
            """,
            (task_id, user_id),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = _decode_json(item.pop("details_json", None), {})
        items.append(item)
    return items


def verify_decision_task_audit(task_id: str, user_id: str = "default") -> dict:
    with _lock:
        task_exists = _get_conn().execute(
            "SELECT 1 FROM decision_tasks WHERE id=? AND user_id=?",
            (task_id, user_id),
        ).fetchone()
    if not task_exists:
        return {"verified": False, "task_id": task_id, "reason": "task_not_found"}
    items = list_decision_task_events(task_id, user_id=user_id)
    previous_hash = None
    for expected_sequence, item in enumerate(items, start=1):
        canonical = {
            "id": item["id"],
            "task_id": item["task_id"],
            "user_id": item["user_id"],
            "sequence_no": item["sequence_no"],
            "event_type": item["event_type"],
            "actor_id": item["actor_id"],
            "details": item["details"],
            "previous_hash": item["previous_hash"],
            "created_at": item["created_at"],
        }
        calculated = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
        if (
            int(item["sequence_no"]) != expected_sequence
            or item["previous_hash"] != previous_hash
            or item["event_hash"] != calculated
        ):
            return {
                "verified": False,
                "task_id": task_id,
                "event_count": len(items),
                "failing_sequence": item["sequence_no"],
                "chain_head": previous_hash,
                "reason": "audit_chain_invalid",
            }
        previous_hash = item["event_hash"]
    return {
        "verified": bool(items),
        "task_id": task_id,
        "event_count": len(items),
        "failing_sequence": None,
        "chain_head": previous_hash,
        "reason": None if items else "audit_events_missing",
    }


def get_decision_task_summary(user_id: str = "default") -> dict:
    """Return only inbox counters so navigation polling never triggers market I/O."""
    now = _decision_task_now()
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            _open_elapsed_snoozes(conn, user_id, now, "decision-task-scheduler")
            summary = _decision_task_summary(conn, user_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {**summary, "generated_at": now}


# ==================== Scheduled decision checks ====================

DECISION_CHECK_INTERVAL_HOURS = (24, 72, 168)
_DECISION_CHECK_RESULTS = {"succeeded", "partial"}


class DecisionCheckConflictError(RuntimeError):
    pass


class DecisionCheckValidationError(ValueError):
    pass


class DecisionCheckLeaseError(RuntimeError):
    pass


def _decision_check_datetime(value=None) -> datetime.datetime:
    if value is None:
        parsed = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(value, datetime.datetime):
        parsed = value
    else:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _decision_check_iso(value=None) -> str:
    return _decision_check_datetime(value).isoformat(timespec="milliseconds")


def _decision_check_from_row(row, *, now=None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    now_text = _decision_check_iso(now)
    item["enabled"] = item["status"] == "active"
    item["running"] = bool(
        item.get("lease_owner")
        and item.get("lease_expires_at")
        and item["lease_expires_at"] > now_text
    )
    return item


def _append_decision_check_event(
    conn,
    *,
    schedule_id: str,
    user_id: str,
    event_type: str,
    actor_id: str,
    details: dict,
    created_at: str,
) -> dict:
    previous = conn.execute(
        """
        SELECT sequence_no, event_hash
        FROM decision_check_events
        WHERE user_id=?
        ORDER BY sequence_no DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    sequence_no = int(previous["sequence_no"] if previous else 0) + 1
    previous_hash = previous["event_hash"] if previous else None
    event_id = f"decision_check_event_{uuid.uuid4().hex}"
    canonical = {
        "id": event_id,
        "schedule_id": schedule_id,
        "user_id": user_id,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "actor_id": actor_id,
        "details": details,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    event_hash = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO decision_check_events (
            id, schedule_id, user_id, sequence_no, event_type, actor_id,
            details_json, previous_hash, event_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            schedule_id,
            user_id,
            sequence_no,
            event_type,
            actor_id,
            canonical_json(details),
            previous_hash,
            event_hash,
            created_at,
        ),
    )
    return {**canonical, "event_hash": event_hash}


def get_decision_check_schedule(user_id: str = "default", *, now=None) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM decision_check_schedules WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return _decision_check_from_row(row, now=now)


def configure_decision_check_schedule(
    user_id: str,
    *,
    enabled: bool,
    interval_hours: int,
    run_immediately: bool = False,
    expected_revision: int | None = None,
    actor_id: str = "user",
    now=None,
) -> tuple[dict, bool]:
    interval = int(interval_hours)
    if interval not in DECISION_CHECK_INTERVAL_HOURS:
        raise DecisionCheckValidationError("自动检查间隔只支持 24、72 或 168 小时")
    if run_immediately and not enabled:
        raise DecisionCheckValidationError("停用自动检查时不能提交立即运行")
    now_dt = _decision_check_datetime(now)
    now_text = _decision_check_iso(now_dt)
    desired_status = "active" if enabled else "paused"

    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if expected_revision is not None:
                if existing is None or int(existing["revision"]) != int(expected_revision):
                    raise DecisionCheckConflictError("自动检查设置已更新，请刷新后重试")

            changed = bool(
                existing is None
                or existing["status"] != desired_status
                or int(existing["interval_hours"]) != interval
                or (enabled and run_immediately)
            )
            if existing is None:
                schedule_id = f"decision_check_{uuid.uuid4().hex}"
                next_run_at = (
                    now_text
                    if enabled and run_immediately
                    else _decision_check_iso(now_dt + datetime.timedelta(hours=interval))
                    if enabled
                    else None
                )
                conn.execute(
                    """
                    INSERT INTO decision_check_schedules (
                        id, user_id, status, interval_hours, revision,
                        next_run_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        schedule_id,
                        user_id,
                        desired_status,
                        interval,
                        next_run_at,
                        now_text,
                        now_text,
                    ),
                )
                event_type = "decision_check.schedule.created"
            else:
                schedule_id = str(existing["id"])
                next_run_at = None
                if enabled:
                    if run_immediately:
                        next_run_at = now_text
                    elif existing["status"] == "active" and int(existing["interval_hours"]) == interval:
                        next_run_at = existing["next_run_at"]
                    else:
                        next_run_at = _decision_check_iso(
                            now_dt + datetime.timedelta(hours=interval)
                        )
                if changed:
                    resumed = existing["status"] == "paused" and enabled
                    conn.execute(
                        """
                        UPDATE decision_check_schedules
                        SET status=?, interval_hours=?, revision=revision+1,
                            next_run_at=?, consecutive_failures=?,
                            last_error_code=?, last_error_message=?,
                            lease_owner=?, lease_expires_at=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            desired_status,
                            interval,
                            next_run_at,
                            0 if resumed else int(existing["consecutive_failures"]),
                            None if resumed else existing["last_error_code"],
                            None if resumed else existing["last_error_message"],
                            None if not enabled else existing["lease_owner"],
                            None if not enabled else existing["lease_expires_at"],
                            now_text,
                            schedule_id,
                        ),
                    )
                if run_immediately:
                    event_type = "decision_check.schedule.queued"
                elif existing["status"] != desired_status:
                    event_type = (
                        "decision_check.schedule.enabled"
                        if enabled
                        else "decision_check.schedule.disabled"
                    )
                else:
                    event_type = "decision_check.schedule.configured"

            if changed:
                _append_decision_check_event(
                    conn,
                    schedule_id=schedule_id,
                    user_id=user_id,
                    event_type=event_type,
                    actor_id=actor_id,
                    details={
                        "status": desired_status,
                        "interval_hours": interval,
                        "next_run_at": next_run_at,
                        "run_immediately": bool(run_immediately),
                    },
                    created_at=now_text,
                )
            row = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _decision_check_from_row(row, now=now_dt), changed


def claim_due_decision_check(
    worker_id: str,
    *,
    lease_seconds: int = 120,
    now=None,
) -> dict | None:
    now_dt = _decision_check_datetime(now)
    now_text = _decision_check_iso(now_dt)
    lease_expires_at = _decision_check_iso(
        now_dt + datetime.timedelta(seconds=max(60, int(lease_seconds)))
    )
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT * FROM decision_check_schedules
                WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at<=?
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                ORDER BY next_run_at, id
                LIMIT 1
                """,
                (now_text, now_text),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            changed = conn.execute(
                """
                UPDATE decision_check_schedules
                SET lease_owner=?, lease_expires_at=?, last_started_at=?,
                    attempt_count=attempt_count+1, updated_at=?
                WHERE id=? AND status='active'
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                """,
                (worker_id, lease_expires_at, now_text, now_text, row["id"], now_text),
            ).rowcount
            if changed != 1:
                conn.commit()
                return None
            _append_decision_check_event(
                conn,
                schedule_id=row["id"],
                user_id=row["user_id"],
                event_type="decision_check.started",
                actor_id=worker_id,
                details={"lease_expires_at": lease_expires_at},
                created_at=now_text,
            )
            claimed = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE id=?",
                (row["id"],),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _decision_check_from_row(claimed, now=now_dt)


def complete_decision_check(
    schedule_id: str,
    worker_id: str,
    *,
    result_status: str,
    open_count: int,
    unavailable_count: int,
    now=None,
) -> dict:
    if result_status not in _DECISION_CHECK_RESULTS:
        raise DecisionCheckValidationError(f"自动检查结果状态无效:{result_status}")
    now_dt = _decision_check_datetime(now)
    now_text = _decision_check_iso(now_dt)
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            schedule = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            if schedule is None:
                raise KeyError(f"自动检查计划不存在:{schedule_id}")
            next_run_at = _decision_check_iso(
                now_dt + datetime.timedelta(hours=int(schedule["interval_hours"]))
            )
            changed = conn.execute(
                """
                UPDATE decision_check_schedules
                SET next_run_at=?, lease_owner=NULL, lease_expires_at=NULL,
                    consecutive_failures=0, last_finished_at=?, last_success_at=?,
                    last_result_status=?, last_open_count=?, last_unavailable_count=?,
                    last_error_code=NULL, last_error_message=NULL, updated_at=?
                WHERE id=? AND status='active' AND lease_owner=?
                """,
                (
                    next_run_at,
                    now_text,
                    now_text if result_status == "succeeded" else schedule["last_success_at"],
                    result_status,
                    max(0, int(open_count)),
                    max(0, int(unavailable_count)),
                    now_text,
                    schedule_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise DecisionCheckLeaseError("自动检查租约已失效，拒绝提交完成状态")
            _append_decision_check_event(
                conn,
                schedule_id=schedule_id,
                user_id=schedule["user_id"],
                event_type=f"decision_check.{result_status}",
                actor_id=worker_id,
                details={
                    "open_count": max(0, int(open_count)),
                    "unavailable_count": max(0, int(unavailable_count)),
                    "next_run_at": next_run_at,
                },
                created_at=now_text,
            )
            row = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _decision_check_from_row(row, now=now_dt)


def fail_decision_check(
    schedule_id: str,
    worker_id: str,
    *,
    error_code: str,
    error_message: str,
    retryable: bool = True,
    now=None,
) -> dict:
    retry_delays = (900, 3600, 14400, 43200)
    now_dt = _decision_check_datetime(now)
    now_text = _decision_check_iso(now_dt)
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            schedule = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            if schedule is None:
                raise KeyError(f"自动检查计划不存在:{schedule_id}")
            failure_count = int(schedule["consecutive_failures"]) + 1
            should_retry = bool(retryable and failure_count <= len(retry_delays))
            next_run_at = (
                _decision_check_iso(
                    now_dt + datetime.timedelta(
                        seconds=retry_delays[min(failure_count - 1, len(retry_delays) - 1)]
                    )
                )
                if should_retry
                else None
            )
            next_status = "active" if should_retry else "paused"
            changed = conn.execute(
                """
                UPDATE decision_check_schedules
                SET status=?, revision=revision+?, next_run_at=?,
                    lease_owner=NULL, lease_expires_at=NULL,
                    consecutive_failures=?, last_finished_at=?, last_result_status='failed',
                    last_error_code=?, last_error_message=?, updated_at=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    next_status,
                    0 if should_retry else 1,
                    next_run_at,
                    failure_count,
                    now_text,
                    str(error_code or "DECISION_CHECK_FAILED")[:100],
                    str(error_message or "")[:500],
                    now_text,
                    schedule_id,
                    worker_id,
                ),
            ).rowcount
            if changed != 1:
                raise DecisionCheckLeaseError("自动检查租约已失效，拒绝提交失败状态")
            _append_decision_check_event(
                conn,
                schedule_id=schedule_id,
                user_id=schedule["user_id"],
                event_type="decision_check.failed",
                actor_id=worker_id,
                details={
                    "error_code": str(error_code or "DECISION_CHECK_FAILED")[:100],
                    "retryable": bool(retryable),
                    "retry_exhausted": not should_retry,
                    "consecutive_failures": failure_count,
                    "next_run_at": next_run_at,
                    "status": next_status,
                },
                created_at=now_text,
            )
            row = conn.execute(
                "SELECT * FROM decision_check_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _decision_check_from_row(row, now=now_dt)


def list_decision_check_events(
    user_id: str = "default",
    *,
    limit: int | None = 100,
) -> list[dict]:
    bounded_limit = None if limit is None else max(1, min(500, int(limit)))
    query = """
        SELECT * FROM decision_check_events
        WHERE user_id=?
        ORDER BY sequence_no
    """
    params: tuple = (user_id,)
    if bounded_limit is not None:
        query += " LIMIT ?"
        params = (user_id, bounded_limit)
    with _lock:
        rows = _get_conn().execute(query, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = _decode_json(item.pop("details_json", None), {})
        items.append(item)
    return items


def verify_decision_check_audit(user_id: str = "default") -> dict:
    schedule = get_decision_check_schedule(user_id=user_id)
    if schedule is None:
        return {"verified": False, "event_count": 0, "reason": "schedule_not_found"}
    items = list_decision_check_events(user_id=user_id, limit=None)
    previous_hash = None
    for expected_sequence, item in enumerate(items, start=1):
        canonical = {
            "id": item["id"],
            "schedule_id": item["schedule_id"],
            "user_id": item["user_id"],
            "sequence_no": item["sequence_no"],
            "event_type": item["event_type"],
            "actor_id": item["actor_id"],
            "details": item["details"],
            "previous_hash": item["previous_hash"],
            "created_at": item["created_at"],
        }
        calculated = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
        if (
            int(item["sequence_no"]) != expected_sequence
            or item["previous_hash"] != previous_hash
            or item["event_hash"] != calculated
        ):
            return {
                "verified": False,
                "event_count": len(items),
                "failing_sequence": item["sequence_no"],
                "chain_head": previous_hash,
                "reason": "audit_chain_invalid",
            }
        previous_hash = item["event_hash"]
    return {
        "verified": bool(items),
        "event_count": len(items),
        "failing_sequence": None,
        "chain_head": previous_hash,
        "reason": None if items else "audit_events_missing",
    }
