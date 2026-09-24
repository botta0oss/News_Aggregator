"""Multi-outcome events: parsing, separation from YES/NO markets, distribution forecast, API."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Market, MultiEvent, MultiPrediction
from backend.markets import polymarket, service as market_service
from backend.multi import service
from tests.conftest import login_client
from tests.test_markets_e2e import entry, ingest_articles
from tests.test_polymarket import gamma_market

TITLE = "Who will win the Champions League final?"
TEAMS = [("Real Madrid", "0.50"), ("Arsenal", "0.25"), ("Inter", "0.15"), ("Bayern", "0.08"), ("Chelsea", "0.02")]


def member(i, team, price, event_id="ev1", closed=False):
    m = gamma_market(100 + i, f"Will {team} win the Champions League?", yes=price, closed=closed)
    m.update({"negRisk": True, "groupItemTitle": team, "events": [{"id": event_id, "slug": "ucl-winner"}]})
    return m


def gamma_event(event_id="ev1", teams=TEAMS, neg_risk=True, closed=False):
    return {"id": event_id, "title": TITLE, "slug": "ucl-winner", "description": "Resolves to the winner of the final.",
            "endDate": "2026-12-31T12:00:00Z", "volume": 2_000_000, "liquidity": 300_000, "negRisk": neg_risk,
            "closed": closed, "markets": [member(i, t, p, event_id, closed) for i, (t, p) in enumerate(teams)]}


def gamma(events, markets=()):
    def handler(request: httpx.Request):
        path = request.url.path
        if path == "/events":
            return httpx.Response(200, json=events if int(request.url.params.get("offset", 0)) == 0 else [])
        if path.startswith("/events/"):
            ev = next((e for e in events if e["id"] == path.rsplit("/", 1)[-1]), None)
            return httpx.Response(200, json=ev) if ev else httpx.Response(404)
        if path == "/markets":
            return httpx.Response(200, json=list(markets) if int(request.url.params.get("offset", 0)) == 0 else [])
        return httpx.Response(404)
    return httpx.AsyncClient(base_url="https://gamma.test", transport=httpx.MockTransport(handler))


def test_parse_event():
    ev = polymarket.parse_event(gamma_event())
    assert ev.id == "ev1" and [o.group_title for o in ev.outcomes][:2] == ["Real Madrid", "Arsenal"]
    assert ev.url == "https://polymarket.com/event/ucl-winner"
    assert polymarket.parse_event(gamma_event(neg_risk=False)) is None
    assert polymarket.parse_event(gamma_event(teams=TEAMS[:2])) is None  # two outcomes = a YES/NO question
    assert polymarket.is_multi_outcome(polymarket.parse_market(member(0, "Real Madrid", "0.5")))


def test_distribution_blend_and_signal(monkeypatch):
    outcomes = [type("O", (), {"id": str(i), "label": t, "yes_price": float(p), "closed": False}) for i, (t, p) in enumerate(TEAMS)]
    items = service.market_distribution(outcomes, max_outcomes=3)
    assert [i["label"] for i in items] == ["Real Madrid", "Arsenal", "Inter", f"{service.OTHER_LABEL} (2)"]
    assert sum(i["market"] for i in items) == pytest.approx(1.0, abs=1e-3)
    assert items[-1]["price"] == pytest.approx(0.10)
    for n, i in enumerate(items):
        i["key"] = f"o{n}"
    monkeypatch.setattr(settings, "MODEL_WEIGHT_MAX", 0.5)
    out, w = service.blend_distribution(items, {"o0": 0.2, "o1": 0.6, "o2": 0.1, "o3": 0.1}, evidence_strength=1.0)
    assert w == 0.5
    arsenal = next(o for o in out if o["label"] == "Arsenal")
    assert arsenal["blended"] == pytest.approx(0.5 * 0.6 + 0.5 * 0.25, abs=1e-3)
    assert sum(o["blended"] for o in out) == pytest.approx(1.0, abs=1e-3)
    signal, best, edge = service.pick_signal(out, evidence_strength=1.0)
    assert signal == "BUY_YES" and best == "1" and edge == pytest.approx(arsenal["edge"])
    assert service.pick_signal(out, evidence_strength=0.1)[0] == "HOLD"


async def sync_all():
    events = [gamma_event()]
    binary = [gamma_market(1, "Will the Fed cut interest rates in December 2026?"), member(0, "Real Madrid", "0.50")]
    async with gamma(events, binary) as client:
        async with SessionLocal() as session:
            await market_service.sync_markets(session, client=client)
            return await service.sync_events(session, client=client)


async def test_events_are_kept_apart_and_forecast(db, monkeypatch, jev_client):
    await ingest_articles(monkeypatch, [entry("Arsenal beat Real Madrid in Champions League semi-final", "https://n.test/ucl")])
    stats = await sync_all()
    assert stats["created"] == 1
    async with SessionLocal() as session:
        assert (await session.get(MultiEvent, "ev1")).title == TITLE
        assert await session.get(Market, "100") is None  # the outcome was not stored as a YES/NO market
        assert await service.refresh_links(session) == 1

    captured = jev_client(noul_value=0.9, score_value=3.0, choice_index=1)  # Jev favours Arsenal
    async with login_client("viewer") as api:
        listing = (await api.get("/multi")).json()
        assert listing["total"] == 1 and listing["events"][0]["outcome_count"] == 5
        assert (await api.get("/markets")).json()["total"] == 1  # YES/NO list untouched
        assert (await api.post("/multi/ev1/predict")).status_code == 403

    async with login_client("admin") as api:
        r = await api.post("/multi/ev1/predict")
        assert r.status_code == 200, r.text
        pred = r.json()
        arsenal = next(o for o in pred["outcomes"] if o["label"] == "Arsenal")
        assert arsenal["model"] > arsenal["market"] and pred["best_outcome_id"] == arsenal["id"]
        assert pred["signal"] == "BUY_YES"
        request = captured[-1]
        assert "Arsenal" in request["questions"]["winner"]["criteria"].values()
        assert [o["name"] for o in request["state"]["market"]["outcomes"]][:2] == ["Real Madrid", "Arsenal"]
        assert "0.25" not in json.dumps(request["state"])  # prices not leaked

        detail = (await api.get("/multi/ev1")).json()
        assert detail["latest_prediction"]["signal"] == "BUY_YES" and len(detail["outcomes"]) == 5
        assert detail["evidence"][0]["relevance"] == 0.9
        assert "arsenal" in [t.lower() for t in detail["evidence"][0]["matched_terms"]]
        assert (await api.get("/multi", params={"q": "arsenal"})).json()["total"] == 1
        assert (await api.get("/multi/nope")).status_code == 404


async def test_event_without_news_is_not_forecast(db, jev_client):
    jev_client()
    await sync_all()
    async with login_client("admin") as api:
        assert (await api.post("/multi/ev1/predict")).status_code == 422


async def test_resolution_is_detected(db):
    await sync_all()
    teams = [(t, "1" if t == "Arsenal" else "0") for t, _ in TEAMS]
    closed = gamma_event(teams=teams, closed=True)

    def handler(request: httpx.Request):
        if request.url.path == "/events":
            return httpx.Response(200, json=[])
        if request.url.path == "/events/ev1":
            return httpx.Response(200, json=closed)
        return httpx.Response(200, json=[])
    async with httpx.AsyncClient(base_url="https://gamma.test", transport=httpx.MockTransport(handler)) as client:
        async with SessionLocal() as session:
            stats = await service.sync_events(session, client=client)
            event = await session.get(MultiEvent, "ev1")
    assert stats["resolved"] == 1 and event.closed and event.winner_id == "101"
