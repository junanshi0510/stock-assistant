# -*- coding: utf-8 -*-
"""Trusted portfolio valuation must bind real observations to current holdings."""

from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import portfolio_valuation as valuation  # noqa: E402
from migrations import portfolio_valuation_v1 as valuation_migration  # noqa: E402
from portfolio_valuation_repository import PortfolioValuationRepository  # noqa: E402


NOW = dt.datetime(2026, 7, 22, 8, 0, tzinfo=dt.timezone.utc)


def observation(
    *,
    kind: str,
    market: str,
    symbol: str,
    currency: str,
    value: float,
    source: str = "Tushare test provider",
    quality: str = "primary",
) -> dict:
    return {
        "kind": kind,
        "asset_type": "currency" if kind == "fx" else "fund" if kind == "nav" else "stock",
        "market": market,
        "symbol": symbol,
        "currency": currency,
        "value": value,
        "as_of": "2026-07-22",
        "source": source,
        "source_url": "https://provider.example.test/data",
        "quality_status": quality,
        "retrieved_at": NOW.isoformat(),
        "expires_at": (NOW + dt.timedelta(hours=12)).isoformat(),
        "payload": {"value": value, "confirmed": True},
    }


def holding(
    holding_id: int,
    *,
    asset_type: str,
    market: str,
    code: str,
    shares: float | None,
    amount: float | None,
) -> dict:
    return {
        "id": holding_id,
        "asset_type": asset_type,
        "market": market,
        "code": code,
        "name": f"资产{holding_id}",
        "shares": shares,
        "amount": amount,
        "profit": 0,
        "updated_at": NOW.isoformat(),
    }


class PortfolioValuationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "valuation.sqlite3"
        self.repository = PortfolioValuationRepository(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def stock_loader(market: str, symbol: str, current: dt.datetime) -> dict:
        values = {
            ("A股", "600519"): (100.0, "CNY"),
            ("港股", "00700"): (50.0, "HKD"),
            ("美股", "AAPL"): (200.0, "USD"),
        }
        value, currency = values[(market, symbol)]
        return observation(
            kind="price",
            market=market,
            symbol=symbol,
            currency=currency,
            value=value,
        )

    @staticmethod
    def fund_loader(code: str, current: dt.datetime) -> dict:
        return observation(
            kind="nav",
            market="基金",
            symbol=code,
            currency="CNY",
            value=2.0,
            source="confirmed fund NAV",
        )

    @staticmethod
    def fx_loader(currency: str, current: dt.datetime) -> dict:
        rates = {"CNY": 1.0, "HKD": 0.92, "USD": 7.2}
        return observation(
            kind="fx",
            market="FX",
            symbol=f"{currency}/CNY",
            currency="CNY",
            value=rates[currency],
            source="central-bank reference rate",
            quality="identity" if currency == "CNY" else "primary",
        )

    def test_cross_market_values_are_converted_and_decision_eligible(self):
        holdings = [
            holding(1, asset_type="stock", market="A股", code="600519", shares=10, amount=900),
            holding(2, asset_type="stock", market="港股", code="00700", shares=100, amount=4000),
            holding(3, asset_type="stock", market="美股", code="AAPL", shares=5, amount=6500),
            holding(4, asset_type="fund", market="基金", code="001480", shares=1000, amount=1800),
        ]
        snapshot = valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            stock_loader=self.stock_loader,
            fund_loader=self.fund_loader,
            fx_loader=self.fx_loader,
            now=NOW,
        )

        payload = snapshot["payload"]
        self.assertEqual(snapshot["status"], "complete")
        self.assertAlmostEqual(payload["summary"]["total_value"], 14800.0)
        self.assertEqual(payload["coverage"]["automatic_value_pct"], 100.0)
        self.assertEqual(payload["coverage"]["professional_value_pct"], 100.0)
        self.assertTrue(payload["decision_gate"]["risk_analysis_eligible"])
        self.assertTrue(payload["decision_gate"]["trade_amount_eligible"])
        self.assertTrue(snapshot["integrity"]["verified"])
        self.assertEqual(len(payload["positions"]), 4)

    def test_recent_manual_amount_keeps_risk_review_available_but_blocks_trade_amount(self):
        holdings = [
            holding(1, asset_type="fund", market="基金", code="001480", shares=None, amount=10000),
        ]
        snapshot = valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            now=NOW,
        )
        payload = snapshot["payload"]
        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(payload["positions"][0]["valuation_method"], "manual_confirmed_amount")
        self.assertTrue(payload["decision_gate"]["risk_analysis_eligible"])
        self.assertFalse(payload["decision_gate"]["trade_amount_eligible"])
        self.assertEqual(payload["coverage"]["automatic_value_pct"], 0.0)

    def test_provider_failure_uses_explicit_manual_fallback(self):
        holdings = [
            holding(1, asset_type="stock", market="美股", code="AAPL", shares=5, amount=6500),
        ]

        def failed_stock_loader(_market, _symbol, _current):
            raise RuntimeError("provider unavailable")

        snapshot = valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            stock_loader=failed_stock_loader,
            fx_loader=self.fx_loader,
            now=NOW,
        )
        position = snapshot["payload"]["positions"][0]
        self.assertEqual(position["valuation_method"], "manual_confirmed_amount")
        self.assertIn("provider unavailable", " ".join(position["issues"]))
        self.assertFalse(snapshot["payload"]["decision_gate"]["trade_amount_eligible"])

    def test_provider_credentials_are_redacted_before_snapshot_persistence(self):
        holdings = [
            holding(1, asset_type="stock", market="美股", code="AAPL", shares=5, amount=6500),
        ]

        def leaked_key_loader(_market, _symbol, _current):
            raise RuntimeError(
                "403 for https://provider.example.test/data?apiKey=do-not-persist-me&limit=10"
            )

        snapshot = valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            stock_loader=leaked_key_loader,
            fx_loader=self.fx_loader,
            now=NOW,
        )
        persisted = " ".join(snapshot["payload"]["positions"][0]["issues"])
        self.assertNotIn("do-not-persist-me", persisted)
        self.assertIn("apiKey=[redacted]", persisted)

    def test_force_refresh_failure_reuses_a_still_current_immutable_observation(self):
        holdings = [
            holding(1, asset_type="stock", market="A股", code="600519", shares=10, amount=900),
        ]
        valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            stock_loader=self.stock_loader,
            fx_loader=self.fx_loader,
            now=NOW,
        )

        def failed_stock_loader(_market, _symbol, _current):
            raise RuntimeError("provider temporarily unavailable")

        snapshot = valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            stock_loader=failed_stock_loader,
            fx_loader=self.fx_loader,
            force=True,
            now=NOW + dt.timedelta(minutes=1),
        )
        position = snapshot["payload"]["positions"][0]
        self.assertEqual(position["price_cache"], "cache_fallback_current")
        self.assertEqual(position["freshness"], "current")
        self.assertTrue(snapshot["payload"]["decision_gate"]["risk_analysis_eligible"])
        self.assertIn("仍有效的缓存价格", " ".join(position["issues"]))

    def test_latest_snapshot_is_invalidated_when_holdings_change(self):
        original = [
            holding(1, asset_type="fund", market="基金", code="001480", shares=None, amount=10000),
        ]
        valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=original,
            now=NOW,
        )
        changed = [{**original[0], "amount": 12000, "updated_at": (NOW + dt.timedelta(minutes=1)).isoformat()}]
        latest = valuation.latest_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=changed,
            now=NOW + dt.timedelta(minutes=2),
        )
        self.assertFalse(latest["binding"]["current"])
        self.assertFalse(latest["runtime_gate"]["risk_analysis_eligible"])
        self.assertIn("持仓已变化", " ".join(latest["runtime_gate"]["reasons"]))

    def test_current_snapshot_overlays_amounts_without_mutating_holdings(self):
        holdings = [
            holding(1, asset_type="stock", market="A股", code="600519", shares=10, amount=900),
        ]
        valuation.refresh_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            stock_loader=self.stock_loader,
            fx_loader=self.fx_loader,
            now=NOW,
        )
        current = valuation.latest_portfolio_valuation(
            user_id="user-a",
            repository=self.repository,
            holdings=holdings,
            now=NOW + dt.timedelta(minutes=1),
        )
        valued = valuation.apply_valuation_to_holdings(holdings, current)
        self.assertEqual(holdings[0]["amount"], 900)
        self.assertEqual(valued[0]["amount"], 1000.0)
        self.assertEqual(valued[0]["valuation_currency"], "CNY")

    def test_market_observations_and_snapshots_are_immutable(self):
        saved = self.repository.save_observation(
            observation(
                kind="price",
                market="A股",
                symbol="600519",
                currency="CNY",
                value=100,
            )
        )
        payload = {
            "created_at": NOW.isoformat(),
            "base_currency": "CNY",
            "fresh_until": (NOW + dt.timedelta(hours=1)).isoformat(),
            "summary": {"total_value": 1000},
            "coverage": {"count_coverage_pct": 100},
            "decision_gate": {"risk_analysis_eligible": True},
            "positions": [],
        }
        snapshot = self.repository.create_snapshot(
            tenant_id="public",
            user_id="user-a",
            actor_id="user-a",
            holdings_sha256="a" * 64,
            status="complete",
            payload=payload,
        )
        self.assertIsNone(
            self.repository.get_snapshot(
                snapshot["id"], tenant_id="public", user_id="user-b"
            )
        )
        self.assertEqual(
            self.repository.list_snapshots(
                tenant_id="public", user_id="user-b", limit=20
            ),
            [],
        )
        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute(
                    "UPDATE market_observations SET value=101 WHERE id=?",
                    (saved["id"],),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute(
                    "DELETE FROM portfolio_valuation_snapshots WHERE id=?",
                    (snapshot["id"],),
                )
        finally:
            connection.close()

    def test_postgres_migration_declares_scoped_immutable_valuation_facts(self):
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS market_observations",
            valuation_migration.POSTGRES_DDL,
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS portfolio_valuation_snapshots",
            valuation_migration.POSTGRES_DDL,
        )
        self.assertIn("tenant_id TEXT NOT NULL", valuation_migration.POSTGRES_DDL)
        self.assertIn("user_id TEXT NOT NULL", valuation_migration.POSTGRES_DDL)
        self.assertIn(
            "BEFORE UPDATE OR DELETE",
            inspect.getsource(valuation_migration.install_portfolio_valuation_schema),
        )


if __name__ == "__main__":
    unittest.main()
