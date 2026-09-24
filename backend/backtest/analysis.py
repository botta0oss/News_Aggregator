"""Backtest metrics and parameter suggestions (pure functions over the evaluated cases)."""
import math
from typing import Iterable, Optional

from backend.config import settings

MIN_BETS_FOR_THRESHOLD = 10


def _mean(values: list) -> Optional[float]:
    return round(sum(values) / len(values), 5) if values else None


def brier(pairs: Iterable[tuple[float, bool]]) -> Optional[float]:
    return _mean([(p - (1.0 if y else 0.0)) ** 2 for p, y in pairs])


def log_loss(pairs: Iterable[tuple[float, bool]]) -> Optional[float]:
    eps = 1e-4
    return _mean([-math.log(max(eps, p if y else 1 - p)) for p, y in pairs])


def metrics(cases: list[dict]) -> dict:
    """Accuracy of price, Jev and blend, and how the simulated bets did."""
    signals = [c for c in cases if c.get("signal") in ("BUY_YES", "BUY_NO")]
    hits = [c for c in signals if (c["signal"] == "BUY_YES") == c["resolved_yes"]]
    bets = [c for c in cases if c.get("verdict") in ("GO", "SMALL") and c.get("outlay")]
    staked = sum(c["outlay"] for c in bets)
    pnl = sum(c["pnl"] or 0.0 for c in bets)
    return {
        "n": len(cases),
        "brier_market": brier((c["price"], c["resolved_yes"]) for c in cases),
        "brier_model": brier((c["model_probability"], c["resolved_yes"]) for c in cases),
        "brier_blended": brier((c["blended_probability"], c["resolved_yes"]) for c in cases),
        "logloss_market": log_loss((c["price"], c["resolved_yes"]) for c in cases),
        "logloss_model": log_loss((c["model_probability"], c["resolved_yes"]) for c in cases),
        "signals": len(signals),
        "signal_hit_rate": round(len(hits) / len(signals), 4) if signals else None,
        "bets": len(bets),
        "bets_won": sum(1 for c in bets if (c["side"] == "YES") == c["resolved_yes"]),
        "staked": round(staked, 2),
        "pnl": round(pnl, 2),
        "roi": round(pnl / staked, 4) if staked else None,
    }


def calibration_bins(cases: list[dict], key: str, bins: int = 10) -> list[dict]:
    """Reliability diagram: mean forecast vs observed frequency of YES, per probability bin."""
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        inside = [c for c in cases if lo <= c[key] < hi or (i == bins - 1 and c[key] == 1.0)]
        if inside:
            out.append({"lo": lo, "hi": hi, "n": len(inside),
                        "mean_forecast": _mean([c[key] for c in inside]),
                        "frequency": round(sum(1 for c in inside if c["resolved_yes"]) / len(inside), 4)})
    return out


def blend(case: dict, max_weight: float) -> float:
    w = max_weight * max(0.0, min(1.0, case["evidence_strength"]))
    return w * case["model_probability"] + (1 - w) * case["price"]


def flat_stake_result(cases: list[dict], max_weight: float, min_edge: float, min_evidence: float) -> dict:
    """1 $ on every signal, bought at the price of the moment plus half the typical spread."""
    cost_extra = settings.DEFAULT_SPREAD / 2
    bets = wins = 0
    profit = 0.0
    for c in cases:
        if c["evidence_strength"] < min_evidence:
            continue
        edge = blend(c, max_weight) - c["price"]
        if abs(edge) < min_edge:
            continue
        yes = edge > 0
        cost = min(0.99, (c["price"] if yes else 1 - c["price"]) + cost_extra)
        won = yes == c["resolved_yes"]
        bets += 1
        wins += won
        profit += (1 / cost - 1) if won else -1.0
    return {"bets": bets, "wins": wins, "profit": round(profit, 3), "roi": round(profit / bets, 4) if bets else None}


def suggest(cases: list[dict]) -> Optional[dict]:
    """Model weight that minimises the Brier score of the blend, then the edge threshold with the best result."""
    if not cases:
        return None
    current_w, current_edge = settings.MODEL_WEIGHT_MAX, settings.MIN_EDGE
    weights = [round(i / 20, 2) for i in range(21)]
    scores = {w: brier((blend(c, w), c["resolved_yes"]) for c in cases) for w in weights}
    best_w = min(weights, key=lambda w: (scores[w], abs(w - current_w)))
    current_score = brier((blend(c, current_w), c["resolved_yes"]) for c in cases)

    edges = [round(e / 100, 2) for e in range(2, 16)]
    table = [{"min_edge": e, **flat_stake_result(cases, best_w, e, settings.MIN_EVIDENCE)} for e in edges]
    eligible = [r for r in table if r["bets"] >= MIN_BETS_FOR_THRESHOLD]
    best_edge = max(eligible, key=lambda r: (r["profit"], r["min_edge"]))["min_edge"] if eligible else None

    n = len(cases)
    return {
        "n": n,
        "confidence": "bassa" if n < 30 else "media" if n < 100 else "alta",
        "model_weight_max": {"current": current_w, "suggested": best_w,
                             "brier_current": current_score, "brier_suggested": scores[best_w],
                             "curve": [{"w": w, "brier": scores[w]} for w in weights]},
        "min_edge": {"current": current_edge, "suggested": best_edge, "table": table,
                     "current_result": flat_stake_result(cases, best_w, current_edge, settings.MIN_EVIDENCE)},
    }


def multi_metrics(cases: list[dict]) -> Optional[dict]:
    """Multi-outcome events: multi-class Brier, how often the favourite won, simulated bets."""
    if not cases:
        return None
    d = [c["details"] for c in cases]
    signals = [c for c in cases if c.get("signal") == "BUY_YES"]
    hits = [c for c in signals if c["details"].get("best") == c["details"].get("winner")]
    bets = [c for c in cases if c.get("verdict") in ("GO", "SMALL") and c.get("outlay")]
    staked = sum(c["outlay"] for c in bets)
    pnl = sum(c["pnl"] or 0.0 for c in bets)
    return {
        "n": len(cases),
        "brier_market": _mean([x["brier_market"] for x in d]),
        "brier_model": _mean([x["brier_model"] for x in d]),
        "brier_blended": _mean([x["brier_blended"] for x in d]),
        "winner_prob_market": _mean([c["price"] for c in cases]),
        "winner_prob_model": _mean([c["model_probability"] for c in cases]),
        "winner_prob_blended": _mean([c["blended_probability"] for c in cases]),
        "favourite_right_market": round(sum(1 for x in d if x["market_top_right"]) / len(d), 4),
        "favourite_right_model": round(sum(1 for x in d if x["model_top_right"]) / len(d), 4),
        "signals": len(signals),
        "signal_hit_rate": round(len(hits) / len(signals), 4) if signals else None,
        "bets": len(bets),
        "bets_won": sum(1 for c in bets if (c["pnl"] or 0) > 0),
        "staked": round(staked, 2), "pnl": round(pnl, 2), "roi": round(pnl / staked, 4) if staked else None,
    }


def summarize(cases: list[dict]) -> dict:
    multi = [c for c in cases if c.get("kind") == "multi" and c["status"] == "ok"]
    cases_all = cases
    cases = [c for c in cases if c.get("kind", "binary") == "binary"]
    ok = [c for c in cases if c["status"] == "ok"]
    horizons = sorted({c["horizon_days"] for c in ok})
    categories = sorted({c["category"] or "Altro" for c in ok})
    skipped = {}
    for c in cases_all:
        if c["status"] != "ok":
            skipped[c["note"] or c["status"]] = skipped.get(c["note"] or c["status"], 0) + 1
    return {
        "overall": metrics(ok),
        "by_horizon": [{"horizon_days": h, **metrics([c for c in ok if c["horizon_days"] == h])} for h in horizons],
        "by_category": [{"category": k, **metrics([c for c in ok if (c["category"] or "Altro") == k])} for k in categories],
        "calibration": {"model": calibration_bins(ok, "model_probability"),
                        "blended": calibration_bins(ok, "blended_probability"),
                        "market": calibration_bins(ok, "price")},
        "suggestion": suggest(ok),
        "skipped": skipped,
        "multi": multi_metrics(multi),
    }
