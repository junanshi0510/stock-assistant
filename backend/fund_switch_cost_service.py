# -*- coding: utf-8 -*-
"""Bind fund replacement research to one user's reconciled FIFO lots."""

from __future__ import annotations

from datetime import date

import funds
import fund_switch_execution_service
import fund_switch_lifecycle_service
import fund_switch_quote_service
import portfolio_review
import storage
from strategies.fund_switch_cost import evaluate_fund_switch_cost


class HoldingNotFoundError(LookupError):
    pass


def _unavailable_fee(code: str, error: Exception) -> dict:
    return {
        "status": "unavailable",
        "code": code,
        "reason": f"real_fee_schedule_unavailable:{str(error)[:180]}",
    }


def get_holding_fund_alternatives(
    holding_id: int,
    *,
    sort: str = "1y",
    limit: int = 3,
    months: int = 36,
    user_id: str = "default",
    review_on: date | None = None,
) -> dict:
    holding = next(
        (
            item
            for item in storage.list_holdings(user_id=user_id)
            if int(item.get("id") or 0) == int(holding_id)
        ),
        None,
    )
    if holding is None:
        raise HoldingNotFoundError("持仓不存在或不属于当前用户")
    if holding.get("asset_type") != "fund":
        raise ValueError("只有基金持仓可以核算基金替代成本")
    code = str(holding.get("code") or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError("基金持仓代码需要是 6 位数字")

    result = funds.get_fund_alternatives(
        code,
        sort=sort,
        limit=limit,
        months=months,
    )
    lot_snapshot = portfolio_review.remaining_lot_snapshot(
        "fund",
        code,
        user_id=user_id,
    )
    try:
        selected_fees = funds._fund_fee_schedule(code)
    except Exception as error:
        selected_fees = _unavailable_fee(code, error)

    valuation = {
        "unit_nav": (result.get("selected") or {}).get("unit_nav"),
        "as_of": (result.get("selected") or {}).get("as_of"),
        "source_url": f"https://fund.eastmoney.com/{code}.html",
    }
    reviewed = []
    for item in result.get("alternatives") or []:
        candidate_code = str(item.get("code") or "")
        try:
            candidate_fees = funds._fund_fee_schedule(candidate_code)
        except Exception as error:
            candidate_fees = _unavailable_fee(candidate_code, error)
        review = evaluate_fund_switch_cost(
            holding,
            lot_snapshot,
            selected_fees,
            candidate_fees,
            valuation,
            item.get("durability") or {},
            item.get("due_diligence") or {},
            candidate_code=candidate_code,
            candidate_name=str(item.get("name") or ""),
            review_on=review_on,
        )
        item["switch_cost_review"] = review
        if review.get("status") == "ready_for_platform_quote":
            persisted = storage.save_fund_switch_cost_review(
                review,
                int(holding_id),
                user_id=user_id,
            )
            item["switch_cost_binding"] = {
                "review_id": persisted.get("id"),
                "payload_sha256": persisted.get("payload_sha256"),
                "evidence_sha256": persisted.get("evidence_sha256"),
                "persisted_at": persisted.get("created_at"),
                "integrity_verified": bool(persisted.get("integrity_verified")),
                "deduplicated": bool(persisted.get("deduplicated")),
            }
            item["latest_platform_quote"] = fund_switch_quote_service.get_latest_quote(
                int(holding_id),
                candidate_code,
                user_id=user_id,
                expected_review_id=str(persisted.get("id") or ""),
                expected_review_payload_sha256=str(persisted.get("payload_sha256") or ""),
            )
        else:
            item["switch_cost_binding"] = None
            item["latest_platform_quote"] = None
        item["latest_execution_review"] = (
            fund_switch_execution_service.get_latest_execution_review(
                int(holding_id),
                candidate_code,
                user_id=user_id,
            )
        )
        try:
            item["switch_lifecycle"] = (
                fund_switch_lifecycle_service.get_candidate_context(
                    int(holding_id),
                    candidate_code,
                    user_id=user_id,
                )
            )
        except Exception as error:
            item["switch_lifecycle"] = {
                "status": "unavailable",
                "case": None,
                "error": f"fund_switch_lifecycle_unavailable:{str(error)[:180]}",
            }
        reviewed.append(review)

    ready = sum(item.get("status") == "ready_for_platform_quote" for item in reviewed)
    blocked = len(reviewed) - ready
    result["switch_cost_audit"] = {
        "diagnostic_id": "fund_switch_cost_review",
        "diagnostic_version": "1.0.0",
        "status": "evaluated" if reviewed else "unavailable",
        "holding_id": int(holding_id),
        "selected_code": code,
        "review_on": (review_on or date.today()).isoformat(),
        "summary": {
            "candidate_count": len(reviewed),
            "ready_for_platform_quote_count": ready,
            "blocked_count": blocked,
            "transaction_lots_missing_count": sum(
                item.get("status") == "transaction_lots_missing" for item in reviewed
            ),
            "share_reconciliation_failed_count": sum(
                item.get("status") == "share_reconciliation_failed" for item in reviewed
            ),
            "current_platform_quote_count": sum(
                (item.get("latest_platform_quote") or {}).get("status") == "confirmed_current"
                for item in result.get("alternatives") or []
            ),
            "stale_platform_quote_count": sum(
                (item.get("latest_platform_quote") or {}).get("status")
                in {"expired", "superseded", "integrity_failed"}
                for item in result.get("alternatives") or []
            ),
            "redemption_review_ready_count": sum(
                (item.get("latest_execution_review") or {}).get("status")
                == "ready_for_redemption_review"
                for item in result.get("alternatives") or []
            ),
            "execution_review_blocked_count": sum(
                bool(item.get("latest_execution_review"))
                and (item.get("latest_execution_review") or {}).get("status")
                != "ready_for_redemption_review"
                for item in result.get("alternatives") or []
            ),
            "active_switch_case_count": sum(
                bool(((item.get("switch_lifecycle") or {}).get("case")))
                and (((item.get("switch_lifecycle") or {}).get("case") or {}).get("status"))
                not in {
                    "completed_attribution_available",
                    "completed_attribution_blocked",
                    "integrity_failed",
                }
                for item in result.get("alternatives") or []
            ),
            "reconciled_switch_case_count": sum(
                bool(
                    ((((item.get("switch_lifecycle") or {}).get("case") or {}).get("decision_gate") or {}).get("holdings_reconciled"))
                )
                for item in result.get("alternatives") or []
            ),
        },
        "ledger": {
            "transaction_count": lot_snapshot.get("transaction_count"),
            "remaining_lot_count": len(lot_snapshot.get("remaining_lots") or []),
            "integrity_issue_count": len(lot_snapshot.get("integrity_issues") or []),
        },
        "policy": "成本核算通过后仍需销售平台确认当日费用、到账时间和申购限制；系统禁止自动换仓。",
    }
    result["source"] = f"{result.get('source') or ''} + 用户交易账本 FIFO 剩余批次"
    return result
