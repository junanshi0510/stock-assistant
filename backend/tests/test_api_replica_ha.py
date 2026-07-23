from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import availability_service
import main


class ApiReplicaContractTests(unittest.TestCase):
    def test_health_and_response_headers_expose_safe_replica_release_identity(self):
        with patch.dict(
            os.environ,
            {"API_REPLICA_ID": "api-8001", "APP_RELEASE_ID": "abc123release"},
        ):
            with TestClient(main.app) as client:
                response = client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-stock-assistant-replica"], "api-8001")
        self.assertEqual(response.headers["x-stock-assistant-release"], "abc123release")
        self.assertEqual(response.json()["api_replica"]["replica_id"], "api-8001")
        self.assertEqual(response.json()["api_replica"]["release_id"], "abc123release")

    def test_edge_readiness_does_not_expose_dependency_topology(self):
        with patch("main.health.readiness") as readiness:
            readiness.return_value = {
                "ready": True,
                "database": {"target": "postgresql://internal-host/private"},
                "object_storage": {"bucket": "private-bucket"},
                "workers": {"workers": {"worker@internal-host": ["agent"]}},
            }
            with TestClient(main.app) as client:
                response = client.get("/health/edge")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"schema_version", "ready", "status", "api_replica"},
        )
        serialized = response.text
        self.assertNotIn("internal-host", serialized)
        self.assertNotIn("private-bucket", serialized)

    def test_replica_configuration_rejects_non_loopback_and_credentials(self):
        with patch.dict(
            os.environ,
            {
                "API_REPLICA_ENDPOINTS": (
                    "public=https://example.com,"
                    "credential=http://user:password@127.0.0.1:8001"
                )
            },
        ):
            specs = availability_service._api_replica_specs()
        self.assertEqual(len(specs), 2)
        self.assertTrue(all(item["configuration_error"] for item in specs))

    def test_deployment_templates_require_two_replicas_and_safe_nginx_retry(self):
        root = Path(__file__).resolve().parents[2]
        nginx = (root / "deploy" / "nginx-stock-assistant.conf").read_text(encoding="utf-8")
        upstreams = (root / "deploy" / "stock-assistant-api-upstreams.conf").read_text(
            encoding="utf-8"
        )
        unit = (root / "deploy" / "stock-assistant-api@.service").read_text(encoding="utf-8")
        healthcheck = (root / "deploy" / "scripts" / "check-runtime.sh").read_text(encoding="utf-8")
        rollout = (root / "deploy" / "scripts" / "rollout-api-release.sh").read_text(encoding="utf-8")

        self.assertIn("stock-assistant-api-upstreams.conf", nginx)
        self.assertIn("127.0.0.1:8001", upstreams)
        self.assertIn("127.0.0.1:8002", upstreams)
        self.assertIn("proxy_next_upstream_tries 2", nginx)
        self.assertNotIn("non_idempotent", nginx)
        self.assertIn("Content-Security-Policy", nginx)
        self.assertIn("frame-ancestors 'none'", nginx)
        self.assertIn("location = /health/edge", nginx)
        self.assertIn("allow 127.0.0.1", nginx)
        self.assertIn("WorkingDirectory=/opt/stock-assistant-api/%i/backend", unit)
        self.assertIn("API_REPLICA_ID=api-%i", unit)
        self.assertIn("/opt/stock-assistant-api/%i/.venv/bin/python", unit)
        self.assertIn("API_REPLICA_REQUIRED", healthcheck)
        self.assertIn("ready API replicas run different releases", healthcheck)
        self.assertIn("duplicate API replica port", rollout)
        self.assertIn("atomic_link", rollout)
        self.assertIn("write_upstream_config", rollout)
        self.assertIn("down;", rollout)
        self.assertIn("public readiness or release identity is invalid", rollout)
        self.assertIn("restoring previous API slots", rollout)
        self.assertFalse((root / "deploy" / "stock-assistant-api.service").exists())


if __name__ == "__main__":
    unittest.main()
