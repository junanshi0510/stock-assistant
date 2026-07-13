# -*- coding: utf-8 -*-
"""SSH-only operator CLI for the persistent Agent strategy registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.repository import AgentRepository
from agent.strategy_governance import StrategyGovernanceService


def _service() -> StrategyGovernanceService:
    repository = AgentRepository()
    service = StrategyGovernanceService(repository)
    service.seed_defaults()
    return service


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理投资 Agent 的持久化策略生命周期")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出策略版本及发布检查")

    register = commands.add_parser("register", help="从 JSON 清单注册不可变 draft 策略版本")
    register.add_argument("manifest_path")
    register.add_argument("--actor", required=True, dest="actor_id")

    show = commands.add_parser("show", help="查看一个精确策略版本")
    show.add_argument("strategy_id")
    show.add_argument("strategy_version")

    verify = commands.add_parser("verify", help="验证策略清单与生命周期审计链")
    verify.add_argument("strategy_id")
    verify.add_argument("strategy_version")

    transition = commands.add_parser("transition", help="执行受约束的策略状态迁移")
    transition.add_argument("strategy_id")
    transition.add_argument("strategy_version")
    transition.add_argument("--expected-status", required=True)
    transition.add_argument("--to", required=True, dest="target_status")
    transition.add_argument("--actor-role", required=True)
    transition.add_argument("--actor", required=True, dest="actor_id")
    transition.add_argument("--reason", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    service = _service()
    try:
        if args.command == "list":
            output = {"items": service.list_public()}
        elif args.command == "register":
            path = Path(args.manifest_path).expanduser().resolve()
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("策略清单根节点必须是 JSON object")
            item, created = service.repository.register_strategy_version(
                manifest,
                initial_status="draft",
                actor_role="owner",
                actor_id=args.actor_id,
            )
            output = {
                "created": created,
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "status": item["status"],
                "manifest_sha256": item["manifest_sha256"],
            }
        elif args.command == "show":
            item = service.get_public(args.strategy_id, args.strategy_version)
            if item is None:
                raise KeyError(f"策略版本不存在:{args.strategy_id}@{args.strategy_version}")
            output = item
        elif args.command == "verify":
            item = service.repository.get_strategy_version(
                args.strategy_id,
                args.strategy_version,
            )
            if item is None:
                raise KeyError(f"策略版本不存在:{args.strategy_id}@{args.strategy_version}")
            output = {
                "strategy_id": args.strategy_id,
                "strategy_version": args.strategy_version,
                "manifest_integrity_verified": item["manifest_integrity_verified"],
                "audit_chain": service.repository.verify_strategy_audit_chain(
                    args.strategy_id,
                    args.strategy_version,
                ),
            }
        else:
            item = service.transition(
                args.strategy_id,
                args.strategy_version,
                expected_status=args.expected_status,
                target_status=args.target_status,
                actor_role=args.actor_role,
                actor_id=args.actor_id,
                reason=args.reason,
            )
            output = {
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "status": item["status"],
                "previous_status": item["previous_status"],
                "status_updated_at": item["status_updated_at"],
            }
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "result": output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
