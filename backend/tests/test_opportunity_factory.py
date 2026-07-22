from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import opportunity_service as service  # noqa: E402
from opportunity_repository import OpportunityRepository  # noqa: E402


def _definition() -> dict:
    value = service.strategy_templates()[0]
    value["markets"] = ["A股"]
    value["universe"] = {
        "include_presets": False,
        "include_watchlist": False,
        "hot_lists": [],
        "hot_limit_per_market": 8,
        "symbols": [
            {"market": "A股", "symbol": "600519", "name": "甲"},
            {"market": "A股", "symbol": "000858", "name": "乙"},
            {"market": "A股", "symbol": "600036", "name": "丙"},
        ],
    }
    value["gates"] = {
        **value["gates"],
        "min_technical_score": 0,
        "min_return_3m": -100,
        "max_annual_vol": 300,
        "max_drawdown_pct": 100,
        "min_factor_coverage": 0.4,
        "min_composite_score": 0,
    }
    return service.normalize_definition(value)


def _returns(seed: int, periods: int = 220) -> pd.Series:
    generator = np.random.default_rng(seed)
    values = generator.normal(0.0005, 0.012, periods)
    return pd.Series(values, index=pd.bdate_range("2025-01-02", periods=periods))


def _candidate(
    symbol: str,
    *,
    momentum: float,
    annual_vol: float,
    seed: int,
    fundamentals_available: bool = True,
    disqualifiers: list[dict] | None = None,
) -> dict:
    metrics = {
        "return_1m": momentum / 3,
        "return_3m": momentum,
        "return_6m": momentum * 1.5,
        "technical_score": 50 + momentum,
        "annual_vol": annual_vol,
        "downside_vol": annual_vol * 0.8,
        "max_drawdown_abs": annual_vol * 0.6,
        "pe": 18 + seed,
        "pb": 2 + seed / 10,
        "pe_percentile": 30 + seed,
        "pb_percentile": 35 + seed,
        "roe": 18 - seed,
        "gross_margin": 40 - seed,
        "net_margin": 15 - seed / 2,
        "debt_ratio": 42 + seed,
        "cashflow_quality": 1.1 - seed / 50,
        "revenue_growth": 12 - seed,
        "profit_growth": 14 - seed,
        "revenue_streak": 3,
        "profit_streak": 3,
    }
    if not fundamentals_available:
        for key in (
            "pe", "pb", "pe_percentile", "pb_percentile", "roe",
            "gross_margin", "net_margin", "debt_ratio", "cashflow_quality",
            "revenue_growth", "profit_growth", "revenue_streak", "profit_streak",
        ):
            metrics[key] = None
    return {
        "market": "A股",
        "symbol": symbol,
        "name": symbol,
        "universe_sources": ["test"],
        "status": "evaluated",
        "data": {
            "history_days": 220,
            "first_date": "2025-01-02",
            "last_date": "2025-11-05",
            "age_days": 0,
            "source": "deterministic-test",
            "retrieved_at": "2026-07-22T00:00:00+00:00",
            "last_close": 100 + seed,
        },
        "technical": {"score": metrics["technical_score"], "direction": "偏多"},
        "fundamentals": {
            "available": fundamentals_available,
            "source_error": None if fundamentals_available else "provider unavailable",
        },
        "metrics": metrics,
        "disqualifiers": list(disqualifiers or []),
        "_returns": _returns(seed),
    }


class OpportunityFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = OpportunityRepository(Path(self.temp.name) / "opportunity.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_definition_rejects_ambiguous_universe_and_invalid_symbol(self):
        value = _definition()
        value["universe"] = {
            "include_presets": False,
            "include_watchlist": False,
            "hot_lists": [],
            "hot_limit_per_market": 8,
            "symbols": [],
        }
        with self.assertRaisesRegex(ValueError, "候选池至少"):
            service.normalize_definition(value)

        value["universe"]["symbols"] = [
            {"market": "A股", "symbol": "AAPL", "name": "wrong market"}
        ]
        with self.assertRaisesRegex(ValueError, "A股代码格式无效"):
            service.normalize_definition(value)

    def test_strategy_versions_are_immutable_and_user_scoped(self):
        original = _definition()
        strategy = self.repo.create_strategy(
            user_id="owner", definition=original, actor_id="owner"
        )
        changed = {**original, "description": "这是第二个不可变策略版本，用于验证历史不会被覆盖。"}
        updated = self.repo.add_strategy_version(
            strategy["id"], user_id="owner", definition=changed, actor_id="owner"
        )
        self.assertEqual(updated["current_version_no"], 2)
        first = self.repo.get_strategy_version(strategy["version_id"], user_id="owner")
        self.assertEqual(first["definition"]["description"], original["description"])
        self.assertIsNone(
            self.repo.get_strategy(strategy["id"], user_id="another-user")
        )

        with closing(sqlite3.connect(self.repo.database_target)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE opportunity_strategy_versions SET actor_id='tampered' WHERE id=?",
                    (strategy["version_id"],),
                )

    def test_peer_factor_grades_are_market_relative_and_missing_is_visible(self):
        definition = _definition()
        rows = [
            _candidate("600519", momentum=24, annual_vol=18, seed=1),
            _candidate("000858", momentum=8, annual_vol=30, seed=2),
            _candidate(
                "600036", momentum=12, annual_vol=22, seed=3,
                fundamentals_available=False,
            ),
        ]
        service._grade_candidates(rows, definition)
        self.assertGreater(
            rows[0]["factors"]["momentum"]["score"],
            rows[1]["factors"]["momentum"]["score"],
        )
        self.assertFalse(rows[2]["factors"]["value"]["available"])
        self.assertAlmostEqual(rows[2]["factor_coverage"], 0.5)
        self.assertIn("缺少", " ".join(rows[2]["concerns"]))
        self.assertEqual(rows[0]["status"], "qualified")

    def test_hard_gate_cannot_be_offset_by_high_factor_score(self):
        definition = _definition()
        failed_gate = {
            "code": "data_stale",
            "label": "行情过旧",
            "actual": 20,
            "threshold": "<=10 天",
        }
        rows = [
            _candidate(
                "600519", momentum=40, annual_vol=12, seed=1,
                disqualifiers=[failed_gate],
            ),
            _candidate("000858", momentum=5, annual_vol=25, seed=2),
        ]
        service._grade_candidates(rows, definition)
        self.assertEqual(rows[0]["status"], "rejected")
        self.assertGreater(rows[0]["composite_score"], rows[1]["composite_score"])

    def test_portfolio_enforces_correlation_position_cap_and_cash(self):
        definition = _definition()
        definition["portfolio"] = {
            **definition["portfolio"],
            "max_positions": 3,
            "max_position_pct": 40,
            "min_cash_pct": 10,
            "max_pair_correlation": 0.8,
        }
        first = _candidate("600519", momentum=25, annual_vol=20, seed=1)
        duplicate = _candidate("000858", momentum=20, annual_vol=20, seed=2)
        diverse = _candidate("600036", momentum=15, annual_vol=20, seed=3)
        duplicate["_returns"] = first["_returns"].copy()
        for rank, row in enumerate((first, duplicate, diverse), 1):
            row.update({"status": "qualified", "rank": rank, "composite_score": 80 - rank})
        proposal = service._portfolio_proposal(
            [first, duplicate, diverse],
            definition,
            [{"market": "A股", "status": "mixed"}],
        )
        self.assertEqual(proposal["position_count"], 2)
        self.assertEqual(proposal["correlation_exclusions"][0]["symbol"], "000858")
        self.assertTrue(all(item["weight_pct"] <= 40 for item in proposal["positions"]))
        self.assertGreaterEqual(proposal["cash_pct"], 20)

    def test_complete_run_freezes_result_and_builds_audit_chain(self):
        definition = _definition()
        strategy = self.repo.create_strategy(
            user_id="owner", definition=definition, actor_id="owner"
        )
        run = self.repo.create_run(strategy["id"], user_id="owner", actor_id="owner")
        items = definition["universe"]["symbols"]

        def evaluate(item, _definition_value):
            index = next(i for i, value in enumerate(items) if value["symbol"] == item["symbol"])
            return _candidate(
                item["symbol"], momentum=24 - index * 5, annual_vol=18 + index * 4, seed=index + 1
            )

        universe = {
            "scope": "candidate_pool",
            "scope_label": "候选池（非交易所全量）",
            "licensed_full_market": False,
            "items": [{**item, "universe_sources": ["manual"]} for item in items],
            "count": len(items),
            "source_counts": {"manual": len(items)},
            "warnings": [],
            "truncated_count": 0,
        }
        with (
            patch.object(service, "_resolve_universe", return_value=universe),
            patch.object(service, "_evaluate_candidate", side_effect=evaluate),
        ):
            completed = service.execute_run(
                run["id"], user_id="owner", repo=self.repo
            )
        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(completed["result_verified"])
        self.assertEqual(completed["result"]["funnel"]["universe"], 3)
        self.assertEqual(
            [event["event_type"] for event in completed["events"]],
            ["run.created", "run.started", "run.completed"],
        )
        with self.assertRaisesRegex(Exception, "已经结束|已经冻结"):
            self.repo.complete_run(
                run["id"],
                user_id="owner",
                result={"tampered": True},
                status="succeeded",
                actor_id="tamper",
            )

    def test_paper_observations_are_append_only_and_user_scoped(self):
        definition = _definition()
        strategy = self.repo.create_strategy(
            user_id="owner", definition=definition, actor_id="owner"
        )
        run = self.repo.create_run(strategy["id"], user_id="owner", actor_id="owner")
        self.repo.mark_running(run["id"], user_id="owner", actor_id="worker")
        result = {
            "funnel": {"evaluated": 2, "universe": 2},
            "strategy": {
                "id": strategy["id"], "version_no": 1, "name": definition["name"]
            },
            "portfolio": {
                "positions": [
                    {
                        "market": "A股", "symbol": "600519", "name": "甲",
                        "weight_pct": 40, "entry_price": 100, "entry_date": "2026-07-01",
                        "price_source": "test",
                    }
                ],
                "cash_pct": 60,
                "warnings": [],
            },
        }
        self.repo.complete_run(
            run["id"], user_id="owner", result=result,
            status="succeeded", actor_id="worker",
        )
        basket, created = service.create_paper_basket(
            run["id"], user_id="owner", repo=self.repo
        )
        self.assertTrue(created)
        self.assertIsNone(
            self.repo.get_paper_basket(basket["id"], user_id="another-user")
        )

        frame = pd.DataFrame(
            {
                "date": pd.bdate_range("2026-07-01", periods=10),
                "open": range(100, 110), "high": range(101, 111),
                "low": range(99, 109), "close": range(100, 110),
                "volume": [1000] * 10,
            }
        )
        frame.attrs["source"] = "test-provider"
        with patch.object(service.data_fetch, "get_history_months", return_value=frame):
            first = service.observe_paper_basket(
                basket["id"], user_id="owner", repo=self.repo
            )
            second = service.observe_paper_basket(
                basket["id"], user_id="owner", repo=self.repo
            )
        self.assertEqual(first["sequence_no"], 1)
        self.assertEqual(second["sequence_no"], 2)
        self.assertEqual(second["previous_hash"], first["event_hash"])
        self.assertTrue(second["payload_verified"])


if __name__ == "__main__":
    unittest.main()
