from __future__ import annotations

import io
import json
import logging
import unittest

from background_jobs import sanitize_worker_error
from observability import JsonFormatter, sanitize_log_value


class ObservabilityTests(unittest.TestCase):
    def test_database_redis_http_and_named_secrets_are_redacted(self):
        message = (
            "DATABASE_URL=postgresql://app:db-secret@127.0.0.1/db "
            "REDIS_URL=redis://:redis-secret@127.0.0.1/0 "
            "https://user:http-secret@example.com/path "
            "api_key=llm-secret"
        )
        sanitized = sanitize_log_value(message)
        for secret in ("db-secret", "redis-secret", "http-secret", "llm-secret"):
            self.assertNotIn(secret, sanitized)
        worker_message = sanitize_worker_error(message)
        for secret in ("db-secret", "redis-secret", "http-secret", "llm-secret"):
            self.assertNotIn(secret, worker_message)

    def test_json_formatter_emits_one_structured_record_without_traceback(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("observability-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        try:
            logger.info("password=do-not-log")
        finally:
            logger.handlers = []
            logger.propagate = True
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["logger"], "observability-test")
        self.assertNotIn("do-not-log", payload["message"])
        self.assertEqual(payload["level"], "INFO")


if __name__ == "__main__":
    unittest.main()
