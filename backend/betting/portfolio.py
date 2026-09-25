"""Simulated (paper) portfolio: economic evaluation of forecasts, automatic virtual bets, settlement."""
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.betting import clv, fees
from backend.betting.economics import Exposure, Quote, estimated_quote, evaluate, model_sigma, Evaluation
from backend.betting.profiles import get_profile
from backend.config import settings
from backend.db.models import (
    Article, BettingSettings, Market, MarketArticleLink, MarketPrediction, PaperBet, PaperExclusion, ProcessedArticle,
)
from backend.markets import polymarket
from backend.markets.forecast import brier_score
from backend.i18n import tr

logger = logging.getLogger(__name__)

MIN_RESOLVED_FOR_CALIBRATION = 30
EXCLUSION_KINDS = ("market", "event", "category")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Settings & ledger ----------

async def get_settings(db: AsyncSession) -> BettingSettings:
    row = await db.get(BettingSettings, 1)
    if row is None:
        row = BettingSettings(id=1, bankroll=settings.PAPER_BANKROLL, preset=get_profile(settings.PAPER_PRESET).key,
                              auto_paper=True, auto_sell=True)
        db.add(row)
        await db.commit()
    return row


async def update_settings(db: AsyncSession, *, preset: Optional[str] = None, auto_paper: Optional[bool] = None,
                          auto_sell: Optional[bool] = None) -> BettingSettings:
    row = await get_settings(db)
    if auto_sell is not None:
        row.auto_sell = auto_sell
    if preset is not None:
        row.preset = get_profile(preset).key
    if auto_paper is not None:
        row.auto_paper = auto_paper
    row.updated_at = _now()
    await db.commit()
    return row


async def reset_portfolio(db: AsyncSession, bankroll: float, preset: Optional[str] = None) -> BettingSettings:
    """Starts over: deletes every simulated bet and sets a new initial capital."""
    if not (10 <= bankroll <= 10_000_000):
        raise ValueError(tr("Il capitale deve essere tra 10 $ e 10.000.000 $", "Capital must be between $10 and $10,000,000"))
    await db.execute(delete(PaperBet))
    row = await get_settings(db)
    row.bankroll = float(bankroll)
    if preset is not None:
        row.preset = get_profile(preset).key
    row.started_at = row.updated_at = _now()
    await db.commit()
    return row


SETTLED = ("won", "lost", "void", "sold")  # void: market resolved 50-50; sold: closed before resolution


async def ledger(db: AsyncSession) -> dict:
    s = await get_settings(db)
    realized = (await db.execute(select(func.coalesce(func.sum(PaperBet.pnl), 0.0)).where(PaperBet.status.in_(SETTLED)))).scalar()
    open_outlay = (await db.execute(
        select(func.coalesce(func.sum(PaperBet.stake + PaperBet.fee), 0.0)).where(PaperBet.status == "open")
    )).scalar()
    equity = s.bankroll + realized
    return {"bankroll": s.bankroll, "realized": realized, "open_outlay": open_outlay,
            "equity": equity, "cash": equity - open_outlay}


async def exposure_for(db: AsyncSession, market: Market) -> Exposure:
    outlay = PaperBet.stake + PaperBet.fee
    base = select(func.coalesce(func.sum(outlay), 0.0)).select_from(PaperBet).join(Market, Market.id == PaperBet.market_id)\
        .where(PaperBet.status == "open")

    async def total(*conds):
        return (await db.execute(base.where(*conds))).scalar()

    return Exposure(
        market=await total(PaperBet.market_id == market.id),
        event=await total(Market.event_slug == market.event_slug) if market.event_slug else await total(PaperBet.market_id == market.id),
        category=await total(Market.category == market.category) if market.category else 0.0,
        total=await total(),
    )


# ---------- Inputs of the evaluation ----------

async def calibration_factor(db: AsyncSession) -> float:
    """How much to widen (> 1) or narrow (< 1) the forecast uncertainty, from resolved markets.

    Reliability ratio: the Brier score the blended forecasts actually got, divided by the one
    they would get on average if they were exactly calibrated (mean of p·(1 − p)). Above 1
    the forecasts were over-confident. 1 until enough markets have resolved.
    """
    latest = (
        select(MarketPrediction)
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
        .subquery()
    )
    rows = (await db.execute(
        select(Market.resolved_yes, latest.c.blended_probability)
        .join(latest, latest.c.market_id == Market.id).where(Market.resolved_yes.is_not(None))
    )).all()
    if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
        return 1.0
    observed = sum((r.blended_probability - (1.0 if r.resolved_yes else 0.0)) ** 2 for r in rows) / len(rows)
    expected = sum(r.blended_probability * (1 - r.blended_probability) for r in rows) / len(rows)
    return max(0.75, min(2.0, math.sqrt(observed / max(expected, 1e-4))))


async def update_market_category(db: AsyncSession, market: Market) -> Optional[str]:
    """Dominant category of the news linked to the market (used for the category exposure cap)."""
    rows = (await db.execute(
        select(ProcessedArticle.category).join(Article, Article.id == ProcessedArticle.article_id)
        .join(MarketArticleLink, MarketArticleLink.article_id == Article.id)
        .where(MarketArticleLink.market_id == market.id, ProcessedArticle.category.is_not(None))
    )).scalars().all()
    if rows:
        market.category = Counter(rows).most_common(1)[0][0]
    return market.category


async def build_quote(market: Market, side: str) -> Quote:
    """Order book of the side to buy; falls back to an estimate from price, spread and liquidity."""
    mid = market.yes_price if side == "YES" else 1.0 - market.yes_price
    token = market.yes_token_id if side == "YES" else market.no_token_id
    fee_bps = await fees.market_fee_bps(token, market.category)
    market.taker_fee_bps = fee_bps
    if token:
        try:
            book = await polymarket.fetch_order_book(token)
            if book.asks:
                return Quote(asks=book.asks, mid=mid, fee_bps=fee_bps, min_order_shares=market.order_min_size, source="book")
        except Exception as e:
            logger.info(f"Order book unavailable for {market.id} ({side}): {e}")
    spread = settings.DEFAULT_SPREAD
    if market.best_bid is not None and market.best_ask is not None and market.best_ask > market.best_bid:
        spread = market.best_ask - market.best_bid
    return estimated_quote(mid, spread, market.liquidity or 0.0, fee_bps, market.order_min_size)


def days_to_end(market: Market) -> float:
    if market.end_date is None:
        return 365.0
    return max(1.0, (market.end_date - _now()).total_seconds() / 86_400)


async def evaluate_prediction(db: AsyncSession, market: Market, prediction: MarketPrediction,
                              quote: Optional[Quote] = None, preset: Optional[str] = None) -> Evaluation:
    s = await get_settings(db)
    profile = get_profile(preset or s.preset)
    side = "NO" if prediction.signal == "BUY_NO" else "YES"
    weight = prediction.model_weight if prediction.model_weight is not None else \
        min(1.0, settings.MODEL_WEIGHT_MAX * prediction.evidence_strength)
    sigma = model_sigma(prediction.model_probability, prediction.evidence_strength, weight,
                        settings.MODEL_PSEUDO_COUNT, await calibration_factor(db))
    await update_market_category(db, market)
    ledger_now = await ledger(db)
    return evaluate(
        signal=prediction.signal,
        p_yes=prediction.blended_probability,
        sigma=sigma,
        quote=quote or await build_quote(market, side),
        days=days_to_end(market),
        profile=profile,
        equity=ledger_now["equity"],
        available_cash=ledger_now["cash"],
        exposure=await exposure_for(db, market),
        liquidity=market.liquidity or 0.0,
        risk_free_rate=settings.RISK_FREE_RATE,
    )


# ---------- Exclusions ----------

async def excluded_reason(db: AsyncSession, market: Market) -> Optional[str]:
    checks = [("market", market.id), ("event", market.event_slug), ("category", market.category)]
    for kind, value in checks:
        if value and (await db.execute(
            select(PaperExclusion.id).where(PaperExclusion.kind == kind, PaperExclusion.value == value)
        )).first():
            return kind
    return None


async def add_exclusion(db: AsyncSession, kind: str, value: str, label: Optional[str] = None) -> PaperExclusion:
    if kind not in EXCLUSION_KINDS or not value:
        raise ValueError(tr("Esclusione non valida", "Invalid exclusion"))
    existing = (await db.execute(select(PaperExclusion).where(PaperExclusion.kind == kind, PaperExclusion.value == value))).scalar_one_or_none()
    if existing:
        return existing
    row = PaperExclusion(kind=kind, value=value, label=label)
    db.add(row)
    await db.commit()
    return row


# ---------- Bets ----------

async def maybe_place_bet(db: AsyncSession, market: Market, prediction: MarketPrediction, ev: Evaluation,
                          placed_by: str = "auto") -> Optional[PaperBet]:
    """Places a simulated bet when the evaluation says so, unless excluded or already open on this market."""
    s = await get_settings(db)
    if placed_by == "auto" and not s.auto_paper:
        return None
    if ev.verdict not in ("GO", "SMALL") or ev.shares <= 0:
        return None
    if placed_by == "auto" and await excluded_reason(db, market):
        return None
    already = (await db.execute(select(PaperBet.id).where(PaperBet.market_id == market.id, PaperBet.status == "open"))).first()
    if already:
        return None
    bet = PaperBet(
        market_id=market.id, prediction_id=prediction.id, side=ev.side, shares=ev.shares,
        avg_price=ev.avg_price, stake=ev.stake, fee=ev.fee, p_side=ev.p_side, p_conservative=ev.p_conservative,
        expected_profit=ev.expected_profit, preset=s.preset, placed_by=placed_by,
    )
    db.add(bet)
    await db.commit()
    logger.info(f"Paper bet {ev.side} {ev.shares:.1f} shares on {market.id} for {ev.outlay:.2f} $")
    return bet


async def apply_economics(db: AsyncSession, market: Market, prediction: MarketPrediction) -> Optional[Evaluation]:
    """Called after every forecast: stores the evaluation and places the automatic simulated bet."""
    try:
        ev = await evaluate_prediction(db, market, prediction)
        prediction.economics = ev.as_dict()
        await db.commit()
        await maybe_place_bet(db, market, prediction, ev)
        await review_open_bets(db, [market.id])
        return ev
    except Exception as e:
        logger.error(f"Economic evaluation failed for {market.id}: {e}")
        await db.rollback()
        return None


async def settle_bets(db: AsyncSession) -> int:
    """Closes open bets on resolved markets: a winning share pays 1 $, a 50-50 split 0,50 $ per share."""
    rows = (await db.execute(
        select(PaperBet, Market).join(Market, Market.id == PaperBet.market_id)
        .where(PaperBet.status == "open", or_(Market.resolution.is_not(None), Market.resolved_yes.is_not(None)))
    )).all()
    for bet, market in rows:
        resolution = market.resolution or ("yes" if market.resolved_yes else "no")
        if resolution == "split":
            bet.payout = bet.shares * 0.5
            bet.status = "void"
        else:
            won = (resolution == "yes") == (bet.side == "YES")
            bet.payout = bet.shares if won else 0.0
            bet.status = "won" if won else "lost"
        bet.pnl = bet.payout - bet.stake - bet.fee
        bet.settled_at = _now()
    await db.commit()
    return len(rows)


# ---------- Selling before resolution ----------

async def sell_bet(db: AsyncSession, bet: PaperBet, market: Market, reason: str, min_price: float = 0.01) -> bool:
    """Sells all the shares of an open bet into the order book, not below `min_price`.
    Without a book, at the best bid known from the last sync. False if the book is too thin."""
    from backend.betting.fees import fee_per_share
    from backend.betting.plans import best_bid
    token = market.yes_token_id if bet.side == "YES" else market.no_token_id
    bids = None
    if token:
        try:
            bids = (await polymarket.fetch_order_book(token)).bids
        except Exception as e:
            logger.info(f"Order book unavailable to sell {bet.id}: {e}")
    if not bids:
        price = await best_bid(market, bet.side)
        bids = [(price, bet.shares)] if price else []
    fee_bps = market.taker_fee_bps if market.taker_fee_bps is not None else fees.category_rate(market.category) * 10_000
    left, proceeds, fee = bet.shares, 0.0, 0.0
    for price, size in bids:
        if price < min_price or left <= 1e-9:
            break
        take = min(size, left)
        proceeds += take * price
        fee += take * fee_per_share(price, fee_bps)
        left -= take
    if left > 1e-6:
        logger.info(f"Not enough bids to sell bet {bet.id} ({left:.1f} shares left)")
        return False
    bet.payout = proceeds - fee
    bet.fee = (bet.fee or 0.0) + fee
    bet.pnl = proceeds - bet.stake - bet.fee
    bet.exit_price = proceeds / bet.shares
    bet.exit_reason = reason
    bet.status = "sold"
    bet.settled_at = _now()
    await db.commit()
    logger.info(f"Paper bet {bet.id} sold at {bet.exit_price:.3f}: {reason}")
    return True


async def review_open_bets(db: AsyncSession, market_ids: Optional[list] = None, limit: int = 50) -> int:
    """Sells the open simulated bets whose plan says SELL (price reached the estimate, or the
    forecast turned). Runs after each sync and each new forecast, if auto_sell is on."""
    from backend.betting.plans import plan_for
    s = await get_settings(db)
    if not s.auto_sell:
        return 0
    stmt = select(PaperBet, Market).join(Market, Market.id == PaperBet.market_id).where(
        PaperBet.status == "open", Market.closed == False)  # noqa: E712
    if market_ids is not None:
        stmt = stmt.where(PaperBet.market_id.in_(market_ids))
    sold = 0
    for bet, market in (await db.execute(stmt.limit(limit))).all():
        try:
            prediction = await latest_prediction(db, market)
            if prediction is None or market.yes_price is None:
                continue
            ev = await evaluate_prediction(db, market, prediction)
            plan = await plan_for(db, market, prediction, ev, get_profile(s.preset), position=bet)
            if plan.action == "SELL":
                # Price reached the estimate: sell only at or above the target. Forecast turned: at the best bids.
                floor = plan.levels.get("sell_above") if plan.code == "target_reached" else None
                sold += await sell_bet(db, bet, market, plan.summary, min_price=floor or 0.01)
        except Exception as e:
            logger.error(f"Review of bet {bet.id} failed: {e}")
            await db.rollback()
    return sold


async def latest_prediction(db: AsyncSession, market: Market):
    """Latest forecast for a market (for an outcome of a multi-outcome event, its share of the distribution)."""
    if market.multi_event_id:
        from backend.db.models import MultiPrediction
        from backend.multi.service import outcome_prediction
        latest = (await db.execute(select(MultiPrediction).where(MultiPrediction.event_id == market.multi_event_id)
                                   .order_by(MultiPrediction.created_at.desc()).limit(1))).scalar_one_or_none()
        return outcome_prediction(latest, market.id) if latest else None
    return (await db.execute(select(MarketPrediction).where(MarketPrediction.market_id == market.id)
                             .order_by(MarketPrediction.created_at.desc()).limit(1))).scalar_one_or_none()


def bet_clv(bet: PaperBet, market: Market) -> Optional[float]:
    """Closing line value of a bet once its market stopped trading; None before."""
    if not market.closed or market.last_trading_price is None:
        return None
    return clv.clv(bet.avg_price, market.last_trading_price, bet.side)


def mark_value(bet: PaperBet, market: Market) -> Optional[float]:
    """Current value of an open bet at the market price (what the shares would be worth now)."""
    if market.yes_price is None:
        return None
    price = market.yes_price if bet.side == "YES" else 1.0 - market.yes_price
    return bet.shares * price


async def summary(db: AsyncSession) -> dict:
    s = await get_settings(db)
    led = await ledger(db)
    bets = (await db.execute(select(PaperBet, Market).join(Market, Market.id == PaperBet.market_id)
                             .order_by(PaperBet.created_at))).all()
    open_value = unrealized = 0.0
    counts = Counter(bet.status for bet, _ in bets)
    expected_settled = 0.0
    settled_rows = sorted((r for r in bets if r[0].status in SETTLED), key=lambda r: r[0].settled_at)
    first = min([s.started_at] + [r[0].created_at for r in settled_rows])
    curve = [{"t": first.isoformat(), "equity": s.bankroll}]
    running = s.bankroll
    for bet, market in settled_rows:
        running += bet.pnl
        expected_settled += bet.expected_profit
        curve.append({"t": bet.settled_at.isoformat(), "equity": round(running, 2)})
    for bet, market in bets:
        if bet.status == "open":
            value = mark_value(bet, market)
            if value is not None:
                open_value += value
                unrealized += value - bet.stake - bet.fee
    settled = counts["won"] + counts["lost"]
    sold_won = sum(1 for bet, _ in bets if bet.status == "sold" and (bet.pnl or 0) > 0)
    return {
        "settings": {"bankroll": s.bankroll, "preset": s.preset, "auto_paper": s.auto_paper, "auto_sell": s.auto_sell,
                     "started_at": s.started_at},
        "equity": led["equity"],
        "cash": led["cash"],
        "invested": led["open_outlay"],
        "open_value": open_value,
        "realized_pnl": led["realized"],
        "unrealized_pnl": unrealized,
        "total_value": led["cash"] + open_value,
        "roi": (led["cash"] + open_value - s.bankroll) / s.bankroll if s.bankroll else None,
        "counts": {"open": counts["open"], "won": counts["won"], "lost": counts["lost"], "void": counts["void"], "sold": counts["sold"], "excluded": counts["excluded"]},
        # Bets sold before resolution count as won when they made money
        "hit_rate": (counts["won"] + sold_won) / (settled + counts["sold"]) if settled + counts["sold"] else None,
        "expected_pnl_settled": expected_settled,
        # Closing line value (points of the side bought) on bets whose market closed, and the
        # move so far on open ones: positive = bought below the price the market settled on
        "clv": clv.summarize([bet_clv(b, m) for b, m in bets if b.status != "excluded"]),
        "clv_open": clv.summarize([clv.clv(b.avg_price, m.yes_price, b.side) for b, m in bets
                                   if b.status == "open" and not m.closed]),
        "equity_curve": curve,
    }
