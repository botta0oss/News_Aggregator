"""Backtest on resolved Polymarket markets.

For each resolved market and each horizon (e.g. 7 days before the end) the situation of
that moment is rebuilt: the price from the CLOB price history and the news published
before that date. Jev answers the same questions as in the live app, with "today" set to
that date; the answer goes through the same blend, signal and economic evaluation. The
real outcome then says who was right.

News of the past, two sources:
- "archive": the articles this app itself had saved by that date (fetched_at <= as_of).
  Nothing written later can slip in: the honest measure, when the archive goes back far enough.
- "google": Google News with date filters. Covers any period, but the filters are known to
  leak later articles and updated pages; results can look better than they are.
"auto" uses the archive when it has news for the case, Google otherwise; every case records
which one it used and the summary reports the archive-only cases separately.

Limit: Jev may already know how past events ended. Markets resolved after the model's
knowledge cutoff give an honest measure; older ones can look better than they are.
"""
import asyncio
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import select

from backend.ai import jev
from backend.ai.ratelimit import RateLimited
from backend.ai.usage import BudgetExceeded, feature
from backend.ai.typesafe_evaluator import _heuristic_evaluation
from backend.backtest.analysis import summarize
from backend.betting.fees import category_rate
from backend.betting.economics import Exposure, estimated_quote, evaluate
from backend.i18n import tr
from backend.betting.profiles import get_profile
from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import BacktestCase, BacktestRun, BettingSettings
from backend.ingestor.deduplicator import get_title_embedding
from backend.ingestor.fetcher import fetch_feed
from backend.markets import polymarket
from backend.markets.forecast import compute_signal
from backend.markets.matching import extract_terms, match_score, objective_evidence, rank_evidence, term_overlap
from backend.markets.service import EVIDENCE_CRITERIA, build_jev_request, parse_forecast
from backend.markets.targeted import build_query

logger = logging.getLogger(__name__)

NEWS_LOOKBACK_DAYS = 7
DECIDED_BELOW, DECIDED_ABOVE = 0.03, 0.97
MAX_RATE_LIMIT_WAITS = 5
MAX_CONSECUTIVE_FAILURES = 5
NEWS_SOURCES = ("auto", "archive", "google")
MULTI_MAX_PRICED = 30   # outcomes of an event whose price history is fetched (most traded first)


@dataclass
class Params:
    resolved_after: datetime
    resolved_before: datetime
    max_markets: int = 30
    min_volume: float = 50_000.0
    horizons: list = field(default_factory=lambda: [7])
    max_calls: int = 60
    exclude_decided: bool = True
    kinds: list = field(default_factory=lambda: ["binary"])   # "binary" (YES/NO) and/or "multi" (several outcomes)
    news_source: str = "auto"                                  # auto / archive / google (see the module docstring)

    def validate(self) -> "Params":
        self.kinds = [k for k in dict.fromkeys(self.kinds) if k in ("binary", "multi")]
        if not self.kinds:
            raise ValueError(tr("Scegli almeno un tipo di mercato", "Choose at least one market type"))
        if self.news_source not in NEWS_SOURCES:
            raise ValueError(tr("Fonte delle notizie non valida", "Invalid news source"))
        if self.resolved_after >= self.resolved_before:
            raise ValueError(tr("La data di inizio deve essere prima della data di fine", "The start date must be before the end date"))
        if self.resolved_before > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError(tr("La data di fine non può essere nel futuro", "The end date cannot be in the future"))
        if not 1 <= self.max_markets <= 300:
            raise ValueError(tr("Il numero di mercati deve essere tra 1 e 300", "The number of markets must be between 1 and 300"))
        if not 0 < self.max_calls <= 1000:
            raise ValueError(tr("Il limite di chiamate deve essere tra 1 e 1000", "The call limit must be between 1 and 1000"))
        self.horizons = sorted({int(h) for h in self.horizons})
        if not self.horizons or any(h < 1 or h > 180 for h in self.horizons):
            raise ValueError(tr("Gli orizzonti vanno da 1 a 180 giorni", "Horizons go from 1 to 180 days"))
        return self

    def as_json(self) -> dict:
        d = asdict(self)
        d["resolved_after"] = self.resolved_after.isoformat()
        d["resolved_before"] = self.resolved_before.isoformat()
        return d


_task: Optional[asyncio.Task] = None
_cancel: Optional[asyncio.Event] = None
_run_id = None


def is_running() -> bool:
    return _task is not None and not _task.done()


async def start(params: Params) -> BacktestRun:
    global _task, _cancel, _run_id
    if is_running():
        raise RuntimeError(tr("Un backtest è già in corso", "A backtest is already running"))
    if not jev.is_enabled():
        raise jev.JevUnavailableError(tr("TYPESAFE_API_KEY non è configurata", "TYPESAFE_API_KEY is not configured"))
    params.validate()
    async with SessionLocal() as db:
        run = BacktestRun(params=params.as_json(), status="running", message=tr("Ricerca dei mercati risolti…", "Looking for resolved markets…"))
        db.add(run)
        await db.commit()
        await db.refresh(run)
    _cancel = asyncio.Event()
    _run_id = run.id
    _task = asyncio.create_task(_run(run.id, params))
    return run


def stop() -> bool:
    if not is_running():
        return False
    _cancel.set()
    return True


async def wait() -> None:
    if _task is not None:
        await _task


async def recover_interrupted() -> None:
    """A restart kills the task: mark runs left 'running' as stopped."""
    async with SessionLocal() as db:
        for run in (await db.execute(select(BacktestRun).where(BacktestRun.status == "running"))).scalars().all():
            run.status, run.message = "stopped", tr("Interrotto dal riavvio del server", "Stopped by the server restart")
            run.finished_at = datetime.now(timezone.utc)
        await db.commit()


# ---------- Planning ----------

def plan_cases(markets: list, horizons: list) -> list[tuple]:
    """(market, horizon, as_of) for each market and horizon that fits in the market's life."""
    plan = []
    for m in markets:
        close = min(m.end_date, m.closed_time) if m.closed_time else m.end_date
        for h in horizons:
            as_of = close - timedelta(days=h)
            if m.start_date and as_of < m.start_date + timedelta(days=1):
                continue  # the market did not exist yet (or had no trading history)
            plan.append((m, h, as_of))
    return plan


def categorize(question: str) -> Optional[str]:
    return _heuristic_evaluation(question, "", question).get("category")


# ---------- News of the past ----------

def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def historical_search_url(question: str, as_of: datetime) -> Optional[str]:
    query = build_query(question)
    if not query:
        return None
    after = (as_of - timedelta(days=NEWS_LOOKBACK_DAYS)).date().isoformat()
    before = (as_of + timedelta(days=1)).date().isoformat()  # items after as_of are dropped below
    q = quote_plus(f"{query} after:{after} before:{before}")
    return f"{settings.TARGETED_NEWS_URL}?q={q}&{settings.TARGETED_NEWS_LOCALE}"


async def archive_evidence(question: str, as_of: datetime, labels: Optional[list] = None) -> list:
    """News this app had already saved at `as_of` about the market, ranked as in the live app."""
    from sqlalchemy import func
    from backend.db.models import Article, ProcessedArticle, Source
    from backend.multi.service import _mentions
    since = as_of - timedelta(days=NEWS_LOOKBACK_DAYS)
    q_vec = await asyncio.to_thread(get_title_embedding, question)
    d_title = Article.title_embedding.cosine_distance(q_vec)
    distance = func.least(d_title, func.coalesce(Article.content_embedding.cosine_distance(q_vec), d_title))
    floor = max(0.0, settings.MARKET_MATCH_THRESHOLD - settings.MARKET_CANDIDATE_MARGIN)
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Article, ProcessedArticle, Source, distance.label("distance"))
            .join(Source, Source.id == Article.source_id)
            .outerjoin(ProcessedArticle, ProcessedArticle.article_id == Article.id)
            .where(Article.fetched_at <= as_of, Article.fetched_at >= since, Article.title_embedding.is_not(None),
                   distance <= 1.0 - floor)
            .order_by(distance).limit(settings.MARKET_MAX_ARTICLES * 6)
        )).all()
    terms = extract_terms(question)
    out = []
    for article, processed, source, dist in rows:
        similarity = round(1.0 - float(dist), 4)
        overlap = term_overlap(terms, f"{article.title}\n{(article.content_raw or '')[:3000]}")
        if labels and any(_mentions(label, article.title) for label in labels):
            overlap.entity_hit, overlap.score = True, min(1.0, overlap.score + 0.2)
        score = match_score(similarity, overlap)
        if score >= settings.MARKET_MATCH_THRESHOLD:
            # Story clusters may have grown after as_of: corroboration is not used here
            out.append((SimpleNamespace(match_score=score, similarity=similarity, relevance=None),
                        SimpleNamespace(title=article.title, url=article.url, content_raw=article.content_raw,
                                        publisher=article.publisher, published_at=article.published_at,
                                        fetched_at=article.fetched_at, cluster_id=article.cluster_id),
                        processed, source))
    return rank_evidence(out, settings.MARKET_MAX_ARTICLES, now=as_of)


async def past_evidence(question: str, as_of: datetime, mode: str, labels: Optional[list] = None) -> tuple[list, str]:
    """(evidence, source used) for the chosen news source."""
    if mode in ("auto", "archive"):
        evidence = await archive_evidence(question, as_of, labels)
        if evidence or mode == "archive":
            return evidence, "archive"
    return await historical_evidence(question, as_of, labels), "google"


async def historical_evidence(question: str, as_of: datetime, labels: Optional[list] = None) -> list:
    """News published before `as_of` about the market (Google News), ranked as in the live app.

    labels: outcome names of a multi-outcome event; naming one gives the same bonus as live.
    """
    from backend.multi.service import _mentions
    url = historical_search_url(question, as_of)
    if not url:
        return []
    result = await fetch_feed(url)
    since = as_of - timedelta(days=NEWS_LOOKBACK_DAYS)
    entries = [e for e in result.entries if e.get("published_at") and since <= e["published_at"] < as_of]
    if not entries:
        return []
    terms = extract_terms(question)
    q_vec = await asyncio.to_thread(get_title_embedding, question)
    rows = []
    for e in entries[:30]:
        title, publisher = e["title"], e.get("publisher")
        if " - " in title:
            head, tail = (p.strip() for p in title.rsplit(" - ", 1))
            if not publisher or tail.lower() == publisher.lower():
                title, publisher = head, publisher or tail
        vec = await asyncio.to_thread(get_title_embedding, title)
        similarity = round(cosine(q_vec, vec), 4)
        overlap = term_overlap(terms, title)
        if labels and any(_mentions(label, title) for label in labels):
            overlap.entity_hit, overlap.score = True, min(1.0, overlap.score + 0.2)
        score = match_score(similarity, overlap)
        if score < settings.MARKET_MATCH_THRESHOLD:
            continue
        publisher = publisher or "Google News"
        rows.append((
            SimpleNamespace(match_score=score, similarity=similarity, relevance=None),
            SimpleNamespace(title=title, url=e["url"], content_raw=None, publisher=publisher,
                            published_at=e["published_at"], fetched_at=e["published_at"], cluster_id=None),
            None,
            SimpleNamespace(id=publisher, name=publisher),
        ))
    return rank_evidence(rows, settings.MARKET_MAX_ARTICLES, now=as_of)


# ---------- Run ----------

async def _jev_with_retries(state, questions):
    for attempt in range(MAX_RATE_LIMIT_WAITS + 1):
        try:
            return await jev.system_one(state, questions)
        except RateLimited as e:
            if attempt == MAX_RATE_LIMIT_WAITS or isinstance(e, BudgetExceeded):
                raise
            try:
                await asyncio.wait_for(_cancel.wait(), timeout=max(1.0, e.retry_in))
                raise asyncio.CancelledError
            except asyncio.TimeoutError:
                pass


def _simulate_bet(signal, price: float, blended: float, model_p: float, evidence: float, weight: float,
                  days: float, liquidity: float, preset: str, category: Optional[str] = None) -> dict:
    """Economic evaluation with the price of that moment (no historical order book: estimated)."""
    from backend.betting.economics import model_sigma
    side = "NO" if signal.signal == "BUY_NO" else "YES"
    mid = price if side == "YES" else 1 - price
    quote = estimated_quote(mid, settings.DEFAULT_SPREAD, liquidity, category_rate(category) * 10_000, None)
    sigma = model_sigma(model_p, evidence, weight, settings.MODEL_PSEUDO_COUNT)
    ev = evaluate(signal=signal.signal, p_yes=blended, sigma=sigma, quote=quote, days=max(1.0, days),
                  profile=get_profile(preset), equity=settings.PAPER_BANKROLL, available_cash=settings.PAPER_BANKROLL,
                  exposure=Exposure(0, 0, 0, 0), liquidity=liquidity, risk_free_rate=settings.RISK_FREE_RATE)
    return {"verdict": ev.verdict, "side": ev.side, "outlay": round(ev.outlay, 2), "shares": ev.shares}


async def _update(run_id, **fields):
    async with SessionLocal() as db:
        run = await db.get(BacktestRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        await db.commit()


async def _run(run_id, params: Params) -> None:
    with feature("backtest"):
        await _run_tagged(run_id, params)


async def _run_tagged(run_id, params: Params) -> None:
    status, message = "done", None
    try:
        async with SessionLocal() as db:
            s = await db.get(BettingSettings, 1)
            preset = s.preset if s else settings.PAPER_PRESET
        plan = []
        if "binary" in params.kinds:
            markets = await polymarket.fetch_resolved_markets(params.resolved_after, params.resolved_before,
                                                              limit=params.max_markets, min_volume=params.min_volume)
            plan += [("binary", m, h, a) for m, h, a in plan_cases(markets, params.horizons)]
        if "multi" in params.kinds:
            events = await polymarket.fetch_resolved_events(params.resolved_after, params.resolved_before,
                                                            limit=params.max_markets, min_volume=params.min_volume)
            plan += [("multi", e, h, a) for e, h, a in plan_event_cases(events, params.horizons)]
        await _update(run_id, total=len(plan), message=None if plan else tr("Nessun mercato risolto con questi filtri", "No resolved market with these filters"))
        done = skipped = failed = calls = consecutive = 0
        histories: dict = {}
        for kind, item, horizon, as_of in plan:
            if _cancel.is_set():
                status, message = "stopped", "Interrotto"
                break
            if calls >= params.max_calls:
                status, message = "done", tr(f"Raggiunto il limite di {params.max_calls} chiamate a Jev", f"Reached the limit of {params.max_calls} Jev calls")
                break
            title = item.question if kind == "binary" else item.title
            case = BacktestCase(run_id=run_id, kind=kind, market_id=item.id, question=title, url=item.url,
                                category=categorize(title), horizon_days=horizon, as_of=as_of, end_date=item.end_date,
                                resolved_yes=item.resolved_yes if kind == "binary" else True, status="ok")
            try:
                evaluate_case = _eval_binary if kind == "binary" else _eval_multi
                if await evaluate_case(case, item, as_of, params, histories, preset):
                    calls += 1
                consecutive = 0
            except asyncio.CancelledError:
                status, message = "stopped", "Interrotto"
                break
            except RateLimited as e:
                status = "stopped"
                message = tr(f"Fermato: {e}", f"Stopped: {e}") if isinstance(e, BudgetExceeded) else tr("Fermato: Jev continua a rifiutare le richieste per limite di frequenza", "Stopped: Jev keeps refusing requests because of the rate limit")
                break
            except Exception as e:
                logger.warning(f"Backtest case {item.id} ({horizon} gg) failed: {e}")
                case.status, case.note = "error", f"{e.__class__.__name__}: {str(e)[:160]}"
                consecutive += 1
            async with SessionLocal() as db:
                db.add(case)
                await db.commit()
            done += case.status == "ok"
            skipped += case.status == "skipped"
            failed += case.status == "error"
            await _update(run_id, done=done, skipped=skipped, failed=failed)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                status, message = "failed", tr("Fermato dopo errori ripetuti: controlla la connessione a Polymarket, Google News e TypeSafe", "Stopped after repeated errors: check the connection to Polymarket, Google News and TypeSafe")
                break
    except Exception as e:
        logger.exception("Backtest failed")
        status, message = "failed", f"Errore: {e.__class__.__name__}: {str(e)[:200]}"
    await finalize(run_id, status, message)


def _news_json(evidence) -> list:
    out = []
    for e in evidence:
        published = e.article.published_at or e.article.fetched_at
        out.append({"title": e.article.title, "source": e.article.publisher or e.source.name,
                    "published_at": published.isoformat() if published else None})
    return out


async def _eval_binary(case, market, as_of, params, histories, preset) -> bool:
    """Fills the case; returns True if Jev was called."""
    if market.id not in histories:
        histories[market.id] = await polymarket.fetch_price_history(
            market.yes_token_id, as_of - timedelta(days=max(params.horizons) + 2), market.end_date)
    price = polymarket.price_at(histories[market.id], as_of)
    if price is None:
        case.status, case.note = "skipped", tr("Prezzo storico non disponibile", "Historical price not available")
        return False
    case.price = price
    if params.exclude_decided and not DECIDED_BELOW <= price <= DECIDED_ABOVE:
        case.status, case.note = "skipped", tr("Esito già scontato dal prezzo", "Outcome already priced in")
        return False
    evidence, source = await past_evidence(market.question, as_of, params.news_source)
    case.details = {"news_source": source}
    case.news_count = len(evidence)
    if not evidence:
        case.status, case.note = "skipped", tr("Nessuna notizia di quei giorni", "No news from those days")
        return False
    ns_market = SimpleNamespace(question=market.question, description=market.description, end_date=market.end_date)
    state, questions = build_jev_request(ns_market, evidence, now=as_of)
    response = await _jev_with_retries(state, questions)
    model_p, strength = parse_forecast(response)
    objective = objective_evidence(evidence, now=as_of)   # as live: the lower of Jev's rating and the facts
    if objective is not None:
        strength = min(strength, objective)
    signal = compute_signal(model_p, price, strength)
    bet = _simulate_bet(signal, price, signal.blended_probability, model_p, strength, signal.model_weight,
                        (market.end_date - as_of).total_seconds() / 86400, market.liquidity or market.volume * 0.02, preset,
                        case.category)
    won = (bet["side"] == "YES") == market.resolved_yes
    case.model_probability = round(model_p, 4)
    case.evidence_strength = round(strength, 4)
    case.blended_probability = signal.blended_probability
    case.edge, case.signal = signal.edge, signal.signal
    case.verdict, case.side = bet["verdict"], bet["side"]
    if bet["verdict"] != "NO" and bet["outlay"] > 0:
        case.outlay = bet["outlay"]
        case.pnl = round((bet["shares"] if won else 0.0) - bet["outlay"], 2)
    case.news = _news_json(evidence)
    return True


def plan_event_cases(events: list, horizons: list) -> list[tuple]:
    plan = []
    for e in events:
        closes = [o.closed_time for o in e.outcomes if o.closed_time]
        close = min([e.end_date] + closes) if closes else e.end_date
        start = min((o.start_date for o in e.outcomes if o.start_date), default=None)
        for h in horizons:
            as_of = close - timedelta(days=h)
            if start and as_of < start + timedelta(days=1):
                continue
            plan.append((e, h, as_of))
    return plan


def multi_brier(outcomes: list[dict], winner_id: str, key: str) -> float:
    """Multi-class Brier: sum over outcomes of (p - outcome)^2; 0 = perfect, 2 = certain and wrong."""
    return round(sum((o[key] - (1.0 if o["id"] == winner_id else 0.0)) ** 2 for o in outcomes), 5)


async def _eval_multi(case, event, as_of, params, histories, preset) -> bool:
    from backend.multi import service as multi

    # Outcomes are chosen by their price at as_of, like live: nothing here depends on who won.
    # Price histories are fetched for the most traded outcomes only (a cap on requests).
    tradable = sorted((o for o in event.outcomes if o.yes_token_id), key=lambda o: -(o.volume or 0))[:MULTI_MAX_PRICED]
    priced = []
    for o in tradable:
        if o.id not in histories:
            histories[o.id] = await polymarket.fetch_price_history(
                o.yes_token_id, as_of - timedelta(days=max(params.horizons) + 2), event.end_date)
        p = polymarket.price_at(histories[o.id], as_of)
        if p is not None:
            priced.append(SimpleNamespace(id=o.id, label=o.group_title, yes_price=p, closed=False))
    if len(priced) < 3:
        case.status, case.note = "skipped", tr("Prezzo storico non disponibile", "Historical price not available")
        return False
    priced.sort(key=lambda o: -o.yes_price)
    items = multi.market_distribution(priced, settings.MULTI_MAX_OUTCOMES)
    listed = {i["id"] for i in items if i["id"] != multi.OTHER_ID}
    has_other = any(i["id"] == multi.OTHER_ID for i in items)
    # The winner may be outside the outcomes shown to Jev: then "other outcomes" won
    winner = event.winner_id if event.winner_id in listed else multi.OTHER_ID
    if winner == multi.OTHER_ID and not has_other:
        case.status, case.note = "skipped", tr("Il vincitore non ha uno storico dei prezzi", "The winner has no price history")
        return False
    evidence, source = await past_evidence(event.title, as_of, params.news_source,
                                           labels=[i["label"] for i in items if i["id"] != multi.OTHER_ID])
    case.news_count = len(evidence)
    if not evidence:
        case.status, case.note = "skipped", tr("Nessuna notizia di quei giorni", "No news from those days")
        return False
    ns_event = SimpleNamespace(title=event.title, description=event.description, end_date=event.end_date)
    state, questions, keys = multi.build_request(ns_event, items, evidence, now=as_of)
    response = await _jev_with_retries(state, questions)
    for key, item in keys.items():
        item["key"] = key
    probs, strength = multi.parse_distribution(response)
    outcomes, w = multi.blend_distribution(items, probs, strength)
    signal, best_id, best_edge = multi.pick_signal(outcomes, strength)
    win = next(o for o in outcomes if o["id"] == winner)
    best = next((o for o in outcomes if o["id"] == best_id), None)
    case.price, case.model_probability, case.blended_probability = win["market"], win["model"], win["blended"]
    case.evidence_strength = round(strength, 4)
    case.signal, case.edge, case.side = signal, best_edge, "NO" if signal == "BUY_NO" else "YES"
    if signal in ("BUY_YES", "BUY_NO") and best:
        sig = SimpleNamespace(signal=signal, blended_probability=best["blended"], model_weight=w)
        bet = _simulate_bet(sig, best["price"], best["blended"], best["model"], strength, w,
                            (event.end_date - as_of).total_seconds() / 86400, event.liquidity or event.volume * 0.02, preset,
                            case.category)
        case.verdict = bet["verdict"]
        if bet["verdict"] != "NO" and bet["outlay"] > 0:
            case.outlay = bet["outlay"]
            won = (best_id == winner) == (signal == "BUY_YES")
            case.pnl = round((bet["shares"] if won else 0.0) - bet["outlay"], 2)
    top_market = max(outcomes, key=lambda o: o["market"])
    top_model = max(outcomes, key=lambda o: o["model"])
    case.details = {
        "news_source": source, "winner_listed": winner != multi.OTHER_ID,
        "winner": win["label"], "best": best["label"] if best else None, "n_outcomes": len(outcomes),
        "brier_market": multi_brier(outcomes, winner, "market"), "brier_model": multi_brier(outcomes, winner, "model"),
        "brier_blended": multi_brier(outcomes, winner, "blended"),
        "market_top_right": top_market["id"] == winner, "model_top_right": top_model["id"] == winner,
        "outcomes": outcomes,
    }
    case.news = _news_json(evidence)
    return True


async def finalize(run_id, status: str, message: Optional[str]) -> None:
    async with SessionLocal() as db:
        cases = (await db.execute(select(BacktestCase).where(BacktestCase.run_id == run_id))).scalars().all()
        run = await db.get(BacktestRun, run_id)
        run.summary = summarize([case_dict(c) for c in cases])
        run.status, run.message = status, message
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()


def case_dict(c: BacktestCase) -> dict:
    return {k: getattr(c, k) for k in (
        "id", "market_id", "question", "url", "category", "horizon_days", "as_of", "end_date", "resolved_yes", "status",
        "note", "price", "news_count", "model_probability", "evidence_strength", "blended_probability", "edge",
        "signal", "verdict", "side", "outlay", "pnl", "news", "kind", "details")}
