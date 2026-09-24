from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.betting import portfolio
from backend.db.database import SessionLocal
from backend.db.models import (
    Article, Market, MarketArticleLink, MarketPrediction, PaperBet, ProcessedArticle, Source,
)
from backend.markets import polymarket
from backend.markets.forecast import compute_signal
from tests.conftest import login_client

NOW = datetime.now(timezone.utc)


@pytest.fixture
def book(monkeypatch):
    """Order books by token id; a missing token raises like an unreachable CLOB."""
    books = {
        "yes-fed": polymarket.OrderBook(asks=[(0.36, 500), (0.37, 1000), (0.40, 3000)], bids=[(0.34, 800)]),
        "no-fed": polymarket.OrderBook(asks=[(0.66, 2000)], bids=[(0.64, 800)]),
    }

    async def fetch(token_id, client=None):
        if token_id not in books:
            raise RuntimeError("CLOB unreachable")
        return books[token_id]
    monkeypatch.setattr(polymarket, "fetch_order_book", fetch)
    return books


async def make_market(s, mid="m-fed", yes=0.35, liquidity=50_000, days=30, event="fed-december", tokens=True, category="Economy"):
    m = Market(id=mid, question=f"Question {mid}?", event_slug=event, yes_price=yes, liquidity=liquidity,
               volume=1e6, end_date=NOW + timedelta(days=days),
               yes_token_id="yes-fed" if tokens else None, no_token_id="no-fed" if tokens else None)
    s.add(m)
    src = (await s.execute(select(Source))).scalars().first()
    if src is None:
        src = Source(name="Reuters", url="https://r.test/rss")
        s.add(src)
        await s.flush()
    a = Article(source_id=src.id, title=f"News for {mid}", url=f"https://n.test/{mid}", url_hash=f"h-{mid}", fetched_at=NOW)
    s.add(a)
    await s.flush()
    s.add(ProcessedArticle(article_id=a.id, category=category))
    s.add(MarketArticleLink(market_id=mid, article_id=a.id, similarity=0.8))
    await s.commit()
    return m


async def make_prediction(s, market, p_jev=0.8, evidence=0.75):
    sig = compute_signal(p_jev, market.yes_price, evidence)
    p = MarketPrediction(market_id=market.id, market_probability=market.yes_price, model_probability=p_jev,
                         evidence_strength=evidence, blended_probability=sig.blended_probability,
                         model_weight=sig.model_weight, edge=sig.edge, signal=sig.signal,
                         kelly_fraction=sig.kelly_fraction, article_count=1)
    s.add(p)
    await s.commit()
    return p


async def test_auto_bet_with_order_book_and_no_duplicates(db, book):
    async with SessionLocal() as s:
        m = await make_market(s)
        p = await make_prediction(s, m)
        ev = await portfolio.apply_economics(s, m, p)
        assert ev.verdict == "GO" and ev.quote_source == "book"
        assert p.economics["verdict"] == "GO" and p.economics["stake"] == pytest.approx(40)  # 4% of 1000
        assert m.category == "Economy"
        bets = (await s.execute(select(PaperBet))).scalars().all()
        assert len(bets) == 1 and bets[0].side == "YES" and bets[0].avg_price == pytest.approx(0.36)
        # A second forecast on the same market does not stack a second bet (and the market cap is full)
        p2 = await make_prediction(s, m)
        ev2 = await portfolio.apply_economics(s, m, p2)
        assert ev2.verdict == "NO" and any(r["code"] == "exposure_cap" for r in p2.economics["reasons"])
        assert len((await s.execute(select(PaperBet))).scalars().all()) == 1
        led = await portfolio.ledger(s)
        assert led["cash"] == pytest.approx(960)


async def test_estimate_when_book_unavailable(db, book):
    async with SessionLocal() as s:
        m = await make_market(s, tokens=False)
        ev = await portfolio.apply_economics(s, m, await make_prediction(s, m))
        assert ev.quote_source == "estimate" and ev.notes


@pytest.mark.parametrize("kind", ["market", "event", "category"])
async def test_exclusions_block_auto_bets(db, book, kind):
    async with SessionLocal() as s:
        m = await make_market(s)
        value = {"market": "m-fed", "event": "fed-december", "category": "Economy"}[kind]
        await portfolio.add_exclusion(s, kind, value)
        ev = await portfolio.apply_economics(s, m, await make_prediction(s, m))
        assert ev.verdict == "GO"  # the evaluation is still computed and shown
        assert (await s.execute(select(PaperBet))).first() is None


async def test_auto_off_and_manual_bet(db, book):
    async with SessionLocal() as s:
        await portfolio.update_settings(s, auto_paper=False)
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        assert (await s.execute(select(PaperBet))).first() is None
    async with login_client("admin") as api:
        r = await api.post("/markets/m-fed/paper-bet")
        assert r.status_code == 200 and r.json()["placed_by"] == "manual"
        assert (await api.post("/markets/m-fed/paper-bet")).status_code == 409


async def test_settlement_pnl_curve_and_exclusion(db, book):
    async with SessionLocal() as s:
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        m.yes_price = 0.5
        await s.commit()
        summ = await portfolio.summary(s)
        bet = (await s.execute(select(PaperBet))).scalar_one()
        assert summ["unrealized_pnl"] == pytest.approx(bet.shares * 0.5 - 40)
        m.resolved_yes, m.closed = True, True
        await s.commit()
        assert await portfolio.settle_bets(s) == 1
        await s.refresh(bet)
        assert bet.status == "won" and bet.pnl == pytest.approx(bet.shares - 40)
        summ = await portfolio.summary(s)
        assert summ["counts"]["won"] == 1 and summ["hit_rate"] == 1.0
        assert summ["equity"] == pytest.approx(1000 + bet.pnl)
        assert [p["equity"] for p in summ["equity_curve"]] == [1000, pytest.approx(1000 + bet.pnl, abs=0.01)]

    async with login_client("admin") as api:
        assert (await api.post(f"/portfolio/bets/{bet.id}/exclude")).status_code == 200
        assert (await api.get("/portfolio")).json()["equity"] == pytest.approx(1000)
        r = await api.post(f"/portfolio/bets/{bet.id}/include")
        assert r.json()["status"] == "won"  # re-included and settled again
        assert (await api.get("/portfolio")).json()["equity"] == pytest.approx(1000 + bet.pnl)


async def test_losing_no_bet(db, book):
    book["no-fed"] = polymarket.OrderBook(asks=[(0.36, 2000)], bids=[(0.34, 500)])  # NO costs ~36¢ when YES is 65¢
    async with SessionLocal() as s:
        m = await make_market(s, yes=0.65)
        p = await make_prediction(s, m, p_jev=0.15)
        ev = await portfolio.apply_economics(s, m, p)
        assert ev.side == "NO" and ev.verdict in ("GO", "SMALL")
        m.resolved_yes = True
        await s.commit()
        await portfolio.settle_bets(s)
        bet = (await s.execute(select(PaperBet))).scalar_one()
        assert bet.status == "lost" and bet.pnl == pytest.approx(-(bet.stake + bet.fee))


async def test_portfolio_api_permissions_settings_reset(db, book):
    async with SessionLocal() as s:
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
    async with login_client("viewer") as viewer:
        body = (await viewer.get("/portfolio")).json()
        assert body["settings"]["preset"] == "bilanciato" and len(body["presets"]) == 3
        assert body["counts"]["open"] == 1 and body["invested"] == pytest.approx(40)
        assert (await viewer.get("/portfolio/bets", params={"status": "open"})).json()[0]["side"] == "YES"
        eco = (await viewer.get("/markets/m-fed/economics", params={"preset": "prudente"})).json()
        assert eco["preset"]["key"] == "prudente" and eco["open_bet"] is not None
        assert (await viewer.put("/portfolio/settings", json={"preset": "aggressivo"})).status_code == 403
        assert (await viewer.post("/portfolio/exclusions", json={"kind": "market", "value": "m-fed"})).status_code == 403
    async with login_client("admin") as api:
        assert (await api.put("/portfolio/settings", json={"preset": "aggressivo", "auto_paper": False})).json()["preset"] == "aggressivo"
        r = await api.post("/portfolio/exclusions", json={"kind": "category", "value": "Crypto", "label": "Crypto"})
        assert r.status_code == 201
        ex = (await api.get("/portfolio/exclusions")).json()
        assert ex[0]["value"] == "Crypto"
        assert (await api.delete(f"/portfolio/exclusions/{ex[0]['id']}")).status_code == 204
        assert (await api.post("/portfolio/reset", json={"bankroll": 5})).status_code == 422
        assert (await api.post("/portfolio/reset", json={"bankroll": 2500, "preset": "prudente"})).status_code == 200
        body = (await api.get("/portfolio")).json()
        assert body["equity"] == 2500 and body["counts"]["open"] == 0 and body["profile"]["key"] == "prudente"


async def test_split_resolution_pays_half(db, book):
    async with SessionLocal() as s:
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        bet = (await s.execute(select(PaperBet))).scalar_one()
        m.resolution, m.closed = "split", True
        await s.commit()
        assert await portfolio.settle_bets(s) == 1
        await s.refresh(bet)
        assert bet.status == "void" and bet.payout == pytest.approx(bet.shares * 0.5)
        assert bet.pnl == pytest.approx(bet.shares * 0.5 - bet.stake - bet.fee)
        summ = await portfolio.summary(s)
        assert summ["counts"]["void"] == 1 and summ["hit_rate"] is None
        assert summ["equity"] == pytest.approx(1000 + bet.pnl)


async def test_fee_rate_from_category_unless_the_market_is_fee_free(db, book, monkeypatch):
    from backend.betting import fees
    async with SessionLocal() as s:
        m = await make_market(s)
        m.category = "Crypto"
        assert (await portfolio.build_quote(m, "YES")).fee_bps == 0      # conftest: CLOB says fee-free

        async def enabled(token_id, client=None):
            return True
        monkeypatch.setattr(fees, "fees_enabled", enabled)
        assert (await portfolio.build_quote(m, "YES")).fee_bps == pytest.approx(700)
        m.category = "Foreign Affairs"
        assert (await portfolio.build_quote(m, "YES")).fee_bps == 0

        async def unknown(token_id, client=None):
            return None
        monkeypatch.setattr(fees, "fees_enabled", unknown)
        m.category = "Politics"
        assert (await portfolio.build_quote(m, "YES")).fee_bps == pytest.approx(400)


def test_parse_fee_switch():
    from backend.betting.fees import parse_fee_enabled
    assert parse_fee_enabled({"base_fee": 0}) is False
    assert parse_fee_enabled({"base_fee": 1000}) is True
    assert parse_fee_enabled({"other": 1}) is None and parse_fee_enabled("x") is None
