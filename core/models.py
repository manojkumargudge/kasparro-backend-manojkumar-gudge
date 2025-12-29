from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from core.db import Base
import uuid


# -----------------------------
# CANONICAL COIN (NORMALIZED)
# -----------------------------
class Coin(Base):
    __tablename__ = "coins"

    id = Column(Integer, primary_key=True, index=True)
    canonical_id = Column(String, unique=True, index=True)
    symbol = Column(String, index=True)
    name = Column(String)
    price_usd = Column(Float, nullable=True)
    market_cap = Column(Float, nullable=True)


# --------------------------------
# SOURCE → CANONICAL COIN MAPPING
# --------------------------------
class CoinSourceMapping(Base):
    __tablename__ = "coin_source_mapping"

    id = Column(Integer, primary_key=True, index=True)
    coin_id = Column(ForeignKey("coins.id"))
    source = Column(String, index=True)          # coingecko / coinpaprika / csv
    source_coin_id = Column(String, index=True)  # external ID


# -----------------------------
# RAW INGESTED DATA (UNCHANGED)
# -----------------------------
class RawCoin(Base):
    __tablename__ = "raw_coins"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, index=True)
    row_hash = Column(String, unique=True, index=True)
    raw = Column(Text)
    processed = Column(Boolean, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# -----------------------------
# INGESTION CHECKPOINTS
# -----------------------------
class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, unique=True, index=True)
    last_run = Column(DateTime(timezone=True), server_default=func.now())
    last_value = Column(String)


# ==========================================================
# NORMALIZATION HELPERS (THIS IS WHAT KASPARR0 EXPECTED)
# ==========================================================

def get_or_create_coin(db, symbol: str, name: str):
    """
    Returns canonical coin for a symbol.
    Creates one if not exists.
    """
    coin = db.query(Coin).filter(Coin.symbol == symbol).first()
    if coin:
        return coin

    coin = Coin(
        canonical_id=str(uuid.uuid4()),
        symbol=symbol,
        name=name
    )
    db.add(coin)
    db.commit()
    db.refresh(coin)
    return coin


def link_source(db, coin_id: int, source: str, source_coin_id: str):
    """
    Links external source coin to canonical coin
    """
    exists = db.query(CoinSourceMapping).filter_by(
        source=source,
        source_coin_id=source_coin_id
    ).first()

    if not exists:
        mapping = CoinSourceMapping(
            coin_id=coin_id,
            source=source,
            source_coin_id=source_coin_id
        )
        db.add(mapping)
        db.commit()
