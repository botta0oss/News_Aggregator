"""Read-only client for Polymarket's public Gamma API (market metadata and prices).

No wallet, signing or order placement happens here: the output is a list of binary
markets with the current implied probability of YES.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
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
    # Trading data (CLOB): token ids of the YES/NO shares, top of book, fees, minimum order
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    taker_fee_bps: Optional[float] = None
    order_min_size: Optional[float] = None
    start_date: Optional[datetime] = None
    closed_time: Optional[datetime] = None   # when trading actually stopped (can be before end_date)
    category: Optional[str] = None           # Polymarket's own category/tag, when present
    event_id: Optional[str] = None
    neg_risk: bool = False                   # outcome of a group of mutually exclusive markets
    group_title: Optional[str] = None        # the outcome's name inside its event (e.g. a candidate)

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

    token_ids = [str(t) for t in _json_list(raw.get("clobTokenIds"))]
    best_bid = _float(raw.get("bestBid"), default=-1.0)
    best_ask = _float(raw.get("bestAsk"), default=-1.0)
    fee = raw.get("takerBaseFee")

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
        yes_token_id=token_ids[0] if len(token_ids) == 2 else None,
        no_token_id=token_ids[1] if len(token_ids) == 2 else None,
        best_bid=best_bid if 0.0 <= best_bid <= 1.0 else None,
        best_ask=best_ask if 0.0 < best_ask <= 1.0 else None,
        taker_fee_bps=_float(fee) if fee is not None else None,
        order_min_size=_float(raw.get("orderMinSize")) or None,
        start_date=_datetime(raw.get("startDate") or raw.get("createdAt")),
        closed_time=_datetime(raw.get("closedTime")),
        category=raw.get("category") or None,
        event_id=str(events[0]["id"]) if events and isinstance(events[0], dict) and events[0].get("id") else None,
        neg_risk=bool(raw.get("negRisk")),
        group_title=(raw.get("groupItemTitle") or "").strip() or None,
    )


def is_multi_outcome(m: "PolymarketMarket") -> bool:
    """Outcome of an event with several mutually exclusive answers (shown under "Più esiti")."""
    return m.neg_risk and bool(m.group_title)


@dataclass
class PolymarketEvent:
    id: str
    title: str
    slug: Optional[str]
    description: Optional[str]
    end_date: Optional[datetime]
    volume: float
    liquidity: float
    closed: bool
    outcomes: list  # PolymarketMarket, one per outcome

    @property
    def url(self) -> Optional[str]:
        return f"https://polymarket.com/event/{self.slug}" if self.slug else None

    @property
    def winner_id(self) -> Optional[str]:
        winners = [o.id for o in self.outcomes if o.resolved_yes]
        return winners[0] if len(winners) == 1 else None


def parse_event(raw: dict) -> Optional[PolymarketEvent]:
    """A multi-outcome event: mutually exclusive (negRisk) and with at least 3 outcomes."""
    if not raw.get("id") or not raw.get("title") or not raw.get("negRisk"):
        return None
    outcomes = []
    for m in raw.get("markets") or []:
        if not isinstance(m, dict):
            continue
        parsed = parse_market({**m, "events": [{"id": raw["id"], "slug": raw.get("slug")}], "negRisk": True})
        if parsed and parsed.group_title and parsed.yes_price is not None:
            outcomes.append(parsed)
    if len(outcomes) < 3:
        return None
    return PolymarketEvent(
        id=str(raw["id"]), title=str(raw["title"]).strip(), slug=raw.get("slug"), description=raw.get("description"),
        end_date=_datetime(raw.get("endDate")), volume=_float(raw.get("volume")), liquidity=_float(raw.get("liquidity")),
        closed=bool(raw.get("closed")), outcomes=outcomes,
    )


async def fetch_multi_events(limit: Optional[int] = None, min_volume: Optional[float] = None,
                             client: Optional[httpx.AsyncClient] = None) -> list[PolymarketEvent]:
    """Open multi-outcome events, most traded first."""
    limit = limit if limit is not None else settings.MULTI_SYNC_LIMIT
    min_volume = min_volume if min_volume is not None else settings.POLYMARKET_MIN_VOLUME
    own_client = client is None
    client = client or _client()
    events: list[PolymarketEvent] = []
    try:
        offset = 0
        for _ in range(20):
            res = await client.get("/events", params={
                "active": "true", "closed": "false", "order": "volume24hr", "ascending": "false",
                "limit": PAGE_SIZE, "offset": offset,
            })
            res.raise_for_status()
            page = res.json()
            if not isinstance(page, list) or not page:
                break
            for raw in page:
                event = parse_event(raw) if isinstance(raw, dict) else None
                if event and event.volume >= min_volume:
                    events.append(event)
            if len(events) >= limit or len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    finally:
        if own_client:
            await client.aclose()
    return events[:limit]


async def fetch_resolved_events(resolved_after: datetime, resolved_before: datetime, limit: int = 50,
                                min_volume: float = 50_000.0, client: Optional[httpx.AsyncClient] = None) -> list[PolymarketEvent]:
    """Closed multi-outcome events with a single winner, most traded first (for backtests)."""
    own_client = client is None
    client = client or _client()
    events: list[PolymarketEvent] = []
    try:
        offset = 0
        for _ in range(20):
            res = await client.get("/events", params={
                "closed": "true", "order": "volume", "ascending": "false",
                "end_date_min": resolved_after.date().isoformat(), "end_date_max": resolved_before.date().isoformat(),
                "limit": PAGE_SIZE, "offset": offset,
            })
            res.raise_for_status()
            page = res.json()
            if not isinstance(page, list) or not page:
                break
            for raw in page:
                event = parse_event(raw) if isinstance(raw, dict) else None
                if event and event.winner_id and event.volume >= min_volume and event.end_date:
                    events.append(event)
            if len(events) >= limit or len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    finally:
        if own_client:
            await client.aclose()
    return events[:limit]


async def fetch_event(event_id: str, client: Optional[httpx.AsyncClient] = None) -> Optional[PolymarketEvent]:
    own_client = client is None
    client = client or _client()
    try:
        res = await client.get(f"/events/{event_id}")
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return parse_event(res.json())
    finally:
        if own_client:
            await client.aclose()


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


async def fetch_resolved_markets(
    resolved_after: datetime,
    resolved_before: datetime,
    limit: int = 100,
    min_volume: float = 10000.0,
    client: Optional[httpx.AsyncClient] = None,
) -> list[PolymarketMarket]:
    """Closed binary markets with a clear YES/NO outcome, most traded first (for backtests)."""
    own_client = client is None
    client = client or _client()
    markets: list[PolymarketMarket] = []
    try:
        offset = 0
        for _ in range(40):  # at most 40 pages
            res = await client.get("/markets", params={
                "closed": "true",
                "order": "volumeNum",
                "ascending": "false",
                "end_date_min": resolved_after.date().isoformat(),
                "end_date_max": resolved_before.date().isoformat(),
                "volume_num_min": min_volume,
                "limit": PAGE_SIZE,
                "offset": offset,
            })
            res.raise_for_status()
            page = res.json()
            if not isinstance(page, list) or not page:
                break
            for raw in page:
                market = parse_market(raw)
                if (market and market.resolved_yes is not None and market.volume >= min_volume
                        and market.yes_token_id and market.end_date is not None):
                    markets.append(market)
            if len(markets) >= limit or len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    finally:
        if own_client:
            await client.aclose()
    return markets[:limit]


async def fetch_price_history(token_id: str, start: datetime, end: datetime,
                              client: Optional[httpx.AsyncClient] = None) -> list[tuple[datetime, float]]:
    """Price points (time, price) of a share between two dates, from the CLOB (hourly)."""
    own_client = client is None
    client = client or httpx.AsyncClient(base_url=settings.POLYMARKET_CLOB_URL, timeout=20.0)
    try:
        res = await client.get("/prices-history", params={
            "market": token_id, "startTs": int(start.timestamp()), "endTs": int(end.timestamp()), "fidelity": 60,
        })
        res.raise_for_status()
        points = []
        for point in (res.json() or {}).get("history") or []:
            t, p = point.get("t"), _float(point.get("p"), -1.0)
            if isinstance(t, (int, float)) and 0.0 <= p <= 1.0:
                points.append((datetime.fromtimestamp(t, tz=timezone.utc), p))
        return sorted(points)
    finally:
        if own_client:
            await client.aclose()


def price_at(history: list[tuple[datetime, float]], when: datetime, max_gap_hours: float = 48.0) -> Optional[float]:
    """Last price at or before `when`, if not older than `max_gap_hours`."""
    before = [(t, p) for t, p in history if t <= when]
    if not before:
        return None
    t, p = before[-1]
    return p if (when - t).total_seconds() <= max_gap_hours * 3600 else None


# ---------------------------------------------------------------------------
# Order book (CLOB, public read-only endpoint)
# ---------------------------------------------------------------------------

@dataclass
class OrderBook:
    """Asks sorted from cheapest, bids from highest; each level is (price, size in shares)."""
    asks: list
    bids: list

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None


def parse_order_book(raw: dict) -> OrderBook:
    def levels(key):
        out = []
        for level in raw.get(key) or []:
            price, size = _float(level.get("price"), -1.0), _float(level.get("size"), 0.0)
            if 0.0 < price < 1.0 and size > 0:
                out.append((price, size))
        return out
    return OrderBook(asks=sorted(levels("asks")), bids=sorted(levels("bids"), reverse=True))


async def fetch_order_book(token_id: str, client: Optional[httpx.AsyncClient] = None) -> OrderBook:
    own_client = client is None
    client = client or httpx.AsyncClient(base_url=settings.POLYMARKET_CLOB_URL, timeout=15.0)
    try:
        res = await client.get("/book", params={"token_id": token_id})
        res.raise_for_status()
        return parse_order_book(res.json())
    finally:
        if own_client:
            await client.aclose()
