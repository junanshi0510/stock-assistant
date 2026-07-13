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
        _ensure_column(_conn, "holdings", "yesterday_profit", "REAL")
        _ensure_column(_conn, "portfolio_transactions", "source", "TEXT")
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
