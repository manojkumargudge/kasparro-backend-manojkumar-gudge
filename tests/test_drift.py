import pytest

from ingestion.drift import DriftRejected, check_drift, match_columns, normalize_header


def test_exact_headers_have_full_confidence():
    report = match_columns(["symbol", "name", "price_usd", "market_cap"])
    assert (report.status, report.confidence, report.drifted) == ("ok", 1.0, True)  # id/observed_at missing
    assert report.missing_fields == ["id", "observed_at"]
    assert report.renamed == {}


def test_renamed_reordered_extra_columns_are_matched():
    report = match_columns(["Notes", "Px (USD)", "TICKR", "Coin Nm", "MktCap_USD", "lastUpdated", "rank"])
    assert report.mapping == {
        "symbol": "TICKR",
        "name": "Coin Nm",
        "price_usd": "Px (USD)",
        "market_cap": "MktCap_USD",
        "observed_at": "lastUpdated",
    }
    assert report.status == "warn"
    assert report.extra_headers == ["Notes", "rank"]
    assert report.remap({"TICKR": "btc", "Coin Nm": "Bitcoin", "Notes": "x"})["symbol"] == "btc"


def test_missing_required_column_lowers_confidence():
    report = match_columns(["symbol", "price_usd"])
    assert report.confidence == pytest.approx(2 / 3, abs=0.01)
    assert report.status == "warn"
    assert "name" in report.missing_fields


def test_unrelated_headers_are_rejected(caplog):
    with pytest.raises(DriftRejected) as exc:
        check_drift("junk", ["foo", "bar", "baz"])
    assert exc.value.report.status == "reject"
    assert exc.value.report.mapping == {}


def test_thresholds_are_configurable(monkeypatch, caplog):
    headers = ["TICKR", "Coin Nm", "Px (USD)"]
    assert check_drift("q", headers).status == "warn"
    assert any("Schema drift" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
    monkeypatch.setenv("DRIFT_WARN_THRESHOLD", "0.5")
    assert check_drift("q", headers).status == "ok"
    monkeypatch.setenv("DRIFT_REJECT_THRESHOLD", "0.99")
    with pytest.raises(DriftRejected):
        check_drift("q", headers)


@pytest.mark.parametrize(
    "raw,expected",
    [("Px (USD)", "px usd"), ("lastUpdated", "last updated"), ("MktCap_USD", "mkt cap usd"), (" name ", "name")],
)
def test_normalize_header(raw, expected):
    assert normalize_header(raw) == expected
