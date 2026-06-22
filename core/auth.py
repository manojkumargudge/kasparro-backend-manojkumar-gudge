import os
from fastapi import Header, HTTPException

APP_API_KEY = os.getenv("APP_API_KEY")

def require_api_key(x_api_key: str | None = Header(None)):
    """Dependency to require X-API-KEY header matches env var."""
    if not APP_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: APP_API_KEY not set")
    if not x_api_key or x_api_key != APP_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True
