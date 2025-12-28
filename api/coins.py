from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.db import SessionLocal
from core.models import Coin

router = APIRouter(prefix="/coins", tags=["coins"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/")
def list_coins(limit: int = 10, db: Session = Depends(get_db)):
    coins = (
        db.query(Coin)
        .order_by(Coin.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "symbol": c.symbol,
            "name": c.name,
            "price_usd": c.price_usd,
            "market_cap": c.market_cap,
            "created_at": c.created_at,
        }
        for c in coins
    ]
