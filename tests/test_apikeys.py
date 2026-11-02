"""API keys and tokens set from the dashboard."""
import pytest

from backend import apikeys
from backend.ai import jev
from backend.alerts import telegram
from backend.config import settings
from backend.db.database import SessionLocal
from tests.conftest import login_client

JEV_KEY = "ts_live_0123456789abcdef"


@pytest.fixture(autouse=True)
def _restore_keys():
    yield
    for key in apikeys.KEYS:
        apikeys._apply(key, None)
    jev.set_client(None)


async def test_keys_set_from_the_dashboard_are_used_and_never_sent_back(db):
    assert not jev.is_enabled() and not telegram.is_configured()
    async with login_client("admin") as api:
        r = await api.get("/settings/api-keys")
        assert r.status_code == 200 and r.json()["TYPESAFE_API_KEY"] == {"set": False, "source": None, "hint": None, "updated_at": None}
        r = await api.put("/settings/api-keys", json={"TYPESAFE_API_KEY": JEV_KEY, "TELEGRAM_BOT_TOKEN": "123456:ABCdefGHIjkl",
                                                      "TELEGRAM_CHAT_ID": "-1001234567890"})
        assert r.status_code == 200, r.text
        assert JEV_KEY not in r.text and "ABCdefGHIjkl" not in r.text
        body = r.json()
        assert body["TYPESAFE_API_KEY"]["set"] and body["TYPESAFE_API_KEY"]["source"] == "app" and body["TYPESAFE_API_KEY"]["hint"] == "…cdef"
        assert body["TELEGRAM_CHAT_ID"]["hint"] == "-1001234567890"     # not a secret
        assert body["GROQ_API_KEY"]["set"] is False                     # not sent: unchanged
    assert settings.TYPESAFE_API_KEY == JEV_KEY and jev.is_enabled() and telegram.is_configured()


async def test_stored_keys_are_applied_at_startup_and_removed_back_to_env(db):
    async with SessionLocal() as s:
        await apikeys.set_values(s, {"GROQ_API_KEY": "gsk_abcdefghijklmnop"})
    apikeys._apply("GROQ_API_KEY", None)            # as after a restart
    assert settings.GROQ_API_KEY is None
    async with SessionLocal() as s:
        await apikeys.load(s)
        assert settings.GROQ_API_KEY == "gsk_abcdefghijklmnop"
        out = await apikeys.set_values(s, {"GROQ_API_KEY": ""})
        assert out["GROQ_API_KEY"]["set"] is False and settings.GROQ_API_KEY is None


async def test_invalid_values_and_viewers_are_refused(db):
    async with login_client("admin") as api:
        assert (await api.put("/settings/api-keys", json={"TYPESAFE_API_KEY": "has space"})).status_code == 422
        assert (await api.put("/settings/api-keys", json={"TELEGRAM_CHAT_ID": "my chat"})).status_code == 422
        assert (await api.put("/settings/api-keys", json={"TELEGRAM_CHAT_ID": "@mychannel"})).status_code == 200
    async with login_client("viewer") as api:
        assert (await api.get("/settings/api-keys")).status_code == 403
        assert (await api.put("/settings/api-keys", json={"GROQ_API_KEY": "x"})).status_code == 403
