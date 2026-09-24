import httpx
import pytest
from sqlalchemy import select, text, func

from backend.db.database import SessionLocal, engine
from backend.db.migrations import run_migrations
from backend.db.models import Article, ProcessedArticle, Source
from backend.ingestor import fetcher, scheduler
from backend.ingestor.sources import load_catalog, seed_sources_if_empty
from tests.conftest import login_client

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title>
<item><title>Fed holds rates steady</title><link>https://n.test/1</link><description>&lt;p&gt;The <b>Fed</b> kept rates.&lt;/p&gt;</description>
<pubDate>Tue, 22 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Bitcoin tops $100k</title><link>https://n.test/2</link></item>
<item><title></title><link>https://n.test/3</link></item>
</channel></rss>"""


@pytest.fixture
def public_dns(monkeypatch):
    async def resolve(host):
        return {"internal.test": ["10.0.0.5"], "loop.test": ["127.0.0.1"], "meta.test": ["169.254.169.254"]}.get(host, ["93.184.216.34"])
    monkeypatch.setattr(fetcher, "resolve_host", resolve)


@pytest.fixture
def feed_server(monkeypatch, public_dns):
    """Mock HTTP layer for the fetcher."""
    routes = {}

    def handler(request: httpx.Request):
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    monkeypatch.setattr(fetcher, "_transport", httpx.MockTransport(handler))
    return routes


# ---------- Fetcher ----------

async def test_fetch_parses_and_cleans(feed_server):
    feed_server["https://feeds.test/rss"] = httpx.Response(200, content=RSS)
    result = await fetcher.fetch_feed("https://feeds.test/rss")
    assert result.title == "Test Feed"
    assert [e["title"] for e in result.entries] == ["Fed holds rates steady", "Bitcoin tops $100k"]  # empty title skipped
    assert result.entries[0]["content_raw"] == "The Fed kept rates."
    assert result.entries[0]["published_at"].isoformat() == "2026-09-22T10:00:00+00:00"


@pytest.mark.parametrize("url", ["ftp://feeds.test/rss", "https://internal.test/rss", "http://loop.test/rss", "http://meta.test/latest"])
async def test_fetch_blocks_non_http_and_internal_addresses(feed_server, url):
    with pytest.raises(fetcher.FeedError):
        await fetcher.fetch_feed(url)


async def test_redirect_to_internal_address_is_blocked(feed_server):
    feed_server["https://feeds.test/moved"] = httpx.Response(302, headers={"location": "http://internal.test/admin"})
    with pytest.raises(fetcher.FeedError, match="rete interna"):
        await fetcher.fetch_feed("https://feeds.test/moved")


async def test_redirect_followed_and_errors_reported(feed_server, monkeypatch):
    feed_server["https://feeds.test/old"] = httpx.Response(301, headers={"location": "/new"})
    feed_server["https://feeds.test/new"] = httpx.Response(200, content=RSS)
    assert len((await fetcher.fetch_feed("https://feeds.test/old")).entries) == 2
    feed_server["https://feeds.test/html"] = httpx.Response(200, content=b"<html>not a feed</html>")
    with pytest.raises(fetcher.FeedError, match="non è un feed"):
        await fetcher.fetch_feed("https://feeds.test/html")
    with pytest.raises(fetcher.FeedError, match="404"):
        await fetcher.fetch_feed("https://feeds.test/missing")
    monkeypatch.setattr(fetcher.settings, "FEED_MAX_BYTES", 100)
    with pytest.raises(fetcher.FeedError, match="troppo grande"):
        await fetcher.fetch_feed("https://feeds.test/new")


# ---------- Catalog, seeding, migrations ----------

def test_catalog_is_valid():
    catalog = load_catalog()
    assert len(catalog) >= 25
    assert len({f["url"] for f in catalog}) == len(catalog)
    assert {"Crypto", "Sports", "Politics", "Economy"} <= {f["category_hint"] for f in catalog}
    assert all(f["url"].startswith("https://") for f in catalog)


async def test_seed_only_when_empty(db):
    async with SessionLocal() as s:
        added = await seed_sources_if_empty(s)
        assert added == sum(1 for f in load_catalog() if f["active"])
        assert await seed_sources_if_empty(s) == 0


async def test_migrations_upgrade_old_schema(db):
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE sources DROP COLUMN category_hint"))
        await conn.execute(text("ALTER TABLE processed_articles DROP COLUMN market_relevance"))
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent
        cols = (await conn.execute(text(
            "select column_name from information_schema.columns where table_name in ('sources','processed_articles')"
        ))).scalars().all()
    assert "category_hint" in cols and "market_relevance" in cols


async def test_pipeline_uses_db_sources_and_records_status(db, monkeypatch):
    async def fake_fetch(url):
        if "broken" in url:
            raise fetcher.FeedError("Il server ha risposto con errore 500")
        return [{"title": "Fed holds rates steady", "url": "https://n.test/a", "content_raw": "", "published_at": None}]
    monkeypatch.setattr(scheduler, "fetch_and_parse_feed", fake_fetch)
    async with SessionLocal() as s:
        s.add_all([Source(name="Good", url="https://good.test/rss"), Source(name="Broken", url="https://broken.test/rss"),
                   Source(name="Off", url="https://off.test/rss", active=False)])
        await s.commit()
    await scheduler._run_pipeline()
    async with SessionLocal() as s:
        by_name = {x.name: x for x in (await s.execute(select(Source))).scalars().all()}
        assert by_name["Good"].last_status == "ok" and by_name["Good"].last_new_items == 1
        assert by_name["Broken"].last_status == "error" and "500" in by_name["Broken"].last_error
        assert by_name["Off"].last_fetched_at is None
        assert (await s.execute(select(func.count(ProcessedArticle.id)))).scalar() == 1


# ---------- API ----------

async def test_sources_crud_and_permissions(db):
    async with login_client("viewer") as viewer:
        assert (await viewer.get("/sources")).status_code == 200
        assert (await viewer.post("/sources", json={"name": "X", "url": "https://x.test/rss"})).status_code == 403

    async with login_client("admin") as api:
        r = await api.post("/sources", json={"name": "  CoinDesk ", "url": "https://coindesk.test/rss", "category_hint": "Crypto"})
        assert r.status_code == 201, r.text
        src = r.json()
        assert src["name"] == "CoinDesk" and src["category_hint"] == "Crypto" and src["active"] is True
        assert (await api.post("/sources", json={"name": "Dup", "url": "https://coindesk.test/rss"})).status_code == 409
        assert (await api.post("/sources", json={"name": "Bad", "url": "javascript:alert(1)"})).status_code == 422
        assert (await api.post("/sources", json={"name": "Bad", "url": "https://x.test", "category_hint": "Nope"})).status_code == 422

        r = await api.patch(f"/sources/{src['id']}", json={"active": False, "name": "CoinDesk EN"})
        assert r.status_code == 200 and r.json()["active"] is False and r.json()["name"] == "CoinDesk EN"

        # Deleting a source with articles needs explicit confirmation and removes them
        async with SessionLocal() as s:
            a = Article(source_id=src["id"], title="t", url="https://n.test/x", url_hash="hx")
            s.add(a)
            await s.flush()
            s.add(ProcessedArticle(article_id=a.id, summary="s"))
            await s.commit()
        listed = {x["id"]: x for x in (await api.get("/sources")).json()}
        assert listed[src["id"]]["article_count"] == 1
        r = await api.delete(f"/sources/{src['id']}")
        assert r.status_code == 409 and "1 notizie" in r.json()["detail"]
        assert (await api.delete(f"/sources/{src['id']}", params={"delete_articles": True})).status_code == 204
        async with SessionLocal() as s:
            assert (await s.execute(select(func.count(Article.id)))).scalar() == 0
        assert (await api.delete(f"/sources/{src['id']}")).status_code == 404


async def test_catalog_endpoints(db):
    async with login_client("admin") as api:
        catalog = (await api.get("/sources/catalog")).json()
        first = catalog[0]
        assert first["added"] is False
        r = await api.post("/sources/catalog", json={"urls": [first["url"], first["url"]]})
        assert r.status_code == 200 and len(r.json()) == 1
        again = {c["url"]: c for c in (await api.get("/sources/catalog")).json()}
        assert again[first["url"]]["added"] is True
        assert (await api.post("/sources/catalog", json={"urls": ["https://not-in-catalog.test"]})).status_code == 422


async def test_feed_test_endpoint(db, feed_server):
    feed_server["https://feeds.test/rss"] = httpx.Response(200, content=RSS)
    async with login_client("admin") as api:
        ok = (await api.post("/sources/test", json={"url": "https://feeds.test/rss"})).json()
        assert ok == {"ok": True, "title": "Test Feed", "item_count": 2,
                      "samples": ["Fed holds rates steady", "Bitcoin tops $100k"], "error": None}
        bad = (await api.post("/sources/test", json={"url": "https://internal.test/rss"})).json()
        assert bad["ok"] is False and "rete interna" in bad["error"]
