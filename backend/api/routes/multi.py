from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev
from backend.ai.ratelimit import RateLimited
from backend.ai.usage import BudgetExceeded, feature
from backend.auth.deps import require_admin
from backend.db.database import get_db
from backend.db.models import MultiArticleLink, MultiEvent, MultiOutcome, MultiPrediction
from backend.multi import service

router = APIRouter(prefix="/multi", tags=["multi"])


def _url(e: MultiEvent) -> Optional[str]:
    return f"https://polymarket.com/event/{e.slug}" if e.slug else None


def _prediction_out(p: Optional[MultiPrediction]) -> Optional[dict]:
    if p is None:
        return None
    return {"id": p.id, "created_at": p.created_at, "model_name": p.model_name, "evidence_strength": p.evidence_strength,
            "model_weight": p.model_weight, "outcomes": p.outcomes, "best_outcome_id": p.best_outcome_id,
            "best_edge": p.best_edge, "signal": p.signal, "article_count": p.article_count,
            "economics": p.economics or {}}


async def _latest(db: AsyncSession, ids: list[str]) -> dict:
    if not ids:
        return {}
    rows = (await db.execute(
        select(MultiPrediction).where(MultiPrediction.event_id.in_(ids))
        .distinct(MultiPrediction.event_id).order_by(MultiPrediction.event_id, MultiPrediction.created_at.desc())
    )).scalars().all()
    return {p.event_id: p for p in rows}


def _event_out(e: MultiEvent, outcomes: list, links: int, pred: Optional[MultiPrediction], top: Optional[int] = None,
               arbitrage: Optional[dict] = None) -> dict:
    return {
        "arbitrage": arbitrage,
        "id": e.id, "title": e.title, "url": _url(e), "end_date": e.end_date, "volume": e.volume,
        "liquidity": e.liquidity, "closed": e.closed, "winner_id": e.winner_id, "linked_articles": links,
        "outcome_count": len(outcomes),
        "outcomes": [{"id": o.id, "label": o.label, "price": o.yes_price, "closed": o.closed, "resolved_yes": o.resolved_yes}
                     for o in (outcomes[:top] if top else outcomes)],
        "latest_prediction": _prediction_out(pred),
    }


@router.get("")
async def list_events(
    q: Optional[str] = Query(None),
    sort: Literal["volume", "end_date", "edge", "signal", "news"] = Query("volume"),
    include_closed: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MultiEvent)
    if not include_closed:
        stmt = stmt.where(MultiEvent.closed == False)  # noqa: E712
    if q:
        stmt = stmt.where(MultiEvent.title.ilike(f"%{q}%") | MultiEvent.id.in_(
            select(MultiOutcome.event_id).where(MultiOutcome.label.ilike(f"%{q}%"))))
    events = (await db.execute(stmt.order_by(MultiEvent.volume.desc()).limit(500))).scalars().all()
    ids = [e.id for e in events]
    preds = await _latest(db, ids)
    links = dict((await db.execute(
        select(MultiArticleLink.event_id, func.count()).where(MultiArticleLink.event_id.in_(ids)).group_by(MultiArticleLink.event_id)
    )).all()) if ids else {}
    outcomes: dict = {}
    if ids:
        for o in (await db.execute(select(MultiOutcome).where(MultiOutcome.event_id.in_(ids))
                                   .order_by(MultiOutcome.yes_price.desc().nulls_last()))).scalars().all():
            outcomes.setdefault(o.event_id, []).append(o)
    key = {
        "volume": lambda e: -(e.volume or 0),
        "end_date": lambda e: (e.end_date is None, e.end_date or 0),
        "edge": lambda e: -abs(preds[e.id].best_edge or 0) if e.id in preds else 1,
        "signal": lambda e: -(preds[e.id].created_at.timestamp()) if e.id in preds else 1,
        "news": lambda e: -links.get(e.id, 0),
    }[sort]
    events = sorted(events, key=key)[:limit]
    arb = await service.arbitrage_for(db, [e.id for e in events if not e.closed])
    return {"total": len(ids), "events": [_event_out(e, outcomes.get(e.id, []), links.get(e.id, 0), preds.get(e.id), top=5,
                                                     arbitrage=arb.get(e.id)) for e in events]}


@router.get("/opportunities")
async def opportunities(
    min_edge: float = Query(0.05, ge=0.0, le=1.0),
    min_evidence: float = Query(0.0, ge=0.0, le=1.0),
    include_hold: bool = Query(False),
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Open events whose latest forecast finds an outcome far from its price (under- or overpriced),
    largest edge first."""
    events = (await db.execute(select(MultiEvent).where(MultiEvent.closed == False))).scalars().all()  # noqa: E712
    preds = await _latest(db, [e.id for e in events])
    chosen = [e for e in events if e.id in preds
              and abs(preds[e.id].best_edge or 0) >= min_edge and preds[e.id].evidence_strength >= min_evidence
              and (include_hold or preds[e.id].signal in ("BUY_YES", "BUY_NO"))]
    chosen.sort(key=lambda e: -abs(preds[e.id].best_edge or 0))
    chosen = chosen[:limit]
    out = []
    for e in chosen:
        outcomes = await service.outcomes_of(db, e.id)
        links = (await db.execute(select(func.count()).select_from(MultiArticleLink).where(MultiArticleLink.event_id == e.id))).scalar() or 0
        out.append(_event_out(e, outcomes, links, preds[e.id], top=5))
    return out


@router.get("/{event_id}")
async def get_event(event_id: str, db: AsyncSession = Depends(get_db)):
    event = await db.get(MultiEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    outcomes = await service.outcomes_of(db, event_id)
    preds = (await db.execute(select(MultiPrediction).where(MultiPrediction.event_id == event_id)
                              .order_by(MultiPrediction.created_at.desc()).limit(30))).scalars().all()
    evidence = await service.get_evidence(db, event_id, limit=50)
    links = (await db.execute(select(func.count()).select_from(MultiArticleLink).where(MultiArticleLink.event_id == event_id))).scalar() or 0
    arb = await service.arbitrage_for(db, [event_id]) if not event.closed else {}
    out = _event_out(event, outcomes, links, preds[0] if preds else None, arbitrage=arb.get(event_id))
    out["description"] = event.description
    out["predictions"] = [_prediction_out(p) for p in preds]
    out["evidence"] = [{
        "article_id": item.article.id, "title": item.article.title, "url": item.article.url,
        "source_name": item.article.publisher or item.source.name, "published_at": item.article.published_at,
        "match_score": item.link.match_score, "matched_terms": item.link.matched_terms or [],
        "corroboration": item.corroboration, "relevance": item.link.relevance, "favours": item.link.impact,
    } for item in evidence]
    return out


@router.post("/{event_id}/predict", dependencies=[Depends(require_admin)])
async def predict(event_id: str, db: AsyncSession = Depends(get_db)):
    event = await db.get(MultiEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    if not jev.is_enabled():
        raise HTTPException(status_code=503, detail="TYPESAFE_API_KEY non è configurata")
    if event.closed:
        raise HTTPException(status_code=409, detail="L'evento è chiuso")
    try:
        with feature("piu_esiti"):
            return _prediction_out(await service.predict_event(db, event, max_wait=10))
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RateLimited as e:
        raise HTTPException(status_code=429, detail=f"Jev ha raggiunto il limite di richieste: riprova tra {max(1, round(e.retry_in))} secondi.")
    except LookupError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
