"""News-vs-price alerts: detection, immediate Jev evaluation, Telegram, price follow-ups, API."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update

from backend.alerts import service, telegram
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Alert, AlertSettings, Article, MarketArticleLink
from backend.markets import service as market_service
from tests.conftest import login_client
from tests.test_markets_e2e import FED_Q, entry, gamma_client
from tests.test_polymarket import gamma_market


@pytest.fixture
def tg(monkeypatch):
    """Telegram configured, requests captured; set `tg.status` to make it fail."""
    sent = SimpleNamespace(messages=[], status=200)

    def handler(request: httpx.Request):
        sent.messages.append(json.loads(request.content))
        if sent.status != 200:
            return httpx.Response(sent.status, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(handler))
    return sent


@pytest.fixture
def live_price(monkeypatch):
    """Price returned by Polymarket when the alert is raised and at the follow-ups."""
    state = SimpleNamespace(price=0.35)

    async def fetch_market(market_id, client=None):
        return SimpleNamespace(yes_price=state.price, closed=False)

    monkeypatch.setattr(service.polymarket, "fetch_market", fetch_market)
    return state


async def add_news(entries):
    from backend.db.models import Source
    from backend.ingestor import scheduler
    async with SessionLocal() as session:
        source = (await session.execute(select(Source).where(Source.name == "Reuters"))).scalar_one_or_none()
        if source is None:
            source = Source(name="Reuters", url="https://example.test/rss", category_hint="Economy")
            session.add(source)
            await session.commit()
        await scheduler.store_entries(session, source.id, entries)


async def setup_fed(monkeypatch, published_hours_ago=0.5, title="Fed officials signal interest rates cut in December"):
    e = entry(title, f"https://n.test/{abs(hash(title))}")
    e["published_at"] = datetime.now(timezone.utc) - timedelta(hours=published_hours_ago)
    await add_news([e])
    market = {**gamma_market(1, FED_Q, yes="0.35"), "liquidityNum": 200_000}  # liquid enough for the presets
    async with gamma_client([market]) as client:
        async with SessionLocal() as session:
            await market_service.sync_markets(session, client=client)
    async with SessionLocal() as session:
        await market_service.refresh_links(session)


async def run():
    async with SessionLocal() as session:
        return await service.run_alerts(session)


async def test_fresh_news_raises_an_alert_and_notifies(db, monkeypatch, jev_client, tg, live_price):
    jev_client(noul_value=0.8, score_value=3.0)
    await setup_fed(monkeypatch)
    live_price.price = 0.34  # the alert uses the price of this moment

    stats = await run()
    assert stats["evaluated"] == 1 and stats["opportunities"] == 1 and stats["notified"] == 1
    async with SessionLocal() as session:
        alert = (await session.execute(select(Alert))).scalar_one()
        assert alert.price == 0.34 and alert.side == "YES" and alert.verdict in ("GO", "SMALL")
        assert alert.notified_at is not None and alert.prediction_id is not None
    msg = tg.messages[0]
    assert msg["chat_id"] == "42" and msg["parse_mode"] == "HTML" and msg["disable_notification"] is False
    assert "Compra SÌ" in msg["text"] and FED_Q in msg["text"] and "34¢" in msg["text"]

    # Every link is looked at once
    assert (await run())["triggers"] == 0


async def test_cooldown_budget_age_and_categories(db, monkeypatch, jev_client, tg, live_price):
    jev_client(noul_value=0.8, score_value=3.0)
    # Old news does not trigger
    await setup_fed(monkeypatch, published_hours_ago=10)
    assert (await run())["triggers"] == 0

    # Category filter
    async with SessionLocal() as session:
        await service.update_settings(session, {"categories": ["Sports"]})
    await setup_fed(monkeypatch, title="Fed officials say December interest rates cut is likely")
    assert (await run())["triggers"] == 0

    # Budget exhausted
    async with SessionLocal() as session:
        await service.update_settings(session, {"categories": [], "daily_budget": 0})
    await setup_fed(monkeypatch, title="Fed interest rates cut in December gains support")
    stats = await run()
    assert stats["triggers"] == 1 and stats["evaluated"] == 0 and stats["skipped_budget"] == 1

    # Evaluated once, then the market is in cooldown
    async with SessionLocal() as session:
        await service.update_settings(session, {"daily_budget": 10})
    await setup_fed(monkeypatch, title="Fed chair backs December interest rates cut")
    assert (await run())["evaluated"] == 1
    await setup_fed(monkeypatch, title="Fed minutes point to December interest rates cut")
    stats = await run()
    assert stats["triggers"] == 1 and stats["evaluated"] == 0


async def test_disabled_or_without_jev_does_nothing(db, monkeypatch, tg, live_price):
    await setup_fed(monkeypatch)
    assert (await run())["triggers"] == 0  # Jev not configured
    async with SessionLocal() as session:
        links = (await session.execute(select(MarketArticleLink))).scalars().all()
        assert links and not any(l.alert_checked for l in links)  # checked later, when Jev is available


async def test_telegram_failure_is_recorded(db, monkeypatch, jev_client, tg, live_price):
    jev_client(noul_value=0.8, score_value=3.0)
    tg.status = 400
    await setup_fed(monkeypatch)
    stats = await run()
    assert stats["opportunities"] == 1 and stats["notified"] == 0
    async with SessionLocal() as session:
        alert = (await session.execute(select(Alert))).scalar_one()
        assert alert.notified_at is None and "chat not found" in alert.notify_error
        assert "secret" not in alert.notify_error


async def test_no_edge_is_recorded_but_not_notified(db, monkeypatch, jev_client, tg, live_price):
    jev_client(noul_value=0.36, score_value=1.0)  # Jev agrees with the price
    await setup_fed(monkeypatch)
    stats = await run()
    assert stats["evaluated"] == 1 and stats["opportunities"] == 0 and tg.messages == []
    async with login_client("viewer") as api:
        assert (await api.get("/alerts")).json() == []
        assert len((await api.get("/alerts", params={"kind": "all"})).json()) == 1


async def test_followups_measure_the_move(db, monkeypatch, jev_client, tg, live_price):
    jev_client(noul_value=0.8, score_value=3.0)
    await setup_fed(monkeypatch)
    await run()
    async with SessionLocal() as session:
        await session.execute(update(Alert).values(created_at=datetime.now(timezone.utc) - timedelta(hours=2)))
        await session.commit()
    live_price.price = 0.41  # the market moved toward YES after the alert
    async with SessionLocal() as session:
        assert await service.record_followups(session) == 1
        assert await service.record_followups(session) == 0  # nothing new due
        alert = (await session.execute(select(Alert))).scalar_one()
        assert set(alert.followups) == {"15m", "1h"}
        assert service.favorable_move(alert, service.followup_price(alert, "1h")) == pytest.approx(0.06)
        summary = await service.summary(session)
    assert summary["opportunities"] == 1 and summary["moves"]["1h"]["share_favorable"] == 1.0
    assert summary["moves"]["6h"]["count"] == 0

    async with login_client("viewer") as api:
        items = (await api.get("/alerts")).json()
        assert items[0]["followups"]["1h"] == 0.41 and items[0]["moves"]["1h"] == pytest.approx(0.06)
        assert items[0]["news"]["source"] == "Reuters"


def test_quiet_hours():
    s = AlertSettings(quiet_start=23, quiet_end=7)
    tz = timezone(timedelta(hours=2))  # Europe/Rome in summer
    at = lambda h: datetime(2026, 7, 1, h, 30, tzinfo=tz)  # noqa: E731
    assert service.in_quiet_hours(s, at(23)) and service.in_quiet_hours(s, at(3))
    assert not service.in_quiet_hours(s, at(7)) and not service.in_quiet_hours(s, at(15))
    assert not service.in_quiet_hours(AlertSettings(quiet_start=None, quiet_end=None), at(3))


def test_settings_validation():
    with pytest.raises(ValueError):
        service.validate_settings({"categories": ["Weather"]})
    with pytest.raises(ValueError):
        service.validate_settings({"min_match": 0.1})
    with pytest.raises(ValueError):
        service.validate_settings({"quiet_start": 25})
    assert service.validate_settings({"daily_budget": 12.0, "quiet_start": None}) == {"daily_budget": 12, "quiet_start": None}


async def test_settings_api_and_permissions(db, monkeypatch):
    async with login_client("viewer") as api:
        s = (await api.get("/alerts/settings")).json()
        assert s["enabled"] is True and s["telegram_configured"] is False
        assert (await api.put("/alerts/settings", json={"enabled": False})).status_code == 403
        assert (await api.post("/alerts/test-telegram")).status_code == 403
    async with login_client("admin") as api:
        r = await api.put("/alerts/settings", json={"categories": ["Economy"], "quiet_start": 23, "quiet_end": 7})
        assert r.status_code == 200 and r.json()["categories"] == ["Economy"] and r.json()["quiet_start"] == 23
        assert (await api.put("/alerts/settings", json={"min_match": 5})).status_code == 422
        r = await api.put("/alerts/settings", json={"quiet_start": None, "quiet_end": None})
        assert r.json()["quiet_start"] is None
        assert (await api.post("/alerts/test-telegram")).status_code == 503
        assert (await api.get("/alerts/summary")).json()["opportunities"] == 0


async def test_test_message(db, tg):
    async with login_client("admin") as api:
        assert (await api.post("/alerts/test-telegram")).status_code == 200
    assert "News × Markets" in tg.messages[0]["text"]


async def test_existing_links_are_not_alerted_after_upgrade(db):
    """The migration marks links made before alerts existed as already checked."""
    from sqlalchemy import text
    async with SessionLocal() as session:
        default = (await session.execute(text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'market_article_links' AND column_name = 'alert_checked'"
        ))).scalar()
    assert default == "false"
