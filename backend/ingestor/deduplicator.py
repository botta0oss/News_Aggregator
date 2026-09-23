import hashlib
from functools import lru_cache
from urllib.parse import urlparse, urlunparse
from backend.config import settings

@lru_cache(maxsize=1)
def _get_model():
    # Loaded lazily: importing the app (or the tests) must not download/load the model
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.EMBEDDING_MODEL)

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
