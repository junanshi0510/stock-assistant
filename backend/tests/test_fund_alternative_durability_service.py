# -*- coding: utf-8 -*-
"""Fund alternative service preserves real-source and share-class gates."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


class FundAlternativeDurabilityServiceTests(unittest.TestCase):
    def test_share_class_deduplication_keeps_one_strategy(self):
        rows = [
            {"code": "000001", "name": "华夏恒生科技ETF联接(QDII)A"},
            {"code": "000002", "name": "华夏恒生科技ETF联接(QDII)C"},
            {"code": "000003", "name": "华商均衡成长混合A"},
            {"code": "000004", "name": "华商均衡成长混合C"},
        ]

        unique, excluded = funds._dedupe_fund_share_classes(
            rows,
            "华银健康生活主题灵活配置A",
        )

        self.assertEqual([item["code"] for item in unique], ["000001", "000003"])
        self.assertEqual([item["code"] for item in excluded], ["000002", "000004"])
        self.assertTrue(all(item["reason"] == "same_strategy_share_class" for item in excluded))

    def test_daily_return_audit_forwards_provider_returns_not_unit_nav(self):
        frames = {
            "001056": pd.DataFrame([
                {"date": "2026-01-01", "unit_nav": 9.9, "daily_return": 1.2},
                {"date": "2026-01-02", "unit_nav": 8.8, "daily_return": -0.4},
            ]),
            "000002": pd.DataFrame([
                {"date": "2026-01-01", "unit_nav": 99.0, "daily_return": 0.5},
                {"date": "2026-01-02", "unit_nav": 88.0, "daily_return": 0.2},
            ]),
        }
        expected = {"status": "evaluated", "candidates": []}
        with (
            patch.object(funds, "_fetch_nav_history", side_effect=lambda code, months: frames[code]),
            patch.object(
                funds,
                "evaluate_alternative_durability",
                return_value=expected,
            ) as evaluator,
        ):
            result = funds._alternative_durability_audit(
                {"code": "001056", "name": "当前基金"},
                [{"code": "000002", "name": "候选基金"}],
                36,
            )

        self.assertEqual(result, expected)
        selected, candidates = evaluator.call_args.args
        self.assertEqual(selected["points"][0]["daily_return_pct"], 1.2)
        self.assertNotIn("unit_nav", selected["points"][0])
        self.assertEqual(candidates[0]["points"][1]["daily_return_pct"], 0.2)

    def test_alternative_service_excludes_duplicate_share_classes_before_analysis(self):
        rank_items = [
            {"code": "001056", "name": "当前基金A", "rank": 9},
            {"code": "100001", "name": "策略一A", "rank": 1},
            {"code": "100002", "name": "策略一C", "rank": 2},
            {"code": "100003", "name": "策略二A", "rank": 3},
            {"code": "100004", "name": "策略三A", "rank": 4},
            {"code": "100005", "name": "策略四A", "rank": 5},
        ]

        def alternative(code, rank_row, selected_metrics, selected_rank, months):
            return {
                "code": code,
                "name": rank_row["name"],
                "rank": rank_row["rank"],
                "score": 80 - rank_row["rank"],
                "label": "优先研究",
                "metrics": {
                    "return_1y": 10.0,
                    "annual_volatility": 20.0,
                    "max_drawdown": -10.0,
                },
                "advantages": [],
                "cautions": [],
            }

        audit = {
            "status": "evaluated",
            "candidates": [
                {"code": code, "status": "mixed_evidence"}
                for code in ("100001", "100003", "100004")
            ],
            "summary": {},
        }
        due_diligence = {
            "status": "evaluated",
            "candidates": [
                {"code": code, "status": "blocked_by_durability"}
                for code in ("100001", "100003", "100004")
            ],
            "summary": {},
        }
        with (
            patch.object(funds, "_fund_search_one", return_value={"name": "当前基金A", "type": "混合型"}),
            patch.object(
                funds,
                "_fetch_rank",
                return_value={
                    "items": rank_items,
                    "category_name": "混合型",
                    "as_of": "2026-07-15",
                },
            ),
            patch.object(
                funds,
                "analyze_fund",
                return_value={
                    "name": "当前基金A",
                    "as_of": "2026-07-15",
                    "metrics": {},
                    "timing": {},
                    "fact_sheet": {"fee": {}},
                },
            ),
            patch.object(funds, "_alternative_row", side_effect=alternative) as loader,
            patch.object(funds, "_alternative_durability_audit", return_value=audit),
            patch.object(funds, "_alternative_due_diligence_audit", return_value=due_diligence),
        ):
            result = funds.get_fund_alternatives("001056", limit=3, months=36)

        loaded_codes = [call.args[0] for call in loader.call_args_list]
        self.assertNotIn("100002", loaded_codes)
        self.assertEqual(result["status"], "complete")
        self.assertEqual([row["code"] for row in result["alternatives"]], ["100001", "100003", "100004"])
        self.assertEqual(result["share_class_exclusions"][0]["code"], "100002")
        self.assertEqual(result["alternatives"][0]["durability"]["status"], "mixed_evidence")
        self.assertEqual(result["alternatives"][0]["due_diligence"]["status"], "blocked_by_durability")


if __name__ == "__main__":
    unittest.main()
