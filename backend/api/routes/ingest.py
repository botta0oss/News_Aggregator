from fastapi import APIRouter, BackgroundTasks, Depends, Query
from backend.auth.deps import require_admin
from backend.ingestor.scheduler import reclassify_articles, run_ingestion_pipeline

router = APIRouter(prefix="/ingest", tags=["ingestion"])

@router.post("", dependencies=[Depends(require_admin)])
async def trigger_ingestion(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ingestion_pipeline)
    return {"status": "started", "message": "Ingestion job triggered"}

@router.post("/reclassify", dependencies=[Depends(require_admin)])
async def trigger_reclassify(background_tasks: BackgroundTasks, limit: int = Query(200, ge=1, le=1000)):
    """Re-classifies stored articles with the current classifier (Jev if configured)."""
    background_tasks.add_task(reclassify_articles, limit)
    return {"status": "started", "message": "Reclassification triggered"}
