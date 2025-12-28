from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime
from sqlalchemy.sql import func
from core.db import Base


class Coin(Base):
    __tablename__ = "coins"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, unique=True, index=True)
    name = Column(String)
    price_usd = Column(Float)
    market_cap = Column(Float)


class RawCoin(Base):
    __tablename__ = "raw_coins"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, index=True)
    row_hash = Column(String, unique=True, index=True)
    raw = Column(Text)
    processed = Column(Boolean, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, unique=True, index=True)
    last_run = Column(DateTime(timezone=True), server_default=func.now())
    last_value = Column(String)
