"""ETL: etl_runs bookkeeping, incremental loads, idempotent upserts, crash + resume, drift."""

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from core.models import Checkpoint, Coin, CoinSourceMapping, EtlRun, PriceSnapshot, RawCoin
from ingestion.coingecko import CoinGeckoSource
from ingestion.coinpaprika import CoinPaprikaSource
from ingestion.csv_source import CsvSource
from ingestion.registry import selected_sources
from scripts import run_etl
from services import etl
from tests.fakes import FakeResponse, FakeUpstream, gecko, paprika

ROOT = Path(__file__).resolve().parents[1]


def csv_source() -> CsvSource:
    return CsvSource("csv", "CSV_PATH", ("data/sample_coins.csv",))


def count(db, model, **where) -> int:
    q = select(func.count()).select_from(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return db.execute(q).scalar_one()


def runs(db, source: str) -> list[EtlRun]:
    db.expire_all()
    return list(db.execute(select(EtlRun).where(EtlRun.source == source).order_by(EtlRun.id)).scalars())


def set_mtime(path: Path, when: str) -> None:
    ts = dt.datetime.fromisoformat(when).timestamp()
    os.utime(path, (ts, ts))


ROWS = [
    ["aaa", "Alpha", "1.0", "10"],
    ["bbb", "Beta", "2.0", "20"],
    ["ccc", "Gamma", "3.0", "30"],
    ["ddd", "Delta", "4.0", "40"],
    ["eee", "Epsilon", "5.0", "50"],
]


# ----------------------------------------------------------------- etl_runs + basic load
def test_csv_run_records_etl_run(write_csv, monkeypatch, db):
    path = write_csv("c.csv", ROWS[:2])
    monkeypatch.setenv("CSV_PATH", str(path))

    result = etl.run_source(csv_source())

    assert (result.status, result.records_in, result.records_inserted, result.records_invalid) == (
        "success",
        2,
        2,
        0,
    )
    [run] = runs(db, "csv")
    assert run.status == "success" and run.ended_at and run.duration_seconds is not None
    assert run.checkpoint == {"batches_committed": 1, "records_committed": 2, "keys": 2}
    assert run.drift["status"] == "ok" and run.drift["confidence"] == 1.0
    assert count(db, PriceSnapshot, source="csv") == 2
    assert count(db, RawCoin, source="csv", processed=True) == 2
    assert db.execute(select(Coin.price_usd).where(Coin.symbol == "AAA")).scalar_one() == 1.0


def test_invalid_rows_logged_and_counted(write_csv, monkeypatch, db, caplog):
    rows = [["", "NoSymbol", "1", "1"], ["bad", "Bad", "abc", "1"], ["neg", "Neg", "-1", "1"], ["ok", "Ok", "", ""]]
    monkeypatch.setenv("CSV_PATH", str(write_csv("bad.csv", rows)))

    result = etl.run_source(csv_source())

    assert (result.status, result.records_in, result.records_inserted, result.records_invalid) == ("partial", 4, 1, 3)
    assert sum("rejected by validation" in r.getMessage() for r in caplog.records) == 3
    assert count(db, RawCoin, source="csv", processed=False) == 3  # raw kept even when rejected
    assert db.execute(select(Coin.price_usd).where(Coin.symbol == "OK")).scalar_one() is None


# ----------------------------------------------------------------- incremental + idempotent
def test_csv_rereads_and_only_writes_changed_rows(write_csv, monkeypatch, db):
    path = write_csv("inc.csv", ROWS[:3])
    set_mtime(path, "2026-11-01T00:00:00+00:00")
    monkeypatch.setenv("CSV_PATH", str(path))
    assert etl.run_source(csv_source()).records_inserted == 3

    second = etl.run_source(csv_source())
    assert (second.records_in, second.records_inserted, second.records_unchanged) == (3, 0, 3)

    changed = [ROWS[0], ["bbb", "Beta", "2.5", "20"], ROWS[2], ROWS[3]]
    write_csv("inc.csv", changed)
    set_mtime(path, "2026-11-02T00:00:00+00:00")
    third = etl.run_source(csv_source())

    assert (third.records_inserted, third.records_unchanged) == (2, 2)  # BBB changed + DDD new
    assert db.execute(select(Coin.price_usd).where(Coin.symbol == "BBB")).scalar_one() == 2.5
    assert count(db, PriceSnapshot, source="csv") == 5


def test_upsert_is_idempotent_without_checkpoint(write_csv, monkeypatch, db):
    monkeypatch.setenv("CSV_PATH", str(write_csv("i.csv", ROWS)))
    etl.run_source(csv_source())
    db.execute(text("DELETE FROM checkpoints"))
    db.commit()

    again = etl.run_source(csv_source())  # every row re-processed, nothing new written

    assert (again.records_inserted, again.records_updated, again.records_unchanged) == (0, 0, 5)
    assert count(db, PriceSnapshot) == 5
    assert count(db, CoinSourceMapping) == 5
    assert count(db, Coin) == 5


def test_same_observation_with_new_values_counts_as_update(write_csv, monkeypatch, db):
    path = write_csv("u.csv", ROWS[:1])
    set_mtime(path, "2026-11-01T00:00:00+00:00")
    monkeypatch.setenv("CSV_PATH", str(path))
    etl.run_source(csv_source())
    write_csv("u.csv", [["aaa", "Alpha", "9.0", "10"]])
    set_mtime(path, "2026-11-01T00:00:00+00:00")  # same observed_at, new value

    result = etl.run_source(csv_source())

    assert (result.records_inserted, result.records_updated) == (0, 1)
    assert db.execute(select(PriceSnapshot.price_usd)).scalar_one() == 9.0


def test_api_watermark_incremental(monkeypatch, db):
    up = FakeUpstream(monkeypatch)
    t1, t2 = "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z"
    up.queue(FakeResponse([gecko("bitcoin", "btc", "Bitcoin", 100, t1), gecko("ethereum", "eth", "Ethereum", 10, t1)]))
    assert etl.run_source(CoinGeckoSource()).records_inserted == 2

    up.responses[:] = [FakeResponse([gecko("bitcoin", "btc", "Bitcoin", 100, t1)])]
    assert etl.run_source(CoinGeckoSource()).records_unchanged == 1

    up.responses[:] = [
        FakeResponse([gecko("bitcoin", "btc", "Bitcoin", 105, t2), gecko("ethereum", "eth", "Ethereum", 10, t1)])
    ]
    third = etl.run_source(CoinGeckoSource())

    assert (third.records_inserted, third.records_unchanged) == (1, 1)
    assert db.execute(select(Coin.price_usd).where(Coin.symbol == "BTC")).scalar_one() == 105
    tokens = json.loads(db.execute(select(Checkpoint.last_value).where(Checkpoint.source == "coingecko")).scalar())
    assert tokens["tokens"]["bitcoin"] == "2026-11-01T11:00:00+00:00"


def test_latest_observation_wins_across_sources(monkeypatch, db):
    up = FakeUpstream(monkeypatch)
    up.queue(FakeResponse([gecko("bitcoin", "btc", "Bitcoin", 200, "2026-11-01T12:00:00Z")]))
    etl.run_source(CoinGeckoSource())
    up.responses[:] = [FakeResponse([paprika("btc-bitcoin", "BTC", "Bitcoin", 150, "2026-11-01T09:00:00Z")])]
    etl.run_source(CoinPaprikaSource())

    assert count(db, Coin) == 1  # unified across sources
    assert db.execute(select(Coin.price_usd)).scalar_one() == 200  # older observation does not win
    assert count(db, PriceSnapshot) == 2


def test_fetch_failure_marks_run_failed(monkeypatch, db):
    FakeUpstream(monkeypatch).queue(FakeResponse(None, 404))
    result = etl.run_source(CoinPaprikaSource())
    assert result.status == "failed"
    assert "HTTP 404" in runs(db, "coinpaprika")[0].error


# ----------------------------------------------------------------- crash + resume
def test_injected_crash_then_resume_without_duplicates(write_csv, monkeypatch, db):
    monkeypatch.setenv("CSV_PATH", str(write_csv("crash.csv", ROWS)))
    monkeypatch.setenv("ETL_BATCH_SIZE", "2")
    monkeypatch.setenv("FAIL_AFTER_N", "3")

    crashed = etl.run_source(csv_source())

    assert crashed.status == "failed"
    assert "InjectedCrash" in crashed.error
    [run] = runs(db, "csv")
    assert run.checkpoint["batches_committed"] == 1  # batch 1 committed, batch 2 rolled back
    assert run.meta["fail_after_n"] == 3
    assert count(db, PriceSnapshot) == 2

    monkeypatch.delenv("FAIL_AFTER_N")
    resumed = etl.run_source(csv_source())

    assert (resumed.status, resumed.records_unchanged, resumed.records_inserted) == ("success", 2, 3)
    assert count(db, PriceSnapshot) == 5
    assert count(db, Coin) == 5


def test_fail_sources_limits_injection(write_csv, monkeypatch):
    monkeypatch.setenv("CSV_PATH", str(write_csv("x.csv", ROWS)))
    monkeypatch.setenv("FAIL_AFTER_N", "1")
    monkeypatch.setenv("FAIL_SOURCES", "coingecko")
    assert etl.run_source(csv_source()).status == "success"


def test_hard_crash_process_exit_then_resume(write_csv, db):
    path = write_csv("hard.csv", ROWS)
    env = {
        **os.environ,
        "ETL_SOURCES": "csv",
        "CSV_PATH": str(path),
        "ETL_BATCH_SIZE": "2",
        "FAIL_AFTER_N": "3",
        "FAIL_MODE": "exit",
    }
    proc = subprocess.run([sys.executable, "scripts/run_etl.py"], cwd=ROOT, env=env, capture_output=True, timeout=120)

    assert proc.returncode == etl.CRASH_EXIT_CODE, proc.stderr.decode()[-2000:]
    [stuck] = runs(db, "csv")
    assert stuck.status == "running"  # the process died mid-run
    assert count(db, PriceSnapshot) == 2

    os.environ["CSV_PATH"] = str(path)
    try:
        resumed = etl.run_source(csv_source())
    finally:
        del os.environ["CSV_PATH"]

    abandoned, latest = runs(db, "csv")
    assert abandoned.status == "failed" and abandoned.error.startswith("abandoned")
    assert (latest.status, resumed.records_unchanged, resumed.records_inserted) == ("success", 2, 3)
    assert count(db, PriceSnapshot) == 5


def test_concurrent_run_of_same_source_is_skipped(write_csv, monkeypatch, db):
    monkeypatch.setenv("CSV_PATH", str(write_csv("l.csv", ROWS[:1])))
    with etl.source_lock("csv") as acquired:
        assert acquired
        assert etl.run_source(csv_source()).status == "skipped"
    assert runs(db, "csv") == []


# ----------------------------------------------------------------- schema drift
def test_quirky_csv_is_real_data_with_drift(monkeypatch, db):
    path = ROOT / "data" / "quirky_coins.csv"
    monkeypatch.setenv("QUIRKY_CSV_PATH", str(path))
    source = next(s for s in selected_sources() if s.name == "quirky_csv")

    result = etl.run_source(source)

    assert (result.status, result.records_in, result.records_inserted, result.records_invalid) == ("partial", 8, 6, 2)
    [run] = runs(db, "quirky_csv")
    assert run.drift["status"] == "warn"
    assert 0.6 <= run.drift["confidence"] < 0.9
    assert run.drift["mapping"] == {
        "symbol": "TICKR",
        "name": "Coin Nm",
        "price_usd": "Px (USD)",
        "market_cap": "MktCap_USD",
        "observed_at": "lastUpdated",
    }
    assert set(run.drift["extra_headers"]) == {"rank", "Notes"}
    prices = dict(db.execute(select(Coin.symbol, Coin.price_usd)).all())
    caps = dict(db.execute(select(Coin.symbol, Coin.market_cap)).all())
    assert prices["BTC"] == 86120.5 and caps["BTC"] == 1.702e12
    assert caps["ETH"] == 3.4e11 and caps["SOL"] is None and prices["XRP"] is None
    doge_at = db.execute(select(Coin.price_observed_at).where(Coin.symbol == "DOGE")).scalar_one()
    assert doge_at == dt.datetime.fromtimestamp(int(path.stat().st_mtime), tz=dt.UTC)


def test_drift_below_floor_rejects_file(write_csv, monkeypatch, db, caplog):
    path = write_csv("junk.csv", [["x", "y", "z"]], header=("foo", "bar", "baz"))
    monkeypatch.setenv("CSV_PATH", str(path))

    result = etl.run_source(csv_source())

    assert result.status == "failed" and "schema drift" in result.error
    [run] = runs(db, "csv")
    assert run.drift["status"] == "reject" and run.drift["confidence"] < 0.6
    assert count(db, RawCoin) == 0
    assert any(r.levelname == "ERROR" and "rejecting" in r.getMessage() for r in caplog.records)


# ----------------------------------------------------------------- runner
def test_run_all_and_script_exit_codes(write_csv, monkeypatch, db):
    monkeypatch.setenv("ETL_SOURCES", "csv,coinpaprika")
    monkeypatch.setenv("CSV_PATH", str(write_csv("r.csv", ROWS[:1])))
    up = FakeUpstream(monkeypatch).queue(FakeResponse([paprika("x", "X", "Xcoin", 1, "2026-11-01T00:00:00Z")]))

    assert run_etl.run() == 0
    assert {r.run_group for r in runs(db, "csv") + runs(db, "coinpaprika")} == {runs(db, "csv")[0].run_group}

    up.responses[:] = [FakeResponse(None, 500)]
    monkeypatch.setenv("COINPAPRIKA_MAX_RETRIES", "1")
    assert run_etl.run() == 1
    monkeypatch.setenv("FAIL_ON_ERROR", "0")
    assert run_etl.run() == 0


def test_run_etl_migration_failure(monkeypatch):
    import core.migrations

    def boom() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(core.migrations, "run_migrations", boom)
    assert run_etl.run() == 1


@pytest.mark.parametrize("names,expected", [("", 4), ("csv", 1), ("csv,coingecko", 2)])
def test_source_selection(monkeypatch, names, expected):
    monkeypatch.setenv("ETL_SOURCES", names)
    assert len(selected_sources()) == expected
