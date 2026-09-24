"""Backtest metrics and parameter suggestions (pure functions over the evaluated cases)."""
import math
import random
from typing import Iterable, Optional

from backend.config import settings
from backend.markets.forecast import blend_probability, fit_platt

MIN_BETS_FOR_THRESHOLD = 10
MIN_TEST_MARKETS = 10     # markets needed on each side of the chronological split to validate
TEST_SHARE = 0.3          # share of the (latest) markets kept aside to check the suggestions
BOOTSTRAP_REPS = 1000


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
        "markets": distinct_markets(cases),
        # Brier gain over the price (> 0 = better than the market), 95% interval by market
        "gain_model": brier_gain(cases, "model_probability"),
        "gain_blended": brier_gain(cases, "blended_probability"),
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


def blend(case: dict, max_weight: float, calib: Optional[tuple] = None) -> float:
    """The live blend (calibrated Jev pooled with the price) with other parameters."""
    return blend_probability(case["model_probability"], case["price"], case["evidence_strength"], max_weight,
                             calib=calib or (settings.JEV_CALIB_A, settings.JEV_CALIB_B))


def flat_stake_result(cases: list[dict], max_weight: float, min_edge: float, min_evidence: float,
                      calib: Optional[tuple] = None) -> dict:
    """1 $ on every signal, bought at the price of the moment plus half the typical spread."""
    cost_extra = settings.DEFAULT_SPREAD / 2
    bets = wins = 0
    profit = 0.0
    for c in cases:
        if c["evidence_strength"] < min_evidence:
            continue
        edge = blend(c, max_weight, calib) - c["price"]
        if abs(edge) < min_edge:
            continue
        yes = edge > 0
        cost = min(0.99, (c["price"] if yes else 1 - c["price"]) + cost_extra)
        won = yes == c["resolved_yes"]
        bets += 1
        wins += won
        profit += (1 / cost - 1) if won else -1.0
    return {"bets": bets, "wins": wins, "profit": round(profit, 3), "roi": round(profit / bets, 4) if bets else None}


# ---------- Uncertainty: bootstrap by market ----------

def cluster_bootstrap(cases: list[dict], value, reps: int = BOOTSTRAP_REPS, seed: int = 7) -> Optional[dict]:
    """Mean of value(case) with a 95% interval, resampling whole markets.

    The horizons of one market share the outcome, so they are not independent: resampling
    cases one by one would give intervals that are too narrow.
    """
    groups: dict = {}
    for c in cases:
        v = value(c)
        if v is not None:
            groups.setdefault(c.get("market_id") or id(c), []).append(v)
    if not groups:
        return None
    keys = list(groups)
    sums = [(sum(groups[k]), len(groups[k])) for k in keys]
    total = sum(s for s, _ in sums) / sum(n for _, n in sums)
    if len(keys) < 2:
        return {"mean": round(total, 5), "lo": None, "hi": None, "markets": len(keys)}
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        s = n = 0
        for _ in keys:
            gs, gn = sums[rng.randrange(len(keys))]
            s += gs
            n += gn
        means.append(s / n)
    means.sort()
    return {"mean": round(total, 5), "lo": round(means[int(0.025 * reps)], 5), "hi": round(means[int(0.975 * reps) - 1], 5),
            "markets": len(keys)}


def brier_gain(cases: list[dict], key: str) -> Optional[dict]:
    """Brier of the price minus Brier of `key`, per case (> 0: `key` did better), with its interval."""
    def gain(c):
        y = 1.0 if c["resolved_yes"] else 0.0
        return (c["price"] - y) ** 2 - (c[key] - y) ** 2
    return cluster_bootstrap(cases, gain)


def distinct_markets(cases: list[dict]) -> int:
    return len({c.get("market_id") or id(c) for c in cases})


def confidence(n_markets: int) -> str:
    return "bassa" if n_markets < 30 else "media" if n_markets < 100 else "alta"


# ---------- Suggested parameters, validated out of sample ----------

def chronological_split(cases: list[dict], test_share: float = TEST_SHARE) -> tuple[list, list]:
    """Earlier-resolving markets to fit, later ones to test (all horizons of a market stay together)."""
    ends = {}
    for c in cases:
        key = c.get("market_id") or id(c)
        end = c.get("end_date") or c.get("as_of")
        ends[key] = max(ends.get(key, end), end) if end is not None else ends.get(key)
    order = sorted(ends, key=lambda k: (ends[k] is None, ends[k] or 0))
    n_test = int(round(len(order) * test_share))
    test_keys = set(order[len(order) - n_test:]) if n_test else set()
    train = [c for c in cases if (c.get("market_id") or id(c)) not in test_keys]
    test = [c for c in cases if (c.get("market_id") or id(c)) in test_keys]
    return train, test


def fit(cases: list[dict]) -> dict:
    """Platt scaling of Jev, then the model weight minimising the Brier score of the blend,
    then the edge threshold with the best flat-stake result (if enough bets)."""
    calib = fit_platt([(c["model_probability"], c["resolved_yes"]) for c in cases])
    weights = [round(i / 20, 2) for i in range(21)]
    current_w = settings.MODEL_WEIGHT_MAX
    scores = {w: brier((blend(c, w, calib), c["resolved_yes"]) for c in cases) for w in weights}
    best_w = min(weights, key=lambda w: (scores[w], abs(w - current_w)))
    edges = [round(e / 100, 2) for e in range(2, 16)]
    table = [{"min_edge": e, **flat_stake_result(cases, best_w, e, settings.MIN_EVIDENCE, calib)} for e in edges]
    eligible = [r for r in table if r["bets"] >= MIN_BETS_FOR_THRESHOLD]
    best_edge = max(eligible, key=lambda r: (r["profit"], r["min_edge"]))["min_edge"] if eligible else None
    return {"calib": calib, "w": best_w, "edge": best_edge, "curve": [{"w": w, "brier": scores[w]} for w in weights],
            "table": table}


def suggest(cases: list[dict]) -> Optional[dict]:
    """Parameters fitted on the earlier markets and checked on the later ones.

    A value is recommended only if it also does better on markets it was not fitted on;
    the recommended values are then refitted on all the markets.
    """
    if not cases:
        return None
    current = {"calib": (settings.JEV_CALIB_A, settings.JEV_CALIB_B), "w": settings.MODEL_WEIGHT_MAX,
               "edge": settings.MIN_EDGE}
    n_markets = distinct_markets(cases)
    train, test = chronological_split(cases)
    validated = distinct_markets(test) >= MIN_TEST_MARKETS and distinct_markets(train) >= MIN_TEST_MARKETS
    full = fit(cases)

    if validated:
        tr = fit(train)
        b_cur = brier((blend(c, current["w"], current["calib"]), c["resolved_yes"]) for c in test)
        b_new = brier((blend(c, tr["w"], tr["calib"]), c["resolved_yes"]) for c in test)
        blend_better = b_new is not None and b_cur is not None and b_new < b_cur
        edge_cur = flat_stake_result(test, tr["w"], current["edge"], settings.MIN_EVIDENCE, tr["calib"])
        edge_new = flat_stake_result(test, tr["w"], tr["edge"], settings.MIN_EVIDENCE, tr["calib"]) if tr["edge"] else None
        edge_better = edge_new is not None and edge_new["bets"] > 0 and edge_new["profit"] > edge_cur["profit"]
        validation = {"train_markets": distinct_markets(train), "test_markets": distinct_markets(test),
                      "brier_test_current": b_cur, "brier_test_suggested": b_new,
                      "edge_test_current": edge_cur, "edge_test_suggested": edge_new}
    else:
        blend_better = edge_better = False
        validation = {"train_markets": distinct_markets(train), "test_markets": distinct_markets(test)}

    brier_current = brier((blend(c, current["w"], current["calib"]), c["resolved_yes"]) for c in cases)
    brier_full = brier((blend(c, full["w"], full["calib"]), c["resolved_yes"]) for c in cases)
    return {
        "n": len(cases),
        "markets": n_markets,
        "confidence": confidence(n_markets),
        "validated": validated,
        "validation": validation,
        "calibration": {"current": {"a": current["calib"][0], "b": current["calib"][1]},
                        "suggested": {"a": full["calib"][0], "b": full["calib"][1]},
                        "recommended": blend_better},
        "model_weight_max": {"current": current["w"], "suggested": full["w"], "recommended": blend_better,
                             "brier_current": brier_current, "brier_suggested": brier_full, "curve": full["curve"]},
        "min_edge": {"current": current["edge"], "suggested": full["edge"], "recommended": edge_better,
                     "table": full["table"],
                     "current_result": flat_stake_result(cases, full["w"], current["edge"], settings.MIN_EVIDENCE, full["calib"])},
    }


def multi_metrics(cases: list[dict]) -> Optional[dict]:
    """Multi-outcome events: multi-class Brier, how often the favourite won, simulated bets."""
    if not cases:
        return None
    d = [c["details"] for c in cases]
    signals = [c for c in cases if c.get("signal") in ("BUY_YES", "BUY_NO")]
    hits = [c for c in signals
            if (c["details"].get("best") == c["details"].get("winner")) == (c["signal"] == "BUY_YES")]
    bets = [c for c in cases if c.get("verdict") in ("GO", "SMALL") and c.get("outlay")]
    staked = sum(c["outlay"] for c in bets)
    pnl = sum(c["pnl"] or 0.0 for c in bets)
    return {
        "n": len(cases),
        "markets": distinct_markets(cases),
        "gain_model": cluster_bootstrap(cases, lambda c: c["details"]["brier_market"] - c["details"]["brier_model"]),
        "gain_blended": cluster_bootstrap(cases, lambda c: c["details"]["brier_market"] - c["details"]["brier_blended"]),
        "winner_listed": round(sum(1 for x in d if x.get("winner_listed", True)) / len(d), 4),
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
    sources = {}
    for c in cases_all:
        if c["status"] == "ok":
            src = (c.get("details") or {}).get("news_source") or "google"
            sources[src] = sources.get(src, 0) + 1
    archive = [c for c in ok if (c.get("details") or {}).get("news_source") == "archive"]
    return {
        "overall": metrics(ok),
        "news_sources": sources,
        # Cases whose news came only from the app's own archive: no later article can leak in
        "leak_free": metrics(archive) if archive else None,
        "by_horizon": [{"horizon_days": h, **metrics([c for c in ok if c["horizon_days"] == h])} for h in horizons],
        "by_category": [{"category": k, **metrics([c for c in ok if (c["category"] or "Altro") == k])} for k in categories],
        "calibration": {"model": calibration_bins(ok, "model_probability"),
                        "blended": calibration_bins(ok, "blended_probability"),
                        "market": calibration_bins(ok, "price")},
        "suggestion": suggest(ok),
        "skipped": skipped,
        "multi": multi_metrics(multi),
    }
