"""Real wallet: the simulated portfolio's buys, limit orders and sales followed by real orders."""
import pytest
from sqlalchemy import select

from backend.betting import live, orders, portfolio
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import LiveOrder, PaperBet, PaperOrder
from tests.conftest import login_client
from tests.test_portfolio import make_market, make_prediction, book  # noqa: F401  (fixture)


class FakeWallet:
    """The methods of live.ClobWallet, in memory: buys and sales now fill at once, limit orders wait."""

    def __init__(self, balance=100.0):
        self._balance = balance
        self.placed, self.cancelled, self.book = [], [], {}
        self.fail = False
        self.cancel_all_called = False

    def address(self):
        return "0xWALLET"

    def balance(self):
        return self._balance

    def place(self, token_id, action, price, shares, order_type, expiration=None):
        if self.fail:
            raise RuntimeError("not enough allowance")
        oid = f"0x{len(self.placed)}"
        self.placed.append({"id": oid, "token": token_id, "action": action, "price": price, "shares": shares,
                            "type": order_type, "expiration": expiration})
        if order_type == "FAK":
            self.book[oid] = {"status": "matched", "filled": shares}
        else:
            self.book[oid] = {"status": "live", "filled": 0.0}
        return {"id": oid, **self.book[oid]}

    def order(self, oid):
        return dict(self.book[oid])

    def cancel(self, oid):
        self.cancelled.append(oid)
        self.book[oid]["status"] = "canceled"

    def cancel_all(self):
        self.cancel_all_called = True


@pytest.fixture
def wallet():
    w = FakeWallet()
    live.set_client(w)
    yield w
    live.set_client(None)


async def _live_orders(s):
    return (await s.execute(select(LiveOrder).order_by(LiveOrder.created_at))).scalars().all()


async def test_off_by_default_nothing_is_sent(db, book, wallet):
    async with SessionLocal() as s:
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        assert (await s.execute(select(PaperBet))).scalar_one()
        assert await _live_orders(s) == [] and wallet.placed == []


async def test_auto_mode_buys_the_same_share_of_capital_on_the_real_balance(db, book, wallet):
    async with SessionLocal() as s:
        await live.set_mode(s, "auto")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        bet = (await s.execute(select(PaperBet))).scalar_one()
        row = (await _live_orders(s))[0]
        # Simulated: 40 $ of 1000 = 4%; real: 4% of 100 $ = 4 $, now (FAK), at most at the maximum price
        assert (row.action, row.order_type, row.side, row.status) == ("BUY", "FAK", "YES", "filled")
        assert row.paper_bet_id == bet.id and row.token_id == "yes-fed" and row.exchange_id == "0x0"
        assert row.usd == pytest.approx(4.0, abs=0.01) and row.price >= bet.avg_price
        assert row.filled_shares == pytest.approx(row.shares)
        assert (await live.exposure(s))["today"] == pytest.approx(row.usd)


async def test_limits_cap_the_real_order_and_too_small_orders_are_blocked(db, book, wallet, monkeypatch):
    wallet._balance = 10_000
    async with SessionLocal() as s:
        await live.set_mode(s, "auto")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        row = (await _live_orders(s))[0]
        assert row.usd == pytest.approx(settings.LIVE_MAX_ORDER_USD, abs=0.01)   # 4% of 10 000 $ capped at 10 $

        # Daily limit used up: the next buy is recorded as blocked, not sent
        monkeypatch.setattr(settings, "LIVE_MAX_DAILY_USD", 12.0)
        m2 = await make_market(s, mid="m-other", event="other")
        await portfolio.apply_economics(s, m2, await make_prediction(s, m2))
        blocked = (await _live_orders(s))[1]
        assert blocked.status == "blocked" and "giornaliero" in blocked.error
        assert len(wallet.placed) == 1


async def test_small_balance_blocks_the_order(db, book, wallet):
    wallet._balance = 20   # 4% = 0,80 $: under the minimum
    async with SessionLocal() as s:
        await live.set_mode(s, "auto")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        row = (await _live_orders(s))[0]
        assert row.status == "blocked" and wallet.placed == []
        assert (await s.execute(select(PaperBet))).scalar_one()   # the simulation goes on


async def test_exchange_error_is_recorded_and_the_simulation_goes_on(db, book, wallet):
    wallet.fail = True
    async with SessionLocal() as s:
        await live.set_mode(s, "auto")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        row = (await _live_orders(s))[0]
        assert row.status == "failed" and "allowance" in row.error
        assert (await s.execute(select(PaperBet))).scalar_one()


async def test_manual_mode_follows_only_bets_placed_by_hand(db, book, wallet):
    async with SessionLocal() as s:
        await live.set_mode(s, "manual")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        assert await _live_orders(s) == []
        bet = (await s.execute(select(PaperBet))).scalar_one()
        bet.status = "excluded"
        await s.commit()
    async with login_client("admin") as api:
        r = await api.post("/markets/m-fed/paper-bet")
        assert r.status_code == 200
    async with SessionLocal() as s:
        rows = await _live_orders(s)
        assert len(rows) == 1 and rows[0].placed_by == "manual" and rows[0].status == "filled"


async def test_a_sale_sells_the_real_shares_held(db, book, wallet):
    async with SessionLocal() as s:
        await live.set_mode(s, "auto")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        bet = (await s.execute(select(PaperBet))).scalar_one()
        bought = (await _live_orders(s))[0]
        assert await live.held_shares(s, bet) == pytest.approx(bought.shares)
        assert await portfolio.sell_bet(s, bet, m, "test")
        sale = (await _live_orders(s))[1]
        assert (sale.action, sale.order_type, sale.status) == ("SELL", "FAK", "filled")
        assert sale.shares == pytest.approx(bought.shares, abs=0.01)
        # Not below the simulated sale price minus the slippage
        assert sale.price >= bet.exit_price - settings.LIVE_SELL_SLIPPAGE - 1e-9
        assert await live.held_shares(s, bet) == pytest.approx(0, abs=0.01)


async def test_maker_orders_are_mirrored_cancelled_and_linked_on_fill(db, book, wallet, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_ORDER_MODE", "maker")
    async with SessionLocal() as s:
        await live.set_mode(s, "auto")
        m = await make_market(s)
        m.best_bid, m.best_ask = 0.34, 0.36
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        order = (await s.execute(select(PaperOrder))).scalar_one()
        row = (await _live_orders(s))[0]
        assert (row.order_type, row.status, row.price, row.paper_order_id) == ("GTD", "live", pytest.approx(order.limit_price), order.id)
        assert wallet.placed[0]["expiration"] == int(order.expires_at.timestamp()) + 60

        # A new forecast cancels the simulated order, and the real one with it
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        await s.refresh(row)
        assert row.status == "cancelled" and wallet.cancelled == [row.exchange_id]

        # The replacement order fills in the simulation: its real order now belongs to the bet
        new_row = (await _live_orders(s))[1]
        wallet.book[new_row.exchange_id] = {"status": "matched", "filled": new_row.shares}
        m.best_ask, m.best_bid = 0.35, 0.33
        await s.commit()
        assert (await orders.process_orders(s))["filled"] == 1
        await live.sync(s)
        bet = (await s.execute(select(PaperBet))).scalar_one()
        await s.refresh(new_row)
        assert new_row.paper_bet_id == bet.id and new_row.status == "filled"
        assert await live.held_shares(s, bet) == pytest.approx(new_row.shares)


async def test_api_needs_confirmation_and_stop_cancels_everything(db, book, wallet):
    async with login_client("admin") as api:
        r = await api.get("/portfolio/live")
        assert r.status_code == 200 and r.json()["mode"] == "off" and r.json()["balance"] == 100
        assert (await api.put("/portfolio/live", json={"mode": "auto"})).status_code == 400
        r = await api.put("/portfolio/live", json={"mode": "auto", "confirm": True})
        assert r.status_code == 200 and r.json()["mode"] == "auto" and r.json()["address"] == "0xWALLET"
        # Starting the simulation over is refused while real orders follow it
        assert (await api.post("/portfolio/reset", json={"bankroll": 500})).status_code == 409
        r = await api.post("/portfolio/live/stop")
        assert r.status_code == 200 and r.json()["mode"] == "off" and wallet.cancel_all_called
    async with login_client("viewer") as api:
        assert (await api.get("/portfolio/live")).status_code == 403


async def test_cannot_turn_on_without_a_key(db):
    live.set_client(None)
    async with login_client("admin") as api:
        r = await api.put("/portfolio/live", json={"mode": "auto", "confirm": True})
        assert r.status_code == 409 and "chiave privata" in r.json()["detail"]
        r = await api.get("/portfolio/live")
        assert r.json()["unavailable"] and r.json()["config"]["has_key"] is False


KEY = "ab" * 32


async def test_wallet_settings_from_the_dashboard(db, monkeypatch):
    """Key, address and signature type saved from the web app connect the wallet; the key is never sent back."""
    live.set_client(None)
    made = []

    class Wallet(FakeWallet):
        def __init__(self, cfg):
            super().__init__(balance=42.0)
            made.append(cfg)
    monkeypatch.setattr(live, "ClobWallet", Wallet)
    monkeypatch.setattr(live, "library_installed", lambda: True)
    funder = "0x" + "1" * 40
    async with login_client("admin") as api:
        assert (await api.put("/portfolio/live/config", json={"private_key": "xyz"})).status_code == 400
        assert (await api.put("/portfolio/live/config", json={"funder": "0x123"})).status_code == 400
        assert (await api.put("/portfolio/live/config", json={"max_order_usd": 0})).status_code == 400
        r = await api.put("/portfolio/live/config", json={"private_key": KEY, "funder": funder, "signature_type": 2,
                                                          "max_order_usd": 25, "max_daily_usd": 80})
        assert r.status_code == 200, r.text
        body = r.json()
        assert KEY not in r.text and body["config"] == {"has_key": True, "key_source": "app", "funder": funder, "signature_type": 2}
        assert body["balance"] == 42.0 and body["unavailable"] is None
        assert body["limits"]["order"] == 25 and body["limits"]["daily"] == 80
        assert made[-1].private_key == "0x" + KEY and made[-1].signature_type == 2
        # Same settings: the connection is reused
        await api.get("/portfolio/live")
        assert len(made) == 1
        # While real orders are on, the wallet cannot be swapped (the limits can)
        assert (await api.put("/portfolio/live", json={"mode": "manual", "confirm": True})).status_code == 200
        r = await api.put("/portfolio/live/config", json={"private_key": "cd" * 32})
        assert r.status_code == 400 and "Spegni" in r.json()["detail"]
        assert (await api.put("/portfolio/live/config", json={"max_open_usd": 300})).status_code == 200
        assert (await api.put("/portfolio/live", json={"mode": "off"})).status_code == 200
        # Removing the key turns it unavailable
        r = await api.put("/portfolio/live/config", json={"clear_key": True})
        assert r.json()["config"]["has_key"] is False and r.json()["unavailable"]
    async with login_client("viewer") as api:
        assert (await api.put("/portfolio/live/config", json={"max_order_usd": 5})).status_code == 403
    live.set_client(None)


async def test_limits_from_the_dashboard_cap_the_order(db, book, wallet):
    wallet._balance = 10_000
    async with SessionLocal() as s:
        await live.save_config(s, max_order_usd=3)
        await live.set_mode(s, "auto")
        m = await make_market(s)
        await portfolio.apply_economics(s, m, await make_prediction(s, m))
        assert (await _live_orders(s))[0].usd == pytest.approx(3, abs=0.01)
