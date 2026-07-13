# -*- coding: utf-8 -*-
"""Investment market permissions must survive SQLite migration and reload."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import storage  # noqa: E402
from agent.portfolio_context import get_portfolio_context  # noqa: E402
from investment_policy import (  # noqa: E402
    CONSENT_TEXT_SHA256,
    CONSENT_VERSION,
    validate_investment_policy,
)
from tests.test_investment_policy import valid_policy  # noqa: E402


class InvestmentProfileStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = storage._DB_PATH
        self.old_conn = storage._conn
        storage._DB_PATH = str(Path(self.temp_dir.name) / "profile.db")
        storage._conn = None

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
        storage._conn = self.old_conn
        storage._DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_existing_profile_table_is_migrated_without_overseas_opt_in(self):
        connection = sqlite3.connect(storage._DB_PATH)
        connection.execute(
            """
            CREATE TABLE investment_profiles (
                user_id TEXT PRIMARY KEY,
                risk TEXT NOT NULL,
                horizon TEXT NOT NULL,
                monthly_budget REAL,
                max_single_ratio REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO investment_profiles VALUES ('default','balanced','mid_long',1000,35,'2026-07-10')"
        )
        connection.commit()
        connection.close()

        profile = storage.get_investment_profile()

        self.assertEqual(profile["allowed_fund_markets"], ["mainland"])
        self.assertFalse(profile["accept_fx_risk"])
        self.assertFalse(profile["configured"])
        self.assertEqual(
            profile["validation"]["errors"][0]["code"],
            "legacy_reconfirmation_required",
        )
        self.assertTrue(storage.verify_investment_profile_audit()["verified"])

    def _draft(self, **overrides):
        payload = valid_policy(**overrides)
        validation = validate_investment_policy(payload)
        return storage.create_investment_profile_draft(payload, validation), validation

    def _activate(self, draft, validation, expected_active_version_id=None):
        return storage.activate_investment_profile_version(
            draft["id"],
            expected_payload_sha256=validation["payload_sha256"],
            expected_active_version_id=expected_active_version_id,
            consent_version=CONSENT_VERSION,
            consent_text_sha256=CONSENT_TEXT_SHA256,
            review_cycle_months=validation["normalized"]["review_cycle_months"],
        )

    def test_draft_never_configures_agent_until_hash_bound_activation(self):
        draft, validation = self._draft(
            allowed_fund_markets=["mainland", "hong_kong", "united_states"],
            accept_fx_risk=True,
        )

        self.assertTrue(draft["created"])
        self.assertEqual(draft["status"], "draft")
        self.assertFalse(storage.get_investment_profile()["configured"])

        activated = self._activate(draft, validation)
        profile = storage.get_investment_profile()
        self.assertTrue(activated["activated"])
        self.assertTrue(profile["configured"])
        self.assertEqual(profile["profile_version_id"], draft["id"])
        self.assertEqual(
            profile["allowed_fund_markets"],
            ["mainland", "hong_kong", "united_states"],
        )
        self.assertTrue(profile["accept_fx_risk"])
        self.assertTrue(profile["integrity_verified"])
        self.assertTrue(storage.verify_investment_profile_audit()["verified"])

    def test_duplicate_draft_and_activation_are_idempotent(self):
        first, validation = self._draft()
        second, _ = self._draft()
        self.assertEqual(first["id"], second["id"])
        self.assertFalse(second["created"])

        activated = self._activate(first, validation)
        repeated = self._activate(first, validation)
        self.assertTrue(activated["activated"])
        self.assertFalse(repeated["activated"])
        self.assertEqual(len(storage.list_investment_profile_versions()), 1)

    def test_optimistic_activation_rejects_stale_active_version(self):
        first, first_validation = self._draft()
        self._activate(first, first_validation)
        second, second_validation = self._draft(monthly_budget=3000)
        third, third_validation = self._draft(monthly_budget=4000)
        self._activate(second, second_validation, expected_active_version_id=first["id"])

        with self.assertRaises(storage.InvestmentProfileConflictError):
            self._activate(third, third_validation, expected_active_version_id=first["id"])
        current = storage.get_investment_profile()
        self.assertEqual(current["profile_version_id"], second["id"])

    def test_invalid_draft_cannot_activate(self):
        draft, validation = self._draft(
            allowed_fund_markets=["mainland", "hong_kong"],
            accept_fx_risk=False,
        )
        self.assertFalse(validation["valid"])
        with self.assertRaises(storage.InvestmentProfileConflictError):
            self._activate(draft, validation)
        self.assertFalse(storage.get_investment_profile()["configured"])

    def test_version_payload_is_database_immutable_and_audit_tampering_is_detected(self):
        draft, validation = self._draft()
        self._activate(draft, validation)
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE investment_profile_versions SET payload_json='{}' WHERE id=?",
                (draft["id"],),
            )
        storage._get_conn().rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            storage._get_conn().execute(
                "UPDATE investment_profile_versions SET consent_version='changed' WHERE id=?",
                (draft["id"],),
            )
        storage._get_conn().rollback()
        self.assertTrue(storage.verify_investment_profile_integrity()["verified"])
        storage._get_conn().execute(
            "UPDATE investment_profile_audit_events SET details_json='{}' WHERE sequence_no=1"
        )
        storage._get_conn().commit()
        self.assertFalse(storage.verify_investment_profile_audit()["verified"])
        self.assertFalse(storage.verify_investment_profile_integrity()["verified"])
        self.assertFalse(storage.get_investment_profile()["configured"])

    def test_agent_context_can_read_exact_superseded_profile_version(self):
        first, first_validation = self._draft(max_single_ratio=30)
        self._activate(first, first_validation)
        second, second_validation = self._draft(max_single_ratio=40)
        self._activate(second, second_validation, expected_active_version_id=first["id"])

        context = get_portfolio_context({
            "code": "001480",
            "profile_version_id": first["id"],
        })
        self.assertTrue(context["profile"]["configured"])
        self.assertEqual(context["profile"]["profile_version_id"], first["id"])
        self.assertEqual(context["profile"]["max_single_ratio"], 30)
        self.assertEqual(context["method"]["profile_binding"], "exact_version_id_from_agent_run")


if __name__ == "__main__":
    unittest.main()
