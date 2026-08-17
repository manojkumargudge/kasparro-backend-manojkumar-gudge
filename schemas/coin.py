from pydantic import BaseModel, Field, field_validator
from typing import Optional

class CoinIn(BaseModel):
    symbol: str = Field(..., min_length=1)
    name: Optional[str]
    price_usd: float = 0.0
    market_cap: float = 0.0

    @field_validator('symbol')
    @classmethod
    def symbol_upper(cls, v):
        return v.strip().upper()

    @field_validator('price_usd', 'market_cap', mode='before')
    @classmethod
    def to_float(cls, v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0
