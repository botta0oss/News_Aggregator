from datetime import datetime, timedelta, timezone

import pytest

from backend.db.database import SessionLocal
from backend.db.models import Article, Market, MarketArticleLink, MarketPrediction, Source
from tests.conftest import login_client

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def markets(db):
    # id, question, price, volume, liquidity, end in days, forecast (hours ago, edge), linked news
    rows = [
        ("a", "Alpha question?", 0.20, 5e6, 1e5, 30, (5, 0.02), 1),
        ("b", "bravo question?", 0.80, 1e6, 5e5, 10, (1, -0.15), 3),
        ("c", "Charlie question?", 0.50, 9e6, 2e4, None, None, 0),
        ("d", "Delta question?", 0.35, 2e6, 3e5, 90, (20, 0.09), 2),
    ]
    async with SessionLocal() as s:
        src = Source(name="S", url="https://s.test/rss")
        s.add(src)
        await s.flush()
        for mid, q, price, vol, liq, days, pred, news in rows:
            s.add(Market(id=mid, question=q, yes_price=price, volume=vol, liquidity=liq,
                         end_date=NOW + timedelta(days=days) if days else None))
            await s.flush()
            if pred:
                hours, edge = pred
                s.add(MarketPrediction(market_id=mid, created_at=NOW - timedelta(hours=hours), market_probability=price,
                                       model_probability=price, evidence_strength=0.5, blended_probability=price + edge,
                                       edge=edge, signal="HOLD", kelly_fraction=0, article_count=1))
            for i in range(news):
                a = Article(source_id=src.id, title=f"{mid}{i}", url=f"https://n.test/{mid}{i}", url_hash=f"{mid}{i}")
                s.add(a)
                await s.flush()
                s.add(MarketArticleLink(market_id=mid, article_id=a.id, similarity=0.7))
        await s.commit()


async def ids(api, **params):
    r = await api.get("/markets", params=params)
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()["markets"]]


@pytest.mark.parametrize("sort,order,expected", [
    ("volume", None, ["c", "a", "d", "b"]),
    ("volume", "asc", ["b", "d", "a", "c"]),
    ("end_date", None, ["b", "a", "d", "c"]),        # soonest first, no end date last
    ("end_date", "desc", ["d", "a", "b", "c"]),      # no end date still last
    ("price", None, ["b", "c", "d", "a"]),
    ("price", "asc", ["a", "d", "c", "b"]),
    ("signal", None, ["b", "a", "d", "c"]),          # most recent forecast first, none last
    ("edge", None, ["b", "d", "a", "c"]),            # largest |edge| first
    ("news", None, ["b", "d", "a", "c"]),
    ("liquidity", None, ["b", "d", "a", "c"]),
    ("question", None, ["a", "b", "c", "d"]),        # case-insensitive
    ("question", "desc", ["d", "c", "b", "a"]),
])
async def test_market_sorting(markets, sort, order, expected):
    async with login_client("viewer") as api:
        params = {"sort": sort}
        if order:
            params["order"] = order
        assert await ids(api, **params) == expected


async def test_sorting_is_stable_across_pages(markets):
    async with login_client("viewer") as api:
        first = await ids(api, sort="end_date", limit=2)
        second = await ids(api, sort="end_date", limit=2, offset=2)
        assert first + second == ["b", "a", "d", "c"]
        assert (await api.get("/markets", params={"sort": "nope"})).status_code == 422
