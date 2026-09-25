import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.auth.deps import require_admin
from backend.db.database import get_db
from backend.db.models import Article, ProcessedArticle, Source
from backend.ingestor import fetcher
from backend.i18n import lang, tr
from backend.ingestor.sources import (
    CATEGORIES, get_by_url, load_catalog, normalize_feed_url, validate_category, validate_name,
)

router = APIRouter(prefix="/sources", tags=["sources"])
admin = [Depends(require_admin)]


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    active: bool
    category_hint: Optional[str]
    created_at: Optional[datetime]
    last_fetched_at: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]
    last_new_items: Optional[int]
    article_count: int = 0
    last_article_at: Optional[datetime] = None


class SourceIn(BaseModel):
    name: str = Field(max_length=200)
    url: str = Field(max_length=2000)
    category_hint: Optional[str] = None
    active: bool = True


class SourcePatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    url: Optional[str] = Field(default=None, max_length=2000)
    category_hint: Optional[str] = None
    active: Optional[bool] = None


class FeedTestIn(BaseModel):
    url: str = Field(max_length=2000)


class FeedTestOut(BaseModel):
    ok: bool
    title: Optional[str] = None
    item_count: int = 0
    samples: list[str] = []
    error: Optional[str] = None


class CatalogItem(BaseModel):
    name: str
    url: str
    category_hint: Optional[str]
    description: Optional[str]
    added: bool


class CatalogAddIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)


def _out(source: Source, count: int = 0, last_at=None) -> dict:
    return {
        **{k: getattr(source, k) for k in (
            "id", "name", "url", "active", "category_hint", "created_at",
            "last_fetched_at", "last_status", "last_error", "last_new_items",
        )},
        "article_count": count,
        "last_article_at": last_at,
    }


async def _stats(db: AsyncSession, ids: list) -> dict:
    if not ids:
        return {}
    rows = await db.execute(
        select(Article.source_id, func.count(Article.id), func.max(Article.fetched_at))
        .where(Article.source_id.in_(ids)).group_by(Article.source_id)
    )
    return {sid: (count, last) for sid, count, last in rows.all()}


async def _get(db: AsyncSession, source_id: uuid.UUID) -> Source:
    source = await db.get(Source, source_id)
    if source is None or source.kind != "feed":  # the targeted-search source is managed automatically
        raise HTTPException(status_code=404, detail=tr("Fonte non trovata", "Source not found"))
    return source


def _bad_request(e: ValueError):
    return HTTPException(status_code=422, detail=str(e))


@router.get("", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    sources = (await db.execute(select(Source).where(Source.kind == "feed").order_by(Source.active.desc(), Source.name))).scalars().all()
    stats = await _stats(db, [s.id for s in sources])
    return [_out(s, *stats.get(s.id, (0, None))) for s in sources]


@router.get("/catalog", response_model=list[CatalogItem])
async def catalog(db: AsyncSession = Depends(get_db)):
    """Recommended feeds from feeds.yaml, marking those already added."""
    existing = set((await db.execute(select(Source.url))).scalars().all())
    return [{**{k: f[k] for k in ("name", "url", "category_hint")},
             "description": (f.get("description_it") if lang() == "it" else None) or f["description"],
             "added": f["url"] in existing}
            for f in load_catalog()]


@router.get("/categories")
async def categories():
    return list(CATEGORIES)


@router.post("/catalog", response_model=list[SourceOut], dependencies=admin)
async def add_from_catalog(body: CatalogAddIn, db: AsyncSession = Depends(get_db)):
    by_url = {f["url"]: f for f in load_catalog()}
    added = []
    for url in dict.fromkeys(body.urls):
        feed = by_url.get(url)
        if feed is None:
            raise HTTPException(status_code=422, detail=tr(f"{url} non è nel catalogo", f"{url} is not in the catalogue"))
        if await get_by_url(db, url):
            continue
        source = Source(name=feed["name"], url=url, category_hint=feed["category_hint"], active=True)
        db.add(source)
        added.append(source)
    await db.commit()
    return [_out(s) for s in added]


@router.post("/test", response_model=FeedTestOut, dependencies=admin)
async def test_feed(body: FeedTestIn):
    """Downloads a feed without saving anything, to check it before adding it."""
    try:
        result = await fetcher.fetch_feed(normalize_feed_url(body.url))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except fetcher.FeedError as e:
        return {"ok": False, "error": str(e)}
    if not result.entries:
        return {"ok": False, "title": result.title, "error": tr("Il feed è valido ma non contiene notizie", "The feed is valid but contains no news")}
    return {"ok": True, "title": result.title, "item_count": len(result.entries),
            "samples": [e["title"] for e in result.entries[:3]]}


@router.post("", response_model=SourceOut, status_code=201, dependencies=admin)
async def create_source(body: SourceIn, db: AsyncSession = Depends(get_db)):
    try:
        url = normalize_feed_url(body.url)
        name = validate_name(body.name)
        category = validate_category(body.category_hint)
    except ValueError as e:
        raise _bad_request(e)
    if await get_by_url(db, url):
        raise HTTPException(status_code=409, detail=tr("Questa fonte è già presente", "This source is already there"))
    source = Source(name=name, url=url, category_hint=category, active=body.active)
    db.add(source)
    await db.commit()
    return _out(source)


@router.patch("/{source_id}", response_model=SourceOut, dependencies=admin)
async def update_source(source_id: uuid.UUID, body: SourcePatch, db: AsyncSession = Depends(get_db)):
    source = await _get(db, source_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        if "name" in fields:
            source.name = validate_name(fields["name"])
        if "url" in fields:
            url = normalize_feed_url(fields["url"])
            other = await get_by_url(db, url)
            if other is not None and other.id != source.id:
                raise HTTPException(status_code=409, detail=tr("Un'altra fonte usa già questo indirizzo", "Another source already uses this address"))
            if url != source.url:
                source.url = url
                source.last_status = source.last_error = source.last_fetched_at = source.last_new_items = None
        if "category_hint" in fields:
            source.category_hint = validate_category(fields["category_hint"])
        if "active" in fields and fields["active"] is not None:
            source.active = fields["active"]
    except ValueError as e:
        raise _bad_request(e)
    await db.commit()
    count, last = (await _stats(db, [source.id])).get(source.id, (0, None))
    return _out(source, count, last)


@router.delete("/{source_id}", status_code=204, dependencies=admin)
async def delete_source(source_id: uuid.UUID, delete_articles: bool = Query(False), db: AsyncSession = Depends(get_db)):
    """Deletes a source. If it has articles, `delete_articles=true` is required and removes them too."""
    source = await _get(db, source_id)
    count = (await db.execute(select(func.count(Article.id)).where(Article.source_id == source_id))).scalar() or 0
    if count and not delete_articles:
        raise HTTPException(status_code=409, detail=tr(f"La fonte ha {count} notizie: conferma per eliminarle insieme alla fonte, oppure disattivala.",
                                                      f"The source has {count} news items: confirm to delete them together with the source, or deactivate it."))
    if count:
        article_ids = select(Article.id).where(Article.source_id == source_id)
        await db.execute(delete(ProcessedArticle).where(ProcessedArticle.article_id.in_(article_ids)))
        await db.execute(delete(Article).where(Article.source_id == source_id))  # market links cascade
    await db.delete(source)
    await db.commit()


@router.post("/{source_id}/fetch", status_code=202, dependencies=admin)
async def fetch_now(source_id: uuid.UUID, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    from backend.ingestor.scheduler import ingest_single_source
    source = await _get(db, source_id)
    background_tasks.add_task(ingest_single_source, source.id)
    return {"status": "started"}
