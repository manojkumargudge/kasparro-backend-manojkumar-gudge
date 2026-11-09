"""CoinGecko ``/coins/markets``.

Auth: ``COINGECKO_API_KEY`` is sent as ``x-cg-demo-api-key`` when set; otherwise the keyless
public tier is used. The app's own ``APP_API_KEY`` is never sent upstream.
"""

import os
from collections.abc import Iterator
from typing import Any

from core.config import env_int, env_str
from ingestion import http
from ingestion.base import Source, log_auth_mode
from schemas.coin import CoinGeckoRecord

DEFAULT_BASE_URL = "https://api.coingecko.com/api/v3"


def build_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.getenv("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    return headers


def markets_url() -> str:
    return env_str("COINGECKO_BASE_URL", DEFAULT_BASE_URL).rstrip("/") + "/coins/markets"


class CoinGeckoSource(Source):
    name = "coingecko"
    model = CoinGeckoRecord
    incremental = "watermark"

    def fetch(self) -> Iterator[dict[str, Any]]:
        log_auth_mode(self.name, "x-cg-demo-api-key" if os.getenv("COINGECKO_API_KEY") else None)
        per_page = env_int("COINGECKO_PER_PAGE", 50)
        for page in range(1, env_int("COINGECKO_PAGES", 1) + 1):
            params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": per_page, "page": page}
            data = http.get_json(self.name, markets_url(), headers=build_headers(), params=params)
            if not isinstance(data, list):
                raise http.FetchError(f"coingecko: expected a list, got {type(data).__name__}")
            yield from data
            if len(data) < per_page:
                break
