from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/postgres"
    SIMILARITY_THRESHOLD: float = 0.92
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    
    # TypeSafe Jev (Composite Scoring & Dimensions)
    TYPESAFE_API_KEY: Optional[str] = None
    
    # Text Summarizer Providers
    SUMMARIZER_PROVIDER: str = "auto"  # "gemini", "groq", "ollama", "auto"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:1.5b"
    
    # Scheduler & Server
    INGEST_INTERVAL_MINUTES: int = 60
    FRONTEND_ORIGIN: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()