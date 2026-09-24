import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Article, Cluster, ProcessedArticle, Source
from sqlalchemy import select
from backend.db.crud import article_exists_by_hash, find_similar_article, get_unprocessed_articles
from backend.ingestor.fetcher import FeedError, fetch_and_parse_feed
from backend.ingestor.sources import seed_sources_if_empty
from backend.ingestor.deduplicator import embedding_text, hash_url, get_title_embedding
from backend.ai.summarizer import summarize_article
from backend.ai.ratelimit import RateLimited
from backend.ai.typesafe_evaluator import evaluate_article_dimensions
from backend.markets.service import run_market_pipeline

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
# Scheduled and manually triggered (/ingest) runs must not overlap: they would race on
# the same unprocessed articles and violate the processed_articles.article_id unique key
_pipeline_lock = asyncio.Lock()

async def process_ai_queue():
    """
    Processes all pending articles through the unified AI pipeline:
    1. Generates concise summary via configured summarizer (Gemini / Groq / Ollama)
    2. Evaluates multi-dimensional quality scores via TypeSafe Jev (Composite Scoring)
    3. Persists normalized scores and category into the database
    """
    async with SessionLocal() as session:
        unprocessed = await get_unprocessed_articles(session, limit=settings.AI_BATCH_SIZE)
        # Snapshot plain values: a rollback expires ORM instances, and lazy-loading
        # expired attributes is not allowed in async sessions
        pending = [(a.id, a.title, a.content_raw or "", a.source_id, a.publisher) for a in unprocessed]
        for article_id, title, content_raw, source_id, publisher in pending:
            try:
                source = await session.get(Source, source_id)
                # Aggregated results (targeted search): judge the original outlet
                source_name = publisher or (source.name if source else "Unknown Source")
                source_hint = source.category_hint if source else None
                
                # 1. TypeSafe Jev evaluation first: if Jev is rate limited the batch stops here,
                #    before spending a summarizer call on an article that will be retried
                eval_res = await evaluate_article_dimensions(
                    title=title,
                    source_name=source_name,
                    content=content_raw,
                    source_hint=source_hint,
                )

                # 2. Text summary
                # Headline-only items (targeted search) have nothing to summarise: no API call
                summary = await summarize_article(title, content_raw[:2500]) if content_raw.strip() else title
                
                composite = eval_res["composite_score"]
                legacy_score = max(1, min(10, int(round(composite * 10))))
                
                processed = ProcessedArticle(
                    article_id=article_id,
                    summary=summary,
                    category=eval_res["category"],
                    clickbait_score=eval_res["clickbait_score"],
                    authority_score=eval_res["authority_score"],
                    technical_depth_score=eval_res["technical_depth_score"],
                    urgency_score=eval_res["urgency_score"],
                    composite_score=composite,
                    importance_score=legacy_score,
                    category_confidence=eval_res.get("category_confidence"),
                    region=eval_res.get("region"),
                    is_opinion=eval_res.get("is_opinion"),
                    market_relevance=eval_res.get("market_relevance"),
                    classifier=eval_res.get("source"),
                )
                session.add(processed)
                await session.commit()
                logger.info(f"Processed article {article_id}: {eval_res['category']} (Composite: {composite})")
            except RateLimited as e:
                # Stop the batch: the remaining articles stay unprocessed and are picked up next run
                logger.warning(f"AI queue paused: {e}. Remaining articles will be processed on the next run")
                await session.rollback()
                break
            except Exception as e:
                logger.error(f"AI processing failed for article {article_id}: {e}")
                await session.rollback()

async def ingest_source(session, source: Source) -> int:
    """Fetches one source and stores new articles (L1 URL-hash and L2 embedding deduplication).

    The outcome (time, new items, error) is saved on the source for the settings page.
    """
    source_id = source.id
    try:
        entries = await fetch_and_parse_feed(source.url)
    except FeedError as e:
        await _record_fetch(session, source_id, error=str(e))
        raise
    except Exception as e:
        await _record_fetch(session, source_id, error=f"Errore imprevisto: {e.__class__.__name__}")
        raise
    try:
        added = len(await store_entries(session, source_id, entries))
    except Exception as e:
        await _record_fetch(session, source_id, error=f"Salvataggio non riuscito: {e.__class__.__name__}")
        raise
    await _record_fetch(session, source_id, new_items=added)
    return added


async def store_entries(session, source_id, entries, publisher_in_title: bool = False) -> list[Article]:
    """Saves new articles (L1 URL-hash and L2 embedding deduplication); returns the ones added.

    publisher_in_title: aggregated results ("Headline - Outlet") keep the outlet separately.
    """
    added = []
    for entry in entries:
        url_h = hash_url(entry["url"])
        if await article_exists_by_hash(session, url_h):
            continue  # L1 Deduplication skipped
        title, publisher = entry["title"], entry.get("publisher")
        if publisher_in_title and " - " in title:
            head, tail = (part.strip() for part in title.rsplit(" - ", 1))
            if not publisher or tail.lower() == publisher.lower():
                title, publisher = head, publisher or tail

        embedding = await asyncio.to_thread(get_title_embedding, title)
        content_embedding = await asyncio.to_thread(get_title_embedding, embedding_text(title, entry.get("content_raw")))
        similar_article = await find_similar_article(session, embedding, settings.SIMILARITY_THRESHOLD)

        cluster_id = None
        if similar_article:
            if not similar_article.cluster_id:
                new_cluster = Cluster(canonical_title=similar_article.title)
                session.add(new_cluster)
                await session.flush()
                similar_article.cluster_id = new_cluster.id
                cluster_id = new_cluster.id
            else:
                cluster_id = similar_article.cluster_id

            # Increment source count
            cluster = await session.get(Cluster, cluster_id)
            if cluster:
                cluster.source_count += 1

        new_article = Article(
            source_id=source_id,
            cluster_id=cluster_id,
            title=title,
            url=entry["url"],
            content_raw=entry.get("content_raw"),
            published_at=entry.get("published_at"),
            url_hash=url_h,
            title_embedding=embedding,
            content_embedding=content_embedding,
            publisher=publisher,
        )
        session.add(new_article)
        await session.commit()
        added.append(new_article)
    return added


async def _record_fetch(session, source_id, new_items: int = 0, error: str | None = None) -> None:
    await session.rollback()  # drop anything half-written before recording the outcome
    source = await session.get(Source, source_id)
    if source is None:
        return
    source.last_fetched_at = datetime.now(timezone.utc)
    source.last_status = "error" if error else "ok"
    source.last_error = error[:300] if error else None
    source.last_new_items = None if error else new_items
    await session.commit()


async def ingest_single_source(source_id) -> None:
    """Fetches one source now (dashboard button), then classifies the new articles."""
    async with _pipeline_lock:
        async with SessionLocal() as session:
            source = await session.get(Source, source_id)
            if source is None:
                return
            try:
                await ingest_source(session, source)
            except Exception as e:
                logger.warning(f"Fetch failed for {source.url}: {e}")
        await process_ai_queue()

async def run_ingestion_pipeline():
    if _pipeline_lock.locked():
        logger.info("Ingestion pipeline already running, skipping this trigger")
        return
    async with _pipeline_lock:
        await _run_pipeline()

async def _fetch_all_sources() -> int:
    total = 0
    async with SessionLocal() as session:
        await seed_sources_if_empty(session)
        sources = (await session.execute(select(Source).where(Source.active == True, Source.kind == "feed"))).scalars().all()  # noqa: E712
        pending = [(s.id, s.name) for s in sources]
        for source_id, name in pending:
            # A broken feed must not abort ingestion of the others
            try:
                source = await session.get(Source, source_id)
                added = await ingest_source(session, source)
                total += added
                logger.info(f"Feed {name}: {added} new articles")
            except Exception as e:
                logger.error(f"Ingestion failed for feed {name}: {e}")
                await session.rollback()
    return total


async def _run_pipeline():
    await _fetch_all_sources()

    # Step 3: AI Pipeline for Unprocessed Articles
    await process_ai_queue()

    # Step 4: Polymarket sync, news <-> market linking and Jev forecasts
    if settings.POLYMARKET_ENABLED:
        try:
            async with SessionLocal() as session:
                stats = await run_market_pipeline(session)
                logger.info(f"Market pipeline: {stats}")
        except Exception as e:
            logger.error(f"Market pipeline failed: {e}")

async def run_alert_scan() -> None:
    """Fast lane for alerts, between two full runs: new news -> links -> alerts.

    No summaries or classification here (the full run does them), so it is cheap enough
    to run every few minutes. Skipped while the full pipeline is running.
    """
    from backend.alerts.service import get_settings, run_alerts
    from backend.markets.service import _market_lock, refresh_links
    from backend.ai import jev

    if not settings.POLYMARKET_ENABLED or not jev.is_enabled() or _pipeline_lock.locked():
        return
    async with SessionLocal() as session:
        if not (await get_settings(session)).enabled:
            return
    async with _pipeline_lock:
        added = await _fetch_all_sources()
        if not added or _market_lock.locked():
            return
        async with _market_lock:
            async with SessionLocal() as session:
                await refresh_links(session)
                await run_alerts(session)


async def run_alert_followups() -> None:
    from backend.alerts.service import record_followups
    async with SessionLocal() as session:
        try:
            await record_followups(session)
        except Exception as e:
            logger.error(f"Alert follow-ups failed: {e}")


def start_scheduler():
    # First run right after startup, then every INGEST_INTERVAL_MINUTES
    scheduler.add_job(
        run_ingestion_pipeline, 'interval',
        minutes=settings.INGEST_INTERVAL_MINUTES,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1, coalesce=True,
    )
    if settings.ALERT_SCAN_MINUTES > 0 and settings.ALERT_SCAN_MINUTES < settings.INGEST_INTERVAL_MINUTES:
        scheduler.add_job(run_alert_scan, 'interval', minutes=settings.ALERT_SCAN_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(run_alert_followups, 'interval', minutes=max(1, settings.ALERT_FOLLOWUP_MINUTES),
                      max_instances=1, coalesce=True)
    scheduler.start()

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

async def reclassify_articles(limit: int = 200) -> int:
    """Re-runs classification on stored articles (keeps summaries).

    With a Jev key: articles not yet classified by Jev. Without: articles classified before
    the richer classifier (no region/market relevance), with the heuristic.
    """
    from backend.ai import jev
    from sqlalchemy import or_
    async with _pipeline_lock:
        async with SessionLocal() as session:
            stmt = select(ProcessedArticle, Article, Source).join(Article, Article.id == ProcessedArticle.article_id)\
                .join(Source, Source.id == Article.source_id)
            if jev.is_enabled():
                stmt = stmt.where(or_(ProcessedArticle.classifier.is_(None), ProcessedArticle.classifier != "jev"))
            else:
                stmt = stmt.where(or_(ProcessedArticle.classifier.is_(None), ProcessedArticle.market_relevance.is_(None)))
            rows = (await session.execute(stmt.order_by(Article.fetched_at.desc()).limit(limit))).all()
            items = [(p.id, a.title, a.content_raw or "", a.publisher or s.name, s.category_hint) for p, a, s in rows]
            done = 0
            for processed_id, title, content, source_name, hint in items:
                try:
                    res = await evaluate_article_dimensions(title=title, source_name=source_name, content=content, source_hint=hint)
                    processed = await session.get(ProcessedArticle, processed_id)
                    for field in ("clickbait_score", "authority_score", "technical_depth_score", "urgency_score",
                                  "composite_score", "category", "category_confidence", "region", "is_opinion",
                                  "market_relevance"):
                        setattr(processed, field, res.get(field))
                    processed.classifier = res["source"]
                    processed.importance_score = max(1, min(10, int(round(res["composite_score"] * 10))))
                    await session.commit()
                    done += 1
                except RateLimited as e:
                    logger.warning(f"Reclassification paused: {e}")
                    await session.rollback()
                    break
                except Exception as e:
                    logger.error(f"Reclassification failed for {processed_id}: {e}")
                    await session.rollback()
            logger.info(f"Reclassified {done} articles")
            return done
