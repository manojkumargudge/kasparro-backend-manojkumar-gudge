"""CoinGecko ``/coins/markets`` ingestion.

Auth: if ``COINGECKO_API_KEY`` is set it is sent as ``x-cg-demo-api-key``; otherwise the
public (keyless) tier is used. The app's own ``APP_API_KEY`` is never sent upstream.
"""

import datetime as dt
import logging
import os

from core.db import SessionLocal
from ingestion.common import FetchError, IngestResult, fetch_json, finish_run, ingest_records
from schemas.coin import CoinGeckoRecord

log = logging.getLogger(__name__)

SOURCE = "coingecko"
DEFAULT_BASE_URL = "https://api.coingecko.com/api/v3"
PARAMS = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": 10,
    "page": 1,
    "sparkline": "false",
}


def build_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.getenv("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    return headers


def markets_url() -> str:
    base = (os.getenv("COINGECKO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/coins/markets"


def ingest_coingecko() -> IngestResult:
    log.info("Starting CoinGecko ingestion")
    result = IngestResult(source=SOURCE)
    started_at = dt.datetime.now(dt.UTC)

    with SessionLocal() as db:
        try:
            data = fetch_json(SOURCE, markets_url(), headers=build_headers(), params=PARAMS)
        except FetchError as exc:
            log.error("%s", exc)
            result.errors += 1
            return finish_run(db, result, started_at)

        if not isinstance(data, list):
            log.error("CoinGecko returned %s, expected a list", type(data).__name__)
            result.errors += 1
            return finish_run(db, result, started_at)

        ingest_records(db, SOURCE, CoinGeckoRecord, data, result)
        return finish_run(db, result, started_at)


if __name__ == "__main__":
    ingest_coingecko()
