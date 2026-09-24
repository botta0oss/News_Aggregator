import asyncio
import html
import ipaddress
import logging
import re
import socket
from calendar import timegm
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from backend.config import settings

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")
USER_AGENT = "NewsMarketsBot/1.0 (+https://github.com/botta0oss/News_Aggregator)"
MAX_REDIRECTS = 3

# Tests replace this with an httpx.MockTransport
_transport: Optional[httpx.AsyncBaseTransport] = None


class FeedError(Exception):
    """A feed could not be fetched or parsed; the message is shown to the user."""


@dataclass
class FeedResult:
    title: Optional[str]
    entries: list = field(default_factory=list)


def clean_html(text: str) -> str:
    """RSS summaries often contain HTML markup/entities: keep plain text only."""
    if not text:
        return ""
    # Strip, unescape, strip again: feeds often carry escaped markup (&lt;p&gt;) inside real markup
    text = _TAG_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text)))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


async def resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list({info[4][0] for info in infos})


async def check_url(url: str) -> None:
    """Rejects non-HTTP URLs and, unless allowed, hosts resolving to internal addresses (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FeedError("L'indirizzo deve iniziare con http:// o https://")
    if settings.ALLOW_PRIVATE_FEEDS:
        return
    try:
        addresses = await resolve_host(parsed.hostname)
    except OSError:
        raise FeedError(f"Il dominio {parsed.hostname} non esiste o non risponde")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise FeedError("L'indirizzo punta a una rete interna: non è consentito")


async def download(url: str) -> bytes:
    """GET with timeout, size cap and redirects re-checked at every hop."""
    async with httpx.AsyncClient(
        timeout=settings.FEED_TIMEOUT_SECONDS, follow_redirects=False, transport=_transport,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            try:
                async with client.stream("GET", url) as res:
                    if res.is_redirect and res.headers.get("location"):
                        url = urljoin(url, res.headers["location"])
                        continue
                    if res.status_code >= 400:
                        raise FeedError(f"Il server ha risposto con errore {res.status_code}")
                    chunks, size = [], 0
                    async for chunk in res.aiter_bytes():
                        size += len(chunk)
                        if size > settings.FEED_MAX_BYTES:
                            raise FeedError("Il feed è troppo grande")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.TimeoutException:
                raise FeedError("Il server non ha risposto in tempo")
            except httpx.HTTPError as e:
                raise FeedError(f"Connessione non riuscita: {e.__class__.__name__}")
        raise FeedError("Troppi reindirizzamenti")


def parse_feed(content: bytes) -> FeedResult:
    feed = feedparser.parse(content)
    # feedparser does not always flag an HTML page as an error: no entries and no feed version means "not a feed"
    if not feed.entries and (feed.get("bozo") or not feed.get("version")):
        raise FeedError("Il contenuto non è un feed RSS o Atom valido")
    entries = []
    for entry in feed.entries:
        title = clean_html(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue  # unusable entry (and an empty URL would collide on url_hash)
        published_at = None
        parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed_time:
            # feedparser returns UTC struct_time: timegm (not mktime, which assumes local time)
            published_at = datetime.fromtimestamp(timegm(parsed_time), tz=timezone.utc)
        entries.append({
            "title": title,
            "url": link,
            "content_raw": clean_html(entry.get("summary", entry.get("description", ""))),
            "published_at": published_at,
        })
    return FeedResult(title=clean_html(feed.feed.get("title", "")) or None, entries=entries)


async def fetch_feed(url: str) -> FeedResult:
    content = await download(url)
    # Parsing is CPU work: keep it off the event loop so the web server stays responsive
    return await asyncio.to_thread(parse_feed, content)


async def fetch_and_parse_feed(url: str) -> list[dict]:
    return (await fetch_feed(url)).entries
