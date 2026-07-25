# -*- coding: utf-8 -*-
"""Immutable bridge from quant-selection mandates to forward evidence.

The bridge deliberately stores only provenance and identifiers.  Forward
returns continue to live in the existing Opportunity Factory paper-observation
ledger, so the profit lab and investment committee consume one evidence model
instead of parallel, inconsistent implementations.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from database import (
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
)


LINK_SCHEMA_VERSION = "quant_selection_forward_validation.v1"
REQUIRED_TABLES = {
    "quant_selection_runs",
    "quant_selection_shadow_mandates",
    "quant_selection_forward_validations",
    "opportunity_strategies",
    "opportunity_strategy_versions",
    "opportunity_runs",
    "opportunity_run_events",
    "opportunity_paper_baskets",
    "opportunity_profit_policy_versions",
}


class QuantSelectionForwardRepositoryError(RuntimeError):
    pass


class QuantSelectionForwardNotFoundError(
    QuantSelectionForwardRepositoryError
):
    pass


class QuantSelectionForwardConflictError(
    QuantSelectionForwardRepositoryError
):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{sha256_text(canonical_json(value))[:32]}"


def _load(value: Any, fallback: Any) -> Any:
    if value in {None, ""}:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc).isoformat(
        timespec="milliseconds"
    )


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS quant_selection_forward_validations (
    id                              TEXT PRIMARY KEY,
    tenant_id                       TEXT NOT NULL,
    user_id                         TEXT NOT NULL,
    actor_id                        TEXT NOT NULL,
    quant_mandate_id                TEXT NOT NULL
        REFERENCES quant_selection_shadow_mandates(id) ON DELETE RESTRICT,
    quant_run_id                    TEXT NOT NULL
        REFERENCES quant_selection_runs(id) ON DELETE RESTRICT,
    quant_snapshot_sha256           TEXT NOT NULL,
    strategy_fingerprint            TEXT NOT NULL,
    opportunity_strategy_id         TEXT NOT NULL
        REFERENCES opportunity_strategies(id) ON DELETE RESTRICT,
    opportunity_strategy_version_id TEXT NOT NULL
        REFERENCES opportunity_strategy_versions(id) ON DELETE RESTRICT,
    opportunity_run_id              TEXT NOT NULL
        REFERENCES opportunity_runs(id) ON DELETE RESTRICT,
    opportunity_basket_id           TEXT NOT NULL
        REFERENCES opportunity_paper_baskets(id) ON DELETE RESTRICT,
    profit_policy_id                TEXT NOT NULL
        REFERENCES opportunity_profit_policy_versions(id) ON DELETE RESTRICT,
    schema_version                  TEXT NOT NULL,
    payload_json                    TEXT NOT NULL,
    payload_sha256                  TEXT NOT NULL,
    created_at                      TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, quant_mandate_id),
    UNIQUE(opportunity_basket_id)
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_forward_scope
ON quant_selection_forward_validations(
    tenant_id, user_id, created_at DESC, id DESC
);
CREATE INDEX IF NOT EXISTS idx_quant_selection_forward_strategy
ON quant_selection_forward_validations(
    user_id, opportunity_strategy_id, created_at DESC
);
CREATE TRIGGER IF NOT EXISTS trg_quant_selection_forward_no_update
BEFORE UPDATE ON quant_selection_forward_validations BEGIN
    SELECT RAISE(ABORT, 'quant selection forward validations are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_quant_selection_forward_no_delete
BEFORE DELETE ON quant_selection_forward_validations BEGIN
    SELECT RAISE(ABORT, 'quant selection forward validations are immutable');
END;
"""


class QuantSelectionForwardRepository:
    def __init__(
        self,
        database_target: str | os.PathLike[str] | None = None,
    ) -> None:
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
            with connect_database(
                self.database_target, close_on_exit=True
            ) as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(connection, REQUIRED_TABLES)
                else:
                    connection.executescript(SQLITE_SCHEMA)
            self._schema_ready = True

    def _connect(self):
        self._ensure_schema()
        return connect_database(
            self.database_target, close_on_exit=True
        )

    @staticmethod
    def _link_from_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        payload_json = str(item.pop("payload_json", ""))
        item["payload"] = _load(payload_json, {})
        item["integrity"] = {
            "verified": bool(
                item.get("schema_version") == LINK_SCHEMA_VERSION
                and sha256_text(payload_json)
                == item.get("payload_sha256")
            )
        }
        return item

    @staticmethod
    def _insert_once(
        connection,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> None:
        connection.execute(f"{sql} ON CONFLICT DO NOTHING", parameters)

    @staticmethod
    def _event(
        *,
        event_id: str,
        run_id: str,
        sequence_no: int,
        event_type: str,
        actor_id: str,
        details: dict[str, Any],
        previous_hash: str | None,
        created_at: str,
    ) -> tuple[dict[str, Any], str]:
        payload = {
            "id": event_id,
            "run_id": run_id,
            "sequence_no": int(sequence_no),
            "event_type": event_type,
            "actor_id": actor_id,
            "details": details,
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        return payload, sha256_text(canonical_json(payload))

    def create_validation(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        mandate_id: str,
        quant_run_id: str,
        quant_snapshot_sha256: str,
        strategy_fingerprint: str,
        strategy_id: str,
        strategy_version_id: str,
        opportunity_run_id: str,
        basket_id: str,
        profit_policy_id: str,
        definition: dict[str, Any],
        profit_policy: dict[str, Any],
        run_result: dict[str, Any],
        basket_snapshot: dict[str, Any],
        link_payload: dict[str, Any],
        source_created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        definition_json = canonical_json(definition)
        definition_sha = sha256_text(definition_json)
        policy_json = canonical_json(profit_policy)
        policy_sha = sha256_text(policy_json)
        result_json = canonical_json(run_result)
        result_sha = sha256_text(result_json)
        basket_json = canonical_json(basket_snapshot)
        basket_sha = sha256_text(basket_json)
        link_json = canonical_json(link_payload)
        link_sha = sha256_text(link_json)
        link_id = stable_id(
            "qsel_forward",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "mandate_id": mandate_id,
            },
        )
        created_at = _iso(
            dt.datetime.fromisoformat(
                str(source_created_at).replace("Z", "+00:00")
            )
        )
        run_created_event_id = stable_id(
            "opp_evt", {"run_id": opportunity_run_id, "sequence": 1}
        )
        run_completed_event_id = stable_id(
            "opp_evt", {"run_id": opportunity_run_id, "sequence": 2}
        )
        created_details = {
            "source": "quant_selection_shadow_mandate",
            "quant_mandate_id": mandate_id,
            "strategy_version_id": strategy_version_id,
            "strategy_sha256": definition_sha,
        }
        created_event, created_event_hash = self._event(
            event_id=run_created_event_id,
            run_id=opportunity_run_id,
            sequence_no=1,
            event_type="run.imported",
            actor_id=actor_id,
            details=created_details,
            previous_hash=None,
            created_at=created_at,
        )
        completed_details = {
            "status": "succeeded",
            "result_sha256": result_sha,
            "quant_snapshot_sha256": quant_snapshot_sha256,
        }
        completed_event, completed_event_hash = self._event(
            event_id=run_completed_event_id,
            run_id=opportunity_run_id,
            sequence_no=2,
            event_type="run.completed",
            actor_id=actor_id,
            details=completed_details,
            previous_hash=created_event_hash,
            created_at=created_at,
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                """
                SELECT m.snapshot_sha256, m.run_id, r.result_sha256
                FROM quant_selection_shadow_mandates m
                JOIN quant_selection_runs r ON r.id=m.run_id
                WHERE m.id=? AND m.tenant_id=? AND m.user_id=?
                  AND r.tenant_id=? AND r.user_id=?
                """,
                (
                    mandate_id,
                    tenant_id,
                    user_id,
                    tenant_id,
                    user_id,
                ),
            ).fetchone()
            if source is None:
                raise QuantSelectionForwardNotFoundError(
                    "量化纸面指令不存在"
                )
            if (
                str(source["run_id"]) != quant_run_id
                or str(source["snapshot_sha256"])
                != quant_snapshot_sha256
                or str(source["result_sha256"])
                != str(link_payload.get("quant_result_sha256") or "")
            ):
                raise QuantSelectionForwardConflictError(
                    "量化纸面指令来源摘要已经变化"
                )
            existing = connection.execute(
                """
                SELECT * FROM quant_selection_forward_validations
                WHERE tenant_id=? AND user_id=? AND quant_mandate_id=?
                """,
                (tenant_id, user_id, mandate_id),
            ).fetchone()
            if existing is not None:
                item = self._link_from_row(existing)
                if item is None or not item["integrity"]["verified"]:
                    raise QuantSelectionForwardConflictError(
                        "既有前向验证映射完整性失败"
                    )
                return item, False

            self._insert_once(
                connection,
                """
                INSERT INTO opportunity_strategies(
                    id, user_id, status, current_version_no,
                    created_at, updated_at
                ) VALUES (?, ?, 'active', 1, ?, ?)
                """,
                (
                    strategy_id,
                    user_id,
                    created_at,
                    created_at,
                ),
            )
            strategy = connection.execute(
                """
                SELECT user_id, status, current_version_no
                FROM opportunity_strategies WHERE id=?
                """,
                (strategy_id,),
            ).fetchone()
            if (
                strategy is None
                or str(strategy["user_id"]) != user_id
                or str(strategy["status"]) != "active"
                or int(strategy["current_version_no"]) != 1
            ):
                raise QuantSelectionForwardConflictError(
                    "量化前向策略身份与既有记录冲突"
                )

            self._insert_once(
                connection,
                """
                INSERT INTO opportunity_strategy_versions(
                    id, strategy_id, user_id, version_no, schema_version,
                    definition_json, definition_sha256, actor_id, created_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_version_id,
                    strategy_id,
                    user_id,
                    str(definition.get("schema_version") or ""),
                    definition_json,
                    definition_sha,
                    actor_id,
                    created_at,
                ),
            )
            version = connection.execute(
                """
                SELECT definition_sha256 FROM opportunity_strategy_versions
                WHERE id=? AND strategy_id=? AND user_id=?
                """,
                (strategy_version_id, strategy_id, user_id),
            ).fetchone()
            if (
                version is None
                or str(version["definition_sha256"]) != definition_sha
            ):
                raise QuantSelectionForwardConflictError(
                    "量化前向策略定义与既有记录冲突"
                )

            self._insert_once(
                connection,
                """
                INSERT INTO opportunity_profit_policy_versions(
                    id, user_id, strategy_id, strategy_version_id,
                    version_no, schema_version, payload_json,
                    payload_sha256, actor_id, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    profit_policy_id,
                    user_id,
                    strategy_id,
                    strategy_version_id,
                    str(profit_policy.get("schema_version") or ""),
                    policy_json,
                    policy_sha,
                    actor_id,
                    created_at,
                ),
            )
            policy = connection.execute(
                """
                SELECT payload_sha256 FROM opportunity_profit_policy_versions
                WHERE id=? AND user_id=?
                """,
                (profit_policy_id, user_id),
            ).fetchone()
            if (
                policy is None
                or str(policy["payload_sha256"]) != policy_sha
            ):
                raise QuantSelectionForwardConflictError(
                    "量化前向收益政策与既有记录冲突"
                )

            self._insert_once(
                connection,
                """
                INSERT INTO opportunity_runs(
                    id, user_id, strategy_id, strategy_version_id,
                    strategy_version_no, strategy_sha256, status, job_id,
                    progress_json, result_json, result_sha256,
                    error_code, error_message, created_at, started_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, 1, ?, 'succeeded', NULL, ?, ?, ?,
                          NULL, NULL, ?, ?, ?)
                """,
                (
                    opportunity_run_id,
                    user_id,
                    strategy_id,
                    strategy_version_id,
                    definition_sha,
                    canonical_json(
                        {
                            "stage": "completed",
                            "completed": 1,
                            "total": 1,
                            "message": "量化纸面指令已接入前向验证",
                        }
                    ),
                    result_json,
                    result_sha,
                    created_at,
                    created_at,
                    created_at,
                ),
            )
            imported_run = connection.execute(
                """
                SELECT result_sha256 FROM opportunity_runs
                WHERE id=? AND user_id=?
                """,
                (opportunity_run_id, user_id),
            ).fetchone()
            if (
                imported_run is None
                or str(imported_run["result_sha256"]) != result_sha
            ):
                raise QuantSelectionForwardConflictError(
                    "量化前向运行记录与既有记录冲突"
                )

            for event, event_hash in (
                (created_event, created_event_hash),
                (completed_event, completed_event_hash),
            ):
                self._insert_once(
                    connection,
                    """
                    INSERT INTO opportunity_run_events(
                        id, run_id, sequence_no, event_type, actor_id,
                        details_json, previous_hash, event_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["id"],
                        event["run_id"],
                        event["sequence_no"],
                        event["event_type"],
                        event["actor_id"],
                        canonical_json(event["details"]),
                        event["previous_hash"],
                        event_hash,
                        event["created_at"],
                    ),
                )

            self._insert_once(
                connection,
                """
                INSERT INTO opportunity_paper_baskets(
                    id, user_id, run_id, schema_version, snapshot_json,
                    snapshot_sha256, created_at
                ) VALUES (?, ?, ?, 'opportunity_paper_basket.v1',
                          ?, ?, ?)
                """,
                (
                    basket_id,
                    user_id,
                    opportunity_run_id,
                    basket_json,
                    basket_sha,
                    created_at,
                ),
            )
            basket = connection.execute(
                """
                SELECT snapshot_sha256 FROM opportunity_paper_baskets
                WHERE id=? AND user_id=?
                """,
                (basket_id, user_id),
            ).fetchone()
            if (
                basket is None
                or str(basket["snapshot_sha256"]) != basket_sha
            ):
                raise QuantSelectionForwardConflictError(
                    "量化前向纸面组合与既有记录冲突"
                )

            connection.execute(
                """
                INSERT INTO quant_selection_forward_validations(
                    id, tenant_id, user_id, actor_id, quant_mandate_id,
                    quant_run_id, quant_snapshot_sha256,
                    strategy_fingerprint, opportunity_strategy_id,
                    opportunity_strategy_version_id, opportunity_run_id,
                    opportunity_basket_id, profit_policy_id,
                    schema_version, payload_json, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    tenant_id,
                    user_id,
                    actor_id,
                    mandate_id,
                    quant_run_id,
                    quant_snapshot_sha256,
                    strategy_fingerprint,
                    strategy_id,
                    strategy_version_id,
                    opportunity_run_id,
                    basket_id,
                    profit_policy_id,
                    LINK_SCHEMA_VERSION,
                    link_json,
                    link_sha,
                    created_at,
                ),
            )
            saved = connection.execute(
                """
                SELECT * FROM quant_selection_forward_validations
                WHERE id=?
                """,
                (link_id,),
            ).fetchone()
        item = self._link_from_row(saved)
        if item is None:
            raise QuantSelectionForwardRepositoryError(
                "前向验证映射保存后不可读取"
            )
        return item, True

    def get_validation(
        self,
        validation_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM quant_selection_forward_validations
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (validation_id, tenant_id, user_id),
            ).fetchone()
        return self._link_from_row(row)

    def get_by_mandate(
        self,
        mandate_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM quant_selection_forward_validations
                WHERE quant_mandate_id=? AND tenant_id=? AND user_id=?
                """,
                (mandate_id, tenant_id, user_id),
            ).fetchone()
        return self._link_from_row(row)

    def list_validations(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM quant_selection_forward_validations
                WHERE tenant_id=? AND user_id=?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (
                    tenant_id,
                    user_id,
                    max(1, min(200, int(limit))),
                ),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self._link_from_row(row)) is not None
        ]


repository = QuantSelectionForwardRepository()
