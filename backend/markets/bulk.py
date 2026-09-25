"""Jev evaluation of every eligible market, started from the dashboard.

One background job at a time. Progress is kept in memory (single-process app) and
exposed through GET /markets/predict-all so the dashboard can show a progress bar.
"""
import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.ai import jev
from backend.ai.ratelimit import RateLimited
from backend.ai.usage import BudgetExceeded, feature
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Article, Market, MarketArticleLink
from backend.markets import service
from backend.markets.targeted import run_targeted_search
from backend.i18n import tr

logger = logging.getLogger(__name__)

# How many times in a row the job waits out a Jev rate limit before giving up
MAX_RATE_LIMIT_WAITS = 5
# Consecutive failures (network down, invalid key...) after which the job stops
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class BulkJob:
    running: bool = False
    only_new: bool = False
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    current: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    stopped: bool = False
    message: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    consecutive_failures: int = 0


job = BulkJob()
_task: Optional[asyncio.Task] = None
_cancel: Optional[asyncio.Event] = None


def eligible_query():
    """Open markets with a price and at least one recent linked news item (Jev needs evidence)."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    with_news = (
        select(MarketArticleLink.market_id)
        .join(Article, Article.id == MarketArticleLink.article_id)
        .where(Article.fetched_at >= since)
        .distinct()
    )
    return (
        select(Market)
        .where(and_(Market.closed == False, Market.yes_price.is_not(None), Market.id.in_(with_news)))  # noqa: E712
        .order_by(Market.volume.desc().nulls_last(), Market.id)
    )


async def eligible_ids(session: AsyncSession, only_new: bool = False) -> list[str]:
    if only_new:
        # Never predicted, or with news linked after the last forecast
        return [m.id for m in await service.markets_needing_prediction(session, limit=10_000)]
    return [m.id for m in (await session.execute(eligible_query())).scalars().all()]


async def count_eligible(session: AsyncSession) -> dict:
    return {
        "all": len(await eligible_ids(session, only_new=False)),
        "new": len(await eligible_ids(session, only_new=True)),
    }


def snapshot() -> dict:
    return asdict(job)


def is_running() -> bool:
    return job.running


def start(only_new: bool = False, refresh_first: bool = True) -> bool:
    """Starts the background job; False if one is already running."""
    global job, _task, _cancel
    if job.running:
        return False
    job = BulkJob(running=True, only_new=only_new, started_at=datetime.now(timezone.utc), message="Preparazione…")
    _cancel = asyncio.Event()  # created here: bound to the running event loop
    _task = asyncio.create_task(_run(refresh_first))
    return True


def stop() -> bool:
    if not job.running:
        return False
    _cancel.set()
    job.message = tr("Interruzione in corso…", "Stopping…")
    return True


async def wait() -> None:
    """Waits for the running job (tests)."""
    if _task is not None:
        await _task


async def _refresh_markets(session: AsyncSession) -> None:
    """Fresh prices and links, so every edge is computed against the current price."""
    job.message = tr("Aggiornamento prezzi e collegamenti…", "Updating prices and links…")
    async with service._market_lock:  # waits if the scheduled pipeline is syncing
        try:
            await service.sync_markets(session)
        except Exception as e:
            await session.rollback()
            logger.warning(f"Bulk prediction: market sync failed, using last prices: {e}")
        job.message = tr("Ricerca di notizie mirate…", "Targeted news search…")
        await run_targeted_search(session)
        job.message = tr("Collegamento delle notizie ai mercati…", "Linking news to markets…")
        await service.refresh_links(session)


async def _run(refresh_first: bool) -> None:
    with feature("valuta_tutti"):
        await _run_tagged(refresh_first)


async def _run_tagged(refresh_first: bool) -> None:
    try:
        async with SessionLocal() as session:
            if refresh_first:
                await _refresh_markets(session)
            ids = await eligible_ids(session, job.only_new)
            job.total = len(ids)
            job.message = None
            for market_id in ids:
                if _cancel.is_set():
                    job.stopped = True
                    break
                # Reloaded each time: a rollback after a failure expires loaded objects
                market = await session.get(Market, market_id, populate_existing=True)
                if market is None or market.closed:
                    job.skipped += 1
                    continue
                job.current = market.question
                if not await _predict_one(session, market):
                    break
    except Exception as e:  # pragma: no cover - unexpected failure (DB down...)
        logger.exception("Bulk prediction failed")
        job.message = tr(f"Errore: {e}", f"Error: {e}")
    finally:
        job.running = False
        job.current = None
        job.finished_at = datetime.now(timezone.utc)
        if job.message is None or job.message.startswith("Interruzione"):
            job.message = ("Interrotta" if job.stopped
                           else "Non riuscita" if job.failed and not job.done
                           else "Completata")
        logger.info(f"Bulk prediction finished: {snapshot()}")


async def _predict_one(session: AsyncSession, market: Market) -> bool:
    """Predicts one market; returns False when the whole job must stop."""
    question = market.question  # rollbacks expire the object
    waits = 0
    while True:
        try:
            await service.predict_market(session, market)
            job.done += 1
            job.consecutive_failures = 0
            return True
        except BudgetExceeded as e:
            await session.rollback()
            job.message = tr(f"Fermata: {e}", f"Stopped: {e}")
            return False
        except RateLimited as e:
            await session.rollback()
            waits += 1
            if waits > MAX_RATE_LIMIT_WAITS:
                job.message = tr("Fermata: Jev continua a rifiutare le richieste per limite di frequenza. Riprova più tardi.", "Stopped: Jev keeps refusing requests because of the rate limit. Try again later.")
                return False
            job.message = tr(f"Limite di richieste Jev: riprendo tra {max(1, round(e.retry_in))} s", f"Jev request limit: resuming in {max(1, round(e.retry_in))} s")
            try:
                await asyncio.wait_for(_cancel.wait(), timeout=max(1.0, e.retry_in))
                job.stopped, job.message = True, None
                return False
            except asyncio.TimeoutError:
                job.message = None
            await session.refresh(market)  # expired by the rollback
        except (LookupError, ValueError) as e:
            await session.rollback()
            job.skipped += 1  # no news in the window any more, or no price
            logger.info(f"Bulk prediction skipped {question!r}: {e}")
            return True
        except jev.JevUnavailableError as e:
            job.message = str(e)
            return False
        except Exception as e:
            await session.rollback()
            job.failed += 1
            job.consecutive_failures += 1
            if len(job.errors) < 5:
                job.errors.append(f"{question[:80]}: {e}")
            logger.error(f"Bulk prediction failed for {question!r}: {e}")
            if job.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                job.message = tr(f"Fermata dopo {MAX_CONSECUTIVE_FAILURES} errori consecutivi: controlla la connessione e TYPESAFE_API_KEY.",
                             f"Stopped after {MAX_CONSECUTIVE_FAILURES} consecutive errors: check the connection and TYPESAFE_API_KEY.")
                return False
            return True
