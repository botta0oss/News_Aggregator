"""Shared TypeSafe Jev (System One) client."""
from typing import Optional
from backend.config import settings

_client = None


class JevUnavailableError(RuntimeError):
    """Raised when a Jev evaluation is required but no API key is configured."""


def is_enabled() -> bool:
    return _client is not None or bool(settings.TYPESAFE_API_KEY)


def get_client():
    """Returns a lazily created, process-wide AsyncTypeSafeClient (reuses the HTTP connection pool)."""
    global _client
    if _client is None:
        if not settings.TYPESAFE_API_KEY:
            raise JevUnavailableError("TYPESAFE_API_KEY is not configured")
        from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        # 429s are handled by our rate limiter (pause + Retry-After); the SDK only retries
        # timeouts and server errors
        retry = RetryPolicy(max_retries=2, http_statuses={408, 500, 502, 503, 504})
        _client = AsyncTypeSafeClient(api_key=settings.TYPESAFE_API_KEY, model=settings.TYPESAFE_MODEL, retry=retry)
    return _client


def set_client(client) -> None:
    """Overrides the shared client (used by tests to inject a mock transport)."""
    global _client
    _client = client


async def close_client() -> None:
    global _client
    client: Optional[object] = _client
    _client = None
    if client is not None:
        await client.aclose()


async def system_one(state, questions, max_wait: Optional[float] = None):
    """Rate-limited System One call. Raises RateLimited when Jev is paused or saturated."""
    from backend.ai import usage
    from backend.ai.ratelimit import get_limiter, retry_after_seconds
    await usage.check("jev")  # daily limits: raises BudgetExceeded (a RateLimited)
    limiter = get_limiter("jev")
    async with limiter.slot(max_wait):
        try:
            response = await get_client().system_one(state=state, questions=questions)
        except Exception as e:
            if getattr(e, "status_code", None) == 429 or type(e).__name__ == "TypeSafeRateLimitError":
                limiter.cooldown(retry_after_seconds(e, 30))
            await usage.record("jev", ok=False)
            raise
    tokens = getattr(response, "usage", None)
    await usage.record("jev", getattr(tokens, "input_tokens", 0), getattr(tokens, "output_tokens", 0))
    return response
