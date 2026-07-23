# -*- coding: utf-8 -*-
"""Durable storage for the cross-market Opportunity Factory.

Strategy definitions are append-only versions.  Scan results, paper basket
snapshots, and paper observations are content-addressed so a later UI change
cannot silently rewrite the evidence that produced an earlier shortlist.
"""

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


TERMINAL_RUN_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
ACTIVE_RUN_STATUSES = {"queued", "running"}
REQUIRED_TABLES = {
    "opportunity_strategies",
    "opportunity_strategy_versions",
    "opportunity_runs",
    "opportunity_run_events",
    "opportunity_paper_baskets",
    "opportunity_paper_observations",
}


class OpportunityRepositoryError(RuntimeError):
    pass


class OpportunityNotFoundError(OpportunityRepositoryError):
    pass


class OpportunityConflictError(OpportunityRepositoryError):
    pass


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunity_strategies (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    status             TEXT NOT NULL CHECK(status IN ('active', 'archived')),
    current_version_no INTEGER NOT NULL CHECK(current_version_no >= 1),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opportunity_strategies_user
ON opportunity_strategies(user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS opportunity_strategy_versions (
    id                TEXT PRIMARY KEY,
    strategy_id       TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    user_id           TEXT NOT NULL,
    version_no        INTEGER NOT NULL CHECK(version_no >= 1),
    schema_version    TEXT NOT NULL,
    definition_json  TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL,
    actor_id          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE(strategy_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_strategy_versions
ON opportunity_strategy_versions(strategy_id, version_no DESC);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_strategy_versions_no_update
BEFORE UPDATE ON opportunity_strategy_versions BEGIN
    SELECT RAISE(ABORT, 'opportunity strategy versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_strategy_versions_no_delete
BEFORE DELETE ON opportunity_strategy_versions BEGIN
    SELECT RAISE(ABORT, 'opportunity strategy versions are immutable');
END;

CREATE TABLE IF NOT EXISTS opportunity_runs (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    strategy_id         TEXT NOT NULL REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    strategy_version_id TEXT NOT NULL REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    strategy_version_no INTEGER NOT NULL,
    strategy_sha256     TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','cancelled')),
    job_id              TEXT,
    progress_json       TEXT NOT NULL,
    result_json         TEXT,
    result_sha256       TEXT,
    error_code          TEXT,
    error_message       TEXT,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_opportunity_runs_user
ON opportunity_runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunity_runs_strategy
ON opportunity_runs(strategy_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_run_result_immutable
BEFORE UPDATE OF result_json, result_sha256 ON opportunity_runs
WHEN OLD.result_json IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'opportunity run result is immutable');
END;

CREATE TABLE IF NOT EXISTS opportunity_run_events (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES opportunity_runs(id) ON DELETE RESTRICT,
    sequence_no   INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    previous_hash TEXT,
    event_hash    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_run_events
ON opportunity_run_events(run_id, sequence_no);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_run_events_no_update
BEFORE UPDATE ON opportunity_run_events BEGIN
    SELECT RAISE(ABORT, 'opportunity run events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_run_events_no_delete
BEFORE DELETE ON opportunity_run_events BEGIN
    SELECT RAISE(ABORT, 'opportunity run events are immutable');
END;

CREATE TABLE IF NOT EXISTS opportunity_paper_baskets (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES opportunity_runs(id) ON DELETE RESTRICT,
    schema_version  TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(user_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_paper_baskets_user
ON opportunity_paper_baskets(user_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_paper_baskets_no_update
BEFORE UPDATE ON opportunity_paper_baskets BEGIN
    SELECT RAISE(ABORT, 'opportunity paper baskets are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_paper_baskets_no_delete
BEFORE DELETE ON opportunity_paper_baskets BEGIN
    SELECT RAISE(ABORT, 'opportunity paper baskets are immutable');
END;

CREATE TABLE IF NOT EXISTS opportunity_paper_observations (
    id             TEXT PRIMARY KEY,
    basket_id      TEXT NOT NULL REFERENCES opportunity_paper_baskets(id) ON DELETE RESTRICT,
    user_id        TEXT NOT NULL,
    sequence_no    INTEGER NOT NULL,
    observed_at    TEXT NOT NULL,
    idempotency_key TEXT,
    payload_json   TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    previous_hash  TEXT,
    event_hash     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE(basket_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_paper_observations
ON opportunity_paper_observations(basket_id, sequence_no DESC);
CREATE TRIGGER IF NOT EXISTS trg_opportunity_paper_observations_no_update
BEFORE UPDATE ON opportunity_paper_observations BEGIN
    SELECT RAISE(ABORT, 'opportunity paper observations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_opportunity_paper_observations_no_delete
BEFORE DELETE ON opportunity_paper_observations BEGIN
    SELECT RAISE(ABORT, 'opportunity paper observations are immutable');
END;
"""


class OpportunityRepository:
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
            with connect_database(self.database_target, close_on_exit=True) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
                    columns = {
                        str(row["name"])
                        for row in connection.execute(
                            "PRAGMA table_info(opportunity_paper_observations)"
                        ).fetchall()
                    }
                    if "idempotency_key" not in columns:
                        connection.execute(
                            "ALTER TABLE opportunity_paper_observations "
                            "ADD COLUMN idempotency_key TEXT"
                        )
                    connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunity_observation_idempotency
                        ON opportunity_paper_observations(
                            user_id, basket_id, idempotency_key
                        )
                        WHERE idempotency_key IS NOT NULL
                        """
                    )
            self._schema_ready = True

    @staticmethod
    def _append_run_event(
        connection,
        run_id: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = connection.execute(
            """
            SELECT sequence_no, event_hash FROM opportunity_run_events
            WHERE run_id=? ORDER BY sequence_no DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        created_at = _iso()
        event = {
            "id": _new_id("opp_evt"),
            "run_id": run_id,
            "sequence_no": sequence_no,
            "event_type": str(event_type),
            "actor_id": str(actor_id),
            "details": details or {},
            "previous_hash": previous["event_hash"] if previous else None,
            "created_at": created_at,
        }
        event_hash = _sha256(_json(event))
        connection.execute(
            """
            INSERT INTO opportunity_run_events(
                id, run_id, sequence_no, event_type, actor_id, details_json,
                previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                run_id,
                sequence_no,
                event_type,
                actor_id,
                _json(details or {}),
                event["previous_hash"],
                event_hash,
                created_at,
            ),
        )
        return {**event, "event_hash": event_hash}

    @staticmethod
    def _strategy_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        definition_json = str(item.pop("definition_json"))
        item["definition"] = _load(definition_json, {})
        item["definition_verified"] = (
            _sha256(definition_json) == item.get("definition_sha256")
        )
        return item

    @staticmethod
    def _run_from_row(row, *, include_result: bool = True) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        progress_json = str(item.pop("progress_json") or "{}")
        result_json = item.pop("result_json", None)
        item["progress"] = _load(progress_json, {})
        if include_result:
            item["result"] = _load(result_json, None)
        item["result_verified"] = bool(
            result_json is None
            or (
                item.get("result_sha256")
                and _sha256(str(result_json)) == item.get("result_sha256")
            )
        )
        return item

    @staticmethod
    def _observation_from_row(row) -> dict[str, Any]:
        item = dict(row)
        payload_json = str(item.pop("payload_json"))
        item["payload"] = _load(payload_json, {})
        item["payload_verified"] = _sha256(payload_json) == item.get("payload_sha256")
        return item

    def create_strategy(
        self,
        *,
        user_id: str,
        definition: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        strategy_id = _new_id("opp_strategy")
        version_id = _new_id("opp_strategy_v")
        definition_json = _json(definition)
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO opportunity_strategies(
                    id, user_id, status, current_version_no, created_at, updated_at
                ) VALUES (?, ?, 'active', 1, ?, ?)
                """,
                (strategy_id, user_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO opportunity_strategy_versions(
                    id, strategy_id, user_id, version_no, schema_version,
                    definition_json, definition_sha256, actor_id, created_at
                ) VALUES (?, ?, ?, 1, 'opportunity_strategy.v1', ?, ?, ?, ?)
                """,
                (
                    version_id,
                    strategy_id,
                    user_id,
                    definition_json,
                    _sha256(definition_json),
                    actor_id,
                    now,
                ),
            )
        created = self.get_strategy(strategy_id, user_id=user_id)
        if created is None:
            raise OpportunityRepositoryError("created opportunity strategy disappeared")
        return created

    def add_strategy_version(
        self,
        strategy_id: str,
        *,
        user_id: str,
        definition: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        definition_json = _json(definition)
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            strategy = connection.execute(
                "SELECT * FROM opportunity_strategies WHERE id=? AND user_id=?",
                (strategy_id, user_id),
            ).fetchone()
            if strategy is None:
                raise OpportunityNotFoundError("机会策略不存在")
            if str(strategy["status"]) != "active":
                raise OpportunityConflictError("已归档策略不能创建新版本")
            version_no = int(strategy["current_version_no"]) + 1
            version_id = _new_id("opp_strategy_v")
            connection.execute(
                """
                INSERT INTO opportunity_strategy_versions(
                    id, strategy_id, user_id, version_no, schema_version,
                    definition_json, definition_sha256, actor_id, created_at
                ) VALUES (?, ?, ?, ?, 'opportunity_strategy.v1', ?, ?, ?, ?)
                """,
                (
                    version_id,
                    strategy_id,
                    user_id,
                    version_no,
                    definition_json,
                    _sha256(definition_json),
                    actor_id,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE opportunity_strategies
                SET current_version_no=?, updated_at=?
                WHERE id=? AND user_id=?
                """,
                (version_no, now, strategy_id, user_id),
            )
        updated = self.get_strategy(strategy_id, user_id=user_id)
        if updated is None:
            raise OpportunityRepositoryError("updated opportunity strategy disappeared")
        return updated

    def archive_strategy(self, strategy_id: str, *, user_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE opportunity_strategies SET status='archived', updated_at=?
                WHERE id=? AND user_id=? AND status='active'
                """,
                (_iso(), strategy_id, user_id),
            )
            if cursor.rowcount != 1:
                existing = connection.execute(
                    "SELECT id FROM opportunity_strategies WHERE id=? AND user_id=?",
                    (strategy_id, user_id),
                ).fetchone()
                if existing is None:
                    raise OpportunityNotFoundError("机会策略不存在")
        item = self.get_strategy(strategy_id, user_id=user_id)
        if item is None:
            raise OpportunityRepositoryError("archived opportunity strategy disappeared")
        return item

    def get_strategy(self, strategy_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, v.id AS version_id, v.version_no AS selected_version_no,
                       v.schema_version,
                       v.definition_json, v.definition_sha256,
                       v.actor_id AS version_actor_id,
                       v.created_at AS version_created_at
                FROM opportunity_strategies s
                JOIN opportunity_strategy_versions v
                  ON v.strategy_id=s.id AND v.version_no=s.current_version_no
                WHERE s.id=? AND s.user_id=?
                """,
                (strategy_id, user_id),
            ).fetchone()
        return self._strategy_from_row(row)

    def get_strategy_version(
        self, version_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, v.id AS version_id, v.version_no AS selected_version_no,
                       v.schema_version,
                       v.definition_json, v.definition_sha256,
                       v.actor_id AS version_actor_id,
                       v.created_at AS version_created_at
                FROM opportunity_strategy_versions v
                JOIN opportunity_strategies s ON s.id=v.strategy_id
                WHERE v.id=? AND v.user_id=? AND s.user_id=?
                """,
                (version_id, user_id, user_id),
            ).fetchone()
        return self._strategy_from_row(row)

    def list_strategies(
        self, *, user_id: str, include_archived: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        where = "s.user_id=?" if include_archived else "s.user_id=? AND s.status='active'"
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT s.*, v.id AS version_id, v.version_no AS selected_version_no,
                       v.schema_version,
                       v.definition_json, v.definition_sha256,
                       v.actor_id AS version_actor_id,
                       v.created_at AS version_created_at
                FROM opportunity_strategies s
                JOIN opportunity_strategy_versions v
                  ON v.strategy_id=s.id AND v.version_no=s.current_version_no
                WHERE {where}
                ORDER BY s.updated_at DESC, s.id DESC LIMIT ?
                """,
                (user_id, max(1, min(500, int(limit)))),
            ).fetchall()
        return [self._strategy_from_row(row) for row in rows]

    def create_run(
        self, strategy_id: str, *, user_id: str, actor_id: str
    ) -> dict[str, Any]:
        strategy = self.get_strategy(strategy_id, user_id=user_id)
        if strategy is None:
            raise OpportunityNotFoundError("机会策略不存在")
        if strategy["status"] != "active":
            raise OpportunityConflictError("已归档策略不能运行")
        if not strategy["definition_verified"]:
            raise OpportunityConflictError("策略版本完整性校验失败")
        run_id = _new_id("opp_run")
        now = _iso()
        progress = {"stage": "queued", "completed": 0, "total": 0, "message": "等待机会扫描"}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO opportunity_runs(
                    id, user_id, strategy_id, strategy_version_id,
                    strategy_version_no, strategy_sha256, status, progress_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    run_id,
                    user_id,
                    strategy_id,
                    strategy["version_id"],
                    strategy["current_version_no"],
                    strategy["definition_sha256"],
                    _json(progress),
                    now,
                ),
            )
            self._append_run_event(
                connection,
                run_id,
                "run.created",
                actor_id,
                {
                    "strategy_version_id": strategy["version_id"],
                    "strategy_sha256": strategy["definition_sha256"],
                },
            )
        created = self.get_run(run_id, user_id=user_id)
        if created is None:
            raise OpportunityRepositoryError("created opportunity run disappeared")
        return created

    def bind_job(self, run_id: str, job_id: str, *, user_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE opportunity_runs SET job_id=?
                WHERE id=? AND user_id=? AND status='queued' AND job_id IS NULL
                """,
                (job_id, run_id, user_id),
            )
            if cursor.rowcount != 1:
                raise OpportunityConflictError("机会扫描已派发或状态已变化")

    def mark_running(self, run_id: str, *, user_id: str, actor_id: str) -> None:
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM opportunity_runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if row is None:
                raise OpportunityNotFoundError("机会扫描不存在")
            if str(row["status"]) in TERMINAL_RUN_STATUSES:
                raise OpportunityConflictError("机会扫描已经结束")
            connection.execute(
                """
                UPDATE opportunity_runs
                SET status='running', started_at=COALESCE(started_at, ?),
                    progress_json=? WHERE id=? AND user_id=?
                """,
                (
                    now,
                    _json({"stage": "universe", "completed": 0, "total": 0, "message": "正在构建候选池"}),
                    run_id,
                    user_id,
                ),
            )
            self._append_run_event(connection, run_id, "run.started", actor_id)

    def update_progress(
        self, run_id: str, *, user_id: str, progress: dict[str, Any]
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE opportunity_runs SET progress_json=?
                WHERE id=? AND user_id=? AND status IN ('queued','running')
                """,
                (_json(progress), run_id, user_id),
            )

    def complete_run(
        self,
        run_id: str,
        *,
        user_id: str,
        result: dict[str, Any],
        status: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "partial"}:
            raise ValueError("invalid successful opportunity status")
        result_json = _json(result)
        completed_at = _iso()
        progress = {
            "stage": "completed",
            "completed": int((result.get("funnel") or {}).get("evaluated") or 0),
            "total": int((result.get("funnel") or {}).get("universe") or 0),
            "message": "机会扫描完成" if status == "succeeded" else "机会扫描部分完成",
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, result_json FROM opportunity_runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if row is None:
                raise OpportunityNotFoundError("机会扫描不存在")
            if row["result_json"] is not None or str(row["status"]) in TERMINAL_RUN_STATUSES:
                raise OpportunityConflictError("机会扫描结果已经冻结")
            connection.execute(
                """
                UPDATE opportunity_runs
                SET status=?, progress_json=?, result_json=?, result_sha256=?,
                    completed_at=?, error_code=NULL, error_message=NULL
                WHERE id=? AND user_id=?
                """,
                (
                    status,
                    _json(progress),
                    result_json,
                    _sha256(result_json),
                    completed_at,
                    run_id,
                    user_id,
                ),
            )
            self._append_run_event(
                connection,
                run_id,
                "run.completed",
                actor_id,
                {"status": status, "result_sha256": _sha256(result_json)},
            )
        completed = self.get_run(run_id, user_id=user_id)
        if completed is None:
            raise OpportunityRepositoryError("completed opportunity run disappeared")
        return completed

    def fail_run(
        self,
        run_id: str,
        *,
        user_id: str,
        error_code: str,
        error_message: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM opportunity_runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if row is None:
                raise OpportunityNotFoundError("机会扫描不存在")
            if str(row["status"]) not in TERMINAL_RUN_STATUSES:
                connection.execute(
                    """
                    UPDATE opportunity_runs
                    SET status='failed', progress_json=?, error_code=?, error_message=?,
                        completed_at=? WHERE id=? AND user_id=?
                    """,
                    (
                        _json({"stage": "failed", "completed": 0, "total": 0, "message": "机会扫描失败"}),
                        str(error_code)[:80],
                        str(error_message)[:500],
                        _iso(),
                        run_id,
                        user_id,
                    ),
                )
                self._append_run_event(
                    connection,
                    run_id,
                    "run.failed",
                    actor_id,
                    {"error_code": str(error_code)[:80]},
                )
        failed = self.get_run(run_id, user_id=user_id)
        if failed is None:
            raise OpportunityRepositoryError("failed opportunity run disappeared")
        return failed

    def get_run(
        self, run_id: str, *, user_id: str, include_events: bool = True
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM opportunity_runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            item = self._run_from_row(row)
            if item is not None and include_events:
                events = connection.execute(
                    """
                    SELECT * FROM opportunity_run_events
                    WHERE run_id=? ORDER BY sequence_no
                    """,
                    (run_id,),
                ).fetchall()
                item["events"] = [
                    {
                        **{key: value for key, value in dict(event).items() if key != "details_json"},
                        "details": _load(event["details_json"], {}),
                    }
                    for event in events
                ]
        return item

    def list_runs(
        self,
        *,
        user_id: str,
        strategy_id: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "user_id=?"
        if strategy_id:
            where += " AND strategy_id=?"
            params.append(strategy_id)
        params.append(max(1, min(200, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM opportunity_runs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._run_from_row(row, include_result=False) for row in rows]

    def get_prior_completed_run(
        self, *, strategy_id: str, before_run_id: str, user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            current = connection.execute(
                "SELECT created_at FROM opportunity_runs WHERE id=? AND user_id=?",
                (before_run_id, user_id),
            ).fetchone()
            if current is None:
                return None
            row = connection.execute(
                """
                SELECT * FROM opportunity_runs
                WHERE user_id=? AND strategy_id=? AND id<>?
                  AND status IN ('succeeded','partial') AND result_json IS NOT NULL
                  AND created_at<=?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (user_id, strategy_id, before_run_id, current["created_at"]),
            ).fetchone()
        return self._run_from_row(row)

    def create_paper_basket(
        self,
        *,
        run_id: str,
        user_id: str,
        snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        snapshot_json = _json(snapshot)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT status, result_sha256 FROM opportunity_runs
                WHERE id=? AND user_id=?
                """,
                (run_id, user_id),
            ).fetchone()
            if run is None:
                raise OpportunityNotFoundError("机会扫描不存在")
            if str(run["status"]) not in {"succeeded", "partial"}:
                raise OpportunityConflictError("只有已完成扫描可以冻结纸面组合")
            existing = connection.execute(
                "SELECT * FROM opportunity_paper_baskets WHERE user_id=? AND run_id=?",
                (user_id, run_id),
            ).fetchone()
            if existing is not None:
                return self._basket_from_row(connection, existing), False
            basket_id = _new_id("opp_paper")
            connection.execute(
                """
                INSERT INTO opportunity_paper_baskets(
                    id, user_id, run_id, schema_version, snapshot_json,
                    snapshot_sha256, created_at
                ) VALUES (?, ?, ?, 'opportunity_paper_basket.v1', ?, ?, ?)
                """,
                (
                    basket_id,
                    user_id,
                    run_id,
                    snapshot_json,
                    _sha256(snapshot_json),
                    _iso(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM opportunity_paper_baskets WHERE id=?", (basket_id,)
            ).fetchone()
            return self._basket_from_row(connection, row), True

    def _basket_from_row(self, connection, row) -> dict[str, Any]:
        item = dict(row)
        snapshot_json = str(item.pop("snapshot_json"))
        item["snapshot"] = _load(snapshot_json, {})
        item["snapshot_verified"] = _sha256(snapshot_json) == item.get("snapshot_sha256")
        observations = connection.execute(
            """
            SELECT * FROM opportunity_paper_observations
            WHERE basket_id=? ORDER BY sequence_no DESC LIMIT 100
            """,
            (item["id"],),
        ).fetchall()
        item["observations"] = [self._observation_from_row(value) for value in observations]
        item["latest_observation"] = item["observations"][0] if item["observations"] else None
        return item

    def get_paper_basket(self, basket_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM opportunity_paper_baskets WHERE id=? AND user_id=?",
                (basket_id, user_id),
            ).fetchone()
            return self._basket_from_row(connection, row) if row is not None else None

    def list_paper_baskets(
        self, *, user_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM opportunity_paper_baskets
                WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (user_id, max(1, min(200, int(limit)))),
            ).fetchall()
            return [self._basket_from_row(connection, row) for row in rows]

    def list_paper_basket_scopes(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Return minimal cross-user basket state for the internal scheduler."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM opportunity_paper_baskets
                ORDER BY created_at ASC, id ASC LIMIT ?
                """,
                (max(1, min(5000, int(limit))),),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                snapshot_json = str(item.pop("snapshot_json"))
                item["snapshot"] = _load(snapshot_json, {})
                item["snapshot_verified"] = (
                    _sha256(snapshot_json) == item.get("snapshot_sha256")
                )
                latest = connection.execute(
                    """
                    SELECT * FROM opportunity_paper_observations
                    WHERE basket_id=? ORDER BY sequence_no DESC LIMIT 1
                    """,
                    (item["id"],),
                ).fetchone()
                item["latest_observation"] = (
                    self._observation_from_row(latest) if latest is not None else None
                )
                items.append(item)
        return items

    def count_tested_strategy_versions(self, *, user_id: str) -> int:
        """Count historical strategy versions that reached forward testing."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT r.strategy_version_id) AS value
                FROM opportunity_runs r
                JOIN opportunity_paper_baskets b
                  ON b.run_id=r.id AND b.user_id=r.user_id
                WHERE r.user_id=?
                """,
                (user_id,),
            ).fetchone()
        return int(row["value"] or 0) if row is not None else 0

    def append_paper_observation(
        self,
        basket_id: str,
        *,
        user_id: str,
        observed_at: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload_json = _json(payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            basket = connection.execute(
                "SELECT id FROM opportunity_paper_baskets WHERE id=? AND user_id=?",
                (basket_id, user_id),
            ).fetchone()
            if basket is None:
                raise OpportunityNotFoundError("纸面组合不存在")
            normalized_key = str(idempotency_key or "").strip() or None
            if normalized_key:
                existing = connection.execute(
                    """
                    SELECT * FROM opportunity_paper_observations
                    WHERE user_id=? AND basket_id=? AND idempotency_key=?
                    """,
                    (user_id, basket_id, normalized_key),
                ).fetchone()
                if existing is not None:
                    result = self._observation_from_row(existing)
                    result["deduplicated"] = True
                    return result
            previous = connection.execute(
                """
                SELECT sequence_no, event_hash FROM opportunity_paper_observations
                WHERE basket_id=? ORDER BY sequence_no DESC LIMIT 1
                """,
                (basket_id,),
            ).fetchone()
            sequence_no = int(previous["sequence_no"] if previous else 0) + 1
            created_at = _iso()
            observation_id = _new_id("opp_obs")
            event_basis = {
                "id": observation_id,
                "basket_id": basket_id,
                "user_id": user_id,
                "sequence_no": sequence_no,
                "observed_at": observed_at,
                "payload_sha256": _sha256(payload_json),
                "idempotency_key": normalized_key,
                "previous_hash": previous["event_hash"] if previous else None,
                "created_at": created_at,
            }
            event_hash = _sha256(_json(event_basis))
            connection.execute(
                """
                INSERT INTO opportunity_paper_observations(
                    id, basket_id, user_id, sequence_no, observed_at, idempotency_key,
                    payload_json, payload_sha256, previous_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    basket_id,
                    user_id,
                    sequence_no,
                    observed_at,
                    normalized_key,
                    payload_json,
                    event_basis["payload_sha256"],
                    event_basis["previous_hash"],
                    event_hash,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM opportunity_paper_observations WHERE id=?",
                (observation_id,),
            ).fetchone()
        result = self._observation_from_row(row)
        result["deduplicated"] = False
        return result


repository = OpportunityRepository()
