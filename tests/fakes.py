"""Test doubles for upstream HTTP and helpers to seed ``etl_runs``."""

import datetime as dt
from typing import Any

import pytest
import requests

from core.models import EtlRun
from ingestion import http


class FakeResponse:
    def __init__(self, payload: Any = None, status: int = 200, headers: dict[str, str] | None = None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeUpstream:
    """Replaces ``requests.get`` inside ``ingestion.http``; records calls and sleeps."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.calls: list[dict[str, Any]] = []
        self.sleeps: list[float] = []
        self.responses: list[FakeResponse | Exception] = []
        monkeypatch.setattr(http.requests, "get", self._get)
        monkeypatch.setattr(http, "sleep", self.sleeps.append)

    def queue(self, *responses: FakeResponse | Exception) -> "FakeUpstream":
        self.responses.extend(responses)
        return self

    def _get(self, url: str, headers: dict[str, str] | None = None, params: Any = None, timeout: Any = None) -> Any:
        self.calls.append({"url": url, "headers": headers or {}, "params": params})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def gecko(coin_id: str, symbol: str, name: str, price: float, updated: str, cap: float = 1e9) -> dict[str, Any]:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": name,
        "current_price": price,
        "market_cap": cap,
        "last_updated": updated,
    }


def paprika(coin_id: str, symbol: str, name: str, price: float, updated: str) -> dict[str, Any]:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": name,
        "quotes": {"USD": {"price": price, "market_cap": 1e9}},
        "last_updated": updated,
    }


def seed_run(
    db: Any,
    source: str,
    *,
    status: str = "success",
    records_in: int = 10,
    duration: float = 1.0,
    minutes_ago: int = 0,
    **extra: Any,
) -> EtlRun:
    started = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes_ago)
    run = EtlRun(
        run_group="seed",
        source=source,
        status=status,
        started_at=started,
        ended_at=started + dt.timedelta(seconds=duration),
        duration_seconds=duration,
        records_in=records_in,
        **extra,
    )
    db.add(run)
    db.commit()
    return run


__all__ = ["FakeResponse", "FakeUpstream", "gecko", "paprika", "seed_run", "requests"]
