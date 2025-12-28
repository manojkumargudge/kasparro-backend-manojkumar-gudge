from fastapi.testclient import TestClient
import os
from core.db import engine, Base, SessionLocal
from core.models import Coin

# Mirror test environment: file-backed sqlite and testing flag
os.environ['TESTING'] = '1'
os.environ['DATABASE_URL'] = os.environ.get('DATABASE_URL', 'sqlite+pysqlite:////tmp/test_db.sqlite3')

from main import app

# Ensure DB schema and seed a coin before creating TestClient
Base.metadata.create_all(bind=engine)
s = SessionLocal()
s.add(Coin(symbol='FOO', name='Foo Coin', price_usd=1.23, market_cap=1000))
s.commit()
s.close()

client = TestClient(app)
r = client.get('/data')
print('status', r.status_code)
print('text', r.text)
