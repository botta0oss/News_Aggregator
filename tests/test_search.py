from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.db.database import SessionLocal, engine
from backend.db.models import Article, ProcessedArticle, Source
from tests.conftest import login_client

NOW = datetime.now(timezone.utc)
DOCS = [
    # title, content, summary, category, region, market_relevance, is_opinion, age_hours, source
    ("Fed holds interest rates steady", "Powell said inflation remains sticky.", "La Fed lascia i tassi invariati.", "Economy", "North America", 0.8, 0.1, 2, "CNBC"),
    ("Bitcoin tops $100,000 for the first time", "Spot ETF inflows drove the rally.", None, "Crypto", "Global", 0.7, 0.1, 5, "CoinDesk"),
    ("Why the rate cut debate is overblown", "An opinion on central banks and interest rates.", None, "Economy", "Global", 0.3, 0.9, 30, "CNBC"),
    ("Arsenal beat Chelsea in London derby", "Saka scored twice.", None, "Sports", "Europe", 0.2, 0.1, 200, "BBC Sport"),
]


@pytest.fixture
async def corpus(db):
    async with SessionLocal() as s:
        sources = {}
        for i, (title, content, summary, cat, region, mr, op, age, src) in enumerate(DOCS):
            if src not in sources:
                sources[src] = Source(name=src, url=f"https://{i}.test/rss")
                s.add(sources[src])
                await s.flush()
            a = Article(source_id=sources[src].id, title=title, url=f"https://n.test/{i}", url_hash=f"h{i}",
                        content_raw=content, published_at=NOW - timedelta(hours=age))
            s.add(a)
            await s.flush()
            s.add(ProcessedArticle(article_id=a.id, summary=summary, category=cat, region=region,
                                   market_relevance=mr, is_opinion=op, authority_score=0.8, classifier="heuristic"))
        await s.commit()
        return {name: src.id for name, src in sources.items()}


async def search(api, **params):
    r = await api.get("/articles", params=params)
    assert r.status_code == 200, r.text
    return [a["title"] for a in r.json()["articles"]], r.json()


async def test_search_title_content_summary_and_prefix(corpus):
    async with login_client("viewer") as api:
        titles, _ = await search(api, q="interest rates")
        assert set(titles) == {"Fed holds interest rates steady", "Why the rate cut debate is overblown"}
        assert titles[0] == "Fed holds interest rates steady"  # title match ranks first
        assert (await search(api, q="ETF inflows"))[0] == ["Bitcoin tops $100,000 for the first time"]  # content
        assert (await search(api, q="tassi invariati"))[0] == ["Fed holds interest rates steady"]      # summary
        assert (await search(api, q="bitc"))[0] == ["Bitcoin tops $100,000 for the first time"]        # prefix
        assert (await search(api, q="saka", scope="title"))[0] == []                                   # title only
        assert (await search(api, q="SAKA"))[0] == ["Arsenal beat Chelsea in London derby"]            # case-insensitive


async def test_search_syntax_and_safety(corpus):
    async with login_client("viewer") as api:
        assert (await search(api, q="rates -opinion"))[0] == ["Fed holds interest rates steady"]
        assert (await search(api, q='"interest rates steady"'))[0] == ["Fed holds interest rates steady"]
        assert set((await search(api, q="bitcoin or arsenal"))[0]) == {
            "Bitcoin tops $100,000 for the first time", "Arsenal beat Chelsea in London derby"}
        for weird in ("!!!", "fed & | !", "':* (", "a" * 190):
            r = await api.get("/articles", params={"q": weird})
            assert r.status_code == 200, weird


async def test_search_highlights(corpus):
    async with login_client("viewer") as api:
        _, body = await search(api, q="inflows")
        art = body["articles"][0]
        assert "\u0002inflows\u0003" in art["snippet"]
        _, body = await search(api, q="bitcoin")
        assert body["articles"][0]["title_highlight"].startswith("\u0002Bitcoin\u0003")
        assert body["articles"][0]["snippet"] is None  # no match in the body text


async def test_filters(corpus):
    async with login_client("viewer") as api:
        assert (await search(api, source_id=str(corpus["CoinDesk"])))[0] == ["Bitcoin tops $100,000 for the first time"]
        assert len((await search(api, since_hours=24))[0]) == 2
        assert (await search(api, region="Europe"))[0] == ["Arsenal beat Chelsea in London derby"]
        assert set((await search(api, min_market_relevance=0.5))[0]) == {
            "Fed holds interest rates steady", "Bitcoin tops $100,000 for the first time"}
        assert "Why the rate cut debate is overblown" not in (await search(api, hide_opinion=True))[0]
        titles, body = await search(api, sort="recent")
        assert titles[0] == "Fed holds interest rates steady" and body["total"] == 4
        assert body["articles"][0]["region"] == "North America" and body["articles"][0]["market_relevance"] == 0.8


async def test_fts_index_is_used(corpus):
    async with engine.connect() as conn:
        await conn.execute(text("SET enable_seqscan = off"))
        plan = "\n".join((await conn.execute(text(
            "EXPLAIN SELECT id FROM articles WHERE to_tsvector('simple', coalesce(title, '') || ' ' || "
            "coalesce(content_raw, '')) @@ to_tsquery('simple', 'bitcoin')"
        ))).scalars().all())
    assert "ix_articles_fts" in plan


async def test_api_query_uses_fts_index(corpus):
    """The expression the endpoint generates must match the index, or Postgres scans the table."""
    from sqlalchemy import select, func
    from backend.api.routes.articles import FTS_EXPR, build_tsquery
    from sqlalchemy import literal_column
    stmt = select(Article.id).where(literal_column(FTS_EXPR).op("@@")(build_tsquery("bitcoin")))
    compiled = stmt.compile(dialect=engine.dialect)
    params = tuple(compiled.params[k] for k in compiled.positiontup)
    async with engine.connect() as conn:
        await conn.execute(text("SET enable_seqscan = off"))
        result = await conn.exec_driver_sql(f"EXPLAIN {compiled}", params)
        plan = "\n".join(r[0] for r in result.all())
    assert "ix_articles_fts" in plan, plan
