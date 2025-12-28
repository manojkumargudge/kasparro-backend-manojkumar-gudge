from fastapi import FastAPI, HTTPException, Query
import asyncio
from sqlalchemy import text

from core.db import engine
from core.logger import get_logger

from ingestion.coingecko import ingest_coingecko
from ingestion.csvingest import ingest_csv
from ingestion.extracsvingest import ingest_extra_csv

log = get_logger(__name__)

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    log.info("Starting ETL jobs on application startup")

    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, ingest_coingecko)
    await loop.run_in_executor(None, ingest_csv)
    await loop.run_in_executor(None, ingest_extra_csv)

    log.info("ETL jobs completed successfully")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/coins")
def get_coins(limit: int = Query(10, ge=1, le=100)):
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT
                        id,
                        symbol,
                        name,
                        price_usd,
                        market_cap
                    FROM coins
                    ORDER BY market_cap DESC
                    LIMIT :limit
                """),
                {"limit": limit}
            )

            return result.mappings().all()

    except Exception as e:
        log.error(f"/coins failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch coins")
