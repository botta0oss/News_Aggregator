from typing import Literal, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev
from backend.ai.ratelimit import RateLimited
from backend.ai.usage import BudgetExceeded, feature
from backend.auth.deps import require_admin
from backend.api.schemas import (
    CalibrationResponse, MarketDetailResponse, MarketListResponse, MarketResponse,
    OpportunityResponse, PredictionResponse,
)
from backend.db.database import get_db, SessionLocal
from backend.db.models import Market, MarketArticleLink, MarketPrediction
from backend.config import settings
from backend.markets import bulk, polymarket, targeted
from backend.markets.forecast import brier_score
from backend.markets.service import get_market_evidence, predict_market, refresh_links, run_market_pipeline

router = APIRouter(prefix="/markets", tags=["markets"])
predictions_router = APIRouter(prefix="/predictions", tags=["predictions"])


async def _latest_predictions(db: AsyncSession, market_ids: list[str]) -> dict[str, MarketPrediction]:
    if not market_ids:
        return {}
    stmt = (
        select(MarketPrediction)
        .where(MarketPrediction.market_id.in_(market_ids))
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
    )
    return {p.market_id: p for p in (await db.execute(stmt)).scalars().all()}


async def _link_counts(db: AsyncSession, market_ids: list[str]) -> dict[str, int]:
    if not market_ids:
        return {}
    stmt = (
        select(MarketArticleLink.market_id, func.count())
        .where(MarketArticleLink.market_id.in_(market_ids))
        .group_by(MarketArticleLink.market_id)
    )
    return dict((await db.execute(stmt)).all())


def _market_url(market: Market) -> Optional[str]:
    if market.event_slug:
        return f"https://polymarket.com/event/{market.event_slug}"
    if market.slug:
        return f"https://polymarket.com/market/{market.slug}"
    return None


def _market_dict(market: Market, links: int = 0, prediction: Optional[MarketPrediction] = None) -> dict:
    return {
        "id": market.id,
        "question": market.question,
        "url": _market_url(market),
        "end_date": market.end_date,
        "yes_price": market.yes_price,
        "volume": market.volume,
        "liquidity": market.liquidity,
        "closed": market.closed,
        "resolved_yes": market.resolved_yes,
        "resolution": market.resolution,
        "linked_articles": links,
        "latest_prediction": PredictionResponse.model_validate(prediction) if prediction else None,
    }


async def _sync_job():
    async with SessionLocal() as session:
        await run_market_pipeline(session)


@router.post("/sync", dependencies=[Depends(require_admin)])
async def trigger_market_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(_sync_job)
    return {"status": "started", "message": "Polymarket sync triggered"}


@router.get("/predict-all", dependencies=[Depends(require_admin)])
async def predict_all_status(db: AsyncSession = Depends(get_db)):
    """Progress of the "evaluate every market" job and how many markets it would cover."""
    return {**bulk.snapshot(), "eligible": await bulk.count_eligible(db), "jev_rpm": settings.JEV_RPM,
            "jev_enabled": jev.is_enabled()}


@router.post("/predict-all", status_code=202, dependencies=[Depends(require_admin)])
async def predict_all_start(only_new: bool = Query(False, description="Solo mercati mai valutati o con notizie nuove dall'ultima previsione"),
                            refresh_first: bool = Query(True, description="Aggiorna prezzi e collegamenti prima di valutare")):
    """Starts a Jev forecast on every open market with recent news (one paid API call per market)."""
    if not jev.is_enabled():
        raise HTTPException(status_code=503, detail="TYPESAFE_API_KEY non è configurata")
    if not bulk.start(only_new=only_new, refresh_first=refresh_first):
        raise HTTPException(status_code=409, detail="Una valutazione di tutti i mercati è già in corso")
    return bulk.snapshot()


@router.post("/predict-all/stop", dependencies=[Depends(require_admin)])
async def predict_all_stop():
    """Stops the job after the market currently being evaluated."""
    bulk.stop()
    return bulk.snapshot()


@router.get("", response_model=MarketListResponse)
async def list_markets(
    q: Optional[str] = Query(None, description="Filtra per testo nella domanda del mercato"),
    only_linked: bool = Query(False, description="Solo mercati con notizie collegate"),
    include_closed: bool = Query(False),
    sort: Literal["volume", "end_date", "price", "signal", "edge", "news", "liquidity", "question"] = Query(
        "volume", description="Ordinamento: volume, scadenza, prezzo SÌ, ultimo segnale, edge, notizie collegate, liquidità, domanda"),
    order: Optional[Literal["asc", "desc"]] = Query(None, description="Direzione; default sensato per ogni campo"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Market).where(Market.multi_event_id.is_(None))  # multi-outcome events: /multi
    if not include_closed:
        query = query.where(Market.closed == False)  # noqa: E712
    if q:
        query = query.where(Market.question.ilike(f"%{q}%"))
    if only_linked:
        query = query.where(Market.id.in_(select(MarketArticleLink.market_id)))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    # Sorting in SQL so pagination covers the whole list
    latest = (
        select(MarketPrediction.market_id, MarketPrediction.created_at, MarketPrediction.edge)
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
        .subquery()
    )
    links = (
        select(MarketArticleLink.market_id, func.count().label("n"))
        .group_by(MarketArticleLink.market_id).subquery()
    )
    query = query.outerjoin(latest, latest.c.market_id == Market.id).outerjoin(links, links.c.market_id == Market.id)
    columns = {
        "volume": (Market.volume, "desc"),
        "end_date": (Market.end_date, "asc"),          # soonest first
        "price": (Market.yes_price, "desc"),
        "signal": (latest.c.created_at, "desc"),       # most recent forecast first
        "edge": (func.abs(latest.c.edge), "desc"),     # largest gap from the price first
        "news": (func.coalesce(links.c.n, 0), "desc"),
        "liquidity": (Market.liquidity, "desc"),
        "question": (func.lower(Market.question), "asc"),
    }
    column, default_order = columns[sort]
    direction = column.desc() if (order or default_order) == "desc" else column.asc()
    # Markets without a value (no forecast, no end date) always go last; volume breaks ties
    query = query.order_by(direction.nulls_last(), Market.volume.desc(), Market.id)

    markets = (await db.execute(query.offset(offset).limit(limit))).scalars().all()
    ids = [m.id for m in markets]
    counts, preds = await _link_counts(db, ids), await _latest_predictions(db, ids)
    return {"total": total, "markets": [_market_dict(m, counts.get(m.id, 0), preds.get(m.id)) for m in markets]}


@router.get("/{market_id}", response_model=MarketDetailResponse)
async def get_market(market_id: str, db: AsyncSession = Depends(get_db)):
    market = await db.get(Market, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    evidence = await get_market_evidence(db, market_id, limit=50)
    predictions = (await db.execute(
        select(MarketPrediction).where(MarketPrediction.market_id == market_id)
        .order_by(MarketPrediction.created_at.desc()).limit(50)
    )).scalars().all()
    counts = await _link_counts(db, [market_id])
    data = _market_dict(market, counts.get(market_id, 0), predictions[0] if predictions else None)
    data["description"] = market.description
    data["evidence"] = [
        {
            "article_id": article.id,
            "title": article.title,
            "url": article.url,
            "source_name": article.publisher or source.name,
            "published_at": article.published_at,
            "similarity": link.similarity,
            "match_score": link.match_score,
            "matched_terms": link.matched_terms or [],
            "evidence_score": item.score,
            "source_quality": item.quality,
            "corroboration": item.corroboration,
            "targeted": source.kind == "targeted",
            "relevance": link.relevance,
            "impact": link.impact,
            "impact_confidence": link.impact_confidence,
        }
        for item in evidence
        for link, article, _processed, source in [tuple(item)]
    ]
    data["predictions"] = [PredictionResponse.model_validate(p) for p in predictions]
    return data


@router.post("/{market_id}/predict", response_model=PredictionResponse, dependencies=[Depends(require_admin)])
async def predict(market_id: str, refresh_price: bool = Query(True), db: AsyncSession = Depends(get_db)):
    """Runs a Jev forecast now (costs one TypeSafe API call)."""
    market = await db.get(Market, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    if not jev.is_enabled():
        raise HTTPException(status_code=503, detail="TYPESAFE_API_KEY is not configured")
    if refresh_price:
        # Edge is only meaningful against the current price
        try:
            fresh = await polymarket.fetch_market(market_id)
            if fresh and fresh.yes_price is not None:
                market.yes_price = fresh.yes_price
                market.closed = fresh.closed
        except Exception:
            pass  # fall back to the last synced price
    if market.closed:
        raise HTTPException(status_code=409, detail="Market is closed")
    try:
        # Interactive request: do not keep the user waiting behind the background queue
        with feature("previsioni"):
            prediction = await predict_market(db, market, max_wait=10)
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RateLimited as e:
        raise HTTPException(status_code=429, detail=f"Jev ha raggiunto il limite di richieste: riprova tra {max(1, round(e.retry_in))} secondi.",
                            headers={"Retry-After": str(max(1, round(e.retry_in)))})
    except LookupError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return prediction


@router.post("/{market_id}/search-news", dependencies=[Depends(require_admin)])
async def search_news(market_id: str, db: AsyncSession = Depends(get_db)):
    """Searches news about this market now (Google News) and links what matches."""
    market = await db.get(Market, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    if not settings.TARGETED_NEWS_ENABLED:
        raise HTTPException(status_code=503, detail="La ricerca mirata è disattivata (TARGETED_NEWS_ENABLED=false)")
    try:
        added = await targeted.search_market(db, market)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Ricerca non riuscita: {e}")
    await refresh_links(db, market_ids=[market_id])
    linked = (await db.execute(
        select(func.count()).select_from(MarketArticleLink).where(MarketArticleLink.market_id == market_id)
    )).scalar() or 0
    return {"query": targeted.build_query(market.question), "added": added, "linked": linked}


@predictions_router.get("/opportunities", response_model=list[OpportunityResponse])
async def list_opportunities(
    min_edge: float = Query(0.05, ge=0.0, le=1.0),
    min_evidence: float = Query(0.0, ge=0.0, le=1.0),
    include_hold: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Open markets whose latest Jev forecast diverges from the market price, largest |edge| first."""
    latest = (
        select(MarketPrediction)
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
        .subquery()
    )
    stmt = (
        select(Market, MarketPrediction)
        .join(latest, latest.c.market_id == Market.id)
        .join(MarketPrediction, MarketPrediction.id == latest.c.id)
        .where(Market.closed == False, Market.multi_event_id.is_(None))  # noqa: E712
        .where(func.abs(MarketPrediction.edge) >= min_edge)
        .where(MarketPrediction.evidence_strength >= min_evidence)
    )
    if not include_hold:
        stmt = stmt.where(MarketPrediction.signal != "HOLD")
    rows = (await db.execute(stmt.order_by(func.abs(MarketPrediction.edge).desc()).limit(limit))).all()
    counts = await _link_counts(db, [m.id for m, _ in rows])
    return [
        {"market": _market_dict(m, counts.get(m.id, 0), p), "prediction": PredictionResponse.model_validate(p)}
        for m, p in rows
    ]


@predictions_router.get("/calibration", response_model=CalibrationResponse)
async def calibration(db: AsyncSession = Depends(get_db)):
    """Brier score (lower is better) of the latest pre-resolution forecast vs the market price."""
    latest = (
        select(MarketPrediction)
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
        .subquery()
    )
    rows = (await db.execute(
        select(Market.resolved_yes, latest.c.market_probability, latest.c.model_probability, latest.c.blended_probability)
        .join(latest, latest.c.market_id == Market.id)
        .where(Market.resolved_yes.is_not(None))
    )).all()
    return {
        "resolved_markets": len(rows),
        "brier_market": brier_score((r.market_probability, r.resolved_yes) for r in rows),
        "brier_model": brier_score((r.model_probability, r.resolved_yes) for r in rows),
        "brier_blended": brier_score((r.blended_probability, r.resolved_yes) for r in rows),
    }
