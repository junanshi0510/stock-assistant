# -*- coding: utf-8 -*-
"""Offline account bootstrap and recovery commands."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from auth import AuthError, AuthService


def _password(args: argparse.Namespace) -> str:
    if args.password_file:
        return Path(args.password_file).read_text(encoding="utf-8").strip()
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords do not match")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock Assistant authentication administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name", default="系统管理员")
    bootstrap.add_argument("--subject-id", default="default")
    bootstrap.add_argument("--password-file")
    recover = subparsers.add_parser("recover-admin")
    recover.add_argument("--username", required=True)
    recover.add_argument("--password-file")
    subparsers.add_parser("verify-audit")
    args = parser.parse_args()

    service = AuthService()
    try:
        if args.command == "bootstrap-admin":
            user = service.bootstrap_admin(
                args.username,
                _password(args),
                display_name=args.display_name,
                subject_id=args.subject_id,
            )
            print(f"created admin username={user['username']} id={user['id']}")
            return 0
        if args.command == "recover-admin":
            user = service.recover_admin(args.username, _password(args))
            print(f"recovered admin username={user['username']} id={user['id']}")
            return 0
        if args.command == "verify-audit":
            verification = service.verify_audit()
            print(
                "verified={verified} event_count={event_count} chain_head={chain_head}".format(
                    **verification
                )
            )
            return 0 if verification["verified"] else 2
    except AuthError as error:
        raise SystemExit(f"{error.code}: {error}") from error
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
