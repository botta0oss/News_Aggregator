import logging
import httpx
from typing import Optional
from backend.config import settings

logger = logging.getLogger(__name__)

async def summarize_with_gemini(title: str, content: str) -> Optional[str]:
    """Generates a neutral 3-sentence summary using Google Gemini (Google AI Studio)."""
    if not settings.GEMINI_API_KEY:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        
        prompt = (
            "You are an objective news editor. Write a concise, 3-sentence factual summary in Italian "
            "highlighting the key facts and significance of the event. "
            "Return only the summary text without introduction.\n\n"
            f"Title: {title}\nContent: {content[:3000]}"
        )
        
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
        return response.text.strip() if response.text else None
    except Exception as e:
        logger.warning(f"Gemini summarization failed: {e}")
        return None

async def summarize_with_groq(title: str, content: str) -> Optional[str]:
    """Generates a neutral 3-sentence summary using Groq Cloud."""
    if not settings.GROQ_API_KEY:
        return None
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        
        response = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are an objective news editor. Write a concise 3-sentence factual summary in Italian."},
                {"role": "user", "content": f"Title: {title}\nContent: {content[:3000]}"}
            ],
            model=settings.GROQ_MODEL,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq summarization failed: {e}")
        return None

async def summarize_with_ollama(title: str, content: str) -> Optional[str]:
    """Generates a neutral summary using a local Ollama instance (e.g. Qwen2.5 1.5B or Phi3)."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
                return data.get("response", "").strip()
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