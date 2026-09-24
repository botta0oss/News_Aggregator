"""Polymarket sync, news <-> market matching and Jev forecasting."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import select, func, and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev
from backend.ai.ratelimit import RateLimited
from backend.ai.usage import feature as usage_feature
from backend.config import settings
from backend.db.models import Article, Cluster, Market, MarketArticleLink, MarketPrediction, ProcessedArticle, Source
from backend.ingestor.deduplicator import embedding_text, get_title_embedding
from backend.markets import polymarket
from backend.markets.forecast import compute_signal
from backend.markets.targeted import run_targeted_search
from backend.alerts.service import run_alerts
from backend.markets.matching import EvidenceItem, extract_terms, match_score, rank_evidence, term_overlap

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
    market.resolution = data.resolution
    for field in ("yes_token_id", "no_token_id", "best_bid", "best_ask", "taker_fee_bps", "order_min_size"):
        value = getattr(data, field)
        if value is not None:
            setattr(market, field, value)
    market.updated_at = datetime.now(timezone.utc)


async def sync_markets(session: AsyncSession, client=None) -> dict:
    """Upserts the most traded open binary markets and detects resolution of tracked ones."""
    fetched = await polymarket.fetch_active_markets(client=client)
    seen_ids = set()
    created = 0
    for data in fetched:
        seen_ids.add(data.id)
        market = await session.get(Market, data.id)
        if polymarket.is_multi_outcome(data):
            # Outcomes of multi-outcome events live under "Più esiti" (multi/service.py)
            if market is not None and market.multi_event_id is None:
                market.multi_event_id = data.event_id
            continue
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
    stmt = select(Market).where(Market.resolution.is_(None), Market.multi_event_id.is_(None))
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
        if data.resolution is not None:
            resolved += 1
    await session.commit()
    return {"synced": len(fetched), "created": created, "checked": len(stale), "resolved": resolved}


async def backfill_content_embeddings(session: AsyncSession, since: datetime, limit: int = 500) -> int:
    """Articles saved before content embeddings existed get one (recent ones only)."""
    articles = (await session.execute(
        select(Article).where(Article.fetched_at >= since, Article.content_embedding.is_(None)).limit(limit)
    )).scalars().all()
    if not articles:
        return 0
    texts = [embedding_text(a.title, a.content_raw) for a in articles]
    vectors = await asyncio.to_thread(lambda: [get_title_embedding(t) for t in texts])
    for article, vector in zip(articles, vectors):
        article.content_embedding = vector
    await session.commit()
    return len(articles)


def _candidate_distance(question_embedding):
    """Best cosine distance between the question and the article's title or title + text."""
    d_title = Article.title_embedding.cosine_distance(question_embedding)
    d_content = Article.content_embedding.cosine_distance(question_embedding)
    return func.least(d_title, func.coalesce(d_content, d_title))


async def refresh_links(session: AsyncSession, market_ids: Optional[list[str]] = None) -> int:
    """Links each open market to recent articles about the same subject.

    Candidates come from pgvector (semantic similarity of title or title + text); each one
    is then scored with the key terms of the question (see matching.py). Existing links are
    re-scored, keeping Jev's relevance and impact. Returns the number of new or changed links.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    await backfill_content_embeddings(session, since)
    floor = max(0.0, settings.MARKET_MATCH_THRESHOLD - settings.MARKET_CANDIDATE_MARGIN)
    stmt = select(Market).where(Market.closed == False, Market.question_embedding.is_not(None),  # noqa: E712
                                Market.multi_event_id.is_(None))
    if market_ids is not None:
        stmt = stmt.where(Market.id.in_(market_ids))
    markets = (await session.execute(stmt)).scalars().all()

    changed = 0
    for market in markets:
        distance = _candidate_distance(market.question_embedding)
        rows = (await session.execute(
            select(Article.id, Article.title, Article.content_raw, distance.label("distance"))
            .where(Article.fetched_at >= since, Article.title_embedding.is_not(None), distance <= 1.0 - floor)
            .order_by(distance)
            .limit(settings.MARKET_MAX_ARTICLES * 6)
        )).all()
        terms = extract_terms(market.question)
        values = []
        for article_id, title, content, dist in rows:
            similarity = round(1.0 - float(dist), 4)
            overlap = term_overlap(terms, f"{title}\n{(content or '')[:3000]}")
            score = match_score(similarity, overlap)
            if score >= settings.MARKET_MATCH_THRESHOLD:
                values.append({"market_id": market.id, "article_id": article_id, "similarity": similarity,
                               "match_score": score, "matched_terms": overlap.matched})
        if not values:
            continue
        insert = pg_insert(MarketArticleLink).values(values)
        result = await session.execute(
            insert.on_conflict_do_update(
                constraint="uq_market_article",
                set_={"similarity": insert.excluded.similarity, "match_score": insert.excluded.match_score,
                      "matched_terms": insert.excluded.matched_terms},
                where=MarketArticleLink.match_score.is_distinct_from(insert.excluded.match_score),
            )
        )
        changed += result.rowcount or 0
    await session.commit()
    return changed


async def get_market_evidence(session: AsyncSession, market_id: str, limit: Optional[int] = None) -> list[EvidenceItem]:
    """Best recent evidence for a market: ranked by match, source quality, recency and Jev's
    past relevance judgements, one article per story. Items unpack as (link, article, processed, source)."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    threshold = settings.MARKET_MATCH_THRESHOLD
    stmt = (
        select(MarketArticleLink, Article, ProcessedArticle, Source)
        .join(Article, Article.id == MarketArticleLink.article_id)
        .join(Source, Source.id == Article.source_id)
        .outerjoin(ProcessedArticle, ProcessedArticle.article_id == Article.id)
        .where(MarketArticleLink.market_id == market_id, Article.fetched_at >= since)
        # Links made before match scores existed are kept if they were similar enough
        .where(or_(MarketArticleLink.match_score >= threshold,
                   and_(MarketArticleLink.match_score.is_(None), MarketArticleLink.similarity >= threshold)))
        .order_by(func.coalesce(MarketArticleLink.match_score, MarketArticleLink.similarity).desc())
        .limit(300)
    )
    rows = (await session.execute(stmt)).all()
    cluster_ids = {article.cluster_id for _l, article, _p, _s in rows if article.cluster_id}
    cluster_sources = {}
    if cluster_ids:
        cluster_sources = dict((await session.execute(
            select(Cluster.id, Cluster.source_count).where(Cluster.id.in_(cluster_ids))
        )).all())
    return rank_evidence(rows, limit or settings.MARKET_MAX_ARTICLES, cluster_sources)


def build_jev_request(market: Market, evidence: list, now: Optional[datetime] = None):
    """Builds the System One state and questions for one market.

    The current market price is deliberately NOT included in the state, so that the
    Jev probability is an independent estimate that can be compared with the price.
    """
    from typesafe_sdk import Choice, Noul, Score

    now = now or datetime.now(timezone.utc)
    news = []
    for i, item in enumerate(evidence):
        link, article, processed, source = item
        published = article.published_at or article.fetched_at
        is_opinion = processed is not None and (processed.is_opinion or 0.0) >= 0.6
        news.append({
            "id": f"n{i}",
            "title": article.title,
            "source": article.publisher or source.name,
            "published_at": published.isoformat() if published else None,
            "age_hours": round((now - published).total_seconds() / 3600, 1) if published else None,
            "type": "opinion/analysis" if is_opinion else "news report",
            "source_reliability": round(getattr(item, "quality", 0.75), 2),
            "reported_by_sources": getattr(item, "corroboration", 1),
            "summary": (processed.summary if processed and processed.summary else (article.content_raw or ""))[:1200],
        })

    state = {
        "today": now.date().isoformat(),
        "market": {
            "question": market.question,
            "resolution_rules": (market.description or "")[:3000],
            "end_date": market.end_date.isoformat() if market.end_date else None,
            "days_left": round((market.end_date - now).total_seconds() / 86400, 1) if market.end_date else None,
        },
        "news": news,
        "how_to_weigh_news": (
            "News items are ordered from most to least useful. Weigh recent news reports from reliable "
            "or primary sources more than opinion pieces; a story reported by several sources is more "
            "credible. Check each item against the exact resolution rules and the time left: a development "
            "that does not satisfy the rules before the end date does not resolve the market."
        ),
    }

    questions = {
        "resolves_yes": Noul(
            instructions=(
                "Considering the market's resolution rules, its end date and the days left, today's date, "
                "the news items (weighted as described in how_to_weigh_news), the base rate of similar "
                "events and general world knowledge, will this market resolve YES?"
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


def parse_forecast(response) -> tuple[float, float]:
    """(P(YES) according to Jev, evidence strength 0-1) from a System One response."""
    model_p = float(response.nouls["resolves_yes"].noul)
    evidence_strength = float(response.scores["evidence_strength"].score) / (len(EVIDENCE_CRITERIA) - 1)
    return model_p, evidence_strength


async def predict_market(session: AsyncSession, market: Market, max_wait: Optional[float] = None) -> MarketPrediction:
    """Runs a Jev forecast for one market and stores it together with the betting signal."""
    if not jev.is_enabled():
        raise jev.JevUnavailableError("TYPESAFE_API_KEY is not configured")
    if market.yes_price is None:
        raise ValueError("Market has no current price")

    evidence = await get_market_evidence(session, market.id)
    if not evidence:
        raise LookupError("No related news found for this market")

    state, questions = build_jev_request(market, evidence)
    response = await jev.system_one(state, questions, max_wait=max_wait)
    model_p, evidence_strength = parse_forecast(response)

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
        model_weight=signal.model_weight,
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
    # Is it worth betting, and how much? Stores the evaluation and places the simulated bet.
    from backend.betting.portfolio import apply_economics
    await apply_economics(session, market, prediction)
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
    from backend.betting.portfolio import settle_bets
    stats = await sync_markets(session)
    stats["settled_bets"] = await settle_bets(session)
    stats["targeted"] = await run_targeted_search(session)
    stats["links"] = await refresh_links(session)
    try:
        from backend.multi.service import run_pipeline as run_multi_pipeline
        stats["multi"] = await run_multi_pipeline(session)
    except Exception as e:  # a failure on events must not stop YES/NO markets
        await session.rollback()
        logger.error(f"Multi-outcome sync failed: {e}")
    # Fresh, relevant news: evaluate those markets right away and notify (before the batch below)
    stats["alerts"] = await run_alerts(session)
    stats["predictions"] = 0
    if settings.PREDICTION_AUTO and jev.is_enabled():
        for market in await markets_needing_prediction(session, settings.PREDICTION_MAX_PER_RUN):
            try:
                with usage_feature("previsioni"):
                    await predict_market(session, market)
                stats["predictions"] += 1
            except RateLimited as e:
                logger.warning(f"Automatic predictions paused: {e}")
                await session.rollback()
                break
            except Exception as e:
                logger.error(f"Prediction failed for market {market.id}: {e}")
                await session.rollback()
    return stats
