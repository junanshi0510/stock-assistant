# -*- coding: utf-8 -*-
"""Versioned, allow-listed tools available to the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import funds as fund_service
import fund_intelligence
import portfolio_exposure
from .portfolio_context import get_portfolio_context
from .synthesis import InvestmentSynthesisService
from strategies.personalized_fund_decision import evaluate_personalized_fund_decision

if TYPE_CHECKING:
    from .strategy_governance import StrategyGovernanceService


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    risk_level: str
    timeout_seconds: float
    handler: ToolHandler


class ToolRegistry:
    """Registry rejects unknown or duplicate tool versions by default."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        key = (definition.name, definition.version)
        if key in self._tools:
            raise ValueError(f"工具已注册:{definition.name}@{definition.version}")
        if definition.risk_level not in {"R0", "R1", "R2", "R3"}:
            raise ValueError(f"无效工具风险等级:{definition.risk_level}")
        if float(definition.timeout_seconds) <= 0:
            raise ValueError(f"工具超时时间必须大于 0:{definition.name}@{definition.version}")
        self._tools[key] = definition

    def get(self, name: str, version: str) -> ToolDefinition:
        try:
            return self._tools[(name, version)]
        except KeyError as error:
            raise KeyError(f"工具未注册或版本不可用:{name}@{version}") from error

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "version": item.version,
                "description": item.description,
                "risk_level": item.risk_level,
                "timeout_seconds": item.timeout_seconds,
            }
            for item in sorted(self._tools.values(), key=lambda value: (value.name, value.version))
        ]


def build_default_registry(
    strategy_governance: "StrategyGovernanceService",
    synthesis_service: InvestmentSynthesisService | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    synthesis_service = synthesis_service or InvestmentSynthesisService()
    registry.register(ToolDefinition(
        name="fund.analysis.get",
        version="1.0.0",
        description="读取一只公募基金的真实历史净值并计算风险、回撤和投入节奏。",
        risk_level="R0",
        timeout_seconds=45,
        handler=lambda payload: fund_service.analyze_fund(
            str(payload["code"]),
            int(payload.get("months") or 60),
            include_profile=False,
        ),
    ))
    registry.register(ToolDefinition(
        name="fund.estimate.get",
        version="1.0.0",
        description="读取第三方盘中基金估值，并与已确认净值严格分离。",
        risk_level="R0",
        timeout_seconds=20,
        handler=lambda payload: fund_service.get_fund_estimate(str(payload["code"])),
    ))
    registry.register(ToolDefinition(
        name="fund.market_profile.get",
        version="1.0.0",
        description="读取真实基金类型和详情页基准，识别内地、港股、美股、全球或跨市场暴露。",
        risk_level="R0",
        timeout_seconds=25,
        handler=lambda payload: fund_service.get_fund_market_profile(str(payload["code"])),
    ))
    registry.register(ToolDefinition(
        name="fund.intelligence.get",
        version="1.0.0",
        description="穿透真实基金披露，读取前列持仓行情、板块和逐条新闻，形成跨市场情报上下文。",
        risk_level="R0",
        timeout_seconds=95,
        handler=lambda payload: fund_intelligence.get_fund_intelligence(
            str(payload["code"]),
            holding_limit=int(payload.get("holding_limit") or 4),
            news_per_holding=int(payload.get("news_per_holding") or 3),
        ),
    ))
    registry.register(ToolDefinition(
        name="fund.decision_outcome.get",
        version="1.1.0",
        description="使用原 Run 确认净值、后续真实净值和来源原生同类平均评估绝对与相对结果。",
        risk_level="R0",
        timeout_seconds=45,
        handler=lambda payload: fund_service.get_fund_decision_outcome(
            str(payload["code"]),
            str(payload["baseline_as_of"]),
            float(payload["baseline_nav"]),
            str(payload.get("action") or ""),
        ),
    ))
    registry.register(ToolDefinition(
        name="fund.strategy_shadow_outcome.get",
        version="1.0.0",
        description="使用冻结策略版本信号、第 N 个后续真实确认净值和来源原生同类序列形成 Shadow Outcome。",
        risk_level="R0",
        timeout_seconds=45,
        handler=lambda payload: fund_service.get_fund_strategy_shadow_outcome(
            str(payload["code"]),
            str(payload["baseline_as_of"]),
            float(payload["baseline_nav"]),
            str(payload["signal_direction"]),
            str(payload["horizon"]),
            int(payload["observation_days"]),
        ),
    ))
    registry.register(ToolDefinition(
        name="fund.disclosure_changes.get",
        version="1.0.0",
        description="比较两个真实且不同的基金定期报告披露期。",
        risk_level="R0",
        timeout_seconds=45,
        handler=lambda payload: fund_service.get_fund_disclosure_changes(str(payload["code"])),
    ))
    registry.register(ToolDefinition(
        name="fund.alternatives.get",
        version="1.0.0",
        description="基于真实同类排行和净值指标筛选多维替代研究候选。",
        risk_level="R0",
        timeout_seconds=120,
        handler=lambda payload: fund_service.get_fund_alternatives(
            str(payload["code"]),
            sort=str(payload.get("sort") or "1y"),
            limit=int(payload.get("limit") or 5),
            months=int(payload.get("months") or 36),
        ),
    ))
    registry.register(ToolDefinition(
        name="strategy.release.check",
        version="1.0.0",
        description="读取精确策略版本的持久化状态、清单哈希、审计链和发布检查，默认拒绝未发布策略影响用户决策。",
        risk_level="R0",
        timeout_seconds=5,
        handler=strategy_governance.evaluate_runtime_use,
    ))
    registry.register(ToolDefinition(
        name="portfolio.context.get",
        version="1.0.0",
        description="读取用户已确认持仓、目标基金仓位和已保存投资约束，用于个人决策门禁。",
        risk_level="R1",
        timeout_seconds=5,
        handler=get_portfolio_context,
    ))
    registry.register(ToolDefinition(
        name="portfolio.exposure.snapshot",
        version="1.0.0",
        description="从用户确认持仓和真实基金定期披露生成不可变组合穿透区间快照。",
        risk_level="R1",
        timeout_seconds=120,
        handler=lambda payload: portfolio_exposure.calculate_exposure_snapshot(
            target_code=str(payload["code"]),
            profile_version_id=payload.get("profile_version_id"),
        ),
    ))
    registry.register(ToolDefinition(
        name="fund.personalized_decision.evaluate",
        version="1.3.0",
        description="把已发布策略、基金研究 Evidence 与用户组合 Evidence 代入版本化风险门禁和金额策略。",
        risk_level="R1",
        timeout_seconds=5,
        handler=lambda payload: {
            **evaluate_personalized_fund_decision(
                payload["analysis"],
                payload["context"],
                payload["market_profile"],
                payload.get("exposure"),
                payload.get("strategy_governance"),
                planned_amount=payload.get("planned_amount"),
            ),
            "input_evidence_ids": payload.get("input_evidence_ids") or [],
        },
    ))
    registry.register(ToolDefinition(
        name="llm.fund_decision.synthesize",
        version="1.0.0",
        description="使用已配置大模型对脱敏 Evidence 做结构化基金研判，并执行证据、动作和安全质量门禁。",
        risk_level="R1",
        timeout_seconds=110,
        handler=synthesis_service.synthesize,
    ))
    return registry
