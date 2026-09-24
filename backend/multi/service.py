"""Multi-outcome events: sync, news linking and Jev forecast of the whole distribution.

An event like "Who will win the election?" is a set of mutually exclusive YES/NO markets,
one per outcome. Jev answers a single Choice question over the outcomes and returns a
probability for each; it is blended with the market prices (normalised to sum to 1) as
for YES/NO markets, and the edge is computed outcome by outcome.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai import jev
from backend.config import settings
from backend.db.models import (
    Article, Cluster, MultiArticleLink, MultiEvent, MultiOutcome, MultiPrediction, ProcessedArticle, Source,
)
from backend.ingestor.deduplicator import get_title_embedding
from backend.markets import polymarket
from backend.markets.forecast import model_weight
from backend.markets.matching import extract_terms, match_score, rank_evidence, term_overlap
from backend.markets.service import EVIDENCE_CRITERIA, _candidate_distance

logger = logging.getLogger(__name__)

OTHER_ID = "other"
OTHER_LABEL = "Altri esiti"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Sync ----------

async def sync_events(session: AsyncSession, client=None) -> dict:
    """Upserts the most traded open multi-outcome events and detects the winner of tracked ones."""
    fetched = await polymarket.fetch_multi_events(client=client)
    seen = set()
    created = 0
    for data in fetched:
        seen.add(data.id)
        event = await session.get(MultiEvent, data.id)
        if event is None:
            event = MultiEvent(id=data.id, title=data.title)
            session.add(event)
            created += 1
        title_changed = event.title_embedding is None or event.title != data.title
        _apply_event(event, data)
        if title_changed:
            event.title_embedding = await asyncio.to_thread(get_title_embedding, data.title)
        await _apply_outcomes(session, event, data)
    await session.commit()

    # Tracked events no longer listed: closed or resolved
    stale = (await session.execute(
        select(MultiEvent).where(MultiEvent.closed == False, MultiEvent.id.not_in(seen or {""}))  # noqa: E712
        .order_by(MultiEvent.updated_at).limit(30)
    )).scalars().all()
    resolved = 0
    for event in stale:
        try:
            data = await polymarket.fetch_event(event.id, client=client)
        except Exception as e:
            logger.warning(f"Could not refresh event {event.id}: {e}")
            continue
        if data is None:
            event.closed = True
            continue
        _apply_event(event, data)
        await _apply_outcomes(session, event, data)
        resolved += event.winner_id is not None
    await session.commit()
    return {"synced": len(fetched), "created": created, "resolved": resolved}


def _apply_event(event: MultiEvent, data) -> None:
    event.title, event.slug, event.description = data.title, data.slug, data.description
    event.end_date, event.volume, event.liquidity = data.end_date, data.volume, data.liquidity
    event.closed = data.closed or all(o.closed for o in data.outcomes)
    event.winner_id = data.winner_id
    event.updated_at = _now()


async def _apply_outcomes(session: AsyncSession, event: MultiEvent, data) -> None:
    from backend.db.models import Market
    for o in data.outcomes:
        row = await session.get(MultiOutcome, o.id)
        if row is None:
            row = MultiOutcome(id=o.id, event_id=event.id, label=o.group_title)
            session.add(row)
        row.label, row.question, row.yes_price = o.group_title, o.question, o.yes_price
        row.volume, row.closed, row.resolved_yes, row.yes_token_id = o.volume, o.closed, o.resolved_yes, o.yes_token_id
        row.updated_at = _now()
        # Each outcome is also a row of the markets table, flagged with multi_event_id (hidden from the
        # YES/NO lists): order book, economic evaluation, simulated bets and settlement work unchanged,
        # and bets on several outcomes of one event share the per-event exposure cap (event_slug).
        m = await session.get(Market, o.id)
        if m is None:
            m = Market(id=o.id, question=o.question)
            session.add(m)
        m.question, m.slug, m.event_slug, m.description = o.question, o.slug, event.slug, event.description
        m.end_date = o.end_date or event.end_date
        m.yes_price, m.volume, m.liquidity = o.yes_price, o.volume, o.liquidity or 0.0
        m.active, m.closed, m.resolved_yes, m.resolution = o.active, o.closed, o.resolved_yes, o.resolution
        for f in ("yes_token_id", "no_token_id", "best_bid", "best_ask", "taker_fee_bps", "order_min_size"):
            if getattr(o, f) is not None:
                setattr(m, f, getattr(o, f))
        m.multi_event_id = event.id
        m.updated_at = _now()


# ---------- Links ----------

async def outcomes_of(session: AsyncSession, event_id: str) -> list[MultiOutcome]:
    return list((await session.execute(
        select(MultiOutcome).where(MultiOutcome.event_id == event_id).order_by(MultiOutcome.yes_price.desc().nulls_last())
    )).scalars().all())


def _mentions(label: str, text: str) -> bool:
    return any(t.entity and t.matches(text) for t in extract_terms(label)) or label.lower() in text.lower()


async def refresh_links(session: AsyncSession, event_ids: Optional[list[str]] = None) -> int:
    """Links events to recent news: similarity to the title + key terms, and a bonus when a
    news item names one of the outcomes (a candidate, a team...)."""
    since = _now() - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    floor = max(0.0, settings.MARKET_MATCH_THRESHOLD - settings.MARKET_CANDIDATE_MARGIN)
    stmt = select(MultiEvent).where(MultiEvent.closed == False, MultiEvent.title_embedding.is_not(None))  # noqa: E712
    if event_ids is not None:
        stmt = stmt.where(MultiEvent.id.in_(event_ids))
    changed = 0
    for event in (await session.execute(stmt)).scalars().all():
        labels = [o.label for o in (await outcomes_of(session, event.id))[:settings.MULTI_MAX_OUTCOMES]]
        distance = _candidate_distance(event.title_embedding)
        rows = (await session.execute(
            select(Article.id, Article.title, Article.content_raw, distance.label("distance"))
            .where(Article.fetched_at >= since, Article.title_embedding.is_not(None), distance <= 1.0 - floor)
            .order_by(distance).limit(settings.MARKET_MAX_ARTICLES * 6)
        )).all()
        terms = extract_terms(event.title)
        values = []
        for article_id, title, content, dist in rows:
            text = f"{title}\n{(content or '')[:3000]}"
            overlap = term_overlap(terms, text)
            named = [l for l in labels if _mentions(l, text)]
            if named:
                overlap.entity_hit = True
                overlap.matched = overlap.matched + named[:3]
                overlap.score = min(1.0, overlap.score + 0.2)
            similarity = round(1.0 - float(dist), 4)
            score = match_score(similarity, overlap)
            if score >= settings.MARKET_MATCH_THRESHOLD:
                values.append({"event_id": event.id, "article_id": article_id, "similarity": similarity,
                               "match_score": score, "matched_terms": overlap.matched})
        if not values:
            continue
        insert = pg_insert(MultiArticleLink).values(values)
        result = await session.execute(insert.on_conflict_do_update(
            constraint="uq_multi_article",
            set_={"similarity": insert.excluded.similarity, "match_score": insert.excluded.match_score,
                  "matched_terms": insert.excluded.matched_terms},
            where=MultiArticleLink.match_score.is_distinct_from(insert.excluded.match_score),
        ))
        changed += result.rowcount or 0
    await session.commit()
    return changed


async def get_evidence(session: AsyncSession, event_id: str, limit: Optional[int] = None) -> list:
    since = _now() - timedelta(hours=settings.MARKET_NEWS_WINDOW_HOURS)
    rows = (await session.execute(
        select(MultiArticleLink, Article, ProcessedArticle, Source)
        .join(Article, Article.id == MultiArticleLink.article_id)
        .join(Source, Source.id == Article.source_id)
        .outerjoin(ProcessedArticle, ProcessedArticle.article_id == Article.id)
        .where(MultiArticleLink.event_id == event_id, Article.fetched_at >= since,
               MultiArticleLink.match_score >= settings.MARKET_MATCH_THRESHOLD)
        .order_by(MultiArticleLink.match_score.desc()).limit(300)
    )).all()
    cluster_ids = {a.cluster_id for _l, a, _p, _s in rows if a.cluster_id}
    clusters = dict((await session.execute(
        select(Cluster.id, Cluster.source_count).where(Cluster.id.in_(cluster_ids))
    )).all()) if cluster_ids else {}
    return rank_evidence(rows, limit or settings.MARKET_MAX_ARTICLES, clusters)


# ---------- Forecast ----------

def market_distribution(outcomes: list[MultiOutcome], max_outcomes: int) -> list[dict]:
    """Top outcomes by price plus an "other" bucket, prices normalised to sum to 1 (removes the vig)."""
    priced = [o for o in outcomes if o.yes_price is not None and not o.closed]
    top, rest = priced[:max_outcomes], priced[max_outcomes:]
    items = [{"id": o.id, "label": o.label, "price": o.yes_price} for o in top]
    other = sum(o.yes_price for o in rest)
    if rest:
        # Not Polymarket's own "Other" outcome (if any): the sum of the outcomes not listed to Jev
        items.append({"id": OTHER_ID, "label": f"{OTHER_LABEL} ({len(rest)})", "price": other})
    total = sum(i["price"] for i in items) or 1.0
    for i in items:
        i["market"] = round(i["price"] / total, 4)
    return items


def build_request(event: MultiEvent, items: list[dict], evidence: list, now: Optional[datetime] = None):
    from typesafe_sdk import Choice, Noul, Score
    from backend.markets.service import build_jev_request

    now = now or _now()
    ns = type("E", (), {"question": event.title, "description": event.description, "end_date": event.end_date})
    state, _ = build_jev_request(ns, evidence, now=now)
    keys = {f"o{i}": item for i, item in enumerate(items)}
    state["market"]["outcomes"] = [{"id": k, "name": v["label"]} for k, v in keys.items()]
    state["market"]["note"] = "Exactly one outcome resolves YES; the others resolve NO."
    questions = {
        "winner": Choice(
            instructions=("Considering the event's resolution rules, its end date and the days left, today's date, "
                          "the news items (weighted as described in how_to_weigh_news), base rates and general world "
                          "knowledge: which outcome will resolve YES?"),
            criteria={k: v["label"] for k, v in keys.items()},
        ),
        "evidence_strength": Score(
            instructions="How strongly do the provided news items inform which outcome will win this event?",
            criteria=EVIDENCE_CRITERIA,
        ),
    }
    for n in state["news"]:
        questions[f"relevant_{n['id']}"] = Noul(instructions=f"Is news item {n['id']} directly relevant to which outcome wins this event?")
        questions[f"favours_{n['id']}"] = Choice(
            instructions=f"Which outcome does news item {n['id']} make more likely, if any?",
            criteria={**{k: v["label"] for k, v in keys.items()}, "none": "No outcome in particular"},
        )
    return state, questions, keys


def blend_distribution(items: list[dict], model_probs: dict, evidence_strength: float) -> tuple[list[dict], float]:
    """Blended probability and edge per outcome; model probabilities renormalised over the listed outcomes."""
    total = sum(max(0.0, model_probs.get(i["key"], 0.0)) for i in items) or 1.0
    w = model_weight(evidence_strength)
    out = []
    for i in items:
        model = max(0.0, model_probs.get(i["key"], 0.0)) / total
        blended = w * model + (1 - w) * i["market"]
        out.append({"id": i["id"], "label": i["label"], "price": i["price"], "market": i["market"],
                    "model": round(model, 4), "blended": round(blended, 4), "edge": round(blended - i["market"], 4)})
    return out, w


def pick_signal(outcomes: list[dict], evidence_strength: float) -> tuple[str, Optional[str], Optional[float]]:
    """BUY_YES on the most underpriced listed outcome, if the edge and the evidence are strong enough."""
    candidates = [o for o in outcomes if o["id"] != OTHER_ID]
    if not candidates:
        return "HOLD", None, None
    best = max(candidates, key=lambda o: o["edge"])
    if best["edge"] >= settings.MIN_EDGE and evidence_strength >= settings.MIN_EVIDENCE:
        return "BUY_YES", best["id"], best["edge"]
    return "HOLD", best["id"], best["edge"]


async def predict_event(session: AsyncSession, event: MultiEvent, max_wait: Optional[float] = None) -> MultiPrediction:
    if not jev.is_enabled():
        raise jev.JevUnavailableError("TYPESAFE_API_KEY is not configured")
    items = market_distribution(await outcomes_of(session, event.id), settings.MULTI_MAX_OUTCOMES)
    if len(items) < 2:
        raise ValueError("L'evento non ha abbastanza esiti con un prezzo")
    evidence = await get_evidence(session, event.id)
    if not evidence:
        raise LookupError("Nessuna notizia recente collegata a questo evento")
    state, questions, keys = build_request(event, items, evidence)
    response = await jev.system_one(state, questions, max_wait=max_wait)

    winner = response.choices["winner"]
    for key, item in keys.items():
        item["key"] = key
    strength = float(response.scores["evidence_strength"].score) / (len(EVIDENCE_CRITERIA) - 1)
    outcomes, w = blend_distribution(items, dict(winner.probabilities), strength)
    signal, best_id, best_edge = pick_signal(outcomes, strength)

    for i, item in enumerate(evidence):
        link = item.link
        relevant = response.nouls.get(f"relevant_n{i}")
        favours = response.choices.get(f"favours_n{i}")
        if relevant is not None:
            link.relevance = round(float(relevant.noul), 4)
        if favours is not None:
            link.impact = keys[favours.choice]["label"] if favours.choice in keys else None
            link.impact_confidence = round(float(favours.confidence), 4)

    prediction = MultiPrediction(
        event_id=event.id, model_name=response.model, evidence_strength=round(strength, 4), model_weight=round(w, 4),
        outcomes=outcomes, best_outcome_id=best_id, best_edge=best_edge, signal=signal, article_count=len(evidence),
    )
    session.add(prediction)
    await session.commit()
    logger.info(f"Jev forecast for event {event.id}: {signal} {best_id} (edge {best_edge})")
    await apply_economics(session, event, prediction)
    return prediction


# ---------- Economics and simulated bets ----------

def outcome_prediction(prediction: MultiPrediction, outcome_id: str):
    """A YES/NO-style view of one outcome of a multi-outcome forecast, for the economic evaluation."""
    from types import SimpleNamespace
    entry = next((o for o in prediction.outcomes if o["id"] == outcome_id), None)
    if entry is None:
        return None
    strong = entry["edge"] >= settings.MIN_EDGE and prediction.evidence_strength >= settings.MIN_EVIDENCE
    return SimpleNamespace(
        id=None, market_id=outcome_id, created_at=prediction.created_at, signal="BUY_YES" if strong else "HOLD",
        market_probability=entry.get("price", entry["market"]), model_probability=entry["model"],
        blended_probability=entry["blended"], evidence_strength=prediction.evidence_strength,
        model_weight=prediction.model_weight, edge=entry["edge"], multi_prediction_id=prediction.id,
    )


async def event_category(session: AsyncSession, event_id: str) -> Optional[str]:
    """Dominant category of the news linked to the event (for the per-category cap and exclusions)."""
    from collections import Counter
    rows = (await session.execute(
        select(ProcessedArticle.category).join(MultiArticleLink, MultiArticleLink.article_id == ProcessedArticle.article_id)
        .where(MultiArticleLink.event_id == event_id, ProcessedArticle.category.is_not(None))
    )).scalars().all()
    return Counter(rows).most_common(1)[0][0] if rows else None


MAX_EVALUATED_OUTCOMES = 3


async def apply_economics(session: AsyncSession, event: MultiEvent, prediction: MultiPrediction) -> dict:
    """Evaluates the most underpriced outcomes (up to 3) and places the simulated bet on the best one."""
    from backend.betting import portfolio
    from backend.db.models import Market
    results = {}
    try:
        category = await event_category(session, event.id)
        candidates = sorted((o for o in prediction.outcomes if o["id"] != OTHER_ID and o["edge"] > 0),
                            key=lambda o: o["edge"], reverse=True)[:MAX_EVALUATED_OUTCOMES]
        for entry in candidates:
            market = await session.get(Market, entry["id"])
            if market is None or market.closed:
                continue
            if category:
                market.category = category
            pred = outcome_prediction(prediction, entry["id"])
            ev = await portfolio.evaluate_prediction(session, market, pred)
            results[entry["id"]] = ev.as_dict()
            if entry["id"] == prediction.best_outcome_id and prediction.signal == "BUY_YES":
                await portfolio.maybe_place_bet(session, market, pred, ev)
        prediction.economics = results
        await session.commit()
    except Exception as e:
        logger.error(f"Economic evaluation failed for event {event.id}: {e}")
        await session.rollback()
    return results


async def run_pipeline(session: AsyncSession) -> dict:
    stats = await sync_events(session)
    stats["links"] = await refresh_links(session)
    return stats
