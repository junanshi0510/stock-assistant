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
import threading
import uuid

from database import (
    configured_database_target,
    connect_database,
    connection_is_closed,
    database_dialect,
    require_database_schema,
)

from investment_policy import (
    CONSENT_TEXT_SHA256,
    CONSENT_VERSION,
    canonical_json,
    payload_sha256,
)

_DB_PATH = configured_database_target(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_assistant.db")
)

# sqlite 默认不允许跨线程共用连接;FastAPI 是多线程的,这里加锁串行化访问,
# 简单可靠(自选股读写量很小,锁完全够用)。
_lock = threading.RLock()
_conn = None


def _get_conn():
    global _conn
    if connection_is_closed(_conn):
        _conn = connect_database(_DB_PATH, close_on_exit=False)
        if database_dialect(_conn) == "postgresql":
            require_database_schema(
                _conn,
                {
                    "storage_schema_migrations",
                    "user_watchlist",
                    "user_alerts",
                    "holdings",
                    "investment_profiles",
                    "portfolio_transactions",
                    "portfolio_snapshots",
                    "decision_tasks",
                    "decision_check_schedules",
                },
            )
            return _conn
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
            CREATE TABLE IF NOT EXISTS fund_switch_cost_reviews (
                id                 TEXT PRIMARY KEY,
                user_id            TEXT NOT NULL DEFAULT 'default',
                holding_id         INTEGER NOT NULL,
                schema_version     TEXT NOT NULL,
                selected_code      TEXT NOT NULL,
                candidate_code     TEXT NOT NULL,
                review_on          TEXT NOT NULL,
                status             TEXT NOT NULL,
                evidence_sha256    TEXT NOT NULL,
                payload_json       TEXT NOT NULL,
                payload_sha256     TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                UNIQUE(user_id, payload_sha256)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_switch_cost_history
            ON fund_switch_cost_reviews(
                user_id, holding_id, selected_code, candidate_code, created_at DESC
            )
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_cost_no_update
            BEFORE UPDATE ON fund_switch_cost_reviews
            BEGIN
                SELECT RAISE(ABORT, 'fund switch cost review is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_cost_no_delete
            BEFORE DELETE ON fund_switch_cost_reviews
            BEGIN
                SELECT RAISE(ABORT, 'fund switch cost review is immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_switch_quote_events (
                id                 TEXT PRIMARY KEY,
                review_id          TEXT NOT NULL,
                user_id            TEXT NOT NULL DEFAULT 'default',
                holding_id         INTEGER NOT NULL,
                selected_code      TEXT NOT NULL,
                candidate_code     TEXT NOT NULL,
                sequence_no        INTEGER NOT NULL,
                schema_version     TEXT NOT NULL,
                actor_id           TEXT NOT NULL,
                quoted_at          TEXT NOT NULL,
                payload_json       TEXT NOT NULL,
                payload_sha256     TEXT NOT NULL,
                previous_hash      TEXT,
                event_hash         TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                UNIQUE(user_id, holding_id, candidate_code, sequence_no),
                FOREIGN KEY(review_id) REFERENCES fund_switch_cost_reviews(id)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_switch_quote_history
            ON fund_switch_quote_events(
                user_id, holding_id, candidate_code, sequence_no DESC
            )
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_quote_no_update
            BEFORE UPDATE ON fund_switch_quote_events
            BEGIN
                SELECT RAISE(ABORT, 'fund switch quote events are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_quote_no_delete
            BEFORE DELETE ON fund_switch_quote_events
            BEGIN
                SELECT RAISE(ABORT, 'fund switch quote events are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_switch_execution_reviews (
                id                 TEXT PRIMARY KEY,
                user_id            TEXT NOT NULL DEFAULT 'default',
                holding_id         INTEGER NOT NULL,
                selected_code      TEXT NOT NULL,
                candidate_code     TEXT NOT NULL,
                quote_event_id     TEXT NOT NULL,
                quote_event_hash   TEXT NOT NULL,
                profile_version_id TEXT NOT NULL,
                thesis_version_id  TEXT NOT NULL,
                sequence_no        INTEGER NOT NULL,
                schema_version     TEXT NOT NULL,
                status             TEXT NOT NULL,
                actor_id           TEXT NOT NULL,
                payload_json       TEXT NOT NULL,
                payload_sha256     TEXT NOT NULL,
                evidence_sha256    TEXT NOT NULL,
                previous_hash      TEXT,
                review_hash        TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                UNIQUE(user_id, holding_id, candidate_code, sequence_no),
                FOREIGN KEY(quote_event_id) REFERENCES fund_switch_quote_events(id)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_switch_execution_history
            ON fund_switch_execution_reviews(
                user_id, holding_id, candidate_code, sequence_no DESC
            )
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_execution_no_update
            BEFORE UPDATE ON fund_switch_execution_reviews
            BEGIN
                SELECT RAISE(ABORT, 'fund switch execution reviews are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_execution_no_delete
            BEFORE DELETE ON fund_switch_execution_reviews
            BEGIN
                SELECT RAISE(ABORT, 'fund switch execution reviews are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_switch_lifecycle_events (
                id                        TEXT PRIMARY KEY,
                case_id                   TEXT NOT NULL,
                user_id                   TEXT NOT NULL DEFAULT 'default',
                holding_id                INTEGER NOT NULL,
                selected_code             TEXT NOT NULL,
                candidate_code            TEXT NOT NULL,
                execution_review_id       TEXT NOT NULL,
                execution_review_hash     TEXT NOT NULL,
                sequence_no               INTEGER NOT NULL,
                event_type                TEXT NOT NULL,
                schema_version            TEXT NOT NULL,
                status                    TEXT NOT NULL,
                actor_id                  TEXT NOT NULL,
                redemption_transaction_id INTEGER,
                purchase_transaction_id   INTEGER,
                payload_json              TEXT NOT NULL,
                payload_sha256            TEXT NOT NULL,
                evidence_sha256           TEXT NOT NULL,
                previous_hash             TEXT,
                event_hash                TEXT NOT NULL,
                created_at                TEXT NOT NULL,
                UNIQUE(user_id, case_id, sequence_no),
                FOREIGN KEY(execution_review_id)
                    REFERENCES fund_switch_execution_reviews(id)
            )
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_switch_lifecycle_case
            ON fund_switch_lifecycle_events(user_id, case_id, sequence_no DESC)
            """
        )
        _conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_switch_lifecycle_candidate
            ON fund_switch_lifecycle_events(
                user_id, holding_id, candidate_code, created_at DESC
            )
            """
        )
        _conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fund_switch_redemption_once
            ON fund_switch_lifecycle_events(user_id, redemption_transaction_id)
            WHERE event_type='redemption_settled'
              AND redemption_transaction_id IS NOT NULL
            """
        )
        _conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fund_switch_execution_review_once
            ON fund_switch_lifecycle_events(user_id, execution_review_id)
            WHERE event_type='redemption_settled'
            """
        )
        _conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fund_switch_purchase_once
            ON fund_switch_lifecycle_events(user_id, purchase_transaction_id)
            WHERE event_type='purchase_recorded'
              AND purchase_transaction_id IS NOT NULL
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_lifecycle_no_update
            BEFORE UPDATE ON fund_switch_lifecycle_events
            BEGIN
                SELECT RAISE(ABORT, 'fund switch lifecycle events are immutable');
            END
            """
        )
        _conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_fund_switch_lifecycle_no_delete
            BEFORE DELETE ON fund_switch_lifecycle_events
            BEGIN
                SELECT RAISE(ABORT, 'fund switch lifecycle events are immutable');
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


def get_portfolio_transaction(
    transaction_id: int,
    user_id: str = "default",
) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            """
            SELECT id, user_id, asset_type, market, code, name, trade_type,
                   trade_date, shares, unit_price, fee, note,
                   COALESCE(source, 'manual') AS source, created_at
            FROM portfolio_transactions
            WHERE id=? AND user_id=?
            """,
            (int(transaction_id), user_id),
        ).fetchone()
    return dict(row) if row else None


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
            RETURNING id
            """,
            values,
        )
        inserted_id = cursor.fetchone()["id"]
        conn.commit()
        row = conn.execute(
            """
            SELECT id, user_id, asset_type, market, code, name, trade_type,
                   trade_date, shares, unit_price, fee, note,
                   COALESCE(source, 'manual') AS source, created_at
            FROM portfolio_transactions
            WHERE id=?
            """,
            (inserted_id,),
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
                    RETURNING id
                    """,
                    value,
                )
                inserted_ids.append(cursor.fetchone()["id"])
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
            RETURNING id
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
        inserted_id = cursor.fetchone()["id"]
        conn.commit()
        row = conn.execute(
            """
            SELECT id, captured_at, reason, holding_count, total_amount,
                   total_profit, total_yesterday_profit
            FROM portfolio_snapshots
            WHERE id=?
            """,
            (inserted_id,),
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
            ORDER BY created_at DESC, id DESC
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


# ==================== 基金替换成本与平台报价 ====================

def _fund_switch_cost_review_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["payload"] = _decode_json(item.pop("payload_json", None), None)
    item["integrity_verified"] = _fund_switch_cost_review_reason(item) is None
    return item


def _fund_switch_cost_review_reason(item: dict) -> str | None:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return "payload_invalid"
    if payload_sha256(payload) != item.get("payload_sha256"):
        return "payload_hash_mismatch"
    evidence_payload = dict(payload)
    embedded_evidence_hash = str(evidence_payload.pop("evidence_sha256", ""))
    if payload_sha256(evidence_payload) != embedded_evidence_hash:
        return "evidence_hash_mismatch"
    binding = payload.get("ledger_binding")
    if not isinstance(binding, dict):
        return "ledger_binding_missing"
    binding_payload = dict(binding)
    binding_schema = str(binding_payload.pop("schema_version", ""))
    binding_hash = str(binding_payload.pop("payload_sha256", ""))
    if (
        binding_schema != "fund_switch_lot_binding.v1"
        or payload_sha256(binding_payload) != binding_hash
    ):
        return "ledger_binding_hash_mismatch"
    expected_schema = (
        f"{payload.get('diagnostic_id')}@{payload.get('diagnostic_version')}"
    )
    checks = (
        (expected_schema, item.get("schema_version"), "schema_version_mismatch"),
        (payload.get("holding_id"), item.get("holding_id"), "holding_id_mismatch"),
        (payload.get("selected_code"), item.get("selected_code"), "selected_code_mismatch"),
        (payload.get("candidate_code"), item.get("candidate_code"), "candidate_code_mismatch"),
        (payload.get("review_on"), item.get("review_on"), "review_date_mismatch"),
        (payload.get("status"), item.get("status"), "status_mismatch"),
        (embedded_evidence_hash, item.get("evidence_sha256"), "evidence_column_mismatch"),
    )
    for embedded, stored, reason in checks:
        if str(embedded or "") != str(stored or ""):
            return reason
    return None


def save_fund_switch_cost_review(
    payload: dict,
    holding_id: int,
    user_id: str = "default",
) -> dict:
    """Persist one immutable, content-addressed disclosed-cost review."""
    if not isinstance(payload, dict):
        raise TypeError("fund switch cost review payload must be an object")
    holding_id = int(holding_id)
    if holding_id <= 0:
        raise ValueError("holding id is invalid")
    diagnostic_id = str(payload.get("diagnostic_id") or "").strip()
    diagnostic_version = str(payload.get("diagnostic_version") or "").strip()
    selected_code = str(payload.get("selected_code") or "").strip()
    candidate_code = str(payload.get("candidate_code") or "").strip()
    review_on = str(payload.get("review_on") or "").strip()
    evidence_sha256 = str(payload.get("evidence_sha256") or "").strip()
    if not diagnostic_id or not diagnostic_version:
        raise ValueError("fund switch cost review schema is incomplete")
    if not (selected_code.isdigit() and len(selected_code) == 6):
        raise ValueError("selected fund code is invalid")
    if not (candidate_code.isdigit() and len(candidate_code) == 6):
        raise ValueError("candidate fund code is invalid")
    try:
        datetime.date.fromisoformat(review_on)
    except ValueError as error:
        raise ValueError("fund switch cost review date is invalid") from error
    evidence_payload = dict(payload)
    evidence_payload.pop("evidence_sha256", None)
    if len(evidence_sha256) != 64 or payload_sha256(evidence_payload) != evidence_sha256:
        raise ValueError("fund switch cost evidence hash is invalid")
    if int(payload.get("holding_id") or 0) != holding_id:
        raise ValueError("fund switch cost review holding id does not match")
    binding = payload.get("ledger_binding")
    if not isinstance(binding, dict):
        raise ValueError("fund switch cost review ledger binding is missing")
    binding_payload = dict(binding)
    binding_schema = str(binding_payload.pop("schema_version", ""))
    binding_hash = str(binding_payload.pop("payload_sha256", ""))
    if (
        binding_schema != "fund_switch_lot_binding.v1"
        or payload_sha256(binding_payload) != binding_hash
    ):
        raise ValueError("fund switch cost review ledger binding is invalid")

    schema_version = f"{diagnostic_id}@{diagnostic_version}"
    payload_json = canonical_json(payload)
    digest = payload_sha256(payload)
    review_id = f"fund_switch_cost_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
    with _lock:
        conn = _get_conn()
        existing = conn.execute(
            """
            SELECT * FROM fund_switch_cost_reviews
            WHERE user_id=? AND payload_sha256=?
            """,
            (user_id, digest),
        ).fetchone()
        if existing:
            item = _fund_switch_cost_review_from_row(existing)
            item["deduplicated"] = True
            return item
        conn.execute(
            """
            INSERT INTO fund_switch_cost_reviews (
                id, user_id, holding_id, schema_version, selected_code,
                candidate_code, review_on, status, evidence_sha256,
                payload_json, payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                user_id,
                holding_id,
                schema_version,
                selected_code,
                candidate_code,
                review_on,
                str(payload.get("status") or "unavailable"),
                evidence_sha256,
                payload_json,
                digest,
                created_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM fund_switch_cost_reviews WHERE id=?",
            (review_id,),
        ).fetchone()
    item = _fund_switch_cost_review_from_row(row)
    item["deduplicated"] = False
    return item


def get_fund_switch_cost_review(
    review_id: str,
    user_id: str = "default",
) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM fund_switch_cost_reviews WHERE id=? AND user_id=?",
            (review_id, user_id),
        ).fetchone()
    return _fund_switch_cost_review_from_row(row)


def verify_fund_switch_cost_review(
    review_id: str,
    user_id: str = "default",
) -> dict:
    item = get_fund_switch_cost_review(review_id, user_id=user_id)
    if item is None:
        return {
            "verified": False,
            "review_id": review_id,
            "reason": "review_not_found",
        }
    reason = _fund_switch_cost_review_reason(item)
    return {
        "verified": reason is None,
        "review_id": review_id,
        "payload_sha256": item.get("payload_sha256"),
        "reason": reason,
    }


def _fund_switch_quote_event_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["payload"] = _decode_json(item.pop("payload_json", None), None)
    payload = item.get("payload")
    item["integrity_verified"] = bool(
        isinstance(payload, dict)
        and payload_sha256(payload) == item.get("payload_sha256")
        and str(payload.get("review_id") or "") == str(item.get("review_id") or "")
        and int(payload.get("holding_id") or 0) == int(item.get("holding_id") or 0)
        and str(payload.get("selected_code") or "") == str(item.get("selected_code") or "")
        and str(payload.get("candidate_code") or "") == str(item.get("candidate_code") or "")
        and str(payload.get("quoted_at") or "") == str(item.get("quoted_at") or "")
        and str(payload.get("schema_version") or "") == str(item.get("schema_version") or "")
    )
    return item


def append_fund_switch_quote_event(
    review_id: str,
    payload: dict,
    *,
    actor_id: str,
    user_id: str = "default",
) -> dict:
    """Append a platform quote without mutating any prior user confirmation."""
    if not isinstance(payload, dict):
        raise TypeError("fund switch quote payload must be an object")
    review = get_fund_switch_cost_review(review_id, user_id=user_id)
    if review is None:
        raise LookupError("fund switch cost review not found")
    if not review.get("integrity_verified"):
        raise ValueError("fund switch cost review integrity failed")
    holding_id = int(review.get("holding_id") or 0)
    selected_code = str(review.get("selected_code") or "")
    candidate_code = str(review.get("candidate_code") or "")
    schema_version = str(payload.get("schema_version") or "").strip()
    quoted_at = str(payload.get("quoted_at") or "").strip()
    if not schema_version or not quoted_at:
        raise ValueError("fund switch quote metadata is incomplete")
    expected = {
        "review_id": review_id,
        "holding_id": holding_id,
        "selected_code": selected_code,
        "candidate_code": candidate_code,
    }
    for key, value in expected.items():
        if str(payload.get(key)) != str(value):
            raise ValueError(f"fund switch quote {key} does not match review")

    payload_json = canonical_json(payload)
    digest = payload_sha256(payload)
    event_id = f"fund_switch_quote_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            previous = conn.execute(
                """
                SELECT sequence_no, event_hash FROM fund_switch_quote_events
                WHERE user_id=? AND holding_id=? AND candidate_code=?
                ORDER BY sequence_no DESC LIMIT 1
                """,
                (user_id, holding_id, candidate_code),
            ).fetchone()
            sequence_no = int(previous["sequence_no"] if previous else 0) + 1
            previous_hash = previous["event_hash"] if previous else None
            canonical = {
                "id": event_id,
                "review_id": review_id,
                "user_id": user_id,
                "holding_id": holding_id,
                "selected_code": selected_code,
                "candidate_code": candidate_code,
                "sequence_no": sequence_no,
                "schema_version": schema_version,
                "actor_id": str(actor_id or "system"),
                "quoted_at": quoted_at,
                "payload": payload,
                "payload_sha256": digest,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(
                canonical_json(canonical).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """
                INSERT INTO fund_switch_quote_events (
                    id, review_id, user_id, holding_id, selected_code,
                    candidate_code, sequence_no, schema_version, actor_id,
                    quoted_at, payload_json, payload_sha256, previous_hash,
                    event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    review_id,
                    user_id,
                    holding_id,
                    selected_code,
                    candidate_code,
                    sequence_no,
                    schema_version,
                    str(actor_id or "system"),
                    quoted_at,
                    payload_json,
                    digest,
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute(
            "SELECT * FROM fund_switch_quote_events WHERE id=?",
            (event_id,),
        ).fetchone()
    return _fund_switch_quote_event_from_row(row)


def list_fund_switch_quote_events(
    *,
    holding_id: int,
    candidate_code: str | None = None,
    user_id: str = "default",
    limit: int = 100,
) -> list[dict]:
    where = ["user_id=?", "holding_id=?"]
    params: list = [user_id, int(holding_id)]
    if candidate_code:
        where.append("candidate_code=?")
        params.append(str(candidate_code))
    params.append(max(1, min(int(limit), 500)))
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT * FROM fund_switch_quote_events
            WHERE {' AND '.join(where)}
            ORDER BY candidate_code, sequence_no DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_fund_switch_quote_event_from_row(row) for row in rows]


def list_latest_fund_switch_quotes(
    user_id: str = "default",
    *,
    holding_id: int | None = None,
) -> list[dict]:
    where = ["q.user_id=?"]
    params: list = [user_id]
    if holding_id is not None:
        where.append("q.holding_id=?")
        params.append(int(holding_id))
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT q.* FROM fund_switch_quote_events q
            WHERE {' AND '.join(where)}
              AND q.sequence_no=(
                  SELECT MAX(q2.sequence_no) FROM fund_switch_quote_events q2
                  WHERE q2.user_id=q.user_id
                    AND q2.holding_id=q.holding_id
                    AND q2.candidate_code=q.candidate_code
              )
            ORDER BY q.created_at DESC, q.id DESC
            """,
            params,
        ).fetchall()
    return [_fund_switch_quote_event_from_row(row) for row in rows]


def verify_fund_switch_quote_audit(
    holding_id: int,
    candidate_code: str,
    user_id: str = "default",
) -> dict:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM fund_switch_quote_events
            WHERE user_id=? AND holding_id=? AND candidate_code=?
            ORDER BY sequence_no
            """,
            (user_id, int(holding_id), str(candidate_code)),
        ).fetchall()
    events = [_fund_switch_quote_event_from_row(row) for row in rows]
    previous_hash = None
    for expected_sequence, item in enumerate(events, start=1):
        review_integrity = verify_fund_switch_cost_review(
            str(item.get("review_id") or ""),
            user_id=user_id,
        )
        canonical = {
            "id": item.get("id"),
            "review_id": item.get("review_id"),
            "user_id": item.get("user_id"),
            "holding_id": item.get("holding_id"),
            "selected_code": item.get("selected_code"),
            "candidate_code": item.get("candidate_code"),
            "sequence_no": item.get("sequence_no"),
            "schema_version": item.get("schema_version"),
            "actor_id": item.get("actor_id"),
            "quoted_at": item.get("quoted_at"),
            "payload": item.get("payload"),
            "payload_sha256": item.get("payload_sha256"),
            "previous_hash": item.get("previous_hash"),
            "created_at": item.get("created_at"),
        }
        calculated = hashlib.sha256(
            canonical_json(canonical).encode("utf-8")
        ).hexdigest()
        if (
            int(item.get("sequence_no") or 0) != expected_sequence
            or item.get("previous_hash") != previous_hash
            or item.get("event_hash") != calculated
            or not item.get("integrity_verified")
            or not review_integrity.get("verified")
        ):
            return {
                "verified": False,
                "holding_id": int(holding_id),
                "candidate_code": str(candidate_code),
                "event_count": len(events),
                "failing_sequence": item.get("sequence_no"),
                "chain_head": previous_hash,
                "reason": "quote_or_review_integrity_failed",
            }
        previous_hash = item.get("event_hash")
    return {
        "verified": bool(events),
        "holding_id": int(holding_id),
        "candidate_code": str(candidate_code),
        "event_count": len(events),
        "failing_sequence": None,
        "chain_head": previous_hash,
        "reason": None if events else "quote_events_missing",
    }


# ==================== 基金换仓执行前审查 ====================

def get_fund_switch_quote_event(
    event_id: str,
    user_id: str = "default",
) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM fund_switch_quote_events WHERE id=? AND user_id=?",
            (str(event_id), user_id),
        ).fetchone()
    return _fund_switch_quote_event_from_row(row)


def _fund_switch_execution_review_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    payload = _decode_json(item.pop("payload_json", None), None)
    item["payload"] = payload
    bindings = payload.get("bindings") if isinstance(payload, dict) else None
    evidence_payload = dict(payload) if isinstance(payload, dict) else None
    embedded_evidence = (
        str(evidence_payload.pop("evidence_sha256") or "")
        if isinstance(evidence_payload, dict)
        else ""
    )
    try:
        payload_holding_id = int(payload.get("holding_id") or 0)
    except (TypeError, ValueError, AttributeError):
        payload_holding_id = 0
    item["integrity_verified"] = bool(
        isinstance(payload, dict)
        and isinstance(bindings, dict)
        and payload_sha256(payload) == item.get("payload_sha256")
        and embedded_evidence == item.get("evidence_sha256")
        and payload_sha256(evidence_payload) == item.get("evidence_sha256")
        and payload_holding_id == int(item.get("holding_id") or 0)
        and str(payload.get("selected_code") or "") == str(item.get("selected_code") or "")
        and str(payload.get("candidate_code") or "") == str(item.get("candidate_code") or "")
        and str(payload.get("schema_version") or "") == str(item.get("schema_version") or "")
        and str(payload.get("status") or "") == str(item.get("status") or "")
        and str(bindings.get("quote_event_id") or "") == str(item.get("quote_event_id") or "")
        and str(bindings.get("quote_event_hash") or "") == str(item.get("quote_event_hash") or "")
        and str(bindings.get("profile_version_id") or "") == str(item.get("profile_version_id") or "")
        and str(bindings.get("thesis_version_id") or "") == str(item.get("thesis_version_id") or "")
    )
    return item


def _fund_switch_execution_review_reason(item: dict) -> str | None:
    if not item.get("integrity_verified"):
        return "execution_review_payload_integrity_failed"
    quote = get_fund_switch_quote_event(
        str(item.get("quote_event_id") or ""),
        user_id=str(item.get("user_id") or "default"),
    )
    if quote is None:
        return "bound_quote_event_missing"
    if not quote.get("integrity_verified"):
        return "bound_quote_event_integrity_failed"
    if str(quote.get("event_hash") or "") != str(item.get("quote_event_hash") or ""):
        return "bound_quote_event_hash_changed"
    if (
        int(quote.get("holding_id") or 0) != int(item.get("holding_id") or 0)
        or str(quote.get("selected_code") or "") != str(item.get("selected_code") or "")
        or str(quote.get("candidate_code") or "") != str(item.get("candidate_code") or "")
    ):
        return "bound_quote_identity_mismatch"
    return None


def append_fund_switch_execution_review(
    payload: dict,
    *,
    actor_id: str,
    user_id: str = "default",
) -> dict:
    """Append one immutable, hash-chained pre-trade review."""
    if not isinstance(payload, dict):
        raise TypeError("fund switch execution review payload must be an object")
    bindings = payload.get("bindings") or {}
    schema_version = str(payload.get("schema_version") or "").strip()
    status = str(payload.get("status") or "").strip()
    selected_code = str(payload.get("selected_code") or "").strip()
    candidate_code = str(payload.get("candidate_code") or "").strip()
    quote_event_id = str(bindings.get("quote_event_id") or "").strip()
    quote_event_hash = str(bindings.get("quote_event_hash") or "").strip()
    profile_version_id = str(bindings.get("profile_version_id") or "").strip()
    thesis_version_id = str(bindings.get("thesis_version_id") or "").strip()
    evidence_sha256 = str(payload.get("evidence_sha256") or "").strip()
    try:
        holding_id = int(payload.get("holding_id") or 0)
    except (TypeError, ValueError):
        holding_id = 0
    if (
        not schema_version
        or not status
        or holding_id <= 0
        or not selected_code
        or not candidate_code
        or not quote_event_id
        or len(quote_event_hash) != 64
        or len(evidence_sha256) != 64
    ):
        raise ValueError("fund switch execution review metadata is incomplete")
    evidence_payload = dict(payload)
    evidence_payload.pop("evidence_sha256", None)
    if payload_sha256(evidence_payload) != evidence_sha256:
        raise ValueError("fund switch execution review evidence hash does not match payload")

    quote = get_fund_switch_quote_event(quote_event_id, user_id=user_id)
    if quote is None:
        raise LookupError("bound fund switch quote event not found")
    if not quote.get("integrity_verified"):
        raise ValueError("bound fund switch quote event integrity failed")
    if str(quote.get("event_hash") or "") != quote_event_hash:
        raise ValueError("bound fund switch quote event hash does not match")
    if (
        int(quote.get("holding_id") or 0) != holding_id
        or str(quote.get("selected_code") or "") != selected_code
        or str(quote.get("candidate_code") or "") != candidate_code
    ):
        raise ValueError("bound fund switch quote identity does not match review")

    payload_json = canonical_json(payload)
    digest = payload_sha256(payload)
    review_id = f"fund_switch_execution_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
    actor = str(actor_id or "system")
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            previous = conn.execute(
                """
                SELECT sequence_no, review_hash FROM fund_switch_execution_reviews
                WHERE user_id=? AND holding_id=? AND candidate_code=?
                ORDER BY sequence_no DESC LIMIT 1
                """,
                (user_id, holding_id, candidate_code),
            ).fetchone()
            sequence_no = int(previous["sequence_no"] if previous else 0) + 1
            previous_hash = previous["review_hash"] if previous else None
            canonical = {
                "id": review_id,
                "user_id": user_id,
                "holding_id": holding_id,
                "selected_code": selected_code,
                "candidate_code": candidate_code,
                "quote_event_id": quote_event_id,
                "quote_event_hash": quote_event_hash,
                "profile_version_id": profile_version_id,
                "thesis_version_id": thesis_version_id,
                "sequence_no": sequence_no,
                "schema_version": schema_version,
                "status": status,
                "actor_id": actor,
                "payload": payload,
                "payload_sha256": digest,
                "evidence_sha256": evidence_sha256,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            review_hash = hashlib.sha256(
                canonical_json(canonical).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """
                INSERT INTO fund_switch_execution_reviews (
                    id, user_id, holding_id, selected_code, candidate_code,
                    quote_event_id, quote_event_hash, profile_version_id,
                    thesis_version_id, sequence_no, schema_version, status,
                    actor_id, payload_json, payload_sha256, evidence_sha256,
                    previous_hash, review_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    user_id,
                    holding_id,
                    selected_code,
                    candidate_code,
                    quote_event_id,
                    quote_event_hash,
                    profile_version_id,
                    thesis_version_id,
                    sequence_no,
                    schema_version,
                    status,
                    actor,
                    payload_json,
                    digest,
                    evidence_sha256,
                    previous_hash,
                    review_hash,
                    created_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute(
            "SELECT * FROM fund_switch_execution_reviews WHERE id=?",
            (review_id,),
        ).fetchone()
    return _fund_switch_execution_review_from_row(row)


def get_fund_switch_execution_review(
    review_id: str,
    user_id: str = "default",
) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM fund_switch_execution_reviews WHERE id=? AND user_id=?",
            (str(review_id), user_id),
        ).fetchone()
    return _fund_switch_execution_review_from_row(row)


def list_fund_switch_execution_reviews(
    *,
    holding_id: int,
    candidate_code: str | None = None,
    user_id: str = "default",
    limit: int = 100,
) -> list[dict]:
    where = ["user_id=?", "holding_id=?"]
    params: list = [user_id, int(holding_id)]
    if candidate_code:
        where.append("candidate_code=?")
        params.append(str(candidate_code))
    params.append(max(1, min(int(limit), 500)))
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT * FROM fund_switch_execution_reviews
            WHERE {' AND '.join(where)}
            ORDER BY candidate_code, sequence_no DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_fund_switch_execution_review_from_row(row) for row in rows]


def list_latest_fund_switch_execution_reviews(
    user_id: str = "default",
    *,
    holding_id: int | None = None,
) -> list[dict]:
    where = ["r.user_id=?"]
    params: list = [user_id]
    if holding_id is not None:
        where.append("r.holding_id=?")
        params.append(int(holding_id))
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT r.* FROM fund_switch_execution_reviews r
            WHERE {' AND '.join(where)}
              AND r.sequence_no=(
                  SELECT MAX(r2.sequence_no) FROM fund_switch_execution_reviews r2
                  WHERE r2.user_id=r.user_id
                    AND r2.holding_id=r.holding_id
                    AND r2.candidate_code=r.candidate_code
              )
            ORDER BY r.created_at DESC, r.id DESC
            """,
            params,
        ).fetchall()
    return [_fund_switch_execution_review_from_row(row) for row in rows]


def verify_fund_switch_execution_audit(
    holding_id: int,
    candidate_code: str,
    user_id: str = "default",
) -> dict:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM fund_switch_execution_reviews
            WHERE user_id=? AND holding_id=? AND candidate_code=?
            ORDER BY sequence_no
            """,
            (user_id, int(holding_id), str(candidate_code)),
        ).fetchall()
    reviews = [_fund_switch_execution_review_from_row(row) for row in rows]
    previous_hash = None
    for expected_sequence, item in enumerate(reviews, start=1):
        canonical = {
            "id": item.get("id"),
            "user_id": item.get("user_id"),
            "holding_id": item.get("holding_id"),
            "selected_code": item.get("selected_code"),
            "candidate_code": item.get("candidate_code"),
            "quote_event_id": item.get("quote_event_id"),
            "quote_event_hash": item.get("quote_event_hash"),
            "profile_version_id": item.get("profile_version_id"),
            "thesis_version_id": item.get("thesis_version_id"),
            "sequence_no": item.get("sequence_no"),
            "schema_version": item.get("schema_version"),
            "status": item.get("status"),
            "actor_id": item.get("actor_id"),
            "payload": item.get("payload"),
            "payload_sha256": item.get("payload_sha256"),
            "evidence_sha256": item.get("evidence_sha256"),
            "previous_hash": item.get("previous_hash"),
            "created_at": item.get("created_at"),
        }
        calculated = hashlib.sha256(
            canonical_json(canonical).encode("utf-8")
        ).hexdigest()
        reason = _fund_switch_execution_review_reason(item)
        quote_audit = verify_fund_switch_quote_audit(
            int(item.get("holding_id") or 0),
            str(item.get("candidate_code") or ""),
            user_id=user_id,
        )
        if (
            int(item.get("sequence_no") or 0) != expected_sequence
            or item.get("previous_hash") != previous_hash
            or item.get("review_hash") != calculated
            or reason is not None
            or not quote_audit.get("verified")
        ):
            return {
                "verified": False,
                "holding_id": int(holding_id),
                "candidate_code": str(candidate_code),
                "review_count": len(reviews),
                "failing_sequence": item.get("sequence_no"),
                "chain_head": previous_hash,
                "reason": reason or "execution_review_chain_integrity_failed",
            }
        previous_hash = item.get("review_hash")
    return {
        "verified": bool(reviews),
        "holding_id": int(holding_id),
        "candidate_code": str(candidate_code),
        "review_count": len(reviews),
        "failing_sequence": None,
        "chain_head": previous_hash,
        "reason": None if reviews else "execution_reviews_missing",
    }


def _fund_switch_lifecycle_event_from_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    payload = _decode_json(item.pop("payload_json", None), None)
    item["payload"] = payload
    bindings = payload.get("bindings") if isinstance(payload, dict) else None
    evidence_payload = dict(payload) if isinstance(payload, dict) else None
    embedded_evidence = (
        str(evidence_payload.pop("evidence_sha256") or "")
        if isinstance(evidence_payload, dict)
        else ""
    )
    try:
        payload_holding_id = int(payload.get("holding_id") or 0)
        payload_redemption_id = int(bindings.get("redemption_transaction_id") or 0)
        payload_purchase_id = int(bindings.get("purchase_transaction_id") or 0)
    except (TypeError, ValueError, AttributeError):
        payload_holding_id = 0
        payload_redemption_id = 0
        payload_purchase_id = 0
    item["integrity_verified"] = bool(
        isinstance(payload, dict)
        and isinstance(bindings, dict)
        and payload_sha256(payload) == item.get("payload_sha256")
        and embedded_evidence == item.get("evidence_sha256")
        and payload_sha256(evidence_payload) == item.get("evidence_sha256")
        and str(payload.get("case_id") or "") == str(item.get("case_id") or "")
        and payload_holding_id == int(item.get("holding_id") or 0)
        and str(payload.get("selected_code") or "") == str(item.get("selected_code") or "")
        and str(payload.get("candidate_code") or "") == str(item.get("candidate_code") or "")
        and str(payload.get("event_type") or "") == str(item.get("event_type") or "")
        and str(payload.get("schema_version") or "") == str(item.get("schema_version") or "")
        and str(payload.get("status") or "") == str(item.get("status") or "")
        and str(bindings.get("execution_review_id") or "")
        == str(item.get("execution_review_id") or "")
        and str(bindings.get("execution_review_hash") or "")
        == str(item.get("execution_review_hash") or "")
        and payload_redemption_id
        == int(item.get("redemption_transaction_id") or 0)
        and payload_purchase_id
        == int(item.get("purchase_transaction_id") or 0)
    )
    return item


def _fund_switch_lifecycle_event_reason(item: dict) -> str | None:
    if not item.get("integrity_verified"):
        return "lifecycle_event_payload_integrity_failed"
    review = get_fund_switch_execution_review(
        str(item.get("execution_review_id") or ""),
        user_id=str(item.get("user_id") or "default"),
    )
    if review is None:
        return "bound_execution_review_missing"
    if not review.get("integrity_verified"):
        return "bound_execution_review_integrity_failed"
    if str(review.get("review_hash") or "") != str(item.get("execution_review_hash") or ""):
        return "bound_execution_review_hash_changed"
    if (
        int(review.get("holding_id") or 0) != int(item.get("holding_id") or 0)
        or str(review.get("selected_code") or "") != str(item.get("selected_code") or "")
        or str(review.get("candidate_code") or "") != str(item.get("candidate_code") or "")
    ):
        return "bound_execution_review_identity_mismatch"
    return None


def append_fund_switch_lifecycle_event(
    payload: dict,
    *,
    actor_id: str,
    user_id: str = "default",
) -> dict:
    """Append one immutable lifecycle event after enforcing case transitions."""
    if not isinstance(payload, dict):
        raise TypeError("fund switch lifecycle payload must be an object")
    bindings = payload.get("bindings") or {}
    case_id = str(payload.get("case_id") or "").strip()
    event_type = str(payload.get("event_type") or "").strip()
    schema_version = str(payload.get("schema_version") or "").strip()
    status = str(payload.get("status") or "").strip()
    selected_code = str(payload.get("selected_code") or "").strip()
    candidate_code = str(payload.get("candidate_code") or "").strip()
    execution_review_id = str(bindings.get("execution_review_id") or "").strip()
    execution_review_hash = str(bindings.get("execution_review_hash") or "").strip()
    evidence_sha256 = str(payload.get("evidence_sha256") or "").strip()
    try:
        holding_id = int(payload.get("holding_id") or 0)
        redemption_transaction_id = int(bindings.get("redemption_transaction_id") or 0) or None
        purchase_transaction_id = int(bindings.get("purchase_transaction_id") or 0) or None
    except (TypeError, ValueError):
        holding_id = 0
        redemption_transaction_id = None
        purchase_transaction_id = None
    allowed_events = {
        "redemption_settled",
        "purchase_requoted",
        "purchase_recorded",
        "holdings_reconciled",
        "attribution_snapshot",
    }
    if (
        not case_id
        or event_type not in allowed_events
        or not schema_version
        or not status
        or holding_id <= 0
        or not selected_code
        or not candidate_code
        or not execution_review_id
        or len(execution_review_hash) != 64
        or len(evidence_sha256) != 64
    ):
        raise ValueError("fund switch lifecycle metadata is incomplete")
    evidence_payload = dict(payload)
    evidence_payload.pop("evidence_sha256", None)
    if payload_sha256(evidence_payload) != evidence_sha256:
        raise ValueError("fund switch lifecycle evidence hash does not match payload")

    review = get_fund_switch_execution_review(execution_review_id, user_id=user_id)
    if review is None:
        raise LookupError("bound fund switch execution review not found")
    if not review.get("integrity_verified"):
        raise ValueError("bound fund switch execution review integrity failed")
    if str(review.get("review_hash") or "") != execution_review_hash:
        raise ValueError("bound fund switch execution review hash does not match")
    if (
        int(review.get("holding_id") or 0) != holding_id
        or str(review.get("selected_code") or "") != selected_code
        or str(review.get("candidate_code") or "") != candidate_code
    ):
        raise ValueError("bound fund switch execution review identity does not match")

    payload_json = canonical_json(payload)
    digest = payload_sha256(payload)
    event_id = f"fund_switch_lifecycle_{uuid.uuid4().hex}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
    actor = str(actor_id or "system")
    with _lock:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior_rows = conn.execute(
                """
                SELECT * FROM fund_switch_lifecycle_events
                WHERE user_id=? AND case_id=?
                ORDER BY sequence_no
                """,
                (user_id, case_id),
            ).fetchall()
            prior = [_fund_switch_lifecycle_event_from_row(row) for row in prior_rows]
            previous = prior[-1] if prior else None
            if not prior and event_type != "redemption_settled":
                raise ValueError("fund switch lifecycle must start with redemption settlement")
            if prior:
                if (
                    int(previous.get("holding_id") or 0) != holding_id
                    or str(previous.get("selected_code") or "") != selected_code
                    or str(previous.get("candidate_code") or "") != candidate_code
                    or str(previous.get("execution_review_id") or "") != execution_review_id
                    or str(previous.get("execution_review_hash") or "") != execution_review_hash
                ):
                    raise ValueError("fund switch lifecycle case identity changed")
                event_types = [str(item.get("event_type") or "") for item in prior]
                if event_type == "redemption_settled":
                    raise ValueError("fund switch lifecycle already has a settlement event")
                if event_type == "purchase_requoted" and "purchase_recorded" in event_types:
                    raise ValueError("purchase was already recorded for this lifecycle")
                if event_type == "purchase_recorded":
                    if previous.get("event_type") != "purchase_requoted":
                        raise ValueError("purchase record requires the latest purchase requote")
                    if "purchase_recorded" in event_types:
                        raise ValueError("purchase was already recorded for this lifecycle")
                if event_type == "holdings_reconciled":
                    if "purchase_recorded" not in event_types:
                        raise ValueError("holdings reconciliation requires a purchase record")
                    if "holdings_reconciled" in event_types:
                        raise ValueError("holdings were already reconciled for this lifecycle")
                if event_type == "attribution_snapshot" and "holdings_reconciled" not in event_types:
                    raise ValueError("attribution requires reconciled holdings")

            sequence_no = len(prior) + 1
            previous_hash = previous.get("event_hash") if previous else None
            canonical = {
                "id": event_id,
                "case_id": case_id,
                "user_id": user_id,
                "holding_id": holding_id,
                "selected_code": selected_code,
                "candidate_code": candidate_code,
                "execution_review_id": execution_review_id,
                "execution_review_hash": execution_review_hash,
                "sequence_no": sequence_no,
                "event_type": event_type,
                "schema_version": schema_version,
                "status": status,
                "actor_id": actor,
                "redemption_transaction_id": redemption_transaction_id,
                "purchase_transaction_id": purchase_transaction_id,
                "payload": payload,
                "payload_sha256": digest,
                "evidence_sha256": evidence_sha256,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(
                canonical_json(canonical).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """
                INSERT INTO fund_switch_lifecycle_events (
                    id, case_id, user_id, holding_id, selected_code,
                    candidate_code, execution_review_id, execution_review_hash,
                    sequence_no, event_type, schema_version, status, actor_id,
                    redemption_transaction_id, purchase_transaction_id,
                    payload_json, payload_sha256, evidence_sha256, previous_hash,
                    event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    case_id,
                    user_id,
                    holding_id,
                    selected_code,
                    candidate_code,
                    execution_review_id,
                    execution_review_hash,
                    sequence_no,
                    event_type,
                    schema_version,
                    status,
                    actor,
                    redemption_transaction_id,
                    purchase_transaction_id,
                    payload_json,
                    digest,
                    evidence_sha256,
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute(
            "SELECT * FROM fund_switch_lifecycle_events WHERE id=?",
            (event_id,),
        ).fetchone()
    return _fund_switch_lifecycle_event_from_row(row)


def get_fund_switch_lifecycle_event(
    event_id: str,
    user_id: str = "default",
) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM fund_switch_lifecycle_events WHERE id=? AND user_id=?",
            (str(event_id), user_id),
        ).fetchone()
    return _fund_switch_lifecycle_event_from_row(row)


def list_fund_switch_lifecycle_events(
    case_id: str,
    user_id: str = "default",
) -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT * FROM fund_switch_lifecycle_events
            WHERE case_id=? AND user_id=?
            ORDER BY sequence_no
            """,
            (str(case_id), user_id),
        ).fetchall()
    return [_fund_switch_lifecycle_event_from_row(row) for row in rows]


def list_fund_switch_lifecycle_case_heads(
    user_id: str = "default",
    *,
    holding_id: int | None = None,
    candidate_code: str | None = None,
    limit: int = 100,
) -> list[dict]:
    where = ["e.user_id=?"]
    params: list = [user_id]
    if holding_id is not None:
        where.append("e.holding_id=?")
        params.append(int(holding_id))
    if candidate_code:
        where.append("e.candidate_code=?")
        params.append(str(candidate_code))
    params.append(max(1, min(int(limit), 500)))
    with _lock:
        rows = _get_conn().execute(
            f"""
            SELECT e.* FROM fund_switch_lifecycle_events e
            WHERE {' AND '.join(where)}
              AND e.sequence_no=(
                  SELECT MAX(e2.sequence_no)
                  FROM fund_switch_lifecycle_events e2
                  WHERE e2.user_id=e.user_id AND e2.case_id=e.case_id
              )
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_fund_switch_lifecycle_event_from_row(row) for row in rows]


def fund_switch_lifecycle_transaction_is_bound(
    transaction_id: int,
    *,
    event_type: str,
    user_id: str = "default",
) -> bool:
    column = {
        "redemption_settled": "redemption_transaction_id",
        "purchase_recorded": "purchase_transaction_id",
    }.get(str(event_type))
    if column is None:
        raise ValueError("unsupported fund switch lifecycle transaction binding")
    with _lock:
        row = _get_conn().execute(
            f"""
            SELECT 1 FROM fund_switch_lifecycle_events
            WHERE user_id=? AND event_type=? AND {column}=?
            LIMIT 1
            """,
            (user_id, str(event_type), int(transaction_id)),
        ).fetchone()
    return row is not None


def list_fund_switch_bound_purchase_transaction_ids(
    user_id: str = "default",
) -> set[int]:
    with _lock:
        rows = _get_conn().execute(
            """
            SELECT purchase_transaction_id
            FROM fund_switch_lifecycle_events
            WHERE user_id=? AND event_type='purchase_recorded'
              AND purchase_transaction_id IS NOT NULL
            """,
            (user_id,),
        ).fetchall()
    return {int(row["purchase_transaction_id"]) for row in rows}


def verify_fund_switch_lifecycle_audit(
    case_id: str,
    user_id: str = "default",
) -> dict:
    events = list_fund_switch_lifecycle_events(case_id, user_id=user_id)
    previous_hash = None
    for expected_sequence, item in enumerate(events, start=1):
        canonical = {
            "id": item.get("id"),
            "case_id": item.get("case_id"),
            "user_id": item.get("user_id"),
            "holding_id": item.get("holding_id"),
            "selected_code": item.get("selected_code"),
            "candidate_code": item.get("candidate_code"),
            "execution_review_id": item.get("execution_review_id"),
            "execution_review_hash": item.get("execution_review_hash"),
            "sequence_no": item.get("sequence_no"),
            "event_type": item.get("event_type"),
            "schema_version": item.get("schema_version"),
            "status": item.get("status"),
            "actor_id": item.get("actor_id"),
            "redemption_transaction_id": item.get("redemption_transaction_id"),
            "purchase_transaction_id": item.get("purchase_transaction_id"),
            "payload": item.get("payload"),
            "payload_sha256": item.get("payload_sha256"),
            "evidence_sha256": item.get("evidence_sha256"),
            "previous_hash": item.get("previous_hash"),
            "created_at": item.get("created_at"),
        }
        calculated = hashlib.sha256(
            canonical_json(canonical).encode("utf-8")
        ).hexdigest()
        reason = _fund_switch_lifecycle_event_reason(item)
        if (
            int(item.get("sequence_no") or 0) != expected_sequence
            or item.get("previous_hash") != previous_hash
            or item.get("event_hash") != calculated
            or reason is not None
        ):
            return {
                "verified": False,
                "case_id": str(case_id),
                "event_count": len(events),
                "failing_sequence": item.get("sequence_no"),
                "chain_head": previous_hash,
                "reason": reason or "lifecycle_event_chain_integrity_failed",
            }
        previous_hash = item.get("event_hash")
    return {
        "verified": bool(events),
        "case_id": str(case_id),
        "event_count": len(events),
        "failing_sequence": None,
        "chain_head": previous_hash,
        "reason": None if events else "lifecycle_events_missing",
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
            ORDER BY current.created_at DESC, current.id DESC
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
            ORDER BY created_at DESC, id DESC
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
