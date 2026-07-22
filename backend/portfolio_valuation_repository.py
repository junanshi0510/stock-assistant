# -*- coding: utf-8 -*-
"""Durable market observations and immutable portfolio valuation snapshots."""

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


OBSERVATION_SCHEMA_VERSION = "market_observation.v1"
SNAPSHOT_SCHEMA_VERSION = "portfolio_valuation_snapshot.v1"
REQUIRED_TABLES = {"market_observations", "portfolio_valuation_snapshots"}


class PortfolioValuationRepositoryError(RuntimeError):
    pass


class PortfolioValuationNotFoundError(PortfolioValuationRepositoryError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_observations (
    id              TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK(kind IN ('price','nav','fx')),
    asset_type      TEXT NOT NULL,
    market          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    currency        TEXT NOT NULL,
    value           REAL NOT NULL CHECK(value > 0),
    as_of           TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT,
    quality_status  TEXT NOT NULL CHECK(quality_status IN ('primary','fallback','identity')),
    retrieved_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_observation_latest
ON market_observations(kind, market, symbol, retrieved_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_market_observation_expiry
ON market_observations(expires_at, kind);
CREATE TRIGGER IF NOT EXISTS trg_market_observations_no_update
BEFORE UPDATE ON market_observations BEGIN
    SELECT RAISE(ABORT, 'market observations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_market_observations_no_delete
BEFORE DELETE ON market_observations BEGIN
    SELECT RAISE(ABORT, 'market observations are immutable');
END;

CREATE TABLE IF NOT EXISTS portfolio_valuation_snapshots (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    user_id            TEXT NOT NULL,
    actor_id           TEXT NOT NULL,
    schema_version     TEXT NOT NULL,
    holdings_sha256    TEXT NOT NULL,
    status             TEXT NOT NULL CHECK(status IN ('complete','partial','blocked')),
    base_currency      TEXT NOT NULL,
    total_value        REAL,
    coverage_pct       REAL NOT NULL,
    decision_eligible  INTEGER NOT NULL CHECK(decision_eligible IN (0, 1)),
    fresh_until        TEXT,
    payload_json       TEXT NOT NULL,
    payload_sha256     TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_valuation_scope
ON portfolio_valuation_snapshots(tenant_id, user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_valuation_binding
ON portfolio_valuation_snapshots(user_id, holdings_sha256, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_portfolio_valuation_no_update
BEFORE UPDATE ON portfolio_valuation_snapshots BEGIN
    SELECT RAISE(ABORT, 'portfolio valuation snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_portfolio_valuation_no_delete
BEFORE DELETE ON portfolio_valuation_snapshots BEGIN
    SELECT RAISE(ABORT, 'portfolio valuation snapshots are immutable');
END;
"""


class PortfolioValuationRepository:
    def __init__(self, database_target: str | os.PathLike[str] | None = None) -> None:
        self.database_target = str(
            database_target
            or configured_database_target(
                str(Path(__file__).resolve().parent / "stock_assistant.db")
            )
        )
        self._schema_ready = False
        self._schema_lock = threading.Lock()

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
            self._schema_ready = True

    def _connect(self):
        self._ensure_schema()
        return connect_database(self.database_target, close_on_exit=True)

    @staticmethod
    def _observation_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["payload"] = _load(item.pop("payload_json", None), {})
        item["integrity"] = {
            "verified": sha256_text(canonical_json(item["payload"]))
            == item.get("payload_sha256"),
            "schema_version": item.get("schema_version"),
        }
        return item

    @staticmethod
    def _snapshot_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["decision_eligible"] = bool(item.get("decision_eligible"))
        item["payload"] = _load(item.pop("payload_json", None), {})
        item["integrity"] = {
            "verified": sha256_text(canonical_json(item["payload"]))
            == item.get("payload_sha256"),
            "schema_version": item.get("schema_version"),
        }
        return item

    def save_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        kind = str(observation.get("kind") or "")
        quality = str(observation.get("quality_status") or "")
        if kind not in {"price", "nav", "fx"}:
            raise ValueError("invalid market observation kind")
        if quality not in {"primary", "fallback", "identity"}:
            raise ValueError("invalid market observation quality")
        try:
            value = float(observation["value"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("market observation value is missing") from error
        if value <= 0:
            raise ValueError("market observation value must be positive")

        payload = dict(observation.get("payload") or {})
        payload_text = canonical_json(payload)
        identity = canonical_json({
            "kind": kind,
            "market": str(observation.get("market") or ""),
            "symbol": str(observation.get("symbol") or ""),
            "currency": str(observation.get("currency") or ""),
            "value": value,
            "as_of": str(observation.get("as_of") or ""),
            "source": str(observation.get("source") or ""),
            "retrieved_at": str(observation.get("retrieved_at") or ""),
            "payload_sha256": sha256_text(payload_text),
        })
        observation_id = f"market_obs_{sha256_text(identity)[:32]}"
        values = (
            observation_id,
            OBSERVATION_SCHEMA_VERSION,
            kind,
            str(observation.get("asset_type") or ""),
            str(observation.get("market") or ""),
            str(observation.get("symbol") or ""),
            str(observation.get("currency") or ""),
            value,
            str(observation.get("as_of") or ""),
            str(observation.get("source") or ""),
            str(observation.get("source_url") or "") or None,
            quality,
            str(observation.get("retrieved_at") or ""),
            str(observation.get("expires_at") or ""),
            payload_text,
            sha256_text(payload_text),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_observations(
                    id, schema_version, kind, asset_type, market, symbol,
                    currency, value, as_of, source, source_url, quality_status,
                    retrieved_at, expires_at, payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                values,
            )
        item = self.get_observation(observation_id)
        if item is None:
            raise PortfolioValuationRepositoryError("saved market observation disappeared")
        return item

    def get_observation(self, observation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_observations WHERE id=?",
                (str(observation_id),),
            ).fetchone()
        return self._observation_from_row(row)

    def latest_observation(
        self,
        *,
        kind: str,
        market: str,
        symbol: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM market_observations
                WHERE kind=? AND market=? AND symbol=?
                ORDER BY retrieved_at DESC, id DESC LIMIT 1
                """,
                (str(kind), str(market), str(symbol)),
            ).fetchone()
        return self._observation_from_row(row)

    def create_snapshot(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        holdings_sha256: str,
        status: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if status not in {"complete", "partial", "blocked"}:
            raise ValueError("invalid portfolio valuation status")
        payload_text = canonical_json(payload)
        snapshot_id = f"portfolio_valuation_{uuid.uuid4().hex}"
        created_at = str(
            payload.get("created_at")
            or dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
        )
        coverage = payload.get("coverage") or {}
        gate = payload.get("decision_gate") or {}
        summary = payload.get("summary") or {}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_valuation_snapshots(
                    id, tenant_id, user_id, actor_id, schema_version,
                    holdings_sha256, status, base_currency, total_value,
                    coverage_pct, decision_eligible, fresh_until,
                    payload_json, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    str(tenant_id),
                    str(user_id),
                    str(actor_id),
                    SNAPSHOT_SCHEMA_VERSION,
                    str(holdings_sha256),
                    status,
                    str(payload.get("base_currency") or "CNY"),
                    summary.get("total_value"),
                    float(coverage.get("count_coverage_pct") or 0),
                    1 if gate.get("risk_analysis_eligible") else 0,
                    payload.get("fresh_until"),
                    payload_text,
                    sha256_text(payload_text),
                    created_at,
                ),
            )
        created = self.get_snapshot(
            snapshot_id, tenant_id=tenant_id, user_id=user_id
        )
        if created is None:
            raise PortfolioValuationRepositoryError("created valuation snapshot disappeared")
        return created

    def get_snapshot(
        self,
        snapshot_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_valuation_snapshots
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (str(snapshot_id), str(tenant_id), str(user_id)),
            ).fetchone()
        return self._snapshot_from_row(row)

    def latest_snapshot(
        self,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portfolio_valuation_snapshots
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (str(tenant_id), str(user_id)),
            ).fetchone()
        return self._snapshot_from_row(row)

    def list_snapshots(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM portfolio_valuation_snapshots
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (str(tenant_id), str(user_id), max(1, min(100, int(limit)))),
            ).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def verify_snapshot(
        self,
        snapshot_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        item = self.get_snapshot(
            snapshot_id, tenant_id=tenant_id, user_id=user_id
        )
        if item is None:
            raise PortfolioValuationNotFoundError("组合估值快照不存在")
        return {"snapshot_id": snapshot_id, **item["integrity"]}
