import uuid
from datetime import date, datetime, time, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend import overrides
from backend.ai import jev
from backend.auth.deps import require_admin
from backend.backtest import engine
from backend.config import settings
from backend.db.database import get_db
from backend.db.models import BacktestCase, BacktestRun
from backend.i18n import tr

router = APIRouter(prefix="/backtest", tags=["backtest"])
admin = [Depends(require_admin)]


class RunIn(BaseModel):
    resolved_after: date
    resolved_before: date
    max_markets: int = Field(30, ge=1, le=300)
    min_volume: float = Field(50_000, ge=0)
    horizons: list[int] = Field(default_factory=lambda: [7])
    max_calls: int = Field(60, ge=1, le=1000)
    exclude_decided: bool = True
    kinds: list[Literal["binary", "multi"]] = Field(default_factory=lambda: ["binary"])
    news_source: Literal["auto", "archive", "google"] = "auto"


class ParametersIn(BaseModel):
    MODEL_WEIGHT_MAX: Optional[float] = None
    MIN_EDGE: Optional[float] = None
    JEV_CALIB_A: Optional[float] = None
    JEV_CALIB_B: Optional[float] = None
    note: Optional[str] = Field(None, max_length=200)


def _run_out(run: BacktestRun, with_summary: bool = True) -> dict:
    out = {
        "id": run.id, "created_at": run.created_at, "finished_at": run.finished_at, "status": run.status,
        "params": run.params, "total": run.total, "done": run.done, "skipped": run.skipped, "failed": run.failed,
        "message": run.message, "running": run.status == "running" and engine.is_running(),
    }
    if with_summary:
        out["summary"] = run.summary
    return out


@router.get("/runs")
async def list_runs(db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(30))).scalars().all()
    return [_run_out(r, with_summary=False) | {"overall": (r.summary or {}).get("overall")} for r in runs]


@router.post("/runs", status_code=202, dependencies=admin)
async def start_run(body: RunIn):
    params = engine.Params(
        resolved_after=datetime.combine(body.resolved_after, time.min, tzinfo=timezone.utc),
        resolved_before=datetime.combine(body.resolved_before, time.max, tzinfo=timezone.utc),
        max_markets=body.max_markets, min_volume=body.min_volume, horizons=body.horizons,
        max_calls=body.max_calls, exclude_decided=body.exclude_decided, kinds=list(body.kinds),
        news_source=body.news_source,
    )
    try:
        run = await engine.start(params)
    except jev.JevUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _run_out(run)


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=tr("Backtest non trovato", "Backtest not found"))
    return _run_out(run)


@router.get("/runs/{run_id}/cases")
async def get_cases(run_id: uuid.UUID, status: Literal["ok", "skipped", "all"] = Query("ok"),
                    db: AsyncSession = Depends(get_db)):
    stmt = select(BacktestCase).where(BacktestCase.run_id == run_id).order_by(BacktestCase.created_at)
    if status != "all":
        stmt = stmt.where(BacktestCase.status == status) if status == "ok" else stmt.where(BacktestCase.status != "ok")
    return [engine.case_dict(c) for c in (await db.execute(stmt)).scalars().all()]


@router.post("/runs/{run_id}/stop", dependencies=admin)
async def stop_run(run_id: uuid.UUID):
    if engine._run_id != run_id or not engine.stop():
        raise HTTPException(status_code=409, detail=tr("Questo backtest non è in corso", "This backtest is not running"))
    return {"status": "stopping"}


@router.delete("/runs/{run_id}", status_code=204, dependencies=admin)
async def delete_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    if engine.is_running() and engine._run_id == run_id:
        raise HTTPException(status_code=409, detail=tr("Ferma prima il backtest in corso", "Stop the running backtest first"))
    await db.execute(delete(BacktestRun).where(BacktestRun.id == run_id))
    await db.commit()


@router.get("/parameters")
async def get_parameters():
    return {"parameters": overrides.current(overrides.FORECAST_KEYS), "min_evidence": settings.MIN_EVIDENCE, "jev_enabled": jev.is_enabled()}


@router.put("/parameters", dependencies=admin)
async def put_parameters(body: ParametersIn, db: AsyncSession = Depends(get_db)):
    values = {k: v for k, v in body.model_dump(exclude={"note"}).items() if v is not None}
    try:
        await overrides.set_values(db, values, note=body.note)
        return {"parameters": overrides.current(overrides.FORECAST_KEYS)}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/parameters/reset", dependencies=admin)
async def reset_parameters(db: AsyncSession = Depends(get_db)):
    await overrides.reset(db, overrides.FORECAST_KEYS)
    return {"parameters": overrides.current(overrides.FORECAST_KEYS)}
