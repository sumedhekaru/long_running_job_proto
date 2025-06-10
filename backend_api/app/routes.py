from fastapi import APIRouter, HTTPException, status, Depends
from typing import List
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

# Temporary in-memory storage for demo
jobs_db = {}

class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

class JobRequest(BaseModel):
    user_id: str
    items: List[dict]  # List of items to forecast
    forecast_horizon: int = 30  # Default 30 days forecast

class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    user_id: str
    forecast_horizon: int
    items_count: int

@router.post("/jobs/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job_request: JobRequest):
    """Create a new forecasting job"""
    job_id = f"job_{len(jobs_db) + 1}"
    job = {
        "job_id": job_id,
        "status": JobStatus.QUEUED,
        "created_at": datetime.utcnow(),
        "user_id": job_request.user_id,
        "forecast_horizon": job_request.forecast_horizon,
        "items": job_request.items,
        "items_count": len(job_request.items)
    }
    jobs_db[job_id] = job
    
    # TODO: Enqueue the job to Google Cloud Tasks
    
    return job

@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str):
    """Get the status of a specific job"""
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs_db[job_id]

@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(job_id: str):
    """Cancel a running job"""
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_db[job_id]
    if job["status"] in [JobStatus.COMPLETED, JobStatus.FAILED]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in {job['status']} state"
        )
    
    job["status"] = JobStatus.STOPPED
    # TODO: Implement actual cancellation logic
    
    return {"status": "cancellation requested"}
