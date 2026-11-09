"""API clients: each key has its own label, rate limit and scopes.

Keys come only from the environment (Secret Manager in the cloud); they are never logged,
never stored raw (rate-limit buckets use a SHA-256 fingerprint) and never sent upstream.

* ``APP_API_KEY``       owner key: ``API_RATE_LIMIT``/window (default 60), scopes ``read`` + ``admin``
* ``APP_DEMO_API_KEY``  public demo key for reviewers: ``DEMO_API_RATE_LIMIT`` (default 20), ``read`` only
* ``APP_API_KEYS``      extra keys, comma-separated. Each entry is either ``<key>`` (read-only,
  ``API_RATE_LIMIT``) or ``<label>:<key>:<limit>[:<scope>+<scope>]``, e.g. ``ci:abc123:30:read``.
  Keys must not contain ``:`` or ``,``.
"""

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field

from core.config import env_int

log = logging.getLogger(__name__)

READ = "read"
ADMIN = "admin"


@dataclass(frozen=True)
class ApiClient:
    label: str
    key: str = field(repr=False)
    limit: int
    scopes: frozenset[str]

    @property
    def fingerprint(self) -> str:
        return key_fingerprint(self.key)


def key_fingerprint(key: str) -> str:
    """Stable, non-reversible identifier for a key (used for rate-limit buckets)."""
    return hashlib.sha256(key.encode()).hexdigest()


def _parse_extra(entry: str, index: int, default_limit: int) -> ApiClient | None:
    parts = [p.strip() for p in entry.split(":")]
    if len(parts) == 1 and parts[0]:
        return ApiClient(f"key{index}", parts[0], default_limit, frozenset({READ}))
    if len(parts) in (3, 4) and parts[0] and parts[1] and parts[2].isdigit():
        scopes = frozenset(s for s in (parts[3] if len(parts) == 4 else READ).split("+") if s)
        return ApiClient(parts[0], parts[1], int(parts[2]), scopes or frozenset({READ}))
    log.warning("Ignoring malformed APP_API_KEYS entry #%d (expected key or label:key:limit[:scopes])", index)
    return None


def configured_clients() -> list[ApiClient]:
    default_limit = env_int("API_RATE_LIMIT", 60)
    clients: list[ApiClient] = []
    owner = os.getenv("APP_API_KEY", "").strip()
    if owner:
        clients.append(ApiClient("owner", owner, default_limit, frozenset({READ, ADMIN})))
    demo = os.getenv("APP_DEMO_API_KEY", "").strip()
    if demo:
        clients.append(ApiClient("demo", demo, env_int("DEMO_API_RATE_LIMIT", 20), frozenset({READ})))
    entries = [e for e in os.getenv("APP_API_KEYS", "").split(",") if e.strip()]
    for index, entry in enumerate(entries, start=1):
        client = _parse_extra(entry, index, default_limit)
        if client and all(c.key != client.key for c in clients):
            clients.append(client)
    return clients


def match_client(candidate: str | None) -> ApiClient | None:
    """Return the client owning ``candidate`` (constant-time compare against every key)."""
    if not candidate:
        return None
    found = None
    for client in configured_clients():
        if hmac.compare_digest(candidate.encode(), client.key.encode()) and found is None:
            found = client
    return found
