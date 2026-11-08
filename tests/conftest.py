"""Test configuration: tests run against PostgreSQL, never SQLite.

``DATABASE_URL`` must point at a disposable database whose name ends in ``_test``
(the schema is dropped and rebuilt via Alembic). Default: local Postgres on 5432.
"""

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/kasparro_test")
os.environ["RUN_ETL_ON_STARTUP"] = "0"

_URL = make_url(os.environ["DATABASE_URL"])
if _URL.get_backend_name() != "postgresql":
    raise pytest.UsageError("Tests require a PostgreSQL DATABASE_URL")
if not (_URL.database or "").endswith("_test"):
    raise pytest.UsageError("Refusing to run: test database name must end with '_test' (it gets wiped)")


def _ensure_database() -> None:
    admin = create_engine(_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": _URL.database}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{_URL.database}"'))
    admin.dispose()


_ensure_database()

from core.db import SessionLocal, engine  # noqa: E402
from core.migrations import run_migrations  # noqa: E402
from ingestion import http  # noqa: E402

TABLES = "price_snapshots, coin_source_mapping, raw_coins, checkpoints, etl_runs, api_rate_limits, coins"
API_KEY = "testkey"

# core.db calls load_dotenv(), so a developer's .env must not leak into tests.
_ISOLATED_ENV = (
    "APP_API_KEYS",
    "CSV_PATH",
    "QUIRKY_CSV_PATH",
    "ETL_SOURCES",
    "ETL_BATCH_SIZE",
    "FAIL_AFTER_N",
    "FAIL_SOURCES",
    "FAIL_MODE",
    "FAIL_ON_ERROR",
    "COINGECKO_API_KEY",
    "COINPAPRIKA_API_KEY",
    "COINGECKO_BASE_URL",
    "COINPAPRIKA_BASE_URL",
    "COINGECKO_MAX_RETRIES",
    "COINPAPRIKA_MAX_RETRIES",
    "COINGECKO_PAGES",
    "API_RATE_LIMIT",
    "API_RATE_WINDOW_SECONDS",
    "DRIFT_WARN_THRESHOLD",
    "DRIFT_REJECT_THRESHOLD",
    "ETL_INTERVAL_SECONDS",
)


def reset_schema() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    reset_schema()
    run_migrations()
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    for var in _ISOLATED_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_API_KEY", API_KEY)
    monkeypatch.setenv("COINGECKO_MIN_INTERVAL", "0")
    monkeypatch.setenv("COINPAPRIKA_MIN_INTERVAL", "0")
    monkeypatch.setattr(http, "sleep", lambda s: None)
    http.reset_rate_limits()
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def write_csv(tmp_path: Path) -> Callable[..., Path]:
    def _write(
        name: str, rows: list[list[Any]], header: tuple[str, ...] = ("symbol", "name", "price_usd", "market_cap")
    ) -> Path:
        import csv

        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return path

    return _write
