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
    client = TestClient(app)


def test_get_data():
    r = client.get('/data')
    assert r.status_code == 200
    body = r.json()
    assert 'request_id' in body
    assert body['total'] >= 1
