import pytest
from backend.markets.forecast import blend_probability, brier_score, compute_signal, kelly_fraction


def test_blend_shrinks_toward_market():
    assert blend_probability(0.9, 0.5, evidence_strength=0.0, max_weight=0.5) == 0.5
    assert blend_probability(0.9, 0.5, evidence_strength=1.0, max_weight=0.5) == pytest.approx(0.7)


def test_kelly():
    assert kelly_fraction(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_fraction(0.4, 0.5) == 0.0
    assert kelly_fraction(0.9, 1.0) == 0.0


def test_signal_buy_yes():
    s = compute_signal(0.9, 0.5, evidence_strength=1.0, min_edge=0.05, min_evidence=0.5, kelly_scale=0.25)
    assert s.signal == "BUY_YES"
    assert s.blended_probability == pytest.approx(0.7)
    assert s.edge == pytest.approx(0.2)
    assert s.kelly_fraction == pytest.approx(0.25 * 0.4)


def test_signal_buy_no():
    s = compute_signal(0.1, 0.6, evidence_strength=1.0, min_edge=0.05, min_evidence=0.5, kelly_scale=1.0)
    assert s.signal == "BUY_NO"
    assert s.edge == pytest.approx(-0.25)
    # NO price 0.4, P(NO) = 0.65 -> (0.65 - 0.4) / 0.6
    assert s.kelly_fraction == pytest.approx(0.25 / 0.6, abs=1e-4)


def test_signal_hold_on_weak_evidence_or_small_edge():
    assert compute_signal(0.9, 0.5, evidence_strength=0.25, min_evidence=0.5).signal == "HOLD"
    assert compute_signal(0.52, 0.5, evidence_strength=1.0, min_edge=0.05).signal == "HOLD"


def test_brier():
    assert brier_score([]) is None
    assert brier_score([(1.0, True), (0.0, False)]) == 0.0
    assert brier_score([(0.5, True), (0.5, False)]) == 0.25


def test_signal_exposes_model_weight():
    s = compute_signal(0.8, 0.35, evidence_strength=0.75)
    assert s.model_weight == pytest.approx(0.375)
    assert s.blended_probability == pytest.approx(0.375 * 0.8 + 0.625 * 0.35, abs=1e-4)
