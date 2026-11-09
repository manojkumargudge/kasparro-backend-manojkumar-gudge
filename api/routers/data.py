import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import get_db, read_client
from core.models import Coin

router = APIRouter(tags=["data"], dependencies=[Depends(read_client)])

SORTS = {"price_usd": Coin.price_usd, "market_cap": Coin.market_cap, "symbol": Coin.symbol}


@router.get("/data")
def get_data(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    symbol: str | None = None,
    name: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: Literal["price_usd", "market_cap", "symbol"] = "price_usd",
    order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Canonical coins with pagination, filters and sorting."""
    started = time.perf_counter()
    q = select(Coin)
    if symbol:
        q = q.where(Coin.symbol.ilike(f"%{symbol}%"))
    if name:
        q = q.where(Coin.name.ilike(f"%{name}%"))
    if min_price is not None:
        q = q.where(Coin.price_usd >= min_price)
    if max_price is not None:
        q = q.where(Coin.price_usd <= max_price)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    column = SORTS[sort]
    ordering = column.asc().nulls_last() if order == "asc" else column.desc().nulls_last()
    rows = db.execute(q.order_by(ordering, Coin.id).offset((page - 1) * limit).limit(limit)).scalars()
    items = [
        {
            "id": c.id,
            "symbol": c.symbol,
            "name": c.name,
            "price_usd": c.price_usd,
            "market_cap": c.market_cap,
            "observed_at": c.price_observed_at,
        }
        for c in rows
    ]
    return {
        "request_id": str(uuid.uuid4()),
        "api_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "data": items,
        "meta": {"page": page, "limit": limit, "total_records": total},
    }
