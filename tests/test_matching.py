"""News <-> market matching, evidence ranking and targeted news search."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import Article, Market, MarketArticleLink, Source
from backend.ingestor.fetcher import FeedResult, parse_feed
from backend.markets import service, targeted
from backend.markets.matching import extract_terms, match_score, rank_evidence, term_overlap
from tests.conftest import login_client
from tests.test_markets_e2e import FED_Q, entry, gamma_client, ingest_articles
from tests.test_polymarket import gamma_market

NOW = datetime.now(timezone.utc)


# ---------- Key terms ----------

def terms_of(question):
    return {t.text: t for t in extract_terms(question)}


def test_extract_terms_weights_names_numbers_and_words():
    t = terms_of("Will the Fed cut interest rates in December 2026?")
    assert t["fed"].entity and t["fed"].weight == 2
    assert {"cut", "interest", "rates"} <= set(t)
    assert t["2026"].weight == 0.5 and t["december"].weight == 0.5
    assert "will" not in t and "the" not in t


def test_multiword_names_and_surnames():
    t = terms_of("Will Donald Trump win the 2028 presidential election?")
    assert "donald trump" in t and "donald" not in t
    assert term_overlap(list(t.values()), "Trump leads in new poll").entity_hit


def test_numbers_match_in_any_notation():
    terms = extract_terms("Will Bitcoin reach $100,000 by December 31, 2026?")
    assert term_overlap(terms, "BTC tops 100k for the first time").matched == ["bitcoin", "$100,000"]
    assert "$100,000" in term_overlap(terms, "Bitcoin at 100000 dollars").matched


def test_short_acronyms_are_case_sensitive():
    terms = extract_terms("US government shutdown before January 31, 2027?")
    assert not term_overlap(terms, "Senators tell us a government shutdown is unlikely").entity_hit
    assert term_overlap(terms, "U.S. government shutdown looms").entity_hit
    assert term_overlap(terms, "United States braces for shutdown").entity_hit


def test_aliases():
    terms = extract_terms("Will the Fed cut interest rates in December 2026?")
    assert term_overlap(terms, "Powell signals the Federal Reserve will lower rates").entity_hit


def test_same_topic_different_subject_is_penalised():
    terms = extract_terms("Will the Fed cut interest rates in December 2026?")
    fed = match_score(0.6, term_overlap(terms, "Fed officials signal December rate cut"))
    ecb = match_score(0.6, term_overlap(terms, "ECB cuts interest rates in December"))
    assert fed >= settings.MARKET_MATCH_THRESHOLD > ecb


def test_key_terms_rescue_a_vague_headline():
    terms = extract_terms("Will the Fed cut interest rates in December 2026?")
    assert match_score(0.45, term_overlap(terms, "Fed: officials weigh December cut to interest rates")) >= settings.MARKET_MATCH_THRESHOLD
    assert match_score(0.45, term_overlap(terms, "Markets rally on Friday")) < settings.MARKET_MATCH_THRESHOLD


# ---------- Evidence ranking ----------

def row(score, hours_ago=1, authority=0.8, clickbait=0.0, opinion=0.0, relevance=None, cluster=None, source="s1"):
    link = SimpleNamespace(match_score=score, similarity=score, relevance=relevance)
    article = SimpleNamespace(published_at=NOW - timedelta(hours=hours_ago), fetched_at=NOW, cluster_id=cluster)
    processed = SimpleNamespace(authority_score=authority, clickbait_score=clickbait, is_opinion=opinion)
    return link, article, processed, SimpleNamespace(id=source, name=source)


def test_ranking_prefers_fresh_reliable_reports():
    fresh = row(0.6, hours_ago=2)
    old = row(0.6, hours_ago=120, source="s2")
    opinion = row(0.6, opinion=0.9, source="s3")
    clickbait = row(0.6, clickbait=0.9, authority=0.2, source="s4")
    ranked = rank_evidence([old, opinion, clickbait, fresh], limit=4, now=NOW)
    assert ranked[0].link is fresh[0]
    assert ranked[-1].link in (old[0], clickbait[0])


def test_ranking_keeps_one_article_per_story_and_counts_sources():
    story = uuid.uuid4()
    a = row(0.7, cluster=story, source="s1")
    b = row(0.6, cluster=story, source="s2")
    other = row(0.55, source="s3")
    ranked = rank_evidence([a, b, other], limit=5, cluster_sources={story: 3}, now=NOW)
    assert [e.link for e in ranked] == [a[0], other[0]]
    assert ranked[0].corroboration == 3 and ranked[1].corroboration == 1


def test_ranking_drops_what_jev_judged_irrelevant_and_caps_per_source():
    irrelevant = row(0.9, relevance=0.1)
    same_source = [row(0.6 - i / 100, source="s9") for i in range(5)]
    ranked = rank_evidence([irrelevant, *same_source], limit=10, now=NOW, max_per_source=3)
    assert irrelevant[0] not in [e.link for e in ranked]
    assert len(ranked) == 3


def test_ranked_items_unpack_as_rows():
    link, article, processed, source = rank_evidence([row(0.6)], limit=1, now=NOW)[0]
    assert link.match_score == 0.6 and source.name == "s1"


# ---------- Linking on the database ----------

async def sync(markets):
    async with gamma_client(markets) as client:
        async with SessionLocal() as session:
            await service.sync_markets(session, client=client)


async def test_refresh_links_scores_and_filters(db, monkeypatch):
    await ingest_articles(monkeypatch, [
        entry("Fed officials signal interest rates cut in December", "https://n.test/fed"),
        entry("ECB signals interest rates cut in December", "https://n.test/ecb"),
    ])
    await sync([gamma_market(1, FED_Q, yes="0.35")])
    async with SessionLocal() as session:
        assert await service.refresh_links(session) == 1
        link, url = (await session.execute(
            select(MarketArticleLink, Article.url).join(Article, Article.id == MarketArticleLink.article_id)
        )).one()
        assert url == "https://n.test/fed"
        assert link.match_score >= settings.MARKET_MATCH_THRESHOLD and "fed" in link.matched_terms
        assert await service.refresh_links(session) == 0  # unchanged scores are not rewritten
        evidence = await service.get_market_evidence(session, "1")
        assert len(evidence) == 1 and evidence[0].score > 0


# ---------- Targeted search ----------

GOOGLE_NEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"Fed" - Google News</title>
<item><title>Fed's Powell hints at December rate cut - Reuters</title>
<link>https://news.google.com/rss/articles/abc1</link>
<pubDate>{now}</pubDate>
<description>&lt;a href="https://news.google.com/rss/articles/abc1"&gt;Fed's Powell hints at December rate cut&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Reuters&lt;/font&gt;</description>
<source url="https://www.reuters.com">Reuters</source></item>
<item><title>Old story about the Fed - AP News</title>
<link>https://news.google.com/rss/articles/old</link>
<pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
<source url="https://apnews.com">AP News</source></item>
</channel></rss>"""


def test_build_query_uses_key_terms():
    assert targeted.build_query(FED_Q) == "fed cut interest rates"
    assert targeted.build_query("Will Donald Trump win the 2028 presidential election?").startswith('"donald trump"')
    assert targeted.build_query("US government shutdown before January 31, 2027?").startswith("US ")
    url = targeted.search_url("fed cut")
    assert url.startswith(settings.TARGETED_NEWS_URL) and "when%3A7d" in url


async def test_targeted_search_stores_links_and_stays_out_of_sources(db, monkeypatch):
    monkeypatch.setattr(settings, "TARGETED_NEWS_ENABLED", True)
    requested = []
    now_rfc = NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")

    async def fake_fetch(url):
        requested.append(url)
        return parse_feed(GOOGLE_NEWS_RSS.format(now=now_rfc).encode())

    monkeypatch.setattr(targeted, "fetch_feed", fake_fetch)
    await sync([gamma_market(1, FED_Q, yes="0.35")])

    async with login_client("admin") as api:
        r = await api.post("/markets/1/search-news")
        assert r.status_code == 200, r.text
        assert r.json()["added"] == 1 and r.json()["linked"] == 1  # the 2024 story is outside the window
        assert "fed" in requested[0]

        detail = (await api.get("/markets/1")).json()
        ev = detail["evidence"][0]
        assert ev["source_name"] == "Reuters" and ev["targeted"] is True
        assert ev["title"] == "Fed's Powell hints at December rate cut"

        # The automatic source is not a feed: not listed, not editable, not fetched
        assert all(s["name"] != targeted.SOURCE_NAME for s in (await api.get("/sources")).json())
    async with SessionLocal() as session:
        source = (await session.execute(select(Source).where(Source.kind == "targeted"))).scalar_one()
        article = (await session.execute(select(Article).where(Article.source_id == source.id))).scalar_one()
        assert article.publisher == "Reuters" and article.content_raw is None
        market = await session.get(Market, "1")
        assert market.targeted_at is not None
        # Searched recently: skipped until TARGETED_NEWS_REFRESH_HOURS pass
        assert (await targeted.run_targeted_search(session))["searched"] == 0
    async with login_client("admin") as api:
        assert (await api.patch(f"/sources/{source.id}", json={"active": False})).status_code == 404
        assert (await api.delete(f"/sources/{source.id}")).status_code == 404


async def test_targeted_search_stops_when_the_service_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "TARGETED_NEWS_ENABLED", True)
    calls = []

    async def broken(url):
        calls.append(url)
        raise RuntimeError("network down")

    monkeypatch.setattr(targeted, "fetch_feed", broken)
    await sync([gamma_market(i, f"Will team {i} win the Champions League final?", yes="0.2") for i in range(1, 6)])
    async with SessionLocal() as session:
        stats = await targeted.run_targeted_search(session)
    assert stats["errors"] == targeted.MAX_CONSECUTIVE_ERRORS == len(calls)


async def test_viewer_cannot_search(db):
    async with login_client("viewer") as api:
        assert (await api.post("/markets/1/search-news")).status_code == 403
