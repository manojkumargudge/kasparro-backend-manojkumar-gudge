import os
from schemas.coin import CoinIn

def test_coinin_normalization():
    c = CoinIn(symbol=' btc ', name='Bitcoin', price_usd='100', market_cap='200')
    assert c.symbol == 'BTC'
    assert isinstance(c.price_usd, float)
    assert c.price_usd == 100.0
