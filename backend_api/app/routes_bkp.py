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
load_dotenv()


router = APIRouter()

# --- Initialize a connection pool at module level ---
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)

print("DATABASE_URL:", os.getenv("DATABASE_URL"))

def forecast_one_item_prophet(item, forecast_horizon):
    """
    Run Prophet forecasting for a single item. Expects item dict with 'item_nbr' and 'history'.
    """
    item_id = item["item_nbr"]
    history = item["history"]
    start = time.time()
    df = pd.DataFrame(history)
    df["ds"] = pd.to_datetime(df["ds"])
    df = df.sort_values("ds")
    print(f"[FastAPI] Forecasting item {item_id} with {len(df)} data points.")
    if len(df) < 104:
        yearly_seasonality = False
    else:
        yearly_seasonality = True
    if len(df) < 30:
        weekly_seasonality = False
    else:
        weekly_seasonality = True
    model = Prophet(yearly_seasonality=yearly_seasonality,weekly_seasonality=weekly_seasonality)
    model.fit(df)
    future = model.make_future_dataframe(periods=forecast_horizon, freq='W')
    forecast = model.predict(future)
    forecast_needed = forecast.tail(forecast_horizon)[["ds", "yhat"]]
    forecast_needed["yhat"] = forecast_needed["yhat"].clip(lower=0)
    duration = time.time() - start
    print(f"[FastAPI] Forecast for item {item_id} took {duration:.2f} seconds.")
    return {
        "item_nbr": item_id,
        "forecast": forecast_needed.to_dict(orient="records")
    }

# --- ORBIT VERSION BELOW ---
from orbit.models import DLT
from orbit.diagnostics.plot import plot_predicted_data

def forecast_one_item(item, forecast_horizon):
    """
    Run Orbit DLT forecasting for a single item. Expects item dict with 'item_nbr' and 'history'.
    Output is 1:1 compatible with Prophet version.
    """
    item_id = item["item_nbr"]
    history = item["history"]
    start = time.time()
    df = pd.DataFrame(history)
    df["ds"] = pd.to_datetime(df["ds"])
    df = df.sort_values("ds")
    print(f"[FastAPI] Forecasting item {item_id} with {len(df)} data points (Orbit).")
    seasonality = []
    if len(df) >= 30:
        seasonality.append("weekly")
    if len(df) >= 104:
        seasonality.append("yearly")
    model = DLT(response_col="y", date_col="ds", seasonality=seasonality if seasonality else None)
    model.fit(df)
    last_date = df["ds"].max()
    future_dates = pd.date_range(start=last_date + pd.Timedelta(weeks=1), periods=forecast_horizon, freq="W")
    future_df = pd.DataFrame({"ds": future_dates})
    df_pred = pd.concat([df, future_df], ignore_index=True, sort=False)
    pred = model.predict(df_pred)
    forecast_needed = pd.DataFrame({
        "ds": future_dates,
        "yhat": pred.loc[df.shape[0]:, "prediction"].clip(lower=0).values
    })
    duration = time.time() - start
    print(f"[FastAPI] Forecast for item {item_id} took {duration:.2f} seconds (Orbit).")
    return {
        "item_nbr": item_id,
        "forecast": forecast_needed.to_dict(orient="records")
    }
    
def update_batch_status(batch_id, status, conn=None):
    """
    Update the status of a batch in the database using the connection pool.
    """
    new_conn = False
    if conn is None:
        new_conn = True
        conn = db_pool.getconn()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE fcst_app.batch_status
        SET status = %s, updated_at = NOW()
        WHERE batch_id = %s
    """, (status, batch_id))
    conn.commit()
    cursor.close()
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


def update_job_status_if_complete(job_id):
    conn = db_pool.getconn()
    try:
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
    finally:
        db_pool.putconn(conn)

def is_job_cancelled(job_id):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status FROM fcst_app.forecast_request
                WHERE job_id = %s
            """, (job_id,))
            result = cur.fetchone()
            if result is None:
                print(f"[FastAPI] is_job_cancelled: job_id {job_id} not found.")
                return False
            status = result[0]
            print(f"[FastAPI] is_job_cancelled: job_id {job_id} status is '{status}'")
            return status == "cancelled"
    finally:
        db_pool.putconn(conn)   

def process_batch_forecasting(job_id, batch_id, forecast_horizon):
    if is_job_cancelled(job_id):
        update_batch_status(batch_id, "Cancelled")
        return
    try:
        # 1. Get sales history for all items in the batch
        df_hist = get_sales_history(job_id, batch_id)

        # Prepare batch items for parallel processing
        batch_items = []
        for item_nbr in df_hist["item_nbr"].unique():
            item_hist = df_hist[df_hist["item_nbr"] == item_nbr][["ds", "y"]]
            history = item_hist.to_dict(orient="records")
            batch_items.append({"item_nbr": item_nbr, "history": history})

        print(f"[FastAPI] Running parallel forecasting for batch {batch_id}...")
        import concurrent.futures
        forecasts = []
        num_workers = min(os.cpu_count() or 1, len(batch_items))
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            future_to_item = {
                executor.submit(forecast_one_item, item, forecast_horizon): item["item_nbr"]
                for item in batch_items
            }
            for future in concurrent.futures.as_completed(future_to_item):
                item_nbr = future_to_item[future]
                try:
                    result = future.result()
                    forecasts.append(result)
                except Exception as e:
                    print(f"[FastAPI] Forecast failed for item {item_nbr}: {e}")
                    forecasts.append({"item_nbr": item_nbr, "error": str(e)})

        
        print(f"[FastAPI] Completed forecasting for batch {batch_id}.")
        print(f"[FastAPI] Updating forecasts for batch {batch_id}...")
        update_forecasts(forecasts, batch_id)
        print(f"[FastAPI] Updating batch status for batch {batch_id} and job {job_id}...")
        update_batch_status(batch_id, "Completed")
        update_job_status_if_complete(job_id)
    except Exception as e:
        update_batch_status(batch_id, "Failed")
        update_job_status_if_complete(job_id)
        print(f"[FastAPI] Error forecasting for batch {batch_id}: {e}")


def get_sales_history(job_id, batch_id):
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            update_batch_status(batch_id, "Obtaining sales history", conn)
            df_hist = pd.read_sql("""
                SELECT sales.item_nbr, sales.wk_end_dt AS ds, sales.unit_sales AS y 
                FROM fcst_app.sales AS sales
                JOIN fcst_app.job_items AS job_items
                  ON sales.item_nbr = job_items.item_nbr
                WHERE job_items.job_id = %s AND job_items.batch_id = %s
            """, conn, params=(job_id, batch_id))
            update_batch_status(batch_id, "Sales history obtained", conn)
            return df_hist
    except Exception as e:
        if conn is not None:
            update_batch_status(batch_id, "Obtaining sales history failed", conn)
        print(f"[FastAPI] Error getting sales history: {e}")
    finally:
        if conn is not None:
            db_pool.putconn(conn)


def update_forecasts(forecasts, batch_id):
    """
    Insert or update forecasts for all items in the batch into the database at once.
    'forecasts' is a list of dicts as returned by forecast_one_item.
    """
    if is_job_cancelled:
        update_batch_status(batch_id, "Cancelled")
        return
        
    try:
        conn = db_pool.getconn()
        update_batch_status(batch_id, "Inserting forecast data", conn)
        # Flatten all forecast results into a list of (item_nbr, wk_end_dt, forecast) tuples
        forecast_rows = []
        import numpy as np
        from pandas import Timestamp
        def make_sql_serializable(obj):
            if isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return round(float(obj), 1)
            elif isinstance(obj, float):
                return round(obj, 1)
            elif isinstance(obj, Timestamp):
                return obj.to_pydatetime()
            else:
                return obj
        for item in forecasts:
            if "forecast" in item and isinstance(item["forecast"], list):
                for entry in item["forecast"]:
                    row = (
                        make_sql_serializable(item["item_nbr"]),
                        make_sql_serializable(entry["ds"]),
                        round(make_sql_serializable(entry["yhat"]), 1)
                    )
                    forecast_rows.append(row)
            # Optionally log errors
            elif "error" in item:
                print(f"[FastAPI] Skipping forecast for item {item.get('item_nbr')}: {item['error']}")

        if not forecast_rows:
            print(f"[FastAPI] No forecasts to insert for batch {batch_id}")
            return
        with conn.cursor() as cur:
            upsert_query = """
                INSERT INTO fcst_app.forecasts 
                (item_nbr, wk_end_dt, forecast)
                VALUES (%s, %s, %s)
                ON CONFLICT (item_nbr, wk_end_dt) DO UPDATE
                SET forecast = EXCLUDED.forecast
            """
            cur.executemany(upsert_query, forecast_rows)
            conn.commit()
            update_batch_status(batch_id, "Completed",conn)
            print(f"[FastAPI] Inserted/updated {len(forecast_rows)} forecasts") 
    except Exception as e:
        update_batch_status(batch_id, "Inserting forecast data failed", conn)
        print(f"[FastAPI] Error updating forecasts: {e}")
    finally:
        db_pool.putconn(conn)
