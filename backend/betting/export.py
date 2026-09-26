"""Export of the simulated portfolio, as it is at the time of the export, for spreadsheets.

Excel workbook (one sheet per table: summary, bets, equity curve, exclusions, and a sheet that
explains every column) or a CSV of the bets. Column names are stable keys (the same as the API);
the descriptions follow the language of the request. Prices and probabilities are fractions
0–1, amounts are in USD, times are UTC.
"""
import csv
import io
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.betting import portfolio, shadow
from backend.betting.plans import known_bid
from backend.betting.profiles import get_profile
from backend.config import settings
from backend.db.models import Market, MarketPrediction, PaperBet, PaperExclusion, PaperOrder
from backend.i18n import tr

# Cell formats (Excel): "money", "price" (0–1 with 4 decimals), "pct", "int", "date", "text"
BET_COLUMNS = [
    # key, format, Italian description, English description
    ("bet_id", "text", "Identificativo della scommessa", "Bet identifier"),
    ("status", "text", "open, won, lost, void (50-50), sold (venduta prima della risoluzione), excluded (esclusa dalla simulazione)",
     "open, won, lost, void (50-50), sold (sold before resolution), excluded (excluded from the simulation)"),
    ("placed_by", "text", "auto (scommessa automatica) o manual", "auto (automatic bet) or manual"),
    ("entry", "text", "taker (comprata sul book al prezzo di vendita) o maker (ordine limite eseguito, senza commissione)",
     "taker (bought from the book at the ask) or maker (limit order filled, no fee)"),
    ("preset", "text", "Preset di rischio all'acquisto", "Risk preset at purchase"),
    ("created_at", "date", "Acquisto (UTC)", "Purchase (UTC)"),
    ("settled_at", "date", "Chiusura: risoluzione o vendita (UTC)", "Close: resolution or sale (UTC)"),
    ("days_held", "num", "Giorni tra acquisto e chiusura (o l'export, se aperta)", "Days between purchase and close (or the export, if open)"),
    ("market_id", "text", "Identificativo del mercato Polymarket", "Polymarket market identifier"),
    ("question", "text", "Domanda del mercato", "Market question"),
    ("market_url", "text", "Pagina su Polymarket", "Page on Polymarket"),
    ("category", "text", "Categoria", "Category"),
    ("event_slug", "text", "Evento Polymarket", "Polymarket event"),
    ("multi_event_id", "text", "Evento a più esiti, se è un suo esito", "Multi-outcome event, if it is one of its outcomes"),
    ("end_date", "date", "Scadenza del mercato (UTC)", "Market end date (UTC)"),
    ("side", "text", "Lato comprato: YES o NO", "Side bought: YES or NO"),
    ("shares", "num", "Quote comprate", "Shares bought"),
    ("avg_price", "price", "Prezzo medio pagato per quota (0–1)", "Average price paid per share (0–1)"),
    ("stake", "money", "Spesa per le quote (USD)", "Spent on shares (USD)"),
    ("fee", "money", "Commissioni pagate, anche quella di vendita se venduta (USD)", "Fees paid, including the sale fee if sold (USD)"),
    ("outlay", "money", "Esborso: spesa + commissioni (USD)", "Outlay: spend + fees (USD)"),
    ("p_side", "price", "Probabilità stimata che il lato vinca, all'acquisto", "Estimated probability that the side wins, at purchase"),
    ("p_conservative", "price", "Probabilità prudente (meno l'incertezza), all'acquisto", "Prudent probability (minus uncertainty), at purchase"),
    ("expected_profit", "money", "Profitto atteso all'acquisto (USD)", "Expected profit at purchase (USD)"),
    ("payout", "money", "Incasso: 1 $ per quota vincente, 0,50 $ se 50-50, ricavo netto se venduta", "Payout: $1 per winning share, $0.50 if 50-50, net proceeds if sold"),
    ("pnl", "money", "Profitto o perdita realizzati (USD)", "Realised profit or loss (USD)"),
    ("return_on_outlay", "pct", "pnl / esborso", "pnl / outlay"),
    ("exit_price", "price", "Prezzo medio di vendita, se venduta", "Average sale price, if sold"),
    ("exit_reason", "text", "Motivo della vendita", "Reason for the sale"),
    ("market_closed", "bool", "Il mercato non si scambia più", "The market no longer trades"),
    ("resolution", "text", "Esito del mercato: yes, no o split", "Market outcome: yes, no or split"),
    ("market_yes_price", "price", "Prezzo del SÌ all'export", "YES price at the export"),
    ("side_price", "price", "Prezzo del lato comprato all'export (metà mercato)", "Price of the side bought at the export (mid)"),
    ("bid_price", "price", "Miglior prezzo di vendita del lato all'export", "Best bid of the side at the export"),
    ("current_value", "money", "Posizione aperta: valore vendendo ora al bid, commissione compresa", "Open position: value selling now at the bid, fee included"),
    ("unrealized_pnl", "money", "Posizione aperta: profitto latente al bid", "Open position: unrealised profit at the bid"),
    ("mid_value", "money", "Posizione aperta: valore al prezzo di mercato", "Open position: value at the market price"),
    ("unrealized_pnl_mid", "money", "Posizione aperta: profitto latente al prezzo di mercato", "Open position: unrealised profit at the market price"),
    ("plan_action", "text", "Posizione aperta: piano attuale, HOLD o SELL", "Open position: current plan, HOLD or SELL"),
    ("plan_sell_above", "price", "Posizione aperta: prezzo di vendita obiettivo", "Open position: target sale price"),
    ("last_trading_price", "price", "Ultimo prezzo del SÌ prima della chiusura del mercato", "Last YES price before the market closed"),
    ("clv", "price", "Closing line value: chiusura del lato − prezzo pagato (> 0 = comprato sotto la chiusura)",
     "Closing line value: close of the side − price paid (> 0 = bought below the close)"),
    ("prediction_id", "text", "Previsione che ha portato all'acquisto", "Forecast that led to the purchase"),
    ("forecast_at", "date", "Ora della previsione (UTC)", "Time of the forecast (UTC)"),
    ("forecast_market_price", "price", "Prezzo del SÌ al momento della previsione", "YES price at the time of the forecast"),
    ("jev_probability", "price", "Stima di Jev della probabilità del SÌ", "Jev's estimate of the probability of YES"),
    ("calibrated_probability", "price", "Stima di Jev dopo la calibrazione", "Jev's estimate after calibration"),
    ("blended_probability", "price", "Probabilità finale (Jev unito al prezzo)", "Final probability (Jev pooled with the price)"),
    ("evidence_strength", "price", "Forza delle evidenze usata (0–1): la più bassa tra Jev e i fatti", "Evidence strength used (0–1): the lower of Jev's and the facts'"),
    ("jev_evidence_strength", "price", "Forza delle evidenze secondo Jev", "Evidence strength as Jev rated it"),
    ("objective_evidence", "price", "Forza delle evidenze dai fatti (fonti, età, conferme)", "Evidence strength from the facts (sources, age, confirmations)"),
    ("base_rate", "price", "Caso tipico secondo Jev: quanto spesso accadono eventi simili", "Jev's base rate: how often similar events happen"),
    ("second_opinion", "price", "Probabilità del SÌ secondo la seconda opinione (Gemini o Groq)", "Probability of YES according to the second opinion (Gemini or Groq)"),
    ("second_opinion_provider", "text", "Chi ha dato la seconda opinione", "Who gave the second opinion"),
    ("model_weight", "price", "Peso di Jev nella probabilità finale", "Jev's weight in the final probability"),
    ("edge", "price", "Probabilità finale − prezzo", "Final probability − price"),
    ("signal", "text", "Segnale: BUY_YES, BUY_NO, HOLD", "Signal: BUY_YES, BUY_NO, HOLD"),
    ("kelly_fraction", "pct", "Kelly semplice suggerito dal segnale", "Simple Kelly suggested by the signal"),
    ("article_count", "int", "Notizie lette da Jev", "News items read by Jev"),
    ("verdict", "text", "Valutazione economica alla previsione: GO, SMALL, NO", "Economic assessment at the forecast: GO, SMALL, NO"),
    ("limit_price", "price", "Prezzo massimo per quota secondo la valutazione", "Maximum price per share according to the assessment"),
    ("net_edge", "price", "Margine dopo costi e incertezza", "Margin after costs and uncertainty"),
    ("apr", "pct", "Rendimento annuo prudente", "Prudent annual return"),
]

EQUITY_COLUMNS = [
    ("t", "date", "Chiusura di una scommessa (UTC); il primo punto è l'inizio", "Close of a bet (UTC); the first point is the start"),
    ("equity", "money", "Capitale dopo le scommesse chiuse fino a quel momento (USD)", "Capital after the bets closed up to then (USD)"),
]

ORDER_COLUMNS = [
    ("order_id", "text", "Identificativo dell'ordine", "Order identifier"),
    ("status", "text", "pending (in attesa), filled (eseguito), expired (scaduto), cancelled (annullato)",
     "pending, filled, expired, cancelled"),
    ("placed_by", "text", "auto o manual", "auto or manual"),
    ("created_at", "date", "Inserimento (UTC)", "Placed (UTC)"),
    ("expires_at", "date", "Scadenza (UTC)", "Expiry (UTC)"),
    ("closed_at", "date", "Esecuzione, scadenza o annullamento (UTC)", "Fill, expiry or cancellation (UTC)"),
    ("market_id", "text", "Identificativo del mercato Polymarket", "Polymarket market identifier"),
    ("question", "text", "Domanda del mercato", "Market question"),
    ("side", "text", "Lato: YES o NO", "Side: YES or NO"),
    ("shares", "num", "Quote", "Shares"),
    ("limit_price", "price", "Prezzo limite dell'ordine", "Limit price of the order"),
    ("taker_price", "price", "Prezzo che si sarebbe pagato prendendo dal book", "Price that taking from the book would have cost"),
    ("outlay", "money", "Importo riservato (USD)", "Amount reserved (USD)"),
    ("p_side", "price", "Probabilità stimata che il lato vinca", "Estimated probability that the side wins"),
    ("reason", "text", "Motivo di scadenza o annullamento", "Reason for expiry or cancellation"),
    ("bet_id", "text", "Scommessa nata dall'ordine eseguito", "Bet created by the filled order"),
]

SHADOW_COLUMNS = [
    ("shadow_id", "text", "Identificativo della scommessa ombra", "Shadow bet identifier"),
    ("filter", "text", "Filtro che l'ha bloccata (codici uniti da +)", "Filter that blocked it (codes joined by +)"),
    ("filter_label", "text", "Filtro, per esteso", "Filter, in words"),
    ("status", "text", "open, won, lost, void (50-50)", "open, won, lost, void (50-50)"),
    ("created_at", "date", "Quando è stata bloccata (UTC)", "When it was blocked (UTC)"),
    ("settled_at", "date", "Risoluzione (UTC)", "Resolution (UTC)"),
    ("market_id", "text", "Identificativo del mercato Polymarket", "Polymarket market identifier"),
    ("question", "text", "Domanda del mercato", "Market question"),
    ("side", "text", "Lato che si sarebbe comprato", "Side that would have been bought"),
    ("shares", "num", "Quote", "Shares"),
    ("avg_price", "price", "Prezzo medio che si sarebbe pagato (book)", "Average price that would have been paid (book)"),
    ("outlay", "money", "Esborso ipotetico (USD)", "Hypothetical outlay (USD)"),
    ("p_side", "price", "Probabilità stimata che il lato vinca", "Estimated probability that the side wins"),
    ("expected_profit", "money", "Profitto atteso ipotetico (USD)", "Hypothetical expected profit (USD)"),
    ("pnl", "money", "Risultato ipotetico (USD): < 0 = il filtro ha evitato una perdita", "Hypothetical result (USD): < 0 = the filter avoided a loss"),
    ("move", "price", "Movimento del prezzo del lato: fino alla chiusura, o finora", "Price move of the side: to the close, or so far"),
]

EXCLUSION_COLUMNS = [
    ("kind", "text", "market, event o category", "market, event or category"),
    ("value", "text", "Mercato, evento o categoria esclusi", "Excluded market, event or category"),
    ("label", "text", "Descrizione", "Description"),
    ("created_at", "date", "Quando (UTC)", "When (UTC)"),
]


def _describe(columns):
    return {key: tr(it, en) for key, _, it, en in columns}


def _market_url(m: Market) -> Optional[str]:
    if m.event_slug:
        return f"https://polymarket.com/event/{m.event_slug}"
    return f"https://polymarket.com/market/{m.slug}" if m.slug else None


async def build(db: AsyncSession) -> dict:
    """All the tables of the export, as lists of dicts, plus the summary as (key, description, value)."""
    now = datetime.now(timezone.utc)
    s = await portfolio.get_settings(db)
    summ = await portfolio.summary(db)
    profile = get_profile(s.preset)
    rows = (await db.execute(
        select(PaperBet, Market, MarketPrediction).join(Market, Market.id == PaperBet.market_id)
        .outerjoin(MarketPrediction, MarketPrediction.id == PaperBet.prediction_id)
        .order_by(PaperBet.created_at)
    )).all()

    bets = []
    for bet, m, p in rows:
        is_open = bet.status == "open"
        value = portfolio.mark_value(bet, m) if is_open else None
        mid = portfolio.mid_value(bet, m) if is_open else None
        plan = await portfolio.open_bet_plan(db, bet, m, profile) if is_open else None
        econ = (p.economics or {}) if p is not None else {}
        outlay = bet.stake + bet.fee
        end = bet.settled_at or now
        bets.append({
            "bet_id": str(bet.id), "status": bet.status, "placed_by": bet.placed_by, "entry": bet.entry, "preset": bet.preset,
            "created_at": bet.created_at, "settled_at": bet.settled_at,
            "days_held": round((end - bet.created_at).total_seconds() / 86400, 2),
            "market_id": m.id, "question": m.question, "market_url": _market_url(m), "category": m.category,
            "event_slug": m.event_slug, "multi_event_id": m.multi_event_id, "end_date": m.end_date,
            "side": bet.side, "shares": bet.shares, "avg_price": bet.avg_price, "stake": bet.stake, "fee": bet.fee,
            "outlay": outlay, "p_side": bet.p_side, "p_conservative": bet.p_conservative,
            "expected_profit": bet.expected_profit, "payout": bet.payout, "pnl": bet.pnl,
            "return_on_outlay": (bet.pnl / outlay) if bet.pnl is not None and outlay else None,
            "exit_price": bet.exit_price, "exit_reason": bet.exit_reason,
            "market_closed": bool(m.closed),
            "resolution": m.resolution or ({True: "yes", False: "no"}.get(m.resolved_yes)),
            "market_yes_price": m.yes_price,
            "side_price": (m.yes_price if bet.side == "YES" else 1 - m.yes_price) if m.yes_price is not None else None,
            "bid_price": known_bid(m, bet.side) if is_open else None,
            "current_value": value, "unrealized_pnl": (value - outlay) if value is not None else None,
            "mid_value": mid, "unrealized_pnl_mid": (mid - outlay) if mid is not None else None,
            "plan_action": plan["action"] if plan else None, "plan_sell_above": plan["sell_above"] if plan else None,
            "last_trading_price": m.last_trading_price, "clv": portfolio.bet_clv(bet, m),
            "prediction_id": str(p.id) if p is not None else None,
            "forecast_at": p.created_at if p is not None else None,
            "forecast_market_price": p.market_probability if p is not None else None,
            "jev_probability": p.model_probability if p is not None else None,
            "calibrated_probability": p.calibrated_probability if p is not None else None,
            "blended_probability": p.blended_probability if p is not None else None,
            "evidence_strength": p.evidence_strength if p is not None else None,
            "jev_evidence_strength": p.jev_evidence_strength if p is not None else None,
            "objective_evidence": p.objective_evidence if p is not None else None,
            "base_rate": p.base_rate if p is not None else None,
            "second_opinion": p.second_opinion if p is not None else None,
            "second_opinion_provider": p.second_opinion_provider if p is not None else None,
            "model_weight": p.model_weight if p is not None else None,
            "edge": p.edge if p is not None else None,
            "signal": p.signal if p is not None else None,
            "kelly_fraction": p.kelly_fraction if p is not None else None,
            "article_count": p.article_count if p is not None else None,
            "verdict": econ.get("verdict"), "limit_price": econ.get("limit_price"),
            "net_edge": econ.get("net_edge"), "apr": econ.get("apr"),
        })

    shadows = [
        {"shadow_id": str(d["id"]), "filter": d["filter"], "filter_label": d["filter_label"], "status": d["status"],
         "created_at": d["created_at"], "settled_at": d["settled_at"], "market_id": d["market_id"], "question": d["question"],
         "side": d["side"], "shares": d["shares"], "avg_price": d["avg_price"], "outlay": d["outlay"], "p_side": d["p_side"],
         "expected_profit": d["expected_profit"], "pnl": d["pnl"], "move": d["move"]}
        for d in reversed(await shadow.listing(db, limit=100_000))
    ]
    exclusions = [
        {"kind": x.kind, "value": x.value, "label": x.label, "created_at": x.created_at}
        for x in (await db.execute(select(PaperExclusion).order_by(PaperExclusion.created_at))).scalars().all()
    ]
    orders = [
        {"order_id": str(o.id), "status": o.status, "placed_by": o.placed_by, "created_at": o.created_at,
         "expires_at": o.expires_at, "closed_at": o.closed_at, "market_id": m.id, "question": m.question,
         "side": o.side, "shares": o.shares, "limit_price": o.limit_price, "taker_price": o.taker_price,
         "outlay": o.outlay, "p_side": o.p_side, "reason": o.reason, "bet_id": str(o.bet_id) if o.bet_id else None}
        for o, m in (await db.execute(select(PaperOrder, Market).join(Market, Market.id == PaperOrder.market_id)
                                      .order_by(PaperOrder.created_at))).all()
    ]
    equity = [{"t": datetime.fromisoformat(pt["t"]), "equity": pt["equity"]} for pt in summ["equity_curve"]]

    c, clv_all, clv_open = summ["counts"], summ["clv"] or {}, summ["clv_open"] or {}
    summary = [
        # key, format, description, value
        ("exported_at", "date", tr("Ora dell'export (UTC)", "Time of the export (UTC)"), now),
        ("started_at", "date", tr("Inizio della simulazione (UTC)", "Start of the simulation (UTC)"), s.started_at),
        ("bankroll", "money", tr("Capitale iniziale", "Starting capital"), s.bankroll),
        ("preset", "text", tr("Preset di rischio attuale", "Current risk preset"), s.preset),
        ("auto_paper", "bool", tr("Scommesse automatiche attive", "Automatic bets on"), s.auto_paper),
        ("auto_sell", "bool", tr("Vendite automatiche attive", "Automatic sales on"), s.auto_sell),
        ("total_value", "money", tr("Valore attuale: liquidità + posizioni aperte al bid", "Current value: cash + open positions at the bid"), summ["total_value"]),
        ("roi", "pct", tr("Rendimento sul capitale iniziale", "Return on the starting capital"), summ["roi"]),
        ("equity", "money", tr("Capitale: iniziale + profitti realizzati", "Capital: starting + realised profits"), summ["equity"]),
        ("cash", "money", tr("Liquidità disponibile", "Available cash"), summ["cash"]),
        ("invested", "money", tr("Investito nelle posizioni aperte", "Invested in open positions"), summ["invested"]),
        ("open_value", "money", tr("Valore delle posizioni aperte al bid", "Value of open positions at the bid"), summ["open_value"]),
        ("realized_pnl", "money", tr("Profitti realizzati", "Realised profits"), summ["realized_pnl"]),
        ("unrealized_pnl", "money", tr("Profitti latenti al bid, commissione di vendita compresa", "Unrealised profits at the bid, sale fee included"), summ["unrealized_pnl"]),
        ("unrealized_pnl_mid", "money", tr("Profitti latenti al prezzo di mercato", "Unrealised profits at the market price"), summ["unrealized_pnl_mid"]),
        ("expected_pnl_settled", "money", tr("Profitto atteso delle scommesse chiuse (da confrontare con i realizzati)", "Expected profit of the closed bets (compare with realised)"), summ["expected_pnl_settled"]),
        ("hit_rate", "pct", tr("Scommesse vinte (le vendute in guadagno contano come vinte)", "Bets won (sales at a profit count as won)"), summ["hit_rate"]),
        ("count_open", "int", tr("Aperte", "Open"), c["open"]),
        ("count_won", "int", tr("Vinte", "Won"), c["won"]),
        ("count_lost", "int", tr("Perse", "Lost"), c["lost"]),
        ("count_void", "int", tr("Annullate 50-50", "Voided 50-50"), c["void"]),
        ("count_sold", "int", tr("Vendute", "Sold"), c["sold"]),
        ("count_excluded", "int", tr("Escluse dalla simulazione", "Excluded from the simulation"), c["excluded"]),
        ("clv_avg", "price", tr("CLV medio sui mercati chiusi", "Average CLV on closed markets"), clv_all.get("avg")),
        ("clv_share_positive", "pct", tr("Quota comprata sotto la chiusura", "Share bought below the close"), clv_all.get("share_positive")),
        ("clv_n", "int", tr("Scommesse con CLV", "Bets with CLV"), clv_all.get("n")),
        ("clv_open_avg", "price", tr("Movimento medio finora sulle aperte", "Average move so far on the open ones"), clv_open.get("avg")),
        ("order_mode", "text", tr("Acquisti automatici: maker (ordini limite) o taker", "Automatic buys: maker (limit orders) or taker"), summ["orders"]["mode"]),
        ("orders_pending", "int", tr("Ordini limite in attesa", "Limit orders pending"), summ["orders"]["counts"]["pending"]),
        ("orders_reserved", "money", tr("Importo riservato dagli ordini in attesa", "Amount reserved by pending orders"), summ["orders"]["reserved"]),
        ("orders_filled", "int", tr("Ordini limite eseguiti", "Limit orders filled"), summ["orders"]["counts"]["filled"]),
        ("orders_expired", "int", tr("Ordini limite scaduti", "Limit orders expired"), summ["orders"]["counts"]["expired"]),
        ("orders_fill_rate", "pct", tr("Eseguiti / (eseguiti + scaduti)", "Filled / (filled + expired)"), summ["orders"]["fill_rate"]),
        ("orders_saved", "money", tr("Risparmio degli ordini eseguiti rispetto al prezzo del book all'inserimento", "Saving of the filled orders against the book price when placed"), summ["orders"]["saved"]),
        *[(f"shadow_{r['filter']}", "money", tr(f"Scommesse ombra «{r['label']}»: {r['n']} bloccate, risultato ipotetico sulle risolte",
                                                    f"Shadow bets «{r['label']}»: {r['n']} blocked, hypothetical result on the resolved ones"), r["pnl"])
          for r in summ["shadow"]],
        ("guard_paused", "bool", tr("Scommesse automatiche in pausa per il CLV", "Automatic bets paused by the CLV guard"), s.paused_at is not None),
        ("guard_reason", "text", tr("Motivo della pausa", "Reason for the pause"), s.paused_reason),
        ("risk_free_rate", "pct", tr("Tasso senza rischio", "Risk-free rate"), settings.RISK_FREE_RATE),
        ("calibration_factor", "num", tr("Correzione dell'incertezza dai risultati passati", "Uncertainty correction from past results"), await portfolio.calibration_factor(db)),
        ("preset_kelly_scale", "num", tr("Preset: frazione di Kelly", "Preset: Kelly fraction"), profile.kelly_scale),
        ("preset_z", "num", tr("Preset: z (deviazioni standard tolte alla probabilità)", "Preset: z (standard deviations taken off the probability)"), profile.z),
        ("preset_min_net_edge", "price", tr("Preset: margine netto minimo", "Preset: minimum net margin"), profile.min_net_edge),
        ("preset_min_apr_premium", "pct", tr("Preset: premio annuo sul tasso senza rischio", "Preset: annual premium over the risk-free rate"), profile.min_apr_premium),
        ("preset_max_market_frac", "pct", tr("Preset: massimo per mercato", "Preset: maximum per market"), profile.max_market_frac),
        ("preset_max_event_frac", "pct", tr("Preset: massimo per evento", "Preset: maximum per event"), profile.max_event_frac),
        ("preset_max_category_frac", "pct", tr("Preset: massimo per categoria", "Preset: maximum per category"), profile.max_category_frac),
        ("preset_max_total_frac", "pct", tr("Preset: massimo investito", "Preset: maximum invested"), profile.max_total_frac),
        ("preset_min_liquidity", "money", tr("Preset: liquidità minima del mercato", "Preset: minimum market liquidity"), profile.min_liquidity),
        ("preset_max_days", "int", tr("Preset: scadenza massima (giorni)", "Preset: maximum end date (days)"), profile.max_days),
    ]
    return {"now": now, "summary": summary, "bets": bets, "orders": orders, "shadow": shadows, "equity": equity,
            "exclusions": exclusions}


def filename(now: datetime, ext: str) -> str:
    return f"news-markets-portfolio-{now:%Y%m%d-%H%M}.{ext}"


def _csv_value(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else v


def to_csv(data: dict) -> bytes:
    """The bets as CSV: comma separated, decimal point, ISO times in UTC, UTF-8 with BOM (Excel)."""
    out = io.StringIO()
    writer = csv.writer(out)
    keys = [k for k, *_ in BET_COLUMNS]
    writer.writerow(keys)
    for row in data["bets"]:
        writer.writerow([_csv_value(row[k]) for k in keys])
    return ("﻿" + out.getvalue()).encode("utf-8")


FORMATS = {"money": '"$"#,##0.00;-"$"#,##0.00', "price": "0.0000", "pct": "0.0%", "int": "0", "num": "0.00",
           "date": "yyyy-mm-dd hh:mm"}


def _xlsx_value(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None)   # Excel has no time zones: UTC
    return v


def to_xlsx(data: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    bold = Font(bold=True)

    def table(ws, columns, rows, widths=None):
        keys = [k for k, *_ in columns]
        ws.append(keys)
        for cell in ws[1]:
            cell.font = bold
        for row in rows:
            ws.append([_xlsx_value(row[k]) for k in keys])
        for i, (key, fmt, *_rest) in enumerate(columns, start=1):
            letter = get_column_letter(i)
            if fmt in FORMATS:
                for cell in ws[letter][1:]:
                    cell.number_format = FORMATS[fmt]
            ws.column_dimensions[letter].width = (widths or {}).get(key, 16 if fmt == "date" else 13)
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = tr("Riepilogo", "Summary")
    ws.append(["key", tr("descrizione", "description"), tr("valore", "value")])
    for cell in ws[1]:
        cell.font = bold
    for key, fmt, desc, value in data["summary"]:
        ws.append([key, desc, _xlsx_value(value)])
        if fmt in FORMATS:
            ws.cell(row=ws.max_row, column=3).number_format = FORMATS[fmt]
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 24, 62, 18
    ws.freeze_panes = "A2"

    table(wb.create_sheet(tr("Scommesse", "Bets")), BET_COLUMNS, data["bets"],
          widths={"bet_id": 38, "question": 48, "market_url": 40, "exit_reason": 40, "prediction_id": 38})
    table(wb.create_sheet(tr("Ordini", "Orders")), ORDER_COLUMNS, data["orders"],
          widths={"order_id": 38, "question": 48, "reason": 40, "bet_id": 38})
    table(wb.create_sheet(tr("Ombra", "Shadow")), SHADOW_COLUMNS, data["shadow"],
          widths={"shadow_id": 38, "question": 48, "filter_label": 36})
    table(wb.create_sheet(tr("Capitale", "Equity")), EQUITY_COLUMNS, data["equity"])
    table(wb.create_sheet(tr("Esclusioni", "Exclusions")), EXCLUSION_COLUMNS, data["exclusions"], widths={"value": 30, "label": 48})

    ws = wb.create_sheet(tr("Colonne", "Columns"))
    ws.append([tr("foglio", "sheet"), tr("colonna", "column"), tr("descrizione", "description")])
    for cell in ws[1]:
        cell.font = bold
    for sheet, columns in ((tr("Scommesse", "Bets"), BET_COLUMNS), (tr("Ordini", "Orders"), ORDER_COLUMNS),
                           (tr("Ombra", "Shadow"), SHADOW_COLUMNS),
                           (tr("Capitale", "Equity"), EQUITY_COLUMNS),
                           (tr("Esclusioni", "Exclusions"), EXCLUSION_COLUMNS)):
        for key, desc in _describe(columns).items():
            ws.append([sheet, key, desc])
    ws.append([])
    ws.append([tr("Prezzi e probabilità sono frazioni tra 0 e 1, gli importi in dollari, le ore in UTC.",
                  "Prices and probabilities are fractions between 0 and 1, amounts in dollars, times in UTC.")])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 14, 26, 90
    for row in ws.iter_rows(min_row=2):
        row[-1].alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
