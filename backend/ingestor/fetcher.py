import feedparser
import asyncio
from datetime import datetime, timezone
from time import mktime

async def fetch_and_parse_feed(url: str):
    loop = asyncio.get_event_loop()
    # Run sync feedparser in threadpool
    feed = await loop.run_in_executor(None, feedparser.parse, url)
    
    entries =[]
    for entry in feed.entries:
        published_at = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            published_at = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
            
        entries.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "content_raw": entry.get("summary", entry.get("description", "")),
            "published_at": published_at
        })
    return entries