"""What to do on a market, at what price, and why. Pure functions, no I/O.

The economic evaluation says whether buying now is worth it. The plan turns it into
instructions a person can follow:
- an action: buy YES / buy NO / wait / avoid / hold / sell / nothing;
- limit orders: the highest price worth paying, and the price at which selling beats holding;
- the price levels that would change the decision ("buy YES only below 31¢");
- reasons for and against, and how much to trust the whole thing.

The price levels move the market price and recompute the same blend the forecast uses
(the blended probability depends on the price too), so they are consistent with the signal.
"""
import math
from dataclasses import dataclass, field
from typing import Optional

from backend.betting.economics import annualize
from backend.betting.fees import fee_per_share
from backend.betting.profiles import RiskProfile
from backend.markets.forecast import pool

GRID = [round(0.01 + i * 0.005, 3) for i in range(197)]   # YES prices from 1¢ to 99¢
# Reasons that no price can fix: the market itself does not suit the preset or the portfolio
STRUCTURAL = {"illiquid", "too_far", "exposure_cap", "no_cash", "no_book"}
SIDE_IT = {"YES": "SÌ", "NO": "NO"}


def _cents(p: Optional[float]) -> str:
    if p is None:
        return "–"
    c = p * 100
    return f"{c:.1f}".replace(".", ",").removesuffix(",0") + "¢"


def _pts(x: float) -> str:
    return f"{abs(x) * 100:.1f}".replace(".", ",") + " punti"


def _pct(p: float) -> str:
    return f"{p * 100:.0f}%"


@dataclass
class Forecast:
    """The pieces of a forecast needed to recompute the blend at another price."""
    model: float                 # Jev, after calibration
    weight: float                # w of Jev in the blend
    evidence: float
    sigma: float                 # uncertainty of the blended probability
    method: str = "logodds"

    def p_yes(self, price_yes: float) -> float:
        return pool(self.model, price_yes, self.weight, self.method)


@dataclass
class Plan:
    action: str                   # BUY / WAIT / AVOID / HOLD / SELL / NONE
    side: Optional[str]           # YES / NO for BUY, HOLD, SELL and a WAIT with one side
    title: str
    summary: str
    orders: list = field(default_factory=list)
    levels: dict = field(default_factory=dict)
    pros: list = field(default_factory=list)
    cons: list = field(default_factory=list)
    exit: list = field(default_factory=list)
    confidence: str = "media"
    confidence_why: list = field(default_factory=list)
    code: Optional[str] = None    # why a SELL: target_reached / forecast_flipped

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("action", "side", "title", "summary", "orders", "levels", "pros",
                                               "cons", "exit", "confidence", "confidence_why", "code")}


# ---------- Price levels ----------

def _buy_ok(fc: Forecast, q: float, side: str, profile: RiskProfile, fee_bps: float, days: float,
            min_edge: float, hurdle: float) -> bool:
    """Would buying `side` be worth it if the YES price were q (and the side's ask its price)?"""
    p_yes = fc.p_yes(q)
    p_side, price = (p_yes, q) if side == "YES" else (1 - p_yes, 1 - q)
    if p_side - price < min_edge:                      # the signal itself
        return False
    cost = price + fee_per_share(price, fee_bps)
    p_cons = max(0.0, p_side - profile.z * fc.sigma)
    if p_cons - cost < profile.min_net_edge:           # margin after costs and uncertainty
        return False
    return annualize(p_cons / cost - 1, days) >= hurdle  # worth the time the money is locked


def buy_levels(fc: Forecast, profile: RiskProfile, fee_bps: float, days: float, min_edge: float,
               hurdle: float) -> dict:
    """Highest YES price at which buying YES is worth it, lowest at which buying NO is."""
    yes = [q for q in GRID if _buy_ok(fc, q, "YES", profile, fee_bps, days, min_edge, hurdle)]
    no = [q for q in GRID if _buy_ok(fc, q, "NO", profile, fee_bps, days, min_edge, hurdle)]
    return {"buy_yes_below": max(yes) if yes else None, "buy_no_above": min(no) if no else None}


def hold_value(fc: Forecast, price_side: float, side: str, days: float, hurdle: float) -> float:
    """What one share is worth to keep: its chance of paying 1 $, discounted for the time the
    money stays locked at the return the preset asks for."""
    q = price_side if side == "YES" else 1 - price_side
    p_side = fc.p_yes(q) if side == "YES" else 1 - fc.p_yes(q)
    return p_side / math.pow(1 + max(hurdle, 0.0), max(days, 0.0) / 365)


def sell_above(fc: Forecast, side: str, fee_bps: float, days: float, hurdle: float) -> Optional[float]:
    """Lowest bid for the side held at which selling beats holding (fee on the sale included)."""
    for x in GRID:
        if x - fee_per_share(x, fee_bps) >= hold_value(fc, x, side, days, hurdle):
            return x
    return None


# ---------- Reasons ----------

def _evidence_lines(tally: Optional[dict], side: str) -> tuple[list, list]:
    """News for and against the side, from Jev's reading of each article."""
    if not tally:
        return [], []
    good_key, bad_key = ("raises_yes", "lowers_yes") if side == "YES" else ("lowers_yes", "raises_yes")
    pros, cons = [], []
    for key, out in ((good_key, pros), (bad_key, cons)):
        n = tally.get(key, 0)
        if n:
            titles = tally.get(f"{key}_titles") or []
            text = f"{n} {'notizia va' if n == 1 else 'notizie vanno'} in questa direzione"
            if key == bad_key:
                text = f"{n} {'notizia va' if n == 1 else 'notizie vanno'} nella direzione opposta"
            if titles:
                text += ": «" + "», «".join(titles[:2]) + "»"
            out.append(text + ".")
    return pros, cons


def _track_lines(track: Optional[dict]) -> tuple[list, list, int]:
    """How the forecasts did so far. Returns pros, cons and a confidence adjustment."""
    if not track or not track.get("resolved_markets"):
        return [], ["Nessun mercato risolto ancora: non si sa se le previsioni battano il prezzo."], -1
    g = track.get("gain_blended") or {}
    n = track["resolved_markets"]
    if g.get("lo") is not None and g["lo"] > 0:
        return [f"Su {n} mercati risolti le previsioni hanno battuto il prezzo, anche nel caso peggiore dell'intervallo."], [], 1
    if g.get("hi") is not None and g["hi"] < 0:
        return [], [f"Su {n} mercati risolti il prezzo è stato più accurato delle previsioni."], -2
    clv = track.get("signal_clv") or {}
    if clv.get("n", 0) >= 10 and clv.get("avg") is not None:
        if clv["avg"] > 0:
            return [f"Finora, dopo i segnali, il prezzo si è mosso nella direzione giusta di {_pts(clv['avg'])} in media."], [], 0
        return [], [f"Finora, dopo i segnali, il prezzo si è mosso contro di {_pts(clv['avg'])} in media."], -1
    return [], [f"Solo {n} mercati risolti: non basta per dire se le previsioni battano il prezzo."], -1


def confidence_of(evidence: float, sigma: float, estimated_book: bool, track_adj: int) -> tuple[str, list]:
    score = 0
    why = []
    if evidence >= 0.75:
        score += 1
        why.append(f"notizie forti ({_pct(evidence)})")
    elif evidence < 0.5:
        score -= 1
        why.append(f"notizie deboli ({_pct(evidence)})")
    if sigma >= 0.05:
        score -= 1
        why.append(f"stima incerta (±{_pts(sigma)})")
    if estimated_book:
        score -= 1
        why.append("prezzi stimati, book non disponibile")
    score += track_adj
    why.append("risultati passati buoni" if track_adj > 0 else "risultati passati ancora da dimostrare" if track_adj < 0 else "risultati passati neutri")
    level = "alta" if score >= 1 else "bassa" if score <= -2 else "media"
    return level, why


# ---------- Plan ----------

def build_plan(*, ev: dict, fc: Forecast, signal: str, market_price: float, profile: RiskProfile,
               fee_bps: float, days: float, min_edge: float, risk_free: float,
               position: Optional[dict] = None, sell_bid: Optional[float] = None,
               tally: Optional[dict] = None, track: Optional[dict] = None,
               market_closed: bool = False, excluded_by: Optional[str] = None) -> Plan:
    """ev: the evaluation (Evaluation.as_dict()) of the latest forecast at the current prices.
    position: the open simulated bet on this market (side, shares, avg_price), if any.
    sell_bid: what one share of the position's side would fetch now (best bid)."""
    hurdle = risk_free + profile.min_apr_premium
    levels = buy_levels(fc, profile, fee_bps, days, min_edge, hurdle)
    p_now = fc.p_yes(market_price)
    blocking = [r for r in ev["reasons"] if r["blocking"]]
    structural = [r for r in blocking if r["code"] in STRUCTURAL and r["code"] != "exposure_cap"]
    track_pros, track_cons, track_adj = _track_lines(track)
    confidence, why = confidence_of(fc.evidence, fc.sigma, ev.get("quote_source") == "estimate", track_adj)

    if market_closed:
        return Plan("NONE", None, "Mercato chiuso", "Non si scambia più: si attende la risoluzione.",
                    confidence=confidence, confidence_why=why)

    # ----- An open position: hold or sell -----
    if position:
        side = position["side"]
        s_it = SIDE_IT[side]
        target = sell_above(fc, side, fee_bps, days, hurdle)
        levels["sell_above"] = target
        p_side_now = p_now if side == "YES" else 1 - p_now
        pros, cons = _evidence_lines(tally, side)
        opposite = (signal == "BUY_NO" and side == "YES") or (signal == "BUY_YES" and side == "NO")
        exit_lines = [
            f"Ordine limite di vendita a {_cents(target)}: a quel prezzo incassare subito rende più che aspettare la risoluzione."
            if target else "Nessun prezzo di vendita conviene più che tenere fino alla risoluzione.",
            "Niente stop-loss fisso: si vende se una nuova previsione dice che il prezzo è ormai sopra la stima, non perché il prezzo è sceso.",
        ]
        entry = position["avg_price"]
        move = (sell_bid - entry) if sell_bid is not None else None
        if opposite:
            return Plan("SELL", side, f"Vendi le quote {s_it}",
                        "La nuova previsione si è girata: ora conviene il lato opposto. Vendi al meglio"
                        + (f" (circa {_cents(sell_bid)})." if sell_bid is not None else "."),
                        orders=[{"type": "sell", "side": side, "shares": position["shares"], "limit": sell_bid}],
                        levels=levels, pros=pros, cons=cons + track_cons, exit=exit_lines,
                        confidence=confidence, confidence_why=why, code="forecast_flipped")
        if sell_bid is not None and target is not None and sell_bid >= target:
            vs_entry = f" ({_pts(move)} {'sopra' if move >= 0 else 'sotto'} il prezzo di acquisto)" if move is not None else ""
            return Plan("SELL", side, f"Vendi le quote {s_it}",
                        f"Il prezzo ({_cents(sell_bid)}) ha raggiunto la stima: incassare ora rende più che aspettare "
                        f"la risoluzione{vs_entry}.",
                        orders=[{"type": "sell", "side": side, "shares": position["shares"], "limit": target}],
                        levels=levels, pros=pros, cons=cons, exit=exit_lines, confidence=confidence, confidence_why=why,
                        code="target_reached")
        hold_pros = pros + [f"La stima ({_pct(p_side_now)}) è ancora sopra il prezzo: tenere rende più che vendere."]
        return Plan("HOLD", side, f"Tieni le quote {s_it}",
                    f"Tieni fino alla risoluzione, con un ordine di vendita a {_cents(target)}." if target
                    else "Tieni fino alla risoluzione.",
                    orders=[{"type": "sell", "side": side, "shares": position["shares"], "limit": target}] if target else [],
                    levels=levels, pros=hold_pros + track_pros, cons=cons + track_cons, exit=exit_lines,
                    confidence=confidence, confidence_why=why)

    # ----- No position -----
    side = "NO" if signal == "BUY_NO" else "YES" if signal == "BUY_YES" else None
    if side is None:
        # No signal: say at which prices there would be one
        side_hint = "YES" if p_now >= market_price else "NO"
        pros, cons = _evidence_lines(tally, side_hint)
        cons = [f"La stima ({_pct(p_now)}) è vicina al prezzo ({_pct(market_price)}): non c'è un vantaggio da sfruttare."] \
            + ([f"Notizie poco informative ({_pct(fc.evidence)}): la stima pesa poco."] if fc.evidence < 0.5 else []) + cons
        orders = []
        if levels["buy_yes_below"] is not None:
            orders.append({"type": "buy", "side": "YES", "limit": levels["buy_yes_below"], "conditional": True})
        if levels["buy_no_above"] is not None:
            orders.append({"type": "buy", "side": "NO", "limit": round(1 - levels["buy_no_above"], 3), "conditional": True})
        return Plan("NONE", None, "Nessuna azione", _conditional_text(levels) or "Nessun prezzo renderebbe conveniente una scommessa.",
                    orders=orders, levels=levels, pros=pros, cons=cons + track_cons, confidence=confidence,
                    confidence_why=why)

    s_it = SIDE_IT[side]
    p_side = p_now if side == "YES" else 1 - p_now
    price_side = market_price if side == "YES" else 1 - market_price
    news_pros, news_cons = _evidence_lines(tally, side)
    pros = [f"La stima dà al {s_it} il {_pct(p_side)} contro un prezzo di {_cents(price_side)}: "
            f"{_pts(p_side - price_side)} di vantaggio sulla carta."]
    if fc.evidence >= 0.75:
        pros.append(f"Le notizie informano molto l'esito (forza {_pct(fc.evidence)}).")
    pros += news_pros
    if ev.get("net_edge") is not None and ev["net_edge"] >= profile.min_net_edge:
        pros.append(f"Anche dopo spread, commissioni e incertezza restano {_pts(ev['net_edge'])} di margine.")
    if ev.get("apr") is not None and ev["apr"] >= ev["hurdle_apr"]:
        pros.append(f"Rendimento annuo prudente {_pct(min(ev['apr'], 10))}, sopra la soglia del {_pct(ev['hurdle_apr'])}.")
    pros += track_pros
    cons = [r["text"] for r in blocking] + news_cons
    if ev.get("prob_loss") is not None:
        cons.append(f"Resta una probabilità del {_pct(ev['prob_loss'])} di perdere la puntata.")
    if days > 90:
        cons.append(f"Il capitale resta bloccato per {days:.0f} giorni.")
    if fc.evidence < 0.5:
        cons.append(f"Notizie poco informative (forza {_pct(fc.evidence)}).")
    if ev.get("quote_source") == "estimate":
        cons.append("Book non disponibile: prezzi e quantità sono stimati.")
    if excluded_by:
        cons.append("Hai escluso questo mercato (o il suo evento o categoria) dal portafoglio simulato.")
    cons += track_cons
    target = sell_above(fc, side, fee_bps, days, hurdle)
    levels["sell_above"] = target
    exit_lines = [
        f"Dopo l'acquisto: ordine limite di vendita a {_cents(target)}, il prezzo a cui incassare rende più che aspettare."
        if target else "Dopo l'acquisto: tieni fino alla risoluzione.",
        "Se una nuova previsione gira la stima, vendi. Niente stop-loss fisso: un prezzo che scende non è da solo un motivo per vendere.",
    ]

    if ev["verdict"] in ("GO", "SMALL"):
        orders = [{"type": "buy", "side": side, "limit": ev["limit_price"], "shares": ev["shares"], "usd": ev["outlay"]}]
        if target:
            orders.append({"type": "sell", "side": side, "limit": target, "shares": ev["shares"], "after_fill": True})
        small = " (puntata ridotta dai limiti del preset)" if ev["verdict"] == "SMALL" else ""
        return Plan("BUY", side, f"Compra {s_it}",
                    f"Ordine limite: {ev['shares']:.0f} quote {s_it} a non più di {_cents(ev['limit_price'])}, "
                    f"circa {ev['outlay']:.2f} $".replace(".", ",") + f"{small}.",
                    orders=orders, levels=levels, pros=pros, cons=cons, exit=exit_lines,
                    confidence=confidence, confidence_why=why)

    if structural or any(r["code"] == "exposure_cap" for r in blocking):
        return Plan("AVOID", side, f"Evita, anche se il {s_it} sembra sottovalutato",
                    "Il vantaggio c'è, ma " + "; ".join(r["text"][0].lower() + r["text"][1:].rstrip(".") for r in (structural or blocking)[:2]) + ".",
                    levels=levels, pros=pros, cons=cons, confidence=confidence, confidence_why=why)

    limit = levels["buy_yes_below"] if side == "YES" else (1 - levels["buy_no_above"] if levels["buy_no_above"] is not None else None)
    return Plan("WAIT", side, f"Aspetta: compra {s_it} solo sotto {_cents(limit)}" if limit else "Aspetta",
                (f"Al prezzo attuale ({_cents(ev.get('best_price'))}) costi e incertezza si mangiano il vantaggio. "
                 f"Metti un ordine limite a {_cents(limit)}: se il prezzo scende fin lì, conviene.")
                if limit else "Al prezzo attuale costi e incertezza si mangiano il vantaggio, e nessun prezzo ragionevole lo recupera.",
                orders=[{"type": "buy", "side": side, "limit": limit, "conditional": True}] if limit else [],
                levels=levels, pros=pros, cons=cons, exit=exit_lines, confidence=confidence, confidence_why=why)


def _conditional_text(levels: dict) -> Optional[str]:
    parts = []
    if levels.get("buy_yes_below") is not None:
        parts.append(f"conviene comprare SÌ se il prezzo scende sotto {_cents(levels['buy_yes_below'])}")
    if levels.get("buy_no_above") is not None:
        parts.append(f"conviene comprare NO se il prezzo del SÌ sale sopra {_cents(levels['buy_no_above'])} "
                     f"(NO a {_cents(1 - levels['buy_no_above'])})")
    if not parts:
        return None
    text = " e ".join(parts)
    return text[0].upper() + text[1:] + "."
