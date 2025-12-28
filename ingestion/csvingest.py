import csv
import json
import hashlib
import os
from sqlalchemy import select
from core.db import SessionLocal
from core.models import RawCoin, Coin
from schemas.coin import CoinIn
from core.models import Checkpoint
from sqlalchemy import select, update
import datetime, json as _json



def _row_hash(row: dict) -> str:
    raw = json.dumps(row, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ingest_csv():
    CSV_PATH = os.getenv("CSV_PATH", "data/sample_coins.csv")
    db = SessionLocal()
    try:
        with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
                delimiter = dialect.delimiter
            except Exception:
                delimiter = ","

            try:
                has_header = csv.Sniffer().has_header(sample)
            except Exception:
                has_header = False

            if has_header:
                reader = csv.DictReader(f, delimiter=delimiter)
                if reader.fieldnames:
                    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
            else:
                expected = ["symbol", "name", "price_usd", "market_cap"]
                reader = csv.DictReader(f, fieldnames=expected, delimiter=delimiter)

            row_count = 0
            new_raw = 0
            new_norm = 0
            for row in reader:
                row_count += 1
                # clean row keys and values
                row = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                rh = _row_hash(row)

                # insert raw row if not exists (idempotent)
                exists = db.execute(select(RawCoin).filter_by(row_hash=rh)).scalar_one_or_none()
                if not exists:
                    raw = RawCoin(source="csv", row_hash=rh, raw=json.dumps(row, ensure_ascii=False))
                    db.add(raw)
                    db.commit()
                    new_raw += 1
                else:
                    # already seen
                    pass

                # validate and normalize; insert into coins if valid and symbol not exists
                try:
                    coin_in = CoinIn(**row)
                    # avoid duplicate coins by symbol
                    sym = coin_in.symbol
                    existing = db.execute(select(Coin).filter_by(symbol=sym)).scalar_one_or_none()
                    if not existing:
                        coin = Coin(symbol=coin_in.symbol, name=coin_in.name, price_usd=coin_in.price_usd, market_cap=coin_in.market_cap)
                        db.add(coin)
                        db.commit()
                        new_norm += 1
                except Exception as e:
                    # validation failed — keep raw for debugging
                    print(f"Skipping normalization for row {row_count}: {e}")

            # update checkpoint
            meta = _json.dumps({"processed_rows": row_count, "new_raw": new_raw, "new_normalized": new_norm})
            cp = db.execute(select(Checkpoint).filter_by(source="csv")).scalar_one_or_none()
            if cp:
                db.execute(update(Checkpoint).where(Checkpoint.id == cp.id).values(last_run=datetime.datetime.utcnow(), last_value=meta))
            else:
                db.add(Checkpoint(source="csv", last_value=meta))
            db.commit()

            print(f"CSV rows processed={row_count} new_raw={new_raw} new_normalized={new_norm}")
    finally:
        db.close()


if __name__ == "__main__":
    ingest_csv()

