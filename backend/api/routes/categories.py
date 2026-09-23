from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.db.database import get_db
from backend.db.models import ProcessedArticle
from backend.api.schemas import CategoryResponse
from typing import List

router = APIRouter(prefix="/categories", tags=["categories"])

@router.get("", response_model=List[CategoryResponse])
async def get_categories(db: AsyncSession = Depends(get_db)):
    query = select(ProcessedArticle.category, func.count(ProcessedArticle.id))\
        .group_by(ProcessedArticle.category)
    result = await db.execute(query)
    
    categories =[{"name": row[0], "count": row[1]} for row in result.all() if row[0]]
    return categories