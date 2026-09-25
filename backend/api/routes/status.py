from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev, usage
from backend.config import settings
from backend.db.database import get_db
from backend.db.models import Article, Market, MarketArticleLink, MarketPrediction, ProcessedArticle, Source
from backend.api.schemas import StatusResponse

router = APIRouter(prefix="/status", tags=["status"])


@router.get("", response_model=StatusResponse)
async def get_status(db: AsyncSession = Depends(get_db)):
    """Configuration flags and counters used by the dashboard."""
    async def count(stmt):
        return (await db.execute(stmt)).scalar() or 0

    open_markets = select(Market.id).where(Market.closed == False, Market.multi_event_id.is_(None))  # noqa: E712
    return {
        "jev_enabled": jev.is_enabled(),
        "polymarket_enabled": settings.POLYMARKET_ENABLED,
        "prediction_auto": settings.PREDICTION_AUTO,
        "targeted_news_enabled": settings.TARGETED_NEWS_ENABLED,
        "usage": await usage.today_status(),
        "min_edge": settings.MIN_EDGE,
        "min_evidence": settings.MIN_EVIDENCE,
        "model_weight_max": settings.MODEL_WEIGHT_MAX,
        "model_disagreement_logit": settings.MODEL_DISAGREEMENT_LOGIT,
        "forecast_max_age_hours": settings.FORECAST_MAX_AGE_HOURS,
        "forecast_max_price_move": settings.FORECAST_MAX_PRICE_MOVE,
        "exclude_price_markets": settings.EXCLUDE_PRICE_MARKETS,
        "blend_method": settings.BLEND_METHOD,
        "jev_calib_a": settings.JEV_CALIB_A,
        "jev_calib_b": settings.JEV_CALIB_B,
        "jev_samples": settings.JEV_SAMPLES,
        "kelly_fraction": settings.KELLY_FRACTION,
        "market_match_threshold": settings.MARKET_MATCH_THRESHOLD,
        "market_news_window_hours": settings.MARKET_NEWS_WINDOW_HOURS,
        "market_max_articles": settings.MARKET_MAX_ARTICLES,
        "sources_active": await count(select(func.count(Source.id)).where(Source.active == True, Source.kind == "feed")),  # noqa: E712
        "sources_with_errors": await count(select(func.count(Source.id)).where(Source.active == True, Source.kind == "feed", Source.last_status == "error")),  # noqa: E712
        "articles": await count(select(func.count(Article.id))),
        "processed_articles": await count(select(func.count(ProcessedArticle.id))),
        "last_article_at": (await db.execute(select(func.max(Article.fetched_at)))).scalar(),
        "open_markets": await count(select(func.count()).select_from(open_markets.subquery())),
        "linked_markets": await count(
            select(func.count(func.distinct(MarketArticleLink.market_id)))
            .where(MarketArticleLink.market_id.in_(open_markets))
        ),
        "predictions": await count(select(func.count(MarketPrediction.id))),
        "last_prediction_at": (await db.execute(select(func.max(MarketPrediction.created_at)))).scalar(),
    }
