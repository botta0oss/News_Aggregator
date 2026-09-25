"""Gathers what the strategy needs from the database and the order book (see strategy.py)."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.betting import fees
from backend.betting.economics import Evaluation
from backend.betting.profiles import RiskProfile
from backend.betting.strategy import Forecast, Plan, build_plan
from backend.config import settings
from backend.db.models import Article, Market, MarketArticleLink, PaperBet
from backend.markets import polymarket
from backend.markets.forecast import calibrate, model_weight

logger = logging.getLogger(__name__)


def half_spread(market: Market) -> float:
    """Half the bid-ask spread: buying pays it above the mid price, selling loses it below."""
    if market.best_bid is not None and market.best_ask is not None and market.best_ask > market.best_bid:
        return (market.best_ask - market.best_bid) / 2
    return settings.DEFAULT_SPREAD / 2


def forecast_of(prediction, sigma: float) -> Forecast:
    """The forecast as it was computed: calibrated Jev, its weight, the pooling method.
    Forecasts made before calibration and log-odds pooling were linear and uncalibrated."""
    method = getattr(prediction, "blend_method", None) or "linear"
    cal = getattr(prediction, "calibrated_probability", None)
    if cal is None:
        cal = prediction.model_probability if method == "linear" else calibrate(prediction.model_probability)
    w = prediction.model_weight if prediction.model_weight is not None else model_weight(prediction.evidence_strength)
    return Forecast(model=cal, weight=w, evidence=prediction.evidence_strength, sigma=sigma, method=method)


async def best_bid(market: Market, side: str) -> Optional[float]:
    """What one share of `side` would fetch now: the book's best bid, else Gamma's top of book."""
    token = market.yes_token_id if side == "YES" else market.no_token_id
    if token:
        try:
            book = await polymarket.fetch_order_book(token)
            if book.bids:
                return book.bids[0][0]
        except Exception as e:
            logger.info(f"Order book unavailable for {market.id} ({side}): {e}")
    return known_bid(market, side)


def known_bid(market: Market, side: str) -> Optional[float]:
    """Best bid of `side` from the last sync (no network): Gamma's top of book, else mid − half spread."""
    if side == "YES" and market.best_bid is not None:
        return market.best_bid
    if side == "NO" and market.best_ask is not None:
        return 1.0 - market.best_ask
    if market.yes_price is None:
        return None
    mid = market.yes_price if side == "YES" else 1.0 - market.yes_price
    return max(0.0, mid - settings.DEFAULT_SPREAD / 2)


async def news_tally(db: AsyncSession, market: Market) -> Optional[dict]:
    """Recent news Jev judged for this market: how many push YES up or down, with the best titles."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    if market.multi_event_id:
        from backend.db.models import MultiArticleLink, MultiOutcome
        outcome = await db.get(MultiOutcome, market.id)
        if outcome is None:
            return None
        rows = (await db.execute(
            select(MultiArticleLink.impact, Article.title).join(Article, Article.id == MultiArticleLink.article_id)
            .where(MultiArticleLink.event_id == market.multi_event_id, MultiArticleLink.impact.is_not(None),
                   Article.fetched_at >= since)
            .order_by(MultiArticleLink.match_score.desc()).limit(50)
        )).all()
        # A news item favouring this outcome raises its YES; one favouring another outcome lowers it
        rows = [("raises_yes" if impact == outcome.label else "lowers_yes", title) for impact, title in rows]
    else:
        rows = (await db.execute(
            select(MarketArticleLink.impact, Article.title).join(Article, Article.id == MarketArticleLink.article_id)
            .where(MarketArticleLink.market_id == market.id, MarketArticleLink.impact.is_not(None),
                   Article.fetched_at >= since)
            .order_by(MarketArticleLink.match_score.desc().nulls_last()).limit(50)
        )).all()
    tally: dict = {"raises_yes": 0, "lowers_yes": 0, "neutral": 0}
    for impact, title in rows:
        if impact in tally:
            tally[impact] += 1
            if impact != "neutral":
                tally.setdefault(f"{impact}_titles", []).append(title)
    return tally


async def plan_for(db: AsyncSession, market: Market, prediction, ev: Evaluation, profile: RiskProfile,
                   track: Optional[dict] = None, excluded_by: Optional[str] = None,
                   position: Optional[PaperBet] = None) -> Plan:
    """The plan for the latest forecast of a market, with the current prices and portfolio."""
    if position is None:
        position = (await db.execute(
            select(PaperBet).where(PaperBet.market_id == market.id, PaperBet.status == "open")
        )).scalar_one_or_none()
    fee_bps = market.taker_fee_bps if market.taker_fee_bps is not None else fees.category_rate(market.category) * 10_000
    sell_bid = await best_bid(market, position.side) if position else None
    return build_plan(
        ev=ev.as_dict(), fc=forecast_of(prediction, ev.sigma), signal=prediction.signal,
        market_price=market.yes_price, profile=profile, fee_bps=fee_bps, days=ev.days,
        min_edge=settings.MIN_EDGE, risk_free=settings.RISK_FREE_RATE,
        position={"side": position.side, "shares": position.shares, "avg_price": position.avg_price} if position else None,
        sell_bid=sell_bid, tally=await news_tally(db, market), track=track,
        market_closed=bool(market.closed), excluded_by=excluded_by, half_spread=half_spread(market),
    )


def exit_plan(prediction, market: Market, side: str, profile: RiskProfile, days: float, sigma: float) -> dict:
    """Light plan for an open position (no order-book call): the sale target and whether it is
    reached at the last known bid, or the forecast has turned against the position."""
    from backend.betting.strategy import sell_above
    fee_bps = market.taker_fee_bps if market.taker_fee_bps is not None else fees.category_rate(market.category) * 10_000
    hurdle = settings.RISK_FREE_RATE + profile.min_apr_premium
    target = sell_above(forecast_of(prediction, sigma), side, fee_bps, days, hurdle, half_spread(market))
    if side == "YES":
        bid = market.best_bid if market.best_bid is not None else market.yes_price
    else:
        bid = 1 - market.best_ask if market.best_ask is not None else (1 - market.yes_price if market.yes_price is not None else None)
    flipped = (prediction.signal == "BUY_NO" and side == "YES") or (prediction.signal == "BUY_YES" and side == "NO")
    action = "SELL" if flipped or (bid is not None and target is not None and bid >= target) else "HOLD"
    return {"action": action, "sell_above": target, "bid": bid, "flipped": flipped}
