import yaml
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Article, Cluster, ProcessedArticle, Source
from backend.db.crud import get_or_create_source, article_exists_by_hash, find_similar_article, get_unprocessed_articles
from backend.ingestor.fetcher import fetch_and_parse_feed
from backend.ingestor.deduplicator import hash_url, get_title_embedding
from backend.ai.summarizer import summarize_article
from backend.ai.typesafe_evaluator import evaluate_article_dimensions

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

async def process_ai_queue():
    """
    Processes all pending articles through the unified AI pipeline:
    1. Generates concise summary via configured summarizer (Gemini / Groq / Ollama)
    2. Evaluates multi-dimensional quality scores via TypeSafe Jev (Composite Scoring)
    3. Persists normalized scores and category into the database
    """
    async with SessionLocal() as session:
        unprocessed = await get_unprocessed_articles(session, limit=50)
        for article in unprocessed:
            try:
                # Fetch source name
                source = await session.get(Source, article.source_id)
                source_name = source.name if source else "Unknown Source"
                
                # 1. Text Summary
                summary = await summarize_article(
                    article.title, 
                    article.content_raw[:2500] if article.content_raw else ""
                )
                
                # 2. TypeSafe Jev Evaluation (Parallel judgments on single article state)
                eval_res = await evaluate_article_dimensions(
                    title=article.title,
                    source_name=source_name,
                    content=article.content_raw or ""
                )
                
                composite = eval_res["composite_score"]
                legacy_score = max(1, min(10, int(round(composite * 10))))
                
                processed = ProcessedArticle(
                    article_id=article.id,
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
                logger.info(f"Processed article {article.id}: {eval_res['category']} (Composite: {composite})")
            except Exception as e:
                logger.error(f"AI processing failed for article {article.id}: {e}")
                await session.rollback()

async def run_ingestion_pipeline():
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
            
            source = await get_or_create_source(session, feed["name"], feed["url"])
            await session.commit()
            
            entries = await fetch_and_parse_feed(feed["url"])
            
            for entry in entries:
                url_h = hash_url(entry["url"])
                if await article_exists_by_hash(session, url_h):
                    continue  # L1 Deduplication skipped
                
                embedding = get_title_embedding(entry["title"])
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

    # Step 3: AI Pipeline for Unprocessed Articles
    await process_ai_queue()

def start_scheduler():
    scheduler.add_job(run_ingestion_pipeline, 'interval', minutes=settings.INGEST_INTERVAL_MINUTES)
    scheduler.start()

def shutdown_scheduler():
    scheduler.shutdown()