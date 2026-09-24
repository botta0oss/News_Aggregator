"""Keeps the stored vectors consistent with the embedding model.

Vectors from two different models cannot be compared. When EMBEDDING_MODEL changes, the
vectors of articles, markets and events are cleared and recomputed in the background,
newest articles first (they matter most for matching); rows without a vector are simply
skipped by the searches until then.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import AppMeta, Article, Market, MultiEvent
from backend.ingestor import deduplicator

logger = logging.getLogger(__name__)

META_KEY = "embedding_model"
LEGACY_MODEL = "all-MiniLM-L6-v2"   # default before the model was recorded
BATCH = 128

_task: asyncio.Task | None = None


async def stored_model(db: AsyncSession) -> str | None:
    row = await db.get(AppMeta, META_KEY)
    if row is not None:
        return row.value
    has_vectors = (await db.execute(select(func.count(Article.id)).where(Article.title_embedding.is_not(None)))).scalar()
    return LEGACY_MODEL if has_vectors else None


async def check_model(db: AsyncSession) -> bool:
    """Clears the vectors if they come from another model. Returns True if a re-embed is needed."""
    previous = await stored_model(db)
    current = settings.EMBEDDING_MODEL
    stale = previous is not None and previous != current
    if stale:
        logger.warning(f"Embedding model changed ({previous} -> {current}): recomputing the stored vectors")
        await db.execute(update(Article).values(title_embedding=None, content_embedding=None))
        await db.execute(update(Market).values(question_embedding=None))
        await db.execute(update(MultiEvent).values(title_embedding=None))
    row = await db.get(AppMeta, META_KEY)
    if row is None:
        db.add(AppMeta(key=META_KEY, value=current))
    elif row.value != current:
        row.value, row.updated_at = current, datetime.now(timezone.utc)
    await db.commit()
    return stale or await missing(db) > 0


async def missing(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(Article.id)).where(Article.title_embedding.is_(None)))).scalar() or 0


async def backfill(batch: int = BATCH) -> int:
    """Recomputes missing vectors: markets and events, then articles newest first."""
    done = 0
    async with SessionLocal() as db:
        for model, text_of, column in ((Market, lambda m: m.question, "question_embedding"),
                                       (MultiEvent, lambda e: e.title, "title_embedding")):
            rows = (await db.execute(select(model).where(getattr(model, column).is_(None)))).scalars().all()
            if rows:
                vectors = await asyncio.to_thread(deduplicator.get_embeddings, [text_of(r) for r in rows])
                for r, v in zip(rows, vectors):
                    setattr(r, column, v)
                await db.commit()
        while True:
            articles = (await db.execute(
                select(Article).where(Article.title_embedding.is_(None)).order_by(Article.fetched_at.desc()).limit(batch)
            )).scalars().all()
            if not articles:
                break
            titles = [a.title for a in articles]
            texts = [deduplicator.embedding_text(a.title, a.content_raw) for a in articles]
            vectors = await asyncio.to_thread(deduplicator.get_embeddings, titles + texts)
            for i, a in enumerate(articles):
                a.title_embedding, a.content_embedding = vectors[i], vectors[len(articles) + i]
            await db.commit()
            done += len(articles)
    if done:
        logger.info(f"Re-embedded {done} articles with {settings.EMBEDDING_MODEL}")
    return done


async def start_if_needed() -> None:
    """At startup: checks the model and runs the re-embedding in the background."""
    global _task
    async with SessionLocal() as db:
        needed = await check_model(db)
    if needed and (_task is None or _task.done()):
        _task = asyncio.create_task(_safe_backfill())


async def _safe_backfill() -> None:
    try:
        await backfill()
    except Exception as e:  # the next start retries
        logger.error(f"Re-embedding failed: {e}")
