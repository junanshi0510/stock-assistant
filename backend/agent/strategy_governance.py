# -*- coding: utf-8 -*-
"""Persistent strategy lifecycle and fail-closed runtime release checks."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from .repository import AgentRepository


RUNTIME_GATE_SCHEMA_VERSION = "strategy_runtime_gate.v1"
RELEASE_GATE_VERSION = "strategy_release_gate@1.0.0"

CONDITIONED_FORWARD_MANIFEST: dict[str, Any] = {
    "schema_version": "strategy_manifest.v1",
    "strategy_id": "fund_conditioned_forward_return",
    "strategy_version": "1.0.0",
    "name": "基金当前条件历史前瞻统计",
    "strategy_kind": "alpha_signal",
    "owner_id": "quant-research",
    "applicability": {
        "asset_types": ["fund"],
        "markets": ["mainland", "hong_kong", "united_states", "global", "unknown_cross_border"],
        "frequency": "confirmed_nav_date",
        "user_scenarios": ["fund_research", "personalized_fund_decision"],
    },
    "dependencies": {
        "tools": ["fund.analysis.get@1.0.0"],
        "minimum_history_observations": 60,
        "required_fields": ["confirmed_unit_nav", "confirmed_nav_date"],
    },
    "method": {
        "sampling": "calendar_month_last_observation",
        "matching_fields": ["trend", "drawdown_band"],
        "forward_horizons": ["3m", "6m", "12m"],
        "current_implementation_has_overlapping_forward_windows": True,
    },
    "required_release_checks": [
        "independent_out_of_sample_period",
        "non_overlapping_evaluation_windows",
        "investable_peer_benchmark",
        "transaction_cost_model",
        "minimum_shadow_outcomes",
        "independent_strategy_review",
    ],
    "release_checks": [
        {
            "code": "independent_out_of_sample_period",
            "status": "fail",
            "detail": "当前实现使用同一基金历史匹配，没有独立冻结的样本外区间。",
            "evidence_ref": "backend/strategies/fund_conditioned_forward.py@1.0.0",
        },
        {
            "code": "non_overlapping_evaluation_windows",
            "status": "fail",
            "detail": "月末样本的 3/6/12 个月前瞻窗口可能重叠，不能视为独立结果。",
            "evidence_ref": "method.current_implementation_has_overlapping_forward_windows",
        },
        {
            "code": "investable_peer_benchmark",
            "status": "fail",
            "detail": "当前只比较基金自身无条件分布，尚未形成可投资同类基准的样本外比较。",
            "evidence_ref": "backend/strategies/fund_conditioned_forward.py@1.0.0",
        },
        {
            "code": "transaction_cost_model",
            "status": "fail",
            "detail": "当前研究统计没有申购、赎回、汇率和机会成本模型。",
            "evidence_ref": "method.transaction_costs:not_applicable_no_trading_simulation",
        },
        {
            "code": "minimum_shadow_outcomes",
            "status": "fail",
            "detail": "尚未积累按策略版本分组、同口径且达到统计门槛的 Shadow Outcome。",
            "evidence_ref": "production_strategy_outcome_count:insufficient",
        },
        {
            "code": "independent_strategy_review",
            "status": "fail",
            "detail": "尚无独立策略 Reviewer 的生产发布审批。",
            "evidence_ref": "strategy_registry_audit:missing_release_approval",
        },
    ],
    "canary": {"percent": 0, "allowed_user_ids": []},
    "known_limitations": [
        "historical_results_are_not_forecasts",
        "single_fund_history_may_include_regime_changes",
        "fund_manager_or_mandate_changes_are_not_normalized",
    ],
    "rollback_version": None,
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _stable_bucket(strategy_id: str, strategy_version: str, user_id: str) -> int:
    payload = f"{strategy_id}|{strategy_version}|{user_id}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:8], 16) % 10000


class StrategyGovernanceService:
    """Own strategy version registration, release transitions, and runtime use policy."""

    _TRANSITIONS = {
        "draft": {"review", "paused", "retired"},
        "review": {"shadow", "paused", "retired"},
        "shadow": {"canary", "paused", "retired"},
        "canary": {"active", "paused", "retired"},
        "active": {"paused", "retired"},
        "paused": {"shadow", "retired"},
        "retired": set(),
    }

    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository

    def seed_defaults(self) -> list[dict[str, Any]]:
        item, created = self.repository.register_strategy_version(
            CONDITIONED_FORWARD_MANIFEST,
            initial_status="shadow",
            actor_role="system",
            actor_id="strategy-bootstrap-v1",
        )
        return [{"strategy": item, "created": created}]

    def _release_assessment(self, item: dict[str, Any]) -> dict[str, Any]:
        manifest = item.get("manifest") or {}
        required = [str(value) for value in manifest.get("required_release_checks") or []]
        declared = {
            str(check.get("code")): check
            for check in manifest.get("release_checks") or []
            if check.get("code")
        }
        checks = []
        for code in required:
            source = declared.get(code) or {}
            status = str(source.get("status") or "missing")
            checks.append({
                "code": code,
                "status": status,
                "detail": source.get("detail") or "策略清单未提供该发布检查。",
                "evidence_ref": source.get("evidence_ref"),
            })
        audit = self.repository.verify_strategy_audit_chain(
            str(item["strategy_id"]),
            str(item["strategy_version"]),
        )
        events = self.repository.list_strategy_audit_events(
            str(item["strategy_id"]),
            str(item["strategy_version"]),
        )
        expected_status = None
        registered_manifest_hash = None
        for event in events:
            details = event.get("details") or {}
            if event.get("event_type") == "strategy.version.registered":
                expected_status = details.get("initial_status")
                registered_manifest_hash = details.get("manifest_sha256")
            elif event.get("event_type") == "strategy.status.changed":
                if details.get("from_status") != expected_status:
                    expected_status = None
                    break
                expected_status = details.get("to_status")
        registry_state_verified = bool(
            audit["verified"]
            and expected_status == item.get("status")
            and registered_manifest_hash == item.get("manifest_sha256")
        )
        manifest_verified = bool(item.get("manifest_integrity_verified"))
        checks_passed = bool(required) and all(check["status"] == "pass" for check in checks)
        return {
            "gate_version": RELEASE_GATE_VERSION,
            "manifest_integrity_verified": manifest_verified,
            "audit_chain": audit,
            "registry_state_verified": registry_state_verified,
            "required_check_count": len(required),
            "passed_check_count": sum(check["status"] == "pass" for check in checks),
            "checks": checks,
            "all_required_checks_passed": checks_passed,
            "release_ready": manifest_verified and registry_state_verified and checks_passed,
        }

    @staticmethod
    def _scope_assessment(
        manifest: dict[str, Any],
        *,
        asset_type: str,
        market: str,
        user_scenario: str,
    ) -> dict[str, Any]:
        scope = manifest.get("applicability") or {}
        asset_allowed = asset_type in set(scope.get("asset_types") or [])
        market_allowed = market in set(scope.get("markets") or [])
        scenario_allowed = user_scenario in set(scope.get("user_scenarios") or [])
        return {
            "asset_type": {"value": asset_type, "allowed": asset_allowed},
            "market": {"value": market, "allowed": market_allowed},
            "user_scenario": {"value": user_scenario, "allowed": scenario_allowed},
            "applicable": asset_allowed and market_allowed and scenario_allowed,
        }

    @staticmethod
    def _canary_assessment(
        manifest: dict[str, Any],
        strategy_id: str,
        strategy_version: str,
        user_id: str,
    ) -> dict[str, Any]:
        canary = manifest.get("canary") or {}
        percent = max(0.0, min(100.0, float(canary.get("percent") or 0)))
        explicit_users = {str(value) for value in canary.get("allowed_user_ids") or []}
        bucket = _stable_bucket(strategy_id, strategy_version, user_id)
        selected = user_id in explicit_users or bucket < round(percent * 100)
        return {
            "percent": percent,
            "bucket": bucket,
            "explicit_user": user_id in explicit_users,
            "selected": selected,
        }

    def evaluate_runtime_use(self, payload: dict[str, Any]) -> dict[str, Any]:
        strategy_id = str(payload.get("strategy_id") or "")
        strategy_version = str(payload.get("strategy_version") or "")
        asset_type = str(payload.get("asset_type") or "fund")
        market = str(payload.get("market") or "unknown_cross_border")
        user_scenario = str(payload.get("user_scenario") or "personalized_fund_decision")
        user_id = str(payload.get("user_id") or "anonymous")
        item = self.repository.get_strategy_version(strategy_id, strategy_version)
        if item is None:
            return {
                "schema_version": RUNTIME_GATE_SCHEMA_VERSION,
                "evaluated_at": _utc_now(),
                "strategy": {
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "registered": False,
                    "status": "unregistered",
                    "manifest_sha256": None,
                },
                "execution": {
                    "calculation_allowed": False,
                    "decision_use_allowed": False,
                    "mode": "blocked",
                    "reason_code": "strategy_version_unregistered",
                    "reason": "策略版本未注册，默认拒绝影响用户决策。",
                },
                "scope": {"applicable": False},
                "release": {
                    "gate_version": RELEASE_GATE_VERSION,
                    "release_ready": False,
                    "checks": [],
                },
                "policy": "未注册或无法验证的策略默认拒绝，不使用旧版本或相近版本兜底。",
            }

        manifest = item.get("manifest") or {}
        release = self._release_assessment(item)
        scope = self._scope_assessment(
            manifest,
            asset_type=asset_type,
            market=market,
            user_scenario=user_scenario,
        )
        canary = self._canary_assessment(
            manifest,
            strategy_id,
            strategy_version,
            user_id,
        )
        status = str(item.get("status") or "")
        decision_allowed = bool(
            release["release_ready"]
            and scope["applicable"]
            and (status == "active" or (status == "canary" and canary["selected"]))
        )
        if not item.get("manifest_integrity_verified"):
            reason_code = "strategy_manifest_integrity_failed"
            reason = "策略清单哈希校验失败，已拒绝执行。"
            calculation_allowed = False
            mode = "blocked"
        elif not release["registry_state_verified"]:
            reason_code = "strategy_audit_chain_failed"
            reason = "策略生命周期审计链或当前状态绑定校验失败，已拒绝执行。"
            calculation_allowed = False
            mode = "blocked"
        elif status in {"paused", "retired"}:
            reason_code = f"strategy_{status}"
            reason = "策略已暂停或退役，禁止继续用于新结果。"
            calculation_allowed = False
            mode = "blocked"
        elif not scope["applicable"]:
            reason_code = "strategy_scope_mismatch"
            reason = "策略不适用于当前资产、市场或用户场景。"
            calculation_allowed = False
            mode = "blocked"
        elif status in {"draft", "review", "shadow"}:
            reason_code = f"strategy_{status}_research_only"
            reason = "策略仅允许研究或 Shadow 观察，禁止影响个人投入金额。"
            calculation_allowed = status == "shadow"
            mode = "shadow" if status == "shadow" else "blocked"
        elif not release["release_ready"]:
            reason_code = "strategy_release_checks_failed"
            reason = "策略发布检查未全部通过，禁止影响个人投入金额。"
            calculation_allowed = status == "canary"
            mode = "shadow" if calculation_allowed else "blocked"
        elif status == "canary" and not canary["selected"]:
            reason_code = "strategy_canary_not_selected"
            reason = "当前用户不在该版本的确定性灰度范围内。"
            calculation_allowed = True
            mode = "shadow"
        else:
            reason_code = "strategy_released"
            reason = "策略版本、发布检查、适用范围和审计链均满足运行门禁。"
            calculation_allowed = True
            mode = status

        return {
            "schema_version": RUNTIME_GATE_SCHEMA_VERSION,
            "evaluated_at": _utc_now(),
            "strategy": {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "registered": True,
                "name": item.get("name"),
                "strategy_kind": item.get("strategy_kind"),
                "owner_id": item.get("owner_id"),
                "status": status,
                "manifest_sha256": item.get("manifest_sha256"),
                "registered_at": item.get("registered_at"),
                "status_updated_at": item.get("status_updated_at"),
            },
            "execution": {
                "calculation_allowed": calculation_allowed,
                "decision_use_allowed": decision_allowed,
                "mode": mode,
                "reason_code": reason_code,
                "reason": reason,
            },
            "scope": scope,
            "canary": canary,
            "release": release,
            "known_limitations": manifest.get("known_limitations") or [],
            "policy": "只有清单哈希和审计链完整、发布检查全部通过、状态为 active 或命中 canary 且适用范围匹配的精确策略版本，才能影响用户决策。",
        }

    def list_public(self) -> list[dict[str, Any]]:
        items = []
        for item in self.repository.list_strategy_versions():
            assessment = self._release_assessment(item)
            manifest = item.get("manifest") or {}
            items.append({
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "name": item["name"],
                "strategy_kind": item["strategy_kind"],
                "owner_id": item["owner_id"],
                "status": item["status"],
                "previous_status": item.get("previous_status"),
                "manifest_sha256": item["manifest_sha256"],
                "manifest_integrity_verified": item["manifest_integrity_verified"],
                "registered_at": item["registered_at"],
                "status_updated_at": item["status_updated_at"],
                "applicability": manifest.get("applicability") or {},
                "release": assessment,
                "known_limitations": manifest.get("known_limitations") or [],
            })
        return items

    def get_public(self, strategy_id: str, strategy_version: str) -> dict[str, Any] | None:
        return next(
            (
                item for item in self.list_public()
                if item["strategy_id"] == strategy_id
                and item["strategy_version"] == strategy_version
            ),
            None,
        )

    def transition(
        self,
        strategy_id: str,
        strategy_version: str,
        *,
        target_status: str,
        actor_role: str,
        actor_id: str,
        reason: str,
        expected_status: str,
    ) -> dict[str, Any]:
        item = self.repository.get_strategy_version(strategy_id, strategy_version)
        if item is None:
            raise KeyError(f"策略版本不存在:{strategy_id}@{strategy_version}")
        current_status = str(item["status"])
        if current_status != expected_status:
            raise RuntimeError(
                f"策略状态已变化，期望 {expected_status}，实际 {current_status}"
            )
        if target_status not in self._TRANSITIONS.get(current_status, set()):
            raise ValueError(f"不允许的策略状态迁移:{current_status}->{target_status}")
        if len(reason.strip()) < 12:
            raise ValueError("状态迁移原因至少需要 12 个字符")
        if target_status in {"canary", "active"}:
            if actor_role != "reviewer":
                raise PermissionError("Canary 或 Active 发布必须由 reviewer 执行")
            if actor_id == item.get("owner_id"):
                raise PermissionError("策略负责人不能审批自己的 Canary 或 Active 发布")
            assessment = self._release_assessment(item)
            if not assessment["release_ready"]:
                raise ValueError("策略发布检查未全部通过，不能进入 Canary 或 Active")
            if target_status == "canary":
                percent = float(((item.get("manifest") or {}).get("canary") or {}).get("percent") or 0)
                explicit = ((item.get("manifest") or {}).get("canary") or {}).get("allowed_user_ids") or []
                if percent <= 0 and not explicit:
                    raise ValueError("Canary 未配置确定性灰度范围")
        elif target_status == "review" and actor_role not in {"owner", "strategy_manager"}:
            raise PermissionError("只有 owner 或 strategy_manager 可以提交评审")
        elif target_status == "shadow" and actor_role not in {"reviewer", "strategy_manager"}:
            raise PermissionError("只有 reviewer 或 strategy_manager 可以进入 Shadow")
        elif target_status in {"paused", "retired"} and actor_role not in {
            "operator", "reviewer", "strategy_manager"
        }:
            raise PermissionError("只有 operator、reviewer 或 strategy_manager 可以暂停或退役")
        assessment = self._release_assessment(item)
        return self.repository.transition_strategy_status(
            strategy_id,
            strategy_version,
            expected_status=expected_status,
            target_status=target_status,
            actor_role=actor_role,
            actor_id=actor_id,
            reason=reason.strip(),
            release_assessment={
                "gate_version": assessment["gate_version"],
                "release_ready": assessment["release_ready"],
                "passed_check_count": assessment["passed_check_count"],
                "required_check_count": assessment["required_check_count"],
            },
        )
