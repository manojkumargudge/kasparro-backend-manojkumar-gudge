import datetime as dt

import pytest
from pydantic import ValidationError

from schemas.coin import CoinGeckoRecord, CoinPaprikaRecord, CsvCoinRecord

T = "2026-11-01T10:00:00Z"


def test_csv_record_normalization():
    coin = CsvCoinRecord.model_validate(
        {"symbol": " btc ", "name": "Bitcoin", "price_usd": "$43,250.10", "market_cap": "N/A", "observed_at": T}
    ).to_coin()
    assert coin.symbol == "BTC"
    assert coin.source_coin_id == "btc"
    assert coin.price_usd == 43250.10
    assert coin.market_cap is None
    assert coin.observed_at == dt.datetime(2026, 11, 1, 10, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1761991200", dt.datetime(2025, 11, 1, 10, tzinfo=dt.UTC)),
        (1761991200, dt.datetime(2025, 11, 1, 10, tzinfo=dt.UTC)),
        ("2026-11-01 10:00:00", dt.datetime(2026, 11, 1, 10, tzinfo=dt.UTC)),
        (dt.datetime(2026, 11, 1, 10), dt.datetime(2026, 11, 1, 10, tzinfo=dt.UTC)),
    ],
)
def test_timestamps_accept_epoch_and_naive(value, expected):
    rec = CsvCoinRecord.model_validate({"symbol": "x", "name": "x", "observed_at": value})
    assert rec.observed_at == expected


@pytest.mark.parametrize("number,expected", [("1.2e12", 1.2e12), ("1_000", 1000.0), (" 5 USD ", 5.0), (3, 3.0)])
def test_numbers_are_coerced(number, expected):
    assert (
        CsvCoinRecord.model_validate({"symbol": "x", "name": "x", "price_usd": number, "observed_at": T}).price_usd
        == expected
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "", "name": "x", "observed_at": T},
        {"symbol": "x", "observed_at": T},
        {"symbol": "x", "name": "x", "price_usd": "abc", "observed_at": T},
        {"symbol": "x", "name": "x", "price_usd": "-5", "observed_at": T},
        {"symbol": "x", "name": "x", "price_usd": "nan", "observed_at": T},
        {"symbol": "x", "name": "x", "observed_at": "yesterday"},
        {"symbol": "x", "name": "x"},
    ],
)
def test_csv_record_rejects_bad_rows(payload):
    with pytest.raises(ValidationError):
        CsvCoinRecord.model_validate(payload)


def test_coingecko_record():
    rec = CoinGeckoRecord.model_validate(
        {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 1.5, "last_updated": T, "x": 1}
    )
    coin = rec.to_coin()
    assert (coin.source_coin_id, coin.symbol, coin.price_usd, coin.market_cap) == ("bitcoin", "BTC", 1.5, None)
    with pytest.raises(ValidationError):
        CoinGeckoRecord.model_validate({"id": "b", "symbol": "btc", "name": "Bitcoin"})  # no last_updated


def test_coinpaprika_record():
    rec = CoinPaprikaRecord.model_validate(
        {
            "id": "btc-bitcoin",
            "symbol": "BTC",
            "name": "Bitcoin",
            "quotes": {"USD": {"price": 2, "market_cap": 3}},
            "last_updated": T,
        }
    )
    assert (rec.to_coin().price_usd, rec.to_coin().market_cap) == (2.0, 3.0)
    bare = CoinPaprikaRecord.model_validate({"id": "a", "symbol": "A", "name": "A", "last_updated": T})
    assert bare.to_coin().price_usd is None
    with pytest.raises(ValidationError):
        CoinPaprikaRecord.model_validate(
            {"id": "a", "symbol": "A", "name": "A", "quotes": {"USD": {"price": "x"}}, "last_updated": T}
        )
