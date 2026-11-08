from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from core.db import Base, engine
from core.migrations import run_migrations
from tests.conftest import reset_schema


def _version() -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_migrations_at_head_and_match_models():
    assert _version() == "0001_initial"
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_run_migrations_is_idempotent():
    run_migrations()
    run_migrations()
    assert _version() == "0001_initial"


def test_legacy_create_all_schema_is_stamped():
    try:
        reset_schema()
        Base.metadata.create_all(engine)  # how the old init_db built the schema
        run_migrations()
        assert _version() == "0001_initial"
        assert "raw_coins" in inspect(engine).get_table_names()
    finally:
        reset_schema()
        run_migrations()


def test_fresh_database_gets_all_tables():
    try:
        reset_schema()
        run_migrations()
        tables = set(inspect(engine).get_table_names())
        assert {"coins", "raw_coins", "checkpoints", "coin_source_mapping", "price_snapshots"} <= tables
    finally:
        reset_schema()
        run_migrations()
