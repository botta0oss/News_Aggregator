"""Second opinion on a forecast from a free model (Gemini, Groq).

Jev's errors and a different model's errors are only partly the same: when a free model,
given the same question, rules and news (and, like Jev, not the price), puts the probability
on the other side of the price, the edge is more likely a Jev error than an opportunity.
Buys then need both to agree (betting/portfolio.py, reason `second_opinion`).
"""
import json
import logging
import re
from typing import Optional

from backend.ai import usage
from backend.ai.ratelimit import RateLimited, get_limiter, retry_after_seconds
from backend.config import settings

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "You are a careful superforecaster. Estimate the probability that the prediction market below "
    "resolves YES, according to its exact resolution rules and end date.\n"
    "First think of the base rate: how often events of this kind happen in a similar period. Then "
    "adjust for the specific news, moving far from the base rate only with strong, reliable evidence "
    "that meets the rules before the end date. Be calibrated, avoid extreme values without decisive evidence.\n"
    'Answer ONLY with JSON: {"base_rate": <0-1>, "probability": <0-1>, "reason": "<one sentence>"}'
)


def build_prompt(state: dict) -> str:
    """The same state Jev gets (market, rules, dates, news), without the price."""
    return f"{INSTRUCTIONS}\n\n{json.dumps(state, ensure_ascii=False, default=str)[:12000]}"


def parse_probability(text: Optional[str]) -> Optional[float]:
    """The probability from the answer: the JSON field, else a "probability" number or percentage."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            value = json.loads(match.group(0)).get("probability")
            if value is not None:
                return _fraction(float(value))
        except (ValueError, AttributeError, TypeError):
            pass
    match = re.search(r"probability\"?\s*[:=]\s*([0-9]*\.?[0-9]+)\s*(%?)", text, re.I)
    if match:
        value = float(match.group(1))
        return _fraction(value / 100 if match.group(2) else value)
    return None


def _fraction(value: float) -> Optional[float]:
    if value > 1 and value <= 100:
        value /= 100            # a percentage written without the sign
    if not 0 <= value <= 1:
        return None
    return min(0.99, max(0.01, value))


async def _ask_gemini(prompt: str) -> Optional[str]:
    if not settings.GEMINI_API_KEY:
        return None
    from backend.ai.summarizer import _gemini, _is_rate_limit
    limiter = get_limiter("gemini")
    try:
        await usage.check("gemini")
        async with limiter.slot():
            response = await _gemini().aio.models.generate_content(model=settings.GEMINI_MODEL, contents=prompt)
            meta = getattr(response, "usage_metadata", None)
            await usage.record("gemini", getattr(meta, "prompt_token_count", 0), getattr(meta, "candidates_token_count", 0))
            return response.text
    except RateLimited as e:
        logger.info(f"Second opinion from Gemini skipped: {e}")
    except Exception as e:
        if _is_rate_limit(e):
            limiter.cooldown(retry_after_seconds(e, 60))
        await usage.record("gemini", ok=False)
        logger.warning(f"Second opinion from Gemini failed: {e}")
    return None


async def _ask_groq(prompt: str) -> Optional[str]:
    if not settings.GROQ_API_KEY:
        return None
    from backend.ai.summarizer import _groq, _is_rate_limit
    limiter = get_limiter("groq")
    extra = {"reasoning_effort": "low", "max_completion_tokens": 900} if "gpt-oss" in settings.GROQ_MODEL else {"max_tokens": 200}
    try:
        await usage.check("groq")
        async with limiter.slot():
            response = await _groq().chat.completions.create(
                messages=[{"role": "user", "content": prompt}], model=settings.GROQ_MODEL, temperature=0.1, **extra)
            tokens = getattr(response, "usage", None)
            await usage.record("groq", getattr(tokens, "prompt_tokens", 0), getattr(tokens, "completion_tokens", 0))
            return response.choices[0].message.content if response.choices else None
    except RateLimited as e:
        logger.info(f"Second opinion from Groq skipped: {e}")
    except Exception as e:
        if _is_rate_limit(e):
            limiter.cooldown(retry_after_seconds(e, 30))
        await usage.record("groq", ok=False)
        logger.warning(f"Second opinion from Groq failed: {e}")
    return None


PROVIDERS = {"gemini": _ask_gemini, "groq": _ask_groq}


def is_available() -> bool:
    keys = {"gemini": settings.GEMINI_API_KEY, "groq": settings.GROQ_API_KEY}
    return settings.SECOND_OPINION_ENABLED and any(keys.get(p) for p in providers())


def providers() -> list[str]:
    return [p.strip().lower() for p in settings.SECOND_OPINION_PROVIDERS.split(",") if p.strip().lower() in PROVIDERS]


async def ask(state: dict) -> Optional[tuple[float, str]]:
    """(P(YES), provider) from the first provider that gives a usable answer; None if none does."""
    if not settings.SECOND_OPINION_ENABLED:
        return None
    prompt = build_prompt(state)
    for name in providers():
        p = parse_probability(await PROVIDERS[name](prompt))
        if p is not None:
            return p, name
    return None


def agrees(p_second: Optional[float], price: Optional[float], signal: str) -> Optional[bool]:
    """Does the second opinion put the probability on the same side of the price as the signal?
    None when there is nothing to compare."""
    if p_second is None or price is None or signal not in ("BUY_YES", "BUY_NO"):
        return None
    margin = settings.SECOND_OPINION_MARGIN
    return p_second > price + margin if signal == "BUY_YES" else p_second < price - margin
