# -*- coding: utf-8 -*-

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from auth import (  # noqa: E402
    AuthError,
    AuthPrincipal,
    AuthService,
    AuthSettings,
)
import storage  # noqa: E402


def _settings(**overrides):
    values = {
        "required": True,
        "cookie_secure": False,
        "session_hours": 12,
        "idle_minutes": 120,
        "login_limit": 3,
        "login_window_minutes": 15,
        "self_registration_enabled": True,
        "registration_limit": 5,
        "registration_window_minutes": 60,
        "audit_pepper": "p" * 64,
        "trust_proxy": False,
    }
    values.update(overrides)
    return AuthSettings(**values)


@contextmanager
def _isolated_storage(db_path: str):
    previous_path = storage._DB_PATH
    with storage._lock:
        try:
            if storage._conn is not None:
                storage._conn.close()
            storage._conn = None
            storage._DB_PATH = db_path
            yield
        finally:
            if storage._conn is not None:
                storage._conn.close()
            storage._conn = None
            storage._DB_PATH = previous_path


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "auth.db")
        self.service = AuthService(self.db_path, settings=_settings())

    def tearDown(self):
        self.tempdir.cleanup()

    def test_password_sessions_roles_and_audit_are_server_enforced(self):
        admin = self.service.bootstrap_admin(
            "admin",
            "Bootstrap-Password-2026!",
            subject_id="default",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            password_hash = connection.execute(
                "SELECT password_hash FROM auth_users WHERE id=?",
                (admin["id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertNotIn("Bootstrap-Password-2026!", password_hash)

        created = self.service.create_user(
            username="investor01",
            password="Investor-Temporary-2026!",
            display_name="投资用户",
            role="user",
            actor_user_id=admin["id"],
        )
        login = self.service.login(
            "investor01",
            "Investor-Temporary-2026!",
            client_hash="client-a",
        )
        principal = self.service.authenticate(login["token"])
        self.assertEqual(principal.subject_id, created["id"])
        self.assertTrue(principal.must_change_password)
        self.assertTrue(self.service.verify_csrf(principal.session_id, login["csrf_token"]))
        self.assertFalse(self.service.verify_csrf(principal.session_id, "wrong"))
        reissued_csrf = self.service.rotate_csrf(principal.session_id)
        self.assertEqual(reissued_csrf, login["csrf_token"])
        self.assertTrue(self.service.verify_csrf(principal.session_id, reissued_csrf))

        self.service.update_user(
            created["id"],
            actor_user_id=admin["id"],
            status="disabled",
        )
        self.assertIsNone(self.service.authenticate(login["token"]))
        with self.assertRaises(AuthError) as context:
            self.service.update_user(
                admin["id"],
                actor_user_id=admin["id"],
                role="user",
            )
        self.assertEqual(context.exception.code, "self_lockout")
        self.assertTrue(self.service.verify_audit()["verified"])

    def test_failed_login_is_rate_limited_without_user_enumeration(self):
        self.service.bootstrap_admin("admin", "Bootstrap-Password-2026!")
        messages = []
        for _ in range(3):
            with self.assertRaises(AuthError) as context:
                self.service.login("missing-user", "not-the-password", client_hash="client-a")
            messages.append(str(context.exception))
            self.assertEqual(context.exception.code, "invalid_credentials")
        self.assertEqual(len(set(messages)), 1)
        with self.assertRaises(AuthError) as context:
            self.service.login("missing-user", "not-the-password", client_hash="client-a")
        self.assertEqual(context.exception.code, "login_rate_limited")

    def test_self_registration_creates_only_ready_ordinary_users_and_is_audited(self):
        self.service.bootstrap_admin("admin", "Bootstrap-Password-2026!")
        registered = self.service.register_user(
            "selfinvestor",
            "Strong-Portfolio-Password-2026!",
            client_hash="client-register",
        )

        self.assertEqual(registered["username"], "selfinvestor")
        self.assertEqual(registered["role"], "user")
        self.assertEqual(registered["status"], "active")
        self.assertFalse(registered["must_change_password"])
        self.assertIsNotNone(registered["password_changed_at"])
        self.assertNotIn("password_hash", registered)

        login = self.service.login(
            "selfinvestor",
            "Strong-Portfolio-Password-2026!",
            client_hash="client-login",
        )
        self.assertFalse(login["user"]["must_change_password"])
        events = self.service.list_audit(10)
        registration = next(
            event for event in events
            if event["event_type"] == "user_self_registered"
        )
        self.assertEqual(registration["actor_user_id"], registered["id"])
        self.assertEqual(registration["target_user_id"], registered["id"])
        self.assertEqual(registration["details"]["role"], "user")
        self.assertTrue(self.service.verify_audit()["verified"])

    def test_self_registration_can_be_disabled_and_is_rate_limited(self):
        disabled = AuthService(
            str(Path(self.tempdir.name) / "registration-disabled.db"),
            settings=_settings(self_registration_enabled=False),
        )
        disabled.bootstrap_admin("admin", "Bootstrap-Password-2026!")
        with self.assertRaises(AuthError) as context:
            disabled.register_user(
                "investor01",
                "Strong-Portfolio-Password-2026!",
                client_hash="client-disabled",
            )
        self.assertEqual(context.exception.code, "self_registration_disabled")

        limited = AuthService(
            str(Path(self.tempdir.name) / "registration-limited.db"),
            settings=_settings(registration_limit=2),
        )
        limited.bootstrap_admin("admin", "Bootstrap-Password-2026!")
        limited.register_user(
            "investor01",
            "Strong-Portfolio-Password-2026!",
            client_hash="shared-client",
        )
        with self.assertRaises(AuthError) as duplicate:
            limited.register_user(
                "investor01",
                "Strong-Portfolio-Password-2026!",
                client_hash="shared-client",
            )
        self.assertEqual(duplicate.exception.code, "username_exists")
        with self.assertRaises(AuthError) as rate_limited:
            limited.register_user(
                "investor02",
                "Another-Portfolio-Password-2026!",
                client_hash="shared-client",
            )
        self.assertEqual(rate_limited.exception.code, "registration_rate_limited")

    def test_offline_admin_recovery_reenables_account_and_revokes_sessions(self):
        admin = self.service.bootstrap_admin("admin", "Bootstrap-Password-2026!")
        login = self.service.login(
            "admin",
            "Bootstrap-Password-2026!",
            client_hash="client-a",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE auth_users SET status='disabled' WHERE id=?",
                (admin["id"],),
            )
            connection.commit()
        finally:
            connection.close()

        recovered = self.service.recover_admin(
            "admin",
            "Emergency-Recovery-Password-2026!",
        )
        self.assertEqual(recovered["status"], "active")
        self.assertTrue(recovered["must_change_password"])
        self.assertIsNone(self.service.authenticate(login["token"]))
        refreshed_login = self.service.login(
            "admin",
            "Emergency-Recovery-Password-2026!",
            client_hash="client-b",
        )
        self.assertTrue(refreshed_login["user"]["must_change_password"])
        self.assertEqual(
            self.service.list_audit(1)[0]["event_type"],
            "login_succeeded",
        )
        self.assertTrue(
            any(
                event["event_type"] == "admin_recovered_offline"
                for event in self.service.list_audit(10)
            )
        )

    def test_user_scoped_storage_does_not_cross_accounts(self):
        with _isolated_storage(self.db_path):
            storage.add_watch("A股", "600519", "账户一", user_id="subject-one")
            storage.add_watch("A股", "600519", "账户二", user_id="subject-two")
            storage.upsert_holding(
                {"asset_type": "fund", "market": "基金", "code": "000001", "name": "一"},
                user_id="subject-one",
            )
            self.assertEqual(storage.list_watchlist("subject-one")[0]["name"], "账户一")
            self.assertEqual(storage.list_watchlist("subject-two")[0]["name"], "账户二")
            self.assertEqual(len(storage.list_holdings("subject-one")), 1)
            self.assertEqual(storage.list_holdings("subject-two"), [])

    def test_legacy_watchlist_migration_runs_only_once(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    added_at TEXT NOT NULL,
                    UNIQUE(market, symbol)
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    score REAL NOT NULL,
                    message TEXT NOT NULL,
                    triggered_at TEXT NOT NULL
                );
                INSERT INTO watchlist(market, symbol, name, added_at)
                VALUES ('A股', '600519', '旧自选', '2026-01-01T00:00:00');
                """
            )
            connection.commit()
        finally:
            connection.close()

        with _isolated_storage(self.db_path):
            self.assertEqual(len(storage.list_watchlist("default")), 1)
            self.assertTrue(storage.remove_watch("A股", "600519", user_id="default"))
            storage._conn.close()
            storage._conn = None
            self.assertEqual(storage.list_watchlist("default"), [])

    def test_bootstrap_assigns_all_legacy_agent_rows_to_initial_admin(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE agent_runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE(user_id, idempotency_key)
                );
                INSERT INTO agent_runs VALUES ('owned', 'default', 'same-key');
                INSERT INTO agent_runs VALUES ('legacy', 'anonymous', 'same-key');
                """
            )
            connection.commit()
        finally:
            connection.close()

        self.service.bootstrap_admin(
            "admin",
            "Bootstrap-Password-2026!",
            subject_id="default",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT user_id, idempotency_key FROM agent_runs ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([row[0] for row in rows], ["default", "default"])
        self.assertEqual([row[1] for row in rows].count(None), 1)

    def test_admin_and_owner_access_rules_do_not_leak_run_ids(self):
        from routers.agent import _can_access

        user = AuthPrincipal(
            user_id="usr-one",
            subject_id="subject-one",
            username="one",
            display_name="One",
            role="user",
            must_change_password=False,
            session_id="session-one",
        )
        admin = AuthPrincipal(
            user_id="usr-admin",
            subject_id="subject-admin",
            username="admin",
            display_name="Admin",
            role="admin",
            must_change_password=False,
            session_id="session-admin",
        )
        self.assertTrue(_can_access("subject-one", user))
        self.assertFalse(_can_access("subject-two", user))
        self.assertTrue(_can_access("subject-two", admin))


class AuthApiBoundaryTests(unittest.TestCase):
    def setUp(self):
        import main as app_main
        from routers import agent as agent_router
        from routers import auth as auth_router
        from routers import portfolio as portfolio_router

        self.tempdir = tempfile.TemporaryDirectory()
        self.service = AuthService(
            str(Path(self.tempdir.name) / "auth-api.db"),
            settings=_settings(),
        )
        self.service.bootstrap_admin("admin", "Bootstrap-Password-2026!")
        self.agent_router = agent_router
        self.portfolio_router = portfolio_router
        self.main_patch = patch.object(app_main, "auth_service", self.service)
        self.router_patch = patch.object(auth_router, "auth_service", self.service)
        self.main_patch.start()
        self.router_patch.start()
        self.client = TestClient(app_main.app)

    def tearDown(self):
        self.client.close()
        self.router_patch.stop()
        self.main_patch.stop()
        self.tempdir.cleanup()

    def _login(self, username: str, password: str) -> str:
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        return response.json()["csrf_token"]

    def _change_password(self, current_password: str, new_password: str, csrf: str) -> None:
        response = self.client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": csrf},
            json={"current_password": current_password, "new_password": new_password},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_cors_wraps_login_preflight_and_early_auth_errors(self):
        origin = "http://127.0.0.1:5173"
        preflight = self.client.options(
            "/api/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
        self.assertEqual(preflight.headers["access-control-allow-credentials"], "true")

        unauthorized = self.client.get(
            "/api/markets",
            headers={"Origin": origin},
        )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers["access-control-allow-origin"], origin)

    def test_public_registration_rejects_role_injection_and_creates_ordinary_user(self):
        injected = self.client.post(
            "/api/auth/register",
            json={
                "username": "injectedadmin",
                "password": "Strong-Portfolio-Password-2026!",
                "role": "admin",
            },
        )
        self.assertEqual(injected.status_code, 422, injected.text)
        self.assertFalse(
            any(user["username"] == "injectedadmin" for user in self.service.list_users())
        )

        registered = self.client.post(
            "/api/auth/register",
            json={
                "username": "selfinvestor",
                "password": "Strong-Portfolio-Password-2026!",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        payload = registered.json()
        self.assertTrue(payload["registered"])
        self.assertEqual(payload["user"]["role"], "user")
        self.assertFalse(payload["user"]["must_change_password"])
        self.assertNotIn("password_hash", payload["user"])
        self.assertNotIn("set-cookie", registered.headers)

        duplicate = self.client.post(
            "/api/auth/register",
            json={
                "username": "selfinvestor",
                "password": "Strong-Portfolio-Password-2026!",
            },
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(duplicate.json()["detail"]["code"], "username_exists")

        self._login("selfinvestor", "Strong-Portfolio-Password-2026!")
        forbidden = self.client.get("/api/admin/overview")
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        self.assertEqual(forbidden.json()["detail"], "需要管理员权限")

    def test_cookie_csrf_forced_password_change_and_admin_rbac(self):
        anonymous = self.client.get("/api/markets")
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.json()["code"], "authentication_required")
        self.assertEqual(anonymous.headers["x-frame-options"], "DENY")

        csrf = self._login("admin", "Bootstrap-Password-2026!")
        forced = self.client.get("/api/admin/overview")
        self.assertEqual(forced.status_code, 403)
        self.assertEqual(forced.json()["code"], "password_change_required")
        self._change_password(
            "Bootstrap-Password-2026!",
            "Safer-Root-Access-Password-2026!",
            csrf,
        )

        csrf = self._login("admin", "Safer-Root-Access-Password-2026!")
        missing_csrf = self.client.post(
            "/api/admin/users",
            json={
                "username": "investor01",
                "display_name": "投资用户",
                "role": "user",
                "temporary_password": "Investor-Temporary-2026!",
            },
        )
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(missing_csrf.json()["code"], "csrf_failed")

        created = self.client.post(
            "/api/admin/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "username": "investor01",
                "display_name": "投资用户",
                "role": "user",
                "temporary_password": "Investor-Temporary-2026!",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotIn("password_hash", created.json()["user"])
        self.assertNotIn("temporary_password", created.json()["user"])
        created_user_id = created.json()["user"]["id"]

        with patch.object(
            self.agent_router.repository,
            "get_run",
            return_value={"id": "run-foreign", "user_id": "another-user"},
        ):
            self.assertEqual(
                self.client.get("/api/v1/agent/runs/run-foreign").status_code,
                200,
            )

        self.client.cookies.clear()
        user_csrf = self._login("investor01", "Investor-Temporary-2026!")
        self._change_password(
            "Investor-Temporary-2026!",
            "Safer-Investor-Password-2026!",
            user_csrf,
        )
        user_csrf = self._login("investor01", "Safer-Investor-Password-2026!")
        forbidden = self.client.get("/api/admin/overview")
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["detail"], "需要管理员权限")

        with patch.object(
            self.agent_router.repository,
            "get_run",
            return_value={"id": "run-foreign", "user_id": "another-user"},
        ):
            self.assertEqual(
                self.client.get("/api/v1/agent/runs/run-foreign").status_code,
                404,
            )
        with patch.object(
            self.agent_router.repository,
            "get_run",
            return_value={"id": "run-owned", "user_id": created_user_id},
        ):
            self.assertEqual(
                self.client.get("/api/v1/agent/runs/run-owned").status_code,
                200,
            )

        with patch.object(
            self.portfolio_router.storage,
            "add_watch",
            return_value={"market": "A股", "symbol": "600519", "name": "我的自选"},
        ) as add_watch:
            response = self.client.post(
                "/api/watchlist",
                headers={"X-CSRF-Token": user_csrf},
                json={"market": "A股", "symbol": "600519", "name": "我的自选"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(add_watch.call_args.kwargs["user_id"], created_user_id)


if __name__ == "__main__":
    unittest.main()
