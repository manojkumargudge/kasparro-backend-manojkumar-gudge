"""Error text exposed by /runs, /health and /compare-runs never contains upstream URLs,
auth headers, DSNs or secret values."""

import re

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.app import app
from core.models import EtlRun
from core.redact import REDACTED, redact
from ingestion.coingecko import CoinGeckoSource
from ingestion.coinpaprika import CoinPaprikaSource
from services import etl
from tests.conftest import API_KEY
from tests.fakes import FakeUpstream

H = {"X-API-KEY": API_KEY}
UPSTREAM_KEY = "CG-realDemoKey1234567"
DSN = "postgresql://kasparro_user:s3cretPassw0rd@ep-cool-sun-123456.ap-south-1.aws.neon.tech/neondb?sslmode=require"

LEAKS = re.compile(r"https?://|postgres(ql)?://|neon\.tech|s3cretPassw0rd|CG-realDemoKey|x-cg-demo-api-key\s*[:=]\s*\S")


@pytest.mark.parametrize(
    "raw",
    [
        f"Invalid URL 'https://api.coingecko.com/api/v3/coins/markets?x_cg_demo_api_key={UPSTREAM_KEY}'",
        f"request failed with headers {{'x-cg-demo-api-key': '{UPSTREAM_KEY}', 'Accept': 'application/json'}}",
        "Authorization: Bearer abc.def.ghi rejected",
        f"could not connect using {DSN}",
        'connection to server at "ep-cool-sun-123456.ap-south-1.aws.neon.tech" (3.110.1.2), port 5432 failed',
        "invalid dsn: host=db.internal port=5432 user=kasparro password=hunter2 dbname=x",
        "(psycopg2.errors.UndefinedTable) relation missing\n[SQL: SELECT * FROM t WHERE k = %(k)s]\n"
        "[parameters: {'k': 'secret'}]\n(Background on this error at: https://sqlalche.me/e/20/f405)",
    ],
)
def test_redact_patterns(raw):
    out = redact(raw)
    assert out is not None
    assert not LEAKS.search(out), out
    assert "hunter2" not in out and "'secret'" not in out and "3.110.1.2" not in out
    assert "abc.def.ghi" not in out


def test_redact_configured_secret_values(monkeypatch):
    monkeypatch.setenv("COINPAPRIKA_API_KEY", "paprika-key-zzz999")
    monkeypatch.setenv("DATABASE_URL", DSN)
    out = redact("bad key paprika-key-zzz999 for kasparro_user at ep-cool-sun-123456.ap-south-1.aws.neon.tech")
    assert "paprika-key-zzz999" not in out and "kasparro_user" not in out and "neon.tech" not in out
    assert out.count(REDACTED) >= 2


def test_redact_keeps_useful_text():
    assert redact("coingecko: HTTP 404 (not retryable)") == "coingecko: HTTP 404 (not retryable)"
    assert redact("injected crash after 25 records at 10:00:00") == "injected crash after 25 records at 10:00:00"
    assert redact(None) is None


def _assert_clean(text: str) -> None:
    assert not LEAKS.search(text), text
    assert UPSTREAM_KEY not in text and "s3cretPassw0rd" not in text


def test_failed_run_error_is_clean_in_db_and_api(monkeypatch, db):
    monkeypatch.setenv("COINGECKO_API_KEY", UPSTREAM_KEY)
    up = FakeUpstream(monkeypatch)
    # requests puts the full URL (incl. query-string keys) in some exception messages.
    up.queue(requests.exceptions.TooManyRedirects(f"https://api.coingecko.com/x?x_cg_demo_api_key={UPSTREAM_KEY}"))
    assert etl.run_source(CoinGeckoSource()).status == "failed"

    class DsnLeakingSource(CoinPaprikaSource):
        def fetch(self):  # type: ignore[override]
            raise RuntimeError(f"could not connect to {DSN} with Authorization: {UPSTREAM_KEY}")
            yield {}

    assert etl.run_source(DsnLeakingSource()).status == "failed"

    stored = list(db.execute(select(EtlRun.error)).scalars())
    assert len(stored) == 2
    for error in stored:
        _assert_clean(error)
        assert REDACTED in error

    c = TestClient(app)
    for path in ("/runs?limit=10", "/compare-runs", "/stats"):
        _assert_clean(c.get(path, headers=H).text)
    _assert_clean(c.get("/health").text)


def test_legacy_unredacted_rows_are_scrubbed_on_read(db):
    from tests.fakes import seed_run

    seed_run(db, "csv", status="failed", error=f"boom {DSN} https://api.coinpaprika.com/v1/tickers")
    c = TestClient(app)
    for text in (c.get("/runs", headers=H).text, c.get("/health").text, c.get("/compare-runs", headers=H).text):
        _assert_clean(text)
