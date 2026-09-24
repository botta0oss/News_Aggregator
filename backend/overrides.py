"""Forecast parameters changed from the dashboard (e.g. after a backtest).

They are stored in the database and applied over the .env values at startup and when
changed. Only a short list of parameters can be changed this way.
"""
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.models import AppSetting

ALLOWED = {
    "MODEL_WEIGHT_MAX": (0.0, 1.0),
    "MIN_EDGE": (0.0, 0.5),
}
# Values from .env / defaults, captured before any override is applied
DEFAULTS = {key: getattr(settings, key) for key in ALLOWED}


async def load(db: AsyncSession) -> None:
    for row in (await db.execute(select(AppSetting))).scalars().all():
        if row.key in ALLOWED:
            setattr(settings, row.key, row.value)


async def set_values(db: AsyncSession, values: dict, note: str | None = None) -> dict:
    for key, value in values.items():
        if key not in ALLOWED:
            raise ValueError(f"{key} non si può modificare dalla dashboard")
        lo, hi = ALLOWED[key]
        value = float(value)
        if not lo <= value <= hi:
            raise ValueError(f"{key} deve essere tra {lo} e {hi}")
        row = await db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
            db.add(row)
        row.value, row.note, row.updated_at = value, note, datetime.now(timezone.utc)
        setattr(settings, key, value)
    await db.commit()
    return current()


async def reset(db: AsyncSession) -> dict:
    await db.execute(delete(AppSetting))
    await db.commit()
    for key, value in DEFAULTS.items():
        setattr(settings, key, value)
    return current()


def current() -> dict:
    return {key: {"value": getattr(settings, key), "default": DEFAULTS[key],
                  "overridden": getattr(settings, key) != DEFAULTS[key]} for key in ALLOWED}
