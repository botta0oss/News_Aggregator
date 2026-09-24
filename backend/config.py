from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/postgres"
    SIMILARITY_THRESHOLD: float = 0.92
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # TypeSafe Jev (Composite Scoring, Classification & Forecasting)
    TYPESAFE_API_KEY: Optional[str] = None
    TYPESAFE_MODEL: str = "jev-latest"

    # Text Summarizer Providers
    SUMMARIZER_PROVIDER: str = "auto"  # "gemini", "groq", "ollama", "auto"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:1.5b"

    # Polymarket (read-only market data via the public Gamma API)
    POLYMARKET_ENABLED: bool = True
    POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com"
    POLYMARKET_SYNC_LIMIT: int = 200          # max active markets kept in sync
    POLYMARKET_MIN_VOLUME: float = 10000.0    # ignore illiquid markets (USD)

    # News -> market matching
    MARKET_MATCH_THRESHOLD: float = 0.55      # cosine similarity article title vs market question
    MARKET_NEWS_WINDOW_HOURS: int = 72
    MARKET_MAX_ARTICLES: int = 8              # articles passed to Jev per prediction

    # Forecasting / betting signals
    PREDICTION_AUTO: bool = False             # run Jev predictions automatically after each ingest
    PREDICTION_MAX_PER_RUN: int = 10
    MODEL_WEIGHT_MAX: float = 0.5             # max weight of Jev vs market price in the blended probability
    MIN_EDGE: float = 0.05                    # minimum |edge| to emit a BUY signal
    MIN_EVIDENCE: float = 0.5                 # minimum normalized evidence strength to emit a signal
    KELLY_FRACTION: float = 0.25              # fractional Kelly sizing

    # Feed fetching
    FEED_TIMEOUT_SECONDS: float = 20.0
    FEED_MAX_BYTES: int = 5_000_000
    ALLOW_PRIVATE_FEEDS: bool = False         # allow feeds on private/internal addresses (SSRF risk)
    AI_BATCH_SIZE: int = 100                  # articles summarized and classified per run

    # Authentication
    SESSION_TTL_HOURS: int = 168              # absolute session lifetime (7 days)
    SESSION_IDLE_MINUTES: int = 720           # session ends after 12 h without activity
    SESSION_COOKIE_SECURE: str = "auto"       # "auto" (Secure except on localhost), "true", "false"
    LOGIN_MAX_ATTEMPTS: int = 5               # failed logins per IP + username before a lockout
    LOGIN_MAX_ATTEMPTS_PER_IP: int = 20       # failed logins per IP (any username)
    LOGIN_WINDOW_MINUTES: int = 15
    ADMIN_USERNAME: Optional[str] = None      # creates the first admin at startup if no user exists
    ADMIN_PASSWORD: Optional[str] = None

    # Scheduler & Server
    INGEST_INTERVAL_MINUTES: int = 60
    CORS_ORIGINS: str = ""                    # comma-separated extra origins; the dashboard itself needs none
    API_DOCS_ENABLED: bool = True             # /docs and /openapi.json (disable in production)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("TYPESAFE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "ADMIN_PASSWORD", mode="before")
    @classmethod
    def _ignore_placeholder_keys(cls, v):
        # Treat empty values and the ".env.example" placeholders as "not configured"
        if v is None:
            return None
        v = str(v).strip()
        if not v or v.startswith("your_"):
            return None
        return v

settings = Settings()
