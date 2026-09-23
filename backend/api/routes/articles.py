import uuid
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from backend.db.database import get_db
from backend.db.models import Article, ProcessedArticle, Source, Cluster
from backend.api.schemas import ArticleListResponse, ArticleResponse
from backend.ai.typesafe_evaluator import calculate_composite_score

router = APIRouter(prefix="/articles", tags=["articles"])

def _or_default(value, default):
    return default if value is None else value

@router.get("", response_model=ArticleListResponse)
async def get_articles(
    category: str = Query(None, description="Filtra per macro categoria"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    # Weight parameters for dynamic composite ranking
    w_authority: float = Query(0.35, ge=0.0, le=2.0, description="Peso autorevolezza"),
    w_tech: float = Query(0.25, ge=0.0, le=2.0, description="Peso profondità tecnica"),
    w_urgency: float = Query(0.25, ge=0.0, le=2.0, description="Peso urgenza/breaking"),
    w_clickbait: float = Query(0.40, ge=0.0, le=2.0, description="Penalità clickbait"),
    # Hard thresholds
    max_clickbait: float = Query(None, ge=0.0, le=1.0, description="Soglia massima tollerata clickbait"),
    min_authority: float = Query(None, ge=0.0, le=1.0, description="Soglia minima autorevolezza"),
    db: AsyncSession = Depends(get_db)
):
    query = select(Article, ProcessedArticle, Source, Cluster).select_from(Article)\
        .join(ProcessedArticle, ProcessedArticle.article_id == Article.id)\
        .join(Source, Source.id == Article.source_id)\
        .outerjoin(Cluster, Cluster.id == Article.cluster_id)
    
    if category:
        query = query.where(ProcessedArticle.category == category)
    if max_clickbait is not None:
        query = query.where(or_(ProcessedArticle.clickbait_score == None, ProcessedArticle.clickbait_score <= max_clickbait))
    if min_authority is not None:
        query = query.where(or_(ProcessedArticle.authority_score == None, ProcessedArticle.authority_score >= min_authority))
        
    # Count total matching query
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    # Dynamic weighted ranking computed in SQL, so ordering and pagination cover
    # the whole result set (same formula as calculate_composite_score)
    auth_col = func.coalesce(ProcessedArticle.authority_score, 0.5)
    tech_col = func.coalesce(ProcessedArticle.technical_depth_score, 0.5)
    urg_col = func.coalesce(ProcessedArticle.urgency_score, 0.5)
    cb_col = func.coalesce(ProcessedArticle.clickbait_score, 0.0)
    rank_expr = func.greatest(0.0, func.least(1.0,
        w_authority * auth_col + w_tech * tech_col + w_urgency * urg_col - w_clickbait * cb_col
    ))
    query = query.order_by(
        rank_expr.desc(),
        func.coalesce(Article.published_at, Article.fetched_at).desc().nulls_last(),
    ).offset(offset).limit(limit)
    
    result = await db.execute(query)
    rows = result.all()
    
    scored_articles = []
    for article, processed, source, cluster in rows:
        auth = processed.authority_score if processed.authority_score is not None else 0.5
        tech = processed.technical_depth_score if processed.technical_depth_score is not None else 0.5
        urg = processed.urgency_score if processed.urgency_score is not None else 0.5
        cb = processed.clickbait_score if processed.clickbait_score is not None else 0.0
        
        # Calculate customized dynamic score based on user-supplied weights
        dynamic_score = calculate_composite_score(
            authority=auth,
            tech_depth=tech,
            urgency=urg,
            clickbait=cb,
            w_authority=w_authority,
            w_tech=w_tech,
            w_urgency=w_urgency,
            w_clickbait=w_clickbait
        )
        
        legacy_importance = processed.importance_score or int(round(dynamic_score * 10))
        
        scored_articles.append({
            "id": article.id,
            "title": article.title,
            "url": article.url,
            "source_name": source.name,
            "published_at": article.published_at,
            "summary": processed.summary,
            "category": processed.category,
            "clickbait_score": cb,
            "authority_score": auth,
            "technical_depth_score": tech,
            "urgency_score": urg,
            "composite_score": dynamic_score,
            "importance_score": legacy_importance,
            "cluster_id": cluster.id if cluster else None,
            "cluster_source_count": cluster.source_count if cluster else 1,
        })
        
    return {"total": total, "articles": scored_articles}

@router.get("/{article_id}", response_model=ArticleResponse)
async def get_article(article_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    query = select(Article, ProcessedArticle, Source, Cluster).select_from(Article)\
        .join(ProcessedArticle, ProcessedArticle.article_id == Article.id)\
        .join(Source, Source.id == Article.source_id)\
        .outerjoin(Cluster, Cluster.id == Article.cluster_id)\
        .where(Article.id == article_id)
        
    result = await db.execute(query)
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
        
    article, processed, source, cluster = row
    return {
        "id": article.id,
        "title": article.title,
        "url": article.url,
        "source_name": source.name,
        "published_at": article.published_at,
        "summary": processed.summary,
        "category": processed.category,
        # Explicit None checks: a legitimate 0.0 score must not be replaced by the default
        "clickbait_score": _or_default(processed.clickbait_score, 0.0),
        "authority_score": _or_default(processed.authority_score, 0.5),
        "technical_depth_score": _or_default(processed.technical_depth_score, 0.5),
        "urgency_score": _or_default(processed.urgency_score, 0.5),
        "composite_score": _or_default(processed.composite_score, 0.5),
        "importance_score": _or_default(processed.importance_score, 5),
        "cluster_id": cluster.id if cluster else None,
        "cluster_source_count": cluster.source_count if cluster else 1
    }