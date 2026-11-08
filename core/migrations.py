"""Run Alembic migrations programmatically (API startup and every ETL run)."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from core.db import engine as default_engine

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
BASELINE_REVISION = "0001_initial"
# Arbitrary constant; serialises concurrent migrators (API + ETL) on Postgres.
_ADVISORY_LOCK_ID = 7_201_337


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def run_migrations(engine: Engine | None = None) -> None:
    """Upgrade the database to the latest revision.

    Databases created by the old ``Base.metadata.create_all`` path have the tables
    but no ``alembic_version``; those are stamped at the baseline first.
    """
    engine = engine or default_engine
    cfg = alembic_config()
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _ADVISORY_LOCK_ID})
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "coins" in tables and "alembic_version" not in tables:
            log.warning("Unversioned legacy schema found; stamping baseline %s", BASELINE_REVISION)
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
    log.info("Database migrations up to date")
