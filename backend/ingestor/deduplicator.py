import hashlib
import logging
from functools import lru_cache
from urllib.parse import urlparse, urlunparse
from backend.config import settings

@lru_cache(maxsize=1)
def _get_model():
    # Loaded lazily: importing the app (or the tests) must not download/load the model
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(settings.EMBEDDING_MODEL)  # uses the GPU when torch sees one
    logging.getLogger(__name__).info(f"Embedding model {settings.EMBEDDING_MODEL} on {model.device}")
    return model

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # Strip tracking, lower netloc, remove trailing slash
    clean_url = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), '', '', ''))
    return clean_url

def hash_url(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

def get_title_embedding(title: str) -> list[float]:
    return _get_model().encode(title).tolist()


def embedding_text(title: str, content: str | None, max_chars: int = 600) -> str:
    """Headline plus the opening of the text: what the article is actually about."""
    lead = " ".join((content or "").split())[:max_chars]
    return f"{title}. {lead}" if lead and lead.lower() not in title.lower() else title


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch encoding (much faster than one call per text)."""
    if not texts:
        return []
    return [v.tolist() for v in _get_model().encode(texts)]
