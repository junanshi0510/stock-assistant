# -*- coding: utf-8 -*-
"""Scheduled decision checks must be isolated, leased, and auditable."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import storage  # noqa: E402
from decision_check_worker import DecisionCheckWorker  # noqa: E402


class DecisionCheckScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = storage._DB_PATH
        self.old_conn = storage._conn
        storage._DB_PATH = str(Path(self.temp_dir.name) / "decision-checks.db")
        storage._conn = None

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.old_conn
        storage._DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_schedule_configuration_is_user_scoped_and_revision_guarded(self):
        schedule_a, changed = storage.configure_decision_check_schedule(
            "user-a",
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            actor_id="auth-a",
            now="2026-07-15T00:00:00+00:00",
        )
        schedule_b, _ = storage.configure_decision_check_schedule(
            "user-b",
            enabled=False,
            interval_hours=168,
            actor_id="auth-b",
            now="2026-07-15T00:00:00+00:00",
        )

        self.assertTrue(changed)
        self.assertTrue(schedule_a["enabled"])
        self.assertEqual(schedule_a["next_run_at"], "2026-07-15T00:00:00.000+00:00")
        self.assertFalse(schedule_b["enabled"])
        self.assertNotEqual(schedule_a["id"], schedule_b["id"])
        self.assertEqual(storage.get_decision_check_schedule("user-a")["interval_hours"], 24)
        self.assertEqual(storage.get_decision_check_schedule("user-b")["interval_hours"], 168)

        with self.assertRaises(storage.DecisionCheckConflictError):
            storage.configure_decision_check_schedule(
                "user-a",
                enabled=False,
                interval_hours=24,
                expected_revision=99,
                actor_id="auth-a",
            )

    def test_lease_prevents_duplicate_completion_and_preserves_audit_chain(self):
        schedule, _ = storage.configure_decision_check_schedule(
            "user-a",
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now="2026-07-15T00:00:00+00:00",
        )
        claimed = storage.claim_due_decision_check(
            "worker-a",
            lease_seconds=120,
            now="2026-07-15T00:00:00+00:00",
        )
        duplicate = storage.claim_due_decision_check(
            "worker-b",
            lease_seconds=120,
            now="2026-07-15T00:01:00+00:00",
        )

        self.assertEqual(claimed["id"], schedule["id"])
        self.assertIsNone(duplicate)
        completed = storage.complete_decision_check(
            schedule["id"],
            "worker-a",
            result_status="succeeded",
            open_count=3,
            unavailable_count=0,
            now="2026-07-15T00:02:00+00:00",
        )
        self.assertEqual(completed["last_result_status"], "succeeded")
        self.assertEqual(completed["last_open_count"], 3)
        self.assertEqual(completed["next_run_at"], "2026-07-16T00:02:00.000+00:00")
        self.assertTrue(storage.verify_decision_check_audit("user-a")["verified"])

        with self.assertRaises(storage.DecisionCheckLeaseError):
            storage.complete_decision_check(
                schedule["id"],
                "worker-a",
                result_status="succeeded",
                open_count=0,
                unavailable_count=0,
            )

    def test_failure_uses_backoff_without_inventing_success(self):
        schedule, _ = storage.configure_decision_check_schedule(
            "user-a",
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now="2026-07-15T00:00:00+00:00",
        )
        storage.claim_due_decision_check(
            "worker-a",
            now="2026-07-15T00:00:00+00:00",
        )
        failed = storage.fail_decision_check(
            schedule["id"],
            "worker-a",
            error_code="PROVIDER_FAILURE",
            error_message="真实数据源不可用",
            now="2026-07-15T00:01:00+00:00",
        )

        self.assertEqual(failed["last_result_status"], "failed")
        self.assertEqual(failed["consecutive_failures"], 1)
        self.assertEqual(failed["next_run_at"], "2026-07-15T00:16:00.000+00:00")
        self.assertIsNone(failed["last_success_at"])

    def test_schedule_events_are_database_immutable(self):
        storage.configure_decision_check_schedule(
            "user-a",
            enabled=True,
            interval_hours=72,
            actor_id="auth-a",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE decision_check_events SET details_json='{}' WHERE user_id=?",
                ("user-a",),
            )
        storage._get_conn().rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "DELETE FROM decision_check_events WHERE user_id=?",
                ("user-a",),
            )
        storage._get_conn().rollback()

    def test_worker_records_partial_real_data_result(self):
        storage.configure_decision_check_schedule(
            "user-a",
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now="2026-07-15T00:00:00+00:00",
        )
        seen_users = []

        def run_check(*, user_id):
            seen_users.append(user_id)
            return {
                "summary": {"unavailable_count": 2},
                "task_inbox": {"summary": {"open_count": 4}},
            }

        worker = DecisionCheckWorker(
            run_check,
            store=storage,
            poll_interval=60,
            lease_seconds=120,
        )
        self.assertTrue(worker.run_once(now="2026-07-15T00:00:00+00:00"))

        result = storage.get_decision_check_schedule(
            "user-a",
            now="2026-07-15T00:01:00+00:00",
        )
        self.assertEqual(seen_users, ["user-a"])
        self.assertEqual(result["last_result_status"], "partial")
        self.assertEqual(result["last_open_count"], 4)
        self.assertEqual(result["last_unavailable_count"], 2)
        self.assertIsNone(result["last_success_at"])

    def test_worker_retries_malformed_result_instead_of_leaking_lease(self):
        storage.configure_decision_check_schedule(
            "user-a",
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            now="2026-07-15T00:00:00+00:00",
        )
        worker = DecisionCheckWorker(
            lambda **_kwargs: None,
            store=storage,
            poll_interval=60,
            lease_seconds=120,
        )

        self.assertTrue(worker.run_once(now="2026-07-15T00:00:00+00:00"))

        result = storage.get_decision_check_schedule("user-a")
        self.assertEqual(result["last_result_status"], "failed")
        self.assertEqual(result["consecutive_failures"], 1)
        self.assertIsNone(result["lease_owner"])


if __name__ == "__main__":
    unittest.main()
