from fastapi import APIRouter, BackgroundTasks
from backend.ingestor.scheduler import run_ingestion_pipeline

router = APIRouter(prefix="/ingest", tags=["ingestion"])

@router.post("")
async def trigger_ingestion(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ingestion_pipeline)
    return {"status": "started", "message": "Ingestion job triggered"}