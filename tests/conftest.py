import hashlib
import json
import math
import os
import re
import sys

import pytest

# Tests use a dedicated database: set before any backend import (the engine is created at import)
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5432/newsagg"
)
# Never hit real providers from tests
for key in ("TYPESAFE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
    os.environ[key] = ""
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"

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
