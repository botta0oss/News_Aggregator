"""Embedding model change: stored vectors are cleared and recomputed. Outlet reliability prior."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.db.database import SessionLocal
from backend.db.models import AppMeta, Article, Market, ProcessedArticle, Source
from backend.ingestor import deduplicator, reembed
from backend.markets.matching import outlet_priors, source_quality
from tests.conftest import fake_embedding

NOW = datetime.now(timezone.utc)


async def add_article(s, source, title, hours_ago=1, embedded=True, publisher=None, authority=None):
    a = Article(source_id=source.id, title=title, url=f"https://n.test/{title}", url_hash=title,
                fetched_at=NOW - timedelta(hours=hours_ago), publisher=publisher,
                title_embedding=fake_embedding(title) if embedded else None,
                content_embedding=fake_embedding(title) if embedded else None)
    s.add(a)
    await s.flush()
    if authority is not None:
        s.add(ProcessedArticle(article_id=a.id, authority_score=authority))
    return a


async def test_model_change_clears_and_recomputes_vectors(db, monkeypatch):
    calls = []

    def encode(texts):
        calls.append(len(texts))
        return [[0.5] * 384 for _ in texts]
    monkeypatch.setattr(deduplicator, "get_embeddings", encode)
    async with SessionLocal() as s:
        src = Source(name="Reuters", url="https://r.test/rss")
        s.add(src)
        await s.flush()
        await add_article(s, src, "Old story", hours_ago=30)
        await add_article(s, src, "New story")
        s.add(Market(id="m1", question="Will it happen?", question_embedding=fake_embedding("Will it happen?")))
        await s.commit()

        # Vectors exist but no model recorded: they come from the old default
        monkeypatch.setattr(settings, "EMBEDDING_MODEL", reembed.LEGACY_MODEL)
        assert await reembed.check_model(s) is False
        assert (await s.get(AppMeta, reembed.META_KEY)).value == reembed.LEGACY_MODEL

        monkeypatch.setattr(settings, "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
        assert await reembed.check_model(s) is True
        assert (await s.execute(select(Article).where(Article.title_embedding.is_not(None)))).first() is None
        assert (await s.get(Market, "m1")).question_embedding is None

    assert await reembed.backfill(batch=1) == 2
    async with SessionLocal() as s:
        rows = (await s.execute(select(Article))).scalars().all()
        assert all(list(a.title_embedding) == [0.5] * 384 and a.content_embedding is not None for a in rows)
        assert (await s.get(Market, "m1")).question_embedding is not None
        assert await reembed.check_model(s) is False   # same model again: nothing to do
    assert calls[0] == 1 and calls[1:] == [2, 2]      # market first, then one article per batch (title + text)


def test_source_quality_uses_the_outlet_record():
    art = SimpleNamespace(authority_score=0.2, clickbait_score=0.0, is_opinion=0.0)
    alone = source_quality(art)
    assert source_quality(art, prior=0.9) > alone              # a reliable outlet's weaker article
    assert source_quality(art, prior=0.2) == pytest.approx(alone)
    assert source_quality(None, prior=0.9) == pytest.approx(0.95)  # not classified yet
    assert source_quality(None) == 0.75


async def test_outlet_priors_need_enough_articles(db):
    async with SessionLocal() as s:
        feed = Source(name="Google", url="https://g.test/rss")
        s.add(feed)
        await s.flush()
        for i in range(5):
            await add_article(s, feed, f"Reuters story {i}", publisher="Reuters", authority=0.9)
        for i in range(2):
            await add_article(s, feed, f"Blog story {i}", publisher="Some Blog", authority=0.1)
        await s.commit()
        rows = [(None, SimpleNamespace(publisher=p), None, feed) for p in ("Reuters", "Some Blog")]
        priors = await outlet_priors(s, rows)
    assert priors == {"reuters": pytest.approx(0.9)}   # the blog has too few articles for a record
