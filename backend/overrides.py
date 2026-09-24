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
    "JEV_CALIB_A": (-3.0, 3.0),
    "JEV_CALIB_B": (0.2, 3.0),
    # Daily limits and prices of the paid AI calls (page "Uso e costi")
    "DAILY_JEV_CALL_LIMIT": (0, 100_000),
    "DAILY_AI_BUDGET_USD": (0.0, 10_000.0),
    "JEV_PRICE_PER_CALL": (0.0, 10.0),
    "JEV_PRICE_INPUT_MTOK": (0.0, 1000.0),
    "JEV_PRICE_OUTPUT_MTOK": (0.0, 1000.0),
    "GROQ_PRICE_INPUT_MTOK": (0.0, 1000.0),
    "GROQ_PRICE_OUTPUT_MTOK": (0.0, 1000.0),
    "GEMINI_PRICE_INPUT_MTOK": (0.0, 1000.0),
    "GEMINI_PRICE_OUTPUT_MTOK": (0.0, 1000.0),
}
FORECAST_KEYS = ("MODEL_WEIGHT_MAX", "MIN_EDGE", "JEV_CALIB_A", "JEV_CALIB_B")
USAGE_KEYS = tuple(k for k in ALLOWED if k not in FORECAST_KEYS)
# Values from .env / defaults, captured before any override is applied
DEFAULTS = {key: getattr(settings, key) for key in ALLOWED}


def _cast(key: str, value: float):
    return int(value) if isinstance(DEFAULTS[key], int) and not isinstance(DEFAULTS[key], bool) else float(value)


async def load(db: AsyncSession) -> None:
    for row in (await db.execute(select(AppSetting))).scalars().all():
        if row.key in ALLOWED:
            setattr(settings, row.key, _cast(row.key, row.value))


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
        setattr(settings, key, _cast(key, value))
    await db.commit()
    return current()


async def reset(db: AsyncSession, keys=None) -> dict:
    """Back to the .env values, for the given keys (all by default)."""
    keys = list(keys or ALLOWED)
    await db.execute(delete(AppSetting).where(AppSetting.key.in_(keys)))
    await db.commit()
    for key in keys:
        setattr(settings, key, DEFAULTS[key])
    return current()


def current(keys=None) -> dict:
    return {key: {"value": getattr(settings, key), "default": DEFAULTS[key],
                  "overridden": getattr(settings, key) != DEFAULTS[key]} for key in (keys or ALLOWED)}
