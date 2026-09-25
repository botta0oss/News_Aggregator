"""Long shots, outside view, closing-line guard and objective evidence strength."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.betting import economics, guard, portfolio
from backend.betting.profiles import get_profile
from backend.betting.strategy import buy_levels
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Market, MarketPrediction, PaperBet, PaperExclusion
from backend.markets import matching
from backend.markets.service import build_jev_request, predict_market
from tests.conftest import login_client
from tests.test_portfolio import NOW, book, make_market, make_prediction  # noqa: F401  (fixture)
from tests.test_strategy import HURDLE, PROFILE, fc


# ---------- 1. Long shots ----------

def test_no_buys_below_ten_cents_on_either_side(monkeypatch):
    monkeypatch.setattr(settings, "LONGSHOT_MIN_PRICE", 0.10)
    profile = get_profile("aggressivo")
    cheap = economics.Quote(asks=[(0.06, 1e5)], mid=0.055, fee_bps=0, source="book")
    ev = economics.evaluate(signal="BUY_YES", p_yes=0.30, sigma=0.0, quote=cheap, days=30, profile=profile,
                            equity=1000, available_cash=1000, exposure=economics.Exposure(), liquidity=1e6, risk_free_rate=0.04)
    assert ev.verdict == "NO" and [r["code"] for r in ev.reasons if r["blocking"]] == ["longshot"]
    ev = economics.evaluate(signal="BUY_NO", p_yes=0.70, sigma=0.0, quote=cheap, days=30, profile=profile,
                            equity=1000, available_cash=1000, exposure=economics.Exposure(), liquidity=1e6, risk_free_rate=0.04)
    assert any(r["code"] == "longshot" for r in ev.reasons)
    # The strategy never suggests buying YES under 10¢ either
    levels = buy_levels(fc(model=0.8), PROFILE, 400, 30, 0.05, HURDLE)
    assert levels["yes_limit"] is None or levels["yes_limit"] >= 0.10


# ---------- 3. Outside view ----------

async def test_jev_is_asked_for_the_base_rate_first(db, book, jev_client):
    jev_client(noul_value=0.3, score_value=3.0)
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        state, questions = build_jev_request(m, [])
        assert "base_rate" in questions and "base rate" in questions["resolves_yes"].instructions.lower()
        p = await predict_market(s, m)
        assert p.base_rate == pytest.approx(0.3)


# ---------- 6. Objective evidence strength ----------

def _item(quality=0.75, corroboration=1, url="https://www.reuters.com/x", relevance=None, age_h=1.0):
    now = NOW
    link = SimpleNamespace(relevance=relevance)
    article = SimpleNamespace(url=url, published_at=now - timedelta(hours=age_h), fetched_at=now)
    source = SimpleNamespace(url=url)
    return matching.EvidenceItem(link, article, None, source, 1.0, corroboration, quality)


def test_objective_evidence_grows_with_confirmations_primary_sources_and_freshness():
    one = matching.objective_evidence([_item()], now=NOW, half=1.5)
    assert 0.2 < one < 0.35
    assert matching.objective_evidence([_item()] * 3, now=NOW, half=1.5) == pytest.approx(0.5, abs=0.05)
    confirmed = matching.objective_evidence([_item(corroboration=3)], now=NOW, half=1.5)
    primary = matching.objective_evidence([_item(url="https://www.federalreserve.gov/newsevents/x")], now=NOW, half=1.5)
    old = matching.objective_evidence([_item(age_h=200)], now=NOW, half=1.5)
    irrelevant = matching.objective_evidence([_item(relevance=0.1)], now=NOW, half=1.5)
    assert confirmed > primary > one > old and one > irrelevant
    assert matching.is_primary_source(SimpleNamespace(url="https://www.bls.gov/news.release/cpi.nr0.htm"), None)
    assert matching.objective_evidence([_item()], half=0) is None


async def test_forecast_uses_the_lower_of_jev_and_the_facts(db, book, jev_client, monkeypatch):
    """Jev rates the single linked article as strong evidence (3/4): the facts say it is one item."""
    monkeypatch.setattr(settings, "EVIDENCE_OBJECTIVE_HALF", 1.5)
    jev_client(noul_value=0.8, score_value=3.0)
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        p = await predict_market(s, m)
        assert p.jev_evidence_strength == pytest.approx(0.75)
        assert p.objective_evidence < 0.5 and p.evidence_strength == pytest.approx(p.objective_evidence, abs=1e-4)
        assert p.signal == "HOLD"                      # below MIN_EVIDENCE: no signal from one article


# ---------- 4. Closing-line guard ----------

async def _bets_moving_against(s, n, category="Economy", start=0):
    """n markets bought at 40¢ YES more than an hour ago, now at 35¢: 5 points against each.
    The portfolio started before them (the guard only counts bets placed since the start)."""
    row = await portfolio.get_settings(s)
    row.started_at = NOW - timedelta(days=1)
    for i in range(start, start + n):
        m = Market(id=f"g{i}", question=f"Guard {i}?", yes_price=0.35, volume=1e6, liquidity=1e5,
                   category=category, end_date=NOW + timedelta(days=30), event_slug=f"g{i}")
        s.add(m)
        s.add(PaperBet(market_id=m.id, side="YES", shares=10, avg_price=0.40, stake=4.0, fee=0.0, p_side=0.5,
                       p_conservative=0.45, expected_profit=1.0, preset="bilanciato", placed_by="auto",
                       created_at=NOW - timedelta(hours=3)))
    await s.commit()


async def test_guard_excludes_a_losing_category_then_pauses_all_automatic_bets(db, book):
    async with SessionLocal() as s:
        await _bets_moving_against(s, 5, category="Crypto")
        target = Market(id="t1", question="Next?", yes_price=0.35, category="Crypto")
        s.add(target)
        await s.commit()
        # 5 crypto bets, all 5 points against: the category goes, the rest can still bet
        assert not await guard.allows_auto_bet(s, target)
        excl = (await s.execute(select(PaperExclusion))).scalar_one()
        assert (excl.kind, excl.value) == ("category", "Crypto") and "5" in excl.label
        other = Market(id="t2", question="Other?", yes_price=0.35, category="Politics")
        s.add(other)
        await s.commit()
        assert await guard.allows_auto_bet(s, other)       # 5 bets overall: below CLV_GUARD_MIN_BETS

        await _bets_moving_against(s, 4, category="Economy", start=10)
        assert not await guard.allows_auto_bet(s, other)   # 9 bets overall, all against: pause
        st = await guard.status(s)
        assert st["paused"] and st["n"] == 9 and st["avg_move"] == pytest.approx(-0.05)
        assert "9" in st["reason"]

    async with login_client("viewer") as api:
        assert (await api.post("/portfolio/guard/resume")).status_code == 403
        assert (await api.get("/portfolio")).json()["guard"]["paused"] is True
    async with login_client("admin") as api:
        r = await api.post("/portfolio/guard/resume")
        assert r.status_code == 200 and r.json()["paused"] is False and r.json()["n"] == 0   # old bets not counted
    async with SessionLocal() as s:
        assert await guard.allows_auto_bet(s, await s.get(Market, "t2"))


async def test_guard_ignores_bets_too_young_to_have_moved_and_manual_bets_are_not_blocked(db, book):
    async with SessionLocal() as s:
        await _bets_moving_against(s, 10)
        for b in (await s.execute(select(PaperBet))).scalars().all():
            b.created_at = NOW - timedelta(minutes=10)
        await s.commit()
        target = Market(id="t3", question="Next?", yes_price=0.35, category="Politics")
        s.add(target)
        await s.commit()
        assert await guard.allows_auto_bet(s, target)
        # Paused: an automatic bet is refused, a manual one still goes through
        m = await make_market(s, mid="m-fed", yes=0.35)
        s_row = await portfolio.get_settings(s)
        s_row.paused_at, s_row.paused_reason = NOW, "test"
        await s.commit()
        p = await make_prediction(s, m)
        ev = await portfolio.evaluate_prediction(s, m, p)
        assert ev.verdict in ("GO", "SMALL")
        assert await portfolio.maybe_place_bet(s, m, p, ev, placed_by="auto") is None
        assert await portfolio.maybe_place_bet(s, m, p, ev, placed_by="manual") is not None
