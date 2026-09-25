"""News <-> market matching and evidence ranking.

Semantic similarity alone links many articles that share the topic but not the subject
(an ECB rate decision for a Fed market, a different team's match for a sports market).
Here it is combined with the overlap of the market's key terms: names, organisations,
tickers, numbers and distinctive words, expanded with common aliases.

The linked articles are then ranked for Jev by match, source quality, recency and Jev's
own past relevance judgements, keeping one article per story (with the number of sources
that reported it) so the evidence is varied.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from backend.config import settings

# Words that carry no information about the subject of a market question
STOPWORDS = set("""
a about above after again against all also an and any are as at be been before being below between both but by
can could did do does doing down during each end ending ends few for from further had has have having he her here
hers him his how i if in into is it its itself just least less more most no nor not now of off on once only or
other our out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would you your
yes before, by, december january february march april may june july august september october november
next last week month year day days today tomorrow new first second third one two three four five
win wins won reach reaches above below hit hits happen happens announce announced announces market markets
resolve resolves resolved resolution price least most higher lower record official officially get gets say says
""".replace(",", " ").split())

# Capitalised words that start questions or are too generic to identify a subject
GENERIC_CAPS = {"Will", "Who", "What", "Which", "When", "How", "Does", "Do", "Is", "Are", "Can", "The", "A", "An",
                "In", "On", "By", "Before", "After", "Yes", "No", "End", "Q1", "Q2", "Q3", "Q4"}

MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"}

# term -> equivalent spellings that count as a match (all lowercase)
ALIASES = {
    "fed": ["federal reserve", "fomc", "powell"],
    "federal reserve": ["fed", "fomc"],
    "ecb": ["european central bank", "lagarde"],
    "boe": ["bank of england"],
    "boj": ["bank of japan"],
    "btc": ["bitcoin"],
    "bitcoin": ["btc"],
    "eth": ["ethereum", "ether"],
    "ethereum": ["eth", "ether"],
    "sol": ["solana"],
    "solana": ["sol"],
    "xrp": ["ripple"],
    "us": ["u.s.", "united states", "america", "american"],
    "usa": ["u.s.", "united states", "america"],
    "united states": ["u.s.", "us", "america"],
    "uk": ["u.k.", "britain", "british", "united kingdom"],
    "eu": ["european union", "brussels"],
    "gop": ["republican", "republicans"],
    "republican": ["gop", "republicans"],
    "republicans": ["gop", "republican"],
    "democrat": ["democrats", "democratic"],
    "democrats": ["democrat", "democratic"],
    "democratic": ["democrat", "democrats"],
    "russia": ["russian", "moscow", "kremlin", "putin"],
    "ukraine": ["ukrainian", "kyiv", "zelensky", "zelenskyy"],
    "israel": ["israeli", "netanyahu", "idf"],
    "gaza": ["hamas"],
    "hamas": ["gaza"],
    "iran": ["iranian", "tehran"],
    "china": ["chinese", "beijing"],
    "taiwan": ["taiwanese", "taipei"],
    "india": ["indian", "delhi"],
    "sec": ["securities and exchange commission"],
    "scotus": ["supreme court"],
    "supreme court": ["scotus"],
    "nvidia": ["nvda"],
    "nvda": ["nvidia"],
    "tesla": ["tsla"],
    "apple": ["aapl"],
    "microsoft": ["msft"],
    "openai": ["chatgpt", "gpt"],
    "gpt": ["openai", "chatgpt"],
    "google": ["alphabet", "gemini"],
    "rate": ["rates"],
    "rates": ["rate"],
    "cut": ["cuts", "lower", "lowers", "easing"],
    "cuts": ["cut", "lower", "easing"],
    "hike": ["hikes", "raise", "raises"],
    "ceasefire": ["cease-fire", "truce"],
    "shutdown": ["shut down"],
    "election": ["elections", "vote", "polls"],
    "president": ["presidency", "presidential"],
    "presidential": ["president", "presidency"],
    "nominee": ["nomination"],
    "recession": ["contraction"],
    "inflation": ["cpi"],
    "cpi": ["inflation", "consumer price"],
    "unemployment": ["jobless", "payrolls"],
}

TOKEN_RE = re.compile(r"\$?\d(?:[\d,.]*\d)?(?:[kKmMbB%](?![A-Za-z]))?|[A-Za-z][A-Za-z0-9.&'-]*")


@dataclass(frozen=True)
class Term:
    text: str              # canonical, lowercase
    weight: float
    entity: bool           # proper name / ticker / organisation
    variants: tuple = ()   # spellings that count as a match, case-insensitive
    exact: tuple = ()      # spellings matched case-sensitively (short acronyms: "US" is not "us")

    def matches(self, text: str) -> bool:
        return any(_find(v, text, ignore_case=True) for v in self.variants) or \
            any(_find(v, text, ignore_case=False) for v in self.exact)


def _find(needle: str, text: str, ignore_case: bool) -> bool:
    flags = re.IGNORECASE if ignore_case else 0
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", text, flags) is not None


@dataclass
class TermMatch:
    score: float                       # weighted share of the key terms found, 0-1
    matched: list[str] = field(default_factory=list)
    has_entities: bool = False
    entity_hit: bool = False


def _number_variants(raw: str) -> tuple:
    token = raw.lower().replace("$", "").replace(" ", "")
    plain = token.replace(",", "")
    variants = {token, plain}
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb%]?)", plain)
    if m:
        value, unit = float(m.group(1)), m.group(2)
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(unit)
        if mult:
            full = int(value * mult)
            variants |= {str(full), f"{full:,}"}
        elif unit == "" and value >= 1000 and value.is_integer():
            full = int(value)
            variants.add(f"{full:,}")
            if full % 1000 == 0:
                variants.add(f"{full // 1000}k")
    return tuple(v for v in variants if v)


def extract_terms(question: str) -> list[Term]:
    """Key terms of a market question, weighted: names 2, numbers 1 (years 0.5), other words 1."""
    terms: dict[str, Term] = {}

    def add(text: str, weight: float, entity: bool, variants: Iterable[str] = ()):
        key = text.lower()
        if key in terms and terms[key].weight >= weight:
            return
        allv = {*(v.lower() for v in variants), *ALIASES.get(key, [])}
        exact = ()
        if entity and text.isupper() and len(text) <= 3:
            exact = (text, ".".join(text) + ".")   # "US", "U.S."
        else:
            allv.add(key)
        terms[key] = Term(key, weight, entity, tuple(sorted(allv)), exact)

    # Multi-word proper names ("Federal Reserve", "Donald Trump", "Supreme Court")
    for m in re.finditer(r"\b([A-Z][a-zA-Z.&'-]+(?:\s+(?:of\s+)?[A-Z][a-zA-Z.&'-]+)+)", question):
        words = [w for w in m.group(1).split() if w not in GENERIC_CAPS]
        if len(words) >= 2:
            last = words[-1].lower()
            # "Trump" alone counts for "Donald Trump"
            add(" ".join(words), 2.0, True, [last] if len(last) >= 4 and last not in STOPWORDS else [])
    for i, token in enumerate(TOKEN_RE.findall(question)):
        clean = token.strip(".,'-")
        low = clean.lower()
        if not clean:
            continue
        if clean[0] in "$0123456789":
            plain = low.replace("$", "").replace(",", "")
            # Years and days of the month say little about the subject
            weak = re.fullmatch(r"(19|20)\d\d|\d{1,2}", plain) is not None
            add(low, 0.5 if weak else 1.0, False, _number_variants(clean))
        elif clean.isupper() and len(clean) >= 2 and clean not in GENERIC_CAPS:
            add(clean, 2.0, True)                             # tickers and acronyms: BTC, ECB, NBA
        elif clean[0].isupper() and clean not in GENERIC_CAPS and low not in STOPWORDS and low not in MONTHS:
            add(low, 2.0, True)                               # names
        elif low in MONTHS:
            add(low, 0.5, False)
        elif len(low) >= 3 and low not in STOPWORDS:
            add(low, 1.0, False)
    # A name inside a longer name ("reserve" in "federal reserve") is not counted twice
    names = [t for t in terms if " " in t]
    return [t for k, t in terms.items() if not any(k != n and k in n.split() for n in names)]


def term_overlap(terms: list[Term], text: str) -> TermMatch:
    if not terms:
        return TermMatch(0.0)
    text = text or ""
    total = sum(t.weight for t in terms)
    matched = [t for t in terms if t.matches(text)]
    return TermMatch(
        score=round(sum(t.weight for t in matched) / total, 4) if total else 0.0,
        matched=[t.text for t in matched],
        has_entities=any(t.entity for t in terms),
        entity_hit=any(t.entity for t in matched),
    )


def match_score(similarity: float, overlap: TermMatch) -> float:
    """Blend of semantic similarity and key-term overlap, 0-1.

    Articles that mention none of the names in the question (same topic, different subject)
    are penalised.
    """
    w = settings.MARKET_MATCH_TERM_WEIGHT
    score = (1 - w) * similarity + w * overlap.score
    if overlap.has_entities and not overlap.entity_hit:
        score *= settings.MARKET_MATCH_NO_ENTITY_PENALTY
    return round(max(0.0, min(1.0, score)), 4)


# ---------- Evidence ranking ----------

OUTLET_PRIOR_WEIGHT = 0.5   # share of the outlet's track record in an article's authority
OUTLET_MIN_ARTICLES = 5     # classified articles an outlet needs before its average counts


def outlet_key(article, source) -> str:
    """The outlet that wrote the article: the publisher for aggregated results, otherwise the feed."""
    return (getattr(article, "publisher", None) or source.name or "").strip().lower()


def source_quality(processed, prior: Optional[float] = None) -> float:
    """0.25-1: authority, penalised for clickbait and opinion pieces.

    prior: the outlet's average authority over its classified articles. One article's score is
    noisy; the outlet's record steadies it (half and half). Not classified yet: the prior alone,
    or 0.75 when there is none.
    """
    if processed is None:
        return max(0.25, min(1.0, 0.5 + 0.5 * prior)) if prior is not None else 0.75
    authority = processed.authority_score if processed.authority_score is not None else prior if prior is not None else 0.5
    if prior is not None and processed.authority_score is not None:
        authority = (1 - OUTLET_PRIOR_WEIGHT) * authority + OUTLET_PRIOR_WEIGHT * prior
    clickbait = processed.clickbait_score or 0.0
    q = (0.5 + 0.5 * authority) * (1 - 0.5 * clickbait)
    if (processed.is_opinion or 0.0) >= 0.6:
        q *= 0.7
    return max(0.25, min(1.0, q))


def recency_factor(published: Optional[datetime], now: Optional[datetime] = None) -> float:
    """1 for fresh news, halving every EVIDENCE_HALF_LIFE_HOURS, never below 0.25."""
    if published is None:
        return 0.6
    now = now or datetime.now(timezone.utc)
    age_h = max(0.0, (now - published).total_seconds() / 3600)
    return 0.25 + 0.75 * math.pow(0.5, age_h / max(1.0, settings.EVIDENCE_HALF_LIFE_HOURS))


def jev_relevance_factor(relevance: Optional[float]) -> float:
    """Jev already judged this article for this market: trust its verdict (0.4-1).

    Not judged yet: 0.8, as for an article Jev would call relevant two times out of three.
    """
    return 0.8 if relevance is None else 0.4 + 0.6 * relevance


@dataclass
class EvidenceItem:
    link: object
    article: object
    processed: object
    source: object
    score: float = 0.0
    corroboration: int = 1       # distinct sources that reported the same story
    quality: float = 0.75

    def __iter__(self):          # unpacks as the (link, article, processed, source) rows used elsewhere
        return iter((self.link, self.article, self.processed, self.source))


def rank_evidence(rows, limit: int, cluster_sources: Optional[dict] = None, now: Optional[datetime] = None,
                  max_per_source: int = 3, outlet_priors: Optional[dict] = None) -> list[EvidenceItem]:
    """Best evidence first; one article per story and at most `max_per_source` per source."""
    cluster_sources = cluster_sources or {}
    outlet_priors = outlet_priors or {}
    items = []
    for link, article, processed, source in rows:
        if link.relevance is not None and link.relevance < settings.EVIDENCE_MIN_JEV_RELEVANCE:
            continue  # Jev said it is not about this market
        quality = source_quality(processed, outlet_priors.get(outlet_key(article, source)))
        base = link.match_score if link.match_score is not None else link.similarity
        score = base * quality * recency_factor(article.published_at or article.fetched_at, now) \
            * jev_relevance_factor(link.relevance)
        items.append(EvidenceItem(link, article, processed, source, round(score, 4),
                                  max(1, cluster_sources.get(article.cluster_id, 1)) if article.cluster_id else 1,
                                  round(quality, 3)))
    items.sort(key=lambda e: e.score, reverse=True)
    chosen, seen_clusters, per_source = [], set(), {}
    for item in items:
        cid = item.article.cluster_id
        if cid is not None and cid in seen_clusters:
            continue
        sid = item.source.id
        if per_source.get(sid, 0) >= max_per_source:
            continue
        chosen.append(item)
        per_source[sid] = per_source.get(sid, 0) + 1
        if cid is not None:
            seen_clusters.add(cid)
        if len(chosen) >= limit:
            break
    return chosen


async def outlet_priors(session, rows) -> dict:
    """Average authority of each outlet appearing in `rows`, over its classified articles."""
    from sqlalchemy import func, select
    from backend.db.models import Article, ProcessedArticle, Source
    keys = {outlet_key(article, source) for _l, article, _p, source in rows}
    keys.discard("")
    if not keys:
        return {}
    key = func.lower(func.coalesce(Article.publisher, Source.name))
    result = await session.execute(
        select(key, func.avg(ProcessedArticle.authority_score))
        .join(Article, Article.id == ProcessedArticle.article_id).join(Source, Source.id == Article.source_id)
        .where(ProcessedArticle.authority_score.is_not(None), key.in_(keys))
        .group_by(key).having(func.count(ProcessedArticle.id) >= OUTLET_MIN_ARTICLES)
    )
    return {k: float(v) for k, v in result.all()}


# ---------- Objective evidence strength ----------

# Official sources: a statement from the body that decides or measures the outcome
PRIMARY_DOMAINS = (
    "federalreserve.gov", "bls.gov", "bea.gov", "sec.gov", "treasury.gov", "whitehouse.gov", "congress.gov",
    "supremecourt.gov", "fec.gov", "cdc.gov", "ecb.europa.eu", "europa.eu", "bankofengland.co.uk", "un.org",
    "who.int", "nasa.gov", "noaa.gov", "openai.com", "blog.google",
)


def is_primary_source(article, source) -> bool:
    from urllib.parse import urlparse
    for url in (getattr(article, "url", None), getattr(source, "url", None)):
        host = (urlparse(url).hostname or "").lower() if url else ""
        if any(host == d or host.endswith("." + d) for d in PRIMARY_DOMAINS):
            return True
    return False


def evidence_weight(item: EvidenceItem, now: Optional[datetime] = None) -> float:
    """What one news item is worth as evidence: source reliability × freshness × Jev's relevance,
    more if several outlets confirm it (up to 3) and if it comes from a primary source."""
    link, article, _processed, source = item
    w = item.quality * recency_factor(article.published_at or article.fetched_at, now) * jev_relevance_factor(link.relevance)
    w *= 1 + 0.5 * (min(item.corroboration, 3) - 1)
    if is_primary_source(article, source):
        w *= 1.5
    return w


def objective_evidence(items: list, now: Optional[datetime] = None, half: Optional[float] = None) -> Optional[float]:
    """Evidence strength from verifiable facts, 0-1: total weight / (total weight + half).
    With half = 1.5: one fresh, relevant item from a reliable outlet gives about 0.25, three
    about 0.5, a confirmed statement from a primary source about 0.6; many strong items approach
    1. None when turned off (half = 0)."""
    half = settings.EVIDENCE_OBJECTIVE_HALF if half is None else half
    if half <= 0:
        return None
    total = sum(evidence_weight(i, now) for i in items)
    return total / (total + half)
