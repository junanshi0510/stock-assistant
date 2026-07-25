# -*- coding: utf-8 -*-
"""Rate-aware HTTP transport for quota-constrained market-data providers.

The limiter is shared by threads and, on POSIX hosts, by worker processes on
the same machine.  It intentionally does not log request parameters because
provider API keys are commonly carried in the query string.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import requests

import config

try:  # POSIX production hosts can coordinate all Celery/API processes.
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the in-process guard.
    fcntl = None


_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()
_last_monotonic: dict[str, float] = {}


def _provider_lock(provider: str) -> threading.Lock:
    with _thread_locks_guard:
        return _thread_locks.setdefault(provider, threading.Lock())


def _rate_limit_directory() -> Path:
    configured = str(
        os.getenv("MARKET_DATA_RATE_LIMIT_DIR") or ""
    ).strip()
    root = (
        Path(configured).expanduser()
        if configured
        else Path(tempfile.gettempdir()) / "stock-assistant-provider-gates"
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _claim_in_process_slot(provider: str, interval_seconds: float) -> None:
    now = time.monotonic()
    wait_seconds = max(
        0.0,
        float(_last_monotonic.get(provider, 0.0))
        + interval_seconds
        - now,
    )
    if wait_seconds:
        time.sleep(wait_seconds)
    _last_monotonic[provider] = time.monotonic()


def _claim_rate_slot(provider: str, interval_seconds: float) -> None:
    """Reserve one provider request slot without allowing a startup burst."""
    interval = max(0.0, float(interval_seconds))
    if interval <= 0:
        return
    lock = _provider_lock(provider)
    with lock:
        if fcntl is None:
            _claim_in_process_slot(provider, interval)
            return
        try:
            path = _rate_limit_directory() / f"{provider}.slot"
            with path.open("a+", encoding="ascii") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    raw = handle.read().strip()
                    try:
                        previous = float(raw)
                    except (TypeError, ValueError):
                        previous = 0.0
                    wait_seconds = max(
                        0.0,
                        previous + interval - time.time(),
                    )
                    if wait_seconds:
                        time.sleep(wait_seconds)
                    handle.seek(0)
                    handle.truncate()
                    handle.write(f"{time.time():.6f}")
                    handle.flush()
                    os.fsync(handle.fileno())
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # A read-only temp directory must not take market data offline.
            _claim_in_process_slot(provider, interval)


def _retry_after_seconds(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return max(
        0.0,
        (
            parsed.astimezone(dt.timezone.utc)
            - dt.datetime.now(dt.timezone.utc)
        ).total_seconds(),
    )


def _rate_limited_get(
    provider: str,
    url: str,
    *,
    interval_seconds: float,
    max_attempts: int,
    max_retry_after_seconds: float,
    **kwargs: Any,
):
    attempts = max(1, int(max_attempts))
    interval = max(0.0, float(interval_seconds))
    retry_cap = max(interval, float(max_retry_after_seconds))
    response = None
    for attempt in range(attempts):
        _claim_rate_slot(provider, interval)
        response = requests.get(url, **kwargs)
        if int(response.status_code) != 429 or attempt == attempts - 1:
            return response
        retry_after = _retry_after_seconds(
            response.headers.get("Retry-After")
        )
        wait_seconds = min(
            retry_cap,
            max(interval, retry_after or interval),
        )
        if wait_seconds:
            time.sleep(wait_seconds)
    return response  # pragma: no cover - loop always returns.


def massive_get(url: str, **kwargs: Any):
    # Massive officially supports bearer authentication.  Move legacy
    # query-string credentials into the header so HTTP errors, reverse-proxy
    # access logs, and exception URLs cannot disclose the key.
    params = dict(kwargs.pop("params", None) or {})
    api_key = params.pop("apiKey", None)
    headers = dict(kwargs.pop("headers", None) or {})
    if api_key and not any(
        str(name).lower() == "authorization" for name in headers
    ):
        headers["Authorization"] = f"Bearer {api_key}"
    return _rate_limited_get(
        "massive",
        url,
        interval_seconds=config.MASSIVE_MIN_REQUEST_INTERVAL_SECONDS,
        max_attempts=config.MASSIVE_RATE_LIMIT_MAX_ATTEMPTS,
        max_retry_after_seconds=(
            config.MASSIVE_MAX_RETRY_AFTER_SECONDS
        ),
        params=params,
        headers=headers,
        **kwargs,
    )


def alpha_vantage_get(url: str, **kwargs: Any):
    return _rate_limited_get(
        "alpha-vantage",
        url,
        interval_seconds=(
            config.ALPHAVANTAGE_MIN_REQUEST_INTERVAL_SECONDS
        ),
        max_attempts=(
            config.ALPHAVANTAGE_RATE_LIMIT_MAX_ATTEMPTS
        ),
        max_retry_after_seconds=(
            config.ALPHAVANTAGE_MAX_RETRY_AFTER_SECONDS
        ),
        **kwargs,
    )
