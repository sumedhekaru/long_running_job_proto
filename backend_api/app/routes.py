from fastapi import APIRouter, HTTPException, status, Request
from typing import List
from enum import Enum
import pandas as pd
import time
import os
import logging
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from prophet import Prophet
import numpy as np
from pandas import Timestamp
import base64
import json
from fastapi.responses import Response
import datetime
import uuid
import io


load_dotenv()


# --- Logging setup ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)
logger = logging.getLogger(__name__)

router = APIRouter()

# --- Initialize SQLAlchemy engine with connection pool ---
DATABASE_URL = os.getenv("DATABASE_URL")
pool_size = int(os.getenv("DB_POOL_SIZE", 10))
engine = create_engine(
    DATABASE_URL,
    pool_size=pool_size
)
logger.info(f"Logged to DB with pool_size: {pool_size}")

def forecast_one_item(item, forecast_horizon):
    """
    Run Prophet forecasting for a single item. Expects item dict with 'item_nbr' and 'history'.
    Returns a DataFrame with columns ["ds", "yhat", "item_nbr"].
    """
    item_id = item["item_nbr"]
    history = item["history"]
    start = time.time()
    df = pd.DataFrame(history)
    df["ds"] = pd.to_datetime(df["ds"])
    df = df.sort_values("ds")
    logger.debug(f"[FastAPI] Forecasting item {item_id} with {len(df)} data points.")
    yearly_seasonality = len(df) >= 104
    weekly_seasonality = len(df) >= 30
    model = Prophet(yearly_seasonality=yearly_seasonality, weekly_seasonality=weekly_seasonality)
    model.fit(df)
    future = model.make_future_dataframe(periods=forecast_horizon, freq='W')
    forecast = model.predict(future)
    forecast_needed = forecast.tail(forecast_horizon)[["ds", "yhat"]]
    forecast_needed["yhat"] = forecast_needed["yhat"].clip(lower=0)
    forecast_needed["item_nbr"] = item_id
    duration = time.time() - start
    logger.info(f"[FastAPI] Forecast for item {item_id} took {duration:.2f} seconds.")
    return forecast_needed

def update_batch_status(batch_id, status, conn=None):
    """
    Update the status of a batch in the database using SQLAlchemy engine.
    """
    if conn is not None:
        conn.execute(
            text("""
                UPDATE fcst_app.batch_status
                SET status = :status, updated_at = NOW()
                WHERE batch_id = :batch_id
            """),
            {"status": status, "batch_id": batch_id}
        )
    else:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE fcst_app.batch_status
                    SET status = :status, updated_at = NOW()
                    WHERE batch_id = :batch_id
                """),
                {"status": status, "batch_id": batch_id}
            )

# --- New Batch Submission Endpoint ---
from fastapi import BackgroundTasks

@router.post("/pubsub")
async def pubsub_push(request: Request, background_tasks: BackgroundTasks):
    envelope = await request.json()
    try:
        message = envelope["message"]
        data = base64.b64decode(message["data"]).decode("utf-8")
        payload = json.loads(data)
        job_id = int(payload["job_id"])
        batch_id = int(payload["batch_id"])
        forecast_horizon = int(payload.get("forecast_horizon", 30))
        logger.info(f"[PubSub] Received push for job_id={job_id}, batch_id={batch_id}, forecast_horizon={forecast_horizon}")
        logger.debug(f"[PubSub] payload={payload}")
        logger.debug(f"[PubSub] job_id={job_id} (type={type(job_id)}), batch_id={batch_id} (type={type(batch_id)}), forecast_horizon={forecast_horizon} (type={type(forecast_horizon)})")
        # Schedule the processing as a background task
        background_tasks.add_task(process_batch_forecasting, job_id, batch_id, forecast_horizon)
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"[PubSub] Error processing push: {e}")
        return Response(status_code=400)

@router.post("/jobs/batch")
async def submit_batch(request: Request, background_tasks: BackgroundTasks):
    """
    Accepts a batch submission with job_id and batch_id. Immediately returns to frontend, then processes forecasting in background.
    """
    data = await request.json()
    job_id = data.get("job_id")
    batch_id = data.get("batch_id")
    forecast_horizon = data.get("forecast_horizon", 30)
    # Use SQLAlchemy engine for DB connection
    with engine.begin() as conn:
        update_batch_status(batch_id, "Submitted.", conn)
    logger.info(f"[FastAPI] Received batch submission: job_id={job_id}, batch_id={batch_id}")

    # Schedule background processing (background task will handle its own DB connections)
    background_tasks.add_task(process_batch_forecasting, job_id, batch_id, forecast_horizon)
    return {"status": "ok", "job_id": job_id, "batch_id": batch_id}


def update_job_status_if_complete(job_id, conn):
    result = conn.execute(
        text("""
            SELECT COUNT(*) FROM fcst_app.batch_status
            WHERE job_id = :job_id AND status NOT IN ('Completed', 'Failed')
        """),
        {"job_id": job_id}
    )
    remaining = result.scalar()
    if remaining == 0:
        logger.info(f"[FastAPI] All batches for job {job_id} completed.")
        conn.execute(
            text("""
                UPDATE fcst_app.forecast_request
                SET status = 'completed', updated_at = NOW()
                WHERE job_id = :job_id
            """),
            {"job_id": job_id}
        )


def is_job_cancelled(job_id, conn):
    result = conn.execute(
        text("""
            SELECT status FROM fcst_app.forecast_request WHERE job_id = :job_id
        """),
        {"job_id": job_id}
    )
    row = result.fetchone()
    if row and row[0] and row[0].lower() == "cancelled":
        print(f"[FastAPI] Job {job_id} is cancelled.")
        return True
    return False

def update_job_batch(job_id, batch_id, msg, conn=None):
    if conn is not None:
        update_batch_status(batch_id, msg, conn)
        update_job_status_if_complete(job_id, conn)
    else:
        with engine.begin() as conn2:
            update_batch_status(batch_id, msg, conn2)
            update_job_status_if_complete(job_id, conn2)


def batch_forecast(df_hist, forecast_horizon, job_id, batch_id, conn):
    #update_job_batch(job_id, batch_id, "Forecasting batch", conn)
    try:
        start_time = time.time()
        dfs = []
        for item_id, item_hist in df_hist.groupby("item_nbr"):
            item = {"item_nbr": item_id, "history": item_hist[["ds", "y"]].to_dict(orient="records")}
            try:
                fcst_df = forecast_one_item(item, forecast_horizon)
                dfs.append(fcst_df)
            except Exception as e:
                logger.error(f"[FastAPI] Error forecasting item {item_id}: {e}")
    
        end_time = time.time()
        elapsed = round(end_time - start_time, 3)
        logger.info(f"[FastAPI] Forecasting batch {batch_id} took {elapsed} seconds.")

        conn.execute(text("""
            UPDATE fcst_app.batch_status
            SET secs_forecast = :elapsed,
                updated_at = NOW(),
                status = 'Forecasted'   
            WHERE batch_id = :batch_id
        """), {"batch_id": batch_id, "elapsed": elapsed} )
        if dfs:
            return pd.concat(dfs, ignore_index=True)
        else:
            return pd.DataFrame()
    except Exception as e:
        logger.error(f"[FastAPI] Error forecasting batch {batch_id}: {e}")
        update_job_batch(job_id, batch_id, "Forecasting batch failed", conn)
        return pd.DataFrame()

def process_batch_forecasting(job_id, batch_id, forecast_horizon):
    logger.info(f"[FastAPI] Starting forecasting for batch {batch_id}...")
    df_hist = get_sales_history(job_id, batch_id)
    if df_hist is None or df_hist.empty:
        update_job_batch(job_id, batch_id, "No sales history")
        return
    with engine.begin() as conn:
        forecasts_df = batch_forecast(df_hist, forecast_horizon, job_id, batch_id, conn)
    logger.info(f"[FastAPI] Updating forecasts for batch {batch_id}...")
    update_forecasts(forecasts_df, batch_id, job_id)

def get_sales_history(job_id, batch_id):
    start_time = time.time()
    try:
        with engine.begin() as conn:
            df_hist = pd.read_sql(
                text("""
                    SELECT sales.item_nbr, sales.wk_end_dt AS ds, sales.unit_sales AS y 
                    FROM fcst_app.sales AS sales
                    JOIN fcst_app.job_items AS job_items
                      ON sales.item_nbr = job_items.item_nbr
                    WHERE job_items.job_id = :job_id AND job_items.batch_id = :batch_id
                """),
                conn,
                params={"job_id": job_id, "batch_id": batch_id}
            )

            end_time = time.time()
            elapsed = round(end_time - start_time, 3)
            # Format start_time as UTC timestamp string for PostgreSQL
            started_at = datetime.datetime.utcfromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')
            # Update batch_status with timing info
            conn.execute(
                text("""
                    UPDATE fcst_app.batch_status
                    SET started_at = :started_at, updated_at = NOW(), secs_get_data = :elapsed,
                        status = 'Data Obtained'
                    WHERE batch_id = :batch_id
                """),
                {"started_at": started_at, "elapsed": elapsed, "batch_id": batch_id}
            )
            return df_hist
    except Exception as e:
        with engine.begin() as conn:
            update_job_batch(job_id, batch_id, "Obtaining sales history failed", conn)
        logger.error(f"[FastAPI] Error getting sales history: {e}")


def update_forecasts(forecasts_df, batch_id, job_id):
    """
    Efficiently inserts or updates forecast data using a temp staging table + COPY + upsert.
    """
    try:
        start_time = time.time()

        if forecasts_df.empty or not {"item_nbr", "ds", "yhat"}.issubset(forecasts_df.columns):
            logger.info(f"[FastAPI] Forecasts DF empty or missing columns for batch {batch_id}")
            with engine.begin() as conn:
                update_job_batch(job_id, batch_id, "Inserting forecast data failed", conn)
            return

        with engine.begin() as conn:
            if is_job_cancelled(job_id, conn):
                update_batch_status(batch_id, "Cancelled", conn)
                return

            # Generate unique temp table name
            temp_table = f"temp_forecast_{uuid.uuid4().hex[:8]}"
            logger.info(f"[FastAPI] Using staging table: {temp_table}")

            # 1. Create temp table
            conn.execute(text(f"""
                CREATE TEMP TABLE {temp_table} (
                    item_nbr INTEGER,
                    wk_end_dt DATE,
                    forecast NUMERIC
                ) ON COMMIT DROP;
            """))

            # 2. Write DataFrame to buffer as CSV (for COPY)
            buffer = io.StringIO()
            forecasts_df[["item_nbr", "ds", "yhat"]].to_csv(buffer, index=False, header=False)
            buffer.seek(0)

            # 3. COPY data into temp table
            raw_conn = conn.connection
            with raw_conn.cursor() as cur:
                copy_sql = f"""
                    COPY {temp_table} (item_nbr, wk_end_dt, forecast)
                    FROM STDIN WITH CSV
                """
                cur.copy_expert(copy_sql, buffer)

            # 4. Upsert into main table
            conn.execute(text(f"""
                INSERT INTO fcst_app.forecasts (item_nbr, wk_end_dt, forecast)
                SELECT item_nbr, wk_end_dt, forecast FROM {temp_table}
                ON CONFLICT (item_nbr, wk_end_dt) DO UPDATE
                SET forecast = EXCLUDED.forecast;
            """))

            # 5. Finalize status update
            elapsed = round(time.time() - start_time, 3)
            conn.execute(
                text("""
                    UPDATE fcst_app.batch_status
                    SET secs_insert = :elapsed,
                        updated_at = NOW(),
                        completed_at = NOW(),
                        status = 'Completed'
                    WHERE batch_id = :batch_id
                """),
                {"batch_id": batch_id, "elapsed": elapsed}
            )
            update_job_status_if_complete(job_id, conn)
            logger.info(f"[FastAPI] Completed batch {batch_id} in {elapsed} seconds.")

    except Exception as e:
        with engine.begin() as conn:
            update_job_batch(job_id, batch_id, "Inserting forecast data failed", conn)
        logger.error(f"[FastAPI] Error updating forecasts for batch {batch_id}: {e}")


