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
    POLYMARKET_CLOB_URL: str = "https://clob.polymarket.com"
    POLYMARKET_SYNC_LIMIT: int = 200          # max active markets kept in sync
    POLYMARKET_MIN_VOLUME: float = 10000.0    # ignore illiquid markets (USD)

    # News -> market matching
    MARKET_MATCH_THRESHOLD: float = 0.5       # minimum match score (semantic similarity + key terms), 0-1
    MARKET_CANDIDATE_MARGIN: float = 0.15     # candidates: semantic similarity >= threshold - margin
    MARKET_MATCH_TERM_WEIGHT: float = 0.3     # weight of the key-term overlap in the match score
    MARKET_MATCH_NO_ENTITY_PENALTY: float = 0.6  # score multiplier when no name of the question appears
    MARKET_NEWS_WINDOW_HOURS: int = 72
    MARKET_MAX_ARTICLES: int = 8              # articles passed to Jev per prediction
    EVIDENCE_HALF_LIFE_HOURS: float = 48.0    # news weight halves every N hours (floor 25%)
    EVIDENCE_MIN_JEV_RELEVANCE: float = 0.25  # articles Jev judged less relevant than this are dropped

    # Targeted news search per market (Google News RSS)
    TARGETED_NEWS_ENABLED: bool = True
    TARGETED_NEWS_URL: str = "https://news.google.com/rss/search"
    TARGETED_NEWS_LOCALE: str = "hl=en-US&gl=US&ceid=US:en"
    TARGETED_NEWS_MAX_MARKETS: int = 25       # markets searched per pipeline run (most traded first)
    TARGETED_NEWS_REFRESH_HOURS: float = 6.0  # a market is searched again after N hours
    TARGETED_NEWS_MAX_RESULTS: int = 10       # articles kept per search
    TARGETED_NEWS_DAYS: int = 7               # only news from the last N days

    # News-vs-price alerts (the thresholds are edited in the dashboard)
    ALERT_SCAN_MINUTES: int = 10              # fast scan: new news -> links -> alerts, without AI summaries
    ALERT_FOLLOWUP_MINUTES: int = 5           # how often the price after each alert is recorded
    ALERT_TIMEZONE: str = "Europe/Rome"       # for quiet hours
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_API_URL: str = "https://api.telegram.org"
    PUBLIC_URL: str = ""                      # dashboard address, for links in the notifications

    # Forecasting / betting signals
    PREDICTION_AUTO: bool = False             # run Jev predictions automatically after each ingest
    PREDICTION_MAX_PER_RUN: int = 10
    MODEL_WEIGHT_MAX: float = 0.5             # max weight of Jev vs market price in the blended probability
    MIN_EDGE: float = 0.05                    # minimum |edge| to emit a BUY signal
    MIN_EVIDENCE: float = 0.5                 # minimum normalized evidence strength to emit a signal
    KELLY_FRACTION: float = 0.25              # fractional Kelly sizing

    # Betting economics (simulated portfolio)
    RISK_FREE_RATE: float = 0.04              # annual return of the risk-free alternative (e.g. T-bills)
    DEFAULT_FEE_BPS: float = 0.0              # taker fee when the market does not report one
    DEFAULT_SPREAD: float = 0.02              # assumed bid-ask spread when the order book is unavailable
    MODEL_PSEUDO_COUNT: float = 20.0          # how many "observations" a fully-evidenced Jev estimate is worth
    PAPER_BANKROLL: float = 1000.0            # initial simulated bankroll (USD), editable in the dashboard
    PAPER_PRESET: str = "bilanciato"          # prudente / bilanciato / aggressivo

    # Feed fetching
    FEED_TIMEOUT_SECONDS: float = 20.0
    FEED_MAX_BYTES: int = 5_000_000
    ALLOW_PRIVATE_FEEDS: bool = False         # allow feeds on private/internal addresses (SSRF risk)
    AI_BATCH_SIZE: int = 25                   # articles summarized and classified per run

    # AI providers rate limits (requests per minute, client side). Set them to your plan's limits.
    GROQ_RPM: float = 20
    GEMINI_RPM: float = 10
    JEV_RPM: float = 30
    JEV_CONCURRENCY: int = 2
    OLLAMA_CONCURRENCY: int = 1
    AI_MAX_WAIT_SECONDS: float = 90           # longest a background job waits for a provider slot

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
