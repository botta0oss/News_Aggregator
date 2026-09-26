"""Second opinion from a free model and maker limit orders in the simulated portfolio."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.ai import second_opinion
from backend.betting import orders, portfolio
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import MarketPrediction, PaperBet, PaperOrder
from backend.markets.service import predict_market
from tests.conftest import login_client
from tests.test_portfolio import NOW, book, make_market, make_prediction  # noqa: F401  (fixture)


# ---------- 2. Second opinion ----------

def test_parse_probability_from_json_numbers_and_percentages():
    assert second_opinion.parse_probability('{"base_rate": 0.2, "probability": 0.35, "reason": "x"}') == pytest.approx(0.35)
    assert second_opinion.parse_probability('Sure! ```json\n{"probability": 62}\n```') == pytest.approx(0.62)
    assert second_opinion.parse_probability('probability: 40%') == pytest.approx(0.40)
    assert second_opinion.parse_probability('{"probability": 1.0}') == pytest.approx(0.99)   # never certain
    assert second_opinion.parse_probability("I cannot say") is None
    assert second_opinion.parse_probability(None) is None


async def test_ask_falls_back_to_the_next_provider(monkeypatch):
    calls = []

    async def gemini(prompt):
        calls.append("gemini")
        return "no idea"

    async def groq(prompt):
        calls.append("groq")
        assert "resolves YES" in prompt and "Will it rain?" in prompt and "price" not in prompt.split("{", 1)[1]
        return '{"probability": 0.7}'
    monkeypatch.setitem(second_opinion.PROVIDERS, "gemini", gemini)
    monkeypatch.setitem(second_opinion.PROVIDERS, "groq", groq)
    assert await second_opinion.ask({"market": {"question": "Will it rain?"}}) == (pytest.approx(0.7), "groq")
    assert calls == ["gemini", "groq"]


def test_agreement_means_the_same_side_of_the_price(monkeypatch):
    assert second_opinion.agrees(0.5, 0.35, "BUY_YES") is True
    assert second_opinion.agrees(0.3, 0.35, "BUY_YES") is False
    assert second_opinion.agrees(0.3, 0.35, "BUY_NO") is True
    assert second_opinion.agrees(0.5, 0.35, "HOLD") is None
    monkeypatch.setattr(settings, "SECOND_OPINION_MARGIN", 0.2)
    assert second_opinion.agrees(0.5, 0.35, "BUY_YES") is False    # not far enough beyond the price


def _second(monkeypatch, value, provider="gemini"):
    asked = []

    async def ask(state):
        asked.append(state)
        return (value, provider) if value is not None else None
    monkeypatch.setattr(second_opinion, "is_available", lambda: True)
    monkeypatch.setattr(second_opinion, "ask", ask)
    return asked


async def test_no_bet_when_the_second_opinion_disagrees(db, book, jev_client, monkeypatch):
    jev_client(noul_value=0.8, score_value=3.0)
    asked = _second(monkeypatch, 0.30)
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        p = await predict_market(s, m)
        assert asked and p.second_opinion == pytest.approx(0.30) and p.second_opinion_provider == "gemini"
        assert p.signal == "BUY_YES" and p.economics["verdict"] == "NO"
        reason = next(r for r in p.economics["reasons"] if r["code"] == "second_opinion")
        assert reason["blocking"] and "Gemini" in reason["text"] and "30%" in reason["text"]
        assert (await s.execute(select(PaperBet))).first() is None


async def test_bet_when_the_second_opinion_agrees(db, book, jev_client, monkeypatch):
    jev_client(noul_value=0.8, score_value=3.0)
    _second(monkeypatch, 0.55, "groq")
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        p = await predict_market(s, m)
        assert p.economics["verdict"] in ("GO", "SMALL")
        assert (await s.execute(select(PaperBet))).scalar_one().side == "YES"


async def test_second_opinion_not_asked_without_a_possible_buy_and_optional_by_default(db, book, jev_client, monkeypatch):
    jev_client(noul_value=0.36, score_value=3.0)        # Jev close to the price: no buy possible
    asked = _second(monkeypatch, 0.9)
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.35)
        p = await predict_market(s, m)
        assert not asked and p.second_opinion is None
    # Nobody answers: by default the buy goes on with Jev alone; with SECOND_OPINION_REQUIRED it does not
    jev_client(noul_value=0.8, score_value=3.0)
    _second(monkeypatch, None)
    async with SessionLocal() as s:
        m = await make_market(s, mid="m-2", yes=0.35, event="e2")
        p = await predict_market(s, m)
        assert p.second_opinion is None and p.economics["verdict"] in ("GO", "SMALL")
        monkeypatch.setattr(settings, "SECOND_OPINION_REQUIRED", True)
        ev = await portfolio.evaluate_prediction(s, m, p)
        assert ev.verdict == "NO" and any(r["code"] == "second_opinion" for r in ev.as_dict()["reasons"])


# ---------- 5. Maker limit orders ----------

@pytest.fixture
def maker(monkeypatch):
    monkeypatch.setattr(settings, "PAPER_ORDER_MODE", "maker")


async def _order_on_new_market(s, mid="m-fed", **kw):
    m = await make_market(s, mid=mid, yes=0.35, **kw)
    m.best_bid, m.best_ask = 0.34, 0.36
    p = await make_prediction(s, m)
    await portfolio.apply_economics(s, m, p)
    return m, p


async def test_automatic_buy_waits_in_the_book_one_tick_above_the_bid(db, book, maker):
    async with SessionLocal() as s:
        cash_before = (await portfolio.ledger(s))["cash"]
        m, p = await _order_on_new_market(s)
        assert (await s.execute(select(PaperBet))).first() is None
        order = (await s.execute(select(PaperOrder))).scalar_one()
        assert (order.status, order.side, order.limit_price) == ("pending", "YES", pytest.approx(0.35))
        assert order.taker_price == pytest.approx(p.economics["avg_price"]) and order.taker_price > order.limit_price
        led = await portfolio.ledger(s)
        assert led["pending_orders"] == pytest.approx(order.outlay) and led["cash"] == pytest.approx(cash_before - order.outlay)
        assert (await portfolio.exposure_for(s, m)).market == pytest.approx(order.outlay)
        assert (await portfolio.summary(s))["total_value"] == pytest.approx(cash_before)   # reserved, not spent

        # The ask stays above the limit: nothing happens
        assert (await orders.process_orders(s))["filled"] == 0
        # A seller comes down to 35¢: filled at the limit, without fee
        m.best_ask, m.best_bid = 0.35, 0.33
        await s.commit()
        assert (await orders.process_orders(s))["filled"] == 1
        bet = (await s.execute(select(PaperBet))).scalar_one()
        await s.refresh(order)
        assert (bet.entry, bet.avg_price, bet.fee, bet.shares) == ("maker", pytest.approx(0.35), 0.0, pytest.approx(order.shares))
        assert order.status == "filled" and order.bet_id == bet.id
        assert (await portfolio.ledger(s))["pending_orders"] == 0
        stats = await orders.stats(s)
        assert stats["counts"]["filled"] == 1 and stats["fill_rate"] == 1.0 and stats["saved"] > 0


async def test_orders_expire_are_replaced_by_new_forecasts_and_cancelled_when_paused(db, book, maker):
    async with SessionLocal() as s:
        m, p = await _order_on_new_market(s)
        first = (await s.execute(select(PaperOrder))).scalar_one()
        # A new forecast cancels it and places a new one
        p2 = await make_prediction(s, m)
        await portfolio.apply_economics(s, m, p2)
        rows = (await s.execute(select(PaperOrder).order_by(PaperOrder.created_at))).scalars().all()
        assert [o.status for o in rows] == ["cancelled", "pending"] and rows[0].id == first.id
        # Expiry
        rows[1].expires_at = NOW - timedelta(minutes=1)
        await s.commit()
        assert (await orders.process_orders(s))["expired"] == 1

        m2, _ = await _order_on_new_market(s, mid="m-2", event="e2")
        row = await portfolio.get_settings(s)
        row.paused_at = NOW
        await s.commit()
        assert (await orders.process_orders(s))["cancelled"] == 1
        assert (await orders.stats(s))["fill_rate"] == 0.0


async def test_a_market_that_closes_against_the_side_fills_the_order(db, book, maker):
    async with SessionLocal() as s:
        m, _ = await _order_on_new_market(s)
        m.closed, m.last_trading_price, m.resolution = True, 0.0, "no"
        await s.commit()
        assert (await orders.process_orders(s))["filled"] == 1
        await portfolio.settle_bets(s)
        bet = (await s.execute(select(PaperBet))).scalar_one()
        assert bet.status == "lost" and bet.pnl == pytest.approx(-bet.stake)


async def test_manual_bet_takes_the_ask_and_replaces_the_order(db, book, maker):
    async with SessionLocal() as s:
        m, _ = await _order_on_new_market(s)
    async with login_client("viewer") as api:
        orders_now = (await api.get("/portfolio/orders")).json()
        assert len(orders_now) == 1 and orders_now[0]["limit_price"] == pytest.approx(0.35)
        assert (await api.post(f"/portfolio/orders/{orders_now[0]['id']}/cancel")).status_code == 403
        econ = (await api.get("/markets/m-fed/economics")).json()
        assert len(econ["pending_orders"]) == 1
        assert (await api.get("/portfolio")).json()["orders"]["counts"]["pending"] == 1
    async with login_client("admin") as api:
        r = await api.post("/markets/m-fed/paper-bet")
        assert r.status_code == 200 and r.json()["entry"] == "taker" and r.json()["fee"] >= 0
        assert (await api.get("/portfolio/orders")).json() == []
        closed = (await api.get("/portfolio/orders", params={"status": "closed"})).json()
        assert closed[0]["status"] == "cancelled"


async def test_cancel_an_order_by_hand(db, book, maker):
    async with SessionLocal() as s:
        await _order_on_new_market(s)
    async with login_client("admin") as api:
        oid = (await api.get("/portfolio/orders")).json()[0]["id"]
        r = await api.post(f"/portfolio/orders/{oid}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert (await api.post(f"/portfolio/orders/{oid}/cancel")).status_code == 409


def test_maker_price_never_touches_the_ask_nor_passes_the_maximum():
    from types import SimpleNamespace
    m = SimpleNamespace(best_bid=0.40, best_ask=0.41, yes_price=0.405)
    ev = SimpleNamespace(side="YES", limit_price=0.60, best_price=0.41)
    assert orders.maker_price(m, ev) == pytest.approx(0.40)          # one-tick spread: join the bid
    ev.limit_price = 0.38
    assert orders.maker_price(m, ev) == pytest.approx(0.38)          # never above the maximum price
    m2 = SimpleNamespace(best_bid=0.30, best_ask=0.40, yes_price=0.35)
    ev2 = SimpleNamespace(side="NO", limit_price=0.9, best_price=None)
    assert orders.maker_price(m2, ev2) == pytest.approx(0.61)        # NO bid = 1 − 0.40, ask = 1 − 0.30
