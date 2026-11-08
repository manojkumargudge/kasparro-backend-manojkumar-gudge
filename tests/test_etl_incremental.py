import json

from core.models import Checkpoint, Coin, CoinSourceMapping, PriceSnapshot, get_or_create_coin
from ingestion.csvingest import ingest_csv
from ingestion.extracsvingest import ingest_extra_csv
from tests.test_etl_csv import write_csv


def test_incremental_ingestion(tmp_path, monkeypatch, db):
    p = write_csv(tmp_path / "inc.csv", [["inc", "Incremental Coin", "2.5", "200"]])
    monkeypatch.setenv("CSV_PATH", str(p))
    assert ingest_csv().linked == 1

    write_csv(p, [["inc", "Incremental Coin", "2.5", "200"], ["inc2", "Incremental Coin 2", "3.5", "300"]])
    second = ingest_csv()

    assert second.linked == 1  # only the new row
    assert db.query(Coin).filter_by(symbol="INC").count() == 1
    assert db.query(Coin).filter_by(symbol="INC2").count() == 1


def test_schema_mismatch_extra_column_is_ignored(tmp_path, monkeypatch, db):
    p = write_csv(
        tmp_path / "m.csv",
        [["mis", "Mismatch Coin", "1.1", "11", "unexpected"]],
        header=("symbol", "name", "price_usd", "market_cap", "extra"),
    )
    monkeypatch.setenv("CSV_PATH", str(p))
    assert ingest_csv().linked == 1
    assert db.query(Coin).filter_by(symbol="MIS").one()


def test_repeat_csv_ingestion_is_idempotent(tmp_path, monkeypatch, db):
    monkeypatch.setenv("CSV_PATH", str(write_csv(tmp_path / "r.csv", [["dup", "Duplicate Coin", "9.99", "900"]])))
    ingest_csv()
    ingest_csv()

    assert db.query(Coin).filter_by(symbol="DUP").count() == 1
    assert db.query(CoinSourceMapping).filter_by(source="csv", source_coin_id="dup").count() == 1
    coin = db.query(Coin).filter_by(symbol="DUP").one()
    assert db.query(PriceSnapshot).filter_by(coin_id=coin.id).count() == 1


def test_symbol_collision_does_not_merge_unrelated_names(db):
    first = get_or_create_coin(db, symbol="same", name="Same One")
    second = get_or_create_coin(db, symbol="same", name="Same Two")
    repeat_first = get_or_create_coin(db, symbol="SAME", name="same one")

    assert first.id != second.id
    assert repeat_first.id == first.id
    assert db.query(Coin).filter_by(symbol="SAME").count() == 2


def test_extra_csv_failure_injection_then_resume(tmp_path, monkeypatch, db):
    rows = [[f"x{i}", f"Extra {i}", str(i), str(i * 10)] for i in range(4)]
    monkeypatch.setenv("EXTRA_CSV_PATH", str(write_csv(tmp_path / "extra.csv", rows)))
    monkeypatch.setenv("FAILURE_INJECT", "1")
    monkeypatch.setenv("FAIL_AFTER", "2")

    failed = ingest_extra_csv()
    assert (failed.linked, failed.errors) == (2, 1)
    meta = json.loads(db.query(Checkpoint).filter_by(source="extra_csv").one().last_value)
    assert meta["run_meta"]["fail_injected"] is True
    assert meta["processed_ids"] == ["x0", "x1"]

    monkeypatch.setenv("FAILURE_INJECT", "0")
    resumed = ingest_extra_csv()
    assert (resumed.linked, resumed.errors) == (2, 0)  # only the remaining rows
    assert db.query(Coin).filter(Coin.symbol.like("X%")).count() == 4
    db.expire_all()
    meta = json.loads(db.query(Checkpoint).filter_by(source="extra_csv").one().last_value)
    assert meta["run_meta"]["resume"] is True


def test_extra_csv_schema_drift_rows_are_invalid(tmp_path, monkeypatch):
    p = write_csv(tmp_path / "drift.csv", [["Drift Coin", "1"]], header=("coin_name", "price"))
    monkeypatch.setenv("EXTRA_CSV_PATH", str(p))
    result = ingest_extra_csv()
    assert (result.linked, result.invalid) == (0, 1)
