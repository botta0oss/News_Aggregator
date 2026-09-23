import feedparser
import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from calendar import timegm

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")

def clean_html(text: str) -> str:
    """RSS summaries often contain HTML markup/entities: keep plain text only."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text))).strip()

async def fetch_and_parse_feed(url: str):
    loop = asyncio.get_running_loop()
    # Run sync feedparser in threadpool
    feed = await loop.run_in_executor(None, feedparser.parse, url)
    if feed.get("bozo") and not feed.entries:
        # feedparser does not raise on network/parse errors: surface them
        logger.warning(f"Feed {url} returned no entries: {feed.get('bozo_exception')}")
    
    entries = []
    for entry in feed.entries:
        title = clean_html(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue  # unusable entry (and an empty URL would collide on url_hash)
        published_at = None
        if getattr(entry, 'published_parsed', None):
            # feedparser returns UTC struct_time: timegm (not mktime, which assumes local time)
            published_at = datetime.fromtimestamp(timegm(entry.published_parsed), tz=timezone.utc)
            
        entries.append({
            "title": title,
            "url": link,
            "content_raw": clean_html(entry.get("summary", entry.get("description", ""))),
            "published_at": published_at
        })
    return entries
