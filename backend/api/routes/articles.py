import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, literal, literal_column
from backend.db.database import get_db
from backend.db.models import Article, ProcessedArticle, Source, Cluster
from backend.api.schemas import ArticleListResponse, ArticleResponse
from backend.ai.typesafe_evaluator import calculate_composite_score
from backend.i18n import tr

router = APIRouter(prefix="/articles", tags=["articles"])

HL_START, HL_STOP = "\u0002", "\u0003"
HEADLINE_OPTS = f"StartSel={HL_START}, StopSel={HL_STOP}, MaxWords=40, MinWords=18, MaxFragments=2, FragmentDelimiter= … "
TITLE_OPTS = f"StartSel={HL_START}, StopSel={HL_STOP}, HighlightAll=true"
WORD_RE = re.compile(r"\w+", re.UNICODE)
FTS_EXPR = "to_tsvector('simple'::regconfig, coalesce(articles.title, '') || ' ' || coalesce(articles.content_raw, ''))"

def _or_default(value, default):
    return default if value is None else value


def build_tsquery(q: str):
    """Plain words: all must match, the last one as a prefix (search-as-you-type).
    Quotes, "-word" or "or": web-search syntax."""
    q = q.strip()
    if '"' in q or re.search(r"(^|\s)-\w", q) or re.search(r"\s(or|OR)\s", q):
        return func.websearch_to_tsquery("simple", q)
    words = [w.lower() for w in WORD_RE.findall(q)][:12]
    if not words:
        return None
    # Words are \w+ only, so they cannot inject tsquery operators
    return func.to_tsquery("simple", " & ".join(words[:-1] + [words[-1] + ":*"]))


def _base_query():
    return select(Article, ProcessedArticle, Source, Cluster).select_from(Article)\
        .join(ProcessedArticle, ProcessedArticle.article_id == Article.id)\
        .join(Source, Source.id == Article.source_id)\
        .outerjoin(Cluster, Cluster.id == Article.cluster_id)


@router.get("", response_model=ArticleListResponse)
async def get_articles(
    q: Optional[str] = Query(None, max_length=200, description="Text to search in title, content and summary"),
    scope: Literal["all", "title"] = Query("all", description="Where to search"),
    sort: Optional[Literal["relevance", "score", "recent"]] = Query(None, description="Sorting (default: relevance when there is a search)"),
    category: str = Query(None, description="Filter by macro category"),
    region: Optional[str] = Query(None, max_length=40),
    source_id: Optional[uuid.UUID] = Query(None),
    since_hours: Optional[int] = Query(None, ge=1, le=24 * 365),
    min_market_relevance: Optional[float] = Query(None, ge=0.0, le=1.0),
    hide_opinion: bool = Query(False, description="Exclude opinion and commentary"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    # Weight parameters for dynamic composite ranking
    w_authority: float = Query(0.35, ge=0.0, le=2.0, description="Authority weight"),
    w_tech: float = Query(0.25, ge=0.0, le=2.0, description="Technical depth weight"),
    w_urgency: float = Query(0.25, ge=0.0, le=2.0, description="Urgency/breaking weight"),
    w_clickbait: float = Query(0.40, ge=0.0, le=2.0, description="Clickbait penalty"),
    # Hard thresholds
    max_clickbait: float = Query(None, ge=0.0, le=1.0, description="Maximum tolerated clickbait"),
    min_authority: float = Query(None, ge=0.0, le=1.0, description="Minimum authority"),
    db: AsyncSession = Depends(get_db)
):
    query = _base_query()

    if category:
        query = query.where(ProcessedArticle.category == category)
    if region:
        query = query.where(ProcessedArticle.region == region)
    if source_id:
        query = query.where(Article.source_id == source_id)
    if since_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        query = query.where(func.coalesce(Article.published_at, Article.fetched_at) >= since)
    if min_market_relevance is not None:
        query = query.where(ProcessedArticle.market_relevance >= min_market_relevance)
    if hide_opinion:
        query = query.where(or_(ProcessedArticle.is_opinion == None, ProcessedArticle.is_opinion < 0.5))
    if max_clickbait is not None:
        query = query.where(or_(ProcessedArticle.clickbait_score == None, ProcessedArticle.clickbait_score <= max_clickbait))
    if min_authority is not None:
        query = query.where(or_(ProcessedArticle.authority_score == None, ProcessedArticle.authority_score >= min_authority))

    # Full-text search. The title+content expression matches the ix_articles_fts index.
    tsq = build_tsquery(q) if q and q.strip() else None
    title_vec = func.to_tsvector("simple", func.coalesce(Article.title, ""))
    # Written literally so it is identical to the ix_articles_fts index expression (bind parameters would not match)
    article_vec = literal_column(FTS_EXPR)
    summary_vec = func.to_tsvector("simple", func.coalesce(ProcessedArticle.summary, ""))
    if q and q.strip() and tsq is None:
        return {"total": 0, "articles": []}
    if tsq is not None:
        if scope == "title":
            query = query.where(title_vec.op("@@")(tsq))
        else:
            query = query.where(or_(article_vec.op("@@")(tsq), summary_vec.op("@@")(tsq)))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    # Dynamic weighted ranking computed in SQL, so ordering and pagination cover
    # the whole result set (same formula as calculate_composite_score)
    auth_col = func.coalesce(ProcessedArticle.authority_score, 0.5)
    tech_col = func.coalesce(ProcessedArticle.technical_depth_score, 0.5)
    urg_col = func.coalesce(ProcessedArticle.urgency_score, 0.5)
    cb_col = func.coalesce(ProcessedArticle.clickbait_score, 0.0)
    rank_expr = func.greatest(0.0, func.least(1.0,
        w_authority * auth_col + w_tech * tech_col + w_urgency * urg_col - w_clickbait * cb_col
    ))
    recency = func.coalesce(Article.published_at, Article.fetched_at).desc().nulls_last()

    sort = sort or ("relevance" if tsq is not None else "score")
    if sort == "relevance" and tsq is not None:
        text_rank = 2 * func.ts_rank(title_vec, tsq) + func.ts_rank(article_vec, tsq) + func.ts_rank(summary_vec, tsq)
        query = query.order_by(text_rank.desc(), recency)
    elif sort == "recent":
        query = query.order_by(recency)
    else:
        query = query.order_by(rank_expr.desc(), recency)

    if tsq is not None:
        query = query.add_columns(
            func.ts_headline("simple", Article.title, tsq, literal(TITLE_OPTS)).label("title_hl"),
            func.ts_headline("simple", func.coalesce(ProcessedArticle.summary, Article.content_raw, ""), tsq,
                             literal(HEADLINE_OPTS)).label("snippet"),
        )

    rows = (await db.execute(query.offset(offset).limit(limit))).all()

    scored_articles = []
    for row in rows:
        article, processed, source, cluster = row[:4]
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
        item = _article_dict(article, processed, source, cluster)
        item.update({
            "clickbait_score": cb,
            "authority_score": auth,
            "technical_depth_score": tech,
            "urgency_score": urg,
            "composite_score": dynamic_score,
            "importance_score": legacy_importance,
        })
        if tsq is not None:
            item["title_highlight"] = row.title_hl
            # Only a snippet that actually contains a match is useful
            item["snippet"] = row.snippet if row.snippet and HL_START in row.snippet else None
        scored_articles.append(item)
        
    return {"total": total, "articles": scored_articles}


def _article_dict(article, processed, source, cluster) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "url": article.url,
        "source_id": source.id,
        "source_name": article.publisher or source.name,
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
        "cluster_source_count": cluster.source_count if cluster else 1,
        "region": processed.region,
        "category_confidence": processed.category_confidence,
        "is_opinion": processed.is_opinion,
        "market_relevance": processed.market_relevance,
        "classifier": processed.classifier,
    }


@router.get("/{article_id}", response_model=ArticleResponse)
async def get_article(article_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(_base_query().where(Article.id == article_id))).first()
    if not row:
        raise HTTPException(status_code=404, detail=tr("Notizia non trovata", "Article not found"))
    return _article_dict(*row)
