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


async def test_evaluator_heuristic_fallback():
    dims = await evaluate_article_dimensions(SAMPLE_TITLE, "Nature Physics", SAMPLE_CONTENT)
    assert dims["source"] == "heuristic"
    assert dims["authority_score"] >= 0.5
    assert dims["clickbait_score"] <= 0.5


async def test_evaluator_with_jev(jev_client):
    captured = jev_client(score_value=1.0, choice_index=4)  # 5th category = "Science"
    dims = await evaluate_article_dimensions(SAMPLE_TITLE, "Nature Physics", SAMPLE_CONTENT)
    assert dims["source"] == "jev"
    assert dims["category"] == "Science"
    assert dims["clickbait_score"] == 0.25  # score 1 of 0..4
    assert dims["category_confidence"] == 0.7
    body = captured[0]
    assert body["model"] == "jev-latest"
    assert body["state"]["title"] == SAMPLE_TITLE
    assert set(body["questions"]) == {"clickbait_level", "journalistic_authority", "technical_depth", "urgency", "category"}


async def test_summarizer_fallback_excerpt():
    summary = await summarize_article(SAMPLE_TITLE, SAMPLE_CONTENT)
    assert summary.startswith("Researchers have unveiled")
