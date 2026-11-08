from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from core.db import Base, engine
from core.migrations import alembic_config, run_migrations
from tests.conftest import reset_schema

HEAD = "0002_etl_core"


def _version() -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


@pytest.fixture
def scratch_schema() -> Iterator[None]:
    """Tests that rebuild the schema; restore head afterwards for the rest of the suite."""
    try:
        reset_schema()
        yield
    finally:
        reset_schema()
        run_migrations()


def _legacy_0001_schema() -> None:
    """The schema the pre-Alembic ``create_all`` produced: 0001 tables, no alembic_version."""
    command.upgrade(alembic_config(), "0001_initial")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))


def test_migrations_at_head_and_match_models():
    assert _version() == HEAD
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_run_migrations_is_idempotent():
    run_migrations()
    run_migrations()
    assert _version() == HEAD


def test_fresh_database_gets_all_tables(scratch_schema):
    run_migrations()
    tables = set(inspect(engine).get_table_names())
    expected = {"coins", "raw_coins", "checkpoints", "coin_source_mapping", "price_snapshots", "etl_runs"}
    assert expected | {"api_rate_limits"} <= tables


def test_legacy_schema_is_stamped_then_upgraded_with_dedupe(scratch_schema):
    _legacy_0001_schema()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO coins (id, canonical_id, symbol, name, price_usd) VALUES "
                "(1, 'a', 'BTC', 'Bitcoin', 1), (2, 'b', 'btc', 'BITCOIN', 2), (3, 'c', 'ETH', 'Ethereum', 3)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO coin_source_mapping (coin_id, source, source_coin_id) VALUES "
                "(2, 'csv', 'btc'), (2, 'csv', 'btc'), (3, 'csv', 'eth')"
            )
        )
        conn.execute(text("INSERT INTO price_snapshots (coin_id, price_usd) VALUES (2, 2.0), (3, 3.0)"))

    run_migrations()

    assert _version() == HEAD
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM coins ORDER BY id")).scalars().all() == [1, 3]
        mappings = conn.execute(text("SELECT coin_id, source_coin_id FROM coin_source_mapping ORDER BY 2")).all()
        assert [tuple(m) for m in mappings] == [(1, "btc"), (3, "eth")]
        snaps = conn.execute(text("SELECT coin_id, source FROM price_snapshots ORDER BY coin_id")).all()
        assert [tuple(s) for s in snaps] == [(1, "legacy"), (3, "legacy")]


def test_downgrade_to_baseline_and_back(scratch_schema):
    run_migrations()
    command.downgrade(alembic_config(), "0001_initial")
    assert "etl_runs" not in inspect(engine).get_table_names()
    command.upgrade(alembic_config(), "head")
    assert _version() == HEAD
