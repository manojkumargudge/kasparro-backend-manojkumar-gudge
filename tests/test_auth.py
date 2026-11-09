"""Multiple API keys, each with its own rate limit and scopes."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.app import app
from api.deps import admin_client, read_client
from core.models import ApiRateLimit
from core.security import ADMIN, READ, configured_clients, key_fingerprint, match_client
from tests.conftest import API_KEY

DEMO = "demo-key-abc123"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_DEMO_API_KEY", DEMO)
    return TestClient(app)


def test_registry_parses_owner_demo_and_extra_keys(monkeypatch):
    monkeypatch.setenv("APP_DEMO_API_KEY", DEMO)
    monkeypatch.setenv("DEMO_API_RATE_LIMIT", "20")
    monkeypatch.setenv("APP_API_KEYS", "plainkey1, ci:cikey9:5:read+admin , bad::x, ops:opskey7:7")
    clients = {c.label: c for c in configured_clients()}

    assert set(clients) == {"owner", "demo", "key1", "ci", "ops"}  # malformed entry skipped
    assert (clients["owner"].limit, clients["owner"].scopes) == (60, frozenset({READ, ADMIN}))
    assert (clients["demo"].limit, clients["demo"].scopes) == (20, frozenset({READ}))
    assert (clients["key1"].limit, clients["key1"].scopes) == (60, frozenset({READ}))
    assert (clients["ci"].limit, clients["ci"].scopes) == (5, frozenset({READ, ADMIN}))
    assert clients["ops"].scopes == frozenset({READ})
    assert DEMO not in repr(clients["demo"])  # keys never appear in repr/logs


def test_malformed_entry_warning_does_not_leak_key(monkeypatch, caplog):
    monkeypatch.setenv("APP_API_KEYS", "label:secretvalue99:notanumber")
    configured_clients()
    assert "malformed" in caplog.text and "secretvalue99" not in caplog.text


def test_match_client(monkeypatch):
    monkeypatch.setenv("APP_DEMO_API_KEY", DEMO)
    assert match_client(DEMO).label == "demo"
    assert match_client(API_KEY).label == "owner"
    assert match_client("nope") is None and match_client(None) is None


def test_demo_key_has_its_own_lower_limit(client, monkeypatch, db):
    monkeypatch.setenv("DEMO_API_RATE_LIMIT", "2")
    demo = {"X-API-KEY": DEMO}
    codes = [client.get("/runs", headers=demo).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    first = client.get("/runs", headers={"X-API-KEY": API_KEY})
    assert first.status_code == 200 and first.headers["X-RateLimit-Limit"] == "60"  # owner unaffected
    counts = dict(db.execute(select(ApiRateLimit.key_hash, ApiRateLimit.count)).all())
    assert counts == {key_fingerprint(DEMO): 3, key_fingerprint(API_KEY): 1}


def test_default_limits_are_20_for_demo_and_60_for_owner(client):
    assert client.get("/stats", headers={"X-API-KEY": DEMO}).headers["X-RateLimit-Limit"] == "20"
    assert client.get("/stats", headers={"X-API-KEY": API_KEY}).headers["X-RateLimit-Limit"] == "60"


def test_extra_key_with_custom_limit(client, monkeypatch):
    monkeypatch.setenv("APP_API_KEYS", "ci:cikey9:1:read")
    assert client.get("/data", headers={"X-API-KEY": "cikey9"}).status_code == 200
    assert client.get("/data", headers={"X-API-KEY": "cikey9"}).status_code == 429


@pytest.mark.parametrize("path", ["/data", "/stats", "/runs", "/compare-runs", "/metrics"])
def test_demo_key_can_read_everything(client, path):
    assert client.get(path, headers={"X-API-KEY": DEMO}).status_code == 200


def test_scopes_enforced_read_only_demo_cannot_use_admin_routes(monkeypatch):
    monkeypatch.setenv("APP_DEMO_API_KEY", DEMO)
    mini = FastAPI()

    @mini.post("/admin-thing", dependencies=[Depends(admin_client)])
    def admin_thing() -> dict[str, bool]:
        return {"ok": True}

    @mini.get("/read-thing", dependencies=[Depends(read_client)])
    def read_thing() -> dict[str, bool]:
        return {"ok": True}

    c = TestClient(mini)
    assert c.post("/admin-thing", headers={"X-API-KEY": DEMO}).status_code == 403
    assert c.post("/admin-thing", headers={"X-API-KEY": API_KEY}).status_code == 200
    assert c.get("/read-thing", headers={"X-API-KEY": DEMO}).status_code == 200
    assert c.post("/admin-thing").status_code == 401


def test_no_keys_configured_is_500(client, monkeypatch):
    monkeypatch.delenv("APP_API_KEY")
    monkeypatch.delenv("APP_DEMO_API_KEY")
    assert client.get("/data", headers={"X-API-KEY": "anything"}).status_code == 500
