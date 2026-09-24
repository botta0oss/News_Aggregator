from sqlalchemy import Text, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.models import Source, Article, Cluster, ProcessedArticle
from datetime import datetime, timedelta, timezone

async def get_or_create_source(session: AsyncSession, name: str, url: str) -> Source:
    stmt = select(Source).where(Source.url == url)
    result = await session.execute(stmt)
    source = result.scalars().first()
    if not source:
        source = Source(name=name, url=url)
        session.add(source)
        await session.flush()
    return source

async def article_exists_by_hash(session: AsyncSession, url_hash: str) -> bool:
    stmt = select(Article.id).where(Article.url_hash == url_hash)
    result = await session.execute(stmt)
    return result.scalars().first() is not None

async def find_similar_article(session: AsyncSession, embedding: list[float], threshold: float,
                               content_embedding: list[float] | None = None, story_threshold: float | None = None,
                               window_hours: float = 48) -> Article | None:
    """An earlier article about the same story: near-identical title, or (when given) title + text
    close enough in meaning. Outlets rewrite headlines, so the story check catches most rewrites."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    stmt = select(Article).where(
        Article.fetched_at >= since
    ).where(
        Article.title_embedding.cosine_distance(embedding) <= (1 - threshold)
    ).order_by(
        Article.title_embedding.cosine_distance(embedding)
    ).limit(1)
    found = (await session.execute(stmt)).scalars().first()
    if found is not None or content_embedding is None or story_threshold is None:
        return found
    stmt = select(Article).where(
        Article.fetched_at >= since, Article.content_embedding.is_not(None),
        Article.content_embedding.cosine_distance(content_embedding) <= (1 - story_threshold),
    ).order_by(Article.content_embedding.cosine_distance(content_embedding)).limit(1)
    return (await session.execute(stmt)).scalars().first()


async def distinct_sources(session: AsyncSession, cluster_id) -> int:
    """Independent outlets in a cluster: the publisher for aggregated results, otherwise the feed."""
    outlet = func.coalesce(func.lower(Article.publisher), func.cast(Article.source_id, Text))
    return (await session.execute(
        select(func.count(func.distinct(outlet))).where(Article.cluster_id == cluster_id)
    )).scalar() or 1

async def get_unprocessed_articles(session: AsyncSession, limit: int = 50):
    stmt = select(Article).outerjoin(ProcessedArticle).where(ProcessedArticle.id == None)\
        .order_by(Article.fetched_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()