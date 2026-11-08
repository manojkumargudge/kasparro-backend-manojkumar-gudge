# --------------------------------------------------
# ETL Run Comparison & Anomaly Detection Endpoints
# --------------------------------------------------
import asyncio
import json as _json
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError

from core.auth import require_api_key
from core.db import SessionLocal
from core.logger import get_logger
from core.migrations import run_migrations
from core.models import Checkpoint, Coin
from ingestion.coingecko import ingest_coingecko
from ingestion.coinpaprika import ingest_coins
from ingestion.csvingest import ingest_csv

# create app early so decorators below can bind to it
app = FastAPI()

_STATIC_DIR = Path(__file__).parent / "static"
_CLIENT_BUCKETS: defaultdict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 30


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    path = request.url.path
    if path not in {"/health", "/dashboard"}:
        client_key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = _CLIENT_BUCKETS[client_key]
        while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        bucket.append(now)
    return await call_next(request)


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serves the live metrics dashboard (static/dashboard.html)."""
    return FileResponse(_STATIC_DIR / "dashboard.html")


@app.get("/runs")
def get_runs(limit: int = 10, x_api_key: str | None = Header(None)) -> dict[str, Any]:
    require_api_key(x_api_key)
    with SessionLocal() as s:
        checkpoints = s.query(Checkpoint).order_by(Checkpoint.last_run.desc()).limit(limit).all()
        runs = []
        for cp in checkpoints:
            try:
                meta = _json.loads(cp.last_value or "{}")
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
def compare_runs(x_api_key: str | None = Header(None)) -> dict[str, Any]:
    require_api_key(x_api_key)
    with SessionLocal() as s:
        checkpoints = s.query(Checkpoint).order_by(Checkpoint.last_run.desc()).all()
        anomalies = []
        prev_linked = None
        for cp in checkpoints:
            try:
                meta = _json.loads(cp.last_value or "{}")
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


log = get_logger(__name__)


def _parse_checkpoint_run_meta(checkpoint: Checkpoint) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        meta = _json.loads(checkpoint.last_value or "{}")
    except Exception:
        meta = {}

    run_meta = meta.get("run_meta") if isinstance(meta, dict) else {}
    if not isinstance(run_meta, dict):
        run_meta = {}

    return meta if isinstance(meta, dict) else {}, run_meta


def _etl_summary() -> dict:
    with SessionLocal() as s:
        checkpoints = s.query(Checkpoint).order_by(Checkpoint.last_run.desc()).all()
        sources = []
        total_records = 0
        total_errors = 0
        success_runs: list[datetime] = []
        failure_runs: list[datetime] = []
        latest_run = None
        latest_status = None
        latest_source = None

        for checkpoint in checkpoints:
            meta, run_meta = _parse_checkpoint_run_meta(checkpoint)
            linked = int(run_meta.get("linked") or meta.get("linked") or 0)
            errors = int(run_meta.get("errors") or meta.get("errors") or 0)
            status = run_meta.get("status") or ("partial" if errors else "success")
            duration_seconds = run_meta.get("duration_seconds")

            sources.append(
                {
                    "source": checkpoint.source,
                    "last_run": checkpoint.last_run,
                    "status": status,
                    "linked": linked,
                    "errors": errors,
                    "duration_seconds": duration_seconds,
                    "run_meta": run_meta,
                }
            )

            total_records += linked
            total_errors += errors

            if checkpoint.last_run and (latest_run is None or checkpoint.last_run > latest_run):
                latest_run = checkpoint.last_run
                latest_status = status
                latest_source = checkpoint.source

            if checkpoint.last_run is None:
                continue
            if status == "success":
                success_runs.append(checkpoint.last_run)
            if errors > 0 or status in {"partial", "failed"}:
                failure_runs.append(checkpoint.last_run)

        return {
            "records_processed": total_records,
            "errors_detected": total_errors,
            "last_success_at": max(success_runs) if success_runs else None,
            "last_failure_at": max(failure_runs) if failure_runs else None,
            "latest_run_at": latest_run,
            "latest_status": latest_status,
            "latest_source": latest_source,
            "sources": sources,
        }


def _health_payload(db_ok: bool) -> dict:
    etl = _etl_summary()
    status = "ok" if db_ok else "degraded"
    if etl.get("latest_status") in {"failed", "partial"} and db_ok:
        status = "degraded"
    return {
        "status": status,
        "db": db_ok,
        "etl": etl,
    }


def _stats_payload(total_records: int, min_price: Any, max_price: Any, avg_price: Any) -> dict[str, Any]:
    return {
        "total_records": total_records,
        "price_stats": {
            "min": float(min_price) if min_price is not None else None,
            "max": float(max_price) if max_price is not None else None,
            "avg": float(avg_price) if avg_price is not None else None,
        },
        "etl": _etl_summary(),
    }


# --------------------------------------------------
# Startup: migrate the schema, then run the ETL in the background
# --------------------------------------------------
async def _wait_for_db_and_migrate() -> None:
    """Apply Alembic migrations, retrying while the database is still starting."""
    deadline = time.monotonic() + int(os.getenv("DB_STARTUP_TIMEOUT", "30"))
    while True:
        try:
            await asyncio.to_thread(run_migrations)
            return
        except OperationalError as exc:
            if time.monotonic() >= deadline:
                log.error("Database unavailable; giving up on migrations")
                raise
            log.warning(f"DB not ready yet: {type(exc).__name__}")
            await asyncio.sleep(1)


async def _background_etl() -> None:
    for ingest in (ingest_coingecko, ingest_csv, ingest_coins):
        try:
            await asyncio.to_thread(ingest)
        except Exception:
            log.exception(f"Background ingestion {ingest.__name__} failed")
    log.info("All background ingestions completed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _wait_for_db_and_migrate()
    etl_task = None
    if os.getenv("RUN_ETL_ON_STARTUP", "1").lower() in {"1", "true", "yes"}:
        etl_task = asyncio.create_task(_background_etl())
    try:
        yield
    finally:
        if etl_task:
            etl_task.cancel()
            with suppress(asyncio.CancelledError):
                await etl_task


app.router.lifespan_context = lifespan


# --------------------------------------------------
# Health endpoint
# --------------------------------------------------
@app.get("/health")
async def health() -> dict[str, object]:
    def _db_check() -> bool:
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

    return _health_payload(db_ok)


# --------------------------------------------------
# Stats endpoint (FINAL – SCHEMA SAFE)
# --------------------------------------------------
@app.get("/stats")
def stats(api_key: bool = Depends(require_api_key)) -> dict[str, Any]:
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

            return _stats_payload(total_records, min_price, max_price, avg_price)

    except Exception:
        log.exception("stats endpoint failed")
        raise HTTPException(status_code=500, detail="Internal server error") from None


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
    x_api_key: str | None = Header(None)
) -> dict[str, Any]:
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
            rows = q.offset((page - 1) * limit).limit(limit).all()

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

    except Exception:
        log.exception("data endpoint failed")
        raise HTTPException(status_code=500, detail="Internal server error") from None

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
def metrics(api_key: bool = Depends(require_api_key)) -> str:
    with SessionLocal() as s:
        total_coins = s.query(func.count(Coin.id)).scalar() or 0
        total_market_cap = s.query(func.sum(Coin.market_cap)).scalar() or 0
        checkpoints = s.query(Checkpoint).order_by(Checkpoint.last_run.desc()).all()

        lines = [
            "# HELP coins_total Total number of distinct canonical coins stored.",
            "# TYPE coins_total gauge",
            f"coins_total {total_coins}",
            "# HELP coins_market_cap_total Sum of market cap across all stored coins (USD).",
            "# TYPE coins_market_cap_total gauge",
            f"coins_market_cap_total {total_market_cap}",
            "# HELP etl_sources_total Number of distinct ETL sources with a checkpoint.",
            "# TYPE etl_sources_total gauge",
            f"etl_sources_total {len(checkpoints)}",
            "# HELP etl_run_linked_records Records linked on the most recent run for this source.",
            "# TYPE etl_run_linked_records gauge",
            "# HELP etl_run_errors Errors on the most recent run for this source.",
            "# TYPE etl_run_errors gauge",
            "# HELP etl_run_duration_seconds Duration of the most recent run for this source.",
            "# TYPE etl_run_duration_seconds gauge",
        ]

        for cp in checkpoints:
            _, run_meta = _parse_checkpoint_run_meta(cp)
            source = cp.source or "unknown"
            linked = run_meta.get("linked", 0)
            errors = run_meta.get("errors", 0)
            duration = run_meta.get("duration_seconds", 0) or 0
            lines.append(f'etl_run_linked_records{{source="{source}"}} {linked}')
            lines.append(f'etl_run_errors{{source="{source}"}} {errors}')
            lines.append(f'etl_run_duration_seconds{{source="{source}"}} {duration}')

        return "\n".join(lines) + "\n"
