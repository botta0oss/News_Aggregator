"""How accurate the forecasts were on resolved markets, and whether prices moved toward the signals."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.backtest.analysis import brier_gain, cluster_bootstrap
from backend.betting import clv
from backend.betting.clv import side_price
from backend.db.models import Market, MarketPrediction
from backend.markets.forecast import brier_score


async def summary(db: AsyncSession) -> dict:
    """Brier score (lower is better) of the latest pre-resolution forecast vs the market price."""
    latest = (
        select(MarketPrediction)
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
        .subquery()
    )
    rows = (await db.execute(
        select(Market.id, Market.resolved_yes, latest.c.market_probability, latest.c.model_probability,
               latest.c.blended_probability)
        .join(latest, latest.c.market_id == Market.id)
        .where(Market.resolved_yes.is_not(None))
    )).all()
    cases = [{"market_id": r.id, "resolved_yes": r.resolved_yes, "price": r.market_probability,
              "model_probability": r.model_probability, "blended_probability": r.blended_probability} for r in rows]

    # Closing line value of the signals: did the price move toward the forecast before the close?
    # Uses every signal (not only the latest per market) on markets that stopped trading.
    signals = (await db.execute(
        select(MarketPrediction.market_id, MarketPrediction.signal, MarketPrediction.market_probability,
               Market.last_trading_price)
        .join(Market, Market.id == MarketPrediction.market_id)
        .where(Market.closed == True, Market.last_trading_price.is_not(None),  # noqa: E712
               MarketPrediction.signal.in_(("BUY_YES", "BUY_NO")))
    )).all()
    moves = [{"market_id": s.market_id,
              "v": clv.clv(side_price(s.market_probability, "YES" if s.signal == "BUY_YES" else "NO"),
                           s.last_trading_price, "YES" if s.signal == "BUY_YES" else "NO")} for s in signals]
    return {
        "resolved_markets": len(rows),
        "brier_market": brier_score((r.market_probability, r.resolved_yes) for r in rows),
        "brier_model": brier_score((r.model_probability, r.resolved_yes) for r in rows),
        "brier_blended": brier_score((r.blended_probability, r.resolved_yes) for r in rows),
        # Brier of the price minus Brier of the forecast (> 0 = better), 95% bootstrap interval
        "gain_model": brier_gain(cases, "model_probability"),
        "gain_blended": brier_gain(cases, "blended_probability"),
        "signal_clv": {**clv.summarize([m["v"] for m in moves]),
                       "interval": cluster_bootstrap(moves, lambda m: m["v"])},
        "second_opinion": await second_opinion_summary(db),
    }


async def second_opinion_summary(db: AsyncSession) -> dict:
    """How the second opinion (ai/second_opinion.py) did on resolved markets, against Jev and the price,
    on the latest forecast with a second opinion per market; and, where it disagreed with a signal,
    who was right."""
    from backend.ai.second_opinion import agrees
    latest = (
        select(MarketPrediction)
        .where(MarketPrediction.second_opinion.is_not(None))
        .distinct(MarketPrediction.market_id)
        .order_by(MarketPrediction.market_id, MarketPrediction.created_at.desc())
        .subquery()
    )
    rows = (await db.execute(
        select(Market.id, Market.resolved_yes, latest.c.market_probability, latest.c.model_probability,
               latest.c.second_opinion, latest.c.signal, latest.c.second_opinion_provider)
        .join(latest, latest.c.market_id == Market.id).where(Market.resolved_yes.is_not(None))
    )).all()
    cases = [{"market_id": r.id, "resolved_yes": r.resolved_yes, "price": r.market_probability,
              "second_opinion": r.second_opinion, "model_probability": r.model_probability} for r in rows]
    disagreements = [r for r in rows if agrees(r.second_opinion, r.market_probability, r.signal) is False]
    # Jev's side bought at the price of the forecast, per share: what blocking these bets was worth
    def jev_side_result(r):
        yes = r.signal == "BUY_YES"
        won = r.resolved_yes == yes
        price = r.market_probability if yes else 1 - r.market_probability
        return (1.0 if won else 0.0) - price
    results = [jev_side_result(r) for r in disagreements]
    providers: dict = {}
    for r in rows:
        providers[r.second_opinion_provider or "?"] = providers.get(r.second_opinion_provider or "?", 0) + 1
    return {
        "n": len(rows),
        "providers": providers,
        "brier_second": brier_score((r.second_opinion, r.resolved_yes) for r in rows),
        "brier_model": brier_score((r.model_probability, r.resolved_yes) for r in rows),
        "brier_market": brier_score((r.market_probability, r.resolved_yes) for r in rows),
        "gain_second": brier_gain(cases, "second_opinion"),
        "gain_model": brier_gain(cases, "model_probability"),
        "disagreements": len(disagreements),
        "jev_right": sum(1 for r in disagreements if r.resolved_yes == (r.signal == "BUY_YES")),
        # Average result per share of the bets the disagreement blocked (< 0: blocking them saved money)
        "blocked_result_per_share": sum(results) / len(results) if results else None,
    }
