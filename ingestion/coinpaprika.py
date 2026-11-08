"""CoinPaprika ``/tickers`` ingestion.

Auth: optional. If ``COINPAPRIKA_API_KEY`` is set it is sent as ``Authorization``.
The free host (default) does not require a key. ``COINPAPRIKA_BASE_URL`` selects the host,
e.g. ``https://api-pro.coinpaprika.com/v1`` for a paid plan.
"""

import datetime as dt
import logging
import os

from core.db import SessionLocal
from ingestion.common import FetchError, IngestResult, fetch_json, finish_run, ingest_records
from schemas.coin import CoinPaprikaRecord

log = logging.getLogger(__name__)

SOURCE = "coinpaprika"
DEFAULT_BASE_URL = "https://api.coinpaprika.com/v1"
MAX_COINS = 50


def build_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.getenv("COINPAPRIKA_API_KEY")
    if api_key:
        headers["Authorization"] = api_key
    return headers


def tickers_url() -> str:
    base = (os.getenv("COINPAPRIKA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/tickers"


def ingest_coins() -> IngestResult:
    log.info("Starting CoinPaprika ingestion")
    result = IngestResult(source=SOURCE)
    started_at = dt.datetime.now(dt.UTC)

    with SessionLocal() as db:
        try:
            data = fetch_json(SOURCE, tickers_url(), headers=build_headers())
        except FetchError as exc:
            log.error("%s", exc)
            result.errors += 1
            return finish_run(db, result, started_at)

        if not isinstance(data, list):
            log.error("CoinPaprika returned %s, expected a list", type(data).__name__)
            result.errors += 1
            return finish_run(db, result, started_at)

        ingest_records(db, SOURCE, CoinPaprikaRecord, data[:MAX_COINS], result)
        return finish_run(db, result, started_at)


if __name__ == "__main__":
    ingest_coins()
