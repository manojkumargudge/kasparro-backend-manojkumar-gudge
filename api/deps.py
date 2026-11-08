"""Shared FastAPI dependencies: API-key auth + per-key rate limiting."""

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.security import configured_api_keys, key_fingerprint, match_api_key
from services import rate_limit
from services.metrics import RATE_LIMITED


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_api_key: str | None = Header(None)) -> str:
    """Validate ``X-API-KEY``; returns the key's fingerprint."""
    if not configured_api_keys():
        raise HTTPException(status_code=500, detail="Server misconfigured: APP_API_KEY not set")
    key = match_api_key(x_api_key)
    if key is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return key_fingerprint(key)


def rate_limited_client(response: Response, fingerprint: str = Depends(require_api_key)) -> str:
    """Count the request against the key's window; 429 + ``Retry-After`` when exhausted."""
    with SessionLocal() as db:
        decision = rate_limit.hit(db, fingerprint)
    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.retry_after),
    }
    if not decision.allowed:
        RATE_LIMITED.inc()
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={**headers, "Retry-After": str(decision.retry_after)},
        )
    response.headers.update(headers)
    return fingerprint
