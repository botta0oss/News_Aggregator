import contextlib
import logging
import os
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.config import settings
from backend.db.database import init_db, SessionLocal
from backend.ingestor.scheduler import start_scheduler, shutdown_scheduler
from backend.ai import jev
from backend.auth.deps import require_user
from backend.auth.service import ensure_bootstrap_admin
from backend.api.routes import articles, auth, categories, ingest, markets, status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Resolved from the repo root, so it works regardless of the working directory
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await ensure_bootstrap_admin(db)
    start_scheduler()
    yield
    shutdown_scheduler()
    await jev.close_client()

docs = settings.API_DOCS_ENABLED
app = FastAPI(
    title="News Aggregator", lifespan=lifespan,
    docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None,
)

# The dashboard is served from the same origin and needs no CORS. Extra origins must be
# listed explicitly: a wildcard together with cookie credentials would expose the API.
cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip() and o.strip() != "*"]
if cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=cors_origins, allow_credentials=True,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-CSRF-Token"],
    )

CSP = (
    "default-src 'self'; script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if not request.url.path.startswith("/docs"):  # Swagger UI loads its assets from a CDN
        h.setdefault("Content-Security-Policy", CSP)
    if request.url.scheme == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith(("/auth", "/status")):
        h.setdefault("Cache-Control", "no-store")
    return response

# Public: login endpoints. Everything else requires a signed-in user; the POST endpoints
# that start jobs or paid API calls additionally require the admin role (see the routers).
app.include_router(auth.router)
protected = [Depends(require_user)]
app.include_router(articles.router, dependencies=protected)
app.include_router(categories.router, dependencies=protected)
app.include_router(ingest.router, dependencies=protected)
app.include_router(markets.router, dependencies=protected)
app.include_router(markets.predictions_router, dependencies=protected)
app.include_router(status.router, dependencies=protected)

# Static dashboard files are public (they contain no data); the data comes from the API above.
# Mounted only if present: StaticFiles raises at startup on a missing directory.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
