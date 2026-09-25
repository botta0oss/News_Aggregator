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
from backend.i18n import dec, dollars, side as side_label, tr
from backend.markets.forecast import pool

GRID = [round(0.01 + i * 0.005, 3) for i in range(197)]   # YES prices from 1¢ to 99¢
# Reasons that no price can fix: the market itself does not suit the preset or the portfolio
STRUCTURAL = {"illiquid", "too_far", "exposure_cap", "no_cash", "no_book"}


def _cents(p: Optional[float]) -> str:
    if p is None:
        return "–"
    text = dec(p * 100, 1)
    return text.removesuffix(",0").removesuffix(".0") + "¢"


def _pts(x: float) -> str:
    return dec(abs(x) * 100, 1) + tr(" punti", " points")


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
            min_edge: float, hurdle: float, half_spread: float = 0.0) -> bool:
    """Would buying `side` be worth it if the YES mid price were q? The share is bought at the
    ask, half a spread above the side's mid price."""
    p_yes = fc.p_yes(q)
    p_side, mid = (p_yes, q) if side == "YES" else (1 - p_yes, 1 - q)
    if p_side - mid < min_edge:                        # the signal itself (measured on the mid, like the forecast)
        return False
    price = min(0.99, mid + half_spread)
    cost = price + fee_per_share(price, fee_bps)
    p_cons = max(0.0, p_side - profile.z * fc.sigma)
    if p_cons - cost < profile.min_net_edge:           # margin after costs and uncertainty
        return False
    return annualize(p_cons / cost - 1, days) >= hurdle  # worth the time the money is locked


def buy_levels(fc: Forecast, profile: RiskProfile, fee_bps: float, days: float, min_edge: float,
               hurdle: float, half_spread: float = 0.0) -> dict:
    """Highest YES mid price at which buying YES is worth it, lowest at which buying NO is, and
    the matching limit prices of the orders (the asks of the side)."""
    yes = [q for q in GRID if _buy_ok(fc, q, "YES", profile, fee_bps, days, min_edge, hurdle, half_spread)]
    no = [q for q in GRID if _buy_ok(fc, q, "NO", profile, fee_bps, days, min_edge, hurdle, half_spread)]
    q_yes, q_no = (max(yes) if yes else None), (min(no) if no else None)
    return {
        "buy_yes_below": q_yes, "buy_no_above": q_no,
        "yes_limit": round(min(0.99, q_yes + half_spread), 4) if q_yes is not None else None,
        "no_limit": round(min(0.99, 1 - q_no + half_spread), 4) if q_no is not None else None,
    }


def hold_value(fc: Forecast, price_side: float, side: str, days: float, hurdle: float) -> float:
    """What one share is worth to keep: its chance of paying 1 $, discounted for the time the
    money stays locked at the return the preset asks for. price_side: mid price of the side."""
    q = price_side if side == "YES" else 1 - price_side
    p_side = fc.p_yes(q) if side == "YES" else 1 - fc.p_yes(q)
    return p_side / math.pow(1 + max(hurdle, 0.0), max(days, 0.0) / 365)


def sell_above(fc: Forecast, side: str, fee_bps: float, days: float, hurdle: float,
               half_spread: float = 0.0) -> Optional[float]:
    """Lowest bid for the side held at which selling beats holding (fee on the sale included).
    The bid sits half a spread below the mid price the forecast is recomputed at."""
    for x in GRID:
        if x - fee_per_share(x, fee_bps) >= hold_value(fc, min(0.99, x + half_spread), side, days, hurdle):
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
            if key == good_key:
                text = (tr(f"{n} notizia va in questa direzione", f"{n} news item points this way") if n == 1
                        else tr(f"{n} notizie vanno in questa direzione", f"{n} news items point this way"))
            else:
                text = (tr(f"{n} notizia va nella direzione opposta", f"{n} news item points the other way") if n == 1
                        else tr(f"{n} notizie vanno nella direzione opposta", f"{n} news items point the other way"))
            if titles:
                text += ": «" + "», «".join(titles[:2]) + "»"
            out.append(text + ".")
    return pros, cons


def _track_lines(track: Optional[dict]) -> tuple[list, list, int]:
    """How the forecasts did so far. Returns pros, cons and a confidence adjustment."""
    if not track or not track.get("resolved_markets"):
        return [], [tr("Nessun mercato risolto ancora: non si sa se le previsioni battano il prezzo.",
                       "No market resolved yet: it is not known whether the forecasts beat the price.")], -1
    g = track.get("gain_blended") or {}
    n = track["resolved_markets"]
    if g.get("lo") is not None and g["lo"] > 0:
        return [tr(f"Su {n} mercati risolti le previsioni hanno battuto il prezzo, anche nel caso peggiore dell'intervallo.",
                   f"Over {n} resolved markets the forecasts beat the price, even in the worst case of the interval.")], [], 1
    if g.get("hi") is not None and g["hi"] < 0:
        return [], [tr(f"Su {n} mercati risolti il prezzo è stato più accurato delle previsioni.",
                       f"Over {n} resolved markets the price was more accurate than the forecasts.")], -2
    clv = track.get("signal_clv") or {}
    if clv.get("n", 0) >= 10 and clv.get("avg") is not None:
        if clv["avg"] > 0:
            return [tr(f"Finora, dopo i segnali, il prezzo si è mosso nella direzione giusta di {_pts(clv['avg'])} in media.",
                       f"So far, after the signals, the price moved the right way by {_pts(clv['avg'])} on average.")], [], 0
        return [], [tr(f"Finora, dopo i segnali, il prezzo si è mosso contro di {_pts(clv['avg'])} in media.",
                       f"So far, after the signals, the price moved against them by {_pts(clv['avg'])} on average.")], -1
    return [], [tr(f"Solo {n} mercati risolti: non basta per dire se le previsioni battano il prezzo.",
                   f"Only {n} resolved markets: not enough to say whether the forecasts beat the price.")], -1


def confidence_of(evidence: float, sigma: float, estimated_book: bool, track_adj: int) -> tuple[str, list]:
    score = 0
    why = []
    if evidence >= 0.75:
        score += 1
        why.append(tr(f"notizie forti ({_pct(evidence)})", f"strong news ({_pct(evidence)})"))
    elif evidence < 0.5:
        score -= 1
        why.append(tr(f"notizie deboli ({_pct(evidence)})", f"weak news ({_pct(evidence)})"))
    if sigma >= 0.05:
        score -= 1
        why.append(tr(f"stima incerta (±{_pts(sigma)})", f"uncertain estimate (±{_pts(sigma)})"))
    if estimated_book:
        score -= 1
        why.append(tr("prezzi stimati, book non disponibile", "estimated prices, book not available"))
    score += track_adj
    why.append(tr("risultati passati buoni", "good past results") if track_adj > 0
               else tr("risultati passati ancora da dimostrare", "past results still to be proven") if track_adj < 0
               else tr("risultati passati neutri", "neutral past results"))
    level = "alta" if score >= 1 else "bassa" if score <= -2 else "media"
    return level, why


# ---------- Plan ----------

def build_plan(*, ev: dict, fc: Forecast, signal: str, market_price: float, profile: RiskProfile,
               fee_bps: float, days: float, min_edge: float, risk_free: float,
               position: Optional[dict] = None, sell_bid: Optional[float] = None,
               tally: Optional[dict] = None, track: Optional[dict] = None,
               market_closed: bool = False, excluded_by: Optional[str] = None, half_spread: float = 0.0) -> Plan:
    """ev: the evaluation (Evaluation.as_dict()) of the latest forecast at the current prices.
    position: the open simulated bet on this market (side, shares, avg_price), if any.
    sell_bid: what one share of the position's side would fetch now (best bid)."""
    hurdle = risk_free + profile.min_apr_premium
    levels = buy_levels(fc, profile, fee_bps, days, min_edge, hurdle, half_spread)
    _cap_with_evaluation(levels, ev, signal, half_spread)
    p_now = fc.p_yes(market_price)
    blocking = [r for r in ev["reasons"] if r["blocking"]]
    structural = [r for r in blocking if r["code"] in STRUCTURAL and r["code"] != "exposure_cap"]
    track_pros, track_cons, track_adj = _track_lines(track)
    confidence, why = confidence_of(fc.evidence, fc.sigma, ev.get("quote_source") == "estimate", track_adj)

    if market_closed:
        return Plan("NONE", None, tr("Mercato chiuso", "Market closed"),
                    tr("Non si scambia più: si attende la risoluzione.", "It no longer trades: waiting for resolution."),
                    confidence=confidence, confidence_why=why)

    # ----- An open position: hold or sell -----
    if position:
        side = position["side"]
        s_it = side_label(side)
        target = sell_above(fc, side, fee_bps, days, hurdle, half_spread)
        levels["sell_above"] = target
        p_side_now = p_now if side == "YES" else 1 - p_now
        pros, cons = _evidence_lines(tally, side)
        opposite = (signal == "BUY_NO" and side == "YES") or (signal == "BUY_YES" and side == "NO")
        exit_lines = [
            tr(f"Ordine limite di vendita a {_cents(target)}: a quel prezzo incassare subito rende più che aspettare la risoluzione.",
               f"Limit sell order at {_cents(target)}: at that price cashing in now pays more than waiting for resolution.")
            if target else tr("Nessun prezzo di vendita conviene più che tenere fino alla risoluzione.",
                              "No selling price pays more than holding until resolution."),
            tr("Niente stop-loss fisso: si vende se una nuova previsione dice che il prezzo è ormai sopra la stima, non perché il prezzo è sceso.",
               "No fixed stop-loss: sell if a new forecast says the price is now above the estimate, not because the price fell."),
        ]
        entry = position["avg_price"]
        move = (sell_bid - entry) if sell_bid is not None else None
        if opposite:
            return Plan("SELL", side, tr(f"Vendi le quote {s_it}", f"Sell the {s_it} shares"),
                        tr("La nuova previsione si è girata: ora conviene il lato opposto. Vendi al meglio",
                           "The new forecast has turned: the opposite side is now the better bet. Sell at market")
                        + (tr(f" (circa {_cents(sell_bid)}).", f" (about {_cents(sell_bid)}).") if sell_bid is not None else "."),
                        orders=[{"type": "sell", "side": side, "shares": position["shares"], "limit": sell_bid}],
                        levels=levels, pros=pros, cons=cons + track_cons, exit=exit_lines,
                        confidence=confidence, confidence_why=why, code="forecast_flipped")
        if sell_bid is not None and target is not None and sell_bid >= target:
            vs_entry = ""
            if move is not None:
                vs_entry = (tr(f" ({_pts(move)} sopra il prezzo di acquisto)", f" ({_pts(move)} above the purchase price)") if move >= 0
                            else tr(f" ({_pts(move)} sotto il prezzo di acquisto)", f" ({_pts(move)} below the purchase price)"))
            return Plan("SELL", side, tr(f"Vendi le quote {s_it}", f"Sell the {s_it} shares"),
                        tr(f"Il prezzo ({_cents(sell_bid)}) ha raggiunto la stima: incassare ora rende più che aspettare la risoluzione{vs_entry}.",
                           f"The price ({_cents(sell_bid)}) has reached the estimate: cashing in now pays more than waiting for resolution{vs_entry}."),
                        orders=[{"type": "sell", "side": side, "shares": position["shares"], "limit": target}],
                        levels=levels, pros=pros, cons=cons, exit=exit_lines, confidence=confidence, confidence_why=why,
                        code="target_reached")
        hold_pros = pros + [tr(f"La stima ({_pct(p_side_now)}) è ancora sopra il prezzo: tenere rende più che vendere.",
                               f"The estimate ({_pct(p_side_now)}) is still above the price: holding pays more than selling.")]
        cons = cons + [tr(f"Resta una probabilità del {_pct(1 - p_side_now)} che le quote {s_it} non paghino nulla.",
                          f"There is still a {_pct(1 - p_side_now)} chance that the {s_it} shares pay nothing.")]
        return Plan("HOLD", side, tr(f"Tieni le quote {s_it}", f"Hold the {s_it} shares"),
                    tr(f"Tieni fino alla risoluzione, con un ordine di vendita a {_cents(target)}.",
                       f"Hold until resolution, with a sell order at {_cents(target)}.") if target
                    else tr("Tieni fino alla risoluzione.", "Hold until resolution."),
                    orders=[{"type": "sell", "side": side, "shares": position["shares"], "limit": target}] if target else [],
                    levels=levels, pros=hold_pros + track_pros, cons=cons + track_cons, exit=exit_lines,
                    confidence=confidence, confidence_why=why)

    # ----- No position -----
    side = "NO" if signal == "BUY_NO" else "YES" if signal == "BUY_YES" else None
    if side is None:
        # No signal: say at which prices there would be one
        side_hint = "YES" if p_now >= market_price else "NO"
        pros, cons = _evidence_lines(tally, side_hint)
        cons = [tr(f"La stima ({_pct(p_now)}) è vicina al prezzo ({_pct(market_price)}): non c'è un vantaggio da sfruttare.",
                   f"The estimate ({_pct(p_now)}) is close to the price ({_pct(market_price)}): there is no edge to exploit.")] \
            + ([tr(f"Notizie poco informative ({_pct(fc.evidence)}): la stima pesa poco.",
                   f"Not very informative news ({_pct(fc.evidence)}): the estimate weighs little.")] if fc.evidence < 0.5 else []) + cons
        orders = []
        if levels["yes_limit"] is not None:
            orders.append({"type": "buy", "side": "YES", "limit": levels["yes_limit"], "conditional": True})
        if levels["no_limit"] is not None:
            orders.append({"type": "buy", "side": "NO", "limit": levels["no_limit"], "conditional": True})
        return Plan("NONE", None, tr("Nessuna azione", "No action"),
                    _conditional_text(levels) or tr("Nessun prezzo renderebbe conveniente una scommessa.", "No price would make a bet worth it."),
                    orders=orders, levels=levels, pros=pros, cons=cons + track_cons, confidence=confidence,
                    confidence_why=why)

    s_it = side_label(side)
    p_side = p_now if side == "YES" else 1 - p_now
    price_side = market_price if side == "YES" else 1 - market_price
    news_pros, news_cons = _evidence_lines(tally, side)
    pros = [tr(f"La stima dà al {s_it} il {_pct(p_side)} contro un prezzo di {_cents(price_side)}: "
               f"{_pts(p_side - price_side)} di vantaggio sulla carta.",
               f"The estimate gives {s_it} {_pct(p_side)} against a price of {_cents(price_side)}: "
               f"{_pts(p_side - price_side)} of edge on paper.")]
    if fc.evidence >= 0.75:
        pros.append(tr(f"Le notizie informano molto l'esito (forza {_pct(fc.evidence)}).",
                       f"The news says a lot about the outcome (strength {_pct(fc.evidence)})."))
    pros += news_pros
    if ev.get("net_edge") is not None and ev["net_edge"] >= profile.min_net_edge:
        pros.append(tr(f"Anche dopo spread, commissioni e incertezza restano {_pts(ev['net_edge'])} di margine.",
                       f"Even after spread, fees and uncertainty {_pts(ev['net_edge'])} of margin remain."))
    if ev.get("apr") is not None and ev["apr"] >= ev["hurdle_apr"]:
        apr = tr("oltre 1000%", "over 1000%") if ev["apr"] >= 10 else _pct(ev["apr"])
        pros.append(tr(f"Rendimento annuo prudente {apr}, sopra la soglia del {_pct(ev['hurdle_apr'])}.",
                       f"Prudent annual return {apr}, above the {_pct(ev['hurdle_apr'])} threshold."))
    pros += track_pros
    cons = [r["text"] for r in blocking] + news_cons
    if ev.get("prob_loss") is not None:
        cons.append(tr(f"Resta una probabilità del {_pct(ev['prob_loss'])} di perdere la puntata.",
                       f"There is still a {_pct(ev['prob_loss'])} chance of losing the stake."))
    if days > 90:
        cons.append(tr(f"Il capitale resta bloccato per {days:.0f} giorni.", f"The capital stays locked for {days:.0f} days."))
    if fc.evidence < 0.5:
        cons.append(tr(f"Notizie poco informative (forza {_pct(fc.evidence)}).", f"Not very informative news (strength {_pct(fc.evidence)})."))
    if ev.get("quote_source") == "estimate":
        cons.append(tr("Book non disponibile: prezzi e quantità sono stimati.", "Book not available: prices and quantities are estimated."))
    if excluded_by:
        cons.append(tr("Hai escluso questo mercato (o il suo evento o categoria) dal portafoglio simulato.",
                       "You excluded this market (or its event or category) from the simulated portfolio."))
    cons += track_cons
    target = sell_above(fc, side, fee_bps, days, hurdle, half_spread)
    levels["sell_above"] = target
    exit_lines = [
        tr(f"Dopo l'acquisto: ordine limite di vendita a {_cents(target)}, il prezzo a cui incassare rende più che aspettare.",
           f"After buying: limit sell order at {_cents(target)}, the price at which cashing in pays more than waiting.")
        if target else tr("Dopo l'acquisto: tieni fino alla risoluzione.", "After buying: hold until resolution."),
        tr("Se una nuova previsione gira la stima, vendi. Niente stop-loss fisso: un prezzo che scende non è da solo un motivo per vendere.",
           "If a new forecast turns the estimate, sell. No fixed stop-loss: a falling price alone is no reason to sell."),
    ]

    if ev["verdict"] in ("GO", "SMALL"):
        orders = [{"type": "buy", "side": side, "limit": ev["limit_price"], "shares": ev["shares"], "usd": ev["outlay"]}]
        if target:
            orders.append({"type": "sell", "side": side, "limit": target, "shares": ev["shares"], "after_fill": True})
        small = tr(" (puntata ridotta dai limiti del preset)", " (stake reduced by the preset limits)") if ev["verdict"] == "SMALL" else ""
        return Plan("BUY", side, tr(f"Compra {s_it}", f"Buy {s_it}"),
                    tr(f"Ordine limite: {ev['shares']:.0f} quote {s_it} a non più di {_cents(ev['limit_price'])}, circa {dollars(ev['outlay'])}{small}.",
                       f"Limit order: {ev['shares']:.0f} {s_it} shares at no more than {_cents(ev['limit_price'])}, about {dollars(ev['outlay'])}{small}."),
                    orders=orders, levels=levels, pros=pros, cons=cons, exit=exit_lines,
                    confidence=confidence, confidence_why=why)

    if structural or any(r["code"] == "exposure_cap" for r in blocking):
        return Plan("AVOID", side, tr(f"Evita, anche se il {s_it} sembra sottovalutato", f"Avoid, even though {s_it} looks undervalued"),
                    tr("Il vantaggio c'è, ma ", "The edge is there, but ") + "; ".join(r["text"][0].lower() + r["text"][1:].rstrip(".") for r in (structural or blocking)[:2]) + ".",
                    levels=levels, pros=pros, cons=cons, confidence=confidence, confidence_why=why)

    limit = levels["yes_limit"] if side == "YES" else levels["no_limit"]
    best = ev.get("best_price")
    if limit is None:
        summary = tr("Al prezzo attuale costi e incertezza si mangiano il vantaggio, e nessun prezzo ragionevole lo recupera.",
                     "At the current price costs and uncertainty eat up the edge, and no reasonable price recovers it.")
    elif best is not None and limit >= best:
        # At the best ask it would be worth it, but not for a useful amount (book, minimum order)
        summary = tr(f"Il prezzo migliore ({_cents(best)}) è al limite: conviene solo per poche quote. "
                     f"Metti un ordine limite a {_cents(limit)} e lascia che il prezzo venga da te.",
                     f"The best price ({_cents(best)}) is at the limit: worth it only for a few shares. "
                     f"Place a limit order at {_cents(limit)} and let the price come to you.")
    else:
        summary = tr(f"Al prezzo attuale ({_cents(best)}) costi e incertezza si mangiano il vantaggio. "
                     f"Metti un ordine limite a {_cents(limit)}: se il prezzo scende fin lì, conviene.",
                     f"At the current price ({_cents(best)}) costs and uncertainty eat up the edge. "
                     f"Place a limit order at {_cents(limit)}: if the price falls that far, it is worth it.")
    return Plan("WAIT", side, tr(f"Aspetta: compra {s_it} a {_cents(limit)} o meno", f"Wait: buy {s_it} at {_cents(limit)} or less")
                if limit else tr("Aspetta", "Wait"), summary,
                orders=[{"type": "buy", "side": side, "limit": limit, "conditional": True}] if limit else [],
                levels=levels, pros=pros, cons=cons, exit=exit_lines, confidence=confidence, confidence_why=why)


def _cap_with_evaluation(levels: dict, ev: dict, signal: str, half_spread: float) -> None:
    """For the side of the signal, the order limit is never above the evaluation's maximum price.

    The level recomputes the blend at each price, so a higher price also raises the estimate
    (the market is part of it): fine to say when a falling price becomes a buy, too generous
    for how much to pay now. The evaluation's maximum price uses the estimate at today's price.
    """
    if signal not in ("BUY_YES", "BUY_NO") or not ev.get("limit_price"):
        return
    cap = ev["limit_price"]
    if signal == "BUY_YES" and levels["yes_limit"] is not None and cap < levels["yes_limit"]:
        levels["yes_limit"] = round(cap, 4)
        levels["buy_yes_below"] = round(max(0.0, cap - half_spread), 4)
    if signal == "BUY_NO" and levels["no_limit"] is not None and cap < levels["no_limit"]:
        levels["no_limit"] = round(cap, 4)
        levels["buy_no_above"] = round(min(1.0, 1 - cap + half_spread), 4)


def _conditional_text(levels: dict) -> Optional[str]:
    parts = []
    if levels.get("buy_yes_below") is not None:
        parts.append(tr(f"conviene comprare SÌ se il prezzo scende sotto {_cents(levels['buy_yes_below'])} "
                        f"(ordine SÌ a {_cents(levels['yes_limit'])})",
                        f"buying YES pays if the price falls below {_cents(levels['buy_yes_below'])} "
                        f"(YES order at {_cents(levels['yes_limit'])})"))
    if levels.get("buy_no_above") is not None:
        parts.append(tr(f"conviene comprare NO se il prezzo del SÌ sale sopra {_cents(levels['buy_no_above'])} "
                        f"(ordine NO a {_cents(levels['no_limit'])})",
                        f"buying NO pays if the YES price rises above {_cents(levels['buy_no_above'])} "
                        f"(NO order at {_cents(levels['no_limit'])})"))
    if not parts:
        return None
    text = tr(" e ", " and ").join(parts)
    return text[0].upper() + text[1:] + "."
