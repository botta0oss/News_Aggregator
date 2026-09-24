"""Feed sources: the recommended catalog (feeds.yaml) and the sources stored in the database."""
import logging
import os
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

import yaml
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Source

logger = logging.getLogger(__name__)

CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "feeds.yaml")
CATEGORIES = ("Politics", "Economy", "Crypto", "Technology", "Foreign Affairs", "Science", "Sports", "Culture")


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict, ...]:
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            feeds = (yaml.safe_load(f) or {}).get("feeds", [])
    except (OSError, yaml.YAMLError) as e:
        logger.error(f"Could not load feeds.yaml: {e}")
        return ()
    catalog = []
    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("url") or not feed.get("name"):
            continue
        category = feed.get("category")
        catalog.append({
            "name": str(feed["name"]).strip(),
            "url": str(feed["url"]).strip(),
            "category_hint": category if category in CATEGORIES else None,
            "description": feed.get("description"),
            "active": bool(feed.get("active", True)),
        })
    return tuple(catalog)


def normalize_feed_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Inserisci un indirizzo completo, che inizi con http:// o https://")
    if len(url) > 2000:
        raise ValueError("L'indirizzo è troppo lungo")
    return url


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not 2 <= len(name) <= 80:
        raise ValueError("Il nome deve avere tra 2 e 80 caratteri")
    return name


def validate_category(category: Optional[str]) -> Optional[str]:
    if category in (None, ""):
        return None
    if category not in CATEGORIES:
        raise ValueError("Categoria non valida")
    return category


async def get_by_url(session: AsyncSession, url: str) -> Optional[Source]:
    return (await session.execute(select(Source).where(Source.url == url))).scalar_one_or_none()


async def seed_sources_if_empty(session: AsyncSession) -> int:
    """First run: adds the catalog feeds marked active. Later changes are made in the dashboard."""
    if (await session.execute(select(func.count(Source.id)))).scalar():
        return 0
    added = 0
    for feed in load_catalog():
        if feed["active"]:
            session.add(Source(name=feed["name"], url=feed["url"], category_hint=feed["category_hint"], active=True))
            added += 1
    await session.commit()
    if added:
        logger.info(f"Added {added} sources from feeds.yaml")
    return added
