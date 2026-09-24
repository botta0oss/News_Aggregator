"""Idempotent schema upgrades.

`create_all` creates missing tables but never alters existing ones, so columns added
after a table was first created are added here. Every statement must be safe to run
on every startup.
"""
from sqlalchemy import text

STATEMENTS = [
    # Sources managed from the dashboard
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS category_hint TEXT",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_fetched_at TIMESTAMPTZ",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_status TEXT",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_error TEXT",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_new_items INTEGER",
    # Richer classification
    "ALTER TABLE processed_articles ADD COLUMN IF NOT EXISTS category_confidence DOUBLE PRECISION",
    "ALTER TABLE processed_articles ADD COLUMN IF NOT EXISTS region TEXT",
    "ALTER TABLE processed_articles ADD COLUMN IF NOT EXISTS is_opinion DOUBLE PRECISION",
    "ALTER TABLE processed_articles ADD COLUMN IF NOT EXISTS market_relevance DOUBLE PRECISION",
    "ALTER TABLE processed_articles ADD COLUMN IF NOT EXISTS classifier TEXT",
    # Forecast explanation
    "ALTER TABLE market_predictions ADD COLUMN IF NOT EXISTS model_weight DOUBLE PRECISION",
    # Economics engine
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS yes_token_id TEXT",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS no_token_id TEXT",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS best_bid DOUBLE PRECISION",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS best_ask DOUBLE PRECISION",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS taker_fee_bps DOUBLE PRECISION",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS order_min_size DOUBLE PRECISION",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS category TEXT",
    "ALTER TABLE market_predictions ADD COLUMN IF NOT EXISTS economics JSONB",
    # Full-text search on title + content ("simple" config: feeds mix languages)
    """CREATE INDEX IF NOT EXISTS ix_articles_fts ON articles
       USING gin (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content_raw, '')))""",
    "CREATE INDEX IF NOT EXISTS ix_articles_source_id ON articles (source_id)",
    "CREATE INDEX IF NOT EXISTS ix_articles_fetched_at ON articles (fetched_at)",
    # Better news <-> market matching and targeted news search
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'feed'",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_embedding vector(384)",
    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS publisher TEXT",
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS targeted_at TIMESTAMPTZ",
    "ALTER TABLE market_article_links ADD COLUMN IF NOT EXISTS match_score DOUBLE PRECISION",
    "ALTER TABLE market_article_links ADD COLUMN IF NOT EXISTS matched_terms JSONB",
    # Alerts: links that existed before are marked as already checked, new ones start unchecked
    "ALTER TABLE market_article_links ADD COLUMN IF NOT EXISTS alert_checked BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE market_article_links ALTER COLUMN alert_checked SET DEFAULT false",
    # Multi-outcome events are kept apart from the YES/NO markets
    "ALTER TABLE markets ADD COLUMN IF NOT EXISTS multi_event_id TEXT",
    "CREATE INDEX IF NOT EXISTS ix_markets_multi_event_id ON markets (multi_event_id)",
]


async def run_migrations(conn) -> None:
    for statement in STATEMENTS:
        await conn.execute(text(statement))
