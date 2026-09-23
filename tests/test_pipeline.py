import asyncio
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.ai.typesafe_evaluator import calculate_composite_score, evaluate_article_dimensions
from backend.ai.summarizer import summarize_article
from backend.config import settings

async def run_checks():
    print("=== 1. Testing Composite Scoring Formula ===")
    score1 = calculate_composite_score(authority=0.9, tech_depth=0.8, urgency=0.5, clickbait=0.0)
    print(f"Quality tech article score: {score1}")
    assert 0.6 <= score1 <= 1.0, f"Expected high score, got {score1}"
    
    score2 = calculate_composite_score(authority=0.4, tech_depth=0.2, urgency=0.3, clickbait=0.9)
    print(f"Clickbait article score: {score2}")
    assert score2 < score1, "Clickbait article must score lower than quality article"
    
    print("\n=== 2. Testing Multidimensional Evaluator (TypeSafe / Fallback) ===")
    sample_title = "Quantum Breakthrough: New Superconducting Qubit Architecture Achieves 99.9% Fidelity"
    sample_content = (
        "Researchers have unveiled a novel topological qubit design that significantly mitigates environmental decoherence. "
        "The peer-reviewed paper demonstrates continuous operation without error amplification."
    )
    
    dims = await evaluate_article_dimensions(sample_title, "Nature Physics", sample_content)
    print(f"Dimensions evaluated: {dims}")
    assert "composite_score" in dims
    assert "category" in dims
    assert dims["authority_score"] >= 0.5
    assert dims["clickbait_score"] <= 0.5
    
    print("\n=== 3. Testing Summarizer Engine ===")
    summary = await summarize_article(sample_title, sample_content)
    print(f"Summary output: {summary}")
    assert len(summary) > 10
    
    print("\n[SUCCESS] All pipeline checks passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_checks())
