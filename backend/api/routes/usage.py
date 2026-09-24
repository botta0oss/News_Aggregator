from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from backend import overrides
from backend.ai import usage
from backend.auth.deps import require_admin
from backend.db.database import get_db

router = APIRouter(prefix="/usage", tags=["usage"])


class LimitsIn(BaseModel):
    DAILY_JEV_CALL_LIMIT: Optional[int] = Field(None, ge=0, le=100_000)
    DAILY_AI_BUDGET_USD: Optional[float] = Field(None, ge=0, le=10_000)
    JEV_PRICE_PER_CALL: Optional[float] = Field(None, ge=0, le=10)
    JEV_PRICE_INPUT_MTOK: Optional[float] = Field(None, ge=0, le=1000)
    JEV_PRICE_OUTPUT_MTOK: Optional[float] = Field(None, ge=0, le=1000)
    GROQ_PRICE_INPUT_MTOK: Optional[float] = Field(None, ge=0, le=1000)
    GROQ_PRICE_OUTPUT_MTOK: Optional[float] = Field(None, ge=0, le=1000)
    GEMINI_PRICE_INPUT_MTOK: Optional[float] = Field(None, ge=0, le=1000)
    GEMINI_PRICE_OUTPUT_MTOK: Optional[float] = Field(None, ge=0, le=1000)


@router.get("")
async def get_usage(days: int = Query(30, ge=1, le=90), db: AsyncSession = Depends(get_db)):
    """Today's counters against the limits, per-day history by provider and feature, settings."""
    return {"today": await usage.today_status(), "report": await usage.report(db, days),
            "settings": overrides.current(overrides.USAGE_KEYS)}


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_settings(body: LimitsIn, db: AsyncSession = Depends(get_db)):
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        await overrides.set_values(db, values, note="Uso e costi")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"today": await usage.today_status(), "settings": overrides.current(overrides.USAGE_KEYS)}


@router.post("/settings/reset", dependencies=[Depends(require_admin)])
async def reset_settings(db: AsyncSession = Depends(get_db)):
    await overrides.reset(db, overrides.USAGE_KEYS)
    return {"today": await usage.today_status(), "settings": overrides.current(overrides.USAGE_KEYS)}
