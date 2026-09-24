"""Usage and cost of the paid AI calls, with daily limits.

Every Jev, Groq and Gemini call is recorded with the feature it served (classification,
forecasts, alerts, backtest...), its tokens and an estimated cost from the prices set in
the dashboard. Before a call, `check()` enforces the daily limits: over the limit it raises
BudgetExceeded, a kind of RateLimited, so the existing pauses apply (loops stop, the
summarizer and the classifier fall back to their free alternatives).

Days follow ALERT_TIMEZONE (midnight local time). Counters are kept in memory for speed
and reloaded from the database at start and at every new day (single-process app).
"""
import contextlib
import logging
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.ai.ratelimit import RateLimited
from backend.config import settings

logger = logging.getLogger(__name__)

FEATURES = {
    "classificazione": "Classificazione notizie",
    "riassunti": "Riassunti",
    "riclassificazione": "Riclassificazione",
    "previsioni": "Previsioni",
    "valuta_tutti": "Valuta tutti con Jev",
    "allerte": "Allerte",
    "piu_esiti": "Più esiti",
    "backtest": "Backtest",
    "altro": "Altro",
}
PAID = ("jev", "groq", "gemini")

_feature: ContextVar[str] = ContextVar("ai_feature", default="altro")


@contextlib.contextmanager
def feature(name: str):
    """Tags the AI calls made inside the block (and in tasks started from it)."""
    token = _feature.set(name if name in FEATURES else "altro")
    try:
        yield
    finally:
        _feature.reset(token)


def current_feature() -> str:
    return _feature.get()


class BudgetExceeded(RateLimited):
    """A daily limit is reached: no more paid calls until midnight."""

    def __init__(self, provider: str, retry_in: float, reason: str):
        super().__init__(provider, retry_in)
        self.reason = reason
        self.args = (reason,)

    def __str__(self) -> str:
        return self.reason


# ---------- Day and counters ----------

def _tz():
    try:
        return ZoneInfo(settings.ALERT_TIMEZONE)
    except Exception:
        return timezone.utc


def day_start(now: Optional[datetime] = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(_tz())
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_to_midnight(now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    return max(60.0, (day_start(now) + timedelta(days=1) - now.astimezone(_tz())).total_seconds())


class _Today:
    def __init__(self):
        self.day: Optional[datetime] = None
        self.jev_calls = 0
        self.cost = 0.0
        self.warned: set = set()


_today = _Today()


def reset() -> None:
    """Forget the in-memory counters (tests; the next call reloads them from the database)."""
    global _today
    _today = _Today()


async def _ensure_today() -> _Today:
    start = day_start()
    if _today.day != start:
        from backend.db.database import SessionLocal
        from backend.db.models import ApiUsage
        _today.day, _today.warned = start, set()
        try:
            async with SessionLocal() as db:
                calls, cost = (await db.execute(
                    select(func.count().filter((ApiUsage.provider == "jev") & ApiUsage.ok), func.coalesce(func.sum(ApiUsage.cost_usd), 0.0))
                    .where(ApiUsage.at >= start)
                )).one()
            _today.jev_calls, _today.cost = int(calls or 0), float(cost or 0.0)
        except Exception as e:  # the database is down: count from zero rather than block everything
            logger.warning(f"Could not load today's AI usage: {e}")
            _today.jev_calls, _today.cost = 0, 0.0
    return _today


def estimate_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    per_call = settings.JEV_PRICE_PER_CALL if provider == "jev" else 0.0
    prices = {
        "jev": (settings.JEV_PRICE_INPUT_MTOK, settings.JEV_PRICE_OUTPUT_MTOK),
        "groq": (settings.GROQ_PRICE_INPUT_MTOK, settings.GROQ_PRICE_OUTPUT_MTOK),
        "gemini": (settings.GEMINI_PRICE_INPUT_MTOK, settings.GEMINI_PRICE_OUTPUT_MTOK),
    }.get(provider, (0.0, 0.0))
    return per_call + (input_tokens or 0) * prices[0] / 1e6 + (output_tokens or 0) * prices[1] / 1e6


# ---------- Before and after each call ----------

async def check(provider: str) -> None:
    """Raises BudgetExceeded if today's limits do not allow another paid call."""
    if provider not in PAID:
        return
    t = await _ensure_today()
    if provider == "jev" and settings.DAILY_JEV_CALL_LIMIT > 0 and t.jev_calls >= settings.DAILY_JEV_CALL_LIMIT:
        raise BudgetExceeded(provider, seconds_to_midnight(),
                             f"Limite giornaliero di {settings.DAILY_JEV_CALL_LIMIT} chiamate a Jev raggiunto: si riparte a mezzanotte.")
    if settings.DAILY_AI_BUDGET_USD > 0 and t.cost >= settings.DAILY_AI_BUDGET_USD:
        raise BudgetExceeded(provider, seconds_to_midnight(),
                             f"Budget giornaliero di {settings.DAILY_AI_BUDGET_USD:.2f} $ per le API AI raggiunto: si riparte a mezzanotte.")


async def record(provider: str, input_tokens: Optional[int] = 0, output_tokens: Optional[int] = 0, ok: bool = True) -> None:
    """Saves one call. Never raises: accounting must not break the call it describes."""
    if provider not in PAID:
        return
    try:
        from backend.db.database import SessionLocal
        from backend.db.models import ApiUsage
        t = await _ensure_today()
        cost = estimate_cost(provider, input_tokens or 0, output_tokens or 0) if ok else 0.0
        async with SessionLocal() as db:
            db.add(ApiUsage(provider=provider, feature=current_feature(), input_tokens=int(input_tokens or 0),
                            output_tokens=int(output_tokens or 0), cost_usd=round(cost, 6), ok=ok))
            await db.commit()
        if ok:
            t.jev_calls += provider == "jev"
            t.cost += cost
            await _maybe_warn(t)
    except Exception as e:
        logger.warning(f"Could not record AI usage: {e}")


def shares(t: Optional[_Today] = None) -> dict:
    t = t or _today
    return {
        "jev_calls": (t.jev_calls / settings.DAILY_JEV_CALL_LIMIT) if settings.DAILY_JEV_CALL_LIMIT > 0 else None,
        "cost": (t.cost / settings.DAILY_AI_BUDGET_USD) if settings.DAILY_AI_BUDGET_USD > 0 else None,
    }


async def _maybe_warn(t: _Today) -> None:
    """Once per day and per threshold (warning share, then 100%): log and, if configured, Telegram."""
    labels = {"jev_calls": "delle chiamate a Jev", "cost": "del budget per le API AI"}
    for key, share in shares(t).items():
        if share is None:
            continue
        if share >= 1.0 and (key, "full") not in t.warned:
            t.warned |= {(key, "full"), (key, "warn")}
            text = (f"⚠️ <b>News × Markets</b>: limite giornaliero {labels[key]} esaurito. Le chiamate a pagamento "
                    "riprendono a mezzanotte; classificazione e riassunti usano le alternative gratuite.")
        elif settings.USAGE_WARN_SHARE <= share < 1.0 and (key, "warn") not in t.warned:
            t.warned.add((key, "warn"))
            text = f"⚠️ <b>News × Markets</b>: usato il {round(share * 100)}% del limite giornaliero {labels[key]}."
        else:
            continue
        logger.warning(text)
        await _notify(text)


async def _notify(text: str) -> None:
    try:
        from backend.alerts import telegram
        if telegram.is_configured():
            await telegram.send_message(text)
    except Exception as e:
        logger.warning(f"Usage warning not sent: {e}")


# ---------- Reports ----------

async def today_status() -> dict:
    t = await _ensure_today()
    s = shares(t)
    return {
        "day": t.day.date().isoformat(),
        "jev_calls": t.jev_calls, "jev_call_limit": settings.DAILY_JEV_CALL_LIMIT,
        "cost": round(t.cost, 4), "budget": settings.DAILY_AI_BUDGET_USD,
        "share_jev_calls": s["jev_calls"], "share_cost": s["cost"],
        "warn_share": settings.USAGE_WARN_SHARE,
        "blocked": any(v is not None and v >= 1.0 for v in s.values()),
        "resets_in_seconds": round(seconds_to_midnight()),
    }


async def report(db, days: int = 30) -> dict:
    from backend.db.models import ApiUsage
    since = day_start() - timedelta(days=days - 1)
    local_day = func.date(func.timezone(settings.ALERT_TIMEZONE, ApiUsage.at))
    rows = (await db.execute(
        select(local_day.label("day"), ApiUsage.provider, ApiUsage.feature,
               func.count().filter(ApiUsage.ok).label("calls"), func.count().filter(~ApiUsage.ok).label("errors"),
               func.coalesce(func.sum(ApiUsage.input_tokens), 0), func.coalesce(func.sum(ApiUsage.output_tokens), 0),
               func.coalesce(func.sum(ApiUsage.cost_usd), 0.0))
        .where(ApiUsage.at >= since)
        .group_by(local_day, ApiUsage.provider, ApiUsage.feature)
        .order_by(local_day)
    )).all()
    items = [{"day": d.isoformat(), "provider": p, "feature": f, "calls": c, "errors": e,
              "input_tokens": int(i), "output_tokens": int(o), "cost": round(float(cost), 4)}
             for d, p, f, c, e, i, o, cost in rows]
    return {"days": days, "since": since.date().isoformat(), "items": items, "features": FEATURES}
