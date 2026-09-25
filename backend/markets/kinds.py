"""Kinds of markets that need special handling.

Price markets ("Will Bitcoin be above $84,000 on September 24?", "Will ETH reach $2,800 this
week?", "Will gold hit $3,000?") are decided by the price of an asset at a moment. Jev reads the
news, not the live price and its volatility, while the market price already reflects both: its
estimates there are uninformed and a large "edge" is almost always Jev being wrong.
"""
import re

from backend.config import settings

ASSETS = (
    # crypto
    "bitcoin", "btc", "ethereum", "ether", "eth", "solana", "sol", "xrp", "ripple", "dogecoin", "doge",
    "cardano", "ada", "bnb", "litecoin", "ltc", "avalanche", "avax", "chainlink", "link", "polkadot", "dot",
    "shiba", "shib", "pepe", "tron", "trx", "toncoin", "ton", "sui", "hyperliquid", "hype",
    # indices and stocks
    "s&p", "s&p 500", "spx", "spy", "nasdaq", "qqq", "dow", "dow jones", "russell", "vix", "nikkei", "ftse",
    "dax", "stock", "stocks", "shares", "tesla", "tsla", "nvidia", "nvda", "apple", "aapl", "microsoft", "msft",
    "amazon", "amzn", "meta", "google", "googl", "alphabet", "netflix", "coinbase", "microstrategy", "mstr",
    # commodities, currencies, yields
    "gold", "silver", "oil", "crude", "wti", "brent", "natural gas", "copper", "eur/usd", "usd/jpy", "dollar index",
    "dxy", "treasury yield", "10-year yield",
)
_ASSET = re.compile(r"(?<![\w$])(" + "|".join(re.escape(a) for a in sorted(ASSETS, key=len, reverse=True)) + r")(?![\w])", re.I)
# A price level: $84,000 · $84k · 3,000 dollars · 4.5% (yields)
_LEVEL = re.compile(r"\$\s?\d[\d,.]*\s?[kmb]?\b|\b\d[\d,.]*\s?(k|dollars|usd)\b|\b\d+(\.\d+)?\s?%", re.I)
_THRESHOLD = re.compile(
    r"\b(above|below|over|under|between|reach(es|ed)?|hit(s)?|dip(s)?|drop(s)?|fall(s)?|close[sd]?|"
    r"trade[sd]?|price of|price be|ath|all[- ]time high|higher than|lower than|at least)\b", re.I)


def is_price_market(question: str) -> bool:
    """True for a market decided by an asset's price crossing a level (see module docstring)."""
    q = question or ""
    if not _ASSET.search(q):
        return False
    return bool(re.search(r"\bup or down\b", q, re.I) or (_LEVEL.search(q) and _THRESHOLD.search(q)))


def skip_paid_forecast(question: str) -> bool:
    """Automatic forecasts, «Assess all» and alerts skip price markets (EXCLUDE_PRICE_MARKETS): no
    bet can come out of them, so the Jev call would be wasted. A forecast asked by hand still runs."""
    return settings.EXCLUDE_PRICE_MARKETS and is_price_market(question)
