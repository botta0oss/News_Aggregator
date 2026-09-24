import asyncio
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.ai import jev, ratelimit, summarizer
from backend.ai.ratelimit import ProviderLimiter, RateLimited, get_limiter, retry_after_seconds
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Article, ProcessedArticle, Source
from backend.ingestor import scheduler
from tests.conftest import api_client, login_client


@pytest.fixture(autouse=True)
def fresh_limiters():
    ratelimit.reset_limiters()
    yield
    ratelimit.reset_limiters()


async def test_spacing_max_wait_and_cooldown():
    lim = ProviderLimiter("t", per_minute=600, concurrency=5)  # one every 0.1 s
    t0 = time.monotonic()
    for _ in range(3):
        async with lim.slot(max_wait=5):
            pass
    assert time.monotonic() - t0 >= 0.19
    lim.cooldown(30)
    with pytest.raises(RateLimited) as err:
        async with lim.slot(max_wait=1):
            pass
    assert err.value.retry_in > 25 and "riprova" in str(err.value)


async def test_concurrency_cap():
    lim = ProviderLimiter("t", per_minute=0, concurrency=1)
    running, peak = 0, 0

    async def work():
        nonlocal running, peak
        async with lim.slot(max_wait=5):
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
    await asyncio.gather(*(work() for _ in range(4)))
    assert peak == 1


def test_retry_after_parsing():
    err = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "12"}))
    assert retry_after_seconds(err) == 12
    assert retry_after_seconds(SimpleNamespace(headers={"retry-after-ms": "1500"})) == 1.5
    assert retry_after_seconds(Exception("x"), default=7) == 7


class FakeRateLimit(Exception):
    status_code = 429

    def __init__(self):
        super().__init__("Error code: 429 - rate_limit_exceeded")
        self.response = SimpleNamespace(headers={"retry-after": "12"})


def fake_groq(behaviour):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return await behaviour(kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


async def test_groq_rate_limit_pauses_provider(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "k")
    monkeypatch.setattr(settings, "AI_MAX_WAIT_SECONDS", 1)

    async def boom(kwargs):
        raise FakeRateLimit()
    client, calls = fake_groq(boom)
    monkeypatch.setattr(summarizer, "_groq_client", client)
    assert await summarizer.summarize_with_groq("t", "c") is None
    assert get_limiter("groq").cooling_down_for() > 10
    # While paused, no further calls reach the API
    assert await summarizer.summarize_with_groq("t", "c") is None
    assert len(calls) == 1


async def test_groq_reasoning_model_params_and_empty_answer(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "k")
    monkeypatch.setattr(settings, "GROQ_MODEL", "openai/gpt-oss-20b")
    answers = iter([None, "  Riassunto.  "])

    async def reply(kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(answers)))])
    client, calls = fake_groq(reply)
    monkeypatch.setattr(summarizer, "_groq_client", client)
    monkeypatch.setattr(ratelimit.settings, "GROQ_RPM", 0)
    assert await summarizer.summarize_with_groq("t", "c") is None  # empty content handled
    assert await summarizer.summarize_with_groq("t", "c") == "Riassunto."
    assert calls[0]["reasoning_effort"] == "low" and calls[0]["max_completion_tokens"] > 0
    assert calls[0]["model"] == "openai/gpt-oss-20b"


async def test_ai_queue_stops_on_jev_limit_and_resumes(db, monkeypatch, jev_client):
    jev_client()
    async with SessionLocal() as s:
        src = Source(name="S", url="https://s.test/rss")
        s.add(src)
        await s.flush()
        for i in range(3):
            s.add(Article(source_id=src.id, title=f"News {i}", url=f"https://n.test/{i}", url_hash=f"h{i}"))
        await s.commit()
    get_limiter("jev").cooldown(60)
    monkeypatch.setattr(settings, "AI_MAX_WAIT_SECONDS", 1)
    await scheduler.process_ai_queue()
    async with SessionLocal() as s:
        assert (await s.execute(select(func.count(ProcessedArticle.id)))).scalar() == 0  # nothing downgraded
    ratelimit.reset_limiters()
    monkeypatch.setattr(settings, "AI_MAX_WAIT_SECONDS", 90)
    monkeypatch.setattr(settings, "JEV_RPM", 600)  # keep the test fast: one call every 0.1 s
    await scheduler.process_ai_queue()
    async with SessionLocal() as s:
        rows = (await s.execute(select(ProcessedArticle.classifier))).scalars().all()
        assert rows == ["jev"] * 3


async def test_manual_predict_returns_429_when_jev_paused(db, jev_client):
    from tests.test_portfolio import make_market
    jev_client()
    async with SessionLocal() as s:
        await make_market(s)
    get_limiter("jev").cooldown(120)
    async with login_client("admin") as api:
        r = await api.post("/markets/m-fed/predict", params={"refresh_price": False})
        assert r.status_code == 429 and "riprova" in r.json()["detail"]
        assert int(r.headers["retry-after"]) > 100


async def test_dashboard_files_are_revalidated(db):
    async with api_client() as client:
        for path in ("/", "/app.js", "/styles.css", "/boot-check.js"):
            r = await client.get(path)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
        assert (await client.get("/auth/me")).headers["cache-control"] == "no-store"
