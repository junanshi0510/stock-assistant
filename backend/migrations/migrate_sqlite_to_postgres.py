# -*- coding: utf-8 -*-
"""Offline, verified migration from the production SQLite file to PostgreSQL."""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Float, Integer, MetaData, create_engine, inspect, select, text

from database import is_postgres_target
from migrations.postgres_schema import install_postgres_runtime_schema


MIGRATION_ID = "sqlite-to-postgres.v1"
EXPECTED_SOURCE_TABLES = {
    "agent_audit_events",
    "agent_batch_allocation_events",
    "agent_batch_items",
    "agent_batch_purchase_attribution_snapshots",
    "agent_batch_purchase_execution_events",
    "agent_batch_purchase_preflight_events",
    "agent_batch_purchase_transaction_bindings",
    "agent_batches",
    "agent_claims",
    "agent_evidence",
    "agent_outcome_schedules",
    "agent_runs",
    "agent_steps",
    "agent_strategy_audit_events",
    "agent_strategy_shadow_cohorts",
    "agent_strategy_shadow_enrollments",
    "agent_strategy_versions",
    "alerts",
    "auth_audit_events",
    "auth_login_attempts",
    "auth_registration_attempts",
    "auth_sessions",
    "auth_users",
    "decision_check_events",
    "decision_check_schedules",
    "decision_task_events",
    "decision_tasks",
    "fund_switch_cost_reviews",
    "fund_switch_execution_reviews",
    "fund_switch_lifecycle_events",
    "fund_switch_quote_events",
    "holding_thesis_versions",
    "holdings",
    "investment_profile_audit_events",
    "investment_profile_versions",
    "investment_profiles",
    "portfolio_action_reports",
    "portfolio_exposure_snapshots",
    "portfolio_imports",
    "portfolio_snapshots",
    "portfolio_transactions",
    "storage_schema_migrations",
    "user_alerts",
    "user_watchlist",
    "watchlist",
}
ALLOWED_EXISTING_TARGET_TABLES = set()
MIGRATION_LOCK_ID = 8_201_027_556_114_219


def _postgres_sqlalchemy_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return value
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    return "postgresql+psycopg://" + value.removeprefix("postgresql://")


def _source_fingerprint(path: Path) -> dict[str, Any]:
    # The shared-memory file can change during a read-only connection. The
    # database and WAL files are the durable inputs whose changes indicate a write.
    files = [path, Path(str(path) + "-wal")]
    result: dict[str, Any] = {}
    for item in files:
        if item.exists():
            stat = item.stat()
            result[item.name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


def _create_snapshot(source: Path, snapshot_dir: Path) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = snapshot_dir / f"{source.stem}-{timestamp}.sqlite3"
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.execute("PRAGMA busy_timeout=30000")
        source_connection.backup(target_connection)
        check = target_connection.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"SQLite 快照完整性检查失败: {check}")
    finally:
        target_connection.close()
        source_connection.close()
    return target


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return {"__float__": format(value, ".15g")}
    if isinstance(value, decimal.Decimal):
        return {"__decimal__": format(value.normalize(), "f")}
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "length": len(value)}
    return value


def _table_digest(connection, table) -> tuple[int, str]:
    primary_keys = list(table.primary_key.columns)
    order = primary_keys or list(table.columns)
    statement = select(table)
    if order:
        statement = statement.order_by(*order)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(statement):
        payload = {
            column.name: _normalize_value(row._mapping[column.name])
            for column in table.columns
        }
        digest.update(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def _prepare_metadata(source_engine) -> MetaData:
    metadata = MetaData()
    metadata.reflect(bind=source_engine)
    missing = EXPECTED_SOURCE_TABLES - set(metadata.tables)
    if missing:
        raise RuntimeError("SQLite 源库缺少业务表: " + ", ".join(sorted(missing)))

    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Float):
                column.type = Float(precision=53)
        for constraint in table.foreign_key_constraints:
            constraint.deferrable = True
            constraint.initially = "DEFERRED"
        for index in table.indexes:
            sqlite_where = index.dialect_options["sqlite"].get("where")
            if sqlite_where is not None:
                index.dialect_options["postgresql"]["where"] = sqlite_where
    return metadata


def _copy_table(source_connection, target_connection, table, batch_size: int) -> int:
    primary_keys = list(table.primary_key.columns)
    statement = select(table)
    if primary_keys:
        statement = statement.order_by(*primary_keys)
    result = source_connection.execution_options(stream_results=True).execute(statement)
    copied = 0
    while True:
        rows = result.fetchmany(batch_size)
        if not rows:
            break
        payload = [dict(row._mapping) for row in rows]
        target_connection.execute(table.insert(), payload)
        copied += len(payload)
    return copied


def _reset_sequences(connection, metadata: MetaData) -> None:
    for table in metadata.sorted_tables:
        for column in table.primary_key.columns:
            if isinstance(column.type, Integer):
                sequence = connection.execute(
                    text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                    {"table_name": table.name, "column_name": column.name},
                ).scalar_one_or_none()
                if not sequence:
                    continue
                maximum = connection.execute(
                    text(
                        f'SELECT MAX("{column.name}") FROM "{table.name}"'
                    )
                ).scalar_one_or_none()
                if maximum is None:
                    connection.execute(
                        text("SELECT setval(:sequence, 1, false)"),
                        {"sequence": sequence},
                    )
                else:
                    connection.execute(
                        text("SELECT setval(:sequence, :value, true)"),
                        {"sequence": sequence, "value": int(maximum)},
                    )


def migrate(
    *,
    sqlite_path: Path,
    database_url: str,
    snapshot_dir: Path,
    report_path: Path,
    batch_size: int = 500,
) -> dict[str, Any]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite 文件不存在: {sqlite_path}")
    if not is_postgres_target(database_url):
        raise ValueError("目标必须是 PostgreSQL DATABASE_URL")

    report: dict[str, Any] = {
        "migration_id": MIGRATION_ID,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "source": str(sqlite_path.resolve()),
        "snapshot": None,
        "tables": {},
        "status": "running",
    }
    source_engine = None
    target_engine = None

    try:
        source_before = _source_fingerprint(sqlite_path)
        snapshot_path = _create_snapshot(sqlite_path, snapshot_dir)
        report["snapshot"] = str(snapshot_path.resolve())
        source_after_snapshot = _source_fingerprint(sqlite_path)
        if source_after_snapshot != source_before:
            raise RuntimeError("生成 SQLite 快照期间源库发生写入，迁移尚未开始")
        source_engine = create_engine(
            "sqlite+pysqlite:///" + snapshot_path.resolve().as_posix(),
            future=True,
        )
        target_engine = create_engine(
            _postgres_sqlalchemy_url(database_url),
            future=True,
            pool_pre_ping=True,
        )
        metadata = _prepare_metadata(source_engine)
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            target_connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            existing_tables = set(inspect(target_connection).get_table_names())
            unexpected = existing_tables - ALLOWED_EXISTING_TARGET_TABLES
            if unexpected:
                raise RuntimeError(
                    "目标 PostgreSQL 不是空库，拒绝覆盖: " + ", ".join(sorted(unexpected))
                )

            metadata.create_all(bind=target_connection)
            target_connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            for table in metadata.sorted_tables:
                copied = _copy_table(
                    source_connection,
                    target_connection,
                    table,
                    max(1, int(batch_size)),
                )
                report["tables"][table.name] = {"copied": copied}

            _reset_sequences(target_connection, metadata)
            install_postgres_runtime_schema(target_connection)

            for table in metadata.sorted_tables:
                source_count, source_digest = _table_digest(source_connection, table)
                target_count, target_digest = _table_digest(target_connection, table)
                item = report["tables"][table.name]
                item.update(
                    {
                        "source_count": source_count,
                        "target_count": target_count,
                        "source_sha256": source_digest,
                        "target_sha256": target_digest,
                        "verified": source_count == target_count
                        and source_digest == target_digest,
                    }
                )
                if not item["verified"]:
                    raise RuntimeError(f"表 {table.name} 的行数或内容摘要不一致")

            source_after = _source_fingerprint(sqlite_path)
            if source_after != source_before:
                raise RuntimeError("迁移期间 SQLite 源库发生写入，已回滚 PostgreSQL 事务")

            target_connection.execute(
                text(
                    """
                    INSERT INTO platform_schema_migrations(migration_id, checksum, applied_at)
                    VALUES (:migration_id, :checksum, :applied_at)
                    ON CONFLICT (migration_id) DO UPDATE
                    SET checksum=EXCLUDED.checksum, applied_at=EXCLUDED.applied_at
                    """
                ),
                {
                    "migration_id": MIGRATION_ID,
                    "checksum": hashlib.sha256(
                        json.dumps(report["tables"], sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(
                        timespec="milliseconds"
                    ),
                },
            )

        report["status"] = "verified"
        report["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="milliseconds"
        )
        return report
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if source_engine is not None:
            source_engine.dispose()
        if target_engine is not None:
            target_engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verified offline migration from SQLite to PostgreSQL"
    )
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument(
        "--database-url",
        default=os.getenv("STOCK_ASSISTANT_DATABASE_URL") or os.getenv("DATABASE_URL"),
    )
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url 或 DATABASE_URL 必须配置")
    try:
        result = migrate(
            sqlite_path=args.sqlite,
            database_url=args.database_url,
            snapshot_dir=args.snapshot_dir,
            report_path=args.report,
            batch_size=args.batch_size,
        )
    except Exception as error:
        print(f"migration failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "table_count": len(result["tables"]),
                "snapshot": result["snapshot"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
