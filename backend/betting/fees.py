"""Polymarket taker fees.

Documented formula (2026): fee = shares × rate × p × (1 − p), highest at 50¢ and zero at the
extremes; makers pay nothing. The rate depends on the market category. The CLOB endpoint
`/fee-rate?token_id=` says whether a market charges fees at all (`base_fee` 0 = no fees), but
the value it returns does not match the documented rates, so it is used only as an on/off
switch and the rate comes from the category.
"""
import logging
import time
from typing import Optional
import httpx
from backend.config import settings

logger = logging.getLogger(__name__)

# Taker fee rate by category: our news categories and Polymarket's own category names
CATEGORY_RATES = {
    "foreign affairs": 0.0, "geopolitics": 0.0,
    "politics": 0.04, "technology": 0.04, "tech": 0.04, "economy": 0.04, "finance": 0.04,
    "economics": 0.05, "culture": 0.05, "pop culture": 0.05, "science": 0.05, "other": 0.05,
    "sports": 0.03,
    "crypto": 0.07,
}
CACHE_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, bool]] = {}


def category_rate(category: Optional[str]) -> float:
    """Fee rate (fraction) for a category; unknown categories use DEFAULT_FEE_BPS."""
    if category and category.strip().lower() in CATEGORY_RATES:
        return CATEGORY_RATES[category.strip().lower()]
    return settings.DEFAULT_FEE_BPS / 10_000


def fee_per_share(price: float, fee_bps: float) -> float:
    """Taker fee paid per share bought at `price`: rate × p × (1 − p)."""
    p = min(1.0, max(0.0, price))
    return (fee_bps / 10_000) * p * (1.0 - p)


def parse_fee_enabled(raw) -> Optional[bool]:
    """`{"base_fee": 0}` → False, any positive value → True; None when the answer is unreadable."""
    if not isinstance(raw, dict):
        return None
    for key in ("base_fee", "fee_rate_bps", "feeRateBps", "fee_rate"):
        if key in raw:
            try:
                return float(raw[key]) > 0
            except (TypeError, ValueError):
                return None
    return None


async def fees_enabled(token_id: Optional[str], client: Optional[httpx.AsyncClient] = None) -> Optional[bool]:
    """Whether the market of this share charges taker fees (cached); None when unknown."""
    if not token_id:
        return None
    hit = _cache.get(token_id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    own_client = client is None
    client = client or httpx.AsyncClient(base_url=settings.POLYMARKET_CLOB_URL, timeout=10.0)
    try:
        res = await client.get("/fee-rate", params={"token_id": token_id})
        res.raise_for_status()
        enabled = parse_fee_enabled(res.json())
    except Exception as e:
        logger.info(f"Fee rate unavailable for token {token_id}: {e}")
        return None
    finally:
        if own_client:
            await client.aclose()
    if enabled is not None:
        _cache[token_id] = (time.monotonic(), enabled)
    return enabled


async def market_fee_bps(token_id: Optional[str], category: Optional[str]) -> float:
    """Taker fee rate in basis points for a market: 0 when the CLOB says it is fee-free,
    otherwise the category's rate (also when the CLOB cannot be reached, to stay prudent)."""
    if await fees_enabled(token_id) is False:
        return 0.0
    return category_rate(category) * 10_000
