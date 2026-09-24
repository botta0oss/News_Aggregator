from datetime import datetime, timezone

from sqlalchemy import select

from backend.db.database import SessionLocal
from backend.db.models import Article, Cluster, Source
from backend.ingestor import scheduler

STORY = ("The Federal Reserve cut interest rates by a quarter point on Wednesday, its first reduction this year, "
         "citing slowing inflation and a cooling labor market; chair Powell signaled more cuts could follow")


def item(title, url, text=STORY, publisher=None):
    return {"title": title, "url": url, "content_raw": text, "published_at": datetime.now(timezone.utc),
            "publisher": publisher}


async def source(session, name):
    row = Source(name=name, url=f"https://{name.lower()}.test/rss")
    session.add(row)
    await session.commit()
    return row


async def test_same_story_from_other_outlets_is_clustered_and_counted_once_per_outlet(db):
    async with SessionLocal() as s:
        reuters, ap, google = await source(s, "Reuters"), await source(s, "AP"), await source(s, "Google")
        await scheduler.store_entries(s, reuters.id, [item("Fed cuts rates by quarter point", "https://r.test/1")])
        # Rewritten headline, same story: joins the cluster
        await scheduler.store_entries(s, ap.id, [item("Powell's Fed lowers borrowing costs", "https://ap.test/1")])
        # The same outlet again adds an article, not a source
        await scheduler.store_entries(s, ap.id, [item("Fed lowers rates, Powell hints at more", "https://ap.test/2")])
        # Aggregated results: each publisher is its own outlet
        await scheduler.store_entries(s, google.id, [
            item("Fed trims rates as inflation cools", "https://g.test/1", publisher="Bloomberg"),
        ])
        # A different story stays apart
        await scheduler.store_entries(s, reuters.id, [item(
            "Apple unveils new iPhone", "https://r.test/2",
            text="Apple presented its new phone lineup at the September event in Cupertino with a faster chip")])

        articles = (await s.execute(select(Article))).scalars().all()
        fed = [a for a in articles if "apple" not in a.title.lower()]
        assert len({a.cluster_id for a in fed}) == 1 and fed[0].cluster_id is not None
        assert next(a for a in articles if "Apple" in a.title).cluster_id is None
        cluster = await s.get(Cluster, fed[0].cluster_id)
        assert cluster.source_count == 3  # Reuters, AP, Bloomberg
