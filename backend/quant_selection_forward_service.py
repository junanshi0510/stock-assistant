# -*- coding: utf-8 -*-
"""Causal forward validation for point-in-time quant-selection mandates."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable
from zoneinfo import ZoneInfo

import opportunity_profit_service
import opportunity_service
from background_jobs import BackgroundJobRepository
from opportunity_profit_repository import (
    OpportunityProfitRepository,
    repository as profit_repository,
)
from opportunity_repository import (
    OpportunityRepository,
    repository as opportunity_repository,
)
from quant_selection_forward_repository import (
    QuantSelectionForwardConflictError,
    QuantSelectionForwardNotFoundError,
    QuantSelectionForwardRepository,
    canonical_json,
    repository,
    sha256_text,
    stable_id,
)
from quant_selection_repository import (
    QuantSelectionRepository,
    repository as quant_repository,
)
from task_queue import (
    QUEUE_MARKET,
    enqueue_background_job,
    uses_celery_queue,
)


ENGINE_VERSION = "quant_selection_forward_validation@1.0.0"
MARKET_TIMEZONES = {
    "A股": "Asia/Shanghai",
    "港股": "Asia/Hong_Kong",
    "美股": "America/New_York",
}
BENCHMARK_NAMES = {
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "512100": "中证1000ETF",
    "02800": "盈富基金",
    "SPY": "标普500ETF",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_datetime(value: Any) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        )
    except ValueError as error:
        raise QuantSelectionForwardConflictError(
            "量化纸面指令缺少有效冻结时间"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _market_date(value: Any, market: str) -> str:
    timezone = MARKET_TIMEZONES.get(market)
    if not timezone:
        raise QuantSelectionForwardConflictError(
            f"前向验证不支持市场:{market or '(空)'}"
        )
    return _parse_datetime(value).astimezone(
        ZoneInfo(timezone)
    ).date().isoformat()


def _validated_targets(
    targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for item in targets:
        symbol = str((item or {}).get("symbol") or "").strip()
        weight = _number((item or {}).get("target_weight_pct"))
        if not symbol or symbol in seen or weight is None or weight <= 0:
            raise QuantSelectionForwardConflictError(
                "最新目标篮子存在重复、空代码或无效权重"
            )
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": str((item or {}).get("name") or symbol)[:80],
                "weight_pct": round(weight, 6),
                "rank": int((item or {}).get("rank") or len(rows) + 1),
                "composite_score": _number(
                    (item or {}).get("composite_score")
                ),
                "signal_last_price": _number(
                    (item or {}).get("last_price")
                ),
                "signal_last_date": (
                    str((item or {}).get("last_date") or "")[:10]
                    or None
                ),
            }
        )
    if len(rows) < 2:
        raise QuantSelectionForwardConflictError(
            "前向验证至少需要两只目标股票"
        )
    if sum(item["weight_pct"] for item in rows) > 100.0001:
        raise QuantSelectionForwardConflictError(
            "最新目标篮子权重超过 100%"
        )
    return rows


def _round_trip_cost_bps(policy: dict[str, Any]) -> float:
    commission = max(0.0, _number(policy.get("commission_bps")) or 0)
    slippage = max(0.0, _number(policy.get("slippage_bps")) or 0)
    sell_tax = max(0.0, _number(policy.get("sell_tax_bps")) or 0)
    return round(
        min(500.0, max(10.0, 2 * (commission + slippage) + sell_tax)),
        4,
    )


def enroll_validation(
    mandate_id: str,
    *,
    acknowledged: bool,
    expected_snapshot_sha256: str,
    tenant_id: str,
    user_id: str,
    actor_id: str,
    now: dt.datetime | None = None,
    quant_repo: QuantSelectionRepository = quant_repository,
    forward_repo: QuantSelectionForwardRepository = repository,
) -> tuple[dict[str, Any], bool]:
    if not acknowledged:
        raise ValueError(
            "必须确认前向验证只从冻结后的下一交易日开始且不自动下单"
        )
    mandate = quant_repo.get_shadow_mandate(
        mandate_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if mandate is None:
        raise QuantSelectionForwardNotFoundError(
            "量化纸面指令不存在"
        )
    if not (mandate.get("integrity") or {}).get("verified"):
        raise QuantSelectionForwardConflictError(
            "量化纸面指令完整性校验失败"
        )
    if str(mandate.get("snapshot_sha256") or "") != str(
        expected_snapshot_sha256
    ):
        raise QuantSelectionForwardConflictError(
            "量化纸面指令摘要已经变化"
        )
    run = quant_repo.get_run(
        str(mandate["run_id"]),
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if run is None:
        raise QuantSelectionForwardNotFoundError(
            "量化纸面指令的来源实验不存在"
        )
    if (
        run.get("status") not in {"succeeded", "partial"}
        or not (run.get("integrity") or {}).get("verified")
        or str(run.get("result_sha256") or "")
        != str(mandate.get("result_sha256") or "")
    ):
        raise QuantSelectionForwardConflictError(
            "量化纸面指令的来源实验不可验证"
        )

    snapshot = mandate.get("snapshot") or {}
    gate = snapshot.get("promotion_gate") or {}
    if not gate.get("paper_shadow_eligible"):
        raise QuantSelectionForwardConflictError(
            "只有通过量化研究门槛的纸面指令可以进入前向验证"
        )
    quant_policy = snapshot.get("policy") or {}
    market = str(quant_policy.get("market") or "")
    signal = snapshot.get("latest_signal") or {}
    signal_date = str(signal.get("signal_date") or "")[:10]
    if not signal_date:
        raise QuantSelectionForwardConflictError(
            "量化纸面指令缺少信号日期"
        )
    targets = _validated_targets(signal.get("targets") or [])
    enrolled = now or dt.datetime.now(dt.timezone.utc)
    if enrolled.tzinfo is None:
        enrolled = enrolled.replace(tzinfo=dt.timezone.utc)
    enrolled = enrolled.astimezone(dt.timezone.utc)
    enrolled_at = enrolled.isoformat(timespec="milliseconds")
    activation_market_date = _market_date(
        enrolled, market
    )
    entry_after_date = max(signal_date, activation_market_date)
    cost_bps = _round_trip_cost_bps(quant_policy)

    strategy_basis = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "source_engine_version": mandate.get("engine_version"),
        "quant_policy_sha256": run.get("policy_sha256"),
    }
    strategy_fingerprint = sha256_text(
        canonical_json(strategy_basis)
    )
    strategy_id = stable_id("qsf_strategy", strategy_basis)
    strategy_version_id = stable_id(
        "qsf_strategy_v",
        {**strategy_basis, "version": 1},
    )
    opportunity_run_id = stable_id(
        "qsf_run",
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "quant_mandate_id": mandate_id,
        },
    )
    basket_id = stable_id(
        "qsf_basket",
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "quant_mandate_id": mandate_id,
        },
    )
    profit_policy_id = stable_id(
        "qsf_profit_policy",
        {**strategy_basis, "policy_version": 1},
    )
    strategy_name = (
        f"量化前向｜{quant_policy.get('name') or market + '多因子'}"
    )[:100]
    definition = {
        "schema_version": "quant_selection_forward_strategy.v1",
        "name": strategy_name,
        "markets": [market],
        "source": {
            "type": "quant_selection_shadow_mandate",
            "engine_version": mandate.get("engine_version"),
            "policy_sha256": run.get("policy_sha256"),
            "strategy_fingerprint": strategy_fingerprint,
        },
        "quant_policy": quant_policy,
        "forward_validation": {
            "entry_timing": "next_trading_day_open_after_freeze",
            "evaluation_horizons": [5, 20, 60],
            "round_trip_cost_bps": cost_bps,
            "minimum_independent_20d_cohorts": 6,
        },
        "execution_authorized": False,
        "broker_connected": False,
        "quantity_generated": False,
    }
    profit_policy = opportunity_profit_service.normalize_policy(
        {
            "evaluation_horizons": [5, 20, 60],
            "primary_horizon": 20,
            "round_trip_cost_bps": cost_bps,
            "minimum_coverage_pct": 90,
            "minimum_mature_baskets": 6,
            "minimum_mean_excess_return_pct": 0.5,
            "minimum_positive_excess_rate_pct": 55,
            "maximum_cohort_drawdown_pct": min(
                25.0,
                max(
                    3.0,
                    _number(
                        quant_policy.get("maximum_drawdown_pct")
                    )
                    or 15.0,
                ),
            ),
            "maximum_manual_pilot_pct": 3,
            "latest_basket_max_age_days": 30,
        }
    )
    position_rows = [
        {
            **item,
            "market": market,
            "signal_date": signal_date,
            "activation_market_date": activation_market_date,
            "entry_after_date": entry_after_date,
            "entry_timing": "next_trading_day_open",
            "entry_date": None,
            "entry_price": None,
        }
        for item in targets
    ]
    target_cash_pct = max(
        0.0,
        _number(signal.get("target_cash_pct"))
        if _number(signal.get("target_cash_pct")) is not None
        else 100 - sum(item["weight_pct"] for item in targets),
    )
    benchmark_symbol = str(
        quant_policy.get("benchmark_symbol") or ""
    )
    strategy_snapshot = {
        "id": strategy_id,
        "version_id": strategy_version_id,
        "version_no": 1,
        "sha256": sha256_text(canonical_json(definition)),
        "name": strategy_name,
        "definition": definition,
    }
    run_result = {
        "schema_version": "quant_selection_forward_import.v1",
        "engine_version": ENGINE_VERSION,
        "source": {
            "quant_mandate_id": mandate_id,
            "quant_run_id": run["id"],
            "quant_result_sha256": run["result_sha256"],
            "quant_snapshot_sha256": mandate["snapshot_sha256"],
        },
        "strategy": strategy_snapshot,
        "funnel": {
            "universe": len(position_rows),
            "evaluated": len(position_rows),
            "qualified": len(position_rows),
            "portfolio": len(position_rows),
        },
        "portfolio": {
            "position_count": len(position_rows),
            "positions": position_rows,
            "cash_pct": round(target_cash_pct, 6),
        },
        "generated_at": enrolled_at,
        "execution_authorized": False,
        "broker_connected": False,
        "quantity_generated": False,
    }
    basket_snapshot = {
        "schema_version": "opportunity_paper_basket.v1",
        "run_id": opportunity_run_id,
        "run_result_sha256": sha256_text(
            canonical_json(run_result)
        ),
        "strategy": strategy_snapshot,
        "frozen_at": enrolled_at,
        "source": {
            "type": "quant_selection_shadow_mandate",
            "quant_mandate_id": mandate_id,
            "quant_run_id": run["id"],
            "quant_result_sha256": run["result_sha256"],
            "quant_snapshot_sha256": mandate["snapshot_sha256"],
            "mandate_frozen_at": mandate.get("created_at"),
        },
        "positions": position_rows,
        "cash_pct": round(target_cash_pct, 6),
        "market_regimes": [],
        "benchmarks": {
            market: {
                "market": market,
                "symbol": benchmark_symbol,
                "name": BENCHMARK_NAMES.get(
                    benchmark_symbol, benchmark_symbol
                ),
            }
        },
        "round_trip_cost_scenario_bps": cost_bps,
        "entry_rules": {
            "signal_date": signal_date,
            "activation_market_date": activation_market_date,
            "entry_after_date": entry_after_date,
            "entry_timing": "next_trading_day_open",
            "same_day_fill_allowed": False,
            "historical_backfill_allowed": False,
        },
        "data_basis": (
            "目标权重来自不可变量化纸面指令；入场价只允许取冻结后"
            "下一真实交易日的复权开盘价，随后按真实收盘价观察"
        ),
        "limitations": [
            "纸面建仓不模拟开盘集合竞价排队、整手、涨跌停、停牌和冲击成交不足。",
            "跨市场收益不计持有期汇率变化，成本是预先冻结的压力情景。",
            "前向验证只积累证据，不连接券商、不生成股数、不提交订单。",
        ],
        "execution_authorized": False,
        "broker_connected": False,
        "quantity_generated": False,
    }
    link_payload = {
        "schema_version": "quant_selection_forward_binding.v1",
        "engine_version": ENGINE_VERSION,
        "quant_mandate_id": mandate_id,
        "quant_run_id": run["id"],
        "quant_result_sha256": run["result_sha256"],
        "quant_snapshot_sha256": mandate["snapshot_sha256"],
        "quant_policy_sha256": run["policy_sha256"],
        "strategy_fingerprint": strategy_fingerprint,
        "opportunity_strategy_id": strategy_id,
        "opportunity_strategy_version_id": strategy_version_id,
        "opportunity_run_id": opportunity_run_id,
        "opportunity_basket_id": basket_id,
        "profit_policy_id": profit_policy_id,
        "causality": {
            "signal_date": signal_date,
            "mandate_frozen_at": mandate.get("created_at"),
            "enrolled_at": enrolled_at,
            "activation_market_date": activation_market_date,
            "entry_after_date": entry_after_date,
            "earliest_entry": "strictly_later_trading_day_open",
            "historical_backfill_allowed": False,
        },
        "execution_authorized": False,
    }
    return forward_repo.create_validation(
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        mandate_id=mandate_id,
        quant_run_id=str(run["id"]),
        quant_snapshot_sha256=str(mandate["snapshot_sha256"]),
        strategy_fingerprint=strategy_fingerprint,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        opportunity_run_id=opportunity_run_id,
        basket_id=basket_id,
        profit_policy_id=profit_policy_id,
        definition=definition,
        profit_policy=profit_policy,
        run_result=run_result,
        basket_snapshot=basket_snapshot,
        link_payload=link_payload,
        source_created_at=enrolled_at,
    )


def _next_action(
    scorecard: dict[str, Any],
    *,
    latest_payload: dict[str, Any],
) -> str:
    positions = latest_payload.get("positions") or []
    if not latest_payload:
        return "等待调度器读取冻结后的下一真实交易日开盘价"
    if any(item.get("status") == "pending_entry" for item in positions):
        return "下一交易日开盘价尚未形成，系统会自动重试且不会历史回填"
    gate = scorecard.get("capital_gate") or {}
    primary = next(
        (
            item
            for item in scorecard.get("horizons") or []
            if int(item.get("horizon_trading_days") or 0) == 20
        ),
        {},
    )
    if gate.get("status") == "collecting":
        return (
            f"20 日独立成熟批次 "
            f"{primary.get('mature_count') or 0}/"
            f"{((scorecard.get('policy') or {}).get('values') or {}).get('minimum_mature_baskets') or 6}；"
            "继续按调仓周期冻结新批次"
        )
    if gate.get("status") == "limited_manual_pilot":
        if not (
            (scorecard.get("latest_persisted") or {}).get(
                "binding_current"
            )
        ):
            return "前向门禁已通过；请在收益实验室冻结当前记分卡后再交投资委员会"
        return "当前证据已可提交投资委员会，但仍只允许受限人工复核"
    if gate.get("status") == "suspended":
        return "前向收益或回撤门禁未通过，策略保持暂停并继续观察"
    return "继续积累不可变的 5/20/60 交易日前向结果"


def forward_overview(
    *,
    tenant_id: str,
    user_id: str,
    limit: int = 100,
    forward_repo: QuantSelectionForwardRepository = repository,
    opp_repo: OpportunityRepository = opportunity_repository,
    profit_repo: OpportunityProfitRepository = profit_repository,
) -> dict[str, Any]:
    links = forward_repo.list_validations(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    profit_error = None
    try:
        profit_lab = opportunity_profit_service.profit_lab_overview(
            user_id=user_id,
            opp_repo=opp_repo,
            profit_repo=profit_repo,
        )
        scorecards = {
            str((item.get("strategy") or {}).get("id") or ""): item
            for item in profit_lab.get("items") or []
        }
    except Exception as error:
        scorecards = {}
        profit_error = str(error)[:300]

    items = []
    for link in links:
        basket = opp_repo.get_paper_basket(
            str(link["opportunity_basket_id"]),
            user_id=user_id,
        )
        scorecard = scorecards.get(
            str(link["opportunity_strategy_id"])
        ) or {}
        latest = (basket or {}).get("latest_observation") or {}
        latest_payload = latest.get("payload") or {}
        latest_positions = latest_payload.get("positions") or []
        pending_entry_count = sum(
            item.get("status") == "pending_entry"
            for item in latest_positions
        )
        available_position_count = sum(
            item.get("status") == "available"
            for item in latest_positions
        )
        gate = scorecard.get("capital_gate") or {}
        if latest_payload.get("max_horizon_complete"):
            observation_state = "complete"
        elif pending_entry_count:
            observation_state = "awaiting_entry"
        elif latest_payload:
            observation_state = "collecting"
        else:
            observation_state = "awaiting_observation"
        items.append(
            {
                **link,
                "basket_integrity_verified": bool(
                    basket and basket.get("snapshot_verified")
                ),
                "observation_state": observation_state,
                "entry": {
                    "pending_position_count": pending_entry_count,
                    "available_position_count": available_position_count,
                    "target_position_count": len(
                        ((basket or {}).get("snapshot") or {}).get(
                            "positions"
                        )
                        or []
                    ),
                    "rules": (
                        ((basket or {}).get("snapshot") or {}).get(
                            "entry_rules"
                        )
                        or {}
                    ),
                },
                "latest_observation": (
                    {
                        "id": latest.get("id"),
                        "observed_at": latest.get("observed_at"),
                        "payload_verified": latest.get(
                            "payload_verified"
                        ),
                        "status": latest_payload.get("status"),
                        "observed_trading_days_min": latest_payload.get(
                            "observed_trading_days_min"
                        ),
                        "net_return_after_cost_pct": latest_payload.get(
                            "net_return_after_cost_pct"
                        ),
                        "benchmark_return_pct": latest_payload.get(
                            "benchmark_return_pct"
                        ),
                        "net_excess_return_pct": latest_payload.get(
                            "net_excess_return_pct"
                        ),
                        "horizons": latest_payload.get("horizons") or [],
                        "positions": latest_positions,
                    }
                    if latest
                    else None
                ),
                "scorecard": {
                    "strategy": scorecard.get("strategy") or {},
                    "automation": scorecard.get("automation") or {},
                    "policy": scorecard.get("policy") or {},
                    "horizons": scorecard.get("horizons") or [],
                    "capital_gate": gate,
                    "latest_persisted": scorecard.get(
                        "latest_persisted"
                    ),
                },
                "committee_ready": bool(
                    gate.get("capital_eligible")
                    and (
                        scorecard.get("latest_persisted") or {}
                    ).get("binding_current")
                ),
                "next_action": _next_action(
                    scorecard,
                    latest_payload=latest_payload,
                ),
            }
        )
    return {
        "schema_version": "quant_selection_forward_overview.v1",
        "engine_version": ENGINE_VERSION,
        "generated_at": dt.datetime.now(
            dt.timezone.utc
        ).isoformat(timespec="seconds"),
        "items": items,
        "summary": {
            "validation_count": len(items),
            "awaiting_entry_count": sum(
                item["observation_state"] == "awaiting_entry"
                for item in items
            ),
            "collecting_count": sum(
                item["observation_state"] == "collecting"
                for item in items
            ),
            "committee_ready_count": sum(
                item["committee_ready"] for item in items
            ),
            "integrity_failure_count": sum(
                not (
                    (item.get("integrity") or {}).get("verified")
                    and item.get("basket_integrity_verified")
                )
                for item in items
            ),
        },
        "profit_lab_error": profit_error,
        "boundary": (
            "前向验证只从冻结后的下一交易日开盘开始，禁止补算冻结前已知收益；"
            "任何门禁结果均不授权自动下单或承诺未来盈利。"
        ),
    }


def request_observation(
    validation_id: str,
    *,
    tenant_id: str,
    user_id: str,
    now: dt.datetime | None = None,
    forward_repo: QuantSelectionForwardRepository = repository,
    opp_repo: OpportunityRepository = opportunity_repository,
    jobs: BackgroundJobRepository | None = None,
    enqueue: Callable[[dict[str, Any], BackgroundJobRepository], str]
    | None = None,
) -> dict[str, Any]:
    validation = forward_repo.get_validation(
        validation_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if validation is None:
        raise QuantSelectionForwardNotFoundError(
            "量化前向验证不存在"
        )
    if not (validation.get("integrity") or {}).get("verified"):
        raise QuantSelectionForwardConflictError(
            "量化前向验证映射完整性失败"
        )
    basket_id = str(validation["opportunity_basket_id"])
    basket = opp_repo.get_paper_basket(
        basket_id, user_id=user_id
    )
    if basket is None or not basket.get("snapshot_verified"):
        raise QuantSelectionForwardConflictError(
            "量化前向纸面组合不存在或完整性失败"
        )
    if not uses_celery_queue():
        observation = opportunity_service.observe_paper_basket(
            basket_id,
            user_id=user_id,
            repo=opp_repo,
        )
        return {
            "status": "succeeded",
            "mode": "embedded",
            "validation_id": validation_id,
            "basket_id": basket_id,
            "observation_id": observation.get("id"),
            "deduplicated": observation.get("deduplicated"),
        }

    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    job_repo = jobs or BackgroundJobRepository()
    job, created = job_repo.create_job(
        job_type="market_data_operation",
        queue_name=QUEUE_MARKET,
        payload={
            "operation": "opportunity.observe",
            "input": {
                "basket_id": basket_id,
                "user_id": user_id,
            },
        },
        tenant_id=tenant_id,
        user_id=user_id,
        idempotency_key=(
            f"quant-forward-observe:{validation_id}:"
            f"{current.strftime('%Y-%m-%dT%H')}"
        ),
        max_attempts=2,
    )
    if created:
        (enqueue or enqueue_background_job)(job, job_repo)
    return {
        "status": "queued" if created else str(job.get("status") or "queued"),
        "mode": "celery",
        "validation_id": validation_id,
        "basket_id": basket_id,
        "job_id": str(job["id"]),
        "created": created,
    }
