"""Client-side rate limiting for the AI providers (Groq, Gemini, Ollama, TypeSafe Jev).

Each provider gets a limiter that spaces requests to at most N per minute, caps how many
run at the same time and, after a 429, pauses the provider for the time the API asks
(Retry-After). Waiting is always `await asyncio.sleep`, so the web server keeps
answering while background jobs wait for their turn.
"""
import asyncio
import contextlib
import logging
import time
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """The provider cannot be called within the allowed wait (limit reached or cooling down)."""

    def __init__(self, provider: str, retry_in: float):
        super().__init__(f"{provider}: limite di richieste raggiunto, riprova tra {max(1, round(retry_in))} s")
        self.provider = provider
        self.retry_in = retry_in


class ProviderLimiter:
    def __init__(self, name: str, per_minute: float, concurrency: int):
        self.name = name
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self.concurrency = max(1, concurrency)
        self._next_start = 0.0
        self._cooldown_until = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._sem: Optional[asyncio.Semaphore] = None

    def _primitives(self):
        # Created lazily so they belong to the running event loop
        if self._lock is None:
            self._lock = asyncio.Lock()
            self._sem = asyncio.Semaphore(self.concurrency)
        return self._lock, self._sem

    def cooling_down_for(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    def cooldown(self, seconds: float) -> None:
        seconds = max(1.0, min(float(seconds), 3600.0))
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)
        logger.warning(f"{self.name}: rate limited by the API, pausing for {seconds:.0f}s")

    async def _reserve(self, max_wait: float) -> float:
        lock, _ = self._primitives()
        async with lock:
            now = time.monotonic()
            start = max(now, self._next_start, self._cooldown_until)
            wait = start - now
            if wait > max_wait:
                raise RateLimited(self.name, wait)
            self._next_start = start + self.interval
            return wait

    @contextlib.asynccontextmanager
    async def slot(self, max_wait: Optional[float] = None):
        """Waits for a free slot (at most `max_wait` seconds) or raises RateLimited."""
        max_wait = settings.AI_MAX_WAIT_SECONDS if max_wait is None else max_wait
        wait = await self._reserve(max_wait)
        if wait > 0:
            await asyncio.sleep(wait)
        _, sem = self._primitives()
        async with sem:
            yield


_limiters: dict[str, ProviderLimiter] = {}


def get_limiter(name: str) -> ProviderLimiter:
    if name not in _limiters:
        per_minute, concurrency = {
            "groq": (settings.GROQ_RPM, 1),
            "gemini": (settings.GEMINI_RPM, 1),
            "ollama": (0, settings.OLLAMA_CONCURRENCY),
            "jev": (settings.JEV_RPM, settings.JEV_CONCURRENCY),
        }.get(name, (30, 1))
        _limiters[name] = ProviderLimiter(name, per_minute, concurrency)
    return _limiters[name]


def reset_limiters() -> None:
    _limiters.clear()


def retry_after_seconds(error: Exception, default: float = 30.0) -> float:
    """Reads Retry-After (seconds) / retry-after-ms from an SDK error's response headers."""
    headers = getattr(error, "headers", None)
    response = getattr(error, "response", None)
    if headers is None and response is not None:
        headers = getattr(response, "headers", None)
    try:
        if headers:
            if headers.get("retry-after-ms"):
                return float(headers["retry-after-ms"]) / 1000
            if headers.get("retry-after"):
                return float(headers["retry-after"])
    except (TypeError, ValueError):
        pass
    return default
