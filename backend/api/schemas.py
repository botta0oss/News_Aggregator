from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime
import uuid

class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    
    cluster_id: Optional[uuid.UUID] = None
    cluster_source_count: int

class ArticleListResponse(BaseModel):
    total: int
    articles: List[ArticleResponse]

class CategoryResponse(BaseModel):
    name: str
    count: int


# --- Prediction markets ---------------------------------------------------

class PredictionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    market_id: str
    created_at: datetime
    model_name: Optional[str]
    market_probability: float
    model_probability: float
    evidence_strength: float
    blended_probability: float
    edge: float
    signal: str
    kelly_fraction: float
    article_count: int

class MarketResponse(BaseModel):
    id: str
    question: str
    url: Optional[str]
    end_date: Optional[datetime]
    yes_price: Optional[float]
    volume: float
    liquidity: float
    closed: bool
    resolved_yes: Optional[bool]
    linked_articles: int = 0
    latest_prediction: Optional[PredictionResponse] = None

class MarketListResponse(BaseModel):
    total: int
    markets: List[MarketResponse]

class MarketEvidenceResponse(BaseModel):
    article_id: uuid.UUID
    title: str
    url: str
    source_name: str
    published_at: Optional[datetime]
    similarity: float
    relevance: Optional[float]
    impact: Optional[str]
    impact_confidence: Optional[float]

class MarketDetailResponse(MarketResponse):
    description: Optional[str]
    evidence: List[MarketEvidenceResponse]
    predictions: List[PredictionResponse]

class OpportunityResponse(BaseModel):
    market: MarketResponse
    prediction: PredictionResponse

class CalibrationResponse(BaseModel):
    resolved_markets: int
    brier_market: Optional[float]
    brier_model: Optional[float]
    brier_blended: Optional[float]
