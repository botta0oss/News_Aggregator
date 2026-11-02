"""API keys and tokens set from the dashboard (page Impostazioni).

Stored in the database (table app_secrets) and applied over the .env values at startup and when
changed, like the parameters of overrides.py. A key removed from the dashboard falls back to the
one in .env, if any. Keys are write-only: the dashboard only ever sees whether one is set, where it
comes from and its last 4 characters.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.models import AppSecret
from backend.i18n import tr

# key: (secret: masked in the dashboard, max length)
KEYS = {
    "TYPESAFE_API_KEY": (True, 300),
    "GEMINI_API_KEY": (True, 300),
    "GROQ_API_KEY": (True, 300),
    "TELEGRAM_BOT_TOKEN": (True, 200),
    "TELEGRAM_CHAT_ID": (False, 100),   # not secret: shown in full
}
# Values from .env, captured before any dashboard value is applied
DEFAULTS = {key: getattr(settings, key) or None for key in KEYS}


def _apply(key: str, value: Optional[str]) -> None:
    setattr(settings, key, value if value else (DEFAULTS[key] or ("" if key.startswith("TELEGRAM") else None)))


async def _reset_clients(keys) -> None:
    """Clients made with the old key are dropped: the next call makes them with the new one."""
    if "TYPESAFE_API_KEY" in keys:
        from backend.ai import jev
        await jev.close_client()
    if keys & {"GEMINI_API_KEY", "GROQ_API_KEY"}:
        from backend.ai import summarizer
        summarizer._groq_client = None
        summarizer._gemini_client = None


async def load(db: AsyncSession) -> None:
    for row in (await db.execute(select(AppSecret))).scalars().all():
        if row.key in KEYS and row.value:
            _apply(row.key, row.value)


async def set_values(db: AsyncSession, values: dict) -> dict:
    """`values`: key -> new value; "" or None removes the dashboard value (back to .env)."""
    changed = set()
    for key, value in values.items():
        if key not in KEYS:
            raise ValueError(tr(f"{key} non si può modificare dalla dashboard", f"{key} cannot be changed from the dashboard"))
        value = (value or "").strip()
        if len(value) > KEYS[key][1] or any(c.isspace() for c in value):
            raise ValueError(tr(f"Valore non valido per {key}", f"Invalid value for {key}"))
        if key == "TELEGRAM_CHAT_ID" and value and not value.lstrip("-").isdigit() and not value.startswith("@"):
            raise ValueError(tr("L'ID della chat Telegram è un numero (es. -1001234567890) o @nomecanale",
                                "The Telegram chat ID is a number (e.g. -1001234567890) or @channelname"))
        row = await db.get(AppSecret, key)
        if value:
            if row is None:
                row = AppSecret(key=key, value=value)
                db.add(row)
            row.value, row.updated_at = value, datetime.now(timezone.utc)
        elif row is not None:
            await db.delete(row)
        _apply(key, value)
        changed.add(key)
    await db.commit()
    await _reset_clients(changed)
    return await current(db)


async def current(db: AsyncSession) -> dict:
    stored = {row.key: row for row in (await db.execute(select(AppSecret))).scalars().all()}
    out = {}
    for key, (secret, _) in KEYS.items():
        value = getattr(settings, key) or None
        row = stored.get(key)
        out[key] = {
            "set": bool(value),
            "source": "app" if row is not None and row.value else ("env" if value else None),
            "hint": (("…" + value[-4:]) if secret and len(value) > 8 else ("…" if secret else value)) if value else None,
            "updated_at": row.updated_at if row is not None else None,
        }
    return out
