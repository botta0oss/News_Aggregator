import logging
import httpx
from typing import Optional
from backend.ai.ratelimit import RateLimited, get_limiter, retry_after_seconds
from backend.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are an objective news editor. Write a concise 3-sentence factual summary in Italian. Return only the summary."

# Shared clients: one connection pool per provider instead of one client per article
_groq_client = None
_gemini_client = None


def _groq():
    global _groq_client
    if _groq_client is None:
        from groq import AsyncGroq
        # Retries are handled by our rate limiter (Retry-After aware), not by the SDK
        _groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=60.0)
    return _groq_client


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _gemini_client


def _is_rate_limit(error: Exception) -> bool:
    return getattr(error, "status_code", None) == 429 or "RateLimit" in type(error).__name__ \
        or "429" in str(error)[:200] or "RESOURCE_EXHAUSTED" in str(error)[:300]


async def summarize_with_gemini(title: str, content: str) -> Optional[str]:
    """Generates a neutral 3-sentence summary using Google Gemini (Google AI Studio)."""
    if not settings.GEMINI_API_KEY:
        return None
    limiter = get_limiter("gemini")
    try:
        async with limiter.slot():
            prompt = (
                "You are an objective news editor. Write a concise, 3-sentence factual summary in Italian "
                "highlighting the key facts and significance of the event. "
                "Return only the summary text without introduction.\n\n"
                f"Title: {title}\nContent: {content[:3000]}"
            )
            response = await _gemini().aio.models.generate_content(model=settings.GEMINI_MODEL, contents=prompt)
            return response.text.strip() if response.text else None
    except RateLimited as e:
        logger.info(f"Gemini skipped: {e}")
    except Exception as e:
        if _is_rate_limit(e):
            limiter.cooldown(retry_after_seconds(e, 60))
        logger.warning(f"Gemini summarization failed: {e}")
    return None


def _groq_extra_params(model: str) -> dict:
    # Reasoning models (e.g. openai/gpt-oss-20b) spend tokens thinking: keep it short so a
    # summary does not eat the tokens-per-minute quota, and leave room for the answer
    if "gpt-oss" in model:
        return {"reasoning_effort": "low", "max_completion_tokens": 700}
    return {"max_tokens": 300}


async def summarize_with_groq(title: str, content: str) -> Optional[str]:
    """Generates a neutral 3-sentence summary using Groq Cloud."""
    if not settings.GROQ_API_KEY:
        return None
    limiter = get_limiter("groq")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Title: {title}\nContent: {content[:3000]}"},
    ]
    try:
        async with limiter.slot():
            extra = _groq_extra_params(settings.GROQ_MODEL)
            try:
                response = await _groq().chat.completions.create(
                    messages=messages, model=settings.GROQ_MODEL, temperature=0.1, **extra)
            except Exception as e:
                if getattr(e, "status_code", None) == 400 and extra:
                    # A model that does not accept these parameters: retry once without them
                    response = await _groq().chat.completions.create(
                        messages=messages, model=settings.GROQ_MODEL, temperature=0.1)
                else:
                    raise
            text = response.choices[0].message.content if response.choices else None
            return text.strip() if text and text.strip() else None
    except RateLimited as e:
        logger.info(f"Groq skipped: {e}")
    except Exception as e:
        if _is_rate_limit(e):
            get_limiter("groq").cooldown(retry_after_seconds(e, 30))
        logger.warning(f"Groq summarization failed: {e}")
    return None


async def summarize_with_ollama(title: str, content: str) -> Optional[str]:
    """Generates a neutral summary using a local Ollama instance (e.g. Qwen2.5 1.5B or Phi3)."""
    try:
        async with get_limiter("ollama").slot():
            async with httpx.AsyncClient(timeout=60.0) as client:
                payload = {
                    "model": settings.OLLAMA_MODEL,
                    "prompt": (
                        f"Sei un giornalista di precisione. Riassumi questa notizia in 3 frasi concise in italiano.\n\n"
                        f"Titolo: {title}\nContenuto: {content[:2000]}\n\nRiassunto:"
                    ),
                    "stream": False
                }
                res = await client.post(f"{settings.OLLAMA_BASE_URL}/api/generate", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("response", "").strip() or None
    except Exception as e:
        logger.debug(f"Ollama summarization failed or server not running: {e}")
    return None

async def summarize_article(title: str, content: str) -> str:
    """
    Summarizes an article using the configured or best available provider
    (Google Gemini -> Groq -> Ollama -> Fallback).
    """
    providers = {
        "gemini": summarize_with_gemini,
        "groq": summarize_with_groq,
        "ollama": summarize_with_ollama,
    }
    preferred = settings.SUMMARIZER_PROVIDER.lower()
    # Preferred provider first, then the auto fallback chain (each provider tried at most once)
    order = ([preferred] if preferred in providers else []) + [p for p in providers if p != preferred]
    for name in order:
        summary = await providers[name](title, content)
        if summary:
            return summary
    
    # Fallback: clean excerpt from raw content
    clean_text = content.strip() if content else title
    sentences = clean_text.split(". ")
    fallback_summary = ". ".join(sentences[:3])
    if not fallback_summary.endswith("."):
        fallback_summary += "..."
    return fallback_summary