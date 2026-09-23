import contextlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.config import settings
from backend.db.database import init_db
from backend.ingestor.scheduler import start_scheduler, shutdown_scheduler
from backend.api.routes import articles, categories, ingest

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    shutdown_scheduler()

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

# Mount frontend files at root
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")