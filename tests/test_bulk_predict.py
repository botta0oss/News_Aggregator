"""'Valuta tutti con Jev': background Jev forecast on every market with recent news."""
import asyncio

from sqlalchemy import func, select

from backend.ai.ratelimit import RateLimited
from backend.db.database import SessionLocal
from backend.db.models import MarketPrediction
from backend.markets import bulk, service
from tests.conftest import login_client
from tests.test_markets_e2e import FED_Q, OTHER_Q, entry, gamma_client, ingest_articles
from tests.test_polymarket import gamma_market

THIRD_Q = "Will the ECB cut interest rates in December 2026?"


async def setup_markets(monkeypatch):
    await ingest_articles(monkeypatch, [
        entry("Fed officials signal interest rates cut in December", "https://n.test/fed"),
        entry("ECB officials signal interest rates cut in December", "https://n.test/ecb"),
    ])
    markets = [gamma_market(1, FED_Q, yes="0.35"), gamma_market(2, OTHER_Q, yes="0.10"), gamma_market(3, THIRD_Q, yes="0.50")]
    async with gamma_client(markets) as client:
        async with SessionLocal() as session:
            await service.sync_markets(session, client=client)
    async with SessionLocal() as session:
        await service.refresh_links(session)


async def prediction_count():
    async with SessionLocal() as session:
        return (await session.execute(select(func.count(MarketPrediction.id)))).scalar()


async def test_predict_all_flow(db, monkeypatch, jev_client):
    await setup_markets(monkeypatch)
    jev_client(noul_value=0.8, score_value=3.0)

    async with login_client("viewer") as api:
        assert (await api.get("/markets/predict-all")).status_code == 403
        assert (await api.post("/markets/predict-all")).status_code == 403

    async with login_client("admin") as api:
        st = (await api.get("/markets/predict-all")).json()
        assert st["running"] is False and st["jev_enabled"] is True
        assert st["eligible"] == {"all": 2, "new": 2}  # Bitcoin market has no related news

        r = await api.post("/markets/predict-all", params={"refresh_first": False})
        assert r.status_code == 202 and r.json()["running"] is True
        assert (await api.post("/markets/predict-all")).status_code == 409  # one run at a time
        await bulk.wait()

        st = (await api.get("/markets/predict-all")).json()
        assert st["running"] is False and st["message"] == "Completata"
        assert (st["total"], st["done"], st["failed"], st["skipped"]) == (2, 2, 0, 0)
        assert st["eligible"] == {"all": 2, "new": 0}
        assert await prediction_count() == 2

        # Only new: nothing changed since the last forecasts
        await api.post("/markets/predict-all", params={"refresh_first": False, "only_new": True})
        await bulk.wait()
        st = (await api.get("/markets/predict-all")).json()
        assert (st["total"], st["done"]) == (0, 0)
        assert await prediction_count() == 2


async def test_predict_all_without_jev(db):
    async with login_client("admin") as api:
        assert (await api.post("/markets/predict-all")).status_code == 503


async def test_predict_all_waits_out_rate_limits_and_stops(db, monkeypatch, jev_client):
    await setup_markets(monkeypatch)
    jev_client()
    real_predict = service.predict_market
    calls = []

    async def flaky(session, market, max_wait=None):
        calls.append(market.id)
        if len(calls) == 1:
            raise RateLimited("jev", 0.01)
        if len(calls) == 3:
            raise RuntimeError("boom")
        return await real_predict(session, market, max_wait=max_wait)

    monkeypatch.setattr(service, "predict_market", flaky)
    assert bulk.start(refresh_first=False)
    await bulk.wait()
    assert calls[0] == calls[1]  # same market retried after the cooldown
    assert (bulk.job.done, bulk.job.failed) == (1, 1)
    assert bulk.job.errors and "boom" in bulk.job.errors[0]

    # Stop: the job ends before evaluating the next market
    async def slow(session, market, max_wait=None):
        await asyncio.sleep(0.2)
        return await real_predict(session, market, max_wait=max_wait)

    monkeypatch.setattr(service, "predict_market", slow)
    assert bulk.start(refresh_first=False)
    await asyncio.sleep(0.05)
    assert bulk.stop()
    await bulk.wait()
    assert bulk.job.stopped and bulk.job.message == "Interrotta" and bulk.job.done == 1


async def test_predict_all_stops_after_repeated_failures(db, monkeypatch, jev_client):
    await setup_markets(monkeypatch)
    jev_client()

    async def broken(session, market, max_wait=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(service, "predict_market", broken)
    monkeypatch.setattr(bulk, "MAX_CONSECUTIVE_FAILURES", 2)
    assert bulk.start(refresh_first=False)
    await bulk.wait()
    assert (bulk.job.done, bulk.job.failed) == (0, 2)
    assert bulk.job.message.startswith("Fermata dopo 2 errori")
