"""API rate limiting: fixed window per API key, counters stored in Postgres so the limit
holds across processes/instances. Only a SHA-256 fingerprint of the key is stored."""

import datetime as dt
import math
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.config import env_int
from core.models import ApiRateLimit


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int  # seconds until the window resets


def limits() -> tuple[int, int]:
    return env_int("API_RATE_LIMIT", 60), max(1, env_int("API_RATE_WINDOW_SECONDS", 60))


def hit(db: Session, key_hash: str) -> RateLimitDecision:
    limit, window = limits()
    now: dt.datetime = db.execute(select(func.now())).scalar_one()
    epoch = now.timestamp()
    window_start = dt.datetime.fromtimestamp(epoch - (epoch % window), tz=dt.UTC)
    stmt = insert(ApiRateLimit).values(key_hash=key_hash, window_start=window_start, count=1)
    count = db.execute(
        stmt.on_conflict_do_update(
            index_elements=[ApiRateLimit.key_hash, ApiRateLimit.window_start],
            set_={"count": ApiRateLimit.count + 1},
        ).returning(ApiRateLimit.count)
    ).scalar_one()
    db.execute(delete(ApiRateLimit).where(ApiRateLimit.key_hash == key_hash, ApiRateLimit.window_start < window_start))
    db.commit()
    retry_after = max(1, math.ceil(window_start.timestamp() + window - epoch))
    return RateLimitDecision(
        allowed=count <= limit, limit=limit, remaining=max(0, limit - count), retry_after=retry_after
    )
