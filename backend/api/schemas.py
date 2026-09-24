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

    # Classification details
    source_id: Optional[uuid.UUID] = None
    region: Optional[str] = None
    category_confidence: Optional[float] = None
    is_opinion: Optional[float] = None
    market_relevance: Optional[float] = None
    classifier: Optional[str] = None

    # Search: text with matches wrapped in \u0002 ... \u0003 (the client turns them into highlights)
    title_highlight: Optional[str] = None
    snippet: Optional[str] = None

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
    model_weight: Optional[float] = None
    edge: float
    signal: str
    kelly_fraction: float
    article_count: int
    economics: Optional[dict] = None

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
    resolution: Optional[str] = None
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
    match_score: Optional[float] = None       # similarity + key terms
    matched_terms: List[str] = []
    evidence_score: Optional[float] = None    # match x source quality x recency x Jev relevance
    source_quality: Optional[float] = None
    corroboration: int = 1                    # sources that reported the same story
    targeted: bool = False                    # found by the per-market news search
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

class StatusResponse(BaseModel):
    jev_enabled: bool
    polymarket_enabled: bool
    prediction_auto: bool
    targeted_news_enabled: bool = False
    usage: Optional[dict] = None
    min_edge: float
    min_evidence: float
    model_weight_max: float
    kelly_fraction: float
    market_match_threshold: float
    market_news_window_hours: int
    market_max_articles: int
    sources_active: int = 0
    sources_with_errors: int = 0
    articles: int
    processed_articles: int
    last_article_at: Optional[datetime]
    open_markets: int
    linked_markets: int
    predictions: int
    last_prediction_at: Optional[datetime]
