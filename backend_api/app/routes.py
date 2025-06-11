from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
import concurrent.futures
import pandas as pd
import os
import time

router = APIRouter()

# Temporary in-memory storage for demo
jobs_db: Dict[str, Dict[str, Any]] = {}

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

def forecast_one_item(item, forecast_horizon):
    from prophet import Prophet
    import time
    item_id = item["item_id"]
    history = item["history"]
    start = time.time()
    df = pd.DataFrame(history)
    df["ds"] = pd.to_datetime(df["ds"])
    # Use all available history for each item
    df = df.sort_values("ds")
    print(f"[FastAPI] Forecasting item {item_id} with {len(df)} data points.")
    model = Prophet(yearly_seasonality=False)
    model.fit(df)
    future = model.make_future_dataframe(periods=forecast_horizon)
    forecast = model.predict(future)
    forecast_needed = forecast.tail(forecast_horizon)[["ds", "yhat"]]
    duration = time.time() - start
    print(f"[FastAPI] Forecast for item {item_id} took {duration:.2f} seconds.")
    return {
        "item_id": item_id,
        "forecast": forecast_needed.to_dict(orient="records")
    }

def run_forecasting_background(job_id, job_request_dict):
    print(f"[FastAPI] Background forecasting started for job {job_id}")
    job = jobs_db[job_id]
    try:
        job["status"] = JobStatus.PROCESSING
        items = job_request_dict["items"]
        forecast_horizon = job_request_dict["forecast_horizon"]
        num_workers = os.cpu_count() or 1
        print(f"[FastAPI] Parallel forecasting for {len(items)} items using {num_workers} workers...")
        total_start = time.time()
        job["progress"] = {"done": 0, "total": len(items)}
        job["log"] = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(forecast_one_item, item, forecast_horizon) for item in items]
            forecasts = []
            for idx, future in enumerate(concurrent.futures.as_completed(futures)):
                # Check for cancellation before processing each result
                if job["status"] == JobStatus.STOPPED:
                    cancel_msg = f"Job {job_id} cancelled by user after {idx} of {len(items)} items."
                    print(f"[FastAPI] {cancel_msg}")
                    job["log"].append(cancel_msg)
                    job["status"] = JobStatus.STOPPED
                    break
                try:
                    result = future.result()
                    msg = f"Forecast completed for item {result['item_id']} ({idx+1}/{len(items)})"
                    print(f"[FastAPI] {msg}")
                    forecasts.append(result)
                    job["progress"]["done"] = idx + 1
                    job["log"].append(msg)
                except Exception as e:
                    err_msg = f"Forecast failed for one item: {e}"
                    print(f"[FastAPI] {err_msg}")
                    job["log"].append(err_msg)
        # Only mark as completed if not cancelled
        if job["status"] != JobStatus.STOPPED:
            total_duration = time.time() - total_start
            job["forecasts"] = forecasts
            job["status"] = JobStatus.COMPLETED
            done_msg = f"Job {job_id} completed successfully in {total_duration:.2f} seconds."
            print(f"[FastAPI] {done_msg}")
            job["log"].append(done_msg)
    except Exception as e:
        job["status"] = JobStatus.FAILED
        job["error"] = str(e)
        err_msg = f"Job {job_id} failed: {e}"
        print(f"[FastAPI] {err_msg}")
        job["log"].append(err_msg)


@router.post("/jobs/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job_request: JobRequest, background_tasks: BackgroundTasks):
    """Create a new forecasting job (now async with background task)"""
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
    print(f"[FastAPI] Job {job_id} created. Queued for background forecasting ({len(job_request.items)} items)...")
    # Launch forecasting in the background
    background_tasks.add_task(run_forecasting_background, job_id, job_request.dict())
    return job


from fastapi.responses import JSONResponse

@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get the status of a specific job"""
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs_db[job_id].copy()
    # Convert datetime to isoformat string for JSON serialization
    if isinstance(job.get("created_at"), datetime):
        job["created_at"] = job["created_at"].isoformat()
    # Convert all pandas.Timestamp in forecasts to ISO strings
    if "forecasts" in job and isinstance(job["forecasts"], list):
        for forecast in job["forecasts"]:
            if "forecast" in forecast and isinstance(forecast["forecast"], list):
                for entry in forecast["forecast"]:
                    if "ds" in entry and hasattr(entry["ds"], "isoformat"):
                        entry["ds"] = entry["ds"].isoformat()
    return JSONResponse(content=job)

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
