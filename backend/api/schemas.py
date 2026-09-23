from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid

class ArticleResponse(BaseModel):
    id: uuid.UUID
    title: str
    url: str
    source_name: str
    published_at: Optional[datetime]
    summary: Optional[str]
    category: Optional[str]
    
    # Multidimensional Scores (0.0 - 1.0)
    clickbait_score: Optional[float] = 0.0
    authority_score: Optional[float] = 0.5
    technical_depth_score: Optional[float] = 0.5
    urgency_score: Optional[float] = 0.5
    composite_score: Optional[float] = 0.5
    
    # Legacy scale (1-10)
    importance_score: Optional[int] = 5
    
    cluster_id: Optional[uuid.UUID]
    cluster_source_count: int

    class Config:
        from_attributes = True

class ArticleListResponse(BaseModel):
    total: int
    articles: List[ArticleResponse]

class CategoryResponse(BaseModel):
    name: str
    count: int