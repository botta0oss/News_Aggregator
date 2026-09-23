import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, Integer, Float, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
    
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    article = relationship("Article", back_populates="processed")