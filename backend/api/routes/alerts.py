from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev
from backend.alerts import service, telegram
from backend.auth.deps import require_admin
from backend.config import settings
from backend.db.database import get_db
from backend.db.models import Alert, Article, Market, Source

router = APIRouter(prefix="/alerts", tags=["alerts"])
admin = [Depends(require_admin)]


class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    categories: Optional[list[str]] = None
    min_match: Optional[float] = None
    max_news_age_hours: Optional[float] = None
    daily_budget: Optional[int] = None
    cooldown_hours: Optional[float] = None
    min_verdict: Optional[Literal["GO", "SMALL"]] = None
    telegram_enabled: Optional[bool] = None
    quiet_start: Optional[int] = None
    quiet_end: Optional[int] = None


def _settings_out(s) -> dict:
    return {
        "enabled": s.enabled, "categories": s.categories or [], "min_match": s.min_match,
        "max_news_age_hours": s.max_news_age_hours, "daily_budget": s.daily_budget,
        "cooldown_hours": s.cooldown_hours, "min_verdict": s.min_verdict,
        "telegram_enabled": s.telegram_enabled, "quiet_start": s.quiet_start, "quiet_end": s.quiet_end,
        "telegram_configured": telegram.is_configured(), "jev_enabled": jev.is_enabled(),
        "scan_minutes": settings.ALERT_SCAN_MINUTES, "ingest_minutes": settings.INGEST_INTERVAL_MINUTES,
        "timezone": settings.ALERT_TIMEZONE,
    }


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return _settings_out(await service.get_settings(db))


@router.put("/settings", dependencies=admin)
async def put_settings(body: SettingsIn, db: AsyncSession = Depends(get_db)):
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    # Quiet hours can be cleared with null; other fields ignore null
    data = {k: v for k, v in data.items() if v is not None or k in ("quiet_start", "quiet_end")}
    try:
        return _settings_out(await service.update_settings(db, data))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/test-telegram", dependencies=admin)
async def test_telegram():
    try:
        await telegram.send_message("✅ <b>News × Markets</b>: le notifiche delle allerte arrivano qui.")
    except telegram.TelegramError as e:
        raise HTTPException(status_code=502 if telegram.is_configured() else 503, detail=str(e))
    return {"ok": True}


@router.post("/run", dependencies=admin)
async def run_now(db: AsyncSession = Depends(get_db)):
    """Checks the news linked since the last check now (the scheduler does it on its own)."""
    return await service.run_alerts(db)


@router.get("/summary")
async def get_summary(days: int = Query(30, ge=1, le=365), db: AsyncSession = Depends(get_db)):
    return await service.summary(db, days)


@router.get("")
async def list_alerts(
    kind: Literal["opportunities", "all"] = Query("opportunities"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Alert, Market, Article, Source)
        .join(Market, Market.id == Alert.market_id)
        .outerjoin(Article, Article.id == Alert.article_id)
        .outerjoin(Source, Source.id == Article.source_id)
        .order_by(Alert.created_at.desc())
        .limit(limit)
    )
    if kind == "opportunities":
        stmt = stmt.where(Alert.opportunity == True)  # noqa: E712
    out = []
    for alert, market, article, source in (await db.execute(stmt)).all():
        followups = {k: service.followup_price(alert, k) for k in service.CHECKPOINTS}
        out.append({
            "id": alert.id,
            "created_at": alert.created_at,
            "market": {"id": market.id, "question": market.question, "yes_price": market.yes_price,
                       "closed": market.closed, "resolved_yes": market.resolved_yes},
            "news": {"title": article.title, "url": article.url, "source": article.publisher or (source.name if source else None),
                     "published_at": alert.news_at} if article else None,
            "price": alert.price,
            "side": alert.side, "verdict": alert.verdict, "signal": alert.signal,
            "edge": alert.edge, "blended": alert.blended, "outlay": alert.outlay, "limit_price": alert.limit_price,
            "opportunity": alert.opportunity,
            "notified_at": alert.notified_at, "notify_error": alert.notify_error,
            "followups": followups,
            "moves": {k: service.favorable_move(alert, p) for k, p in followups.items()},
            "now_move": service.favorable_move(alert, market.yes_price),
        })
    return out
