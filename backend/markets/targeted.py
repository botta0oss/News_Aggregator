"""Targeted news search per market (Google News RSS).

General feeds rarely cover niche markets (a specific court case, a minor election, a
product launch). For the most traded markets the key terms of the question are searched
on Google News; the results are stored as articles of an automatic source and go through
the normal pipeline: deduplication, classification, matching and ranking.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.models import Market, Source
from backend.ingestor.fetcher import fetch_feed
from backend.markets.matching import extract_terms

logger = logging.getLogger(__name__)

SOURCE_NAME = "Ricerca mirata (Google News)"
MAX_TERMS = 6
MAX_CONSECUTIVE_ERRORS = 3


def build_query(question: str) -> str:
    """Names first, then distinctive words and numbers; years and months are left out."""
    terms = sorted(extract_terms(question), key=lambda t: (-t.weight, not t.entity))
    parts = []
    for term in terms:
        if term.weight < 1:
            continue
        text = term.exact[0] if term.exact else term.text
        parts.append(f'"{text}"' if " " in text else text)
    return " ".join(parts[:MAX_TERMS])


def search_url(query: str) -> str:
    q = quote_plus(f"{query} when:{settings.TARGETED_NEWS_DAYS}d")
    return f"{settings.TARGETED_NEWS_URL}?q={q}&{settings.TARGETED_NEWS_LOCALE}"


async def get_source(session: AsyncSession) -> Source:
    source = (await session.execute(select(Source).where(Source.kind == "targeted"))).scalars().first()
    if source is None:
        source = Source(name=SOURCE_NAME, url=settings.TARGETED_NEWS_URL, kind="targeted", active=True)
        session.add(source)
        await session.commit()
    return source


def _clean_entry(entry: dict) -> dict:
    # Google News descriptions only repeat the headline and the outlet: not real content
    content = (entry.get("content_raw") or "").strip()
    headline = entry["title"].rsplit(" - ", 1)[0].strip().lower()
    if not content or content.lower().startswith(headline[:40]):
        content = None
    return {**entry, "content_raw": content}


async def search_market(session: AsyncSession, market: Market) -> int:
    """Searches news for one market; returns how many new articles were stored."""
    from backend.ingestor.scheduler import store_entries  # the scheduler imports the market service

    query = build_query(market.question)
    added = 0
    if query:
        result = await fetch_feed(search_url(query))
        since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
        entries = [_clean_entry(e) for e in result.entries
                   if e.get("published_at") is None or e["published_at"] >= since][:settings.TARGETED_NEWS_MAX_RESULTS]
        source = await get_source(session)
        added = len(await store_entries(session, source.id, entries, publisher_in_title=True))
        logger.info(f"Targeted search {market.id} [{query}]: {len(entries)} results, {added} new")
    market.targeted_at = datetime.now(timezone.utc)
    await session.commit()
    return added


async def markets_to_search(session: AsyncSession, limit: int, market_ids: Optional[list[str]] = None) -> list[Market]:
    stale = datetime.now(timezone.utc) - timedelta(hours=settings.TARGETED_NEWS_REFRESH_HOURS)
    stmt = (
        select(Market)
        .where(Market.closed == False)  # noqa: E712
        .where((Market.targeted_at.is_(None)) | (Market.targeted_at < stale))
        .order_by(Market.volume.desc().nulls_last(), Market.id)
        .limit(limit)
    )
    if market_ids is not None:
        stmt = stmt.where(Market.id.in_(market_ids))
    return list((await session.execute(stmt)).scalars().all())


async def run_targeted_search(session: AsyncSession, limit: Optional[int] = None,
                              market_ids: Optional[list[str]] = None) -> dict:
    """Searches the most traded markets not searched recently. Stops if the service keeps failing."""
    stats = {"searched": 0, "added": 0, "errors": 0}
    if not settings.TARGETED_NEWS_ENABLED:
        return stats
    consecutive = 0
    ids = [m.id for m in await markets_to_search(session, limit or settings.TARGETED_NEWS_MAX_MARKETS, market_ids)]
    for market_id in ids:
        market = await session.get(Market, market_id, populate_existing=True)  # a rollback expires objects
        if market is None:
            continue
        try:
            stats["added"] += await search_market(session, market)
            stats["searched"] += 1
            consecutive = 0
        except Exception as e:
            await session.rollback()
            stats["errors"] += 1
            consecutive += 1
            logger.warning(f"Targeted search failed for market {market_id}: {e}")
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                logger.warning("Targeted search paused: the search service keeps failing")
                break
    return stats
