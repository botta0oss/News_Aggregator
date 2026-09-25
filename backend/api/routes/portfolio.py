import uuid
from datetime import datetime
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.auth.deps import require_admin
from backend.betting import export, plans, portfolio
from backend.betting.profiles import PROFILES, get_profile
from backend.markets.calibration import summary as calibration_summary
from backend.config import settings
from backend.db.database import get_db
from backend.db.models import Market, MarketPrediction, PaperBet, PaperExclusion
from backend.i18n import tr

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
admin = [Depends(require_admin)]


class SettingsIn(BaseModel):
    preset: Optional[Literal["prudente", "bilanciato", "aggressivo"]] = None
    auto_paper: Optional[bool] = None
    auto_sell: Optional[bool] = None


class ResetIn(BaseModel):
    bankroll: float = Field(ge=10, le=10_000_000)
    preset: Optional[Literal["prudente", "bilanciato", "aggressivo"]] = None


class ExclusionIn(BaseModel):
    kind: Literal["market", "event", "category"]
    value: str = Field(min_length=1, max_length=300)
    label: Optional[str] = Field(default=None, max_length=300)


def _market_url(m: Market) -> Optional[str]:
    if m.event_slug:
        return f"https://polymarket.com/event/{m.event_slug}"
    return f"https://polymarket.com/market/{m.slug}" if m.slug else None


def _bet_dict(bet: PaperBet, market: Market) -> dict:
    value = portfolio.mark_value(bet, market) if bet.status == "open" else None
    mid = portfolio.mid_value(bet, market) if bet.status == "open" else None
    return {
        "id": bet.id, "market_id": market.id, "question": market.question, "url": _market_url(market),
        "event_slug": market.event_slug, "category": market.category, "end_date": market.end_date,
        "multi_event_id": market.multi_event_id,
        "side": bet.side, "shares": bet.shares, "avg_price": bet.avg_price, "stake": bet.stake, "fee": bet.fee,
        "outlay": bet.stake + bet.fee, "p_side": bet.p_side, "p_conservative": bet.p_conservative,
        "expected_profit": bet.expected_profit, "preset": bet.preset, "status": bet.status, "placed_by": bet.placed_by,
        "created_at": bet.created_at, "settled_at": bet.settled_at, "payout": bet.payout, "pnl": bet.pnl,
        "current_price": (market.yes_price if bet.side == "YES" else 1 - market.yes_price) if market.yes_price is not None else None,
        "current_value": value,                # selling now at the best bid, sale fee included
        "unrealized_pnl": (value - bet.stake - bet.fee) if value is not None else None,
        "mid_value": mid,
        "unrealized_pnl_mid": (mid - bet.stake - bet.fee) if mid is not None else None,
        "bid_price": plans.known_bid(market, bet.side) if bet.status == "open" else None,
        "clv": portfolio.bet_clv(bet, market),
        "exit_price": bet.exit_price, "exit_reason": bet.exit_reason,
    }


@router.get("")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    data = await portfolio.summary(db)
    data["profile"] = get_profile(data["settings"]["preset"]).as_dict()
    data["presets"] = [p.as_dict() for p in PROFILES.values()]
    data["risk_free_rate"] = settings.RISK_FREE_RATE
    data["calibration_factor"] = await portfolio.calibration_factor(db)
    return data


@router.get("/export")
async def export_portfolio(format: Literal["xlsx", "csv"] = Query("xlsx"), db: AsyncSession = Depends(get_db)):
    """The whole simulated portfolio as it is now: an Excel workbook (summary, bets, equity curve,
    exclusions, column descriptions) or a CSV of the bets."""
    data = await export.build(db)
    if format == "csv":
        body, media = export.to_csv(data), "text/csv; charset=utf-8"
    else:
        body, media = export.to_xlsx(data), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    name = export.filename(data["now"], format)
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.put("/settings", dependencies=admin)
async def update_settings(body: SettingsIn, db: AsyncSession = Depends(get_db)):
    s = await portfolio.update_settings(db, preset=body.preset, auto_paper=body.auto_paper, auto_sell=body.auto_sell)
    return {"bankroll": s.bankroll, "preset": s.preset, "auto_paper": s.auto_paper, "auto_sell": s.auto_sell}


@router.post("/reset", dependencies=admin)
async def reset(body: ResetIn, db: AsyncSession = Depends(get_db)):
    """Deletes all simulated bets and starts over with a new capital."""
    s = await portfolio.reset_portfolio(db, body.bankroll, body.preset)
    return {"bankroll": s.bankroll, "preset": s.preset, "auto_paper": s.auto_paper}


@router.post("/bets/{bet_id}/sell", dependencies=admin)
async def sell_bet_now(bet_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Sells an open simulated bet now, at the best bids of the order book."""
    bet = await _bet(db, bet_id)
    if bet.status != "open":
        raise HTTPException(status_code=409, detail=tr("La scommessa non è aperta", "The bet is not open"))
    market = await db.get(Market, bet.market_id)
    if market.closed:
        raise HTTPException(status_code=409, detail=tr("Il mercato è chiuso: si attende la risoluzione", "The market is closed: waiting for resolution"))
    if not await portfolio.sell_bet(db, bet, market, "Venduta a mano"):
        raise HTTPException(status_code=409, detail=tr("Il book non ha abbastanza offerte di acquisto per vendere tutte le quote", "The book does not have enough bids to sell all the shares"))
    return _bet_dict(bet, market)


@router.get("/bets")
async def list_bets(status: Literal["open", "settled", "excluded", "all"] = Query("all"),
                    limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)):
    stmt = select(PaperBet, Market).join(Market, Market.id == PaperBet.market_id)
    if status == "open":
        stmt = stmt.where(PaperBet.status == "open")
    elif status == "settled":
        stmt = stmt.where(PaperBet.status.in_(portfolio.SETTLED))
    elif status == "excluded":
        stmt = stmt.where(PaperBet.status == "excluded")
    rows = (await db.execute(stmt.order_by(PaperBet.created_at.desc()).limit(limit))).all()
    out = [_bet_dict(b, m) for b, m in rows]
    # Open bets: the exit plan (sale target, or sell now) with the latest forecast
    profile = get_profile((await portfolio.get_settings(db)).preset)
    for item, (bet, market) in zip(out, rows):
        plan = await portfolio.open_bet_plan(db, bet, market, profile)
        if plan is not None:
            item["plan"] = plan
    return out


async def _bet(db: AsyncSession, bet_id: uuid.UUID) -> PaperBet:
    bet = await db.get(PaperBet, bet_id)
    if bet is None:
        raise HTTPException(status_code=404, detail=tr("Scommessa non trovata", "Bet not found"))
    return bet


@router.post("/bets/{bet_id}/exclude", dependencies=admin)
async def exclude_bet(bet_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Removes a bet from the simulation (kept in the list, not counted in capital and results).
    Only the status changes: payout, profit and closing time stay, so readmitting it restores
    the bet as it was (ledger and summary count bets by status)."""
    bet = await _bet(db, bet_id)
    bet.status = "excluded"
    await db.commit()
    return {"status": bet.status}


@router.post("/bets/{bet_id}/include", dependencies=admin)
async def include_bet(bet_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bet = await _bet(db, bet_id)
    if bet.status != "excluded":
        return {"status": bet.status}
    other_open = (await db.execute(select(PaperBet.id).where(
        PaperBet.market_id == bet.market_id, PaperBet.status == "open", PaperBet.id != bet.id))).first()
    if other_open and bet.exit_price is None:
        raise HTTPException(status_code=409, detail=tr("C'è già una scommessa aperta su questo mercato", "There is already an open bet on this market"))
    if bet.exit_price is not None:
        bet.status = "sold"          # sold before being excluded: the shares are no longer held
        if bet.pnl is None:          # excluded before exclusions kept the result: rebuild it from the sale
            bet.pnl = bet.exit_price * bet.shares - bet.stake - bet.fee
            bet.settled_at = bet.settled_at or portfolio._now()
        await db.commit()
    else:
        bet.status = "open"
        await db.commit()
        await portfolio.settle_bets(db)  # settles it right away if the market has resolved
    await db.refresh(bet)
    return {"status": bet.status}


@router.get("/exclusions")
async def list_exclusions(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PaperExclusion).order_by(PaperExclusion.created_at.desc()))).scalars().all()
    return [{"id": r.id, "kind": r.kind, "value": r.value, "label": r.label, "created_at": r.created_at} for r in rows]


@router.post("/exclusions", status_code=201, dependencies=admin)
async def add_exclusion(body: ExclusionIn, db: AsyncSession = Depends(get_db)):
    row = await portfolio.add_exclusion(db, body.kind, body.value.strip(), body.label)
    return {"id": row.id, "kind": row.kind, "value": row.value, "label": row.label}


@router.delete("/exclusions/{exclusion_id}", status_code=204, dependencies=admin)
async def remove_exclusion(exclusion_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = await db.get(PaperExclusion, exclusion_id)
    if row is None:
        raise HTTPException(status_code=404, detail=tr("Esclusione non trovata", "Exclusion not found"))
    await db.delete(row)
    await db.commit()


# ---------- Per-market evaluation (mounted under /markets) ----------

markets_router = APIRouter(prefix="/markets", tags=["portfolio"])


async def _latest(db: AsyncSession, market_id: str):
    market = await db.get(Market, market_id)
    if market is None:
        raise HTTPException(status_code=404, detail=tr("Mercato non trovato", "Market not found"))
    if market.multi_event_id:
        # Outcome of a multi-outcome event: its share of the latest distribution forecast
        from backend.db.models import MultiPrediction
        from backend.multi.service import outcome_prediction
        latest = (await db.execute(select(MultiPrediction).where(MultiPrediction.event_id == market.multi_event_id)
                                   .order_by(MultiPrediction.created_at.desc()).limit(1))).scalar_one_or_none()
        prediction = outcome_prediction(latest, market_id) if latest else None
        if prediction is None:
            raise HTTPException(status_code=409, detail=tr("L'evento non ha ancora una previsione per questo esito", "The event has no forecast for this outcome yet"))
        return market, prediction
    prediction = (await db.execute(select(MarketPrediction).where(MarketPrediction.market_id == market_id)
                                   .order_by(MarketPrediction.created_at.desc()).limit(1))).scalar_one_or_none()
    if prediction is None:
        raise HTTPException(status_code=409, detail=tr("Il mercato non ha ancora una previsione", "The market has no forecast yet"))
    return market, prediction


@markets_router.get("/{market_id}/economics")
async def market_economics(market_id: str, preset: Optional[Literal["prudente", "bilanciato", "aggressivo"]] = None,
                           db: AsyncSession = Depends(get_db)):
    """Live evaluation of the latest forecast: current order book, current portfolio."""
    market, prediction = await _latest(db, market_id)
    ev = await portfolio.evaluate_prediction(db, market, prediction, preset=preset)
    await db.commit()  # keeps the refreshed market category
    open_bet = (await db.execute(select(PaperBet).where(PaperBet.market_id == market_id, PaperBet.status == "open"))).scalar_one_or_none()
    profile = get_profile(preset or (await portfolio.get_settings(db)).preset)
    excluded_by = await portfolio.excluded_reason(db, market)
    plan = await plans.plan_for(db, market, prediction, ev, profile, track=await calibration_summary(db),
                                excluded_by=excluded_by, position=open_bet)
    return {
        "strategy": plan.as_dict(),
        "evaluation": ev.as_dict(),
        "prediction_id": prediction.id,
        "prediction_created_at": prediction.created_at,
        "preset": profile.as_dict(),
        "excluded_by": excluded_by,
        "open_bet": _bet_dict(open_bet, market) if open_bet else None,
        "market": {"id": market.id, "event_slug": market.event_slug, "category": market.category, "question": market.question,
                   "yes_price": market.yes_price},
        "evaluated_at": datetime.now().astimezone(),
    }


@markets_router.post("/{market_id}/paper-bet", dependencies=admin)
async def place_manual_bet(market_id: str, db: AsyncSession = Depends(get_db)):
    """Places the simulated bet now (ignores exclusions and the automatic setting, not the economics)."""
    market, prediction = await _latest(db, market_id)
    ev = await portfolio.evaluate_prediction(db, market, prediction)
    if ev.verdict not in ("GO", "SMALL"):
        reasons = "; ".join(r["text"] for r in ev.as_dict()["reasons"] if r["blocking"])
        raise HTTPException(status_code=409, detail=tr(f"Non conviene: {reasons}", f"Not worth it: {reasons}"))
    bet = await portfolio.maybe_place_bet(db, market, prediction, ev, placed_by="manual")
    if bet is None:
        raise HTTPException(status_code=409, detail=tr("C'è già una scommessa aperta su questo mercato", "There is already an open bet on this market"))
    return _bet_dict(bet, market)
