import logging
import re
from typing import Any, Dict, Optional
from backend.ai import jev
from backend.ai.ratelimit import RateLimited

logger = logging.getLogger(__name__)

# Categories for the Choice question. Aligned with the main prediction-market areas; each
# description says what belongs there and what doesn't, which is what the model needs to
# separate neighbouring categories (e.g. a Fed decision is Economy, not Politics).
CATEGORIES = {
    "Politics": "Domestic politics: elections, polls, campaigns, parliaments and congress, legislation, "
                "government appointments, courts ruling on political cases. Not international diplomacy.",
    "Economy": "Macroeconomics and markets: central banks and interest rates, inflation, jobs data, GDP, "
               "stocks, bonds, commodities, trade and tariffs, company earnings, mergers. Not crypto.",
    "Crypto": "Cryptocurrencies and blockchain: Bitcoin, Ethereum, stablecoins, crypto ETFs, exchanges, "
              "token prices, crypto regulation and enforcement.",
    "Technology": "Technology industry: AI models and labs, chips, software, big tech companies and their "
                  "products, cybersecurity, telecoms, space launches by companies.",
    "Foreign Affairs": "International relations: wars and ceasefires, diplomacy, summits, sanctions, treaties, "
                       "military operations, relations between countries.",
    "Science": "Science, health and climate: research findings, medicine, pandemics, public health, "
               "climate and weather events, space science.",
    "Sports": "Sports: matches, tournaments, results, transfers, athletes, leagues and championships.",
    "Culture": "Culture and society: entertainment, film, music, celebrities, awards, media, religion, "
               "lifestyle, crime stories without political weight.",
}

REGIONS = {
    "North America": "United States, Canada, Mexico",
    "Europe": "European Union, United Kingdom, Russia, Ukraine, other European countries",
    "Middle East & Africa": "Middle East, Israel, Iran, Gulf states, Turkey, Africa",
    "Asia-Pacific": "China, Japan, India, Korea, Taiwan, Southeast Asia, Australia",
    "Latin America": "Central and South America, Caribbean",
    "Global": "Worldwide, several regions, or no specific region (e.g. crypto markets, global tech)",
}

MARKET_RELEVANCE_CRITERIA = [
    "No link to any verifiable future event people could bet on",
    "Background context only; unlikely to change any forecast",
    "Relevant to a future event (election, rate decision, conflict, ruling, price level, match) but not decisive",
    "Clearly shifts the likelihood of a specific, verifiable future event",
    "Directly decides or nearly decides the outcome of a specific future event",
]


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


def build_jev_request(title: str, source_name: str, content: str, source_hint: Optional[str] = None):
    from typesafe_sdk import Choice, Noul, Score

    state = {
        "title": title,
        "source": source_name,
        "content": content[:2500] if content else title,
    }
    if source_hint:
        state["source_usual_topic"] = source_hint  # a hint only: the article itself decides

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
            instructions="What is the primary topic of this news story? Choose by what the story is mainly about, "
                         "not by the outlet that published it.",
            criteria=CATEGORIES,
        ),
        "region": Choice(
            instructions="Which geographic region is this story mainly about?",
            criteria=REGIONS,
        ),
        "is_opinion": Noul(
            instructions="Is this an opinion piece, editorial, column, analysis/explainer or sponsored content, "
                         "rather than a news report of facts?",
        ),
        "market_relevance": Score(
            instructions="How much could this news change the probability of a specific, verifiable future event "
                         "that people bet on in prediction markets (elections, central bank decisions, economic "
                         "data, wars and ceasefires, court rulings, crypto prices, sports results, company events)?",
            criteria=MARKET_RELEVANCE_CRITERIA,
        ),
    }
    return state, questions


async def evaluate_article_dimensions(
    title: str, source_name: str, content: str, source_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluates quality scores and classification with TypeSafe Jev (System One), in one call.
    Falls back to local heuristics without an API key or on errors.
    """
    if jev.is_enabled():
        try:
            state, questions = build_jev_request(title, source_name, content, source_hint)
            response = await jev.system_one(state, questions)

            # Score answers are expected values over 0..(levels-1): normalize to 0..1
            def norm(name: str) -> float:
                return response.scores[name].score / (len(questions[name].criteria) - 1)

            clickbait_norm = norm("clickbait_level")
            authority_norm = norm("journalistic_authority")
            tech_depth_norm = norm("technical_depth")
            urgency_norm = norm("urgency")
            category_answer = response.choices["category"]
            region_answer = response.choices["region"]

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
                "category": category_answer.choice if category_answer.choice in CATEGORIES else "Culture",
                "category_confidence": round(category_answer.confidence, 3),
                "region": region_answer.choice if region_answer.choice in REGIONS else "Global",
                "is_opinion": round(response.nouls["is_opinion"].noul, 3),
                "market_relevance": round(norm("market_relevance"), 3),
                "source": "jev",
            }
        except RateLimited:
            raise  # the caller retries this article on the next run instead of downgrading it
        except Exception as e:
            if getattr(e, "status_code", None) == 429 or type(e).__name__ == "TypeSafeRateLimitError":
                raise RateLimited("jev", 30)
            logger.warning(f"TypeSafe evaluation error, falling back to heuristic: {e}")

    return _heuristic_evaluation(title, source_name, content, source_hint)


# ---------------------------------------------------------------------------
# Heuristic fallback (no API key, or the API failed)
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "Politics": ["election", "elections", "poll", "polls", "parliament", "senate", "congress", "minister", "president",
                 "governor", "campaign", "vote", "votes", "voters", "ballot", "democrat", "democrats", "republican",
                 "republicans", "gop", "white house", "legislation", "bill", "impeachment", "primary", "candidate",
                 "elezioni", "governo", "parlamento", "premier", "partito"],
    "Economy": ["fed", "federal reserve", "ecb", "central bank", "interest rate", "interest rates", "rate cut",
                "rate hike", "inflation", "cpi", "gdp", "jobs report", "payrolls", "unemployment", "recession",
                "stocks", "stock", "shares", "nasdaq", "s&p", "dow", "bond", "bonds", "yields", "treasury", "tariff",
                "tariffs", "trade", "earnings", "revenue", "profit", "ipo", "merger", "acquisition", "oil", "gold",
                "dollar", "euro", "economy", "economic", "bce", "inflazione", "borsa", "tassi"],
    "Crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "blockchain", "stablecoin",
               "stablecoins", "token", "tokens", "solana", "xrp", "binance", "coinbase", "defi", "nft", "altcoin",
               "memecoin", "sec crypto", "spot etf", "halving", "satoshi", "web3"],
    "Technology": ["ai", "artificial intelligence", "openai", "anthropic", "chatgpt", "llm", "chip", "chips",
                   "semiconductor", "nvidia", "apple", "google", "microsoft", "meta", "amazon", "tesla", "software",
                   "cyber", "cyberattack", "hack", "hackers", "smartphone", "iphone", "startup", "spacex", "robot",
                   "quantum computing", "data center"],
    "Foreign Affairs": ["war", "ceasefire", "truce", "treaty", "nato", "united nations", "sanctions", "diplomat",
                        "diplomatic", "diplomacy", "embassy", "invasion", "troops", "missile", "missiles", "strike",
                        "airstrike", "hostages", "summit", "border", "ukraine", "russia", "israel", "gaza", "iran",
                        "china", "taiwan", "guerra", "tregua"],
    "Science": ["study", "researchers", "scientists", "research", "nasa", "space", "vaccine", "virus", "outbreak",
                "cancer", "health", "disease", "climate", "hurricane", "earthquake", "wildfire", "heatwave",
                "species", "quantum", "qubit", "telescope", "medicine", "drug", "fda", "world health organization"],
    "Sports": ["match", "game", "season", "league", "cup", "championship", "final", "tournament", "coach", "player",
               "players", "goal", "goals", "win", "wins", "beat", "beats", "defeat", "nba", "nfl", "mlb", "nhl",
               "premier league", "champions league", "serie a", "la liga", "f1", "formula 1", "grand prix",
               "tennis", "wimbledon", "olympics", "world cup", "super bowl", "transfer", "striker", "quarterback"],
    "Culture": ["film", "movie", "music", "album", "singer", "actor", "actress", "celebrity", "oscar", "oscars",
                "grammy", "festival", "series", "netflix", "book", "art", "museum", "fashion", "royal", "pope"],
}

REGION_KEYWORDS = {
    "North America": ["u.s.", "united states", "america", "american", "washington", "trump", "biden",
                      "congress", "senate", "white house", "canada", "mexico", "fed", "federal reserve", "new york",
                      "california", "texas", "nfl", "nba", "mlb"],
    "Europe": ["eu", "european", "europe", "uk", "britain", "british", "london", "germany", "berlin", "france",
               "paris", "italy", "italia", "rome", "roma", "spain", "ecb", "bce", "brussels", "ukraine", "russia",
               "kremlin", "moscow", "kyiv", "nato", "premier league", "serie a"],
    "Middle East & Africa": ["israel", "gaza", "iran", "iraq", "syria", "lebanon", "saudi", "yemen", "qatar",
                             "turkey", "egypt", "africa", "nigeria", "south africa", "sudan", "houthi", "hamas"],
    "Asia-Pacific": ["china", "beijing", "japan", "tokyo", "india", "korea", "taiwan", "hong kong", "singapore",
                     "australia", "indonesia", "vietnam", "philippines", "pakistan"],
    "Latin America": ["brazil", "argentina", "venezuela", "colombia", "chile", "peru", "cuba", "latin america"],
}

MARKET_KEYWORDS = ["election", "poll", "polls", "vote", "fed", "rate", "rates", "inflation", "cpi", "jobs report",
                   "gdp", "ceasefire", "truce", "court", "ruling", "verdict", "supreme court", "bitcoin", "etf",
                   "price", "championship", "final", "playoff", "earnings", "ipo", "launch", "deadline", "shutdown",
                   "nominee", "nomination", "indictment", "resign", "resigns", "tariff", "sanctions", "recession",
                   "approval", "odds", "forecast", "by december", "by year-end"]

OPINION_MARKERS = ["opinion", "analysis", "comment", "editorial", "column", "explainer", "what we know", "why ",
                   "how ", "the case for", "the case against", "we must", "podcast", "review:", "sponsored"]

CLICKBAIT_WORDS = ["shocking", "you won't believe", "secret", "revealed", "insane", "magic", "miracle", "top 10",
                   "this is why", "what happened next", "jaw-dropping", "must see"]
TRUSTED_SOURCES = ["reuters", "bbc", "al jazeera", "bloomberg", "nature", "ieee", "arxiv", "financial times",
                   "npr", "guardian", "federal reserve", "bce", "ecb", "ansa", "politico", "cnbc", "dw", "france 24"]
TECH_WORDS = ["algorithm", "model", "neural", "gpu", "architecture", "framework", "kernel", "quantum", "protocol",
              "data", "percent", "basis points", "methodology", "study"]
URGENCY_WORDS = ["breaking", "just in", "urgent", "live", "emergency", "alert", "launches", "announces", "today"]


def _count(text: str, words) -> int:
    """Whole-word / phrase matches (avoids e.g. "ai" matching "said" or "rate" matching "corporate")."""
    return sum(1 for w in words if re.search(rf"(?<![\w.]){re.escape(w)}(?![\w])", text))


def _has_word(text: str, words) -> bool:
    return _count(text, words) > 0


def _best(scores: Dict[str, float], default: str) -> tuple[str, float]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return default, 0.0
    top, total = ranked[0][1], sum(v for _, v in ranked)
    return ranked[0][0], round(top / total, 3)


def _heuristic_evaluation(title: str, source_name: str, content: str, source_hint: Optional[str] = None) -> Dict[str, Any]:
    """Keyword scoring: title matches count double, the source's usual topic breaks ties."""
    title_l = title.lower()
    body_l = (content or "").lower()[:1500]

    category_scores = {cat: 2.0 * _count(title_l, kw) + _count(body_l, kw) for cat, kw in CATEGORY_KEYWORDS.items()}
    if source_hint in category_scores:
        category_scores[source_hint] += 1.5
    category, _ = _best(category_scores, source_hint or "Culture")

    region_scores = {r: 2.0 * _count(title_l, kw) + _count(body_l, kw) for r, kw in REGION_KEYWORDS.items()}
    region, _ = _best(region_scores, "Global")

    clickbait_norm = min(1.0, _count(title_l, CLICKBAIT_WORDS) * 0.3 + (0.1 if title.strip().endswith("?") else 0.0))
    authority_norm = 0.85 if any(s in source_name.lower() for s in TRUSTED_SOURCES) else 0.55
    tech_depth_norm = min(1.0, 0.3 + _count(title_l + " " + body_l[:500], TECH_WORDS) * 0.15)
    urgency_norm = 0.85 if _has_word(title_l, URGENCY_WORDS) else 0.40
    is_opinion = 0.8 if _has_word(title_l, OPINION_MARKERS) or title_l.startswith(("why", "how")) else 0.15
    market_relevance = min(1.0, 0.1 + 0.2 * _count(title_l, MARKET_KEYWORDS) + 0.05 * _count(body_l, MARKET_KEYWORDS))
    if is_opinion > 0.5:
        market_relevance *= 0.7

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
        "region": region,
        "is_opinion": is_opinion,
        "market_relevance": round(market_relevance, 3),
        "source": "heuristic",
    }
