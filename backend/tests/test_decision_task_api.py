# -*- coding: utf-8 -*-
"""Decision-task HTTP handlers must preserve ownership and public audit boundaries."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from auth import AuthPrincipal  # noqa: E402
from routers import portfolio as portfolio_router  # noqa: E402


def principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id="auth-user-a",
        subject_id="portfolio-user-a",
        username="user-a",
        display_name="User A",
        role="user",
        must_change_password=False,
        session_id="session-a",
    )


def private_task() -> dict:
    return {
        "id": "decision-task-a",
        "user_id": "portfolio-user-a",
        "action_key": "single-position-limit",
        "fingerprint": "secret-internal-fingerprint",
        "revision": 2,
        "status": "open",
        "priority": "high",
        "category": "组合风险",
        "title": "单项持仓超过政策上限",
        "detail": "当前比例高于用户确认上限。",
        "evidence": ["第一大持仓 48%"],
        "target": "portfolio",
        "action_label": "检查持仓",
        "source": "用户确认持仓",
        "first_seen_at": "2026-07-15T00:00:00+00:00",
        "last_seen_at": "2026-07-15T01:00:00+00:00",
        "acknowledged_at": None,
        "snoozed_until": None,
        "resolved_at": None,
    }


class DecisionTaskApiTests(unittest.TestCase):
    def test_summary_uses_subject_scope_without_market_refresh(self):
        with patch.object(
            portfolio_router.storage,
            "get_decision_task_summary",
            return_value={"open_count": 2, "generated_at": "2026-07-15T01:00:00+00:00"},
        ) as mocked:
            result = portfolio_router.get_decision_task_summary(principal=principal())

        mocked.assert_called_once_with(user_id="portfolio-user-a")
        self.assertEqual(result["open_count"], 2)

    def test_list_scopes_to_subject_and_hides_internal_identity_fields(self):
        with patch.object(portfolio_router.storage, "list_decision_tasks", return_value={
            "items": [private_task()],
            "count": 1,
            "summary": {"open_count": 1},
            "generated_at": "2026-07-15T01:00:00+00:00",
        }) as mocked:
            result = portfolio_router.get_decision_tasks(
                task_status=None,
                include_resolved=False,
                limit=50,
                principal=principal(),
            )

        mocked.assert_called_once_with(
            user_id="portfolio-user-a",
            status=None,
            include_resolved=False,
            limit=50,
        )
        self.assertNotIn("user_id", result["items"][0])
        self.assertNotIn("fingerprint", result["items"][0])

    def test_update_binds_subject_and_actor_and_returns_verified_chain(self):
        task = private_task()
        request = portfolio_router.DecisionTaskUpdateRequest(
            status="acknowledged",
            expected_revision=2,
        )
        with (
            patch.object(
                portfolio_router.storage,
                "update_decision_task",
                return_value={"task": {**task, "status": "acknowledged", "revision": 3}, "summary": {}},
            ) as mocked,
            patch.object(
                portfolio_router.storage,
                "verify_decision_task_audit",
                return_value={"verified": True},
            ),
        ):
            result = portfolio_router.update_decision_task(
                task["id"],
                request,
                principal=principal(),
            )

        mocked.assert_called_once_with(
            task["id"],
            "acknowledged",
            2,
            user_id="portfolio-user-a",
            actor_id="auth-user-a",
            snooze_hours=None,
        )
        self.assertTrue(result["audit"]["verified"])

    def test_non_snooze_request_rejects_snooze_hours(self):
        request = portfolio_router.DecisionTaskUpdateRequest(
            status="open",
            expected_revision=2,
            snooze_hours=24,
        )
        with self.assertRaises(HTTPException) as raised:
            portfolio_router.update_decision_task(
                "decision-task-a",
                request,
                principal=principal(),
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_audit_hides_actor_identity(self):
        with (
            patch.object(
                portfolio_router.storage,
                "verify_decision_task_audit",
                return_value={"verified": True, "event_count": 2},
            ),
            patch.object(
                portfolio_router.storage,
                "list_decision_task_events",
                return_value=[
                    {
                        "sequence_no": 1,
                        "event_type": "task.created",
                        "actor_id": "decision-engine",
                        "details": {},
                        "previous_hash": None,
                        "event_hash": "a" * 64,
                        "created_at": "2026-07-15T00:00:00+00:00",
                    },
                    {
                        "sequence_no": 2,
                        "event_type": "task.acknowledged",
                        "actor_id": "auth-user-a",
                        "details": {},
                        "previous_hash": "a" * 64,
                        "event_hash": "b" * 64,
                        "created_at": "2026-07-15T01:00:00+00:00",
                    },
                ],
            ),
        ):
            result = portfolio_router.get_decision_task_audit(
                "decision-task-a",
                principal=principal(),
            )

        self.assertEqual([item["actor"] for item in result["items"]], ["system", "user"])
        self.assertTrue(all("actor_id" not in item for item in result["items"]))

    def test_schedule_configuration_binds_subject_actor_and_hides_lease(self):
        private_schedule = {
            "id": "decision-check-a",
            "user_id": "portfolio-user-a",
            "status": "active",
            "enabled": True,
            "running": True,
            "interval_hours": 24,
            "revision": 3,
            "next_run_at": "2026-07-15T02:00:00+00:00",
            "lease_owner": "internal-worker-id",
            "lease_expires_at": "2026-07-15T02:02:00+00:00",
        }
        request = portfolio_router.DecisionCheckScheduleRequest(
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            expected_revision=2,
        )
        with (
            patch.object(
                portfolio_router.storage,
                "configure_decision_check_schedule",
                return_value=(private_schedule, True),
            ) as mocked,
            patch.object(
                portfolio_router.storage,
                "verify_decision_check_audit",
                return_value={"verified": True},
            ),
        ):
            result = portfolio_router.configure_decision_check_schedule(
                request,
                principal=principal(),
            )

        mocked.assert_called_once_with(
            "portfolio-user-a",
            enabled=True,
            interval_hours=24,
            run_immediately=True,
            expected_revision=2,
            actor_id="auth-user-a",
        )
        self.assertTrue(result["schedule"]["running"])
        self.assertNotIn("id", result["schedule"])
        self.assertNotIn("user_id", result["schedule"])
        self.assertNotIn("lease_owner", result["schedule"])

    def test_schedule_poll_skips_full_audit_unless_requested(self):
        private_schedule = {
            "id": "decision-check-a",
            "user_id": "portfolio-user-a",
            "status": "active",
            "enabled": True,
            "running": False,
            "interval_hours": 24,
            "revision": 1,
        }
        with (
            patch.object(
                portfolio_router.storage,
                "get_decision_check_schedule",
                return_value=private_schedule,
            ) as schedule_mock,
            patch.object(
                portfolio_router.storage,
                "verify_decision_check_audit",
                return_value={"verified": True},
            ) as audit_mock,
        ):
            result = portfolio_router.get_decision_check_schedule(
                verify_audit=False,
                principal=principal(),
            )

        schedule_mock.assert_called_once_with(user_id="portfolio-user-a")
        audit_mock.assert_not_called()
        self.assertIsNone(result["audit"])


if __name__ == "__main__":
    unittest.main()
