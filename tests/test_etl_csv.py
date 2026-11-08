import csv

from core.models import Coin, RawCoin
from ingestion.csvingest import ingest_csv


def write_csv(path, rows, header=("symbol", "name", "price_usd", "market_cap")):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def test_ingest_csv(tmp_path, monkeypatch, db):
    monkeypatch.setenv("CSV_PATH", str(write_csv(tmp_path / "s.csv", [["tst", "Test Coin", "1.5", "100"]])))
    result = ingest_csv()

    assert (result.linked, result.invalid, result.errors, result.status) == (1, 0, 0, "success")
    coin = db.query(Coin).filter_by(symbol="TST").one()
    assert coin.price_usd == 1.5
    assert db.query(RawCoin).filter_by(source="csv", processed=True).count() == 1


def test_invalid_rows_are_logged_counted_and_skipped(tmp_path, monkeypatch, db, caplog):
    rows = [
        ["", "NoSymbol", "1.0", "10"],  # missing symbol
        ["bad", "Bad Price", "notanumber", "10"],  # non-numeric price
        ["neg", "Negative", "-1", "10"],  # negative price
        ["ok", "Okay Coin", "", ""],  # blanks -> NULL, still valid
    ]
    monkeypatch.setenv("CSV_PATH", str(write_csv(tmp_path / "bad.csv", rows)))

    result = ingest_csv()

    assert (result.linked, result.invalid, result.errors) == (1, 3, 0)
    assert result.status == "partial"
    assert db.query(Coin).filter(Coin.symbol.in_(["BAD", "NEG"])).count() == 0
    ok = db.query(Coin).filter_by(symbol="OK").one()
    assert ok.price_usd is None and ok.market_cap is None
    assert sum("rejected by validation" in r.getMessage() for r in caplog.records) == 3
    # raw payloads are kept (unprocessed) even for rejected rows
    assert db.query(RawCoin).filter_by(source="csv", processed=False).count() == 3


def test_missing_file_counts_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CSV_PATH", str(tmp_path / "nope.csv"))
    result = ingest_csv()
    assert (result.errors, result.status) == (1, "failed")
