from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import health


class HealthReadinessTests(unittest.TestCase):
    def tearDown(self):
        health._cached = None

    def test_missing_required_worker_queue_fails_readiness(self):
        inspector = Mock()
        inspector.active_queues.return_value = {
            "market@host": [{"name": "market-data"}],
            "agent@host": [{"name": "agent"}],
        }
        with (
            patch.object(health, "uses_celery_queue", return_value=True),
            patch.object(health.celery_app.control, "inspect", return_value=inspector),
            patch.dict(
                os.environ,
                {"REQUIRED_WORKER_QUEUES": "agent,market-data,llm,ocr,scheduler"},
            ),
        ):
            result = health._worker_readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(result["missing_queues"], ["llm", "ocr", "scheduler"])

    def test_optional_dependency_failure_keeps_read_traffic_but_marks_full_service_degraded(self):
        with (
            patch.object(health, "_database_readiness", return_value={"ready": True}),
            patch.object(
                health,
                "redis_readiness",
                return_value={"ready": True, "queue_depths": {}},
            ),
            patch.object(health, "_worker_readiness", return_value={"ready": False}),
            patch.object(
                health,
                "_object_storage_readiness",
                return_value={"ready": True},
            ),
        ):
            result = health.readiness(use_cache=False)
        self.assertTrue(result["ready"])
        self.assertTrue(result["traffic_ready"])
        self.assertFalse(result["full_service_ready"])
        self.assertEqual(result["status"], "degraded")

    def test_database_failure_removes_traffic_readiness(self):
        with (
            patch.object(health, "_database_readiness", return_value={"ready": False}),
            patch.object(
                health,
                "redis_readiness",
                return_value={"ready": True, "queue_depths": {}},
            ),
            patch.object(health, "_worker_readiness", return_value={"ready": True}),
            patch.object(health, "_object_storage_readiness", return_value={"ready": True}),
        ):
            result = health.readiness(use_cache=False)
        self.assertFalse(result["ready"])
        self.assertFalse(result["traffic_ready"])
        self.assertFalse(result["full_service_ready"])
        self.assertEqual(result["status"], "outage")

    def test_required_object_storage_configuration_failure_is_visible(self):
        with (
            patch.dict(os.environ, {"REQUIRE_OBJECT_STORAGE": "true"}),
            patch(
                "object_storage.AliyunObjectStorage",
                side_effect=RuntimeError("configuration missing"),
            ),
        ):
            result = health._object_storage_readiness()
        self.assertFalse(result["ready"])
        self.assertTrue(result["required"])
        self.assertEqual(result["error"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
