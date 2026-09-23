"""Read-only client for Polymarket's public Gamma API (market metadata and prices).

No wallet, signing or order placement happens here: the output is a list of binary
markets with the current implied probability of YES.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
import httpx
from backend.config import settings

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


@dataclass
class PolymarketMarket:
    id: str
    question: str
    slug: Optional[str]
    event_slug: Optional[str]
    description: Optional[str]
    end_date: Optional[datetime]
    yes_price: Optional[float]
    volume: float
    liquidity: float
    active: bool
    closed: bool
    resolved_yes: Optional[bool]

    @property
    def url(self) -> Optional[str]:
        if self.event_slug:
            return f"https://polymarket.com/event/{self.event_slug}"
        if self.slug:
            return f"https://polymarket.com/market/{self.slug}"
        return None


def _json_list(value: Any) -> list:
    """Gamma encodes `outcomes` / `outcomePrices` as JSON strings (e.g. '["Yes", "No"]')."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_market(raw: dict) -> Optional[PolymarketMarket]:
    """Parses a Gamma market object. Returns None for non-binary or malformed markets."""
    outcomes = [str(o).strip().lower() for o in _json_list(raw.get("outcomes"))]
    prices = _json_list(raw.get("outcomePrices"))
    if outcomes != ["yes", "no"] or len(prices) != 2 or not raw.get("id") or not raw.get("question"):
        return None

    yes_price = _float(prices[0], default=-1.0)
    yes_price = yes_price if 0.0 <= yes_price <= 1.0 else None
    closed = bool(raw.get("closed"))

    # Once a market resolves, outcome prices settle to 1/0
    resolved_yes = None
    if closed and yes_price is not None:
        if yes_price >= 0.99:
            resolved_yes = True
        elif yes_price <= 0.01:
            resolved_yes = False

    events = raw.get("events") or []
    event_slug = events[0].get("slug") if events and isinstance(events[0], dict) else None

    return PolymarketMarket(
        id=str(raw["id"]),
        question=str(raw["question"]).strip(),
        slug=raw.get("slug"),
        event_slug=event_slug,
        description=raw.get("description"),
        end_date=_datetime(raw.get("endDate")),
        yes_price=yes_price,
        volume=_float(raw.get("volumeNum", raw.get("volume"))),
        liquidity=_float(raw.get("liquidityNum", raw.get("liquidity"))),
        active=bool(raw.get("active", True)),
        closed=closed,
        resolved_yes=resolved_yes,
    )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.POLYMARKET_GAMMA_URL, timeout=20.0)


async def fetch_active_markets(
    limit: Optional[int] = None,
    min_volume: Optional[float] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> list[PolymarketMarket]:
    """Fetches open binary markets ordered by 24h volume (most traded first)."""
    limit = limit if limit is not None else settings.POLYMARKET_SYNC_LIMIT
    min_volume = min_volume if min_volume is not None else settings.POLYMARKET_MIN_VOLUME
    own_client = client is None
    client = client or _client()
    markets: list[PolymarketMarket] = []
    try:
        offset = 0
        while len(markets) < limit:
            res = await client.get("/markets", params={
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
                "limit": PAGE_SIZE,
                "offset": offset,
            })
            res.raise_for_status()
            page = res.json()
            if not isinstance(page, list) or not page:
                break
            for raw in page:
                market = parse_market(raw)
                if market and market.yes_price is not None and market.volume >= min_volume:
                    markets.append(market)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    finally:
        if own_client:
            await client.aclose()
    return markets[:limit]


async def fetch_market(market_id: str, client: Optional[httpx.AsyncClient] = None) -> Optional[PolymarketMarket]:
    """Fetches a single market (used to detect resolution of tracked markets)."""
    own_client = client is None
    client = client or _client()
    try:
        res = await client.get(f"/markets/{market_id}")
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return parse_market(res.json())
    finally:
        if own_client:
            await client.aclose()
