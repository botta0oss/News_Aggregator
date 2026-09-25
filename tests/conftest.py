import hashlib
import json
import math
import os
import re
import sys

import contextlib

import pytest

# Tests use a dedicated database: set before any backend import (the engine is created at import)
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5432/newsagg"
)
# Never hit real providers from tests
for key in ("TYPESAFE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
    os.environ[key] = ""
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
# The existing tests check the Italian texts; tests/test_i18n.py covers English
os.environ["APP_LANGUAGE"] = "it"
# The existing tests were written for the earlier forecast parameters; tests/test_trust.py covers
# the lower Jev weight, the disagreement reduction and the exclusion of price markets
os.environ["MODEL_WEIGHT_MAX"] = "0.5"
os.environ["MODEL_DISAGREEMENT_LOGIT"] = "0"
os.environ["EXCLUDE_PRICE_MARKETS"] = "false"
# ... and without the objective evidence strength and the long-shot rule (tests/test_evidence.py)
os.environ["EVIDENCE_OBJECTIVE_HALF"] = "0"
os.environ["LONGSHOT_MIN_PRICE"] = "0"
# Fast client-side rate limits in tests (test_ratelimit.py sets its own)
os.environ["JEV_RPM"] = "6000"
os.environ["GROQ_RPM"] = "6000"
os.environ["GEMINI_RPM"] = "6000"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

EMBEDDING_DIM = 384


def fake_embedding(text: str) -> list[float]:
    """Deterministic bag-of-words embedding: texts sharing words get high cosine similarity."""
    vec = [0.0] * EMBEDDING_DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 3:
            continue
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def jev_mock_transport(noul_value: float = 0.8, score_value: float = 3.0, choice_index: int = 0, captured=None):
    """httpx2 MockTransport emulating POST /v1/systemone for any set of questions."""
    import httpx2

    def handler(request: "httpx2.Request") -> "httpx2.Response":
        body = json.loads(request.content)
        if captured is not None:
            captured.append(body)
        answers = {}
        for name, q in body["questions"].items():
            if q["type"] == "noul":
                answers[name] = {"type": "noul", "noul": noul_value}
            elif q["type"] == "score":
                levels = len(q["criteria"])
                value = min(score_value, levels - 1)
                answers[name] = {
                    "type": "score", "score": value, "confidence": 0.8,
                    "legend": {str(i): c for i, c in enumerate(q["criteria"])},
                    "probabilities": {str(i): (1.0 if i == round(value) else 0.0) for i in range(levels)},
                }
            elif q["type"] == "choice":
                labels = list(q["criteria"])
                chosen = labels[min(choice_index, len(labels) - 1)]
                answers[name] = {
                    "type": "choice", "choice": chosen, "confidence": 0.7,
                    "probabilities": {l: (0.7 if l == chosen else 0.3 / (len(labels) - 1)) for l in labels},
                }
        return httpx2.Response(200, json={
            "model": "jev-test", "usage": {"input_tokens": 100, "output_tokens": 5}, "answers": answers,
        })

    return httpx2.MockTransport(handler)


@pytest.fixture
def jev_client():
    """Installs a mocked AsyncTypeSafeClient as the shared Jev client."""
    from typesafe_sdk import AsyncTypeSafeClient
    from backend.ai import jev

    captured: list = []

    def install(**kwargs):
        client = AsyncTypeSafeClient(api_key="test-key", transport=jev_mock_transport(captured=captured, **kwargs))
        jev.set_client(client)
        return captured

    yield install
    jev.set_client(None)


@pytest.fixture
async def db(monkeypatch):
    """Fresh schema on the test database, fake embeddings, clean login limiter."""
    from sqlalchemy import text
    from backend.config import settings
    from backend.db.database import engine
    from backend.db.models import Base
    from backend.db.migrations import run_migrations
    from backend.ingestor import scheduler
    from backend.markets import service
    from backend.auth.deps import login_limiter

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            await run_migrations(conn)
    except Exception as e:  # pragma: no cover
        if os.environ.get("CI"):
            raise  # in CI a missing database is a failure, not a reason to skip half the suite
        pytest.skip(f"Test database not available: {e}")
    monkeypatch.setattr(service, "get_title_embedding", fake_embedding)
    monkeypatch.setattr(scheduler, "get_title_embedding", fake_embedding)
    from backend.multi import service as multi_service
    monkeypatch.setattr(multi_service, "get_title_embedding", fake_embedding)
    monkeypatch.setattr(settings, "MARKET_MATCH_THRESHOLD", 0.5)
    monkeypatch.setattr(settings, "TARGETED_NEWS_ENABLED", False)  # no network in tests; enabled where tested
    login_limiter.clear()
    yield
    await engine.dispose()


TEST_PASSWORD = "correct-horse-battery"


def api_client():
    """ASGI test client on a localhost URL (so the auto Secure cookie flag stays off)."""
    import httpx
    from backend.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def ensure_user(username: str, role: str, password: str = TEST_PASSWORD):
    from backend.auth import service
    from backend.db.database import SessionLocal
    async with SessionLocal() as session:
        if await service.get_user(session, username) is None:
            await service.create_user(session, username, password, role=role)


@contextlib.asynccontextmanager
async def login_client(role: str):
    """Signed-in client (user named after the role) sending the CSRF header on every request."""
    await ensure_user(role, role)
    async with api_client() as client:
        r = await client.post("/auth/login", json={"username": role, "password": TEST_PASSWORD})
        assert r.status_code == 200, r.text
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield client


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Limiters keep state (cooldowns) across calls: start every test clean."""
    from backend.ai import ratelimit, usage
    ratelimit.reset_limiters()
    usage.reset()
    yield
    ratelimit.reset_limiters()
    usage.reset()


@pytest.fixture(autouse=True)
def _no_fee_lookup(monkeypatch):
    """No CLOB calls from tests: markets are fee-free unless a test says otherwise."""
    from backend.betting import fees

    async def fee_free(token_id, client=None):
        return False
    monkeypatch.setattr(fees, "fees_enabled", fee_free)
    yield
