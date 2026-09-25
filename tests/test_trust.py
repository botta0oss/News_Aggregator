"""Guards added after the first real portfolio lost money (see docs/strategy.md, "What went wrong").

1. The forecast is re-evaluated at the current price; old forecasts, or a price that moved a lot,
   need a new forecast before buying.
2. No bets close to the end of a market.
3. Markets decided by an asset's price are excluded.
4. Less trust in Jev: lower maximum weight, and less weight the further Jev is from the price.
5. The uncertainty is narrowed only when the blend has beaten the market.
6. A minimum absolute return per bet.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.betting import economics, portfolio
from backend.betting.plans import plan_for
from backend.betting.profiles import get_profile
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Market, MarketPrediction, PaperBet
from backend.markets import forecast
from backend.markets.kinds import is_price_market
from tests.test_portfolio import NOW, book, make_market, make_prediction  # noqa: F401  (fixture)


@pytest.fixture
def new_defaults(monkeypatch):
    """The parameters the app ships with (the other tests run with the earlier ones)."""
    monkeypatch.setattr(settings, "MODEL_WEIGHT_MAX", 0.25)
    monkeypatch.setattr(settings, "MODEL_DISAGREEMENT_LOGIT", 2.0)
    monkeypatch.setattr(settings, "EXCLUDE_PRICE_MARKETS", True)


# ---------- 4. Trust in Jev ----------

def test_the_real_losing_bets_give_no_signal_with_the_new_defaults(new_defaults):
    """Jev far from liquid prices: 66% vs 5.5%, 67% vs 11.5%, 72% vs 7.5%.
    With weight 0.5 and no reduction these became edges of 15–25 points; now they are noise.
    (17% vs 68% on «Bitcoin above $84,000» still gives a signal: that one is stopped by the
    minimum time to the end and by the exclusion of price markets.)"""
    for jev, price, evidence in ((0.66, 0.055, 0.815), (0.67, 0.115, 0.7625), (0.72, 0.075, 0.6475)):
        sig = forecast.compute_signal(jev, price, evidence)
        assert sig.signal == "HOLD", (jev, price, sig)
        assert abs(sig.edge) < settings.MIN_EDGE


def test_disagreement_reduces_the_weight_only_past_the_threshold(new_defaults):
    assert forecast.disagreement_factor(0.6, 0.5) == 1.0
    far = forecast.disagreement_factor(0.66, 0.055)             # ≈ 3.5 log-odds apart
    assert far == pytest.approx(2.0 / abs(forecast.logit(0.66) - forecast.logit(0.055)))
    # A moderate, well-supported disagreement still moves the blend
    sig = forecast.compute_signal(0.8, 0.35, 1.0)
    assert sig.model_weight == pytest.approx(0.25, abs=0.002) and sig.blended_probability > 0.4
    # Pooling at another price recomputes the reduction there
    assert forecast.pool(0.8, 0.05, 0.25) < forecast.pool(0.8, 0.05, 0.25 * 1.0) + 1e-9
    assert forecast.pool(0.8, 0.05, 0.25) - 0.05 < 0.05 * 0.8


# ---------- 1. Re-evaluation at the current price ----------

async def test_evaluation_uses_the_current_price_not_the_stored_blend(db, book):
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        p = await make_prediction(s, m, p_jev=0.8)             # blended ≈ 0.53 at 0.35
        m.yes_price = 0.45                                       # moved 10 points: still within the limit
        await s.commit()
        ev = await portfolio.evaluate_prediction(s, m, p)
        from backend.betting.plans import forecast_of
        assert ev.p_side == pytest.approx(forecast_of(p, 0).p_yes(0.45), abs=1e-6)
        assert ev.p_side != pytest.approx(p.blended_probability, abs=1e-3)


async def test_a_price_that_moved_or_an_old_forecast_needs_a_new_forecast(db, book):
    """The manual bet of the real portfolio: forecast at 89.45%, price later 98.3%, stored blend
    turned a 1.7¢ NO into +22 $ expected. Now it is refused until a new forecast."""
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.8945)
        p = await make_prediction(s, m, p_jev=0.44, evidence=0.55)
        m.yes_price = 0.983          # 8.85 points, but from 2.1 to 4.1 in log-odds
        await s.commit()
        ev = await portfolio.evaluate_prediction(s, m, p)
        assert ev.verdict == "NO" and any(r["code"] == "stale_forecast" for r in ev.reasons)
        plan = await plan_for(s, m, p, ev, get_profile("bilanciato"))
        assert plan.action in ("WAIT", "NONE")

        m2 = await make_market(s, mid="m-old", yes=0.35, event="old")
        p2 = await make_prediction(s, m2, p_jev=0.8)
        p2.created_at = NOW - timedelta(hours=settings.FORECAST_MAX_AGE_HOURS + 1)
        await s.commit()
        ev2 = await portfolio.evaluate_prediction(s, m2, p2)
        assert ev2.verdict == "NO" and any(r["code"] == "stale_forecast" for r in ev2.reasons)
        plan2 = await plan_for(s, m2, p2, ev2, get_profile("bilanciato"))
        assert plan2.action == "WAIT" and plan2.orders == []
    async with login_client_admin() as api:
        r = await api.post(f"/markets/{m2.id}/paper-bet")
        assert r.status_code == 409


def login_client_admin():
    from tests.conftest import login_client
    return login_client("admin")


# ---------- 2. Close to the end ----------

async def test_no_bet_close_to_the_end(db, book):
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        m.end_date = datetime.now(timezone.utc) + timedelta(minutes=25)
        await s.commit()
        p = await make_prediction(s, m, p_jev=0.8)
        ev = await portfolio.apply_economics(s, m, p)
        assert ev.verdict == "NO" and any(r["code"] == "too_close" and "25 minuti" in r["text"] for r in ev.reasons)
        assert (await s.execute(select(PaperBet))).first() is None
        plan = await plan_for(s, m, p, ev, get_profile("bilanciato"))
        assert plan.action == "AVOID" and plan.levels == {}


# ---------- 3. Price markets ----------

def test_price_markets_are_recognised():
    assert is_price_market("Will the price of Bitcoin be above $84,000 on September 24?")
    assert is_price_market("Will Bitcoin dip to $75,000 in September?")
    assert is_price_market("Will Ethereum reach $2,800 September 21-27?")
    assert is_price_market("Will gold hit $3,000 by December 31?")
    assert is_price_market("Bitcoin Up or Down on September 25?")
    assert not is_price_market("Will a Bitcoin ETF be approved in 2026?")
    assert not is_price_market("Will 1 Fed rate hike happen in 2026?")
    assert not is_price_market("Saudi Oil Pipeline (East-West) restarts by October 31?")
    assert not is_price_market("Will inflation be above 3% in October?")


async def test_price_markets_get_no_bets_and_no_paid_forecasts(db, book, new_defaults):
    from backend.markets.service import markets_needing_prediction
    async with SessionLocal() as s:
        m = await make_market(s, mid="m-btc", yes=0.055, event="btc")
        m.question = "Will Bitcoin dip to $75,000 in September?"
        await make_market(s, mid="m-fed", yes=0.35)
        await s.commit()
        ids = [x.id for x in await markets_needing_prediction(s, 10)]
        assert "m-btc" not in ids and "m-fed" in ids
        p = await make_prediction(s, m, p_jev=0.66)
        ev = await portfolio.evaluate_prediction(s, m, p)
        assert ev.verdict == "NO" and any(r["code"] == "price_market" for r in ev.reasons)


# ---------- 5. Uncertainty correction ----------

async def test_uncertainty_is_not_narrowed_unless_the_blend_beats_the_market(db):
    """A blend that is consistent with itself (observed Brier below expected) but no better
    than the price: the factor stays at 1 or above."""
    async with SessionLocal() as s:
        for i in range(40):
            yes = i % 2 == 0
            m = Market(id=f"r{i}", question=f"Resolved {i}?", yes_price=1.0 if yes else 0.0, closed=True,
                       resolved_yes=yes, volume=1e5, end_date=NOW - timedelta(days=1))
            s.add(m)
            # price and blend equally good: 0.7 on the winner
            p = 0.7 if yes else 0.3
            s.add(MarketPrediction(market_id=m.id, market_probability=p, model_probability=p, evidence_strength=0.5,
                                   blended_probability=p, model_weight=0.1, edge=0.0, signal="HOLD"))
        await s.commit()
        assert await portfolio.calibration_factor(s) >= 1.0

        # Now the blend is clearly better than the price on every market: narrowing is allowed
        for pred in (await s.execute(select(MarketPrediction))).scalars().all():
            m = await s.get(Market, pred.market_id)
            pred.market_probability = 0.5
            pred.blended_probability = 0.9 if m.resolved_yes else 0.1
        await s.commit()
        assert await portfolio.calibration_factor(s) < 1.0


# ---------- 6. Minimum absolute return ----------

def test_minimum_return_per_bet():
    profile = get_profile("bilanciato")
    q = economics.Quote(asks=[(0.60, 1e5)], mid=0.595, fee_bps=0, source="book")
    # 3.2 points of prudent margin on a 60¢ share: over 2 days it passes any annual threshold,
    # but the bet returns 5.3%, under the 6% of the preset
    ev = economics.evaluate(signal="BUY_YES", p_yes=0.632, sigma=0.0, quote=q, days=2, profile=profile,
                            equity=1000, available_cash=1000, exposure=economics.Exposure(), liquidity=1e6,
                            risk_free_rate=0.04)
    assert ev.verdict == "NO" and [r["code"] for r in ev.reasons if r["blocking"]] == ["roi_too_low"]
    ev = economics.evaluate(signal="BUY_YES", p_yes=0.70, sigma=0.0, quote=q, days=2, profile=profile,
                            equity=1000, available_cash=1000, exposure=economics.Exposure(), liquidity=1e6,
                            risk_free_rate=0.04)
    assert ev.verdict in ("GO", "SMALL")
