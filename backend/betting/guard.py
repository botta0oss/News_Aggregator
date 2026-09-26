"""Closing-line guard: stops automatic bets when the market keeps moving against them.

The result of a bet takes weeks to arrive; the price moves within hours. If, over the latest
bets, the price of the side bought has on average gone down since the purchase (negative
closing line value), the signals are not ahead of the market and betting on is just paying
the spread. Then:
- per category (at least CLV_GUARD_CATEGORY_MIN_BETS bets): the category is excluded from
  automatic bets, visibly, in the exclusions;
- overall (at least CLV_GUARD_MIN_BETS bets): automatic bets pause, with a Telegram message,
  until someone resumes them. Bets placed before a resume are not counted again.
Manual bets are never blocked, but they count.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.betting import clv
from backend.config import settings
from backend.db.models import Market, PaperBet, PaperExclusion
from backend.i18n import tr

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def bet_move(bet: PaperBet, market: Market) -> Optional[float]:
    """How much the price of the side bought has moved since the purchase (points, > 0 = in favour):
    to the closing price once the market has closed, to the current price before."""
    price = market.last_trading_price if market.closed and market.last_trading_price is not None else market.yes_price
    if price is None:
        return None
    return clv.clv(bet.avg_price, price, bet.side)


async def recent_moves(db: AsyncSession, since: datetime, category: Optional[str] = None) -> list[float]:
    """Moves of the latest CLV_GUARD_WINDOW bets placed after `since` and old enough to have moved."""
    stmt = (select(PaperBet, Market).join(Market, Market.id == PaperBet.market_id)
            .where(PaperBet.status != "excluded", PaperBet.created_at >= since,
                   PaperBet.created_at <= _now() - timedelta(hours=settings.CLV_GUARD_MIN_AGE_HOURS))
            .order_by(PaperBet.created_at.desc()).limit(settings.CLV_GUARD_WINDOW))
    if category is not None:
        stmt = stmt.where(Market.category == category)
    moves = [bet_move(b, m) for b, m in (await db.execute(stmt)).all()]
    return [m for m in moves if m is not None]


def _pts(x: float) -> str:
    from backend.i18n import dec
    return dec(x * 100, 1)


async def status(db: AsyncSession) -> dict:
    """What the guard sees now (for the portfolio page)."""
    from backend.betting.portfolio import get_settings
    s = await get_settings(db)
    moves = await recent_moves(db, s.guard_since or s.started_at)
    return {
        "enabled": settings.CLV_GUARD_ENABLED, "paused": s.paused_at is not None, "paused_at": s.paused_at,
        "reason": s.paused_reason, "n": len(moves), "min_bets": settings.CLV_GUARD_MIN_BETS,
        "avg_move": sum(moves) / len(moves) if moves else None, "threshold": settings.CLV_GUARD_MIN_AVG,
    }


async def allows_auto_bet(db: AsyncSession, market: Market) -> bool:
    """Checks the guard before an automatic bet; pauses or excludes the category when it trips."""
    if not settings.CLV_GUARD_ENABLED:
        return True
    from backend.betting.portfolio import get_settings
    s = await get_settings(db)
    if s.paused_at is not None:
        return False
    since = s.guard_since or s.started_at

    if market.category:
        moves = await recent_moves(db, since, market.category)
        if len(moves) >= settings.CLV_GUARD_CATEGORY_MIN_BETS and sum(moves) / len(moves) < settings.CLV_GUARD_MIN_AVG:
            avg = sum(moves) / len(moves)
            label = tr(f"Pausa automatica: dopo le ultime {len(moves)} scommesse il prezzo si è mosso contro di {_pts(-avg)} punti in media",
                       f"Automatic pause: after the latest {len(moves)} bets the price moved against them by {_pts(-avg)} points on average")
            exists = (await db.execute(select(PaperExclusion).where(
                PaperExclusion.kind == "category", PaperExclusion.value == market.category))).scalar_one_or_none()
            if exists is None:
                db.add(PaperExclusion(kind="category", value=market.category, label=label, source="guard"))
                await db.commit()
                logger.warning(f"CLV guard: category {market.category} excluded ({avg:+.3f} over {len(moves)} bets)")
                await _notify(tr(f"categoria {market.category} esclusa dalle scommesse automatiche. {label}.",
                                 f"category {market.category} excluded from automatic bets. {label}."))
            return False

    moves = await recent_moves(db, since)
    if len(moves) >= settings.CLV_GUARD_MIN_BETS:
        avg = sum(moves) / len(moves)
        if avg < settings.CLV_GUARD_MIN_AVG:
            s.paused_at = _now()
            s.paused_reason = tr(
                f"Dopo le ultime {len(moves)} scommesse il prezzo si è mosso contro di {_pts(-avg)} punti in media: "
                "i segnali non anticipano il mercato.",
                f"After the latest {len(moves)} bets the price moved against them by {_pts(-avg)} points on average: "
                "the signals are not ahead of the market.")
            await db.commit()
            logger.warning(f"CLV guard: automatic bets paused ({avg:+.3f} over {len(moves)} bets)")
            await _notify(tr("scommesse automatiche in pausa. ", "automatic bets paused. ") + s.paused_reason)
            return False
    return True


async def resume(db: AsyncSession) -> None:
    """Resumes automatic bets; the bets so far are not counted again."""
    from backend.betting.portfolio import get_settings
    s = await get_settings(db)
    s.paused_at, s.paused_reason, s.guard_since = None, None, _now()
    await db.commit()


async def _notify(text: str) -> None:
    try:
        from backend.alerts import telegram
        if telegram.is_configured():
            await telegram.send_message(f"⏸ <b>News × Markets</b>: {telegram.escape(text)}")
    except Exception as e:  # a failed message must not break the betting flow
        logger.info(f"CLV guard notification failed: {e}")
