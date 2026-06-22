# --------------------------------------------------
# ETL Run Comparison & Anomaly Detection Endpoints
# --------------------------------------------------
from fastapi import Response, FastAPI, Header

# create app early so decorators below can bind to it
app = FastAPI()

@app.get("/runs")
def get_runs(limit: int = 10, x_api_key: str | None = Header(None)):
    from core.auth import require_api_key
    require_api_key(x_api_key)
    with SessionLocal() as s:
        checkpoints = s.query(Checkpoint).order_by(Checkpoint.last_run.desc()).limit(limit).all()
        runs = []
        for cp in checkpoints:
            try:
                meta = _json.loads(cp.last_value or '{}')
                run_meta = meta.get("run_meta", {})
                runs.append({
                    "source": cp.source,
                    "last_run": cp.last_run,
                    "linked": run_meta.get("linked", 0),
                    "errors": run_meta.get("errors", 0),
                    "fail_injected": run_meta.get("fail_injected", False),
                    "resume": run_meta.get("resume", False),
                })
            except Exception:
                runs.append({"source": cp.source, "last_run": cp.last_run, "meta": cp.last_value})
        return {"runs": runs}

@app.get("/compare-runs")
def compare_runs(x_api_key: str | None = Header(None)):
    from core.auth import require_api_key
    require_api_key(x_api_key)
    with SessionLocal() as s:
        checkpoints = s.query(Checkpoint).order_by(Checkpoint.last_run.desc()).all()
        anomalies = []
        prev_linked = None
        for cp in checkpoints:
            try:
                meta = _json.loads(cp.last_value or '{}')
                run_meta = meta.get("run_meta", {})
                linked = run_meta.get("linked", 0)
                errors = run_meta.get("errors", 0)
                fail_injected = run_meta.get("fail_injected", False)
                if prev_linked is not None and abs(linked - prev_linked) > 10:
                    anomalies.append({
                        "source": cp.source,
                        "last_run": cp.last_run,
                        "linked": linked,
                        "prev_linked": prev_linked,
                        "anomaly": f"Linked count changed by {linked - prev_linked}"
                    })
                if errors > 0:
                    anomalies.append({
                        "source": cp.source,
                        "last_run": cp.last_run,
                        "errors": errors,
                        "anomaly": "Errors detected"
                    })
                if fail_injected:
                    anomalies.append({
                        "source": cp.source,
                        "last_run": cp.last_run,
                        "anomaly": "Failure injected during ETL"
                    })
                prev_linked = linked
            except Exception:
                continue
        return {"anomalies": anomalies}
from fastapi.responses import PlainTextResponse
from sqlalchemy import func

import json as _json
# --------------------------------------------------
# Metrics endpoint (Prometheus format)
# --------------------------------------------------
@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with SessionLocal() as s:
        total_coins = s.query(func.count(Coin.id)).scalar() or 0
        total_market_cap = s.query(func.sum(Coin.market_cap)).scalar() or 0
        # ETL run metadata from checkpoints
        checkpoints = s.query(Checkpoint).all()
        etl_runs = []
        for cp in checkpoints:
            try:
                meta = _json.loads(cp.last_value or '{}')
                run_meta = meta.get("run_meta", {})
                etl_runs.append(run_meta)
            except Exception:
                pass
        lines = [
            f"coins_total {total_coins}",
            f"coins_market_cap_total {total_market_cap}",
            f"etl_runs_count {len(etl_runs)}",
        ]
        for idx, run in enumerate(etl_runs):
            lines.append(f'etl_run_linked{{run="{idx}"}} {run.get("linked", 0)}')
            lines.append(f'etl_run_errors{{run="{idx}"}} {run.get("errors", 0)}')
        return "\n".join(lines)
from fastapi import FastAPI, HTTPException, Query, Depends
import asyncio
import os
import time
import uuid
from typing import Dict

from sqlalchemy import text
from core.db import engine, SessionLocal
from core.logger import get_logger
from core.auth import require_api_key
from core.models import Coin, Checkpoint

from ingestion.coingecko import ingest_coingecko
from ingestion.csvingest import ingest_csv
from ingestion.coinpaprika import ingest_coins

log = get_logger(__name__)



# --------------------------------------------------
# Background startup task (ETL runs here)
# --------------------------------------------------
async def _background_startup():
    import psycopg2

    db_timeout = int(os.getenv("DB_STARTUP_TIMEOUT", "30"))
    start = asyncio.get_event_loop().time()

    while asyncio.get_event_loop().time() - start < db_timeout:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: psycopg2.connect(os.getenv("DATABASE_URL")),
            )
            break
        except Exception as e:
            log.warning(f"DB not ready yet: {e}")
            await asyncio.sleep(1)

    try:
        engine.dispose()
    except Exception:
        pass

    try:
        await asyncio.to_thread(ingest_coingecko)
        await asyncio.to_thread(ingest_csv)
        await asyncio.to_thread(ingest_coins)
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

    return {"status": "ok", "db": db_ok}


# --------------------------------------------------
# Stats endpoint (FINAL – SCHEMA SAFE)
# --------------------------------------------------
@app.get("/stats")
def stats(api_key: bool = Depends(require_api_key)):
    try:
        with SessionLocal() as s:
            total_records = s.query(Coin).count()

            min_price, max_price, avg_price = s.execute(
                text("""
                    SELECT
                        MIN(price_usd),
                        MAX(price_usd),
                        AVG(price_usd)
                    FROM coins
                """)
            ).one()

            return {
                "total_records": total_records,
                "price_stats": {
                    "min": float(min_price) if min_price is not None else None,
                    "max": float(max_price) if max_price is not None else None,
                    "avg": float(avg_price) if avg_price is not None else None,
                },
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# Data endpoint (FINAL – SCHEMA SAFE)
# --------------------------------------------------
@app.get("/data")
def get_data(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    symbol: str | None = None,
    name: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: str = "price_usd",
    order: str = "desc",
    , x_api_key: str | None = Header(None)
):
    from core.auth import require_api_key
    require_api_key(x_api_key)
    request_id = str(uuid.uuid4())
    start_time = time.time()

    try:
        with SessionLocal() as s:
            q = s.query(Coin)

            if symbol:
                q = q.filter(Coin.symbol.ilike(f"%{symbol}%"))

            if name:
                q = q.filter(Coin.name.ilike(f"%{name}%"))

            if min_price is not None:
                q = q.filter(Coin.price_usd >= min_price)

            if max_price is not None:
                q = q.filter(Coin.price_usd <= max_price)

            allowed_sorts = {
                "price_usd": Coin.price_usd,
                "market_cap": Coin.market_cap,
            }
            sort_col = allowed_sorts.get(sort, Coin.price_usd)

            if order.lower() == "asc":
                q = q.order_by(sort_col.asc())
            else:
                q = q.order_by(sort_col.desc())

            total = q.count()

            rows = (
                q.offset((page - 1) * limit)
                .limit(limit)
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
        "data": items,
        "meta": {
            "page": page,
            "limit": limit,
            "total_records": total,
        },
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics(api_key: bool = Depends(require_api_key)):
    with SessionLocal() as s:
        total_coins = s.query(func.count(Coin.id)).scalar() or 0
        total_market_cap = s.query(func.sum(Coin.market_cap)).scalar() or 0
        # ETL run metadata from checkpoints
        checkpoints = s.query(Checkpoint).all()
        etl_runs = []
        for cp in checkpoints:
            try:
                meta = _json.loads(cp.last_value or '{}')
                run_meta = meta.get("run_meta", {})
                etl_runs.append(run_meta)
            except Exception:
                pass
        lines = [
            f"coins_total {total_coins}",
            f"coins_market_cap_total {total_market_cap}",
            f"etl_runs_count {len(etl_runs)}",
        ]
        for idx, run in enumerate(etl_runs):
            lines.append(f"etl_run_linked{{run=\"{idx}\"}} {run.get('linked', 0)}")
            lines.append(f"etl_run_errors{{run=\"{idx}\"}} {run.get('errors', 0)}")
        return "\n".join(lines)
