from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import task_queue


class TaskQueueProtocolTests(unittest.TestCase):
    def _dispatch(self, job):
        repository = Mock()
        with (
            patch.object(task_queue, "uses_celery_queue", return_value=True),
            patch.object(task_queue, "_assert_queue_ready"),
            patch.object(
                task_queue.celery_app,
                "send_task",
                return_value=SimpleNamespace(id="celery-task-1"),
            ) as send_task,
        ):
            task_id = task_queue.enqueue_background_job(job, repository)
        return task_id, repository, send_task

    def test_market_data_message_contains_only_job_id(self):
        job = {
            "id": "job-market-1",
            "job_type": "market_data_operation",
            "queue_name": task_queue.QUEUE_MARKET,
            "payload": {"operation": "fund.analyze", "input": {"code": "013403"}},
        }
        task_id, repository, send_task = self._dispatch(job)
        self.assertEqual(task_id, "celery-task-1")
        send_task.assert_called_once_with(
            task_queue.TASK_MARKET_DATA,
            args=["job-market-1"],
            queue=task_queue.QUEUE_MARKET,
            task_id="job-job-market-1",
        )
        serialized_call = repr(send_task.call_args)
        self.assertNotIn("013403", serialized_call)
        self.assertNotIn("fund.analyze", serialized_call)
        repository.mark_dispatched.assert_called_once_with(
            "job-market-1", "celery-task-1"
        )

    def test_tool_and_ocr_jobs_are_routed_by_type_not_only_queue(self):
        cases = (
            (
                {
                    "id": "job-tool",
                    "job_type": "tool_execution",
                    "queue_name": task_queue.QUEUE_MARKET,
                },
                task_queue.TASK_MARKET_TOOL,
            ),
            (
                {
                    "id": "job-llm",
                    "job_type": "tool_execution",
                    "queue_name": task_queue.QUEUE_LLM,
                },
                task_queue.TASK_LLM_TOOL,
            ),
            (
                {
                    "id": "job-ocr",
                    "job_type": "ocr",
                    "queue_name": task_queue.QUEUE_OCR,
                },
                task_queue.TASK_OCR,
            ),
            (
                {
                    "id": "job-opportunity",
                    "job_type": "opportunity_scan",
                    "queue_name": task_queue.QUEUE_MARKET,
                },
                task_queue.TASK_OPPORTUNITY_SCAN,
            ),
        )
        for job, expected_task in cases:
            with self.subTest(job=job["id"]):
                _, _, send_task = self._dispatch(job)
                self.assertEqual(send_task.call_args.args[0], expected_task)
                self.assertEqual(send_task.call_args.kwargs["args"], [job["id"]])

    def test_unregistered_queue_and_job_type_combination_is_rejected(self):
        with (
            patch.object(task_queue, "uses_celery_queue", return_value=True),
            patch.object(task_queue, "_assert_queue_ready"),
        ):
            with self.assertRaises(task_queue.TaskQueueConfigurationError):
                task_queue.enqueue_background_job(
                    {
                        "id": "job-invalid",
                        "job_type": "ocr",
                        "queue_name": task_queue.QUEUE_MARKET,
                    },
                    Mock(),
                )

    def test_availability_probe_is_periodic_and_scheduler_routed(self):
        self.assertEqual(
            task_queue.celery_app.conf.task_routes[task_queue.TASK_AVAILABILITY_PROBE],
            {"queue": task_queue.QUEUE_SCHEDULER},
        )
        schedule = task_queue.celery_app.conf.beat_schedule[
            "record-platform-availability"
        ]
        self.assertEqual(schedule["task"], task_queue.TASK_AVAILABILITY_PROBE)
        self.assertGreaterEqual(float(schedule["schedule"]), 60.0)
        self.assertEqual(schedule["options"]["expires"], 240)

    def test_opportunity_forward_observations_are_periodic_and_scheduler_routed(self):
        self.assertEqual(
            task_queue.celery_app.conf.task_routes[
                task_queue.TASK_OPPORTUNITY_OBSERVATIONS
            ],
            {"queue": task_queue.QUEUE_SCHEDULER},
        )
        schedule = task_queue.celery_app.conf.beat_schedule[
            "observe-opportunity-baskets"
        ]
        self.assertEqual(
            schedule["task"], task_queue.TASK_OPPORTUNITY_OBSERVATIONS
        )
        self.assertGreaterEqual(float(schedule["schedule"]), 900.0)
        self.assertEqual(schedule["options"]["expires"], 900)


if __name__ == "__main__":
    unittest.main()
