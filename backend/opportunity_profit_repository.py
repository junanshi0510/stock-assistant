# -*- coding: utf-8 -*-
"""Append-only policies and scorecards for forward strategy validation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from database import (
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
)
from opportunity_repository import OpportunityNotFoundError


POLICY_SCHEMA_VERSION = "opportunity_profit_policy.v1"
SCORECARD_SCHEMA_VERSION = "opportunity_profit_scorecard.v1"
REQUIRED_TABLES = {
    "opportunity_profit_policy_versions",
    "opportunity_profit_scorecards",
}


class OpportunityProfitRepositoryError(RuntimeError):
    pass


def _iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunity_profit_policy_versions (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    strategy_id         TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    strategy_version_id TEXT NOT NULL REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    version_no          INTEGER NOT NULL CHECK(version_no >= 1),
    schema_version      TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    payload_sha256      TEXT NOT NULL,
    actor_id            TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(user_id, strategy_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_profit_policy_strategy
ON opportunity_profit_policy_versions(user_id, strategy_id, version_no DESC);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_profit_policy_no_update
BEFORE UPDATE ON opportunity_profit_policy_versions BEGIN
    SELECT RAISE(ABORT, 'opportunity profit policies are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_profit_policy_no_delete
BEFORE DELETE ON opportunity_profit_policy_versions BEGIN
    SELECT RAISE(ABORT, 'opportunity profit policies are immutable');
END;

CREATE TABLE IF NOT EXISTS opportunity_profit_scorecards (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    strategy_id         TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    strategy_version_id TEXT NOT NULL REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    policy_id           TEXT NOT NULL REFERENCES opportunity_profit_policy_versions(id) ON DELETE RESTRICT,
    schema_version      TEXT NOT NULL,
    evidence_cutoff_at  TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    payload_sha256      TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(user_id, strategy_id, policy_id, payload_sha256)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_profit_scorecards_strategy
ON opportunity_profit_scorecards(user_id, strategy_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_profit_scorecards_no_update
BEFORE UPDATE ON opportunity_profit_scorecards BEGIN
    SELECT RAISE(ABORT, 'opportunity profit scorecards are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_profit_scorecards_no_delete
BEFORE DELETE ON opportunity_profit_scorecards BEGIN
    SELECT RAISE(ABORT, 'opportunity profit scorecards are immutable');
END;
"""


class OpportunityProfitRepository:
    def __init__(self, database_target: str | os.PathLike[str] | None = None) -> None:
        self.database_target = str(
            database_target
            or configured_database_target(
                str(Path(__file__).resolve().parent / "stock_assistant.db")
            )
        )
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self):
        self._ensure_schema()
        return connect_database(self.database_target, close_on_exit=True)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with connect_database(
                self.database_target, close_on_exit=True
            ) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
            self._schema_ready = True

    @staticmethod
    def _policy_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload_json = str(item.pop("payload_json"))
        item["policy"] = _load(payload_json, {})
        item["integrity_verified"] = (
            _sha256(payload_json) == item.get("payload_sha256")
            and item.get("schema_version") == POLICY_SCHEMA_VERSION
        )
        item["persisted"] = True
        return item

    @staticmethod
    def _scorecard_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload_json = str(item.pop("payload_json"))
        item["scorecard"] = _load(payload_json, {})
        item["integrity_verified"] = (
            _sha256(payload_json) == item.get("payload_sha256")
            and item.get("schema_version") == SCORECARD_SCHEMA_VERSION
        )
        return item

    def latest_policy(
        self,
        strategy_id: str,
        *,
        user_id: str,
        strategy_version_id: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [user_id, strategy_id]
        where = "user_id=? AND strategy_id=?"
        if strategy_version_id:
            where += " AND strategy_version_id=?"
            params.append(strategy_version_id)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM opportunity_profit_policy_versions
                WHERE {where}
                ORDER BY version_no DESC, created_at DESC LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._policy_from_row(row)

    def create_policy(
        self,
        *,
        user_id: str,
        strategy_id: str,
        strategy_version_id: str,
        policy: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        payload_json = _json(policy)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            strategy = connection.execute(
                """
                SELECT s.id
                FROM opportunity_strategies s
                JOIN opportunity_strategy_versions v ON v.strategy_id=s.id
                WHERE s.id=? AND s.user_id=? AND v.id=? AND v.user_id=?
                """,
                (strategy_id, user_id, strategy_version_id, user_id),
            ).fetchone()
            if strategy is None:
                raise OpportunityNotFoundError("机会策略或绑定版本不存在")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(version_no), 0) + 1 AS value
                FROM opportunity_profit_policy_versions
                WHERE user_id=? AND strategy_id=?
                """,
                (user_id, strategy_id),
            ).fetchone()
            version_no = int(row["value"])
            policy_id = _new_id("opp_profit_policy")
            created_at = _iso()
            connection.execute(
                """
                INSERT INTO opportunity_profit_policy_versions(
                    id, user_id, strategy_id, strategy_version_id, version_no,
                    schema_version, payload_json, payload_sha256, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_id,
                    user_id,
                    strategy_id,
                    strategy_version_id,
                    version_no,
                    POLICY_SCHEMA_VERSION,
                    payload_json,
                    _sha256(payload_json),
                    actor_id,
                    created_at,
                ),
            )
            saved = connection.execute(
                "SELECT * FROM opportunity_profit_policy_versions WHERE id=?",
                (policy_id,),
            ).fetchone()
        result = self._policy_from_row(saved)
        if result is None:
            raise OpportunityProfitRepositoryError("收益验证政策保存后不可读取")
        return result

    def save_scorecard(
        self,
        *,
        user_id: str,
        strategy_id: str,
        strategy_version_id: str,
        policy_id: str,
        evidence_cutoff_at: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        payload_json = _json(payload)
        digest = _sha256(payload_json)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            policy = connection.execute(
                """
                SELECT id FROM opportunity_profit_policy_versions
                WHERE id=? AND user_id=? AND strategy_id=? AND strategy_version_id=?
                """,
                (policy_id, user_id, strategy_id, strategy_version_id),
            ).fetchone()
            if policy is None:
                raise OpportunityNotFoundError("收益验证政策不存在或绑定失效")
            existing = connection.execute(
                """
                SELECT * FROM opportunity_profit_scorecards
                WHERE user_id=? AND strategy_id=? AND policy_id=? AND payload_sha256=?
                """,
                (user_id, strategy_id, policy_id, digest),
            ).fetchone()
            if existing is not None:
                result = self._scorecard_from_row(existing)
                if result is None:
                    raise OpportunityProfitRepositoryError("收益记分卡读取失败")
                return result, False
            scorecard_id = _new_id("opp_profit_score")
            created_at = _iso()
            connection.execute(
                """
                INSERT INTO opportunity_profit_scorecards(
                    id, user_id, strategy_id, strategy_version_id, policy_id,
                    schema_version, evidence_cutoff_at, payload_json,
                    payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scorecard_id,
                    user_id,
                    strategy_id,
                    strategy_version_id,
                    policy_id,
                    SCORECARD_SCHEMA_VERSION,
                    evidence_cutoff_at,
                    payload_json,
                    digest,
                    created_at,
                ),
            )
            saved = connection.execute(
                "SELECT * FROM opportunity_profit_scorecards WHERE id=?",
                (scorecard_id,),
            ).fetchone()
        result = self._scorecard_from_row(saved)
        if result is None:
            raise OpportunityProfitRepositoryError("收益记分卡保存后不可读取")
        return result, True

    def latest_scorecard(
        self, strategy_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM opportunity_profit_scorecards
                WHERE user_id=? AND strategy_id=?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (user_id, strategy_id),
            ).fetchone()
        return self._scorecard_from_row(row)

    def get_scorecard(
        self, scorecard_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM opportunity_profit_scorecards
                WHERE id=? AND user_id=?
                """,
                (scorecard_id, user_id),
            ).fetchone()
        return self._scorecard_from_row(row)

    def list_scorecards(
        self,
        *,
        user_id: str,
        strategy_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "user_id=?"
        if strategy_id:
            where += " AND strategy_id=?"
            params.append(strategy_id)
        params.append(max(1, min(200, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM opportunity_profit_scorecards
                WHERE {where}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [
            item
            for item in (self._scorecard_from_row(row) for row in rows)
            if item is not None
        ]


repository = OpportunityProfitRepository()
