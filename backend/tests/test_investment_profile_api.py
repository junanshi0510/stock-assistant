# -*- coding: utf-8 -*-
"""IPS API requires explicit consent and Agent Runs pin the active version."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import storage  # noqa: E402
from agent.repository import AgentRepository  # noqa: E402
from agent.comparison import compare_run_results  # noqa: E402
from investment_policy import CONSENT_TEXT_SHA256, CONSENT_VERSION  # noqa: E402
from routers import agent as agent_router  # noqa: E402
from routers import portfolio as portfolio_router  # noqa: E402
from tests.test_investment_policy import valid_policy  # noqa: E402


class InvestmentProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = storage._DB_PATH
        self.old_conn = storage._conn
        storage._DB_PATH = str(Path(self.temp_dir.name) / "portfolio.db")
        storage._conn = None

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.old_conn
        storage._DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def _draft_request(self):
        return portfolio_router.InvestmentProfileRequest(**valid_policy())

    def _activation_request(self, draft, *, acknowledged=True, expected_active_version_id=None):
        return portfolio_router.InvestmentProfileActivationRequest(
            acknowledged=acknowledged,
            expected_payload_sha256=draft["payload_sha256"],
            expected_active_version_id=expected_active_version_id,
            consent_version=CONSENT_VERSION,
            consent_text_sha256=CONSENT_TEXT_SHA256,
        )

    def test_api_creates_draft_and_refuses_activation_without_acknowledgement(self):
        created = portfolio_router.create_investment_profile_draft(self._draft_request())
        draft = created["draft"]
        self.assertTrue(created["validation"]["valid"])
        self.assertFalse(storage.get_investment_profile()["configured"])

        with self.assertRaises(HTTPException) as raised:
            portfolio_router.activate_investment_profile_version(
                draft["id"],
                self._activation_request(draft, acknowledged=False),
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(storage.get_investment_profile()["configured"])

    def test_api_activation_returns_current_profile_and_verified_audit(self):
        created = portfolio_router.create_investment_profile_draft(self._draft_request())
        draft = created["draft"]
        result = portfolio_router.activate_investment_profile_version(
            draft["id"],
            self._activation_request(draft),
        )

        self.assertTrue(result["activated"])
        self.assertTrue(result["profile"]["configured"])
        self.assertEqual(result["profile"]["profile_version_id"], draft["id"])
        self.assertTrue(result["audit"]["verified"])
        versions = portfolio_router.get_investment_profile_versions(limit=20)
        self.assertEqual(versions["count"], 1)

    def test_agent_run_pins_active_profile_version_in_input_and_column(self):
        created = portfolio_router.create_investment_profile_draft(self._draft_request())
        draft = created["draft"]
        portfolio_router.activate_investment_profile_version(
            draft["id"],
            self._activation_request(draft),
        )
        repository = AgentRepository(Path(self.temp_dir.name) / "agent.db")
        with (
            patch.object(agent_router, "repository", repository),
            patch.object(agent_router, "start_worker"),
        ):
            result = agent_router.create_agent_run(
                agent_router.CreateAgentRunRequest(code="001480"),
                idempotency_key="pin-profile-version",
            )

        run = result["run"]
        self.assertEqual(run["profile_version_id"], draft["id"])
        self.assertEqual(run["input"]["profile_version_id"], draft["id"])

    def test_rerun_comparison_exposes_investment_policy_version_change(self):
        result = {"fund": {"code": "001480", "as_of": "2026-07-10"}, "facts": []}
        comparison = compare_run_results(
            {"id": "run_new", "input": {"code": "001480"}, "result": result, "profile_version_id": "ips_v2"},
            {"id": "run_old", "input": {"code": "001480"}, "result": result, "profile_version_id": "ips_v1"},
        )
        profile_change = next(
            item for item in comparison["dimensions"]
            if item["key"] == "investment_policy_version"
        )
        self.assertTrue(profile_change["changed"])
        self.assertFalse(comparison["summary"]["stable"])


if __name__ == "__main__":
    unittest.main()
