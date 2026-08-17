import os
import tempfile
import json
from fastapi.testclient import TestClient

from core.db import engine, Base, SessionLocal
from main import app
from core.models import Coin, Checkpoint

# Create TestClient after DB tables are created to avoid startup tasks
client = None


def setup_module(module):
    # ensure a clean DB for this test run
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # insert one coin
    s = SessionLocal()
    s.add(Coin(symbol='FOO', name='Foo Coin', price_usd=1.23, market_cap=1000))
    s.add(
        Checkpoint(
            source='csv',
            last_value=json.dumps(
                {
                    'linked': 1,
                    'errors': 0,
                    'run_meta': {
                        'start_time': '2026-07-01T00:00:00',
                        'end_time': '2026-07-01T00:00:01',
                        'duration_seconds': 1.0,
                        'linked': 1,
                        'errors': 0,
                        'status': 'success',
                    },
                }
            ),
        )
    )
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
    assert 'etl' in body
    assert body['etl']['records_processed'] >= 1
    assert body['etl']['last_success_at'] is not None

def test_stats():
    r = client.get('/stats', headers={"X-API-KEY": os.environ.get('APP_API_KEY')})
    assert r.status_code == 200
    body = r.json()
    assert 'total_records' in body
    assert 'price_stats' in body
    assert 'etl' in body
    assert body['etl']['records_processed'] >= 1


def test_unauthorized():
    # missing API key -> 401 for protected endpoint
    r = client.get('/data')
    assert r.status_code == 401


def test_non_health_routes_require_api_key():
    protected_paths = ['/data', '/stats', '/metrics', '/runs', '/compare-runs']
    for path in protected_paths:
        r = client.get(path)
        assert r.status_code == 401, f'{path} should require an API key'

    dashboard = client.get('/dashboard')
    assert dashboard.status_code == 200
