import yaml
import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Article, Cluster, ProcessedArticle, Source
from backend.db.crud import get_or_create_source, article_exists_by_hash, find_similar_article, get_unprocessed_articles
from backend.ingestor.fetcher import fetch_and_parse_feed
from backend.ingestor.deduplicator import hash_url, get_title_embedding
from backend.ai.summarizer import summarize_article
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
        unprocessed = await get_unprocessed_articles(session, limit=50)
        # Snapshot plain values: a rollback expires ORM instances, and lazy-loading
        # expired attributes is not allowed in async sessions
        pending = [(a.id, a.title, a.content_raw or "", a.source_id) for a in unprocessed]
        for article_id, title, content_raw, source_id in pending:
            try:
                # Fetch source name
                source = await session.get(Source, source_id)
                source_name = source.name if source else "Unknown Source"
                
                # 1. Text Summary
                summary = await summarize_article(title, content_raw[:2500])
                
                # 2. TypeSafe Jev Evaluation (Parallel judgments on single article state)
                eval_res = await evaluate_article_dimensions(
                    title=title,
                    source_name=source_name,
                    content=content_raw
                )
                
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
                    importance_score=legacy_score
                )
                session.add(processed)
                await session.commit()
                logger.info(f"Processed article {article_id}: {eval_res['category']} (Composite: {composite})")
            except Exception as e:
                logger.error(f"AI processing failed for article {article_id}: {e}")
                await session.rollback()

async def ingest_feed(session, feed: dict) -> int:
    """Fetches one feed and stores new articles (L1 URL-hash and L2 embedding deduplication)."""
    source = await get_or_create_source(session, feed["name"], feed["url"])
    await session.commit()
    
    entries = await fetch_and_parse_feed(feed["url"])
    added = 0
    
    for entry in entries:
        url_h = hash_url(entry["url"])
        if await article_exists_by_hash(session, url_h):
            continue  # L1 Deduplication skipped
        
        embedding = await asyncio.to_thread(get_title_embedding, entry["title"])
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
            source_id=source.id,
            cluster_id=cluster_id,
            title=entry["title"],
            url=entry["url"],
            content_raw=entry["content_raw"],
            published_at=entry["published_at"],
            url_hash=url_h,
            title_embedding=embedding
        )
        session.add(new_article)
        await session.commit()
        added += 1
    return added

async def run_ingestion_pipeline():
    if _pipeline_lock.locked():
        logger.info("Ingestion pipeline already running, skipping this trigger")
        return
    async with _pipeline_lock:
        await _run_pipeline()

async def _run_pipeline():
    try:
        with open("feeds.yaml", "r") as f:
            feeds_conf = yaml.safe_load(f).get("feeds", [])
    except Exception as e:
        logger.error(f"Could not load feeds.yaml: {e}")
        return

    async with SessionLocal() as session:
        for feed in feeds_conf:
            if not feed.get("active"):
                continue
            # A broken feed must not abort ingestion of the others
            try:
                added = await ingest_feed(session, feed)
                logger.info(f"Feed {feed['name']}: {added} new articles")
            except Exception as e:
                logger.error(f"Ingestion failed for feed {feed.get('name')}: {e}")
                await session.rollback()

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

def start_scheduler():
    # First run right after startup, then every INGEST_INTERVAL_MINUTES
    scheduler.add_job(
        run_ingestion_pipeline, 'interval',
        minutes=settings.INGEST_INTERVAL_MINUTES,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1, coalesce=True,
    )
    scheduler.start()

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)