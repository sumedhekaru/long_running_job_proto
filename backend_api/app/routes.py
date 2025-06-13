from fastapi import APIRouter, HTTPException, status, Request
from typing import List
from enum import Enum
import pandas as pd
import time
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from dotenv import load_dotenv
from prophet import Prophet
import numpy as np
from pandas import Timestamp
load_dotenv()


router = APIRouter()

# --- Initialize a connection pool at module level ---
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)

print("DATABASE_URL:", os.getenv("DATABASE_URL"))

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
    print(f"[FastAPI] Forecasting item {item_id} with {len(df)} data points.")
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
    print(f"[FastAPI] Forecast for item {item_id} took {duration:.2f} seconds.")
    return forecast_needed

def update_batch_status(batch_id, status, conn=None):
    """
    Update the status of a batch in the database using the connection pool.
    Ensures connections are always returned to the pool.
    """
    new_conn = False
    if conn is None:
        new_conn = True
        conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE fcst_app.batch_status
            SET status = %s, updated_at = NOW()
            WHERE batch_id = %s
        """, (status, batch_id))
        conn.commit()
        cursor.close()
    finally:
        if new_conn:
            db_pool.putconn(conn)



# --- New Batch Submission Endpoint ---
from fastapi import BackgroundTasks

@router.post("/jobs/batch")
async def submit_batch(request: Request, background_tasks: BackgroundTasks):
    """
    Accepts a batch submission with job_id and batch_id. Immediately returns to frontend, then processes forecasting in background.
    """
    data = await request.json()
    job_id = data.get("job_id")
    batch_id = data.get("batch_id")
    forecast_horizon = data.get("forecast_horizon", 30)
    conn = db_pool.getconn()
    try:
        update_batch_status(batch_id, "Submitted.", conn)
    finally:
        db_pool.putconn(conn)
    print(f"[FastAPI] Received batch submission: job_id={job_id}, batch_id={batch_id}")

    # Schedule background processing (background task will handle its own DB connections)
    background_tasks.add_task(process_batch_forecasting, job_id, batch_id, forecast_horizon)
    return {"status": "ok", "job_id": job_id, "batch_id": batch_id}


def update_job_status_if_complete(job_id, conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM fcst_app.batch_status
            WHERE job_id = %s AND status NOT IN ('Completed', 'Failed')
        """, (job_id,))
        remaining = cur.fetchone()[0]
        if remaining == 0:
            print(f"[FastAPI] All batches for job {job_id} completed.")
            cur.execute("""
                UPDATE fcst_app.forecast_request
                SET status = 'completed', updated_at = NOW()
                WHERE job_id = %s
            """, (job_id,))
            conn.commit()

def is_job_cancelled(job_id, conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT status FROM fcst_app.forecast_request WHERE job_id = %s
        """, (job_id,))
        row = cur.fetchone()
        if row and row[0] and row[0].lower() == "cancelled":
            print(f"[FastAPI] Job {job_id} is cancelled.")
            return True
        return False

def update_job_batch(job_id, batch_id, msg, conn=None):
    new_conn = False
    if conn is None:
        conn = db_pool.getconn()
        new_conn = True
    try:
        update_batch_status(batch_id, msg, conn)
        update_job_status_if_complete(job_id, conn)
    finally:
        if new_conn:
            db_pool.putconn(conn)

def batch_forecast(df_hist, forecast_horizon):
    dfs = []
    for item_id, item_hist in df_hist.groupby("item_nbr"):
        item = {"item_nbr": item_id, "history": item_hist[["ds", "y"]].to_dict(orient="records")}
        try:
            fcst_df = forecast_one_item(item, forecast_horizon)
            dfs.append(fcst_df)
        except Exception as e:
            print(f"[FastAPI] Error forecasting item {item_id}: {e}")
    if dfs:
        return pd.concat(dfs, ignore_index=True)
    else:
        return pd.DataFrame()

def process_batch_forecasting(job_id, batch_id, forecast_horizon):
    print(f"[FastAPI] Starting forecasting for batch {batch_id}...")
    df_hist = get_sales_history(job_id, batch_id)
    if df_hist is None or df_hist.empty:
        update_job_batch(job_id, batch_id, "No sales history")
        return
    forecasts_df = batch_forecast(df_hist, forecast_horizon)
    print(f"[FastAPI] Updating forecasts for batch {batch_id}...")
    update_forecasts(forecasts_df, batch_id, job_id)

def get_sales_history(job_id, batch_id):
    conn = db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            update_batch_status(batch_id, "Obtaining sales history", conn)
            df_hist = pd.read_sql("""
                SELECT sales.item_nbr, sales.wk_end_dt AS ds, sales.unit_sales AS y 
                FROM fcst_app.sales AS sales
                JOIN fcst_app.job_items AS job_items
                  ON sales.item_nbr = job_items.item_nbr
                WHERE job_items.job_id = %s AND job_items.batch_id = %s
            """, conn, params=(job_id, batch_id))
            update_batch_status(batch_id, "Sales history obtained")
            return df_hist
    except Exception as e:
        update_job_batch(job_id, batch_id, "Obtaining sales history failed")
        print(f"[FastAPI] Error getting sales history: {e}")
    finally:
        db_pool.putconn(conn)


def update_forecasts(forecasts_df, batch_id, job_id):
    """
    Insert or update forecasts for all items in the batch into the database at once.
    forecasts_df is a DataFrame with columns ["item_nbr", "ds", "yhat"].
    Uses a single DB connection for all operations in this function.
    """
    conn = db_pool.getconn()
    try:
        if is_job_cancelled(job_id, conn):
            update_batch_status(batch_id, "Cancelled", conn)
            return

        update_batch_status(batch_id, "Inserting forecast data", conn)
        forecast_rows = [
            (row.item_nbr, row.ds, row.yhat)
            for row in forecasts_df.itertuples()
            if hasattr(row, "yhat")
        ]
        with conn.cursor() as cur:
            upsert_query = """
                INSERT INTO fcst_app.forecasts (item_nbr, wk_end_dt, forecast)
                VALUES (%s, %s, %s)
                ON CONFLICT (item_nbr, wk_end_dt) DO UPDATE
                SET forecast = EXCLUDED.forecast
            """
            if forecast_rows:
                cur.executemany(upsert_query, forecast_rows)
                conn.commit()
                update_job_batch(job_id, batch_id, "Completed", conn)
                print(f"[FastAPI] Inserted/updated {len(forecast_rows)} forecasts")
            else:
                update_job_batch(job_id, batch_id, "No valid forecasts", conn)
                print(f"[FastAPI] No valid forecasts to insert for batch {batch_id}")
    except Exception as e:
        update_job_batch(job_id, batch_id, "Inserting forecast data failed", conn)
        print(f"[FastAPI] Error updating forecasts: {e}")
    finally:
        db_pool.putconn(conn)
