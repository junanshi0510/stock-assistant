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

    def test_explicit_cross_market_permissions_round_trip(self):
        saved = storage.save_investment_profile({
            "risk": "balanced",
            "horizon": "long",
            "monthly_budget": 2000,
            "max_single_ratio": 30,
            "allowed_fund_markets": ["mainland", "hong_kong", "united_states"],
            "accept_fx_risk": True,
        })

        self.assertEqual(saved["allowed_fund_markets"], ["mainland", "hong_kong", "united_states"])
        self.assertTrue(saved["accept_fx_risk"])


if __name__ == "__main__":
    unittest.main()
