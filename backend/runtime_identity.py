# -*- coding: utf-8 -*-
"""Stable, non-secret identity for API replicas and immutable releases."""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path


_SAFE_IDENTIFIER = re.compile(r"[^A-Za-z0-9._:-]+")
_PROCESS_STARTED_AT = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _identifier(value: object, fallback: str, *, limit: int = 80) -> str:
    normalized = _SAFE_IDENTIFIER.sub("-", str(value or "").strip()).strip("-._:")
    return (normalized or fallback)[:limit]


def _release_from_file() -> str:
    release_file = Path(__file__).resolve().parent.parent / "RELEASE_ID"
    try:
        return release_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def api_replica_identity() -> dict[str, str]:
    """Return public-safe process identity without hostnames, paths or secrets."""
    return {
        "schema_version": "api_replica_identity.v1",
        "replica_id": _identifier(os.getenv("API_REPLICA_ID"), "api-local"),
        "release_id": _identifier(
            os.getenv("APP_RELEASE_ID") or _release_from_file(),
            "development",
        ),
        "started_at": _PROCESS_STARTED_AT,
    }


__all__ = ["api_replica_identity"]
