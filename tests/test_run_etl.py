import sys
import types

from scripts import run_etl


def _module_with(name, fn):
    module = types.ModuleType(name)
    setattr(module, name, fn)
    return module


def test_run_skips_coinpaprika_without_key(monkeypatch):
    monkeypatch.setenv("FAIL_ON_ERROR", "1")
    monkeypatch.delenv("COINPAPRIKA_API_KEY", raising=False)

    calls = {"coingecko": 0, "csv": 0, "coinpaprika": 0}

    def fake_cg():
        calls["coingecko"] += 1
        return 1, 0

    def fake_csv():
        calls["csv"] += 1
        return 2, 0

    def fake_cp():
        calls["coinpaprika"] += 1
        return 3, 0

    monkeypatch.setitem(sys.modules, "ingestion.coingecko", _module_with("ingest_coingecko", fake_cg))
    monkeypatch.setitem(sys.modules, "ingestion.csvingest", _module_with("ingest_csv", fake_csv))
    monkeypatch.setitem(sys.modules, "ingestion.coinpaprika", _module_with("ingest_coins", fake_cp))

    run_etl.run()

    assert calls == {"coingecko": 1, "csv": 1, "coinpaprika": 0}


def test_run_calls_coinpaprika_with_key(monkeypatch):
    monkeypatch.setenv("FAIL_ON_ERROR", "1")
    monkeypatch.setenv("COINPAPRIKA_API_KEY", "dummy")

    calls = {"coingecko": 0, "csv": 0, "coinpaprika": 0}

    def fake_cg():
        calls["coingecko"] += 1
        return 1, 0

    def fake_csv():
        calls["csv"] += 1
        return 2, 0

    def fake_cp():
        calls["coinpaprika"] += 1
        return 3, 0

    monkeypatch.setitem(sys.modules, "ingestion.coingecko", _module_with("ingest_coingecko", fake_cg))
    monkeypatch.setitem(sys.modules, "ingestion.csvingest", _module_with("ingest_csv", fake_csv))
    monkeypatch.setitem(sys.modules, "ingestion.coinpaprika", _module_with("ingest_coins", fake_cp))

    run_etl.run()

    assert calls == {"coingecko": 1, "csv": 1, "coinpaprika": 1}
