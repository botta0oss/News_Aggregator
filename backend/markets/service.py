"""Polymarket sync, news <-> market matching and Jev forecasting."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import select, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev
from backend.config import settings
from backend.db.models import Article, Market, MarketArticleLink, MarketPrediction, ProcessedArticle, Source
from backend.ingestor.deduplicator import get_title_embedding
from backend.markets import polymarket
from backend.markets.forecast import compute_signal

logger = logging.getLogger(__name__)

MAX_RESOLUTION_CHECKS = 50
# Scheduled runs and POST /markets/sync must not upsert the same markets concurrently
_market_lock = asyncio.Lock()

IMPACT_CRITERIA = {
    "raises_yes": "The news makes a YES resolution more likely",
    "lowers_yes": "The news makes a YES resolution less likely",
    "neutral": "The news has no material effect on the outcome",
}

EVIDENCE_CRITERIA = [
    "No news item is relevant to the outcome of this market",
    "News items are only tangentially related; the outcome is essentially unaffected",
    "News items give relevant context but no decisive development",
    "News items report significant developments that clearly shift the likelihood of the outcome",
    "News items report decisive or near-decisive information about the outcome",
]


def _apply_market(market: Market, data: polymarket.PolymarketMarket) -> None:
    market.question = data.question
    market.slug = data.slug
    market.event_slug = data.event_slug
    market.description = data.description
    market.end_date = data.end_date
    if data.yes_price is not None:
        market.yes_price = data.yes_price
    market.volume = data.volume
    market.liquidity = data.liquidity
    market.active = data.active
    market.closed = data.closed
    market.resolved_yes = data.resolved_yes
    market.updated_at = datetime.now(timezone.utc)


async def sync_markets(session: AsyncSession, client=None) -> dict:
    """Upserts the most traded open binary markets and detects resolution of tracked ones."""
    fetched = await polymarket.fetch_active_markets(client=client)
    seen_ids = set()
    created = 0
    for data in fetched:
        seen_ids.add(data.id)
        market = await session.get(Market, data.id)
        if market is None:
            market = Market(id=data.id, question=data.question)
            session.add(market)
            created += 1
        question_changed = market.question_embedding is None or market.question != data.question
        _apply_market(market, data)
        if question_changed:
            market.question_embedding = await asyncio.to_thread(get_title_embedding, data.question)
    await session.commit()

    # Tracked markets no longer in the active list: check whether they closed/resolved
    stmt = select(Market).where(Market.resolved_yes.is_(None))
    if seen_ids:
        stmt = stmt.where(Market.id.not_in(seen_ids))
    stmt = stmt.order_by(Market.updated_at.asc()).limit(MAX_RESOLUTION_CHECKS)
    stale = (await session.execute(stmt)).scalars().all()
    resolved = 0
    for market in stale:
        try:
            data = await polymarket.fetch_market(market.id, client=client)
        except Exception as e:
            logger.warning(f"Could not refresh market {market.id}: {e}")
            continue
        if data is None:
            market.active = False
            market.updated_at = datetime.now(timezone.utc)
            continue
        _apply_market(market, data)
        if data.resolved_yes is not None:
            resolved += 1
    await session.commit()
    return {"synced": len(fetched), "created": created, "checked": len(stale), "resolved": resolved}


async def refresh_links(session: AsyncSession) -> int:
    """Links each open market to semantically similar recent articles (pgvector cosine distance)."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    max_distance = 1.0 - settings.MARKET_MATCH_THRESHOLD
    markets = (await session.execute(
        select(Market).where(Market.closed == False, Market.question_embedding.is_not(None))  # noqa: E712
    )).scalars().all()

    inserted = 0
    for market in markets:
        distance = Article.title_embedding.cosine_distance(market.question_embedding)
        rows = (await session.execute(
            select(Article.id, distance.label("distance"))
            .where(Article.fetched_at >= since, Article.title_embedding.is_not(None), distance <= max_distance)
            .order_by(distance)
            .limit(settings.MARKET_MAX_ARTICLES * 2)
        )).all()
        if not rows:
            continue
        result = await session.execute(
            pg_insert(MarketArticleLink)
            .values([{"market_id": market.id, "article_id": aid, "similarity": round(1.0 - float(d), 4)} for aid, d in rows])
            .on_conflict_do_nothing(constraint="uq_market_article")
        )
        inserted += result.rowcount or 0
    await session.commit()
    return inserted


async def get_market_evidence(session: AsyncSession, market_id: str, limit: Optional[int] = None):
    """Returns (link, article, processed, source) rows for the most similar recent articles."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    stmt = (
        select(MarketArticleLink, Article, ProcessedArticle, Source)
        .join(Article, Article.id == MarketArticleLink.article_id)
        .join(Source, Source.id == Article.source_id)
        .outerjoin(ProcessedArticle, ProcessedArticle.article_id == Article.id)
        .where(MarketArticleLink.market_id == market_id, Article.fetched_at >= since)
        .order_by(MarketArticleLink.similarity.desc())
        .limit(limit or settings.MARKET_MAX_ARTICLES)
    )
    return (await session.execute(stmt)).all()


def build_jev_request(market: Market, evidence: list, now: Optional[datetime] = None):
    """Builds the System One state and questions for one market.

    The current market price is deliberately NOT included in the state, so that the
    Jev probability is an independent estimate that can be compared with the price.
    """
    from typesafe_sdk import Choice, Noul, Score

    now = now or datetime.now(timezone.utc)
    news = []
    for i, (link, article, processed, source) in enumerate(evidence):
        published = article.published_at or article.fetched_at
        news.append({
            "id": f"n{i}",
            "title": article.title,
            "source": source.name,
            "published_at": published.isoformat() if published else None,
            "summary": (processed.summary if processed and processed.summary else (article.content_raw or ""))[:1200],
        })

    state = {
        "today": now.date().isoformat(),
        "market": {
            "question": market.question,
            "resolution_rules": (market.description or "")[:3000],
            "end_date": market.end_date.isoformat() if market.end_date else None,
        },
        "news": news,
    }

    questions = {
        "resolves_yes": Noul(
            instructions=(
                "Considering the market's resolution rules, its end date, today's date, the news items "
                "and general world knowledge, will this market resolve YES?"
            ),
            criteria={
                "true": "The market resolves YES according to its resolution rules",
                "false": "The market resolves NO according to its resolution rules",
            },
        ),
        "evidence_strength": Score(
            instructions="How strongly do the provided news items inform the outcome of this market?",
            criteria=EVIDENCE_CRITERIA,
        ),
    }
    for item in news:
        questions[f"relevant_{item['id']}"] = Noul(
            instructions=f"Is news item {item['id']} directly relevant to the outcome of this market?"
        )
        questions[f"impact_{item['id']}"] = Choice(
            instructions=f"How does news item {item['id']} affect the probability that this market resolves YES?",
            criteria=IMPACT_CRITERIA,
        )
    return state, questions


async def predict_market(session: AsyncSession, market: Market) -> MarketPrediction:
    """Runs a Jev forecast for one market and stores it together with the betting signal."""
    if not jev.is_enabled():
        raise jev.JevUnavailableError("TYPESAFE_API_KEY is not configured")
    if market.yes_price is None:
        raise ValueError("Market has no current price")

    evidence = await get_market_evidence(session, market.id)
    if not evidence:
        raise LookupError("No related news found for this market")

    state, questions = build_jev_request(market, evidence)
    response = await jev.get_client().system_one(state=state, questions=questions)

    model_p = float(response.nouls["resolves_yes"].noul)
    evidence_strength = float(response.scores["evidence_strength"].score) / (len(EVIDENCE_CRITERIA) - 1)

    for i, (link, *_rest) in enumerate(evidence):
        relevant = response.nouls.get(f"relevant_n{i}")
        impact = response.choices.get(f"impact_n{i}")
        if relevant is not None:
            link.relevance = round(float(relevant.noul), 4)
        if impact is not None:
            link.impact = impact.choice
            link.impact_confidence = round(float(impact.confidence), 4)

    signal = compute_signal(model_p, market.yes_price, evidence_strength)
    prediction = MarketPrediction(
        market_id=market.id,
        model_name=response.model,
        market_probability=market.yes_price,
        model_probability=round(model_p, 4),
        evidence_strength=round(evidence_strength, 4),
        blended_probability=signal.blended_probability,
        edge=signal.edge,
        signal=signal.signal,
        kelly_fraction=signal.kelly_fraction,
        article_count=len(evidence),
    )
    session.add(prediction)
    await session.commit()
    logger.info(
        f"Jev forecast {market.id}: model={model_p:.3f} market={market.yes_price:.3f} "
        f"evidence={evidence_strength:.2f} -> {signal.signal} (edge {signal.edge:+.3f})"
    )
    return prediction


async def markets_needing_prediction(session: AsyncSession, limit: int) -> list[Market]:
    """Open markets with news linked after their latest prediction (or never predicted)."""
    last_pred = (
        select(MarketPrediction.market_id, func.max(MarketPrediction.created_at).label("last_at"))
        .group_by(MarketPrediction.market_id)
        .subquery()
    )
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    new_links = (
        select(MarketArticleLink.market_id)
        .join(Article, Article.id == MarketArticleLink.article_id)
        .outerjoin(last_pred, last_pred.c.market_id == MarketArticleLink.market_id)
        .where(Article.fetched_at >= since)
        .where((last_pred.c.last_at.is_(None)) | (MarketArticleLink.created_at > last_pred.c.last_at))
        .distinct()
    )
    stmt = (
        select(Market)
        .where(and_(Market.closed == False, Market.yes_price.is_not(None), Market.id.in_(new_links)))  # noqa: E712
        .order_by(Market.volume.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def run_market_pipeline(session: AsyncSession) -> dict:
    """Sync markets, link fresh news and (optionally) run Jev predictions."""
    if _market_lock.locked():
        logger.info("Market pipeline already running, skipping")
        return {"skipped": True}
    async with _market_lock:
        return await _run_market_pipeline(session)


async def _run_market_pipeline(session: AsyncSession) -> dict:
    stats = await sync_markets(session)
    stats["links"] = await refresh_links(session)
    stats["predictions"] = 0
    if settings.PREDICTION_AUTO and jev.is_enabled():
        for market in await markets_needing_prediction(session, settings.PREDICTION_MAX_PER_RUN):
            try:
                await predict_market(session, market)
                stats["predictions"] += 1
            except Exception as e:
                logger.error(f"Prediction failed for market {market.id}: {e}")
                await session.rollback()
    return stats
