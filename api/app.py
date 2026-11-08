"""FastAPI application: routers, request metrics, and startup (migrate → background ETL)."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from sqlalchemy.exc import OperationalError

from api.routers import data, health, ops, runs
from core.config import env_bool, env_int
from core.migrations import run_migrations
from services.etl import run_all
from services.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = logging.getLogger(__name__)


async def wait_for_db_and_migrate() -> None:
    """Apply Alembic migrations, retrying while the database is still starting."""
    deadline = time.monotonic() + env_int("DB_STARTUP_TIMEOUT", 30)
    while True:
        try:
            await asyncio.to_thread(run_migrations)
            return
        except OperationalError as exc:
            if time.monotonic() >= deadline:
                log.error("Database unavailable; giving up on migrations")
                raise
            log.warning("DB not ready yet: %s", type(exc).__name__)
            await asyncio.sleep(1)


async def etl_loop() -> None:
    """Run the ETL once at startup, then every ``ETL_INTERVAL_SECONDS`` (0 = once)."""
    interval = env_int("ETL_INTERVAL_SECONDS", 0)
    while True:
        try:
            await asyncio.to_thread(run_all)
        except Exception:
            log.exception("Background ETL failed")
        if interval <= 0:
            return
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await wait_for_db_and_migrate()
    task = asyncio.create_task(etl_loop()) if env_bool("RUN_ETL_ON_STARTUP", True) else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    app = FastAPI(title="Kasparro Backend", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def record_metrics(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, template, str(response.status_code)).inc()
        HTTP_LATENCY.labels(template).observe(time.perf_counter() - started)
        return response

    for module in (data, runs, health, ops):
        app.include_router(module.router)
    return app


app = create_app()
