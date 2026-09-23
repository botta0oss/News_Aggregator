import hashlib
from urllib.parse import urlparse, urlunparse
from sentence_transformers import SentenceTransformer
from backend.config import settings

# Singleton initialization
model = SentenceTransformer(settings.EMBEDDING_MODEL)

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # Strip tracking, lower netloc, remove trailing slash
    clean_url = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), '', '', ''))
    return clean_url

def hash_url(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

def get_title_embedding(title: str) -> list[float]:
    return model.encode(title).tolist()