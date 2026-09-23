"""End-to-end: ingestion -> news/market linking -> Jev forecast -> API, on a real Postgres + pgvector.

Polymarket and TypeSafe are mocked at the HTTP layer. Skipped if the test database is unreachable.
"""
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import text, select

from backend.config import settings
from backend.db.database import engine, SessionLocal
from backend.db.models import Base, Article, Market, MarketArticleLink, ProcessedArticle, Source
from backend.markets import service
from backend.ingestor import scheduler
from tests.conftest import fake_embedding
from tests.test_polymarket import gamma_market

FED_Q = "Will the Fed cut interest rates in December 2026?"
OTHER_Q = "Will Bitcoin reach 200k dollars in 2026?"


@pytest.fixture
async def db(monkeypatch):
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Test database not available: {e}")
    monkeypatch.setattr(service, "get_title_embedding", fake_embedding)
    monkeypatch.setattr(scheduler, "get_title_embedding", fake_embedding)
    monkeypatch.setattr(settings, "MARKET_MATCH_THRESHOLD", 0.5)
    yield
    await engine.dispose()


def gamma_client(active, single=None):
    single = single or {}

    def handler(request: httpx.Request):
        if request.url.path == "/markets":
            offset = int(request.url.params.get("offset", 0))
            return httpx.Response(200, json=active if offset == 0 else [])
        market_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=single[market_id]) if market_id in single else httpx.Response(404)

    return httpx.AsyncClient(base_url="https://gamma.test", transport=httpx.MockTransport(handler))


async def ingest_articles(monkeypatch, entries):
    async def fake_fetch(url):
        return entries
    monkeypatch.setattr(scheduler, "fetch_and_parse_feed", fake_fetch)
    async with SessionLocal() as session:
        return await scheduler.ingest_feed(session, {"name": "Reuters", "url": "https://example.test/rss"})


def entry(title, url):
    return {"title": title, "url": url, "content_raw": f"{title}. More details here.", "published_at": datetime.now(timezone.utc)}


async def test_full_market_flow(db, monkeypatch, jev_client):
    # 1. Ingestion with L1 (URL) and L2 (embedding) deduplication
    added = await ingest_articles(monkeypatch, [
        entry("Fed officials signal interest rates cut in December", "https://n.test/fed"),
        entry("Fed officials signal interest rates cut in December", "https://n.test/fed-copy"),  # L2 duplicate
        entry("Fed officials signal interest rates cut in December", "https://n.test/fed/"),      # L1 duplicate
        entry("Local football club wins championship final", "https://n.test/football"),
    ])
    assert added == 3
    async with SessionLocal() as session:
        fed = (await session.execute(select(Article).where(Article.url == "https://n.test/fed"))).scalar_one()
        copy = (await session.execute(select(Article).where(Article.url == "https://n.test/fed-copy"))).scalar_one()
        assert fed.cluster_id is not None and fed.cluster_id == copy.cluster_id

    # 2. AI queue (heuristic evaluator + fallback summarizer, no keys configured)
    await scheduler.process_ai_queue()
    async with SessionLocal() as session:
        processed = (await session.execute(select(ProcessedArticle))).scalars().all()
        assert len(processed) == 3

    # 3. Polymarket sync + linking
    async with gamma_client([gamma_market(1, FED_Q, yes="0.35"), gamma_market(2, OTHER_Q, yes="0.10")]) as client:
        async with SessionLocal() as session:
            stats = await service.sync_markets(session, client=client)
    assert stats["synced"] == 2 and stats["created"] == 2

    async with SessionLocal() as session:
        assert await service.refresh_links(session) >= 1
        assert await service.refresh_links(session) == 0  # idempotent
        links = (await session.execute(select(MarketArticleLink))).scalars().all()
        assert {l.market_id for l in links} == {"1"}
        needing = await service.markets_needing_prediction(session, 10)
        assert [m.id for m in needing] == ["1"]

    # 4. Jev forecast: P(yes)=0.8, evidence 3/4, market 0.35 -> BUY_YES
    captured = jev_client(noul_value=0.8, score_value=3.0, choice_index=0)
    async with SessionLocal() as session:
        market = await session.get(Market, "1")
        pred = await service.predict_market(session, market)
    assert pred.signal == "BUY_YES"
    assert pred.model_probability == 0.8 and pred.evidence_strength == 0.75
    assert pred.blended_probability == pytest.approx(0.375 * 0.8 + 0.625 * 0.35, abs=1e-4)
    assert pred.edge > settings.MIN_EDGE and pred.kelly_fraction > 0
    state = captured[-1]["state"]
    assert state["market"]["question"] == FED_Q
    assert "yes_price" not in str(state) and "0.35" not in str(state)  # price not leaked to the model
    assert "relevant_n0" in captured[-1]["questions"] and "impact_n0" in captured[-1]["questions"]

    async with SessionLocal() as session:
        assert await service.markets_needing_prediction(session, 10) == []
        link = (await session.execute(select(MarketArticleLink))).scalars().first()
        assert link.relevance == 0.8 and link.impact == "raises_yes"

    # 5. API
    from backend.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as api:
        r = await api.get("/markets", params={"only_linked": True})
        assert r.status_code == 200 and r.json()["total"] == 1
        assert r.json()["markets"][0]["latest_prediction"]["signal"] == "BUY_YES"

        r = await api.get("/markets/1")
        assert r.status_code == 200
        assert r.json()["evidence"][0]["impact"] == "raises_yes"
        assert r.json()["url"] == "https://polymarket.com/event/event-1"

        r = await api.get("/predictions/opportunities")
        assert r.status_code == 200 and [o["market"]["id"] for o in r.json()] == ["1"]

        r = await api.post("/markets/1/predict", params={"refresh_price": False})
        assert r.status_code == 200 and r.json()["signal"] == "BUY_YES"
        r = await api.post("/markets/2/predict", params={"refresh_price": False})
        assert r.status_code == 422  # no related news

        r = await api.get("/articles", params={"limit": 2, "offset": 1})
        assert r.status_code == 200 and r.json()["total"] == 3 and len(r.json()["articles"]) == 2
        r = await api.get(f"/articles/{fed.id}")
        assert r.status_code == 200
        assert (await api.get("/articles/not-a-uuid")).status_code == 422
        assert (await api.get(f"/articles/{uuid.uuid4()}")).status_code == 404
        assert (await api.get("/categories")).status_code == 200

    # 6. Market 1 resolves YES: detected on the next sync, used for calibration
    resolved = gamma_market(1, FED_Q, yes="1", closed=True)
    async with gamma_client([gamma_market(2, OTHER_Q, yes="0.10")], single={"1": resolved}) as client:
        async with SessionLocal() as session:
            stats = await service.sync_markets(session, client=client)
    assert stats["resolved"] == 1
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as api:
        cal = (await api.get("/predictions/calibration")).json()
    assert cal["resolved_markets"] == 1
    assert cal["brier_model"] < cal["brier_market"]
