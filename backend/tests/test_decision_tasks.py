# -*- coding: utf-8 -*-
"""Durable decision tasks must preserve user isolation and lifecycle truth."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import storage  # noqa: E402


def action(
    action_key: str = "single-position-limit",
    *,
    priority: str = "high",
    title: str = "单项持仓超过政策上限",
    detail: str = "当前比例高于用户确认上限。",
) -> dict:
    return {
        "id": action_key,
        "priority": priority,
        "category": "组合风险",
        "title": title,
        "detail": detail,
        "evidence": ["第一大持仓 48%", "政策上限 35%"],
        "target": "portfolio",
        "action_label": "检查持仓",
        "source": "用户确认持仓 + 已激活投资政策",
    }


class DecisionTaskStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = storage._DB_PATH
        self.old_conn = storage._conn
        storage._DB_PATH = str(Path(self.temp_dir.name) / "decision-tasks.db")
        storage._conn = None

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.old_conn
        storage._DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_condition_lifecycle_is_durable_and_auditable(self):
        created = storage.sync_decision_tasks(
            [action()],
            user_id="user-a",
            observed_at="2026-07-15T01:00:00.000+00:00",
        )
        task = created["items"][0]
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["revision"], 1)

        acknowledged = storage.update_decision_task(
            task["id"],
            "acknowledged",
            1,
            user_id="user-a",
            actor_id="auth-user-a",
        )["task"]
        self.assertEqual(acknowledged["status"], "acknowledged")
        self.assertEqual(acknowledged["revision"], 2)

        refreshed_action = action(detail="真实比例已经更新，但仍然超过政策上限。")
        unchanged = storage.sync_decision_tasks(
            [refreshed_action],
            user_id="user-a",
            observed_at="2026-07-15T02:00:00.000+00:00",
        )["items"][0]
        self.assertEqual(unchanged["status"], "acknowledged")
        self.assertEqual(unchanged["revision"], 2)
        self.assertIn("已经更新", unchanged["detail"])

        storage.sync_decision_tasks(
            [],
            user_id="user-a",
            observed_at="2026-07-15T03:00:00.000+00:00",
        )
        resolved = storage.list_decision_tasks(
            user_id="user-a",
            status="resolved",
        )["items"][0]
        self.assertEqual(resolved["revision"], 3)

        reopened = storage.sync_decision_tasks(
            [refreshed_action],
            user_id="user-a",
            observed_at="2026-07-15T04:00:00.000+00:00",
        )["items"][0]
        self.assertEqual(reopened["status"], "open")
        self.assertEqual(reopened["revision"], 4)
        self.assertIsNone(reopened["acknowledged_at"])

        events = storage.list_decision_task_events(task["id"], user_id="user-a")
        self.assertEqual(
            [item["event_type"] for item in events],
            ["task.created", "task.acknowledged", "task.auto_resolved", "task.reopened"],
        )
        self.assertTrue(storage.verify_decision_task_audit(task["id"], user_id="user-a")["verified"])

    def test_material_condition_change_reopens_acknowledged_task(self):
        task = storage.sync_decision_tasks([action()], user_id="user-a")["items"][0]
        storage.update_decision_task(
            task["id"], "acknowledged", task["revision"], user_id="user-a"
        )

        changed = storage.sync_decision_tasks(
            [action(priority="medium", title="单项仓位接近政策上限")],
            user_id="user-a",
        )["items"][0]

        self.assertEqual(changed["status"], "open")
        self.assertEqual(changed["revision"], 3)
        self.assertEqual(changed["priority"], "medium")
        self.assertEqual(
            storage.list_decision_task_events(task["id"], user_id="user-a")[-1]["event_type"],
            "task.changed",
        )

    def test_snooze_limits_and_elapsed_reopen_are_priority_aware(self):
        task = storage.sync_decision_tasks([action()], user_id="user-a")["items"][0]
        with self.assertRaises(storage.DecisionTaskValidationError):
            storage.update_decision_task(
                task["id"],
                "snoozed",
                task["revision"],
                user_id="user-a",
                snooze_hours=25,
            )

        snoozed = storage.update_decision_task(
            task["id"],
            "snoozed",
            task["revision"],
            user_id="user-a",
            snooze_hours=24,
        )["task"]
        self.assertEqual(snoozed["status"], "snoozed")

        reopened = storage.sync_decision_tasks(
            [action()],
            user_id="user-a",
            observed_at="2030-01-01T00:00:00.000+00:00",
        )["items"][0]
        self.assertEqual(reopened["status"], "open")
        self.assertIsNone(reopened["snoozed_until"])
        self.assertEqual(
            storage.list_decision_task_events(task["id"], user_id="user-a")[-1]["event_type"],
            "task.snooze_elapsed",
        )

    def test_user_scope_and_optimistic_revision_block_cross_access_and_stale_updates(self):
        task_a = storage.sync_decision_tasks([action()], user_id="user-a")["items"][0]
        task_b = storage.sync_decision_tasks([action()], user_id="user-b")["items"][0]
        self.assertNotEqual(task_a["id"], task_b["id"])
        self.assertIsNone(
            storage.update_decision_task(
                task_a["id"],
                "acknowledged",
                task_a["revision"],
                user_id="user-b",
            )
        )

        updated = storage.update_decision_task(
            task_a["id"],
            "acknowledged",
            task_a["revision"],
            user_id="user-a",
        )["task"]
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(storage.DecisionTaskConflictError):
            storage.update_decision_task(
                task_a["id"],
                "open",
                task_a["revision"],
                user_id="user-a",
            )
        self.assertEqual(storage.list_decision_tasks(user_id="user-b")["count"], 1)

    def test_event_rows_are_database_immutable(self):
        task = storage.sync_decision_tasks([action()], user_id="user-a")["items"][0]
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE decision_task_events SET details_json='{}' WHERE task_id=?",
                (task["id"],),
            )
        storage._get_conn().rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "DELETE FROM decision_task_events WHERE task_id=?",
                (task["id"],),
            )
        storage._get_conn().rollback()
        self.assertTrue(storage.verify_decision_task_audit(task["id"], user_id="user-a")["verified"])


if __name__ == "__main__":
    unittest.main()
