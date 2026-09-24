"""Pure functions turning a Jev probability into a betting signal.

Market prices on liquid prediction markets are usually well calibrated, so the raw
Jev probability is not traded directly:
1. it is recalibrated (Platt scaling fitted on resolved markets: logit p' = a + b · logit p);
2. it is pooled with the market price in log-odds, with a weight proportional to how much
   the news evidence actually informs the outcome. Log-odds pooling keeps a confident,
   well-supported estimate from being diluted the way a linear average does (a linear pool
   of calibrated forecasts is under-confident).
"""
import math
from dataclasses import dataclass
from typing import Iterable, Optional
from backend.config import settings

EPS = 1e-6
P_CLIP = 1e-4


@dataclass
class Signal:
    blended_probability: float
    model_weight: float    # w: share of the Jev estimate in the blend
    edge: float
    signal: str            # BUY_YES / BUY_NO / HOLD
    kelly_fraction: float  # suggested fraction of bankroll (already scaled by KELLY_FRACTION)
    calibrated_probability: Optional[float] = None  # Jev after Platt scaling


def logit(p: float) -> float:
    p = min(1 - P_CLIP, max(P_CLIP, p))
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def model_weight(evidence_strength: float, max_weight: Optional[float] = None) -> float:
    """w = max_weight * evidence_strength, clamped to [0, 1]."""
    max_weight = settings.MODEL_WEIGHT_MAX if max_weight is None else max_weight
    return max(0.0, min(1.0, max_weight * max(0.0, min(1.0, evidence_strength))))


def calibrate(p: float, a: Optional[float] = None, b: Optional[float] = None) -> float:
    """Platt scaling of the Jev probability; a = 0, b = 1 leaves it unchanged."""
    a = settings.JEV_CALIB_A if a is None else a
    b = settings.JEV_CALIB_B if b is None else b
    if a == 0 and b == 1:
        return p
    return sigmoid(a + b * logit(p))


def pool(model_p: float, market_p: float, w: float, method: Optional[str] = None) -> float:
    """Combines two probabilities: log-odds (default) or linear average, weight w on the model."""
    method = method or settings.BLEND_METHOD
    if w <= 0:
        return market_p
    if method == "linear":
        return w * model_p + (1.0 - w) * market_p
    return sigmoid(w * logit(model_p) + (1.0 - w) * logit(market_p))


def blend_probability(model_p: float, market_p: float, evidence_strength: float, max_weight: Optional[float] = None,
                      calib: Optional[tuple] = None, method: Optional[str] = None) -> float:
    """Calibrated Jev pooled with the market, w = max_weight × evidence_strength."""
    w = model_weight(evidence_strength, max_weight)
    a, b = calib if calib is not None else (None, None)
    return pool(calibrate(model_p, a, b), market_p, w, method)


def pool_distribution(model: list[float], market: list[float], w: float, method: Optional[str] = None) -> list[float]:
    """Multi-outcome version: geometric (log-linear) pool p_i ∝ model_i^w · market_i^(1−w), or linear."""
    method = method or settings.BLEND_METHOD
    if method == "linear" or w <= 0:
        return [w * m + (1 - w) * q for m, q in zip(model, market)]
    raw = [math.exp(w * math.log(max(P_CLIP, m)) + (1 - w) * math.log(max(P_CLIP, q))) for m, q in zip(model, market)]
    total = sum(raw) or 1.0
    return [r / total for r in raw]


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
        calibrated_probability=round(calibrate(model_p), 4),
    )


def fit_platt(pairs: list[tuple[float, bool]], ridge: float = 1.0) -> tuple[float, float]:
    """Platt scaling (a, b) by logistic regression of the outcome on logit(p), with a small
    ridge penalty pulling toward the identity (a = 0, b = 1) so few cases cannot overfit.
    Newton's method with step halving (plain Newton can overshoot on logistic losses)."""
    xs = [(logit(p), 1.0 if y else 0.0) for p, y in pairs]
    if not xs:
        return 0.0, 1.0

    def loss(a, b):
        total = 0.5 * ridge * (a * a + (b - 1) ** 2)
        for x, y in xs:
            z = a + b * x
            # log(1 + e^z) - y·z, computed stably
            total += (z if z > 0 else 0.0) + math.log1p(math.exp(-abs(z))) - y * z
        return total

    a, b = 0.0, 1.0
    current = loss(a, b)
    for _ in range(100):
        ga, gb = ridge * a, ridge * (b - 1)
        haa, hab, hbb = ridge, 0.0, ridge
        for x, y in xs:
            q = sigmoid(a + b * x)
            r, wq = q - y, q * (1 - q)
            ga += r
            gb += r * x
            haa += wq
            hab += wq * x
            hbb += wq * x * x
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        step = 1.0
        while step > 1e-6:
            na, nb = a - step * da, b - step * db
            new = loss(na, nb)
            if new <= current:
                break
            step /= 2
        else:
            break
        converged = abs(current - new) < 1e-10
        a, b, current = na, nb, new
        if converged:
            break
    return round(max(-3.0, min(3.0, a)), 4), round(max(0.2, min(3.0, b)), 4)


def brier_score(pairs: Iterable[tuple[float, bool]]) -> Optional[float]:
    """Mean squared error between predicted P(YES) and the realized outcome (lower is better)."""
    pairs = list(pairs)
    if not pairs:
        return None
    return round(sum((p - (1.0 if outcome else 0.0)) ** 2 for p, outcome in pairs) / len(pairs), 4)
