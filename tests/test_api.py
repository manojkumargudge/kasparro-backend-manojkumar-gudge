import json

import pytest
from fastapi.testclient import TestClient

import main
from core.models import Checkpoint, Coin
from main import app

HEADERS = {"X-API-KEY": "testkey"}


@pytest.fixture
def client(db) -> TestClient:
    db.add(Coin(symbol="FOO", name="Foo Coin", price_usd=1.23, market_cap=1000))
    db.add(Coin(symbol="BAR", name="Bar Coin", price_usd=5.0, market_cap=10))
    run_meta = {"duration_seconds": 1.0, "linked": 1, "errors": 0, "status": "success"}
    db.add(Checkpoint(source="csv", last_value=json.dumps({"linked": 1, "errors": 0, "run_meta": run_meta})))
    db.commit()
    return TestClient(app)


def test_get_data(client):
    r = client.get("/data", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"]
    assert isinstance(body["api_latency_ms"], int)
    assert body["meta"] == {"page": 1, "limit": 10, "total_records": 2}


def test_data_filters_sorting_pagination(client):
    r = client.get("/data", params={"symbol": "fo"}, headers=HEADERS)
    assert [c["symbol"] for c in r.json()["data"]] == ["FOO"]

    r = client.get("/data", params={"sort": "market_cap", "order": "asc", "limit": 1, "page": 2}, headers=HEADERS)
    assert [c["symbol"] for c in r.json()["data"]] == ["FOO"]

    r = client.get("/data", params={"min_price": 2}, headers=HEADERS)
    assert [c["symbol"] for c in r.json()["data"]] == ["BAR"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["etl"]["records_processed"] >= 1
    assert body["etl"]["last_success_at"] is not None


def test_stats(client):
    r = client.get("/stats", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["total_records"] == 2
    assert body["price_stats"]["min"] == pytest.approx(1.23)
    assert body["etl"]["records_processed"] >= 1


def test_metrics_and_runs(client):
    r = client.get("/metrics", headers=HEADERS)
    assert r.status_code == 200
    assert "coins_total 2" in r.text
    assert 'etl_run_linked_records{source="csv"} 1' in r.text

    r = client.get("/runs", headers=HEADERS)
    assert r.json()["runs"][0]["source"] == "csv"

    r = client.get("/compare-runs", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"anomalies": []}


def test_non_health_routes_require_api_key(client):
    for path in ["/data", "/stats", "/metrics", "/runs", "/compare-runs"]:
        assert client.get(path).status_code == 401, f"{path} should require an API key"
        assert client.get(path, headers={"X-API-KEY": "wrong"}).status_code == 401
    assert client.get("/dashboard").status_code == 200


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "_RATE_LIMIT_MAX_REQUESTS", 3)
    codes = [client.get("/data", headers=HEADERS).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.get("/health").status_code == 200  # health is exempt


def test_startup_runs_migrations(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_migrations", lambda: calls.append("migrated"))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
    assert calls == ["migrated"]
