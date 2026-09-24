import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Integer, Float, Text, ForeignKey, DateTime, UniqueConstraint, Index
from typing import Optional
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Main topic of the feed (e.g. "Crypto"), used as a hint by the classifier
    category_hint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Outcome of the last fetch, shown in the settings page
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # ok / error
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_new_items: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "feed": fetched on schedule; "targeted": results of the per-market news search
    kind: Mapped[str] = mapped_column(Text, default="feed", server_default="feed")

class Cluster(Base):
    __tablename__ = 'clusters'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source_count: Mapped[int] = mapped_column(Integer, default=1)

class Article(Base):
    __tablename__ = 'articles'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('sources.id'))
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('clusters.id'), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_raw: Mapped[str] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    url_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title_embedding = mapped_column(Vector(384))
    # Title + opening of the text: matches markets even when the headline is vague
    content_embedding = mapped_column(Vector(384), nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # original outlet (aggregated results)
    
    source = relationship("Source")
    cluster = relationship("Cluster")
    processed = relationship("ProcessedArticle", back_populates="article", uselist=False)

class ProcessedArticle(Base):
    __tablename__ = 'processed_articles'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('articles.id'), unique=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=True)
    
    # Multidimensional Quality Dimensions (0.0 to 1.0)
    clickbait_score: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    authority_score: Mapped[float] = mapped_column(Float, nullable=True, default=0.5)
    technical_depth_score: Mapped[float] = mapped_column(Float, nullable=True, default=0.5)
    urgency_score: Mapped[float] = mapped_column(Float, nullable=True, default=0.5)
    
    # Global Default Composite Score (0.0 to 1.0)
    composite_score: Mapped[float] = mapped_column(Float, nullable=True, default=0.5)
    # Legacy scale (1 to 10)
    importance_score: Mapped[int] = mapped_column(Integer, nullable=True, default=5)

    # Classification details
    category_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    region: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_opinion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)        # P(opinion / analysis piece)
    market_relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-1, affects bettable events
    classifier: Mapped[Optional[str]] = mapped_column(Text, nullable=True)           # jev / heuristic
    
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    article = relationship("Article", back_populates="processed")


class Market(Base):
    """A binary (Yes/No) Polymarket market, synced read-only from the Gamma API."""
    __tablename__ = 'markets'
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Polymarket market id
    question: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # resolution rules
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    yes_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # implied P(YES), 0-1
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_yes: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # set once resolved
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # yes / no / split, once final
    # Last YES price seen while the market was still trading: the "closing line" for CLV
    last_trading_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Trading data for the economics engine
    yes_token_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    no_token_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    best_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    taker_fee_bps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    order_min_size: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # dominant category of the linked news
    question_embedding = mapped_column(Vector(384), nullable=True)
    targeted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # last news search
    # Outcome of a multi-outcome event: shown under "Più esiti", not among the YES/NO markets
    multi_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MarketArticleLink(Base):
    """News article semantically related to a market (candidate evidence for the forecast)."""
    __tablename__ = 'market_article_links'
    __table_args__ = (UniqueConstraint('market_id', 'article_id', name='uq_market_article'),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id: Mapped[str] = mapped_column(ForeignKey('markets.id', ondelete='CASCADE'), index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('articles.id', ondelete='CASCADE'), index=True)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)       # similarity + key terms
    matched_terms: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)      # key terms found in the article
    alert_checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # seen by the alert scan
    # Filled by Jev at prediction time
    relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # P(article is relevant)
    impact: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # raises_yes / lowers_yes / neutral
    impact_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    article = relationship("Article")


class MarketPrediction(Base):
    """A Jev forecast for a market, compared with the market price at prediction time."""
    __tablename__ = 'market_predictions'
    __table_args__ = (Index('ix_market_predictions_market_created', 'market_id', 'created_at'),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id: Mapped[str] = mapped_column(ForeignKey('markets.id', ondelete='CASCADE'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    model_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    market_probability: Mapped[float] = mapped_column(Float, nullable=False)   # price at prediction time
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)    # raw Jev P(YES)
    calibrated_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # after Platt scaling
    blend_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # logodds / linear
    model_samples: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Jev calls averaged
    evidence_strength: Mapped[float] = mapped_column(Float, nullable=False)    # 0-1
    blended_probability: Mapped[float] = mapped_column(Float, nullable=False)  # shrunk toward market
    model_weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # weight w of Jev in the blend
    edge: Mapped[float] = mapped_column(Float, nullable=False)                 # blended - market
    signal: Mapped[str] = mapped_column(Text, nullable=False)                  # BUY_YES / BUY_NO / HOLD
    kelly_fraction: Mapped[float] = mapped_column(Float, default=0.0)          # suggested bankroll fraction
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    economics: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # economic evaluation at prediction time


class User(Base):
    """Dashboard account. Passwords are stored only as Argon2id hashes."""
    __tablename__ = 'users'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # normalized lower-case
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")  # admin / viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserSession(Base):
    """Server-side login session. Only the SHA-256 of the cookie token is stored."""
    __tablename__ = 'user_sessions'
    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ip: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user = relationship("User")



class BettingSettings(Base):
    """Settings of the simulated portfolio (single row, id = 1)."""
    __tablename__ = 'betting_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bankroll: Mapped[float] = mapped_column(Float, nullable=False)        # initial capital (USD)
    preset: Mapped[str] = mapped_column(Text, nullable=False)
    auto_paper: Mapped[bool] = mapped_column(Boolean, default=True)       # bet automatically on every GO/SMALL
    auto_sell: Mapped[bool] = mapped_column(Boolean, default=True)        # sell open bets when the plan says so
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PaperBet(Base):
    """A simulated bet, placed at the executable price of the moment and settled at resolution."""
    __tablename__ = 'paper_bets'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id: Mapped[str] = mapped_column(ForeignKey('markets.id', ondelete='CASCADE'), index=True)
    prediction_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('market_predictions.id', ondelete='SET NULL'), nullable=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)                # YES / NO
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)            # USD spent on shares
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    p_side: Mapped[float] = mapped_column(Float, nullable=False)           # blended P(side wins) at entry
    p_conservative: Mapped[float] = mapped_column(Float, nullable=False)
    expected_profit: Mapped[float] = mapped_column(Float, default=0.0)
    preset: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="open", index=True)  # open / won / lost / void / sold / excluded
    placed_by: Mapped[str] = mapped_column(Text, default="auto")          # auto / manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payout: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # average sale price, if sold
    exit_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)     # why it was sold

    market = relationship("Market")


class PaperExclusion(Base):
    """Markets, events or categories the automatic simulated betting must skip."""
    __tablename__ = 'paper_exclusions'
    __table_args__ = (UniqueConstraint('kind', 'value', name='uq_paper_exclusion'),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)    # market / event / category
    value: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AlertSettings(Base):
    """News-vs-price alerts settings (single row, id = 1). Telegram credentials stay in .env."""
    __tablename__ = 'alert_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    categories: Mapped[list] = mapped_column(JSONB, default=list)           # empty = all categories
    min_match: Mapped[float] = mapped_column(Float, default=0.6)            # minimum news <-> market match
    max_news_age_hours: Mapped[float] = mapped_column(Float, default=6.0)   # only fresh news triggers
    daily_budget: Mapped[int] = mapped_column(Integer, default=30)          # Jev calls per 24 h
    cooldown_hours: Mapped[float] = mapped_column(Float, default=3.0)       # per market
    min_verdict: Mapped[str] = mapped_column(Text, default="SMALL")         # notify GO only, or GO + SMALL
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # hour 0-23, silent notifications
    quiet_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Alert(Base):
    """A fresh, relevant news item for a market, the immediate Jev evaluation and the price afterwards."""
    __tablename__ = 'alerts'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id: Mapped[str] = mapped_column(ForeignKey('markets.id', ondelete='CASCADE'), index=True)
    multi_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)  # alert on a multi-outcome event
    article_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('articles.id', ondelete='SET NULL'), nullable=True)
    prediction_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('market_predictions.id', ondelete='SET NULL'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    news_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)   # publication of the trigger
    trigger_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)          # P(YES) when the alert was raised
    side: Mapped[Optional[str]] = mapped_column(Text, nullable=True)     # YES / NO
    verdict: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # GO / SMALL / NO
    signal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    blended: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    outlay: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    opportunity: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followups: Mapped[dict] = mapped_column(JSONB, default=dict)         # {"15m": price, "1h": ..., "6h": ..., "24h": ...}

    market = relationship("Market")
    article = relationship("Article")


class BacktestRun(Base):
    """A backtest on resolved markets: parameters, progress and summary."""
    __tablename__ = 'backtest_runs'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="running")   # running / done / stopped / failed
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    total: Mapped[int] = mapped_column(Integer, default=0)          # cases planned
    done: Mapped[int] = mapped_column(Integer, default=0)          # cases evaluated with Jev
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class BacktestCase(Base):
    """One market evaluated as of a past date, with the price of that moment and the real outcome."""
    __tablename__ = 'backtest_cases'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('backtest_runs.id', ondelete='CASCADE'), index=True)
    market_id: Mapped[str] = mapped_column(Text, nullable=False)       # Polymarket id (not in the markets table)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_yes: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="ok")             # ok / skipped / error
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)            # P(YES) at as_of
    news_count: Mapped[int] = mapped_column(Integer, default=0)
    model_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_strength: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    blended_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    signal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verdict: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    side: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outlay: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    news: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)   # titles and sources passed to Jev
    # "binary" or "multi"; for multi-outcome events price / model / blended are those of the winner and
    # `details` holds the whole distribution and the multi-class Brier scores
    kind: Mapped[str] = mapped_column(Text, default="binary", server_default="binary")
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AppMeta(Base):
    """Internal facts about the stored data (e.g. which embedding model produced the vectors)."""
    __tablename__ = 'app_meta'
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AppSetting(Base):
    """Forecast parameters changed from the dashboard; they override the .env values."""
    __tablename__ = 'app_settings'
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class MultiEvent(Base):
    """Polymarket event with several mutually exclusive outcomes (e.g. "Who will win the election?").

    Each outcome is a YES/NO market on Polymarket; here they are kept together, apart from the
    single YES/NO markets, because they are read and forecast as one distribution.
    """
    __tablename__ = 'multi_events'
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Gamma event id
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    winner_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # outcome that resolved YES
    title_embedding = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MultiOutcome(Base):
    __tablename__ = 'multi_outcomes'
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Polymarket market id of this outcome
    event_id: Mapped[str] = mapped_column(ForeignKey('multi_events.id', ondelete='CASCADE'), index=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)    # e.g. the candidate's name
    question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    yes_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_yes: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    yes_token_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MultiArticleLink(Base):
    __tablename__ = 'multi_article_links'
    __table_args__ = (UniqueConstraint('event_id', 'article_id', name='uq_multi_article'),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(ForeignKey('multi_events.id', ondelete='CASCADE'), index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('articles.id', ondelete='CASCADE'), index=True)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    matched_terms: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    impact: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # outcome favoured by the news, if any
    impact_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alert_checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MultiPrediction(Base):
    __tablename__ = 'multi_predictions'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(ForeignKey('multi_events.id', ondelete='CASCADE'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    model_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_strength: Mapped[float] = mapped_column(Float, nullable=False)
    model_weight: Mapped[float] = mapped_column(Float, nullable=False)
    # [{"id", "label", "market", "model", "blended", "edge"}], "Other" bucket included; market = normalised price
    outcomes: Mapped[list] = mapped_column(JSONB, nullable=False)
    best_outcome_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    best_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    signal: Mapped[str] = mapped_column(Text, default="HOLD")   # BUY_YES (on best_outcome_id) / HOLD
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    economics: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # {outcome_id: evaluation}


class ApiUsage(Base):
    """One paid AI call (Jev, Groq, Gemini): what it was for, tokens and estimated cost."""
    __tablename__ = 'api_usage'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)     # jev / groq / gemini
    feature: Mapped[str] = mapped_column(Text, nullable=False)      # classificazione, previsioni, allerte...
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
