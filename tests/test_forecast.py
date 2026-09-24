import pytest
import math
from backend.config import settings
from backend.markets.forecast import (
    blend_probability, brier_score, calibrate, compute_signal, fit_platt, kelly_fraction, logit, pool,
    pool_distribution, sigmoid,
)


def lo_pool(model, market, w):
    return sigmoid(w * logit(model) + (1 - w) * logit(market))


def test_blend_shrinks_toward_market():
    assert blend_probability(0.9, 0.5, evidence_strength=0.0, max_weight=0.5) == 0.5
    assert blend_probability(0.9, 0.5, evidence_strength=1.0, max_weight=0.5) == pytest.approx(0.75)  # sqrt(9)/(1+sqrt(9))
    assert blend_probability(0.9, 0.5, 1.0, 0.5, method="linear") == pytest.approx(0.7)
    # Log-odds pooling is more decisive than the linear average, and exact at w = 0 and w = 1
    assert pool(0.95, 0.6, 0.5) > pool(0.95, 0.6, 0.5, method="linear")
    assert pool(0.3, 0.6, 1.0) == pytest.approx(0.3) and pool(0.3, 0.6, 0.0) == 0.6


def test_platt_calibration_and_fit():
    assert calibrate(0.8, 0.0, 1.0) == 0.8
    assert calibrate(0.8, 0.0, 2.0) == pytest.approx(sigmoid(2 * logit(0.8)))   # b > 1 sharpens
    assert calibrate(0.8, 0.0, 0.5) < 0.8                                        # b < 1 softens
    # Over-confident forecaster: says 90% / 10% but is right 70% of the time -> b < 1
    pairs = [(0.9, i % 10 < 7) for i in range(200)] + [(0.1, i % 10 >= 7) for i in range(200)]
    a, b = fit_platt(pairs)
    assert b < 1 and abs(a) < 0.05
    assert calibrate(0.9, a, b) == pytest.approx(0.7, abs=0.02)
    # Few cases: the ridge keeps it near the identity
    assert fit_platt([(0.9, False)]) != (0.0, 1.0) and fit_platt([]) == (0.0, 1.0)


def test_distribution_pool_is_normalised():
    out = pool_distribution([0.7, 0.2, 0.1], [0.4, 0.4, 0.2], 0.5)
    assert sum(out) == pytest.approx(1.0)
    raw = [math.sqrt(0.7 * 0.4), math.sqrt(0.2 * 0.4), math.sqrt(0.1 * 0.2)]
    assert out[0] == pytest.approx(raw[0] / sum(raw))
    assert pool_distribution([0.7, 0.3], [0.4, 0.6], 0.0) == [0.4, 0.6]


def test_kelly():
    assert kelly_fraction(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_fraction(0.4, 0.5) == 0.0
    assert kelly_fraction(0.9, 1.0) == 0.0


def test_signal_buy_yes():
    s = compute_signal(0.9, 0.5, evidence_strength=1.0, min_edge=0.05, min_evidence=0.5, kelly_scale=0.25)
    assert s.signal == "BUY_YES"
    assert s.blended_probability == pytest.approx(0.75)
    assert s.edge == pytest.approx(0.25)
    assert s.kelly_fraction == pytest.approx(0.25 * 0.5)


def test_signal_buy_no():
    s = compute_signal(0.1, 0.6, evidence_strength=1.0, min_edge=0.05, min_evidence=0.5, kelly_scale=1.0)
    assert s.signal == "BUY_NO"
    blended = lo_pool(0.1, 0.6, 0.5)
    assert s.edge == pytest.approx(blended - 0.6, abs=1e-4)
    # NO price 0.4, P(NO) = 1 - blended -> (P(NO) - 0.4) / 0.6
    assert s.kelly_fraction == pytest.approx((1 - blended - 0.4) / 0.6, abs=1e-3)


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
    assert s.blended_probability == pytest.approx(lo_pool(0.8, 0.35, 0.375), abs=1e-4)


def test_signal_uses_the_calibrated_jev(monkeypatch):
    monkeypatch.setattr(settings, "JEV_CALIB_B", 0.5)
    s = compute_signal(0.8, 0.35, evidence_strength=0.75)
    assert s.calibrated_probability == pytest.approx(sigmoid(0.5 * logit(0.8)), abs=1e-4)
    assert s.blended_probability == pytest.approx(lo_pool(s.calibrated_probability, 0.35, 0.375), abs=1e-3)
