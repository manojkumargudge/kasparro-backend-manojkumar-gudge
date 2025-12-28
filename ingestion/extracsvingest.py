import csv
from core.db import get_db
from core.models import Coin


def ingest_extra_csv():
    db = next(get_db())

    with open("data/extra_coins.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            coin = Coin(
                symbol=row["symbol"],
                name=row["name"],
                price_usd=float(row["price_usd"])
            )
            db.add(coin)

        db.commit()

