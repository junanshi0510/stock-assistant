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

    def test_all_dependencies_must_be_ready(self):
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
        self.assertFalse(result["ready"])

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
