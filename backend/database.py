# -*- coding: utf-8 -*-
"""Database compatibility and PostgreSQL connection management.

SQLite remains available for isolated tests and offline migration input. A
configured PostgreSQL URL is always authoritative in production; failures are
surfaced and never fall back to the local SQLite file.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import atexit
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


POSTGRES_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg://")
_LEGACY_WRITE_LOCK_ID = 7_318_642_019_384_117
_POOL_LOCK = threading.Lock()
_POOLS: dict[str, Any] = {}


class DatabaseConfigurationError(RuntimeError):
    """Raised when the configured database cannot be used safely."""


class DatabaseSchemaError(DatabaseConfigurationError):
    """Raised when a PostgreSQL database has not been migrated."""


class _ClosingSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def configured_database_target(default_sqlite_path: str) -> str:
    return str(
        os.getenv("STOCK_ASSISTANT_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("STOCK_ASSISTANT_DB_PATH")
        or os.getenv("AGENT_DB_PATH")
        or default_sqlite_path
    )


def is_postgres_target(target: str | os.PathLike[str]) -> bool:
    value = str(target or "").strip().lower()
    return value.startswith(POSTGRES_SCHEMES)


def database_dialect(target_or_connection: Any) -> str:
    explicit = getattr(target_or_connection, "dialect", None)
    if explicit:
        return str(explicit)
    return "postgresql" if is_postgres_target(str(target_or_connection)) else "sqlite"


def redact_database_url(target: str) -> str:
    if not is_postgres_target(target):
        return target
    normalized = _normalize_postgres_url(target)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    username = parsed.username or ""
    netloc = f"{username}:***@{hostname}" if username else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _normalize_postgres_url(target: str) -> str:
    value = str(target).strip()
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgres://"):
        return "postgresql://" + value.removeprefix("postgres://")
    return value


def _postgres_modules():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as error:
        raise DatabaseConfigurationError(
            "PostgreSQL 已配置，但缺少 psycopg 驱动；请安装 backend/requirements.txt"
        ) from error
    return psycopg, dict_row, ConnectionPool


def _pool_for(target: str):
    normalized = _normalize_postgres_url(target)
    with _POOL_LOCK:
        pool = _POOLS.get(normalized)
        if pool is not None:
            return pool
        _, dict_row, connection_pool = _postgres_modules()
        minimum = max(1, int(os.getenv("DB_POOL_MIN_SIZE", "1")))
        maximum = max(minimum, int(os.getenv("DB_POOL_MAX_SIZE", "8")))
        timeout = max(1.0, float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "10")))
        pool = connection_pool(
            conninfo=normalized,
            min_size=minimum,
            max_size=maximum,
            timeout=timeout,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": max(2, int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5"))),
                "application_name": os.getenv(
                    "DB_APPLICATION_NAME", "stock-assistant"
                ),
            },
            open=False,
        )
        pool.open(wait=True, timeout=timeout)
        _POOLS[normalized] = pool
        return pool


def close_database_pools() -> None:
    with _POOL_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        try:
            pool.close(timeout=5.0)
        except Exception:
            # Interpreter shutdown must not hide the original process result.
            pass


atexit.register(close_database_pools)


class CompatRow(Mapping[str, Any]):
    """A psycopg row with sqlite.Row-compatible key and index access."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)
        self._keys = tuple(self._values)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[self._keys[key]]
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self):
        return self._values.keys()


class PostgresCursorAdapter:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @staticmethod
    def _adapt(row: Any) -> CompatRow | None:
        if row is None:
            return None
        return CompatRow(row)

    def fetchone(self) -> CompatRow | None:
        return self._adapt(self._cursor.fetchone())

    def fetchall(self) -> list[CompatRow]:
        return [CompatRow(row) for row in self._cursor.fetchall()]

    def fetchmany(self, size: int | None = None) -> list[CompatRow]:
        rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
        return [CompatRow(row) for row in rows]

    def __iter__(self):
        for row in self._cursor:
            yield CompatRow(row)

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        raise DatabaseConfigurationError(
            "PostgreSQL 不支持 lastrowid；写入语句必须显式使用 RETURNING"
        )


def _replace_qmark_placeholders(sql: str) -> str:
    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            output.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            output.append(char)
        elif char == "?":
            output.append("%s")
        else:
            output.append(char)
        index += 1
    return "".join(output)


class PostgresConnectionAdapter:
    dialect = "postgresql"

    def __init__(self, raw_connection: Any, lease: Any) -> None:
        self._connection = raw_connection
        self._lease = lease
        self._released = False

    @property
    def closed(self) -> bool:
        return self._released or bool(self._connection.closed)

    def execute(self, sql: str, parameters: Any = None) -> PostgresCursorAdapter:
        statement = str(sql).strip()
        cursor = self._connection.cursor()
        if re.fullmatch(r"BEGIN\s+IMMEDIATE;?", statement, flags=re.IGNORECASE):
            cursor.execute("BEGIN")
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_LEGACY_WRITE_LOCK_ID,)
            )
            return PostgresCursorAdapter(cursor)
        cursor.execute(
            _replace_qmark_placeholders(str(sql)),
            () if parameters is None else parameters,
        )
        return PostgresCursorAdapter(cursor)

    def executemany(self, sql: str, parameters: Any) -> PostgresCursorAdapter:
        cursor = self._connection.cursor()
        cursor.executemany(_replace_qmark_placeholders(str(sql)), parameters)
        return PostgresCursorAdapter(cursor)

    def executescript(self, _script: str):
        raise DatabaseSchemaError(
            "禁止在 PostgreSQL 运行时自动执行 SQLite DDL；请先运行数据库迁移"
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        if self._released:
            return
        self._lease.__exit__(None, None, None)
        self._released = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._released:
            return False
        self._released = True
        return self._lease.__exit__(exc_type, exc_value, traceback)


def connect_database(
    target: str | os.PathLike[str],
    *,
    close_on_exit: bool = True,
):
    value = str(target)
    if is_postgres_target(value):
        pool = _pool_for(value)
        lease = pool.connection()
        raw_connection = lease.__enter__()
        return PostgresConnectionAdapter(raw_connection, lease)

    factory = _ClosingSQLiteConnection if close_on_exit else sqlite3.Connection
    connection = sqlite3.connect(
        value,
        timeout=30,
        check_same_thread=False,
        factory=factory,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def connection_is_closed(connection: Any) -> bool:
    if connection is None:
        return True
    closed = getattr(connection, "closed", False)
    return bool(closed)


def table_exists(connection: Any, table: str) -> bool:
    if database_dialect(connection) == "postgresql":
        row = connection.execute("SELECT to_regclass(?) AS name", (table,)).fetchone()
        return bool(row and row["name"])
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def list_database_tables(connection: Any) -> set[str]:
    if database_dialect(connection) == "postgresql":
        rows = connection.execute(
            """
            SELECT tablename AS name
            FROM pg_catalog.pg_tables
            WHERE schemaname=current_schema()
            """
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {str(row["name"]) for row in rows}


def require_database_schema(connection: Any, required_tables: set[str]) -> None:
    available = list_database_tables(connection)
    missing = sorted(required_tables - available)
    if missing:
        raise DatabaseSchemaError(
            "PostgreSQL 架构尚未完成迁移，缺少表：" + ", ".join(missing)
        )


def json_text_expression(target_or_connection: Any, column: str, *path: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", column):
        raise ValueError("invalid JSON column")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in path):
        raise ValueError("invalid JSON path")
    if database_dialect(target_or_connection) == "postgresql":
        args = ", ".join("'" + item + "'" for item in path)
        return f"jsonb_extract_path_text({column}::jsonb, {args})"
    json_path = "$." + ".".join(path)
    return f"json_extract({column}, '{json_path}')"


try:
    import psycopg as _psycopg

    INTEGRITY_ERRORS = (sqlite3.IntegrityError, _psycopg.IntegrityError)
except ImportError:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
