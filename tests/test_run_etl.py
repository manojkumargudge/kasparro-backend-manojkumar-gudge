import pytest

import core.migrations
import ingestion.coingecko
import ingestion.coinpaprika
import ingestion.csvingest
from ingestion.common import IngestResult
from scripts import run_etl


@pytest.fixture
def fake_sources(monkeypatch):
    order: list[str] = []
    results = {"coingecko": IngestResult("coingecko", linked=1), "csv": IngestResult("csv", linked=2),
               "coinpaprika": IngestResult("coinpaprika", linked=3)}

    def make(name):
        def ingest():
            order.append(name)
            return results[name]
        return ingest

    monkeypatch.setattr(core.migrations, "run_migrations", lambda: order.append("migrate"))
    monkeypatch.setattr(ingestion.coingecko, "ingest_coingecko", make("coingecko"))
    monkeypatch.setattr(ingestion.csvingest, "ingest_csv", make("csv"))
    monkeypatch.setattr(ingestion.coinpaprika, "ingest_coins", make("coinpaprika"))
    return order, results


def test_migrates_before_ingesting(fake_sources):
    order, _ = fake_sources
    assert run_etl.run() == 0
    assert order == ["migrate", "coingecko", "csv", "coinpaprika"]


def test_invalid_records_do_not_fail_run(fake_sources):
    _, results = fake_sources
    results["csv"].invalid = 5
    assert run_etl.run() == 0


def test_errors_fail_run_unless_disabled(fake_sources, monkeypatch):
    _, results = fake_sources
    results["coinpaprika"].errors = 1
    assert run_etl.run() == 1
    monkeypatch.setenv("FAIL_ON_ERROR", "0")
    assert run_etl.run() == 0


def test_migration_failure_aborts(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(core.migrations, "run_migrations", boom)
    assert run_etl.run() == 1
