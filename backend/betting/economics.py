"""Is a signal worth betting, and how much? Pure functions, no I/O.

Inputs are the forecast (blended probability and its uncertainty), what it costs to buy
(order book, fees), how long the money stays locked, the risk preset and the current
portfolio. The output says whether to bet, how much, at what maximum price, and why.
"""
import math
from dataclasses import dataclass, field, asdict
from typing import Optional
from backend.betting.fees import fee_per_share
from backend.betting.profiles import RiskProfile

MIN_ORDER_USD = 1.0


def _num(x: float, decimals: int = 0) -> str:
    """Italian number format: 50.000 / 1,9."""
    text = f"{x:,.{decimals}f}"
    return text.replace(",", "\u2009").replace(".", ",").replace("\u2009", ".")
MAX_APR = 10.0  # 1000%: very short horizons make the annualized figure meaningless beyond this


@dataclass
class Quote:
    """What it costs to buy one side. `asks` = [(price, shares)] cheapest first."""
    asks: list
    mid: float                    # current price of the side (implied probability)
    fee_bps: float = 0.0          # taker fee rate in basis points (fee per share = rate × p × (1 − p))
    min_order_shares: Optional[float] = None
    source: str = "book"          # book / estimate


@dataclass
class Exposure:
    market: float = 0.0
    event: float = 0.0
    category: float = 0.0
    total: float = 0.0


@dataclass
class Reason:
    code: str
    text: str
    blocking: bool = True


@dataclass
class Evaluation:
    verdict: str                  # GO / SMALL / NO
    side: str                     # YES / NO
    p_side: float                 # blended probability that the chosen side wins
    p_conservative: float         # p_side minus z * uncertainty
    sigma: float
    mid: float
    best_price: Optional[float]
    limit_price: float            # maximum price per share worth paying
    net_edge: Optional[float]     # p_conservative - (best price + fee), probability points
    hurdle_apr: float
    days: float
    stake: float                  # USD spent on shares
    fee: float
    outlay: float                 # stake + fee: the most you can lose
    shares: float
    avg_price: Optional[float]
    profit_if_win: float
    expected_profit: float        # with the blended probability
    expected_profit_conservative: float
    roi: Optional[float]          # expected_profit / outlay
    apr: Optional[float]          # annualized, conservative
    prob_loss: float
    break_even: Optional[float]   # minimum win probability for the bet to pay off
    kelly_stake: float            # full book-aware Kelly stake before caps
    target_stake: float           # after the preset's Kelly fraction
    caps: dict = field(default_factory=dict)
    limited_by: Optional[str] = None
    quote_source: str = "book"
    reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


# ---------- Building blocks ----------

def max_price_for(target: float, fee_bps: float) -> float:
    """Highest price x with x + fee(x) <= target (price plus fee is increasing in x)."""
    if target <= 0:
        return 0.0
    r = fee_bps / 10_000
    if r <= 1e-12:
        return min(target, 1.0)
    # r·x² − (1 + r)·x + target = 0, smaller root
    disc = (1 + r) ** 2 - 4 * r * target
    if disc < 0:
        return 1.0
    return min(1.0, ((1 + r) - math.sqrt(disc)) / (2 * r))


def model_sigma(p_jev: float, evidence: float, weight: float, pseudo_count: float, calibration_factor: float = 1.0) -> float:
    """Uncertainty of the blended probability.

    The Jev estimate is treated as worth `pseudo_count × evidence` observations (a Beta
    posterior); only its weighted part moves the blend, so the blend inherits w × sigma.
    `calibration_factor` > 1 widens it when past forecasts did worse than expected.
    """
    n = max(0.0, pseudo_count * max(0.0, min(1.0, evidence))) + 1.0
    sigma_jev = math.sqrt(max(p_jev * (1 - p_jev), 1e-4) / n)
    return weight * sigma_jev * calibration_factor


def buy(asks: list, budget: float, max_price: float = 1.0) -> tuple[float, float]:
    """Spends up to `budget` USD walking the asks up to `max_price`. Returns (shares, usd spent)."""
    shares = spent = 0.0
    for price, size in asks:
        if price > max_price or spent >= budget:
            break
        take = min(size, (budget - spent) / price)
        shares += take
        spent += take * price
    return shares, spent


def depth_usd(asks: list, max_price: float) -> float:
    return sum(price * size for price, size in asks if price <= max_price)


def annualize(roi: float, days: float) -> float:
    if roi <= -1:
        return -1.0
    years = max(days, 1.0) / 365.0
    # In logs: (1 + roi) ** 365 overflows for cheap shares close to expiry (e.g. roi 10 in 1 day)
    growth = math.log1p(roi) / years
    if growth >= math.log1p(MAX_APR):
        return MAX_APR
    return math.expm1(growth)


def _growth(p: float, shares: float, spent: float, fee: float, equity: float) -> float:
    """Expected log-growth of equity for a bet (Kelly criterion objective)."""
    outlay = spent + fee
    if outlay >= equity:
        return -math.inf
    win = 1 + (shares - outlay) / equity
    lose = 1 - outlay / equity
    return p * math.log(win) + (1 - p) * math.log(lose)


def kelly_stake(p: float, asks: list, fee_bps: float, equity: float, max_price: float) -> float:
    """Stake maximizing expected log growth, accounting for the price getting worse as you buy more."""
    top = min(depth_usd(asks, max_price), equity * 0.99)
    if top <= 0:
        return 0.0

    def g(stake):
        shares, spent = buy(asks, stake, max_price)
        fee = sum(fee_per_share(pr, fee_bps) * sz for pr, sz in _fills(asks, stake, max_price))
        return _growth(p, shares, spent, fee, equity)

    # Coarse grid then golden-section refinement (the objective is concave in practice)
    grid = [top * i / 200 for i in range(1, 201)]
    best = max(grid, key=g)
    if g(best) <= 0:
        return 0.0
    lo, hi = max(0.0, best - top / 200), min(top, best + top / 200)
    for _ in range(40):
        a, b = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if g(a) < g(b):
            lo = a
        else:
            hi = b
    return (lo + hi) / 2


def _fills(asks: list, budget: float, max_price: float) -> list:
    out, spent = [], 0.0
    for price, size in asks:
        if price > max_price or spent >= budget:
            break
        take = min(size, (budget - spent) / price)
        out.append((price, take))
        spent += take * price
    return out


def fill(asks: list, budget: float, max_price: float, fee_bps: float) -> tuple[float, float, float]:
    """(shares, spent, fee) for a budget."""
    fills = _fills(asks, budget, max_price)
    shares = sum(sz for _, sz in fills)
    spent = sum(pr * sz for pr, sz in fills)
    fee = sum(fee_per_share(pr, fee_bps) * sz for pr, sz in fills)
    return shares, spent, fee


# Synthetic book: share of the depth at each level, one spread apart (real books thin out
# away from the best price, so a large order pays progressively more)
ESTIMATED_LEVELS = (0.4, 0.3, 0.2, 0.1)


def estimated_quote(mid: float, spread: float, liquidity: float, fee_bps: float, min_order_shares=None) -> Quote:
    """Fallback without an order book: levels from mid + half spread upward, one spread apart,
    holding in total half the reported liquidity (in dollars)."""
    depth = max(0.0, liquidity * 0.5)
    asks = []
    for k, share in enumerate(ESTIMATED_LEVELS):
        price = round(min(0.99, mid + spread / 2 + k * max(spread, 0.01)), 4)
        if price <= 0 or depth <= 0:
            break
        if asks and price <= asks[-1][0]:
            asks[-1] = (asks[-1][0], asks[-1][1] + depth * share / price)  # capped at 0.99: merge
        else:
            asks.append((price, depth * share / price))
    return Quote(asks=asks, mid=mid, fee_bps=fee_bps, min_order_shares=min_order_shares, source="estimate")


# ---------- Evaluation ----------

def evaluate(
    *,
    signal: str,
    p_yes: float,
    sigma: float,
    quote: Quote,
    days: float,
    profile: RiskProfile,
    equity: float,
    available_cash: float,
    exposure: Exposure,
    liquidity: float,
    risk_free_rate: float,
) -> Evaluation:
    side = "NO" if signal == "BUY_NO" else "YES"
    p_side = p_yes if side == "YES" else 1.0 - p_yes
    p_cons = max(0.0, p_side - profile.z * sigma)
    # Most worth paying per share: price plus fee must leave the preset's minimum net edge
    limit_price = max(0.0, min(0.99, max_price_for(p_cons - profile.min_net_edge, quote.fee_bps)))
    hurdle = risk_free_rate + profile.min_apr_premium
    asks = quote.asks
    best = asks[0][0] if asks else None
    net_edge = (p_cons - (best + fee_per_share(best, quote.fee_bps))) if best is not None else None

    reasons: list[Reason] = []
    notes: list[str] = []
    if quote.source == "estimate":
        notes.append("Book non disponibile: prezzo stimato come prezzo medio + metà spread, profondità dalla liquidità dichiarata.")

    if signal == "HOLD":
        reasons.append(Reason("no_signal", "Il modello non vede una differenza sufficiente rispetto al prezzo."))
    if liquidity < profile.min_liquidity:
        reasons.append(Reason("illiquid", f"Mercato poco liquido ({_num(liquidity)} $, il preset chiede almeno {_num(profile.min_liquidity)} $)."))
    if days > profile.max_days:
        reasons.append(Reason("too_far", f"Si risolve tra {days:.0f} giorni: il preset accetta al massimo {profile.max_days} giorni."))
    if best is None:
        reasons.append(Reason("no_book", "Nessuna offerta di vendita disponibile per questo lato."))
    elif net_edge < profile.min_net_edge:
        reasons.append(Reason("edge_after_costs",
                              f"Dopo spread, commissioni e incertezza il margine è {_num(net_edge * 100, 1)} punti: ne servono almeno {_num(profile.min_net_edge * 100)}."))

    # Sizing
    caps = {
        "market": max(0.0, profile.max_market_frac * equity - exposure.market),
        "event": max(0.0, profile.max_event_frac * equity - exposure.event),
        "category": max(0.0, profile.max_category_frac * equity - exposure.category),
        "total": max(0.0, profile.max_total_frac * equity - exposure.total),
        "cash": max(0.0, available_cash),
        "book": profile.max_book_share * depth_usd(asks, limit_price),
    }
    k_full = kelly_stake(p_cons, asks, quote.fee_bps, equity, limit_price) if best is not None and equity > 0 else 0.0
    target = profile.kelly_scale * k_full
    limiting = min(caps, key=caps.get)
    stake = min(target, caps[limiting])
    limited_by = limiting if caps[limiting] < target else None

    if caps["market"] <= 0 or caps["event"] <= 0 or caps["category"] <= 0 or caps["total"] <= 0:
        reasons.append(Reason("exposure_cap", _cap_text(caps)))
    elif caps["cash"] <= 0:
        reasons.append(Reason("no_cash", "Capitale disponibile esaurito: è tutto investito in posizioni aperte."))

    shares, spent, fee = fill(asks, stake, limit_price, quote.fee_bps) if stake > 0 else (0.0, 0.0, 0.0)
    min_order = max(MIN_ORDER_USD, (quote.min_order_shares or 0) * (best or 0))
    if not [r for r in reasons if r.blocking] and spent < min_order:
        reasons.append(Reason("stake_below_min", f"La puntata calcolata ({_num(spent, 2)} $) è sotto l'ordine minimo ({_num(min_order, 2)} $)."))

    outlay = spent + fee
    avg_price = spent / shares if shares > 0 else None
    exp_profit = p_side * shares - outlay
    exp_profit_cons = p_cons * shares - outlay
    if outlay > 0:
        roi = exp_profit / outlay
        apr = annualize(exp_profit_cons / outlay, days)
    elif best is not None:
        unit_cost = best + fee_per_share(best, quote.fee_bps)
        roi = p_side / unit_cost - 1
        apr = annualize(p_cons / unit_cost - 1, days)
    else:
        roi = apr = None
    if apr is not None and apr < hurdle and signal != "HOLD" and best is not None:
        reasons.append(Reason("return_too_low",
                              f"Rendimento annualizzato prudente {_num(apr * 100)}%, sotto la soglia del {_num(hurdle * 100)}% "
                              f"(tasso senza rischio {_num(risk_free_rate * 100)}% + premio del preset)."))

    blocking = [r for r in reasons if r.blocking]
    if blocking:
        verdict = "NO"
    elif limited_by and stake < 0.5 * target:
        verdict = "SMALL"
        reasons.append(Reason("limited", _limited_text(limited_by), blocking=False))
    else:
        verdict = "GO"

    if verdict == "NO":
        shares = spent = fee = outlay = 0.0
        exp_profit = exp_profit_cons = 0.0

    return Evaluation(
        verdict=verdict, side=side, p_side=p_side, p_conservative=p_cons, sigma=sigma, mid=quote.mid,
        best_price=best, limit_price=limit_price, net_edge=net_edge, hurdle_apr=hurdle, days=days,
        stake=spent, fee=fee, outlay=outlay, shares=shares, avg_price=avg_price,
        profit_if_win=shares - outlay, expected_profit=exp_profit, expected_profit_conservative=exp_profit_cons,
        roi=roi, apr=apr, prob_loss=1 - p_side, break_even=(outlay / shares) if shares > 0 else None,
        kelly_stake=k_full, target_stake=target, caps=caps, limited_by=limited_by, quote_source=quote.source,
        reasons=[asdict(r) for r in reasons], notes=notes,
    )


CAP_LABELS = {
    "market": "limite per singolo mercato", "event": "limite per evento", "category": "limite per categoria",
    "total": "limite di capitale investito", "cash": "capitale disponibile", "book": "profondità del book",
}


def _cap_text(caps: dict) -> str:
    full = [CAP_LABELS[k] for k in ("market", "event", "category", "total") if caps[k] <= 0]
    return "Esposizione già al massimo: " + ", ".join(full) + "."


def _limited_text(key: str) -> str:
    return f"Puntata ridotta dal {CAP_LABELS[key]}: meno della metà di quella che Kelly suggerirebbe."
