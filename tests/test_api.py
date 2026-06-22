import os
import tempfile
from fastapi.testclient import TestClient

from core.db import engine, Base, SessionLocal
from main import app
from core.models import Coin

# Create TestClient after DB tables are created to avoid startup tasks
client = None


def setup_module(module):
    # ensure a clean DB for this test run
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # insert one coin
    s = SessionLocal()
    s.add(Coin(symbol='FOO', name='Foo Coin', price_usd=1.23, market_cap=1000))
    s.commit()
    s.close()
    global client
    # set test API key for protected endpoints
    import os
    os.environ['APP_API_KEY'] = 'testkey'
    client = TestClient(app)


def test_get_data():
    r = client.get('/data', headers={"X-API-KEY": os.environ.get('APP_API_KEY')})
    assert r.status_code == 200
    body = r.json()
    assert 'request_id' in body
    assert 'meta' in body
    assert body['meta']['total_records'] >= 1

def test_health():
    r = client.get('/health')
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'ok'
    assert 'db' in body

def test_stats():
    r = client.get('/stats', headers={"X-API-KEY": os.environ.get('APP_API_KEY')})
    assert r.status_code == 200
    body = r.json()
    assert 'total_records' in body
    assert 'price_stats' in body


def test_unauthorized():
    # missing API key -> 401 for protected endpoint
    r = client.get('/data')
    assert r.status_code == 401
