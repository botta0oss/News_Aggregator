import contextlib
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.config import settings
from backend.db.database import init_db
from backend.ingestor.scheduler import start_scheduler, shutdown_scheduler
from backend.ai import jev
from backend.api.routes import articles, categories, ingest, markets, status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Resolved from the repo root, so it works regardless of the working directory
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    shutdown_scheduler()
    await jev.close_client()

app = FastAPI(title="News Aggregator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "localhost" in settings.FRONTEND_ORIGIN else [settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(articles.router)
app.include_router(categories.router)
app.include_router(ingest.router)
app.include_router(markets.router)
app.include_router(markets.predictions_router)
app.include_router(status.router)

# Mount frontend files at root (only if present: StaticFiles raises at startup on a missing directory)
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
