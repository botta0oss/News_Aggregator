"""Texts in the language the dashboard asks for (X-Lang), APP_LANGUAGE otherwise."""
import re
from pathlib import Path

from backend.config import settings
from backend.i18n import dec, dollars, lang, reset_lang, set_lang, tr
from tests.conftest import login_client
from tests.test_strategy import fc, plan

ROOT = Path(__file__).resolve().parent.parent


def in_lang(value, fn):
    token = set_lang(value)
    try:
        return fn()
    finally:
        reset_lang(token)


def test_language_from_request_then_setting(monkeypatch):
    assert lang() == "it"                      # tests run with APP_LANGUAGE=it
    assert in_lang("en", lang) == "en" and in_lang("EN-gb", lang) == "en"
    assert in_lang("fr", lang) == "it"          # unknown: the setting
    monkeypatch.setattr(settings, "APP_LANGUAGE", "en")
    assert lang() == "en" and in_lang("it", lang) == "it"
    assert in_lang("en", lambda: (tr("Sì", "Yes"), dec(1.25, 1), dollars(41.02))) == ("Yes", "1.2", "$41.02")
    assert in_lang("it", lambda: (tr("Sì", "Yes"), dec(1.5, 1), dollars(41.02))) == ("Sì", "1,5", "41,02 $")


def test_plan_in_english():
    p = in_lang("en", lambda: plan(fc(), 0.35, "BUY_YES", tally={"raises_yes": 1, "lowers_yes": 0, "neutral": 0}))
    assert p.title == "Buy YES" and p.summary.startswith("Limit order:") and "$" in p.summary
    assert any("news item points this way" in r for r in p.pros)
    assert any("chance of losing" in r for r in p.cons)
    assert not re.search(r"\d,\d", p.summary)   # decimal point, not comma
    text = " ".join([p.title, p.summary, *p.pros, *p.cons, *p.exit, *p.confidence_why])
    assert not re.search(r"\b(probabilità|prezzo|notizie|quote)\b", text)


async def test_api_errors_follow_x_lang(db):
    async with login_client("admin") as client:
        r = await client.post("/markets/does-not-exist/predict", headers={"X-Lang": "en"})
        assert r.status_code == 404 and r.json()["detail"] == "Market not found"
        r = await client.post("/markets/does-not-exist/predict", headers={"X-Lang": "it"})
        assert r.json()["detail"] == "Mercato non trovato"
        r = await client.get("/portfolio", headers={"X-Lang": "en"})
        assert r.status_code == 200 and {p["label"] for p in r.json()["presets"]} == {"Prudent", "Balanced", "Aggressive"}


def test_every_ui_text_has_an_english_translation():
    """Every literal passed to t() in the dashboard is in i18n-en.js, with the same placeholders."""
    frontend = ROOT / "frontend"
    dictionary = (frontend / "i18n-en.js").read_text(encoding="utf-8")
    entries = dict(re.findall(r'^  ("(?:[^"\\]|\\.)*"): ("(?:[^"\\]|\\.)*"),$', dictionary, re.M))
    import json
    en = {json.loads(k): json.loads(v) for k, v in entries.items()}
    missing = set()
    for path in [*frontend.glob("*.js"), *frontend.glob("views/*.js")]:
        if path.name.startswith("i18n"):
            continue
        for m in re.finditer(r'\bt\("((?:[^"\\]|\\.)*)"', path.read_text(encoding="utf-8")):
            key = json.loads(f'"{m.group(1)}"')
            if key not in en:
                missing.add(key)
            elif sorted(re.findall(r"\{\d+\}", key)) != sorted(re.findall(r"\{\d+\}", en[key])):
                missing.add(f"placeholders: {key}")
    assert not missing, sorted(missing)[:20]
