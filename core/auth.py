import os
from fastapi import Header, HTTPException

def require_api_key(x_api_key: str | None = Header(None)):
    """Dependency to require X-API-KEY header matches env var."""
    app_api_key = os.getenv("APP_API_KEY")
    if not app_api_key:
        raise HTTPException(status_code=500, detail="Server misconfigured: APP_API_KEY not set")
    if not x_api_key or x_api_key != app_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True
