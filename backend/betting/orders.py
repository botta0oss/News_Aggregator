"""Maker limit orders for the simulated portfolio.

Taking the ask pays half the spread and the taker fee on every bet. A maker order instead
waits in the book one tick above the best bid (never above the maximum price of the
evaluation, never touching the ask): no fee, a better price. The cost is that it fills only
when someone sells down to it, which happens more often when the price is moving against the
bet (adverse selection), and some orders never fill. Both effects are simulated:
- an order fills, at its limit price, when a market sync shows the side's best ask at or
  below the limit (a seller crossed it); prices between two syncs are not seen, so a brief dip
  that recovers is missed (conservative);
- it expires after MAKER_ORDER_TTL_HOURS, is cancelled by a new forecast on the market (which
  places a new one if it still says buy), by exclusions or by the closing-line guard's pause.
Money of pending orders is reserved: it is not available cash and counts in the exposure.
"""
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.models import Market, PaperBet, PaperOrder
from backend.i18n import tr

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def maker_mode() -> bool:
    return settings.PAPER_ORDER_MODE.lower() == "maker"


def side_ask(market: Market, side: str) -> Optional[float]:
    """Best ask of `side` known from the last sync (Gamma's top of book of the YES token)."""
    if side == "YES":
        return market.best_ask
    return 1.0 - market.best_bid if market.best_bid is not None else None


def maker_price(market: Market, ev) -> Optional[float]:
    """Price of the limit order: one tick above the side's best bid, below its best ask, not above
    the evaluation's maximum price; rounded down to the tick. None if nothing sensible is left."""
    from backend.betting.plans import known_bid
    tick = settings.MAKER_TICK
    price = ev.limit_price
    bid = known_bid(market, ev.side)
    ask = ev.best_price if ev.best_price is not None else side_ask(market, ev.side)
    if bid is not None:
        price = min(price, bid + tick)
    if ask is not None:
        price = min(price, ask - tick)
    price = math.floor(round(price / tick, 6)) * tick
    if price < max(tick, settings.LONGSHOT_MIN_PRICE):
        return None
    return round(price, 4)


async def place_order(db: AsyncSession, market: Market, prediction, ev, preset: str,
                      placed_by: str = "auto") -> Optional[PaperOrder]:
    price = maker_price(market, ev)
    if price is None or ev.shares <= 0:
        return None
    order = PaperOrder(
        market_id=market.id, prediction_id=getattr(prediction, "id", None), side=ev.side, shares=ev.shares,
        limit_price=price, taker_price=ev.avg_price, p_side=ev.p_side, p_conservative=ev.p_conservative,
        preset=preset, placed_by=placed_by, expires_at=_now() + timedelta(hours=settings.MAKER_ORDER_TTL_HOURS),
    )
    db.add(order)
    await db.commit()
    logger.info(f"Paper limit order {ev.side} {ev.shares:.1f} shares at {price:.3f} on {market.id}")
    return order


async def pending_on(db: AsyncSession, market_id: str) -> list[PaperOrder]:
    return list((await db.execute(select(PaperOrder).where(
        PaperOrder.market_id == market_id, PaperOrder.status == "pending"))).scalars().all())


def _close(order: PaperOrder, status: str, reason: Optional[str] = None) -> None:
    order.status, order.reason, order.closed_at = status, reason, _now()


async def cancel_on_market(db: AsyncSession, market_id: str, reason: str) -> int:
    orders = await pending_on(db, market_id)
    for order in orders:
        _close(order, "cancelled", reason)
    if orders:
        await db.commit()
    return len(orders)


async def cancel(db: AsyncSession, order: PaperOrder, reason: str) -> None:
    _close(order, "cancelled", reason)
    await db.commit()


async def fill(db: AsyncSession, order: PaperOrder, at: Optional[datetime] = None) -> PaperBet:
    """The order becomes a bet at its limit price, without fee (makers pay none), at time `at`
    (when the price history shows it filled) or now."""
    at = at or _now()
    bet = PaperBet(
        market_id=order.market_id, prediction_id=order.prediction_id, side=order.side, shares=order.shares,
        avg_price=order.limit_price, stake=order.shares * order.limit_price, fee=0.0, p_side=order.p_side,
        p_conservative=order.p_conservative, expected_profit=order.shares * (order.p_side - order.limit_price),
        preset=order.preset, placed_by=order.placed_by, entry="maker", created_at=at,
    )
    db.add(bet)
    await db.flush()
    order.status, order.closed_at, order.bet_id = "filled", at, bet.id
    return bet


async def process_orders(db: AsyncSession) -> dict:
    """After a market sync: fills, expires or cancels the pending orders."""
    from backend.betting.portfolio import excluded_reason, get_settings
    s = await get_settings(db)
    rows = (await db.execute(select(PaperOrder, Market).join(Market, Market.id == PaperOrder.market_id)
                             .where(PaperOrder.status == "pending"))).all()
    stats = {"filled": 0, "expired": 0, "cancelled": 0}
    for order, market in rows:
        side_of = (lambda p: p if order.side == "YES" else 1.0 - p)
        open_bet = (await db.execute(select(PaperBet.id).where(
            PaperBet.market_id == market.id, PaperBet.status == "open"))).first()
        if market.closed or market.resolution is not None:
            last = market.last_trading_price if market.last_trading_price is not None else market.yes_price
            # A market that closed against the side went through the order on its way down
            if last is not None and side_of(last) <= order.limit_price and open_bet is None:
                await fill(db, order)
                stats["filled"] += 1
            else:
                _close(order, "expired", tr("Mercato chiuso", "Market closed"))
                stats["expired"] += 1
            continue
        if open_bet is not None:
            _close(order, "cancelled", tr("C'è già una scommessa aperta sul mercato", "There is already an open bet on the market"))
            stats["cancelled"] += 1
            continue
        if order.placed_by == "auto" and (s.paused_at is not None or await excluded_reason(db, market)):
            _close(order, "cancelled", tr("Scommesse automatiche in pausa o mercato escluso",
                                          "Automatic bets paused or market excluded"))
            stats["cancelled"] += 1
            continue
        ask = side_ask(market, order.side)
        if ask is None and market.yes_price is not None:
            ask = side_of(market.yes_price) + settings.DEFAULT_SPREAD / 2
        if ask is not None and ask <= order.limit_price + 1e-9:
            await fill(db, order)
            stats["filled"] += 1
            continue
        traded_at = await traded_through(order, market) if settings.MAKER_FILL_FROM_HISTORY else None
        if traded_at is not None:
            await fill(db, order, at=traded_at)
            stats["filled"] += 1
        elif _now() >= order.expires_at:
            _close(order, "expired", tr("Non eseguito in tempo", "Not filled in time"))
            stats["expired"] += 1
    await db.commit()
    if any(stats.values()):
        logger.info(f"Paper limit orders: {stats}")
    return stats


async def traded_through(order: PaperOrder, market: Market) -> Optional[datetime]:
    """First time, while the order was waiting, the side traded below its limit (minute price
    history of the CLOB): someone sold under our bid, so the order ahead of them in the book was
    hit. Strictly below, because at the limit itself others may have been ahead in the queue.
    None without a token, without points or if the CLOB cannot be reached."""
    from backend.markets import polymarket
    token = market.yes_token_id if order.side == "YES" else market.no_token_id
    if not token:
        return None
    end = min(_now(), order.expires_at)
    if end <= order.created_at:
        return None
    try:
        history = await polymarket.fetch_price_history(token, order.created_at, end, fidelities=(1, 60))
    except Exception as e:
        logger.info(f"Price history unavailable for order {order.id}: {e}")
        return None
    for t, price in history:
        if order.created_at <= t <= end and price < order.limit_price - 1e-9:
            return t
    return None


async def reserved(db: AsyncSession, *conds) -> float:
    """Money of the pending orders matching `conds` (joined with Market)."""
    stmt = (select(func.coalesce(func.sum(PaperOrder.shares * PaperOrder.limit_price), 0.0))
            .select_from(PaperOrder).join(Market, Market.id == PaperOrder.market_id)
            .where(PaperOrder.status == "pending", *conds))
    return (await db.execute(stmt)).scalar()


async def stats(db: AsyncSession) -> dict:
    """Counts by status, fill rate and what the filled orders saved against taking the ask."""
    counts = dict((await db.execute(select(PaperOrder.status, func.count()).group_by(PaperOrder.status))).all())
    filled, expired = counts.get("filled", 0), counts.get("expired", 0)
    saved = (await db.execute(select(func.coalesce(func.sum(
        PaperOrder.shares * (PaperOrder.taker_price - PaperOrder.limit_price)), 0.0))
        .where(PaperOrder.status == "filled", PaperOrder.taker_price.is_not(None)))).scalar()
    return {
        "mode": "maker" if maker_mode() else "taker",
        "counts": {k: counts.get(k, 0) for k in ("pending", "filled", "expired", "cancelled")},
        "fill_rate": filled / (filled + expired) if filled + expired else None,
        "saved": saved,
        "reserved": await reserved(db),
    }


def order_dict(order: PaperOrder, market: Optional[Market] = None) -> dict:
    d = {k: getattr(order, k) for k in ("id", "market_id", "side", "shares", "limit_price", "taker_price", "p_side",
                                        "p_conservative", "preset", "placed_by", "status", "reason", "created_at",
                                        "expires_at", "closed_at", "bet_id")}
    d["outlay"] = order.outlay
    if market is not None:
        d["question"] = market.question
        d["best_ask"] = side_ask(market, order.side)
    return d
