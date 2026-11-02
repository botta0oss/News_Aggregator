"""API keys and tokens from the dashboard (admins only): never sent back, only whether they are set."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend import apikeys
from backend.auth.deps import require_admin
from backend.db.database import get_db

router = APIRouter(prefix="/settings/api-keys", tags=["settings"], dependencies=[Depends(require_admin)])


class KeysIn(BaseModel):
    # Missing field = unchanged; "" = remove the dashboard value (back to .env)
    TYPESAFE_API_KEY: Optional[str] = Field(default=None, max_length=300)
    GEMINI_API_KEY: Optional[str] = Field(default=None, max_length=300)
    GROQ_API_KEY: Optional[str] = Field(default=None, max_length=300)
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(default=None, max_length=200)
    TELEGRAM_CHAT_ID: Optional[str] = Field(default=None, max_length=100)


@router.get("")
async def get_keys(db: AsyncSession = Depends(get_db)):
    return await apikeys.current(db)


@router.put("")
async def put_keys(body: KeysIn, db: AsyncSession = Depends(get_db)):
    try:
        return await apikeys.set_values(db, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
