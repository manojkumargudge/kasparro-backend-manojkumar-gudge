import pytest
from pydantic import ValidationError

from schemas.coin import CoinGeckoRecord, CoinPaprikaRecord, CsvCoinRecord


def test_csv_record_normalization():
    coin = CsvCoinRecord.model_validate({"symbol": " btc ", "name": "Bitcoin", "price_usd": "100", "market_cap": ""})
    normalized = coin.to_coin()
    assert normalized.symbol == "BTC"
    assert normalized.source_coin_id == "btc"
    assert normalized.price_usd == 100.0
    assert normalized.market_cap is None


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "", "name": "x"},
        {"symbol": "x"},
        {"symbol": "x", "name": "x", "price_usd": "abc"},
        {"symbol": "x", "name": "x", "price_usd": "-5"},
        {"symbol": "x", "name": "x", "price_usd": "nan"},
    ],
)
def test_csv_record_rejects_bad_rows(payload):
    with pytest.raises(ValidationError):
        CsvCoinRecord.model_validate(payload)


def test_coingecko_record():
    rec = CoinGeckoRecord.model_validate(
        {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 1.5, "market_cap": None, "x": 1}
    )
    assert rec.to_coin().model_dump() == {
        "source_coin_id": "bitcoin",
        "symbol": "BTC",
        "name": "Bitcoin",
        "price_usd": 1.5,
        "market_cap": None,
    }
    with pytest.raises(ValidationError):
        CoinGeckoRecord.model_validate({"symbol": "btc", "name": "Bitcoin"})  # no id


def test_coinpaprika_record():
    rec = CoinPaprikaRecord.model_validate(
        {"id": "btc-bitcoin", "symbol": "BTC", "name": "Bitcoin", "quotes": {"USD": {"price": 2, "market_cap": 3}}}
    )
    assert (rec.to_coin().price_usd, rec.to_coin().market_cap) == (2.0, 3.0)
    no_quotes = CoinPaprikaRecord.model_validate({"id": "a", "symbol": "A", "name": "A"})
    assert no_quotes.to_coin().price_usd is None
    with pytest.raises(ValidationError):
        CoinPaprikaRecord.model_validate({"id": "a", "symbol": "A", "name": "A", "quotes": {"USD": {"price": "x"}}})
