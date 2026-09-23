from sqlalchemy import select, func
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

async def find_similar_article(session: AsyncSession, embedding: list[float], threshold: float) -> Article | None:
    six_hours_ago = datetime.now(timezone.utc) - timedelta(hours=6)
    stmt = select(Article).where(
        Article.fetched_at >= six_hours_ago
    ).where(
        Article.title_embedding.cosine_distance(embedding) <= (1 - threshold)
    ).order_by(
        Article.title_embedding.cosine_distance(embedding)
    ).limit(1)
    
    result = await session.execute(stmt)
    return result.scalars().first()

async def get_unprocessed_articles(session: AsyncSession, limit: int = 50):
    stmt = select(Article).outerjoin(ProcessedArticle).where(ProcessedArticle.id == None).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()