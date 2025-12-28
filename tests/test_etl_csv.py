import os
import tempfile
import csv

from core.db import engine, Base, SessionLocal
from ingestion.csvingest import ingest_csv
from core.models import Coin


def setup_module(module):
    Base.metadata.create_all(bind=engine)


def test_ingest_csv(tmp_path):
    # write a small CSV
    p = tmp_path / 'sample.csv'
    with open(p, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol','name','price_usd','market_cap'])
        w.writerow(['tst','Test Coin','1.5','100'])

    os.environ['CSV_PATH'] = str(p)
    ingest_csv()

    s = SessionLocal()
    coins = s.query(Coin).filter_by(symbol='TST').all()
    s.close()
    assert len(coins) == 1
