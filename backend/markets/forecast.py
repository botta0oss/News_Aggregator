"""Pure functions turning a Jev probability into a betting signal.

Market prices on liquid prediction markets are usually well calibrated, so the raw
Jev probability is not traded directly: it is shrunk toward the market price with a
weight proportional to how much the news evidence actually informs the outcome.
"""
from dataclasses import dataclass
from typing import Iterable, Optional
from backend.config import settings

EPS = 1e-6


@dataclass
class Signal:
    blended_probability: float
    model_weight: float    # w: share of the Jev estimate in the blend
    edge: float
    signal: str            # BUY_YES / BUY_NO / HOLD
    kelly_fraction: float  # suggested fraction of bankroll (already scaled by KELLY_FRACTION)


def model_weight(evidence_strength: float, max_weight: Optional[float] = None) -> float:
    """w = max_weight * evidence_strength, clamped to [0, 1]."""
    max_weight = settings.MODEL_WEIGHT_MAX if max_weight is None else max_weight
    return max(0.0, min(1.0, max_weight * max(0.0, min(1.0, evidence_strength))))


def blend_probability(model_p: float, market_p: float, evidence_strength: float, max_weight: Optional[float] = None) -> float:
    """Linear pool: w * model + (1 - w) * market, with w = max_weight * evidence_strength."""
    w = model_weight(evidence_strength, max_weight)
    return w * model_p + (1.0 - w) * market_p


def kelly_fraction(p: float, price: float) -> float:
    """Full-Kelly stake for buying a binary share at `price` that pays 1 with probability `p`."""
    if price <= EPS or price >= 1.0 - EPS:
        return 0.0
    return max(0.0, (p - price) / (1.0 - price))


def compute_signal(
    model_p: float,
    market_p: float,
    evidence_strength: float,
    min_edge: Optional[float] = None,
    min_evidence: Optional[float] = None,
    kelly_scale: Optional[float] = None,
) -> Signal:
    min_edge = settings.MIN_EDGE if min_edge is None else min_edge
    min_evidence = settings.MIN_EVIDENCE if min_evidence is None else min_evidence
    kelly_scale = settings.KELLY_FRACTION if kelly_scale is None else kelly_scale

    blended = blend_probability(model_p, market_p, evidence_strength)
    edge = blended - market_p

    signal, stake = "HOLD", 0.0
    if evidence_strength >= min_evidence and abs(edge) >= min_edge:
        if edge > 0:
            signal, stake = "BUY_YES", kelly_fraction(blended, market_p)
        else:
            # Buying NO at (1 - price) with win probability (1 - p)
            signal, stake = "BUY_NO", kelly_fraction(1.0 - blended, 1.0 - market_p)

    return Signal(
        blended_probability=round(blended, 4),
        model_weight=round(model_weight(evidence_strength), 4),
        edge=round(edge, 4),
        signal=signal,
        kelly_fraction=round(stake * kelly_scale, 4),
    )


def brier_score(pairs: Iterable[tuple[float, bool]]) -> Optional[float]:
    """Mean squared error between predicted P(YES) and the realized outcome (lower is better)."""
    pairs = list(pairs)
    if not pairs:
        return None
    return round(sum((p - (1.0 if outcome else 0.0)) ** 2 for p, outcome in pairs) / len(pairs), 4)
