import os
import tempfile
import csv
import pytest
from core.db import engine, Base, SessionLocal
from ingestion.csvingest import ingest_csv
from ingestion.extracsvingest import ingest_extra_csv
from core.models import Coin, Checkpoint

def setup_module(module):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

def test_incremental_ingestion(tmp_path):
    # Write initial CSV
    p = tmp_path / 'inc1.csv'
    with open(p, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol','name','price_usd','market_cap'])
        w.writerow(['inc','Incremental Coin','2.5','200'])
    os.environ['CSV_PATH'] = str(p)
    ingest_csv()
    s = SessionLocal()
    assert s.query(Coin).filter_by(symbol='INC').count() == 1
    s.close()
    # Write new CSV with same + new coin
    with open(p, 'a', newline='') as f:
        w = csv.writer(f)
        w.writerow(['inc','Incremental Coin','2.5','200'])
        w.writerow(['inc2','Incremental Coin 2','3.5','300'])
    ingest_csv()
    s = SessionLocal()
    # Should not duplicate 'INC', only add 'INC2'
    assert s.query(Coin).filter_by(symbol='INC').count() == 1
    assert s.query(Coin).filter_by(symbol='INC2').count() == 1
    s.close()

def test_etl_failure_handling(tmp_path):
    # Write CSV with a bad row (missing symbol)
    p = tmp_path / 'fail.csv'
    with open(p, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol','name','price_usd','market_cap'])
        w.writerow(['','NoSymbol','1.0','10'])
        w.writerow(['fail','Fail Coin','badfloat','badfloat'])
    os.environ['CSV_PATH'] = str(p)
    # Should not raise, should skip bad rows
    ingest_csv()
    s = SessionLocal()
    # Only 'FAIL' should be present, but price/market_cap should be 0.0
    c = s.query(Coin).filter_by(symbol='FAIL').first()
    assert c is not None
    assert c.price_usd == 0.0
    assert c.market_cap == 0.0
    s.close()

def test_schema_mismatch(tmp_path):
    # Write CSV with extra column
    p = tmp_path / 'mismatch.csv'
    with open(p, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol','name','price_usd','market_cap','extra'])
        w.writerow(['mis','Mismatch Coin','1.1','11','unexpected'])
    os.environ['CSV_PATH'] = str(p)
    ingest_csv()
    s = SessionLocal()
    c = s.query(Coin).filter_by(symbol='MIS').first()
    assert c is not None
    s.close()
