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
    Article, BettingSettings, Market, MarketArticleLink, MarketPrediction, PaperBet, PaperExclusion, PaperOrder,
    ProcessedArticle, ShadowBet,
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
    await db.execute(delete(PaperOrder))
    await db.execute(delete(ShadowBet))
    await db.execute(delete(PaperBet))
    row = await get_settings(db)
    row.bankroll = float(bankroll)
    if preset is not None:
        row.preset = get_profile(preset).key
    row.started_at = row.updated_at = _now()
    row.paused_at = row.paused_reason = row.guard_since = None
    await db.commit()
    return row


SETTLED = ("won", "lost", "void", "sold")  # void: market resolved 50-50; sold: closed before resolution


async def ledger(db: AsyncSession) -> dict:
    s = await get_settings(db)
    realized = (await db.execute(select(func.coalesce(func.sum(PaperBet.pnl), 0.0)).where(PaperBet.status.in_(SETTLED)))).scalar()
    open_outlay = (await db.execute(
        select(func.coalesce(func.sum(PaperBet.stake + PaperBet.fee), 0.0)).where(PaperBet.status == "open")
    )).scalar()
    from backend.betting.orders import reserved
    pending = await reserved(db)   # money of the maker orders waiting in the book
    equity = s.bankroll + realized
    return {"bankroll": s.bankroll, "realized": realized, "open_outlay": open_outlay, "pending_orders": pending,
            "equity": equity, "cash": equity - open_outlay - pending}


async def exposure_for(db: AsyncSession, market: Market) -> Exposure:
    outlay = PaperBet.stake + PaperBet.fee
    base = select(func.coalesce(func.sum(outlay), 0.0)).select_from(PaperBet).join(Market, Market.id == PaperBet.market_id)\
        .where(PaperBet.status == "open")

    from backend.betting.orders import reserved

    async def total(*conds):
        # Open bets plus the pending limit orders, which would become bets
        return (await db.execute(base.where(*conds))).scalar() + await reserved(db, *conds)

    return Exposure(
        market=await total(Market.id == market.id),
        event=await total(Market.event_slug == market.event_slug) if market.event_slug else await total(Market.id == market.id),
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
        select(Market.resolved_yes, latest.c.blended_probability, latest.c.market_probability)
        .join(latest, latest.c.market_id == Market.id).where(Market.resolved_yes.is_not(None))
    )).all()
    if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
        return 1.0
    outcome = [1.0 if r.resolved_yes else 0.0 for r in rows]
    observed = sum((r.blended_probability - y) ** 2 for r, y in zip(rows, outcome)) / len(rows)
    expected = sum(r.blended_probability * (1 - r.blended_probability) for r in rows) / len(rows)
    factor = max(0.75, min(2.0, math.sqrt(observed / max(expected, 1e-4))))
    # Narrowing the uncertainty makes the bets bigger: only when the blend has shown it beats
    # the market price on the same markets (paired Brier gain, two standard errors above zero).
    # Being consistent with itself is not enough.
    gains = [(r.market_probability - y) ** 2 - (r.blended_probability - y) ** 2 for r, y in zip(rows, outcome)]
    mean = sum(gains) / len(gains)
    se = math.sqrt(sum((g - mean) ** 2 for g in gains) / (len(gains) - 1) / len(gains)) if len(gains) > 1 else float("inf")
    if mean - 2 * se <= 0:
        factor = max(1.0, factor)
    return factor


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
                              quote: Optional[Quote] = None, preset: Optional[str] = None,
                              ignore: frozenset = frozenset()) -> Evaluation:
    """Economic evaluation of a forecast at the market's current price.

    The blend is recomputed at the current price (Jev pooled with the price of now, with today's
    calibration and weights): the stored blend was pooled with the price of the forecast, and
    using it against a price that has moved since makes up an edge that is not there. Past
    FORECAST_MAX_AGE_HOURS or FORECAST_MAX_PRICE_MOVE the forecast needs a new one before buying."""
    from backend.betting import plans
    from backend.markets.forecast import disagreement_factor
    s = await get_settings(db)
    profile = get_profile(preset or s.preset)
    price = market.yes_price
    fc = plans.forecast_of(prediction, 0)
    if price is None or plans.is_outcome(prediction):
        # Outcomes keep the probability of their distribution (pooled over all the outcomes)
        p_yes, signal = prediction.blended_probability, prediction.signal
        weight = prediction.model_weight if prediction.model_weight is not None else fc.weight
    else:
        p_yes, signal = fc.p_yes(price), plans.signal_at(prediction, price)
        weight = fc.weight * disagreement_factor(fc.model, price)
    side = "NO" if signal == "BUY_NO" else "YES"
    sigma = model_sigma(prediction.model_probability, prediction.evidence_strength, weight,
                        settings.MODEL_PSEUDO_COUNT, await calibration_factor(db))
    await update_market_category(db, market)
    ledger_now = await ledger(db)
    return evaluate(
        signal=signal,
        p_yes=p_yes,
        sigma=sigma,
        quote=quote or await build_quote(market, side),
        days=days_to_end(market),
        profile=profile,
        equity=ledger_now["equity"],
        available_cash=ledger_now["cash"],
        exposure=await exposure_for(db, market),
        liquidity=market.liquidity or 0.0,
        risk_free_rate=settings.RISK_FREE_RATE,
        hours_to_end=hours_to_end(market),
        extra_reasons=forecast_reasons(market, prediction, signal),
        ignore=ignore,
    )


def hours_to_end(market: Market) -> Optional[float]:
    """Hours left before the market resolves (None without an end date)."""
    if market.end_date is None:
        return None
    return (market.end_date - _now()).total_seconds() / 3600


def forecast_reasons(market: Market, prediction, signal: Optional[str] = None) -> list:
    """Blocking reasons about the forecast itself: too old, the price moved too much since,
    a market decided by an asset's price (see markets/kinds.py), or a second opinion that
    does not agree (ai/second_opinion.py)."""
    from backend.betting.economics import Reason
    from backend.markets.kinds import is_price_market
    reasons = []
    if settings.EXCLUDE_PRICE_MARKETS and is_price_market(market.question):
        reasons.append(Reason("price_market", tr(
            "Mercato sul prezzo di un asset: si decide sul prezzo del momento, che Jev non vede e il mercato sì.",
            "Market on an asset's price: it is decided by the price of the moment, which Jev does not see and the market does.")))
    created = getattr(prediction, "created_at", None)
    age = (_now() - created).total_seconds() / 3600 if created else 0.0
    from backend.markets.forecast import logit
    has_prices = market.yes_price is not None and prediction.market_probability is not None
    move = abs(market.yes_price - prediction.market_probability) if has_prices else 0.0
    moved = has_prices and abs(logit(market.yes_price) - logit(prediction.market_probability)) > settings.FORECAST_MAX_PRICE_MOVE
    if age > settings.FORECAST_MAX_AGE_HOURS or moved:
        why = tr(f"ha {age:.0f} ore", f"is {age:.0f} hours old") if age > settings.FORECAST_MAX_AGE_HOURS else \
            tr(f"il prezzo del SÌ si è mosso di {move * 100:.0f} punti da allora", f"the YES price has moved {move * 100:.0f} points since")
        reasons.append(Reason("stale_forecast", tr(
            f"La previsione {why}: prima di comprare serve una previsione nuova.",
            f"The forecast {why}: a new forecast is needed before buying.")))
    reasons.extend(second_opinion_reasons(market, prediction, signal))
    return reasons


def second_opinion_reasons(market: Market, prediction, signal: Optional[str]) -> list:
    from backend.ai import second_opinion
    from backend.betting.economics import Reason
    if not settings.SECOND_OPINION_ENABLED or signal not in ("BUY_YES", "BUY_NO"):
        return []
    p2 = getattr(prediction, "second_opinion", None)
    if p2 is None:
        # Outcomes of multi-outcome events and forecasts made before the second opinion have none
        if settings.SECOND_OPINION_REQUIRED and getattr(prediction, "multi_prediction_id", None) is None:
            return [Reason("second_opinion", tr(
                "Nessuna seconda opinione disponibile (Gemini, Groq): senza, non si compra.",
                "No second opinion available (Gemini, Groq): no buying without one."))]
        return []
    if second_opinion.agrees(p2, market.yes_price, signal):
        return []
    who = (getattr(prediction, "second_opinion_provider", None) or "").capitalize() or tr("l'altro modello", "the other model")
    side = tr("sopra", "above") if signal == "BUY_YES" else tr("sotto", "below")
    return [Reason("second_opinion", tr(
        f"Seconda opinione contraria: {who} stima il SÌ al {p2 * 100:.0f}%, non {side} il prezzo ({market.yes_price * 100:.0f}%) come Jev.",
        f"Second opinion disagrees: {who} puts YES at {p2 * 100:.0f}%, not {side} the price ({market.yes_price * 100:.0f}%) like Jev."))]


PORTFOLIO_REASONS = ("exposure_cap", "no_cash")  # reasons that depend on the portfolio, not on the market


async def stale_portfolio_reasons(db: AsyncSession, market: Market, economics: Optional[dict]) -> list[str]:
    """Blocking reasons of a stored evaluation that depend on the portfolio (exposure caps full, no
    cash) and no longer hold now, e.g. after a sale. Prices are not re-read: only the portfolio."""
    codes = {r.get("code") for r in (economics or {}).get("reasons", []) if r.get("blocking")} & set(PORTFOLIO_REASONS)
    if not codes:
        return []
    profile = get_profile((await get_settings(db)).preset)
    led, exp = await ledger(db), await exposure_for(db, market)
    equity = led["equity"]
    full = any(frac * equity - used <= 0 for frac, used in (
        (profile.max_market_frac, exp.market), (profile.max_event_frac, exp.event),
        (profile.max_category_frac, exp.category), (profile.max_total_frac, exp.total)))
    stale = []
    if "exposure_cap" in codes and not full:
        stale.append("exposure_cap")
    if "no_cash" in codes and led["cash"] > 0:
        stale.append("no_cash")
    return stale


# ---------- Exclusions ----------

async def matching_exclusion(db: AsyncSession, market: Market) -> Optional[PaperExclusion]:
    checks = [("market", market.id), ("event", market.event_slug), ("category", market.category)]
    for kind, value in checks:
        if value:
            row = (await db.execute(select(PaperExclusion).where(
                PaperExclusion.kind == kind, PaperExclusion.value == value))).scalars().first()
            if row is not None:
                return row
    return None


async def excluded_reason(db: AsyncSession, market: Market) -> Optional[str]:
    row = await matching_exclusion(db, market)
    return row.kind if row is not None else None


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
    """Places a simulated bet when the evaluation says so, unless excluded or already open on this market.
    Automatic bets in maker mode become a limit order (betting/orders.py) that is filled later."""
    from backend.betting import orders
    s = await get_settings(db)
    if placed_by == "auto" and not s.auto_paper:
        return None
    if ev.verdict not in ("GO", "SMALL") or ev.shares <= 0:
        return None
    from backend.betting import shadow
    if placed_by == "auto":
        exclusion = await matching_exclusion(db, market)
        if exclusion is not None:
            if exclusion.source == "guard":   # a category the closing-line guard took out: measure it
                await shadow.record(db, market, prediction, ev, "clv_guard")
            return None
    already_open = (await db.execute(select(PaperBet.id).where(PaperBet.market_id == market.id, PaperBet.status == "open"))).first()
    if already_open:
        return None
    from backend.betting import guard
    if placed_by == "auto" and not await guard.allows_auto_bet(db, market):
        # paused: the price has been moving against the recent bets (betting/guard.py)
        await shadow.record(db, market, prediction, ev, "clv_guard")
        return None
    if placed_by == "auto":
        if await orders.pending_on(db, market.id):
            return None
        if orders.maker_mode():
            return await orders.place_order(db, market, prediction, ev, s.preset, placed_by)
    else:
        await orders.cancel_on_market(db, market.id, tr("Sostituito da una scommessa manuale", "Replaced by a manual bet"))
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
        from backend.betting import orders
        # A new forecast replaces the limit orders of the previous one (a new one follows if it still says buy)
        await orders.cancel_on_market(db, market.id, tr("Nuova previsione", "New forecast"))
        ev = await evaluate_prediction(db, market, prediction)
        prediction.economics = ev.as_dict()
        await db.commit()
        await maybe_place_bet(db, market, prediction, ev)
        from backend.betting import shadow
        await shadow.after_evaluation(db, market, prediction, ev)
        if isinstance(prediction, MarketPrediction):
            await shadow.after_objective_evidence(db, market, prediction)
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
        bet.settled_at = bet.settled_at or _now()  # a readmitted bet keeps its original closing time
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
    fee_bps = fee_bps_of(market)
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
    """Latest forecast for a market (for an outcome of a multi-outcome event, its share of the distribution).
    An outcome without a forecast of its event falls back to a forecast of the market itself:
    bets placed before multi-outcome support were forecast as Yes/No markets."""
    if market.multi_event_id:
        from backend.db.models import MultiPrediction
        from backend.multi.service import outcome_prediction
        latest = (await db.execute(select(MultiPrediction).where(MultiPrediction.event_id == market.multi_event_id)
                                   .order_by(MultiPrediction.created_at.desc()).limit(1))).scalar_one_or_none()
        outcome = outcome_prediction(latest, market.id) if latest else None
        if outcome is not None:
            return outcome
    return (await db.execute(select(MarketPrediction).where(MarketPrediction.market_id == market.id)
                             .order_by(MarketPrediction.created_at.desc()).limit(1))).scalar_one_or_none()


async def open_bet_plan(db: AsyncSession, bet: PaperBet, market: Market, profile) -> Optional[dict]:
    """Exit plan of an open bet (sale target, or sell now) with the latest forecast; None if it
    is not open, its market is closed or there is no forecast."""
    if bet.status != "open" or market.closed:
        return None
    prediction = await latest_prediction(db, market)
    if prediction is None:
        return None
    from backend.betting import plans
    sigma = model_sigma(prediction.model_probability, prediction.evidence_strength,
                        plans.forecast_of(prediction, 0).weight, settings.MODEL_PSEUDO_COUNT)
    return plans.exit_plan(prediction, market, bet.side, profile, days_to_end(market), sigma)


def bet_clv(bet: PaperBet, market: Market) -> Optional[float]:
    """Closing line value of a bet once its market stopped trading; None before."""
    if not market.closed or market.last_trading_price is None:
        return None
    return clv.clv(bet.avg_price, market.last_trading_price, bet.side)


def fee_bps_of(market: Market) -> float:
    return market.taker_fee_bps if market.taker_fee_bps is not None else fees.category_rate(market.category) * 10_000


def mid_value(bet: PaperBet, market: Market) -> Optional[float]:
    """Value of an open bet at the market (mid) price."""
    if market.yes_price is None:
        return None
    price = market.yes_price if bet.side == "YES" else 1.0 - market.yes_price
    return bet.shares * price


def mark_value(bet: PaperBet, market: Market) -> Optional[float]:
    """What an open bet would fetch if sold now: the shares at the best bid known from the last
    sync, minus the sale fee. Lower than the value at the mid price by half the spread."""
    from backend.betting.fees import fee_per_share
    from backend.betting.plans import known_bid
    bid = known_bid(market, bet.side)
    if bid is None:
        return None
    return bet.shares * (bid - fee_per_share(bid, fee_bps_of(market)))


async def summary(db: AsyncSession) -> dict:
    s = await get_settings(db)
    led = await ledger(db)
    bets = (await db.execute(select(PaperBet, Market).join(Market, Market.id == PaperBet.market_id)
                             .order_by(PaperBet.created_at))).all()
    open_value = unrealized = unrealized_mid = 0.0
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
            mid = mid_value(bet, market)
            if mid is not None:
                unrealized_mid += mid - bet.stake - bet.fee
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
        "unrealized_pnl": unrealized,          # selling now at the best bid, sale fee included
        "unrealized_pnl_mid": unrealized_mid,  # at the market (mid) price
        # Money reserved by pending limit orders is still ours until they fill
        "total_value": led["cash"] + led["pending_orders"] + open_value,
        "roi": (led["cash"] + led["pending_orders"] + open_value - s.bankroll) / s.bankroll if s.bankroll else None,
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
        "orders": await orders_stats(db),
        "shadow": await shadow_report(db),
    }


async def shadow_report(db: AsyncSession) -> list:
    from backend.betting import shadow
    return await shadow.report(db)


async def orders_stats(db: AsyncSession) -> dict:
    from backend.betting import orders
    return await orders.stats(db)
