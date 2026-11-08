"""CoinGecko / CoinPaprika adapters: auth headers, base URLs, validation, retries."""

from typing import Any

import pytest
import requests

import ingestion.common as common
from core.models import Coin
from ingestion.coingecko import ingest_coingecko
from ingestion.coinpaprika import ingest_coins


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)  # type: ignore[arg-type]

    def json(self) -> Any:
        return self._payload


@pytest.fixture
def http(monkeypatch):
    """Record outgoing requests; respond from a queue (last response repeats)."""
    calls: list[dict[str, Any]] = []
    responses: list[FakeResponse] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "params": params})
        return responses.pop(0) if len(responses) > 1 else responses[0]

    monkeypatch.setattr(common.requests, "get", fake_get)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setenv("APP_API_KEY", "app-secret-must-not-leak")
    return calls, responses


GECKO = [
    {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 50000, "market_cap": 1e12},
    {"id": "", "symbol": "bad", "name": "No Id"},
    {"id": "eth", "symbol": "eth", "name": "Ethereum", "current_price": "oops"},
]
PAPRIKA = [
    {"id": "btc-bitcoin", "symbol": "BTC", "name": "Bitcoin", "quotes": {"USD": {"price": 1, "market_cap": 2}}},
    {"id": "x", "symbol": "", "name": "Blank"},
]


def _no_app_key(headers: dict[str, str]) -> None:
    assert "app-secret-must-not-leak" not in headers.values()
    assert "X-API-KEY" not in headers


def test_coingecko_without_key(http, db):
    calls, responses = http
    responses.append(FakeResponse(GECKO))

    result = ingest_coingecko()

    assert calls[0]["url"] == "https://api.coingecko.com/api/v3/coins/markets"
    assert "x-cg-demo-api-key" not in calls[0]["headers"]
    _no_app_key(calls[0]["headers"])
    assert (result.linked, result.invalid, result.errors, result.status) == (1, 2, 0, "partial")
    assert db.query(Coin).filter_by(symbol="BTC").one().price_usd == 50000


def test_coingecko_with_demo_key_and_base_url(http, monkeypatch):
    calls, responses = http
    responses.append(FakeResponse([]))
    monkeypatch.setenv("COINGECKO_API_KEY", "cg-demo")
    monkeypatch.setenv("COINGECKO_BASE_URL", "https://gecko.example/api/v3/")

    ingest_coingecko()

    assert calls[0]["url"] == "https://gecko.example/api/v3/coins/markets"
    assert calls[0]["headers"]["x-cg-demo-api-key"] == "cg-demo"
    _no_app_key(calls[0]["headers"])


def test_coinpaprika_without_key_is_not_required(http, db):
    calls, responses = http
    responses.append(FakeResponse(PAPRIKA))

    result = ingest_coins()

    assert calls[0]["url"] == "https://api.coinpaprika.com/v1/tickers"
    assert "Authorization" not in calls[0]["headers"]
    _no_app_key(calls[0]["headers"])
    assert (result.linked, result.invalid, result.errors) == (1, 1, 0)


def test_coinpaprika_with_key_and_base_url(http, monkeypatch):
    calls, responses = http
    responses.append(FakeResponse([]))
    monkeypatch.setenv("COINPAPRIKA_API_KEY", "cp-key")
    monkeypatch.setenv("COINPAPRIKA_BASE_URL", "https://api-pro.coinpaprika.com/v1")

    ingest_coins()

    assert calls[0]["url"] == "https://api-pro.coinpaprika.com/v1/tickers"
    assert calls[0]["headers"]["Authorization"] == "cp-key"
    _no_app_key(calls[0]["headers"])


def test_retry_with_backoff_then_success(http, monkeypatch):
    calls, responses = http
    monkeypatch.setenv("COINGECKO_MAX_RETRIES", "3")
    responses.extend([FakeResponse(None, 429), FakeResponse(None, 500), FakeResponse(GECKO[:1])])

    result = ingest_coingecko()

    assert len(calls) == 3
    assert (result.linked, result.errors) == (1, 0)


def test_fetch_gives_up_and_counts_error(http, monkeypatch, caplog):
    calls, responses = http
    monkeypatch.setenv("COINPAPRIKA_MAX_RETRIES", "2")
    monkeypatch.setenv("COINPAPRIKA_API_KEY", "cp-secret")
    responses.append(FakeResponse(None, 503))

    result = ingest_coins()

    assert len(calls) == 2
    assert (result.linked, result.errors, result.status) == (0, 1, "failed")
    assert "cp-secret" not in caplog.text


def test_unexpected_payload_shape_counts_error(http):
    _, responses = http
    responses.append(FakeResponse({"error": "nope"}))
    assert ingest_coingecko().errors == 1
