"""API key handling. Keys come only from the environment and are never logged or stored raw."""

import hashlib
import hmac
import os


def configured_api_keys() -> list[str]:
    """Accepted client keys: ``APP_API_KEYS`` (comma-separated) plus ``APP_API_KEY``."""
    keys = [k.strip() for k in os.getenv("APP_API_KEYS", "").split(",") if k.strip()]
    single = os.getenv("APP_API_KEY", "").strip()
    if single:
        keys.append(single)
    return keys


def match_api_key(candidate: str | None) -> str | None:
    """Return the matching configured key (constant-time compare), else ``None``."""
    if not candidate:
        return None
    found = None
    for key in configured_api_keys():
        if hmac.compare_digest(candidate.encode(), key.encode()):
            found = key
    return found


def key_fingerprint(key: str) -> str:
    """Stable, non-reversible identifier for a key (used for rate-limit buckets)."""
    return hashlib.sha256(key.encode()).hexdigest()
