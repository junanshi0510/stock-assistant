# -*- coding: utf-8 -*-
"""Database metadata and audit chain for private object-store assets."""

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


def _iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None) -> dict[str, Any]:
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ObjectAssetRepository:
    def __init__(self, target: str | os.PathLike[str] | None = None) -> None:
        self.target = str(
            target
            or configured_database_target(
                str(Path(__file__).resolve().parent / "stock_assistant.db")
            )
        )
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        self.ensure_schema()

    def _connect(self):
        return connect_database(self.target, close_on_exit=True)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(
                        connection, {"object_assets", "object_asset_events"}
                    )
                else:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS object_assets (
                            id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            purpose TEXT NOT NULL,
                            provider TEXT NOT NULL,
                            bucket TEXT NOT NULL,
                            object_key TEXT NOT NULL,
                            sha256 TEXT NOT NULL,
                            content_type TEXT NOT NULL,
                            byte_size INTEGER NOT NULL,
                            status TEXT NOT NULL,
                            retention_until TEXT,
                            encryption_mode TEXT NOT NULL,
                            metadata_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            deleted_at TEXT,
                            UNIQUE(bucket, object_key)
                        );
                        CREATE TABLE IF NOT EXISTS object_asset_events (
                            id TEXT PRIMARY KEY,
                            asset_id TEXT NOT NULL REFERENCES object_assets(id) ON DELETE RESTRICT,
                            sequence_no INTEGER NOT NULL,
                            event_type TEXT NOT NULL,
                            details_json TEXT NOT NULL,
                            previous_hash TEXT,
                            event_hash TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            UNIQUE(asset_id, sequence_no)
                        );
                        CREATE TRIGGER IF NOT EXISTS trg_object_asset_events_no_update
                        BEFORE UPDATE ON object_asset_events BEGIN
                            SELECT RAISE(ABORT, 'object asset events are immutable');
                        END;
                        CREATE TRIGGER IF NOT EXISTS trg_object_asset_events_no_delete
                        BEFORE DELETE ON object_asset_events BEGIN
                            SELECT RAISE(ABORT, 'object asset events are immutable');
                        END;
                        """
                    )
            self._schema_ready = True

    def _append_event(
        self,
        connection,
        asset_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous = connection.execute(
            """
            SELECT sequence_no, event_hash FROM object_asset_events
            WHERE asset_id=? ORDER BY sequence_no DESC LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        created_at = _iso()
        payload = {
            "id": f"asset_evt_{uuid.uuid4().hex}",
            "asset_id": asset_id,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "details": details or {},
            "previous_hash": previous["event_hash"] if previous else None,
            "created_at": created_at,
        }
        event_hash = _sha256(_json(payload))
        connection.execute(
            """
            INSERT INTO object_asset_events(
                id, asset_id, sequence_no, event_type, details_json,
                previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                asset_id,
                sequence_no,
                event_type,
                _json(details or {}),
                payload["previous_hash"],
                event_hash,
                created_at,
            ),
        )

    @staticmethod
    def _from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["metadata"] = _load(item.pop("metadata_json", None))
        return item

    def reserve(
        self,
        *,
        user_id: str,
        purpose: str,
        bucket: str,
        object_key: str,
        sha256: str,
        content_type: str,
        byte_size: int,
        retention_until: str,
        encryption_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM object_assets
                WHERE user_id=? AND purpose=? AND sha256=?
                  AND status IN ('pending', 'available')
                """,
                (user_id, purpose, sha256),
            ).fetchone()
            if existing is not None:
                return self._from_row(existing), False
            asset_id = f"asset_{uuid.uuid4().hex}"
            created_at = _iso()
            connection.execute(
                """
                INSERT INTO object_assets(
                    id, user_id, purpose, provider, bucket, object_key, sha256,
                    content_type, byte_size, status, retention_until,
                    encryption_mode, metadata_json, created_at
                ) VALUES (?, ?, ?, 'aliyun_oss', ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    user_id,
                    purpose,
                    bucket,
                    object_key,
                    sha256,
                    content_type,
                    int(byte_size),
                    retention_until,
                    encryption_mode,
                    _json(metadata or {}),
                    created_at,
                ),
            )
            self._append_event(
                connection,
                asset_id,
                "asset_reserved",
                {"sha256": sha256, "byte_size": int(byte_size)},
            )
            row = connection.execute(
                "SELECT * FROM object_assets WHERE id=?", (asset_id,)
            ).fetchone()
        return self._from_row(row), True

    def mark_available(self, asset_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE object_assets SET status='available' WHERE id=? AND status='pending'",
                (asset_id,),
            )
            if cursor.rowcount == 1:
                self._append_event(connection, asset_id, "asset_uploaded")
            row = connection.execute(
                "SELECT * FROM object_assets WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise KeyError("对象资产不存在")
        return self._from_row(row)

    def mark_quarantined(self, asset_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE object_assets SET status='quarantined' WHERE id=?",
                (asset_id,),
            )
            self._append_event(
                connection,
                asset_id,
                "asset_quarantined",
                {"reason": str(reason)[:120]},
            )

    def mark_deleted(self, asset_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE object_assets SET status='deleted', deleted_at=?
                WHERE id=? AND status!='deleted'
                """,
                (_iso(), asset_id),
            )
            if cursor.rowcount == 1:
                self._append_event(connection, asset_id, "asset_deleted")

    def get(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM object_assets WHERE id=?", (asset_id,)
            ).fetchone()
        return self._from_row(row)

    def list_expired(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM object_assets
                WHERE status IN ('available', 'quarantined')
                  AND retention_until IS NOT NULL AND retention_until<=?
                ORDER BY retention_until, id LIMIT ?
                """,
                (_iso(), max(1, min(1000, int(limit)))),
            ).fetchall()
        return [self._from_row(row) for row in rows]
