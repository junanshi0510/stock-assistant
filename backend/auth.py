# -*- coding: utf-8 -*-
"""Server-side authentication, sessions, RBAC, and tamper-evident auth audit."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

from database import (
    INTEGRITY_ERRORS,
    configured_database_target,
    connect_database,
    database_dialect,
    require_database_schema,
    table_exists,
)


ROLE_ADMIN = "admin"
ROLE_USER = "user"
USER_ACTIVE = "active"
USER_DISABLED = "disabled"
SESSION_COOKIE_NAME = "stock_assistant_session"
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_COMMON_PASSWORDS = {
    "123456789012",
    "admin123456",
    "password1234",
    "qwerty123456",
    "stockassistant",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="milliseconds")


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _default_db_path() -> str:
    return configured_database_target(
        str(Path(__file__).resolve().parent / "stock_assistant.db")
    )


@dataclass(frozen=True)
class AuthSettings:
    required: bool
    cookie_secure: bool
    session_hours: int
    idle_minutes: int
    login_limit: int
    login_window_minutes: int
    self_registration_enabled: bool
    registration_limit: int
    registration_window_minutes: int
    audit_pepper: str
    trust_proxy: bool

    @classmethod
    def from_environment(cls) -> "AuthSettings":
        return cls(
            required=_env_bool("AUTH_REQUIRED", False),
            cookie_secure=_env_bool("AUTH_COOKIE_SECURE", False),
            session_hours=_bounded_int("AUTH_SESSION_HOURS", 12, 1, 168),
            idle_minutes=_bounded_int("AUTH_IDLE_MINUTES", 120, 15, 1440),
            login_limit=_bounded_int("AUTH_LOGIN_LIMIT", 5, 3, 20),
            login_window_minutes=_bounded_int("AUTH_LOGIN_WINDOW_MINUTES", 15, 5, 120),
            self_registration_enabled=_env_bool("AUTH_SELF_REGISTRATION_ENABLED", True),
            registration_limit=_bounded_int("AUTH_REGISTRATION_LIMIT", 5, 1, 20),
            registration_window_minutes=_bounded_int(
                "AUTH_REGISTRATION_WINDOW_MINUTES", 60, 5, 1440
            ),
            audit_pepper=str(os.getenv("AUTH_AUDIT_PEPPER") or "").strip(),
            trust_proxy=_env_bool("AUTH_TRUST_PROXY", False),
        )

    @property
    def configuration_ready(self) -> bool:
        return not self.required or len(self.audit_pepper) >= 32


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: str
    subject_id: str
    username: str
    display_name: str
    role: str
    must_change_password: bool
    session_id: str | None
    auth_disabled: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "must_change_password": self.must_change_password,
        }


LEGACY_PRINCIPAL = AuthPrincipal(
    user_id="legacy-admin",
    subject_id="default",
    username="legacy-admin",
    display_name="本地开发管理员",
    role=ROLE_ADMIN,
    must_change_password=False,
    session_id=None,
    auth_disabled=True,
)


class AuthError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class AuthService:
    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        settings: AuthSettings | None = None,
    ) -> None:
        self.db_path = str(db_path or _default_db_path())
        self.settings = settings or AuthSettings.from_environment()
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        # OWASP's minimum Argon2id profile: 19 MiB, two iterations, one lane.
        self._password_hasher = PasswordHasher(
            time_cost=2,
            memory_cost=19_456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        self._dummy_password_hash = self._password_hasher.hash(
            secrets.token_urlsafe(24)
        )
        self.ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        connection = connect_database(self.db_path, close_on_exit=True)
        if database_dialect(connection) == "sqlite":
            connection.execute("PRAGMA journal_mode=WAL")
        try:
            with connection:
                yield connection
        finally:
            if not getattr(connection, "closed", False):
                connection.close()

    @staticmethod
    def _table_exists(connection: Any, table: str) -> bool:
        return table_exists(connection, table)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as connection:
                if database_dialect(connection) == "postgresql":
                    require_database_schema(
                        connection,
                        {
                            "auth_users",
                            "auth_sessions",
                            "auth_login_attempts",
                            "auth_registration_attempts",
                            "auth_audit_events",
                        },
                    )
                    self._schema_ready = True
                    return
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS auth_users (
                        id                   TEXT PRIMARY KEY,
                        subject_id           TEXT NOT NULL UNIQUE,
                        username             TEXT NOT NULL,
                        username_normalized  TEXT NOT NULL UNIQUE,
                        display_name         TEXT NOT NULL,
                        password_hash        TEXT NOT NULL,
                        role                 TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                        status               TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
                        must_change_password INTEGER NOT NULL DEFAULT 1,
                        created_at           TEXT NOT NULL,
                        updated_at           TEXT NOT NULL,
                        password_changed_at  TEXT,
                        last_login_at        TEXT,
                        created_by           TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_users_status_role
                    ON auth_users(status, role);

                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        id          TEXT PRIMARY KEY,
                        user_id     TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                        token_hash  TEXT NOT NULL UNIQUE,
                        csrf_hash   TEXT NOT NULL,
                        created_at  TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        expires_at  TEXT NOT NULL,
                        revoked_at  TEXT,
                        revoke_reason TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active
                    ON auth_sessions(user_id, revoked_at, expires_at);

                    CREATE TABLE IF NOT EXISTS auth_login_attempts (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        username_hash   TEXT NOT NULL,
                        client_hash     TEXT NOT NULL,
                        attempted_at    TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_window
                    ON auth_login_attempts(attempted_at, username_hash, client_hash);

                    CREATE TABLE IF NOT EXISTS auth_registration_attempts (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_hash  TEXT NOT NULL,
                        attempted_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_registration_attempts_window
                    ON auth_registration_attempts(attempted_at, client_hash);

                    CREATE TABLE IF NOT EXISTS auth_audit_events (
                        id              TEXT PRIMARY KEY,
                        sequence_no     INTEGER NOT NULL UNIQUE,
                        event_type      TEXT NOT NULL,
                        actor_user_id   TEXT,
                        target_user_id  TEXT,
                        details_json    TEXT NOT NULL,
                        client_hash     TEXT,
                        previous_hash   TEXT,
                        event_hash      TEXT NOT NULL,
                        created_at      TEXT NOT NULL
                    );

                    CREATE TRIGGER IF NOT EXISTS trg_auth_audit_no_update
                    BEFORE UPDATE ON auth_audit_events
                    BEGIN
                        SELECT RAISE(ABORT, 'auth audit events are immutable');
                    END;

                    CREATE TRIGGER IF NOT EXISTS trg_auth_audit_no_delete
                    BEFORE DELETE ON auth_audit_events
                    BEGIN
                        SELECT RAISE(ABORT, 'auth audit events cannot be deleted');
                    END;
                    """
                )
            self._schema_ready = True

    @staticmethod
    def normalize_username(username: str) -> str:
        value = str(username or "").strip()
        if not _USERNAME_RE.fullmatch(value):
            raise AuthError(
                "用户名只能包含字母、数字、点、下划线或连字符，长度为 3-32 位",
                code="invalid_username",
            )
        return value.casefold()

    @staticmethod
    def validate_password(password: str, username: str = "") -> None:
        value = str(password or "")
        if len(value) < 12 or len(value) > 128:
            raise AuthError("密码长度必须为 12-128 个字符", code="weak_password")
        lowered = value.casefold()
        if lowered in _COMMON_PASSWORDS:
            raise AuthError("密码过于常见，请使用更长且不重复的密码", code="weak_password")
        normalized_username = str(username or "").strip().casefold()
        if normalized_username and normalized_username in lowered:
            raise AuthError("密码不能包含用户名", code="weak_password")

    def _privacy_hash(self, value: str) -> str:
        pepper = self.settings.audit_pepper or "local-development-only"
        return _sha256(f"{pepper}|{value}")

    def client_hash(self, value: str) -> str:
        return self._privacy_hash(str(value or "unknown")[:256])

    def _csrf_token(self, session_id: str) -> str:
        key = (self.settings.audit_pepper or "local-development-only").encode("utf-8")
        return hmac.new(
            key,
            f"stock-assistant|csrf|{session_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _public_user(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "status": row["status"],
            "must_change_password": bool(row["must_change_password"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "password_changed_at": row["password_changed_at"],
            "last_login_at": row["last_login_at"],
        }

    def _append_audit(
        self,
        connection: Any,
        event_type: str,
        *,
        actor_user_id: str | None = None,
        target_user_id: str | None = None,
        details: dict[str, Any] | None = None,
        client_hash: str | None = None,
    ) -> dict[str, Any]:
        previous = connection.execute(
            "SELECT sequence_no, event_hash FROM auth_audit_events ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone()
        sequence_no = int(previous["sequence_no"] if previous else 0) + 1
        created_at = _iso()
        event_id = _new_id("auth_evt")
        payload = {
            "id": event_id,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "actor_user_id": actor_user_id,
            "target_user_id": target_user_id,
            "details": details or {},
            "client_hash": client_hash,
            "previous_hash": previous["event_hash"] if previous else None,
            "created_at": created_at,
        }
        event_hash = _sha256(_canonical(payload))
        connection.execute(
            """
            INSERT INTO auth_audit_events(
                id, sequence_no, event_type, actor_user_id, target_user_id,
                details_json, client_hash, previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                sequence_no,
                event_type,
                actor_user_id,
                target_user_id,
                _canonical(details or {}),
                client_hash,
                payload["previous_hash"],
                event_hash,
                created_at,
            ),
        )
        return {**payload, "event_hash": event_hash}

    def user_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS value FROM auth_users").fetchone()
        return int(row["value"] if row else 0)

    def readiness(self) -> dict[str, Any]:
        initialized = self.user_count() > 0
        return {
            "required": self.settings.required,
            "configured": self.settings.configuration_ready,
            "initialized": initialized,
            "ready": (
                not self.settings.required
                or (self.settings.configuration_ready and initialized)
            ),
            "cookie_secure": self.settings.cookie_secure,
            "self_registration_enabled": self.settings.self_registration_enabled,
        }

    def bootstrap_admin(
        self,
        username: str,
        password: str,
        *,
        display_name: str = "系统管理员",
        subject_id: str = "default",
    ) -> dict[str, Any]:
        normalized = self.normalize_username(username)
        self.validate_password(password, username)
        display_name = str(display_name or "系统管理员").strip()[:80] or "系统管理员"
        subject_id = str(subject_id or "default").strip()[:80] or "default"
        password_hash = self._password_hasher.hash(password)
        now = _iso()
        user_id = _new_id("usr")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone():
                connection.rollback()
                raise AuthError(
                    "系统已经存在用户，不能再次初始化管理员",
                    code="already_initialized",
                    status_code=409,
                )
            connection.execute(
                """
                INSERT INTO auth_users(
                    id, subject_id, username, username_normalized, display_name,
                    password_hash, role, status, must_change_password,
                    created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, 'admin', 'active', 1, ?, ?, 'system-bootstrap')
                """,
                (
                    user_id,
                    subject_id,
                    str(username).strip(),
                    normalized,
                    display_name,
                    password_hash,
                    now,
                    now,
                ),
            )
            # Preserve immutable Evidence payloads while assigning legacy Agent ownership.
            migrated_agent_rows = {}
            for table in (
                "agent_runs",
                "agent_batches",
                "agent_outcome_schedules",
                "agent_strategy_shadow_enrollments",
            ):
                if not self._table_exists(connection, table):
                    continue
                if table in {"agent_runs", "agent_batches"}:
                    connection.execute(
                        f"""
                        UPDATE {table} SET idempotency_key=NULL
                        WHERE user_id='anonymous' AND idempotency_key IS NOT NULL
                          AND EXISTS (
                              SELECT 1 FROM {table} AS owned
                              WHERE owned.user_id=?
                                AND owned.idempotency_key={table}.idempotency_key
                          )
                        """,
                        (subject_id,),
                    )
                cursor = connection.execute(
                    f"UPDATE {table} SET user_id=? WHERE user_id='anonymous'",
                    (subject_id,),
                )
                migrated_agent_rows[table] = max(0, int(cursor.rowcount))
            self._append_audit(
                connection,
                "admin_bootstrapped",
                actor_user_id=user_id,
                target_user_id=user_id,
                details={
                    "role": ROLE_ADMIN,
                    "subject_id": subject_id,
                    "legacy_agent_rows_assigned": migrated_agent_rows,
                },
            )
            connection.commit()
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row)

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str,
        role: str,
        actor_user_id: str,
        client_hash: str | None = None,
    ) -> dict[str, Any]:
        normalized = self.normalize_username(username)
        self.validate_password(password, username)
        if role not in {ROLE_ADMIN, ROLE_USER}:
            raise AuthError("用户角色无效", code="invalid_role")
        display_name = str(display_name or username).strip()[:80] or str(username).strip()
        now = _iso()
        user_id = _new_id("usr")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO auth_users(
                        id, subject_id, username, username_normalized, display_name,
                        password_hash, role, status, must_change_password,
                        created_at, updated_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?)
                    """,
                    (
                        user_id,
                        user_id,
                        str(username).strip(),
                        normalized,
                        display_name,
                        self._password_hasher.hash(password),
                        role,
                        now,
                        now,
                        actor_user_id,
                    ),
                )
                self._append_audit(
                    connection,
                    "user_created",
                    actor_user_id=actor_user_id,
                    target_user_id=user_id,
                    details={"role": role},
                    client_hash=client_hash,
                )
                connection.commit()
                row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        except INTEGRITY_ERRORS as error:
            raise AuthError("用户名已经存在", code="username_exists", status_code=409) from error
        return self._public_user(row)

    def _reserve_registration_attempt(self, client_hash: str) -> None:
        cutoff = _iso(
            _utc_now()
            - dt.timedelta(minutes=self.settings.registration_window_minutes)
        )
        cleanup_cutoff = _iso(_utc_now() - dt.timedelta(days=1))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM auth_registration_attempts WHERE attempted_at<?",
                (cleanup_cutoff,),
            )
            row = connection.execute(
                """
                SELECT COUNT(*) AS value FROM auth_registration_attempts
                WHERE attempted_at>=? AND client_hash=?
                """,
                (cutoff, client_hash),
            ).fetchone()
            if int(row["value"] if row else 0) >= self.settings.registration_limit:
                connection.commit()
                raise AuthError(
                    "注册尝试过于频繁，请稍后再试",
                    code="registration_rate_limited",
                    status_code=429,
                )
            connection.execute(
                "INSERT INTO auth_registration_attempts(client_hash, attempted_at) VALUES (?, ?)",
                (client_hash, _iso()),
            )
            connection.commit()

    def register_user(
        self,
        username: str,
        password: str,
        *,
        client_hash: str,
    ) -> dict[str, Any]:
        if not self.settings.required or not self.settings.self_registration_enabled:
            raise AuthError(
                "当前未开放用户注册",
                code="self_registration_disabled",
                status_code=403,
            )
        if not self.settings.configuration_ready:
            raise AuthError(
                "认证安全配置尚未完成",
                code="auth_configuration_incomplete",
                status_code=503,
            )
        if self.user_count() == 0:
            raise AuthError(
                "系统尚未初始化管理员",
                code="auth_bootstrap_required",
                status_code=503,
            )

        self._reserve_registration_attempt(client_hash)
        normalized = self.normalize_username(username)
        self.validate_password(password, username)
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM auth_users WHERE username_normalized=?",
                (normalized,),
            ).fetchone():
                raise AuthError(
                    "用户名已经存在",
                    code="username_exists",
                    status_code=409,
                )

        now = _iso()
        user_id = _new_id("usr")
        password_hash = self._password_hasher.hash(password)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO auth_users(
                        id, subject_id, username, username_normalized, display_name,
                        password_hash, role, status, must_change_password,
                        created_at, updated_at, password_changed_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, 'user', 'active', 0, ?, ?, ?, 'self-registration')
                    """,
                    (
                        user_id,
                        user_id,
                        str(username).strip(),
                        normalized,
                        str(username).strip(),
                        password_hash,
                        now,
                        now,
                        now,
                    ),
                )
                self._append_audit(
                    connection,
                    "user_self_registered",
                    actor_user_id=user_id,
                    target_user_id=user_id,
                    details={"role": ROLE_USER},
                    client_hash=client_hash,
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM auth_users WHERE id=?", (user_id,)
                ).fetchone()
        except INTEGRITY_ERRORS as error:
            raise AuthError(
                "用户名已经存在",
                code="username_exists",
                status_code=409,
            ) from error
        return self._public_user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM auth_users ORDER BY created_at ASC, username_normalized ASC"
            ).fetchall()
            result = []
            for row in rows:
                item = self._public_user(row)
                subject_id = row["subject_id"]
                item["data"] = {
                    "holding_count": (
                        int(connection.execute(
                            "SELECT COUNT(*) AS value FROM holdings WHERE user_id=?",
                            (subject_id,),
                        ).fetchone()["value"])
                        if self._table_exists(connection, "holdings") else 0
                    ),
                    "agent_run_count": (
                        int(connection.execute(
                            "SELECT COUNT(*) AS value FROM agent_runs WHERE user_id=?",
                            (subject_id,),
                        ).fetchone()["value"])
                        if self._table_exists(connection, "agent_runs") else 0
                    ),
                }
                result.append(item)
        return result

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        try:
            normalized = self.normalize_username(username)
        except AuthError:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_users WHERE username_normalized=?",
                (normalized,),
            ).fetchone()
        return self._public_user(row) if row else None

    def update_user(
        self,
        user_id: str,
        *,
        actor_user_id: str,
        display_name: str | None = None,
        role: str | None = None,
        status: str | None = None,
        client_hash: str | None = None,
    ) -> dict[str, Any]:
        if role is not None and role not in {ROLE_ADMIN, ROLE_USER}:
            raise AuthError("用户角色无效", code="invalid_role")
        if status is not None and status not in {USER_ACTIVE, USER_DISABLED}:
            raise AuthError("用户状态无效", code="invalid_status")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise AuthError("用户不存在", code="user_not_found", status_code=404)
            new_role = role or row["role"]
            new_status = status or row["status"]
            if user_id == actor_user_id and (
                new_role != ROLE_ADMIN or new_status != USER_ACTIVE
            ):
                connection.rollback()
                raise AuthError("管理员不能停用或降级自己的当前账户", code="self_lockout", status_code=409)
            if row["role"] == ROLE_ADMIN and row["status"] == USER_ACTIVE and (
                new_role != ROLE_ADMIN or new_status != USER_ACTIVE
            ):
                active_admins = connection.execute(
                    "SELECT COUNT(*) AS value FROM auth_users WHERE role='admin' AND status='active'"
                ).fetchone()["value"]
                if int(active_admins) <= 1:
                    connection.rollback()
                    raise AuthError("系统必须保留至少一个启用的管理员", code="last_admin", status_code=409)
            new_display_name = (
                str(display_name).strip()[:80]
                if display_name is not None else row["display_name"]
            ) or row["username"]
            now = _iso()
            connection.execute(
                "UPDATE auth_users SET display_name=?, role=?, status=?, updated_at=? WHERE id=?",
                (new_display_name, new_role, new_status, now, user_id),
            )
            security_changed = new_role != row["role"] or new_status != row["status"]
            if security_changed:
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=?, revoke_reason='account_changed' "
                    "WHERE user_id=? AND revoked_at IS NULL",
                    (now, user_id),
                )
            self._append_audit(
                connection,
                "user_updated",
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                details={
                    "display_name_changed": new_display_name != row["display_name"],
                    "from_role": row["role"],
                    "to_role": new_role,
                    "from_status": row["status"],
                    "to_status": new_status,
                    "sessions_revoked": security_changed,
                },
                client_hash=client_hash,
            )
            connection.commit()
            updated = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(updated)

    def reset_password(
        self,
        user_id: str,
        password: str,
        *,
        actor_user_id: str,
        client_hash: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise AuthError("用户不存在", code="user_not_found", status_code=404)
        self.validate_password(password, row["username"])
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE auth_users
                SET password_hash=?, must_change_password=1, password_changed_at=?, updated_at=?
                WHERE id=?
                """,
                (self._password_hasher.hash(password), now, now, user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=?, revoke_reason='password_reset' "
                "WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )
            self._append_audit(
                connection,
                "password_reset_by_admin",
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                details={"sessions_revoked": True, "must_change_password": True},
                client_hash=client_hash,
            )
            connection.commit()
            updated = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(updated)

    def recover_admin(self, username: str, password: str) -> dict[str, Any]:
        normalized = self.normalize_username(username)
        self.validate_password(password, username)
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM auth_users WHERE username_normalized=?",
                (normalized,),
            ).fetchone()
            if row is None or row["role"] != ROLE_ADMIN:
                connection.rollback()
                raise AuthError(
                    "管理员账户不存在",
                    code="admin_not_found",
                    status_code=404,
                )
            connection.execute(
                """
                UPDATE auth_users
                SET password_hash=?, status='active', must_change_password=1,
                    password_changed_at=?, updated_at=?
                WHERE id=?
                """,
                (self._password_hasher.hash(password), now, now, row["id"]),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=?, revoke_reason='offline_recovery' "
                "WHERE user_id=? AND revoked_at IS NULL",
                (now, row["id"]),
            )
            self._append_audit(
                connection,
                "admin_recovered_offline",
                actor_user_id="system-recovery",
                target_user_id=row["id"],
                details={"sessions_revoked": True, "must_change_password": True},
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM auth_users WHERE id=?",
                (row["id"],),
            ).fetchone()
        return self._public_user(updated)

    def _verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self._password_hasher.verify(password_hash, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def _rate_limited(
        self,
        connection: Any,
        username_hash: str,
        client_hash: str,
    ) -> bool:
        cutoff = _iso(_utc_now() - dt.timedelta(minutes=self.settings.login_window_minutes))
        cleanup_cutoff = _iso(_utc_now() - dt.timedelta(days=1))
        connection.execute(
            "DELETE FROM auth_login_attempts WHERE attempted_at<?",
            (cleanup_cutoff,),
        )
        row = connection.execute(
            """
            SELECT COUNT(*) AS value FROM auth_login_attempts
            WHERE attempted_at>=? AND (username_hash=? OR client_hash=?)
            """,
            (cutoff, username_hash, client_hash),
        ).fetchone()
        return int(row["value"] if row else 0) >= self.settings.login_limit

    def login(self, username: str, password: str, *, client_hash: str) -> dict[str, Any]:
        if not self.settings.configuration_ready:
            raise AuthError(
                "认证安全配置尚未完成",
                code="auth_configuration_incomplete",
                status_code=503,
            )
        try:
            normalized = self.normalize_username(username)
        except AuthError:
            normalized = str(username or "").strip().casefold()[:64]
        username_hash = self._privacy_hash(normalized)
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone():
                raise AuthError(
                    "系统尚未初始化管理员",
                    code="auth_bootstrap_required",
                    status_code=503,
                )
            if self._rate_limited(connection, username_hash, client_hash):
                raise AuthError(
                    "登录尝试过于频繁，请稍后再试",
                    code="login_rate_limited",
                    status_code=429,
                )
            row = connection.execute(
                "SELECT * FROM auth_users WHERE username_normalized=?",
                (normalized,),
            ).fetchone()
        password_hash = row["password_hash"] if row is not None else self._dummy_password_hash
        valid = self._verify_password(password_hash, str(password or ""))
        if row is None or row["status"] != USER_ACTIVE or not valid:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO auth_login_attempts(username_hash, client_hash, attempted_at) VALUES (?, ?, ?)",
                    (username_hash, client_hash, _iso()),
                )
                self._append_audit(
                    connection,
                    "login_failed",
                    details={"username_hash": username_hash},
                    client_hash=client_hash,
                )
                connection.commit()
            raise AuthError(
                "用户名或密码错误",
                code="invalid_credentials",
                status_code=401,
            )

        token = secrets.token_urlsafe(48)
        now_dt = _utc_now()
        now = _iso(now_dt)
        expires_at = _iso(now_dt + dt.timedelta(hours=self.settings.session_hours))
        session_id = _new_id("sess")
        csrf_token = self._csrf_token(session_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session_cleanup_cutoff = _iso(now_dt - dt.timedelta(days=30))
            connection.execute(
                """
                DELETE FROM auth_sessions
                WHERE (revoked_at IS NOT NULL AND revoked_at<?)
                   OR expires_at<?
                """,
                (session_cleanup_cutoff, session_cleanup_cutoff),
            )
            if self._password_hasher.check_needs_rehash(row["password_hash"]):
                connection.execute(
                    "UPDATE auth_users SET password_hash=?, updated_at=? WHERE id=?",
                    (self._password_hasher.hash(password), now, row["id"]),
                )
            connection.execute(
                "DELETE FROM auth_login_attempts WHERE username_hash=?",
                (username_hash,),
            )
            active_sessions = connection.execute(
                """
                SELECT id FROM auth_sessions
                WHERE user_id=? AND revoked_at IS NULL AND expires_at>?
                ORDER BY created_at DESC
                """,
                (row["id"], now),
            ).fetchall()
            for stale in active_sessions[4:]:
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=?, revoke_reason='session_limit' WHERE id=?",
                    (now, stale["id"]),
                )
            connection.execute(
                """
                INSERT INTO auth_sessions(
                    id, user_id, token_hash, csrf_hash, created_at,
                    last_seen_at, expires_at, revoked_at, revoke_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    session_id,
                    row["id"],
                    _sha256(token),
                    _sha256(csrf_token),
                    now,
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                "UPDATE auth_users SET last_login_at=?, updated_at=? WHERE id=?",
                (now, now, row["id"]),
            )
            self._append_audit(
                connection,
                "login_succeeded",
                actor_user_id=row["id"],
                target_user_id=row["id"],
                details={"session_id": session_id},
                client_hash=client_hash,
            )
            connection.commit()
            refreshed = connection.execute("SELECT * FROM auth_users WHERE id=?", (row["id"],)).fetchone()
        return {
            "token": token,
            "csrf_token": csrf_token,
            "session_id": session_id,
            "expires_at": expires_at,
            "user": self._public_user(refreshed),
        }

    def authenticate(self, token: str | None, *, touch: bool = True) -> AuthPrincipal | None:
        if not token:
            return None
        token_hash = _sha256(str(token))
        now_dt = _utc_now()
        now = _iso(now_dt)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    s.id AS session_id, s.last_seen_at, s.expires_at, s.revoked_at,
                    u.id, u.subject_id, u.username, u.display_name, u.role,
                    u.status, u.must_change_password
                FROM auth_sessions AS s
                JOIN auth_users AS u ON u.id=s.user_id
                WHERE s.token_hash=?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            last_seen = _parse_time(row["last_seen_at"])
            expires = _parse_time(row["expires_at"])
            idle_expired = bool(
                last_seen
                and now_dt - last_seen > dt.timedelta(minutes=self.settings.idle_minutes)
            )
            invalid = bool(
                row["revoked_at"]
                or row["status"] != USER_ACTIVE
                or expires is None
                or expires <= now_dt
                or idle_expired
            )
            if invalid:
                if not row["revoked_at"]:
                    connection.execute(
                        "UPDATE auth_sessions SET revoked_at=?, revoke_reason=? WHERE id=?",
                        (now, "idle_timeout" if idle_expired else "expired_or_disabled", row["session_id"]),
                    )
                return None
            if touch and (last_seen is None or (now_dt - last_seen).total_seconds() >= 60):
                connection.execute(
                    "UPDATE auth_sessions SET last_seen_at=? WHERE id=?",
                    (now, row["session_id"]),
                )
        return AuthPrincipal(
            user_id=row["id"],
            subject_id=row["subject_id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            must_change_password=bool(row["must_change_password"]),
            session_id=row["session_id"],
        )

    def rotate_csrf(self, session_id: str) -> str:
        # Deterministic per session so browser tabs do not invalidate one another.
        csrf_token = self._csrf_token(session_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE auth_sessions SET csrf_hash=? WHERE id=? AND revoked_at IS NULL",
                (_sha256(csrf_token), session_id),
            )
        return csrf_token

    def verify_csrf(self, session_id: str | None, token: str | None) -> bool:
        if not session_id or not token:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT csrf_hash FROM auth_sessions WHERE id=? AND revoked_at IS NULL",
                (session_id,),
            ).fetchone()
        return bool(row and hmac.compare_digest(str(row["csrf_hash"]), _sha256(str(token))))

    def logout(
        self,
        session_id: str,
        *,
        actor_user_id: str,
        client_hash: str | None = None,
    ) -> None:
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=?, revoke_reason='logout' "
                "WHERE id=? AND revoked_at IS NULL",
                (now, session_id),
            )
            self._append_audit(
                connection,
                "logout",
                actor_user_id=actor_user_id,
                target_user_id=actor_user_id,
                details={"session_id": session_id},
                client_hash=client_hash,
            )
            connection.commit()

    def change_password(
        self,
        principal: AuthPrincipal,
        current_password: str,
        new_password: str,
        *,
        client_hash: str | None = None,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (principal.user_id,)).fetchone()
        if row is None or not self._verify_password(row["password_hash"], current_password):
            raise AuthError("当前密码错误", code="current_password_invalid", status_code=401)
        self.validate_password(new_password, row["username"])
        if self._verify_password(row["password_hash"], new_password):
            raise AuthError("新密码不能与当前密码相同", code="password_unchanged")
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE auth_users
                SET password_hash=?, must_change_password=0,
                    password_changed_at=?, updated_at=?
                WHERE id=?
                """,
                (self._password_hasher.hash(new_password), now, now, principal.user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=?, revoke_reason='password_changed' "
                "WHERE user_id=? AND revoked_at IS NULL",
                (now, principal.user_id),
            )
            self._append_audit(
                connection,
                "password_changed",
                actor_user_id=principal.user_id,
                target_user_id=principal.user_id,
                details={"sessions_revoked": True},
                client_hash=client_hash,
            )
            connection.commit()

    def overview(self) -> dict[str, Any]:
        now = _iso()
        with self._connect() as connection:
            users = {
                f"{row['role']}_{row['status']}": int(row["value"])
                for row in connection.execute(
                    "SELECT role, status, COUNT(*) AS value FROM auth_users GROUP BY role, status"
                ).fetchall()
            }
            sessions = connection.execute(
                "SELECT COUNT(*) AS value FROM auth_sessions "
                "WHERE revoked_at IS NULL AND expires_at>?",
                (now,),
            ).fetchone()["value"]
            run_statuses = {}
            if self._table_exists(connection, "agent_runs"):
                run_statuses = {
                    row["status"]: int(row["value"])
                    for row in connection.execute(
                        "SELECT status, COUNT(*) AS value FROM agent_runs GROUP BY status"
                    ).fetchall()
                }
        return {
            "users": {
                "active_admins": users.get("admin_active", 0),
                "active_users": users.get("user_active", 0),
                "disabled": users.get("admin_disabled", 0) + users.get("user_disabled", 0),
            },
            "active_sessions": int(sessions),
            "agent_runs": run_statuses,
            "generated_at": now,
        }

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM auth_audit_events ORDER BY sequence_no DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "sequence_no": row["sequence_no"],
                "event_type": row["event_type"],
                "actor_user_id": row["actor_user_id"],
                "target_user_id": row["target_user_id"],
                "details": json.loads(row["details_json"] or "{}"),
                "client_hash": row["client_hash"],
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def verify_audit(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM auth_audit_events ORDER BY sequence_no ASC"
            ).fetchall()
        previous_hash = None
        errors = []
        for expected_sequence, row in enumerate(rows, start=1):
            details = json.loads(row["details_json"] or "{}")
            payload = {
                "id": row["id"],
                "sequence_no": row["sequence_no"],
                "event_type": row["event_type"],
                "actor_user_id": row["actor_user_id"],
                "target_user_id": row["target_user_id"],
                "details": details,
                "client_hash": row["client_hash"],
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            if int(row["sequence_no"]) != expected_sequence:
                errors.append(f"sequence:{row['id']}")
            if row["previous_hash"] != previous_hash:
                errors.append(f"previous_hash:{row['id']}")
            if row["event_hash"] != _sha256(_canonical(payload)):
                errors.append(f"event_hash:{row['id']}")
            previous_hash = row["event_hash"]
        return {
            "verified": not errors,
            "event_count": len(rows),
            "chain_head": previous_hash,
            "errors": errors[:20],
        }


def request_client_identifier(request: Request, service: AuthService) -> str:
    if service.settings.trust_proxy:
        forwarded = str(request.headers.get("x-real-ip") or "").strip()
        if forwarded:
            return forwarded
    return str(request.client.host if request.client else "unknown")


def principal_from_request(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="请先登录")
    return principal


def require_admin(request: Request) -> AuthPrincipal:
    principal = principal_from_request(request)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


auth_service = AuthService()
