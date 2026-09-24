"""News-vs-price alerts.

On Polymarket the advantage is mostly speed: a relevant news item comes out and the price
takes minutes or hours to adjust. When a fresh, relevant news item is linked to an open
market, the market is evaluated with Jev right away (one call). If the evaluation says the
bet is worth it, a Telegram notification is sent. The price is then recorded 15 minutes,
1 hour, 6 hours and 24 hours later, to measure whether alerts really come before the move.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai import jev
from backend.ai.ratelimit import RateLimited
from backend.alerts import telegram
from backend.betting.economics import _num
from backend.config import settings
from backend.db.models import (
    Alert, AlertSettings, Article, Market, MarketArticleLink, MarketPrediction, ProcessedArticle, Source,
)
from backend.ingestor.sources import CATEGORIES
from backend.markets import polymarket
from backend.betting.clv import summarize as clv_summary
from backend.markets.matching import source_quality

logger = logging.getLogger(__name__)

CHECKPOINTS = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24)}
VERDICTS = {"GO": ("GO",), "SMALL": ("GO", "SMALL")}
MIN_SOURCE_QUALITY = 0.5
MAX_FOLLOWUPS_PER_RUN = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Settings ----------

async def get_settings(db: AsyncSession) -> AlertSettings:
    s = await db.get(AlertSettings, 1)
    if s is None:
        s = AlertSettings(id=1, enabled=True, categories=[], min_match=0.6, max_news_age_hours=6.0, daily_budget=30,
                          cooldown_hours=3.0, min_verdict="SMALL", telegram_enabled=True)
        db.add(s)
        await db.commit()
    return s


def validate_settings(data: dict) -> dict:
    """Checks a partial update; raises ValueError with a message for the user."""
    out = {}
    if "enabled" in data:
        out["enabled"] = bool(data["enabled"])
    if "telegram_enabled" in data:
        out["telegram_enabled"] = bool(data["telegram_enabled"])
    if "categories" in data:
        cats = list(dict.fromkeys(data["categories"] or []))
        if any(c not in CATEGORIES for c in cats):
            raise ValueError("Categoria non valida")
        out["categories"] = cats
    ranges = {"min_match": (0.3, 1.0, "La pertinenza minima deve essere tra 30% e 100%"),
              "max_news_age_hours": (0.5, 72, "L'età massima della notizia deve essere tra 0,5 e 72 ore"),
              "daily_budget": (0, 500, "Il limite giornaliero deve essere tra 0 e 500 chiamate"),
              "cooldown_hours": (0, 72, "La pausa per mercato deve essere tra 0 e 72 ore")}
    for key, (lo, hi, msg) in ranges.items():
        if key in data:
            value = float(data[key])
            if not lo <= value <= hi:
                raise ValueError(msg)
            out[key] = int(value) if key == "daily_budget" else value
    if "min_verdict" in data:
        if data["min_verdict"] not in VERDICTS:
            raise ValueError("Esito minimo non valido")
        out["min_verdict"] = data["min_verdict"]
    for key in ("quiet_start", "quiet_end"):
        if key in data:
            value = data[key]
            if value is not None and not (isinstance(value, int) and 0 <= value <= 23):
                raise ValueError("Le ore silenziose vanno da 0 a 23")
            out[key] = value
    return out


async def update_settings(db: AsyncSession, data: dict) -> AlertSettings:
    s = await get_settings(db)
    for key, value in validate_settings(data).items():
        setattr(s, key, value)
    s.updated_at = _now()
    await db.commit()
    return s


def in_quiet_hours(s: AlertSettings, now: Optional[datetime] = None) -> bool:
    if s.quiet_start is None or s.quiet_end is None or s.quiet_start == s.quiet_end:
        return False
    try:
        hour = (now or _now()).astimezone(ZoneInfo(settings.ALERT_TIMEZONE)).hour
    except Exception:
        hour = (now or _now()).hour
    if s.quiet_start < s.quiet_end:
        return s.quiet_start <= hour < s.quiet_end
    return hour >= s.quiet_start or hour < s.quiet_end  # across midnight, e.g. 23-7


# ---------- Detection ----------

async def calls_last_24h(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(Alert.id)).where(Alert.created_at >= _now() - timedelta(hours=24)))).scalar() or 0


async def find_triggers(db: AsyncSession, s: AlertSettings) -> tuple[list[dict], list]:
    """Fresh, relevant, reliable news linked to open markets and not seen yet.

    Returns (one trigger per market, best first; ids of all links looked at).
    """
    fresh_since = _now() - timedelta(hours=s.max_news_age_hours)
    rows = (await db.execute(
        select(MarketArticleLink, Article, Market, ProcessedArticle, Source)
        .join(Article, Article.id == MarketArticleLink.article_id)
        .join(Market, Market.id == MarketArticleLink.market_id)
        .join(Source, Source.id == Article.source_id)
        .outerjoin(ProcessedArticle, ProcessedArticle.article_id == Article.id)
        .where(MarketArticleLink.alert_checked == False)  # noqa: E712
        .where(Article.fetched_at >= _now() - timedelta(hours=max(s.max_news_age_hours, 1) * 2))
        .limit(2000)
    )).all()
    seen = [link.id for link, *_ in rows]
    best: dict[str, dict] = {}
    for link, article, market, processed, source in rows:
        published = article.published_at or article.fetched_at
        if published is None or published < fresh_since:
            continue
        if market.closed or market.yes_price is None:
            continue
        if (link.match_score or 0.0) < s.min_match:
            continue
        quality = source_quality(processed)
        if quality < MIN_SOURCE_QUALITY or (processed is not None and (processed.is_opinion or 0) >= 0.6):
            continue
        category = market.category or (processed.category if processed else None)
        if s.categories and category not in s.categories:
            continue
        score = round(link.match_score * quality, 4)
        if market.id not in best or score > best[market.id]["score"]:
            best[market.id] = {"market_id": market.id, "article_id": article.id, "title": article.title,
                               "source": article.publisher or source.name, "published": published, "score": score}
    return sorted(best.values(), key=lambda t: t["score"], reverse=True), seen


async def in_cooldown(db: AsyncSession, market_id: Optional[str], hours: float, event_id: Optional[str] = None) -> bool:
    if hours <= 0:
        return False
    cond = Alert.multi_event_id == event_id if event_id else Alert.market_id == market_id
    last = (await db.execute(select(func.max(Alert.created_at)).where(cond))).scalar()
    return last is not None and last >= _now() - timedelta(hours=hours)


async def find_event_triggers(db: AsyncSession, s: AlertSettings) -> tuple[list[dict], list]:
    """Same as find_triggers, for news linked to open multi-outcome events."""
    from backend.db.models import MultiArticleLink, MultiEvent
    fresh_since = _now() - timedelta(hours=s.max_news_age_hours)
    rows = (await db.execute(
        select(MultiArticleLink, Article, MultiEvent, ProcessedArticle, Source)
        .join(Article, Article.id == MultiArticleLink.article_id)
        .join(MultiEvent, MultiEvent.id == MultiArticleLink.event_id)
        .join(Source, Source.id == Article.source_id)
        .outerjoin(ProcessedArticle, ProcessedArticle.article_id == Article.id)
        .where(MultiArticleLink.alert_checked == False)  # noqa: E712
        .where(Article.fetched_at >= _now() - timedelta(hours=max(s.max_news_age_hours, 1) * 2))
        .limit(2000)
    )).all()
    seen = [link.id for link, *_ in rows]
    best: dict[str, dict] = {}
    for link, article, event, processed, source in rows:
        published = article.published_at or article.fetched_at
        if published is None or published < fresh_since or event.closed:
            continue
        if (link.match_score or 0.0) < s.min_match:
            continue
        quality = source_quality(processed)
        if quality < MIN_SOURCE_QUALITY or (processed is not None and (processed.is_opinion or 0) >= 0.6):
            continue
        category = processed.category if processed else None
        if s.categories and category not in s.categories:
            continue
        score = round(link.match_score * quality, 4)
        if event.id not in best or score > best[event.id]["score"]:
            best[event.id] = {"event_id": event.id, "event_title": event.title, "article_id": article.id,
                              "title": article.title, "source": article.publisher or source.name,
                              "published": published, "score": score}
    return sorted(best.values(), key=lambda t: t["score"], reverse=True), seen


async def run_alerts(db: AsyncSession) -> dict:
    """Checks new links and raises alerts. Safe to call after every link refresh."""
    from backend.ai.usage import feature
    with feature("allerte"):
        return await _run_alerts(db)


async def _run_alerts(db: AsyncSession) -> dict:
    from backend.markets.service import predict_market  # the market service imports this module

    stats = {"triggers": 0, "evaluated": 0, "opportunities": 0, "notified": 0, "skipped_budget": 0}
    s = await get_settings(db)
    if not s.enabled or not jev.is_enabled():
        return stats
    triggers, seen = await find_triggers(db, s)
    if seen:
        # Every link is looked at once: a link that did not trigger now never will
        await db.execute(update(MarketArticleLink).where(MarketArticleLink.id.in_(seen)).values(alert_checked=True))
        await db.commit()
    stats["triggers"] = len(triggers)
    budget = max(0, s.daily_budget - await calls_last_24h(db))
    for trig in triggers:
        if await in_cooldown(db, trig["market_id"], s.cooldown_hours):
            continue
        if budget <= 0:
            stats["skipped_budget"] += 1
            continue
        market = await db.get(Market, trig["market_id"], populate_existing=True)
        if market is None or market.closed:
            continue
        await _refresh_price(market)
        price = market.yes_price
        try:
            prediction = await predict_market(db, market)
        except RateLimited as e:
            logger.warning(f"Alerts paused: {e}")
            await db.rollback()
            break
        except (LookupError, ValueError) as e:
            await db.rollback()
            logger.info(f"Alert skipped for {trig['market_id']}: {e}")
            continue
        except Exception as e:
            await db.rollback()
            logger.error(f"Alert evaluation failed for {trig['market_id']}: {e}")
            continue
        budget -= 1
        stats["evaluated"] += 1
        alert = await _record(db, market, prediction, trig, price, s)
        if alert.opportunity:
            stats["opportunities"] += 1
            if await notify(db, alert, market, prediction, trig, s):
                stats["notified"] += 1
    else:  # not reached after a `break` (Jev rate limited or over budget): events wait for the next run
        await _run_event_alerts(db, s, stats)
    if stats["evaluated"] or stats["triggers"]:
        logger.info(f"Alerts: {stats}")
    return stats


async def _run_event_alerts(db: AsyncSession, s: AlertSettings, stats: dict) -> None:
    """Multi-outcome events: one Jev call re-forecasts the whole distribution."""
    from backend.db.models import MultiArticleLink, MultiEvent
    from backend.multi import service as multi

    budget = max(0, s.daily_budget - await calls_last_24h(db))
    triggers, seen = await find_event_triggers(db, s)
    if seen:
        await db.execute(update(MultiArticleLink).where(MultiArticleLink.id.in_(seen)).values(alert_checked=True))
        await db.commit()
    stats["triggers"] += len(triggers)
    for trig in triggers:
        if await in_cooldown(db, None, s.cooldown_hours, event_id=trig["event_id"]):
            continue
        if budget <= 0:
            stats["skipped_budget"] += 1
            continue
        event = await db.get(MultiEvent, trig["event_id"], populate_existing=True)
        if event is None or event.closed:
            continue
        try:
            fresh = await polymarket.fetch_event(event.id)  # prices of this moment
            if fresh:
                multi._apply_event(event, fresh)
                await multi._apply_outcomes(db, event, fresh)
                await db.commit()
        except Exception:
            await db.rollback()
        try:
            prediction = await multi.predict_event(db, event)
        except RateLimited as e:
            logger.warning(f"Alerts paused: {e}")
            await db.rollback()
            break
        except (LookupError, ValueError) as e:
            await db.rollback()
            logger.info(f"Alert skipped for event {event.id}: {e}")
            continue
        except Exception as e:
            await db.rollback()
            logger.error(f"Alert evaluation failed for event {event.id}: {e}")
            continue
        budget -= 1
        stats["evaluated"] += 1
        best_id = prediction.best_outcome_id
        market = await db.get(Market, best_id) if best_id and best_id != multi.OTHER_ID else None
        if market is None:
            continue
        pred = multi.outcome_prediction(prediction, best_id)
        ev = (prediction.economics or {}).get(best_id) or {}
        entry = next(o for o in prediction.outcomes if o["id"] == best_id)
        alert = Alert(
            market_id=best_id, multi_event_id=event.id, article_id=trig["article_id"], news_at=trig["published"],
            trigger_score=trig["score"], price=entry.get("price", entry["market"]),
            side="NO" if prediction.signal == "BUY_NO" else "YES", verdict=ev.get("verdict"),
            signal=prediction.signal, edge=entry["edge"], blended=entry["blended"], outlay=ev.get("outlay"),
            limit_price=ev.get("limit_price"),
            opportunity=prediction.signal in ("BUY_YES", "BUY_NO") and ev.get("verdict") in VERDICTS.get(s.min_verdict, ("GO",)),
            followups={},
        )
        db.add(alert)
        await db.commit()
        if alert.opportunity:
            stats["opportunities"] += 1
            trig = {**trig, "outcome": entry["label"]}
            if await notify(db, alert, market, pred, trig, s):
                stats["notified"] += 1


async def _refresh_price(market: Market) -> None:
    """The alert compares against the price right now, not the one of the last sync."""
    try:
        fresh = await polymarket.fetch_market(market.id)
        if fresh and fresh.yes_price is not None:
            market.yes_price = fresh.yes_price
            market.closed = fresh.closed
    except Exception:
        pass  # keep the last synced price


async def _record(db: AsyncSession, market: Market, prediction: MarketPrediction, trig: dict,
                  price: float, s: AlertSettings) -> Alert:
    ev = prediction.economics or {}
    verdict = ev.get("verdict")
    alert = Alert(
        market_id=market.id, article_id=trig["article_id"], prediction_id=prediction.id,
        news_at=trig["published"], trigger_score=trig["score"], price=price if price is not None else prediction.market_probability,
        side=ev.get("side"), verdict=verdict, signal=prediction.signal, edge=prediction.edge,
        blended=prediction.blended_probability, outlay=ev.get("outlay"), limit_price=ev.get("limit_price"),
        opportunity=verdict in VERDICTS.get(s.min_verdict, ("GO",)), followups={},
    )
    db.add(alert)
    await db.commit()
    return alert


# ---------- Notification ----------

def _short(x: float) -> str:
    """One decimal, none when it is zero: 34¢, 44,5¢."""
    text = _num(x, 1)
    return text[:-2] if text.endswith(",0") else text


def _pct(p: Optional[float]) -> str:
    return "–" if p is None else f"{_short(p * 100)}%"


def _cents(p: Optional[float]) -> str:
    return "–" if p is None else f"{_short(p * 100)}¢"


def format_message(alert: Alert, market: Market, prediction: MarketPrediction, trig: dict,
                   sell_above: Optional[float] = None) -> str:
    e = telegram.escape
    side = "SÌ" if alert.side == "YES" else "NO"
    verdict = "conviene" if alert.verdict == "GO" else "conviene, puntata piccola"
    age_min = max(0, int((_now() - trig["published"]).total_seconds() // 60)) if trig.get("published") else None
    age = "" if age_min is None else (f", {age_min} min fa" if age_min < 120 else f", {age_min // 60} ore fa")
    head = f"Compra {side} su {e(trig['outcome'])}" if trig.get("outcome") else f"Compra {side}"
    lines = [
        f"🔔 <b>{head}</b> · {verdict}",
        f"<b>{e(trig.get('event_title') or market.question)}</b>",
        "",
        f"Notizia: {e(trig['title'])} ({e(trig['source'])}{age})",
        f"Prezzo SÌ {_cents(alert.price)} → stima {_pct(prediction.blended_probability)} "
        f"(Jev {_pct(prediction.model_probability)}, evidenze {_pct(prediction.evidence_strength)})",
        f"Edge {'+' if (alert.edge or 0) >= 0 else '−'}{_num(abs(alert.edge or 0) * 100, 1)} pt",
    ]
    if alert.outlay:
        lines += ["", f"<b>Ordine</b>: compra {side} con limite {_cents(alert.limit_price)} (puntata simulata {_num(alert.outlay, 2)} $)"]
        if sell_above is not None:
            lines.append(f"<b>Poi</b>: vendita limite a {_cents(sell_above)}, oppure tieni fino alla risoluzione")
    if settings.PUBLIC_URL:
        path = f"multi/{market.multi_event_id}" if market.multi_event_id else f"mercati/{market.id}"
        lines += ["", f"{settings.PUBLIC_URL.rstrip('/')}/#/{path}"]
    return "\n".join(lines)


async def _sell_target(db: AsyncSession, alert: Alert, market: Market, prediction) -> Optional[float]:
    """Price at which to sell the shares the alert suggests buying (see betting/strategy.py)."""
    from backend.betting import portfolio
    from backend.betting.economics import model_sigma
    from backend.betting.plans import exit_plan, forecast_of
    from backend.betting.profiles import get_profile
    if alert.side not in ("YES", "NO"):
        return None
    try:
        profile = get_profile((await portfolio.get_settings(db)).preset)
        sigma = model_sigma(prediction.model_probability, prediction.evidence_strength,
                            forecast_of(prediction, 0).weight, settings.MODEL_PSEUDO_COUNT)
        return exit_plan(prediction, market, alert.side, profile, portfolio.days_to_end(market), sigma)["sell_above"]
    except Exception as e:
        logger.info(f"No sale target for alert {alert.id}: {e}")
        return None


async def notify(db: AsyncSession, alert: Alert, market: Market, prediction: MarketPrediction, trig: dict,
                 s: AlertSettings) -> bool:
    if not s.telegram_enabled or not telegram.is_configured():
        return False
    try:
        await telegram.send_message(format_message(alert, market, prediction, trig, await _sell_target(db, alert, market, prediction)),
                                    silent=in_quiet_hours(s))
        alert.notified_at = _now()
        ok = True
    except telegram.TelegramError as e:
        alert.notify_error = str(e)
        logger.warning(f"Alert notification failed: {e}")
        ok = False
    await db.commit()
    return ok


# ---------- Price after the alert ----------

async def record_followups(db: AsyncSession, fetch=None) -> int:
    """Stores the market price at each checkpoint that has come due."""
    fetch = fetch or polymarket.fetch_market
    now = _now()
    alerts = (await db.execute(
        select(Alert).where(Alert.created_at >= now - timedelta(hours=26), Alert.created_at <= now - CHECKPOINTS["15m"])
        .order_by(Alert.created_at)
    )).scalars().all()
    due = [(a, [k for k, d in CHECKPOINTS.items() if k not in (a.followups or {}) and now >= a.created_at + d]) for a in alerts]
    due = [(a, keys) for a, keys in due if keys][:MAX_FOLLOWUPS_PER_RUN]
    prices: dict[str, Optional[float]] = {}
    recorded = 0
    for alert, keys in due:
        if alert.market_id not in prices:
            try:
                data = await fetch(alert.market_id)
                prices[alert.market_id] = data.yes_price if data else None
            except Exception as e:
                logger.warning(f"Alert follow-up: price of {alert.market_id} not available: {e}")
                prices[alert.market_id] = None
        price = prices[alert.market_id]
        if price is None:
            continue
        # Checkpoints missed (server down) get the current price, flagged as late
        late = {k: now - (alert.created_at + CHECKPOINTS[k]) > max(timedelta(minutes=10), CHECKPOINTS[k] / 4) for k in keys}
        alert.followups = {**(alert.followups or {}), **{k: {"price": price, "late": late[k]} for k in keys}}
        recorded += 1
    await db.commit()
    return recorded


def favorable_move(alert: Alert, price: Optional[float]) -> Optional[float]:
    """Price change in the direction of the suggested side, in probability points."""
    if price is None or alert.side not in ("YES", "NO"):
        return None
    move = price - alert.price
    return round(move if alert.side == "YES" else -move, 4)


def followup_price(alert: Alert, key: str) -> Optional[float]:
    value = (alert.followups or {}).get(key)
    return value.get("price") if isinstance(value, dict) else value


async def summary(db: AsyncSession, days: int = 30) -> dict:
    """How the opportunities did: price move after the alert and outcome of resolved markets."""
    since = _now() - timedelta(days=days)
    rows3 = (await db.execute(
        select(Alert, Market.resolved_yes, Market).join(Market, Market.id == Alert.market_id)
        .where(Alert.created_at >= since)
    )).all()
    rows = [(a, r) for a, r, _ in rows3]
    opps = [(a, r) for a, r in rows if a.opportunity]
    moves = {}
    for key in CHECKPOINTS:
        values = [m for a, _ in opps if (m := favorable_move(a, followup_price(a, key))) is not None]
        moves[key] = {
            "count": len(values),
            "avg_move": round(sum(values) / len(values), 4) if values else None,
            "share_favorable": round(sum(1 for v in values if v > 0) / len(values), 4) if values else None,
        }
    closing = {a.id: favorable_move(a, m.last_trading_price) for a, _, m in rows3 if m.closed}
    clv_values = [closing.get(a.id) for a, _ in opps]
    resolved = [(a, r) for a, r in opps if r is not None and a.side in ("YES", "NO")]
    won = sum(1 for a, r in resolved if (a.side == "YES") == r)
    s = await get_settings(db)
    return {
        "days": days,
        "evaluated": len(rows),
        "opportunities": len(opps),
        "notified": sum(1 for a, _ in opps if a.notified_at),
        "moves": moves,
        "resolved": len(resolved),
        "won": won,
        # Move from the alert price to the closing price, in the suggested direction
        "clv": clv_summary(clv_values),
        "calls_last_24h": await calls_last_24h(db),
        "daily_budget": s.daily_budget,
    }
