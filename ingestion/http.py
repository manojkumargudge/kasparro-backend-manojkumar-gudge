"""Outgoing HTTP for source APIs: per-source rate limit, retries with exponential
backoff + full jitter, and ``Retry-After`` support for 429/503.

Per-source tunables (``<SOURCE>`` upper-cased, e.g. ``COINGECKO_MIN_INTERVAL``):

* ``_MIN_INTERVAL``  minimum seconds between requests to that source (default 1.0)
* ``_MAX_RETRIES``   attempts before giving up (default 5)
* ``_BACKOFF_BASE``  first backoff ceiling in seconds, doubled per attempt (default 1.0)
* ``_BACKOFF_MAX``   cap for backoff and for honoured Retry-After (default 60)
"""

import datetime as dt
import email.utils
import logging
import random
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from core.config import env_float, env_int

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Indirection points so tests can run without real waiting.
sleep: Callable[[float], None] = time.sleep
monotonic: Callable[[], float] = time.monotonic
jitter: Callable[[float, float], float] = random.uniform

_lock = threading.Lock()
_next_allowed: dict[str, float] = {}


class FetchError(RuntimeError):
    """The source could not be fetched (non-retryable error or retries exhausted)."""


def parse_retry_after(value: str | None, now: dt.datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return max(0.0, (when - (now or dt.datetime.now(dt.UTC))).total_seconds())


def _throttle(source: str, min_interval: float) -> None:
    """Block until this source's next request slot (shared across threads)."""
    with _lock:
        now = monotonic()
        slot = max(now, _next_allowed.get(source, now))
        _next_allowed[source] = slot + min_interval
    wait = slot - now
    if wait > 0:
        log.info("rate limit: waiting %.2fs before %s request", wait, source, extra={"source": source})
        sleep(wait)


def reset_rate_limits() -> None:
    with _lock:
        _next_allowed.clear()


def get_json(
    source: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    prefix = source.upper()
    min_interval = env_float(f"{prefix}_MIN_INTERVAL", 1.0)
    max_retries = max(1, env_int(f"{prefix}_MAX_RETRIES", 5))
    base = env_float(f"{prefix}_BACKOFF_BASE", 1.0)
    cap = env_float(f"{prefix}_BACKOFF_MAX", 60.0)

    for attempt in range(1, max_retries + 1):
        _throttle(source, min_interval)
        status: int | None = None
        retry_after: float | None = None
        try:
            response = requests.get(url, headers=dict(headers or {}), params=params, timeout=timeout)
            status = response.status_code
            if status < 400:
                log.info("%s request ok (attempt %d)", source, attempt, extra={"source": source, "attempt": attempt})
                return response.json()
            if status not in RETRYABLE_STATUS:
                raise FetchError(f"{source}: HTTP {status} (not retryable)")
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            reason = f"HTTP {status}"
        except (requests.ConnectionError, requests.Timeout) as exc:
            reason = type(exc).__name__
        except ValueError as exc:  # invalid JSON body
            raise FetchError(f"{source}: invalid JSON response") from exc

        if attempt == max_retries:
            raise FetchError(f"{source}: giving up after {attempt} attempts ({reason})")
        backoff = jitter(0.0, min(cap, base * 2 ** (attempt - 1)))
        delay = min(cap, retry_after) if retry_after is not None else backoff
        # Never log headers: they may carry API keys.
        log.warning(
            "%s request failed (%s); retry %d/%d in %.2fs%s",
            source,
            reason,
            attempt,
            max_retries - 1,
            delay,
            " (Retry-After)" if retry_after is not None else "",
            extra={"source": source, "attempt": attempt, "status": status, "delay_seconds": round(delay, 3)},
        )
        sleep(delay)
    raise FetchError(f"{source}: no attempts made")  # pragma: no cover
