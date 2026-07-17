# -*- coding: utf-8 -*-
"""Build a user-scoped review queue from immutable decisions and real outcomes."""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from typing import Any

from .repository import AgentRepository


REVIEW_STATUSES = frozenset({"blocked", "due", "ready", "upcoming", "unscheduled"})
REVIEW_FILTERS = REVIEW_STATUSES | {"attention"}
REVIEW_STATUS_PRIORITY = {
    "blocked": 0,
    "due": 1,
    "ready": 2,
    "upcoming": 3,
    "unscheduled": 4,
}


def _date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


class DecisionReviewService:
    """Connect a user's latest journal entry to later confirmed NAV Evidence."""

    MAX_CANDIDATES = 500

    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository

    @staticmethod
    def _feedback_view(feedback: dict[str, Any]) -> dict[str, Any]:
        return {
            key: feedback.get(key)
            for key in (
                "id",
                "run_id",
                "sequence_no",
                "schema_version",
                "feedback_verdict",
                "user_decision",
                "reason_codes",
                "note",
                "planned_review_at",
                "run_result_sha256",
                "previous_hash",
                "event_hash",
                "created_at",
                "integrity_verified",
            )
        }

    @staticmethod
    def _schedule_view(schedule: dict[str, Any] | None) -> dict[str, Any] | None:
        if schedule is None:
            return None
        return {
            key: schedule.get(key)
            for key in (
                "status",
                "interval_hours",
                "next_run_at",
                "consecutive_failures",
                "last_success_at",
                "last_provider_as_of",
                "last_error_code",
                "last_error_message",
            )
        }

    @staticmethod
    def _outcome_view(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
        if evidence is None or not evidence.get("integrity_verified"):
            return None
        payload = evidence.get("payload") or {}
        observed = payload.get("observed") or {}
        peer = payload.get("peer_comparison") or {}
        interpretation = payload.get("interpretation") or {}
        provider_as_of = (
            payload.get("provider_as_of")
            or observed.get("as_of")
            or evidence.get("as_of")
        )
        return {
            "evidence_id": evidence.get("id"),
            "provider_as_of": provider_as_of,
            "observed_as_of": observed.get("as_of"),
            "evaluation_status": payload.get("status"),
            "quality_status": evidence.get("quality_status"),
            "unit_nav": _number(observed.get("unit_nav")),
            "confirmed_nav_count": _integer(observed.get("confirmed_nav_count")),
            "return_pct": _number(observed.get("return_pct")),
            "peer_return_pct": _number(peer.get("period_return_pct")),
            "relative_excess_return_pct": _number(
                peer.get("relative_excess_return_pct")
            ),
            "interpretation_status": interpretation.get("status"),
            "interpretation_label": interpretation.get("label"),
            "source": payload.get("source") or evidence.get("provider"),
            "payload_sha256": evidence.get("payload_sha256"),
            "integrity_verified": True,
            "measurement": "fund_confirmed_nav_change_since_run_baseline",
            "user_execution_inferred": False,
            "personal_pnl_inferred": False,
        }

    def _review_item(
        self,
        feedback: dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
        as_of: dt.date,
    ) -> dict[str, Any] | None:
        run_id = str(feedback.get("run_id") or "")
        run = self.repository.get_run(run_id, include_details=False)
        if (
            run is None
            or str(run.get("tenant_id") or "") != tenant_id
            or str(run.get("user_id") or "") != user_id
        ):
            return None

        feedback_verification = self.repository.verify_run_feedback_chain(run_id)
        evidence_verification = self.repository.verify_run_evidence_integrity(run_id)
        outcome_evidence = self.repository.list_evidence_by_type(
            run_id,
            "outcome_observation",
            include_payload=True,
        )
        latest_outcome_evidence = outcome_evidence[0] if outcome_evidence else None
        latest_outcome_integrity = bool(
            latest_outcome_evidence is None
            or latest_outcome_evidence.get("integrity_verified")
        )
        current_outcome = (
            self._outcome_view(latest_outcome_evidence)
            if evidence_verification.get("verified") and latest_outcome_integrity
            else None
        )
        schedule = self.repository.get_outcome_schedule(run_id)

        planned_review = _date(feedback.get("planned_review_at"))
        outcome_date = _date((current_outcome or {}).get("observed_as_of"))
        blocked_reasons: list[str] = []
        if not feedback_verification.get("verified"):
            blocked_reasons.append("feedback_chain_invalid")
        if not evidence_verification.get("verified"):
            blocked_reasons.append("run_evidence_integrity_failed")
        if not latest_outcome_integrity:
            blocked_reasons.append("outcome_evidence_integrity_failed")
        if feedback.get("planned_review_at") and planned_review is None:
            blocked_reasons.append("invalid_planned_review_date")
        if (
            planned_review is not None
            and planned_review <= as_of
            and current_outcome is None
            and schedule is not None
            and schedule.get("status") == "paused"
            and schedule.get("last_error_code")
        ):
            blocked_reasons.append("outcome_collection_failed")

        if blocked_reasons:
            review_status = "blocked"
        elif planned_review is None:
            review_status = "unscheduled"
        elif planned_review > as_of:
            review_status = "upcoming"
        elif outcome_date is not None and outcome_date >= planned_review:
            review_status = "ready"
        else:
            review_status = "due"

        result = run.get("result") or {}
        fund = result.get("fund") or {}
        conclusion = result.get("conclusion") or {}
        decision = (result.get("personalized_decision") or {}).get("decision") or {}
        days_to_review = (
            (planned_review - as_of).days if planned_review is not None else None
        )
        next_action = {
            "blocked": "restore_verified_evidence",
            "due": "refresh_real_outcome",
            "ready": "review_original_decision",
            "upcoming": "wait_for_review_date",
            "unscheduled": "set_review_date",
        }[review_status]
        return {
            "run_id": run_id,
            "status": review_status,
            "needs_attention": review_status in {"blocked", "due", "ready"},
            "next_action": next_action,
            "planned_review_at": feedback.get("planned_review_at"),
            "days_to_review": days_to_review,
            "run": {
                "status": run.get("status"),
                "completed_at": run.get("completed_at"),
                "result_schema_version": result.get("schema_version"),
                "code": fund.get("code") or (run.get("input") or {}).get("code"),
                "name": fund.get("name"),
                "as_of": fund.get("as_of"),
                "headline": conclusion.get("headline"),
                "risk_band": conclusion.get("risk_band"),
                "timing_label": conclusion.get("timing_label"),
                "decision_action": decision.get("action"),
            },
            "feedback": self._feedback_view(feedback),
            "current_outcome": current_outcome,
            "schedule": self._schedule_view(schedule),
            "verification": {
                "feedback_verified": bool(feedback_verification.get("verified")),
                "feedback_event_count": int(
                    feedback_verification.get("event_count") or 0
                ),
                "evidence_verified": bool(evidence_verification.get("verified")),
                "evidence_count": int(evidence_verification.get("evidence_count") or 0),
                "blocked_reasons": blocked_reasons,
            },
        }

    def list_reviews(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 20,
        status: str | None = None,
        as_of: dt.date | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in REVIEW_FILTERS:
            raise ValueError(f"unsupported review status: {status}")
        page_size = max(1, min(50, int(limit)))
        review_date = as_of or dt.date.today()
        feedback_items, has_more_candidates = self.repository.list_latest_run_feedback(
            tenant_id=tenant_id,
            user_id=user_id,
            limit=self.MAX_CANDIDATES,
        )
        items = [
            item
            for item in (
                self._review_item(
                    feedback,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    as_of=review_date,
                )
                for feedback in feedback_items
            )
            if item is not None
        ]
        items.sort(
            key=lambda item: (
                REVIEW_STATUS_PRIORITY[item["status"]],
                item.get("planned_review_at") or "9999-12-31",
            )
        )
        counts = Counter(item["status"] for item in items)
        filtered = [
            item
            for item in items
            if status is None
            or (status == "attention" and item["needs_attention"])
            or item["status"] == status
        ]
        visible = filtered[:page_size]
        return {
            "as_of": review_date.isoformat(),
            "filter": status or "all",
            "items": visible,
            "count": len(visible),
            "filtered_total": len(filtered),
            "total_candidates": len(items),
            "has_more": len(filtered) > page_size,
            "candidate_window_truncated": has_more_candidates,
            "counts": {
                key: int(counts.get(key, 0))
                for key in ("blocked", "due", "ready", "upcoming", "unscheduled")
            },
            "policy": (
                "复盘队列只关联原 Run、用户最新决策版本和之后追加的真实确认净值 Evidence。"
                "净值变化描述标的表现，不推断用户已成交、个人盈亏或策略必然有效；"
                "原结论不会被重写，交易仍需用户单独确认。"
            ),
        }
