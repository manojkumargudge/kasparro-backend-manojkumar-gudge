import csv
from core.db import SessionLocal
from core.models import get_or_create_coin, link_source


CSV_FILE = "data/coins.csv"   # keep your existing path if different


def ingest_csv():
    db = SessionLocal()
    try:
        linked = 0

        with open(CSV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                symbol = (row.get("symbol") or "").strip().upper()
                name = row.get("name")
                source_coin_id = row.get("id") or row.get("symbol")

                if not symbol:
                    continue

                # 🔹 NORMALIZATION
                coin = get_or_create_coin(
                    db=db,
                    symbol=symbol,
                    name=name
                )

                link_source(
                    db=db,
                    coin_id=coin.id,
                    source="csv",
                    source_coin_id=source_coin_id
                )

                linked += 1

        db.commit()
        print(f"CSV ingestion complete ({linked} coins linked)")

    except Exception as e:
        db.rollback()
        print("ERROR:", e)

    finally:
        db.close()


if __name__ == "__main__":
    ingest_csv()
