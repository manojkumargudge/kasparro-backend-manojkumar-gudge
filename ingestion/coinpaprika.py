import requests
from core.db import SessionLocal
from core.models import Coin

URL = "https://api.coinpaprika.com/v1/tickers"

def ingest_coins():
    db = SessionLocal()
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        for item in data[:50]:  # limit to 50 coins
            coin = Coin(
                symbol=item["symbol"],
                name=item["name"],
                price_usd=item["quotes"]["USD"]["price"],
                market_cap=item["quotes"]["USD"]["market_cap"]
            )
            db.add(coin)

        db.commit()
        print("COIN DATA INGESTED")

    except Exception as e:
        db.rollback()
        print("ERROR:", e)
    finally:
        db.close()

if __name__ == "__main__":
    ingest_coins()
