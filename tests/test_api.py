import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

import api.app
import api.routers.health
from api.app import app
from core.models import ApiRateLimit, Coin
from core.security import key_fingerprint
from tests.conftest import API_KEY
from tests.fakes import seed_run

H = {"X-API-KEY": API_KEY}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def coins(db) -> None:
    db.add_all(
        [
            Coin(symbol="FOO", name="Foo Coin", price_usd=1.23, market_cap=1000),
            Coin(symbol="BAR", name="Bar Coin", price_usd=5.0, market_cap=10),
            Coin(symbol="NUL", name="No Price", price_usd=None, market_cap=None),
        ]
    )
    db.commit()


# ----------------------------------------------------------------- /data
def test_get_data(client, coins):
    body = client.get("/data", headers=H).json()
    assert body["request_id"] and isinstance(body["api_latency_ms"], float)
    assert body["meta"] == {"page": 1, "limit": 10, "total_records": 3}
    assert [c["symbol"] for c in body["data"]] == ["BAR", "FOO", "NUL"]  # price desc, nulls last


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"symbol": "fo"}, ["FOO"]),
        ({"name": "bar"}, ["BAR"]),
        ({"min_price": 2}, ["BAR"]),
        ({"max_price": 2}, ["FOO"]),
        ({"sort": "market_cap", "order": "asc", "limit": 1, "page": 2}, ["FOO"]),
        ({"sort": "symbol", "order": "asc"}, ["BAR", "FOO", "NUL"]),
    ],
)
def test_data_filters_sorting_pagination(client, coins, params, expected):
    assert [c["symbol"] for c in client.get("/data", params=params, headers=H).json()["data"]] == expected


def test_data_rejects_bad_params(client):
    assert client.get("/data", params={"limit": 500}, headers=H).status_code == 422
    assert client.get("/data", params={"sort": "drop table"}, headers=H).status_code == 422


# ----------------------------------------------------------------- auth
@pytest.mark.parametrize("path", ["/data", "/stats", "/runs", "/compare-runs"])
def test_protected_routes_require_key(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-KEY": "wrong"}).status_code == 401
    assert client.get(path, headers=H).status_code == 200


def test_public_routes(client):
    for path in ("/health", "/metrics", "/dashboard"):
        assert client.get(path).status_code == 200


def test_missing_server_key_is_500(client, monkeypatch):
    monkeypatch.delenv("APP_API_KEY")
    assert client.get("/data", headers=H).status_code == 500


# ----------------------------------------------------------------- rate limit
def test_rate_limit_per_key_in_postgres(client, monkeypatch, db):
    monkeypatch.setenv("API_RATE_LIMIT", "3")
    monkeypatch.setenv("APP_API_KEYS", "second-key")

    codes = [client.get("/data", headers=H) for _ in range(4)]
    assert [r.status_code for r in codes] == [200, 200, 200, 429]
    assert codes[0].headers["X-RateLimit-Remaining"] == "2"
    assert 1 <= int(codes[3].headers["Retry-After"]) <= 60
    assert codes[3].json() == {"detail": "Rate limit exceeded"}

    assert client.get("/data", headers={"X-API-KEY": "second-key"}).status_code == 200  # separate bucket
    assert client.get("/health").status_code == 200  # public routes are not limited

    rows = db.execute(select(ApiRateLimit.key_hash, ApiRateLimit.count)).all()
    assert dict(rows) == {key_fingerprint(API_KEY): 4, key_fingerprint("second-key"): 1}
    assert API_KEY not in str(rows)  # only fingerprints are stored


def test_rate_limit_window_resets(client, monkeypatch, db):
    monkeypatch.setenv("API_RATE_LIMIT", "1")
    assert client.get("/runs", headers=H).status_code == 200
    assert client.get("/runs", headers=H).status_code == 429
    db.execute(ApiRateLimit.__table__.update().values(window_start=dt.datetime(2000, 1, 1, tzinfo=dt.UTC)))
    db.commit()
    assert client.get("/runs", headers=H).status_code == 200  # new window; old one pruned
    assert db.execute(select(ApiRateLimit.window_start)).scalars().all()[0].year > 2000


# ----------------------------------------------------------------- /health
def test_health_ok_with_latest_run_per_source(client, db):
    seed_run(db, "csv", minutes_ago=10, status="failed")
    seed_run(db, "csv", minutes_ago=1)
    seed_run(db, "coingecko", minutes_ago=5, status="partial")
    body = client.get("/health").json()
    assert (body["status"], body["db"], body["failed_sources"]) == ("ok", True, [])
    assert {s["source"]: s["status"] for s in body["etl"]["sources"]} == {"csv": "success", "coingecko": "partial"}
    assert body["etl"]["latest_source"] == "csv"


def test_health_degraded_when_latest_run_failed(client, db):
    seed_run(db, "coinpaprika", status="failed", error="FetchError: boom")
    body = client.get("/health").json()
    assert (body["status"], body["failed_sources"]) == ("degraded", ["coinpaprika"])
    assert body["etl"]["sources"][0]["error"] == "FetchError: boom"


def test_health_503_when_db_down(client, monkeypatch):
    def broken():
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr(api.routers.health, "SessionLocal", broken)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "down"


# ----------------------------------------------------------------- /stats, /runs
def test_stats_reads_etl_runs(client, coins, db):
    seed_run(db, "csv", minutes_ago=30, status="failed", records_in=0)
    seed_run(db, "csv", minutes_ago=20, records_in=5, records_inserted=5, duration=2.0)
    seed_run(db, "coingecko", minutes_ago=10, records_in=7, records_invalid=1, status="partial")
    body = client.get("/stats", headers=H).json()

    assert body["total_records"] == 3
    assert body["price_stats"]["min"] == pytest.approx(1.23)
    etl = body["etl"]
    assert (etl["records_processed"], etl["total_runs"]) == (12, 3)
    assert etl["last_success_at"] and etl["last_failure_at"]
    csv = next(s for s in etl["sources"] if s["source"] == "csv")
    assert (csv["runs"], csv["records_inserted"], csv["last_run"]["duration_seconds"]) == (2, 5, 2.0)
    assert etl["last_duration_seconds"]["coingecko"] == 1.0


def test_runs_limit_and_source_filter(client, db):
    for i in range(5):
        seed_run(db, "csv", minutes_ago=10 - i, records_in=i)
    seed_run(db, "coingecko", minutes_ago=0)

    body = client.get("/runs", params={"limit": 3}, headers=H).json()
    assert [r["source"] for r in body["runs"]] == ["coingecko", "csv", "csv"]
    assert {"records_in", "records_inserted", "records_updated", "records_invalid", "drift", "checkpoint"} <= set(
        body["runs"][0]
    )
    only_csv = client.get("/runs", params={"limit": 10, "source": "csv"}, headers=H).json()["runs"]
    assert [r["records_in"] for r in only_csv] == [4, 3, 2, 1, 0]
    assert client.get("/runs", params={"limit": 0}, headers=H).status_code == 422


# ----------------------------------------------------------------- /compare-runs
def history(db, source: str, counts: list[int], durations: list[float] | None = None) -> None:
    durations = durations or [1.0] * len(counts)
    for i, (n, d) in enumerate(zip(counts, durations, strict=True)):
        seed_run(db, source, records_in=n, duration=d, minutes_ago=len(counts) - i)


def test_compare_runs_flags_count_anomaly(client, db):
    history(db, "csv", [100, 101, 99, 100, 102, 98, 400], [1.0, 1.1, 0.9, 1.0, 1.2, 0.8, 1.0])
    body = client.get("/compare-runs", params={"source": "csv"}, headers=H).json()
    entry = body["sources"][0]
    assert entry["metrics"]["records_in"]["anomaly"] is True
    assert entry["metrics"]["records_in"]["z"] > 3
    assert entry["metrics"]["duration_seconds"]["anomaly"] is False
    assert entry["delta_vs_previous"]["records_in"] == 302
    assert [a["metric"] for a in body["anomalies"]] == ["records_in"]
    assert len(entry["history"]) == 7


def test_compare_runs_flags_duration_anomaly(client, db):
    history(db, "coingecko", [10] * 7, [1.0, 1.1, 0.9, 1.0, 1.1, 0.9, 30.0])
    flags = client.get("/compare-runs", headers=H).json()["anomalies"]
    assert [(a["source"], a["metric"]) for a in flags] == [("coingecko", "duration_seconds")]


def test_compare_runs_needs_min_samples(client, db):
    history(db, "csv", [100, 100, 5000])
    entry = client.get("/compare-runs", headers=H).json()["sources"][0]
    assert entry["metrics"]["records_in"] == {
        "latest": 5000.0,
        "samples": 2,
        "mean": None,
        "stdev": None,
        "z": None,
        "anomaly": False,
        "reason": "insufficient history",
    }
    lowered = client.get("/compare-runs", params={"min_samples": 2}, headers=H).json()
    assert lowered["sources"][0]["metrics"]["records_in"]["anomaly"] is True  # flat history → any change flags


def test_compare_runs_normal_variation_and_failed_latest(client, db):
    history(db, "csv", [100, 101, 99, 100, 102, 98, 101])
    seed_run(db, "csv", status="failed", error="boom")
    body = client.get("/compare-runs", headers=H).json()
    entry = body["sources"][0]
    assert entry["metrics"]["records_in"]["anomaly"] is False  # failed run excluded from the stats
    assert [a["metric"] for a in entry["anomalies"]] == ["status"]
    assert body["anomalies"][0]["anomaly"] == "latest run failed"


# ----------------------------------------------------------------- /metrics
def test_metrics_prometheus_format_without_secrets(client, coins, db, monkeypatch):
    monkeypatch.setenv("APP_API_KEYS", "another-secret")
    seed_run(db, "csv", records_in=4, records_inserted=3, records_invalid=1, drift={"confidence": 0.88})
    seed_run(db, "coingecko", status="failed")
    client.get("/data", headers=H)

    r = client.get("/metrics")

    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    for line in (
        "kasparro_db_up 1.0",
        "kasparro_coins 3.0",
        'kasparro_etl_runs_total{source="csv",status="success"} 1.0',
        'kasparro_etl_last_run_success{source="coingecko"} 0.0',
        'kasparro_etl_last_run_records{outcome="invalid",source="csv"} 1.0',
        'kasparro_etl_last_run_schema_confidence{source="csv"} 0.88',
        'kasparro_http_requests_total{method="GET",route="/data",status="200"}',
        "# TYPE kasparro_http_request_duration_seconds histogram",
    ):
        assert line in text, line
    assert API_KEY not in text and "another-secret" not in text


def test_metrics_when_db_down(client, monkeypatch):
    import services.metrics

    def broken():
        raise OperationalError("x", {}, Exception("down"))

    monkeypatch.setattr(services.metrics, "SessionLocal", broken)
    assert "kasparro_db_up 0.0" in client.get("/metrics").text


def test_rate_limited_counter(client, monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT", "0")
    assert client.get("/stats", headers=H).status_code == 429
    assert "kasparro_api_rate_limited_total" in client.get("/metrics").text


# ----------------------------------------------------------------- startup
def test_startup_migrates_then_runs_etl(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(api.app, "run_migrations", lambda: calls.append("migrate"))
    monkeypatch.setattr(api.app, "run_all", lambda: calls.append("etl"))
    monkeypatch.setenv("RUN_ETL_ON_STARTUP", "1")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        for _ in range(100):
            if "etl" in calls:
                break
            import time

            time.sleep(0.01)
    assert calls == ["migrate", "etl"]


def test_startup_retries_until_db_ready(monkeypatch):
    attempts: list[int] = []

    def flaky() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise OperationalError("x", {}, Exception("starting"))

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(api.app, "run_migrations", flaky)
    monkeypatch.setattr(api.app.asyncio, "sleep", no_sleep)
    with TestClient(app):
        pass
    assert len(attempts) == 3


def test_startup_gives_up_after_timeout(monkeypatch):
    def down() -> None:
        raise OperationalError("x", {}, Exception("down"))

    monkeypatch.setattr(api.app, "run_migrations", down)
    monkeypatch.setenv("DB_STARTUP_TIMEOUT", "0")
    with pytest.raises(OperationalError), TestClient(app):
        pass
