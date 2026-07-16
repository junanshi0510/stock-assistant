from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from background_jobs import BackgroundJobLeaseError, BackgroundJobRepository


class BackgroundJobRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.db"
        self.repository = BackgroundJobRepository(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_payload_stays_in_database_and_lifecycle_is_hash_chained(self):
        payload = {"tool_name": "fund.analysis.get", "input": {"code": "001480"}}
        job, created = self.repository.create_job(
            job_type="tool_execution",
            queue_name="market-data",
            payload=payload,
            tenant_id="public",
            user_id="user-a",
            idempotency_key="run-a:fund-analysis",
        )
        duplicate, duplicate_created = self.repository.create_job(
            job_type="tool_execution",
            queue_name="market-data",
            payload=payload,
            tenant_id="public",
            user_id="user-a",
            idempotency_key="run-a:fund-analysis",
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], job["id"])

        self.repository.mark_dispatched(job["id"], "celery-1")
        claimed = self.repository.claim_job(job["id"], "market-worker-1")
        self.assertEqual(claimed["payload"], payload)
        completed = self.repository.complete_job(
            job["id"], "market-worker-1", {"status": "complete", "as_of": "2026-07-16"}
        )
        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(completed["result_verified"])
        self.assertTrue(self.repository.verify_event_chain(job["id"])["verified"])

    def test_stale_worker_is_fenced_and_retry_budget_is_durable(self):
        job, _ = self.repository.create_job(
            job_type="tool_execution",
            queue_name="llm",
            payload={"tool_name": "llm.fund_decision.synthesize", "input": {}},
            tenant_id="public",
            user_id="user-a",
            max_attempts=2,
        )
        self.repository.claim_job(job["id"], "llm-worker-1")
        with self.assertRaises(BackgroundJobLeaseError):
            self.repository.complete_job(job["id"], "llm-worker-stale", {})
        retried = self.repository.fail_job(
            job["id"],
            "llm-worker-1",
            error_code="MODEL_TIMEOUT",
            error_message="Authorization=secret-value model timeout",
            retryable=True,
            retry_delay_seconds=1,
        )
        self.assertEqual(retried["status"], "queued")
        self.assertNotIn("secret-value", retried["error_message"])

    def test_event_rows_are_database_immutable(self):
        job, _ = self.repository.create_job(
            job_type="ocr",
            queue_name="ocr",
            payload={"asset_id": "asset-1"},
            tenant_id="public",
            user_id="user-a",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE background_job_events SET event_type='tampered' WHERE job_id=?",
                    (job["id"],),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM background_job_events WHERE job_id=?", (job["id"],)
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()

