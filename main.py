from fastapi import FastAPI, HTTPException, Query
import asyncio
import os
import time
import uuid
from typing import Dict

from sqlalchemy import text
from core.db import engine, SessionLocal
from core.logger import get_logger
from core.models import Coin, Checkpoint

from ingestion.coingecko import ingest_coingecko
from ingestion.csvingest import ingest_csv
from ingestion.extracsvingest import ingest_extra_csv

log = get_logger(__name__)

app = FastAPI()


# --------------------------------------------------
# Background startup task (ETL runs here)
# --------------------------------------------------
async def _background_startup():
    import psycopg2

    db_timeout = int(os.getenv("DB_STARTUP_TIMEOUT", "30"))
    start = asyncio.get_event_loop().time()
    db_ready = False

    # Wait until Postgres is reachable
    while asyncio.get_event_loop().time() - start < db_timeout:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: psycopg2.connect(os.getenv("DATABASE_URL")),
            )
            db_ready = True
            break
        except Exception as e:
            log.warning(f"DB not ready yet: {e}")
            await asyncio.sleep(1)

    if db_ready:
        try:
            engine.dispose()
            log.info("DB ready, SQLAlchemy engine disposed")
        except Exception:
            pass
    else:
        log.warning("DB not ready after timeout, continuing anyway")

    # Run all ingestions sequentially
    try:
        await asyncio.to_thread(ingest_coingecko)
        await asyncio.to_thread(ingest_csv)
        await asyncio.to_thread(ingest_extra_csv)
        log.info("All background ingestions completed")
    except Exception as e:
        log.error(f"Background ingestion failed: {e}")


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_background_startup())


# --------------------------------------------------
# Health endpoint
# --------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, object]:
    def _db_check():
        try:
            with SessionLocal() as s:
                s.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    try:
        db_ok = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _db_check),
            timeout=5,
        )
    except Exception:
        db_ok = False

    etl_status = {}
    try:
        with SessionLocal() as s:
            cps = s.query(Checkpoint).all()
            for c in cps:
                etl_status[c.source] = {
                    "last_run": c.last_run.isoformat() if c.last_run else None,
                    "last_value": c.last_value,
                }
    except Exception:
        pass

    return {"status": "ok", "db": db_ok, "etl": etl_status}


# --------------------------------------------------
# Stats endpoint (P1 requirement)
# --------------------------------------------------
@app.get("/stats")
def stats():
    try:
        with SessionLocal() as s:
            cps = s.query(Checkpoint).all()
            result = {}
            for c in cps:
                result[c.source] = {
                    "last_run": c.last_run.isoformat() if c.last_run else None,
                    "last_value": c.last_value,
                }
        return {"etl_stats": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# Data endpoint
# --------------------------------------------------
@app.get("/data")
def get_data(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    symbol: str | None = None,
    name: str | None = None,
):
    request_id = str(uuid.uuid4())
    start_time = time.time()

    try:
        with SessionLocal() as s:
            q = s.query(Coin)

            if symbol:
                q = q.filter(Coin.symbol.ilike(f"%{symbol}%"))
            if name:
                q = q.filter(Coin.name.ilike(f"%{name}%"))

            total = q.count()
            rows = (
                q.offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )

            items = [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "name": r.name,
                    "price_usd": r.price_usd,
                    "market_cap": r.market_cap,
                }
                for r in rows
            ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    latency_ms = int((time.time() - start_time) * 1000)

    return {
        "request_id": request_id,
        "api_latency_ms": latency_ms,
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": items,
    }
