"""Usage and cost of the paid AI calls: recording, features, daily limits, warnings, API."""
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from backend.ai import jev, usage
from backend.ai.typesafe_evaluator import evaluate_article_dimensions
from backend.alerts import telegram
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import ApiUsage
from tests.conftest import login_client


async def call_jev():
    from typesafe_sdk import Noul
    return await jev.system_one({"x": 1}, {"q": Noul(instructions="?")})


async def rows():
    async with SessionLocal() as db:
        return (await db.execute(select(ApiUsage).order_by(ApiUsage.id))).scalars().all()


async def test_calls_are_recorded_with_feature_tokens_and_cost(db, jev_client, monkeypatch):
    jev_client()
    monkeypatch.setattr(settings, "JEV_PRICE_PER_CALL", 0.01)
    monkeypatch.setattr(settings, "JEV_PRICE_INPUT_MTOK", 2.0)
    monkeypatch.setattr(settings, "JEV_PRICE_OUTPUT_MTOK", 10.0)
    await call_jev()
    with usage.feature("allerte"):
        await call_jev()
    r = await rows()
    assert [x.feature for x in r] == ["altro", "allerte"]
    assert r[0].provider == "jev" and r[0].input_tokens == 100 and r[0].output_tokens == 5
    assert r[0].cost_usd == pytest.approx(0.01 + 100 * 2 / 1e6 + 5 * 10 / 1e6)
    status = await usage.today_status()
    assert status["jev_calls"] == 2 and status["cost"] == pytest.approx(2 * r[0].cost_usd, abs=1e-4)


async def test_feature_propagates_to_tasks_and_resets():
    import asyncio
    seen = []

    async def inner():
        seen.append(usage.current_feature())

    with usage.feature("backtest"):
        await asyncio.create_task(inner())
    await inner()
    assert seen == ["backtest", "altro"]


async def test_daily_jev_limit_blocks_and_falls_back(db, jev_client, monkeypatch):
    jev_client()
    monkeypatch.setattr(settings, "DAILY_JEV_CALL_LIMIT", 2)
    await call_jev()
    await call_jev()
    with pytest.raises(usage.BudgetExceeded) as e:
        await call_jev()
    assert "2 chiamate" in str(e.value) and e.value.retry_in >= 60
    # Classification keeps working for free
    res = await evaluate_article_dimensions("Fed cuts interest rates", "Reuters", "The Federal Reserve cut rates.")
    assert res["source"] == "heuristic"
    assert len(await rows()) == 2  # blocked calls are not sent nor recorded

    # A new day starts from zero; the counters are reloaded from the database
    usage.reset()
    assert (await usage.today_status())["jev_calls"] == 2


async def test_budget_in_dollars_and_warnings(db, jev_client, monkeypatch):
    jev_client()
    sent = []
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "9")
    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(
        lambda req: (sent.append(json.loads(req.content)["text"]), httpx.Response(200, json={"ok": True}))[1]))
    monkeypatch.setattr(settings, "JEV_PRICE_PER_CALL", 0.3)
    monkeypatch.setattr(settings, "DAILY_AI_BUDGET_USD", 1.0)
    for _ in range(2):
        await call_jev()
    assert sent == []  # 60%
    await call_jev()
    assert len(sent) == 1 and "90%" in sent[0]  # past the 80% warning, once
    await call_jev()  # 0.9 < 1.0: still allowed, then 120%
    assert len(sent) == 2 and "esaurito" in sent[1]
    with pytest.raises(usage.BudgetExceeded):
        await call_jev()
    assert len(sent) == 2
    status = await usage.today_status()
    assert status["blocked"] and status["share_cost"] == pytest.approx(1.2)


async def test_predict_endpoint_reports_the_limit(db, monkeypatch):
    from backend.markets import service

    async def blocked(*a, **k):
        raise usage.BudgetExceeded("jev", 3600, "Limite giornaliero di 5 chiamate a Jev raggiunto: si riparte a mezzanotte.")

    monkeypatch.setattr("backend.api.routes.markets.predict_market", blocked)
    monkeypatch.setattr(jev, "is_enabled", lambda: True)
    from backend.db.models import Market
    async with SessionLocal() as session:
        session.add(Market(id="m1", question="Will X happen?", yes_price=0.4))
        await session.commit()
    async with login_client("admin") as api:
        r = await api.post("/markets/m1/predict", params={"refresh_price": False})
        assert r.status_code == 429 and "5 chiamate" in r.json()["detail"]


async def test_usage_api(db, jev_client, monkeypatch):
    jev_client()
    with usage.feature("previsioni"):
        await call_jev()
    async with login_client("viewer") as api:
        data = (await api.get("/usage")).json()
        assert data["today"]["jev_calls"] == 1
        item = data["report"]["items"][0]
        assert item["provider"] == "jev" and item["feature"] == "previsioni" and item["calls"] == 1
        assert data["settings"]["DAILY_JEV_CALL_LIMIT"]["value"] == 0
        assert (await api.put("/usage/settings", json={"DAILY_JEV_CALL_LIMIT": 5})).status_code == 403
        assert (await api.get("/status")).json()["usage"]["jev_calls"] == 1
    async with login_client("admin") as api:
        r = await api.put("/usage/settings", json={"DAILY_JEV_CALL_LIMIT": 5, "DAILY_AI_BUDGET_USD": 2.5, "JEV_PRICE_PER_CALL": 0.02})
        assert r.status_code == 200
        assert r.json()["today"]["jev_call_limit"] == 5 and settings.DAILY_AI_BUDGET_USD == 2.5
        assert isinstance(settings.DAILY_JEV_CALL_LIMIT, int)
        assert (await api.put("/usage/settings", json={"DAILY_JEV_CALL_LIMIT": -1})).status_code == 422
        # The backtest reset does not touch the limits
        await api.post("/backtest/parameters/reset")
        assert settings.DAILY_JEV_CALL_LIMIT == 5
        r = await api.post("/usage/settings/reset")
        assert r.json()["settings"]["DAILY_JEV_CALL_LIMIT"]["value"] == 0 and settings.DAILY_JEV_CALL_LIMIT == 0
