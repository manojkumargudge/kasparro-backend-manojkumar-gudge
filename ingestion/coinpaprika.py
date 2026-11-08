"""CoinPaprika ``/tickers``.

Auth: optional. ``COINPAPRIKA_API_KEY`` is sent as ``Authorization`` when set. The free host
(default) does not need it; ``COINPAPRIKA_BASE_URL`` selects another host (e.g. Pro).
"""

import os
from collections.abc import Iterator
from typing import Any

from core.config import env_int, env_str
from ingestion import http
from ingestion.base import Source
from schemas.coin import CoinPaprikaRecord

DEFAULT_BASE_URL = "https://api.coinpaprika.com/v1"


def build_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.getenv("COINPAPRIKA_API_KEY")
    if api_key:
        headers["Authorization"] = api_key
    return headers


def tickers_url() -> str:
    return env_str("COINPAPRIKA_BASE_URL", DEFAULT_BASE_URL).rstrip("/") + "/tickers"


class CoinPaprikaSource(Source):
    name = "coinpaprika"
    model = CoinPaprikaRecord
    incremental = "watermark"

    def fetch(self) -> Iterator[dict[str, Any]]:
        data = http.get_json(self.name, tickers_url(), headers=build_headers())
        if not isinstance(data, list):
            raise http.FetchError(f"coinpaprika: expected a list, got {type(data).__name__}")
        yield from data[: env_int("COINPAPRIKA_LIMIT", 50)]
