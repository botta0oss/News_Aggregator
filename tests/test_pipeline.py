from backend.ai.typesafe_evaluator import calculate_composite_score, evaluate_article_dimensions, _heuristic_evaluation
from backend.ai.summarizer import summarize_article

SAMPLE_TITLE = "Quantum Breakthrough: New Superconducting Qubit Architecture Achieves 99.9% Fidelity"
SAMPLE_CONTENT = (
    "Researchers have unveiled a novel topological qubit design that significantly mitigates environmental decoherence. "
    "The peer-reviewed paper demonstrates continuous operation without error amplification."
)


def test_composite_score_penalizes_clickbait():
    quality = calculate_composite_score(authority=0.9, tech_depth=0.8, urgency=0.5, clickbait=0.0)
    clickbait = calculate_composite_score(authority=0.4, tech_depth=0.2, urgency=0.3, clickbait=0.9)
    assert 0.6 <= quality <= 1.0
    assert clickbait < quality
    assert 0.0 <= clickbait


def test_heuristic_category_uses_whole_words():
    # "said" contains "ai" and "corporate" contains "rate": must not be misclassified
    assert _heuristic_evaluation("Minister said the corporate plan is fine", "X", "")["category"] == "Politics"
    assert _heuristic_evaluation("Nvidia unveils new AI chip", "X", "")["category"] == "Technology"
    assert _heuristic_evaluation(SAMPLE_TITLE, "Nature Physics", SAMPLE_CONTENT)["category"] == "Science"
    assert _heuristic_evaluation("Ceasefire talks resume in Gaza", "X", "")["category"] == "Foreign Affairs"


def test_heuristic_new_categories_region_and_signals():
    crypto = _heuristic_evaluation("Bitcoin slips below $90,000 as ETF outflows mount", "CoinDesk", "")
    assert crypto["category"] == "Crypto" and crypto["region"] == "Global"
    sport = _heuristic_evaluation("Arsenal beat Chelsea to go top of the Premier League", "BBC Sport", "")
    assert sport["category"] == "Sports" and sport["region"] == "Europe"
    fed = _heuristic_evaluation("Fed signals December rate cut as inflation cools", "CNBC", "")
    assert fed["category"] == "Economy" and fed["region"] == "North America"
    assert fed["market_relevance"] > sport["market_relevance"]
    opinion = _heuristic_evaluation("Why the Senate stopgap bill matters", "Politico", "")
    assert opinion["is_opinion"] > 0.5 and fed["is_opinion"] < 0.5
    # The source's usual topic breaks ties when the text has no signal
    assert _heuristic_evaluation("Weekly roundup", "CoinDesk", "", source_hint="Crypto")["category"] == "Crypto"


async def test_evaluator_heuristic_fallback():
    dims = await evaluate_article_dimensions(SAMPLE_TITLE, "Nature Physics", SAMPLE_CONTENT)
    assert dims["source"] == "heuristic"
    assert dims["authority_score"] >= 0.5
    assert dims["clickbait_score"] <= 0.5


async def test_evaluator_with_jev(jev_client):
    captured = jev_client(score_value=1.0, choice_index=5, noul_value=0.2)  # 6th category = "Science"
    dims = await evaluate_article_dimensions(SAMPLE_TITLE, "Nature Physics", SAMPLE_CONTENT, source_hint="Science")
    assert dims["source"] == "jev"
    assert dims["category"] == "Science"
    assert dims["clickbait_score"] == 0.25  # score 1 of 0..4
    assert dims["market_relevance"] == 0.25
    assert dims["category_confidence"] == 0.7
    assert dims["is_opinion"] == 0.2
    assert dims["region"] == "Global"  # 6th region
    body = captured[0]
    assert body["model"] == "jev-latest"
    assert body["state"]["title"] == SAMPLE_TITLE
    assert body["state"]["source_usual_topic"] == "Science"
    assert set(body["questions"]) == {
        "clickbait_level", "journalistic_authority", "technical_depth", "urgency",
        "category", "region", "is_opinion", "market_relevance",
    }
    assert "Sports" in body["questions"]["category"]["criteria"]


async def test_summarizer_fallback_excerpt():
    summary = await summarize_article(SAMPLE_TITLE, SAMPLE_CONTENT)
    assert summary.startswith("Researchers have unveiled")
