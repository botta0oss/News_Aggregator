import logging
import re
from typing import Dict, Any
from backend.ai import jev

logger = logging.getLogger(__name__)

# Categories used for Choice evaluation
CATEGORIES = {
    "Technology": "Software, AI, hardware, cybersecurity, consumer electronics, tech industry",
    "Politics": "Government policy, legislation, elections, governance, public administration",
    "Economy": "Markets, finance, macroeconomics, business, inflation, trade",
    "Foreign Affairs": "International diplomacy, global conflicts, geopolitics, treaties",
    "Science": "Space exploration, healthcare, biology, physics, climate science, research",
    "Culture": "Arts, entertainment, society, lifestyle, media"
}

def calculate_composite_score(
    authority: float,
    tech_depth: float,
    urgency: float,
    clickbait: float,
    w_authority: float = 0.35,
    w_tech: float = 0.25,
    w_urgency: float = 0.25,
    w_clickbait: float = 0.40
) -> float:
    """
    Computes a weighted linear composite score with clickbait penalty.
    All inputs and outputs are normalized to [0.0, 1.0].
    """
    score = (
        (w_authority * authority) +
        (w_tech * tech_depth) +
        (w_urgency * urgency) -
        (w_clickbait * clickbait)
    )
    # Clamp between 0.0 and 1.0
    return max(0.0, min(1.0, round(score, 4)))

async def evaluate_article_dimensions(title: str, source_name: str, content: str) -> Dict[str, Any]:
    """
    Evaluates multi-dimensional quality and relevance scores using TypeSafe Jev (System One).
    Returns normalized scores (0.0 to 1.0) for each dimension and chosen category.
    """
    if jev.is_enabled():
        try:
            from typesafe_sdk import Score, Choice
            
            client = jev.get_client()
            state = {
                "title": title,
                "source": source_name,
                "content": content[:2500] if content else title
            }
            
            questions = {
                "clickbait_level": Score(
                    instructions="How much sensationalism, curiosity gap, or exaggeration is in this headline/article?",
                    criteria=[
                        "Factual, accurate, and sober title; zero clickbait",
                        "Clear and engaging title with minor stylistic hook",
                        "Moderate sensation, question headline, or mild curiosity gap",
                        "Hyperbolic, exaggerated claims, or misleading context",
                        "Pure clickbait: outrage bait, deceptive, or unsubstantiated hype"
                    ]
                ),
                "journalistic_authority": Score(
                    instructions="How authoritative, verified, and well-sourced is this news item?",
                    criteria=[
                        "Unverified blog, personal opinion, or rumor without named sources",
                        "Secondary aggregation citing third-party reports without original data",
                        "Standard news report referencing known organizations or official sources",
                        "Deep investigative piece, direct on-the-record quotes, or verifiable data",
                        "Primary institutional announcement, official decree, or peer-reviewed finding"
                    ]
                ),
                "technical_depth": Score(
                    instructions="What is the depth of technical, quantitative, or domain-specific analysis?",
                    criteria=[
                        "General high-level buzzword coverage for the non-technical public",
                        "Introductory overview with basic explanations of terms",
                        "Structured discussion with practical mechanisms, data, and context",
                        "Advanced technical breakdown with architecture, code, or methodology",
                        "Expert/specialist-level deep dive"
                    ]
                ),
                "urgency": Score(
                    instructions="How temporally critical, time-sensitive, or breaking is this event?",
                    criteria=[
                        "Evergreen, historical overview, or timeless analysis",
                        "Routine periodic update or standard scheduled event",
                        "Recent development impacting the next 24-48 hours",
                        "Significant breaking news with actively developing impact",
                        "Extraordinary global event requiring immediate attention"
                    ]
                ),
                "category": Choice(
                    instructions="What is the primary macro-category of this news story?",
                    criteria=CATEGORIES
                )
            }
            
            response = await client.system_one(state=state, questions=questions)
            
            # Each Score primitive has 5 criteria levels (index 0 to 4), normalize by dividing by 4.0
            clickbait_norm = response.scores["clickbait_level"].score / 4.0
            authority_norm = response.scores["journalistic_authority"].score / 4.0
            tech_depth_norm = response.scores["technical_depth"].score / 4.0
            urgency_norm = response.scores["urgency"].score / 4.0
            category_answer = response.choices["category"]
            category_chosen = category_answer.choice if category_answer.choice in CATEGORIES else "Culture"
            
            composite = calculate_composite_score(
                authority=authority_norm,
                tech_depth=tech_depth_norm,
                urgency=urgency_norm,
                clickbait=clickbait_norm
            )
            
            return {
                "clickbait_score": round(clickbait_norm, 3),
                "authority_score": round(authority_norm, 3),
                "technical_depth_score": round(tech_depth_norm, 3),
                "urgency_score": round(urgency_norm, 3),
                "composite_score": round(composite, 3),
                "category": category_chosen,
                "category_confidence": round(category_answer.confidence, 3),
                "source": "jev"
            }
        except Exception as e:
            logger.warning(f"TypeSafe evaluation error, falling back to heuristic: {e}")

    # Heuristic fallback (when offline or API key is not configured)
    return _heuristic_evaluation(title, source_name, content)

def _has_word(text: str, words) -> bool:
    """Whole-word / phrase match (avoids e.g. "ai" matching "said" or "rate" matching "corporate")."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)

def _heuristic_evaluation(title: str, source_name: str, content: str) -> Dict[str, Any]:
    """Fallback heuristic scorer when offline or API key is unset."""
    title_lower = title.lower()
    content = content or ""
    
    # Clickbait signals
    clickbait_words = ["shocking", "you won't believe", "secret", "revealed", "insane", "magic", "miracle", "top 10"]
    clickbait_matches = sum(1 for w in clickbait_words if _has_word(title_lower, [w]))
    clickbait_norm = min(1.0, clickbait_matches * 0.3)
    
    # Authority signals
    trusted_sources = ["reuters", "bbc", "al jazeera", "bloomberg", "nature", "ieee", "arxiv", "financial times"]
    is_trusted = any(s in source_name.lower() for s in trusted_sources)
    authority_norm = 0.85 if is_trusted else 0.55
    
    # Tech depth signals
    tech_words = ["algorithm", "model", "neural", "gpu", "architecture", "framework", "kernel", "quantum", "protocol"]
    head = title_lower + " " + content.lower()[:500]
    tech_matches = sum(1 for w in tech_words if _has_word(head, [w]))
    tech_depth_norm = min(1.0, 0.3 + tech_matches * 0.15)
    
    # Urgency
    urgency_words = ["breaking", "just in", "urgent", "live", "emergency", "alert", "launches"]
    is_urgent = _has_word(title_lower, urgency_words)
    urgency_norm = 0.85 if is_urgent else 0.40
    
    # Category detection
    if _has_word(title_lower, ["ai", "chip", "chips", "software", "google", "apple", "nvidia", "cyber", "openai", "microsoft"]):
        category = "Technology"
    elif _has_word(title_lower, ["election", "parliament", "senate", "minister", "president", "policy", "congress", "vote"]):
        category = "Politics"
    elif _has_word(title_lower, ["stock", "stocks", "market", "markets", "inflation", "gdp", "bank", "rates", "economy", "dollar", "fed", "tariff", "tariffs"]):
        category = "Economy"
    elif _has_word(title_lower, ["space", "nasa", "health", "cancer", "vaccine", "climate", "quantum", "study", "researchers", "qubit"]):
        category = "Science"
    elif _has_word(title_lower, ["war", "ceasefire", "treaty", "nato", "un", "sanctions", "diplomat", "diplomatic", "embassy", "invasion"]):
        category = "Foreign Affairs"
    else:
        category = "Culture"
        
    composite = calculate_composite_score(
        authority=authority_norm,
        tech_depth=tech_depth_norm,
        urgency=urgency_norm,
        clickbait=clickbait_norm
    )
    
    return {
        "clickbait_score": round(clickbait_norm, 3),
        "authority_score": round(authority_norm, 3),
        "technical_depth_score": round(tech_depth_norm, 3),
        "urgency_score": round(urgency_norm, 3),
        "composite_score": round(composite, 3),
        "category": category,
        "category_confidence": None,
        "source": "heuristic"
    }
