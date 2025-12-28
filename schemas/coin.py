from pydantic import BaseModel, Field, validator
from typing import Optional

class CoinIn(BaseModel):
    symbol: str = Field(..., min_length=1)
    name: Optional[str]
    price_usd: float = 0.0
    market_cap: float = 0.0

    @validator('symbol')
    def symbol_upper(cls, v):
        return v.strip().upper()

    @validator('price_usd', 'market_cap', pre=True)
    def to_float(cls, v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0
