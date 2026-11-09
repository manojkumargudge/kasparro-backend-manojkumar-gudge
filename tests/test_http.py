"""Outgoing calls: auth headers, base URLs, rate limit, backoff with jitter, Retry-After."""

import datetime as dt

import pytest

from ingestion import coingecko, coinpaprika, http
from tests.fakes import FakeResponse, FakeUpstream, gecko, requests

APP_KEY = "app-secret-must-not-leak"


@pytest.fixture
def up(monkeypatch) -> FakeUpstream:
    monkeypatch.setenv("APP_API_KEY", APP_KEY)
    return FakeUpstream(monkeypatch)


def no_app_key(headers: dict[str, str]) -> None:
    assert APP_KEY not in headers.values()
    assert "X-API-KEY" not in headers


# ----------------------------------------------------------------- auth + URLs
@pytest.mark.parametrize(
    "source_cls,env,header",
    [
        (coingecko.CoinGeckoSource, "COINGECKO_API_KEY", "x-cg-demo-api-key"),
        (coinpaprika.CoinPaprikaSource, "COINPAPRIKA_API_KEY", "Authorization"),
    ],
)
def test_auth_mode_is_logged_without_the_key(up, monkeypatch, caplog, source_cls, env, header):
    up.queue(FakeResponse([]))
    list(source_cls().fetch())
    keyless = [r for r in caplog.records if getattr(r, "auth", None)]
    assert keyless[-1].auth == "keyless"

    monkeypatch.setenv(env, "real-key-value-123")
    list(source_cls().fetch())
    keyed = [r for r in caplog.records if getattr(r, "auth", None)][-1]
    assert keyed.auth == "api_key" and header in keyed.getMessage()
    assert "real-key-value-123" not in caplog.text


def test_coingecko_keyless(up):
    up.queue(FakeResponse([]))
    list(coingecko.CoinGeckoSource().fetch())
    call = up.calls[0]
    assert call["url"] == "https://api.coingecko.com/api/v3/coins/markets"
    assert "x-cg-demo-api-key" not in call["headers"]
    no_app_key(call["headers"])


def test_coingecko_demo_key_base_url_and_pages(up, monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "cg-demo")
    monkeypatch.setenv("COINGECKO_BASE_URL", "https://gecko.example/api/v3/")
    monkeypatch.setenv("COINGECKO_PAGES", "3")
    monkeypatch.setenv("COINGECKO_PER_PAGE", "1")
    t = "2026-11-01T00:00:00Z"
    up.queue(FakeResponse([gecko("a", "a", "A", 1, t)]), FakeResponse([]))

    items = list(coingecko.CoinGeckoSource().fetch())

    assert len(items) == 1 and len(up.calls) == 2  # stops at the first short page
    assert up.calls[0]["url"] == "https://gecko.example/api/v3/coins/markets"
    assert [c["params"]["page"] for c in up.calls] == [1, 2]
    assert up.calls[0]["headers"]["x-cg-demo-api-key"] == "cg-demo"
    no_app_key(up.calls[0]["headers"])


def test_coinpaprika_keyless_and_limit(up, monkeypatch):
    monkeypatch.setenv("COINPAPRIKA_LIMIT", "2")
    up.queue(FakeResponse([{"id": str(i)} for i in range(5)]))
    assert len(list(coinpaprika.CoinPaprikaSource().fetch())) == 2
    assert up.calls[0]["url"] == "https://api.coinpaprika.com/v1/tickers"
    assert "Authorization" not in up.calls[0]["headers"]
    no_app_key(up.calls[0]["headers"])


def test_coinpaprika_key_and_base_url(up, monkeypatch):
    monkeypatch.setenv("COINPAPRIKA_API_KEY", "cp-key")
    monkeypatch.setenv("COINPAPRIKA_BASE_URL", "https://api-pro.coinpaprika.com/v1")
    up.queue(FakeResponse([]))
    list(coinpaprika.CoinPaprikaSource().fetch())
    assert up.calls[0]["url"] == "https://api-pro.coinpaprika.com/v1/tickers"
    assert up.calls[0]["headers"]["Authorization"] == "cp-key"


@pytest.mark.parametrize("source_cls", [coingecko.CoinGeckoSource, coinpaprika.CoinPaprikaSource])
def test_non_list_payload_is_fetch_error(up, source_cls):
    up.queue(FakeResponse({"error": "nope"}))
    with pytest.raises(http.FetchError, match="expected a list"):
        list(source_cls().fetch())


# ----------------------------------------------------------------- retries
def test_retry_with_exponential_backoff_and_jitter(up, monkeypatch, caplog):
    monkeypatch.setenv("COINGECKO_MAX_RETRIES", "4")
    monkeypatch.setenv("COINGECKO_BACKOFF_BASE", "1")
    monkeypatch.setenv("COINGECKO_BACKOFF_MAX", "3")
    bounds: list[tuple[float, float]] = []
    monkeypatch.setattr(http, "jitter", lambda lo, hi: bounds.append((lo, hi)) or hi / 2)
    up.queue(FakeResponse(None, 500), FakeResponse(None, 502), FakeResponse(None, 503), FakeResponse([1]))

    assert http.get_json("coingecko", "https://x") == [1]

    assert bounds == [(0.0, 1.0), (0.0, 2.0), (0.0, 3.0)]  # doubles, capped at BACKOFF_MAX
    assert up.sleeps == [0.5, 1.0, 1.5]
    retries = [r for r in caplog.records if "retry" in r.getMessage()]
    assert len(retries) == 3 and retries[0].status == 500


def test_429_retry_after_seconds_is_honoured(up, monkeypatch):
    monkeypatch.setattr(http, "jitter", lambda lo, hi: 0.01)
    up.queue(FakeResponse(None, 429, {"Retry-After": "7"}), FakeResponse([]))
    http.get_json("coinpaprika", "https://x")
    assert up.sleeps == [7.0]


def test_429_retry_after_is_capped(up, monkeypatch):
    monkeypatch.setenv("COINPAPRIKA_BACKOFF_MAX", "5")
    up.queue(FakeResponse(None, 429, {"Retry-After": "3600"}), FakeResponse([]))
    http.get_json("coinpaprika", "https://x")
    assert up.sleeps == [5.0]


def test_retry_after_http_date():
    now = dt.datetime(2026, 11, 8, 12, 0, 0, tzinfo=dt.UTC)
    assert http.parse_retry_after("Sun, 08 Nov 2026 12:00:30 GMT", now=now) == 30.0
    assert http.parse_retry_after("Sun, 08 Nov 2026 11:00:00 GMT", now=now) == 0.0
    assert http.parse_retry_after("garbage") is None
    assert http.parse_retry_after(None) is None
    assert http.parse_retry_after("12") == 12.0


def test_non_retryable_status_fails_fast(up):
    up.queue(FakeResponse(None, 401))
    with pytest.raises(http.FetchError, match="HTTP 401"):
        http.get_json("coingecko", "https://x")
    assert len(up.calls) == 1


def test_connection_errors_retry_then_give_up(up, monkeypatch):
    monkeypatch.setenv("COINGECKO_MAX_RETRIES", "3")
    up.queue(requests.ConnectionError("boom"))
    with pytest.raises(http.FetchError, match="giving up after 3 attempts"):
        http.get_json("coingecko", "https://x")
    assert len(up.calls) == 3


def test_invalid_json_is_fetch_error(up):
    up.queue(FakeResponse(requests.JSONDecodeError("not json", "<html>", 0)))
    with pytest.raises(http.FetchError, match="invalid JSON"):
        http.get_json("coingecko", "https://x")


@pytest.mark.parametrize(
    "exc_type",
    [requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema, requests.exceptions.MissingSchema],
)
def test_url_errors_are_not_reported_as_invalid_json(up, exc_type):
    # These are ValueError subclasses; the old `except ValueError` mislabeled them as invalid JSON.
    assert issubclass(exc_type, ValueError)
    up.queue(exc_type("bad url"))
    with pytest.raises(exc_type):
        http.get_json("coingecko", "https://x")
    assert len(up.calls) == 1  # not retried either


def test_secret_headers_never_logged(up, monkeypatch, caplog):
    monkeypatch.setenv("COINPAPRIKA_API_KEY", "cp-secret-value")
    monkeypatch.setenv("COINPAPRIKA_MAX_RETRIES", "2")
    up.queue(FakeResponse(None, 503))
    with pytest.raises(http.FetchError):
        list(coinpaprika.CoinPaprikaSource().fetch())
    assert "cp-secret-value" not in caplog.text


# ----------------------------------------------------------------- per-source rate limit
def test_per_source_min_interval(monkeypatch, up):
    clock = [100.0]
    monkeypatch.setattr(http, "monotonic", lambda: clock[0])
    monkeypatch.setenv("COINGECKO_MIN_INTERVAL", "2")
    monkeypatch.setenv("COINPAPRIKA_MIN_INTERVAL", "2")
    up.queue(FakeResponse([]))

    http.get_json("coingecko", "https://x")  # first call: no wait
    http.get_json("coinpaprika", "https://y")  # different source: independent budget
    clock[0] += 0.5
    http.get_json("coingecko", "https://x")  # 1.5s left in this source's interval

    assert up.sleeps == [1.5]
