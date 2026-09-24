"""Backtest: planning, historical news without look-ahead, metrics, suggestions, API."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from backend.backtest import analysis, engine
from backend.config import settings
from backend.ingestor.fetcher import parse_feed
from backend.markets.polymarket import PolymarketMarket, price_at
from tests.conftest import fake_embedding, login_client

NOW = datetime.now(timezone.utc)
FED_Q = "Will the Fed cut interest rates in March 2026?"


def market(id_, question, resolved_yes, end_days_ago=20, volume=500_000):
    end = NOW - timedelta(days=end_days_ago)
    return PolymarketMarket(
        id=str(id_), question=question, slug=f"m-{id_}", event_slug=f"e-{id_}", description="Resolves YES if ...",
        end_date=end, yes_price=1.0 if resolved_yes else 0.0, volume=volume, liquidity=50_000, active=False,
        closed=True, resolved_yes=resolved_yes, yes_token_id=f"tok{id_}", no_token_id=f"tokno{id_}",
        start_date=end - timedelta(days=90), closed_time=None,
    )


# ---------- Pure parts ----------

def test_price_at_uses_the_last_point_before():
    h = [(NOW - timedelta(hours=10), 0.3), (NOW - timedelta(hours=2), 0.4), (NOW + timedelta(hours=1), 0.9)]
    assert price_at(h, NOW) == 0.4
    assert price_at(h, NOW - timedelta(hours=20)) is None
    assert price_at(h, NOW + timedelta(days=5)) is None  # last point too old


def test_plan_skips_horizons_before_the_market_existed():
    m = market(1, FED_Q, True)
    m.start_date = m.end_date - timedelta(days=10)
    plan = engine.plan_cases([m], [1, 7, 30])
    assert [h for _m, h, _a in plan] == [1, 7]
    assert plan[0][2] == m.end_date - timedelta(days=1)


def test_search_url_has_date_filters():
    url = engine.historical_search_url(FED_Q, datetime(2026, 3, 10, 12, tzinfo=timezone.utc))
    assert "after%3A2026-03-03" in url and "before%3A2026-03-11" in url


def case(price, model, evidence, yes, horizon=7, category="Economy"):
    return {"status": "ok", "price": price, "model_probability": model, "evidence_strength": evidence,
            "blended_probability": analysis.blend({"price": price, "model_probability": model, "evidence_strength": evidence}, 0.5),
            "resolved_yes": yes, "horizon_days": horizon, "category": category, "signal": "HOLD",
            "verdict": "NO", "outlay": None, "pnl": None, "side": None, "note": None}


def test_suggestion_trusts_an_informative_model_and_ignores_noise():
    rng = random.Random(3)
    informative, noise = [], []
    for _ in range(300):
        truth = rng.random()
        yes = rng.random() < truth
        price = min(0.95, max(0.05, 0.5 + (truth - 0.5) * 0.3))      # market only half-informed
        informative.append(case(price, truth, 0.8, yes))
        noise.append(case(price, rng.random(), 0.8, yes))
    good = analysis.suggest(informative)
    bad = analysis.suggest(noise)
    assert good["model_weight_max"]["suggested"] >= 0.6
    assert good["model_weight_max"]["brier_suggested"] <= good["model_weight_max"]["brier_current"]
    assert bad["model_weight_max"]["suggested"] <= 0.2
    assert good["confidence"] == "alta"
    assert good["min_edge"]["suggested"] is not None


def test_summary_metrics_and_calibration():
    cases = [case(0.3, 0.8, 0.8, True), case(0.6, 0.2, 0.8, False, horizon=1), {**case(0.5, 0.5, 0.5, True), "status": "skipped", "note": "Nessuna notizia"}]
    cases[0].update(signal="BUY_YES", verdict="GO", outlay=10.0, pnl=20.0, side="YES")
    s = analysis.summarize(cases)
    o = s["overall"]
    assert o["n"] == 2 and o["bets"] == 1 and o["pnl"] == 20.0 and o["roi"] == 2.0
    assert o["brier_model"] < o["brier_market"]
    assert [h["horizon_days"] for h in s["by_horizon"]] == [1, 7]
    assert s["skipped"] == {"Nessuna notizia": 1}
    assert sum(b["n"] for b in s["calibration"]["model"]) == 2


# ---------- Full run with mocked services ----------

def rss(items):
    body = "".join(f"<item><title>{t} - {src}</title><link>https://news.google.com/rss/articles/{i}</link>"
                   f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate><source url='https://x'>{src}</source></item>"
                   for i, (t, src, d) in enumerate(items))
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>{body}</channel></rss>".encode()


@pytest.fixture
def services(monkeypatch):
    markets = [market(1, FED_Q, True), market(2, "Will Bitcoin reach 200k dollars in March 2026?", False)]
    urls = []

    async def resolved(after, before, limit, min_volume, client=None):
        return markets[:limit]

    async def history(token, start, end, client=None):
        prices = {"tok1": 0.30, "tok2": 0.40}
        points, t = [], start
        while t <= end:
            points.append((t, prices[token]))
            t += timedelta(hours=6)
        return points

    async def feed(url):
        urls.append(url)
        as_of = markets[0].end_date - timedelta(days=7)
        if "fed" in url.lower():
            return parse_feed(rss([
                ("Fed officials signal interest rates cut in March", "Reuters", as_of - timedelta(days=1)),
                ("Fed cuts interest rates in March", "AP News", as_of + timedelta(hours=3)),  # after as_of: must be ignored
            ]))
        return parse_feed(rss([]))

    monkeypatch.setattr(engine.polymarket, "fetch_resolved_markets", resolved)
    monkeypatch.setattr(engine.polymarket, "fetch_price_history", history)
    monkeypatch.setattr(engine, "fetch_feed", feed)
    monkeypatch.setattr(engine, "get_title_embedding", fake_embedding)
    return urls


async def test_backtest_run_end_to_end(db, jev_client, services):
    captured = jev_client(noul_value=0.8, score_value=3.0)
    async with login_client("viewer") as api:
        assert (await api.post("/backtest/runs", json={"resolved_after": "2026-01-01", "resolved_before": "2026-02-01"})).status_code == 403

    async with login_client("admin") as api:
        body = {"resolved_after": (NOW - timedelta(days=60)).date().isoformat(),
                "resolved_before": (NOW - timedelta(days=1)).date().isoformat(), "horizons": [7], "max_calls": 10}
        r = await api.post("/backtest/runs", json=body)
        assert r.status_code == 202, r.text
        run_id = r.json()["id"]
        assert (await api.post("/backtest/runs", json=body)).status_code == 409
        await engine.wait()

        run = (await api.get(f"/backtest/runs/{run_id}")).json()
        assert run["status"] == "done" and run["total"] == 2 and run["done"] == 1 and run["skipped"] == 1
        overall = run["summary"]["overall"]
        assert overall["n"] == 1 and overall["brier_model"] < overall["brier_market"]
        assert run["summary"]["skipped"] == {"Nessuna notizia di quei giorni": 1}

        cases = (await api.get(f"/backtest/runs/{run_id}/cases")).json()
        c = cases[0]
        assert c["price"] == 0.3 and c["model_probability"] == 0.8 and c["signal"] == "BUY_YES"
        assert [n["title"] for n in c["news"]] == ["Fed officials signal interest rates cut in March"]  # no look-ahead
        assert c["news"][0]["source"] == "Reuters"
        # Jev saw the date of that moment, not today
        assert captured[-1]["state"]["today"] == c["as_of"][:10]
        assert "0.3" not in str(captured[-1]["state"])  # price not leaked

        assert len((await api.get("/backtest/runs")).json()) == 1
        assert (await api.delete(f"/backtest/runs/{run_id}")).status_code == 204
        assert (await api.get(f"/backtest/runs/{run_id}")).status_code == 404


async def test_backtest_validation_and_jev_required(db, services):
    async with login_client("admin") as api:
        r = await api.post("/backtest/runs", json={"resolved_after": "2026-01-01", "resolved_before": "2026-02-01"})
        assert r.status_code == 503  # Jev not configured


async def test_parameters_apply_and_reset(db, monkeypatch):
    from backend import overrides
    monkeypatch.setitem(overrides.DEFAULTS, "MODEL_WEIGHT_MAX", 0.5)
    monkeypatch.setattr(settings, "MODEL_WEIGHT_MAX", 0.5)
    async with login_client("viewer") as api:
        assert (await api.put("/backtest/parameters", json={"MODEL_WEIGHT_MAX": 0.3})).status_code == 403
    async with login_client("admin") as api:
        r = await api.put("/backtest/parameters", json={"MODEL_WEIGHT_MAX": 0.3, "note": "backtest"})
        assert r.status_code == 200 and r.json()["parameters"]["MODEL_WEIGHT_MAX"] == {"value": 0.3, "default": 0.5, "overridden": True}
        assert settings.MODEL_WEIGHT_MAX == 0.3
        assert (await api.put("/backtest/parameters", json={"MIN_EDGE": 2})).status_code == 422
        # Applied again at startup
        settings.MODEL_WEIGHT_MAX = 0.5
        from backend.db.database import SessionLocal
        async with SessionLocal() as session:
            await overrides.load(session)
        assert settings.MODEL_WEIGHT_MAX == 0.3
        r = await api.post("/backtest/parameters/reset")
        assert r.json()["parameters"]["MODEL_WEIGHT_MAX"]["overridden"] is False and settings.MODEL_WEIGHT_MAX == 0.5


# ---------- Multi-outcome events ----------

def resolved_event():
    from backend.markets.polymarket import parse_event
    from tests.test_multi import TEAMS, gamma_event
    teams = [(t, "1" if t == "Arsenal" else "0") for t, _ in TEAMS]
    raw = gamma_event(teams=teams, closed=True)
    raw["endDate"] = (NOW - timedelta(days=20)).isoformat()
    for i, m in enumerate(raw["markets"]):
        m["clobTokenIds"] = f'["t{i}", "n{i}"]'
        m["volumeNum"] = 100_000 - i
    return parse_event(raw)


async def test_multi_outcome_backtest(db, jev_client, monkeypatch):
    event = resolved_event()
    assert event.winner_id == "101"
    prices = {"t0": 0.45, "t1": 0.20, "t2": 0.15, "t3": 0.12, "t4": 0.08}  # Arsenal underpriced at the time

    async def events(after, before, limit, min_volume, client=None):
        return [event]

    async def history(token, start, end, client=None):
        points, t = [], start
        while t <= end:
            points.append((t, prices[token]))
            t += timedelta(hours=6)
        return points

    async def feed(url):
        as_of = event.end_date - timedelta(days=7)
        return parse_feed(rss([("Arsenal favourites after beating Real Madrid in Champions League", "BBC", as_of - timedelta(days=1))]))

    monkeypatch.setattr(engine.polymarket, "fetch_resolved_events", events)
    monkeypatch.setattr(engine.polymarket, "fetch_price_history", history)
    monkeypatch.setattr(engine, "fetch_feed", feed)
    monkeypatch.setattr(engine, "get_title_embedding", fake_embedding)
    jev_client(noul_value=0.9, score_value=3.0, choice_index=1)  # Jev favours Arsenal (2nd by price)

    async with login_client("admin") as api:
        r = await api.post("/backtest/runs", json={
            "resolved_after": (NOW - timedelta(days=60)).date().isoformat(),
            "resolved_before": (NOW - timedelta(days=1)).date().isoformat(), "horizons": [7], "kinds": ["multi"]})
        assert r.status_code == 202, r.text
        await engine.wait()
        run = (await api.get(f"/backtest/runs/{r.json()['id']}")).json()
        assert run["status"] == "done" and run["done"] == 1
        m = run["summary"]["multi"]
        assert m["n"] == 1 and m["brier_model"] < m["brier_market"]
        assert m["favourite_right_model"] == 1.0 and m["favourite_right_market"] == 0.0
        assert run["summary"]["overall"]["n"] == 0  # no YES/NO case mixed in
        case = (await api.get(f"/backtest/runs/{r.json()['id']}/cases")).json()[0]
        assert case["kind"] == "multi" and case["details"]["winner"] == "Arsenal" and case["details"]["best"] == "Arsenal"
        assert case["signal"] == "BUY_YES"
        assert (await api.post("/backtest/runs", json={"resolved_after": "2026-01-01", "resolved_before": "2026-02-01",
                                                        "kinds": []})).status_code == 422
