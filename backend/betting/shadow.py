"""Shadow bets: what the filters blocked, followed as if it had been bought.

Every filter trades bets for safety, and it is not obvious that it helps: a filter that blocks
bets that would have won only costs money. When an automatic bet would have passed if not for
one or more filters, it is recorded here, without money, with the filter that blocked it, and
settled like a real bet when the market resolves. Per filter the portfolio then shows how many
bets it blocked, their hypothetical result and closing line value: negative means the filter
avoided losses, positive that it is costing money.

The filters measured:
- longshot, second_opinion, price_market, too_close, roi_too_low: blocking reasons of the
  economic assessment (the assessment is repeated without them);
- objective_evidence: the signal was a buy with Jev's own evidence rating and became HOLD with
  the one from the facts;
- clv_guard: the closing-line guard paused automatic bets or excluded the category.
The hypothetical bet is the taker one (at the ask), with the stake the assessment would give.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.betting import clv
from backend.config import settings
from backend.db.models import Market, MarketPrediction, ShadowBet
from backend.i18n import tr

logger = logging.getLogger(__name__)

ECONOMIC_FILTERS = ("longshot", "second_opinion", "price_market", "too_close", "roi_too_low")
FILTERS = ECONOMIC_FILTERS + ("objective_evidence", "clv_guard")


def label(code: str) -> str:
    labels = {
        "longshot": tr("Quote sotto il prezzo minimo", "Shares under the minimum price"),
        "second_opinion": tr("Seconda opinione contraria", "Second opinion disagreeing"),
        "price_market": tr("Mercati sul prezzo di un asset", "Markets on an asset's price"),
        "too_close": tr("Troppo vicino alla scadenza", "Too close to the end"),
        "roi_too_low": tr("Rendimento della scommessa troppo basso", "Return of the bet too low"),
        "objective_evidence": tr("Evidenze dai fatti più deboli di Jev", "Evidence from the facts weaker than Jev's"),
        "clv_guard": tr("Pausa per il prezzo di chiusura", "Closing-line pause"),
    }
    return " + ".join(labels.get(c, c) for c in code.split("+"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record(db: AsyncSession, market: Market, prediction, ev, filter_code: str) -> Optional[ShadowBet]:
    """A shadow bet from an evaluation that says buy; at most one open per market and filter."""
    if not settings.SHADOW_BETS_ENABLED or ev.verdict not in ("GO", "SMALL") or ev.shares <= 0:
        return None
    exists = (await db.execute(select(ShadowBet.id).where(
        ShadowBet.market_id == market.id, ShadowBet.filter == filter_code, ShadowBet.status == "open"))).first()
    if exists:
        return None
    row = ShadowBet(market_id=market.id, prediction_id=getattr(prediction, "id", None), filter=filter_code,
                    side=ev.side, shares=ev.shares, avg_price=ev.avg_price, stake=ev.stake, fee=ev.fee,
                    p_side=ev.p_side, expected_profit=ev.expected_profit)
    db.add(row)
    await db.commit()
    logger.info(f"Shadow bet {ev.side} on {market.id}: blocked by {filter_code}")
    return row


async def after_evaluation(db: AsyncSession, market: Market, prediction, ev) -> Optional[ShadowBet]:
    """After the evaluation of a new forecast: if only filters blocked it, the bet it would have been."""
    if not settings.SHADOW_BETS_ENABLED or ev.verdict in ("GO", "SMALL"):
        return None
    codes = {r["code"] for r in ev.as_dict()["reasons"] if r["blocking"]}
    if not codes or not codes <= set(ECONOMIC_FILTERS):
        return None
    from backend.betting.portfolio import evaluate_prediction
    without = await evaluate_prediction(db, market, prediction, ignore=frozenset(codes))
    return await record(db, market, prediction, without, "+".join(sorted(codes)))


async def after_objective_evidence(db: AsyncSession, market: Market, prediction: MarketPrediction) -> Optional[ShadowBet]:
    """The forecast became HOLD only because the facts rated the evidence lower than Jev: the bet
    Jev's own rating would have given, if it passes everything else."""
    from backend.markets.forecast import compute_signal
    jev = prediction.jev_evidence_strength
    if not settings.SHADOW_BETS_ENABLED or prediction.signal != "HOLD" or jev is None \
            or jev <= prediction.evidence_strength or market.yes_price is None:
        return None
    if compute_signal(prediction.model_probability, market.yes_price, jev).signal == "HOLD":
        return None
    from backend.betting.portfolio import evaluate_prediction
    as_jev = MarketPrediction(**{c.key: getattr(prediction, c.key) for c in MarketPrediction.__table__.columns
                                 if c.key != "id"})
    as_jev.evidence_strength = jev
    ev = await evaluate_prediction(db, market, as_jev)
    return await record(db, market, prediction, ev, "objective_evidence")


async def settle(db: AsyncSession) -> int:
    """Closes the open shadow bets on resolved markets, like real bets."""
    rows = (await db.execute(
        select(ShadowBet, Market).join(Market, Market.id == ShadowBet.market_id)
        .where(ShadowBet.status == "open", or_(Market.resolution.is_not(None), Market.resolved_yes.is_not(None)))
    )).all()
    for bet, market in rows:
        resolution = market.resolution or ("yes" if market.resolved_yes else "no")
        if resolution == "split":
            bet.payout, bet.status = bet.shares * 0.5, "void"
        else:
            won = (resolution == "yes") == (bet.side == "YES")
            bet.payout, bet.status = (bet.shares if won else 0.0), ("won" if won else "lost")
        bet.pnl = bet.payout - bet.outlay
        bet.settled_at = _now()
    if rows:
        await db.commit()
    return len(rows)


def move(bet: ShadowBet, market: Market) -> Optional[float]:
    """Points the price of the side moved since the (hypothetical) purchase: to the close, or to now."""
    price = market.last_trading_price if market.closed and market.last_trading_price is not None else market.yes_price
    return clv.clv(bet.avg_price, price, bet.side) if price is not None else None


async def report(db: AsyncSession, since: Optional[datetime] = None) -> list[dict]:
    """Per filter: bets blocked, open, won/lost, hypothetical result and price move."""
    stmt = select(ShadowBet, Market).join(Market, Market.id == ShadowBet.market_id)
    if since is not None:
        stmt = stmt.where(ShadowBet.created_at >= since)
    groups: dict[str, list] = {}
    for bet, market in (await db.execute(stmt)).all():
        groups.setdefault(bet.filter, []).append((bet, market))
    out = []
    for code, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        settled = [b for b, _ in rows if b.status in ("won", "lost", "void")]
        moves = [m for m in (move(b, mk) for b, mk in rows) if m is not None]
        stake = sum(b.outlay for b in settled)
        pnl = sum(b.pnl or 0.0 for b in settled)
        out.append({
            "filter": code, "label": label(code), "n": len(rows),
            "open": sum(1 for b, _ in rows if b.status == "open"),
            "won": sum(1 for b in settled if b.status == "won"), "lost": sum(1 for b in settled if b.status == "lost"),
            "pnl": pnl if settled else None, "roi": pnl / stake if stake else None,
            "expected_pnl": sum(b.expected_profit for b in settled) if settled else None,
            "avg_move": sum(moves) / len(moves) if moves else None, "n_moves": len(moves),
        })
    return out


async def listing(db: AsyncSession, limit: int = 200) -> list[dict]:
    rows = (await db.execute(select(ShadowBet, Market).join(Market, Market.id == ShadowBet.market_id)
                             .order_by(ShadowBet.created_at.desc()).limit(limit))).all()
    return [shadow_dict(b, m) for b, m in rows]


def shadow_dict(b: ShadowBet, m: Market) -> dict:
    return {
        "id": b.id, "market_id": m.id, "question": m.question, "filter": b.filter, "filter_label": label(b.filter),
        "side": b.side, "shares": b.shares, "avg_price": b.avg_price, "stake": b.stake, "fee": b.fee,
        "outlay": b.outlay, "p_side": b.p_side, "expected_profit": b.expected_profit, "status": b.status,
        "created_at": b.created_at, "settled_at": b.settled_at, "payout": b.payout, "pnl": b.pnl,
        "move": move(b, m), "prediction_id": b.prediction_id,
    }


async def count_open(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(ShadowBet).where(ShadowBet.status == "open"))).scalar()
