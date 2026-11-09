"""Shared FastAPI dependencies: API-key auth, scopes and per-key rate limiting."""

from collections.abc import Callable, Iterator

from fastapi import Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.security import ADMIN, READ, ApiClient, configured_clients, match_client
from services import rate_limit
from services.metrics import RATE_LIMITED


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_api_key: str | None = Header(None)) -> ApiClient:
    """Validate ``X-API-KEY`` against the configured clients."""
    if not configured_clients():
        raise HTTPException(status_code=500, detail="Server misconfigured: no API keys configured")
    client = match_client(x_api_key)
    if client is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return client


def authorized(scope: str) -> Callable[..., ApiClient]:
    """Dependency factory: key must have ``scope``; the request counts against that key's limit."""

    def dependency(response: Response, client: ApiClient = Depends(require_api_key)) -> ApiClient:
        if scope not in client.scopes:
            raise HTTPException(status_code=403, detail=f"This key does not have the '{scope}' scope")
        with SessionLocal() as db:
            decision = rate_limit.hit(db, client.fingerprint, client.limit)
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
        return client

    dependency.__name__ = f"authorized_{scope}"
    return dependency


read_client = authorized(READ)
admin_client = authorized(ADMIN)  # for future write/admin routes
