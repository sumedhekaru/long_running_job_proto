from fastapi import APIRouter, HTTPException, status, Request
from typing import List
from enum import Enum
import pandas as pd
import time
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool

router = APIRouter()

# --- Initialize a connection pool at module level ---
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)

# --- Helper Enums and Forecast Function ---
class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

def forecast_one_item(item, forecast_horizon):
    """
    Run Prophet forecasting for a single item. Expects item dict with 'item_nbr' and 'history'.
    """
    from prophet import Prophet
    item_id = item["item_nbr"]
    history = item["history"]
    start = time.time()
    df = pd.DataFrame(history)
    df["ds"] = pd.to_datetime(df["ds"])
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
        "item_nbr": item_id,
        "forecast": forecast_needed.to_dict(orient="records")
    }
    
def update_batch_status(batch_id, status,cursor=None):
    """`
    Update the status of a batch in the database using the connection pool.
    """
    if cursor is None:
        new_conn = True
        conn = db_pool.getconn()
        cursor = conn.cursor()

    cursor.execute("""
            UPDATE fcst_app.batch_status
            SET status = %s, updated_at = NOW()
            WHERE batch_id = %s
        """, (status, batch_id))
    cursor.connection.commit()

    if new_conn:
        cursor.close()
        db_pool.putconn(conn)


# --- New Batch Submission Endpoint ---
@router.post("/jobs/batch")
async def submit_batch(request: Request):
    """
    Accepts a batch submission with job_id and batch_id.
    Fetches items for the batch, retrieves their sales history, runs forecasting,
    and returns the results. Prints/logs key steps for learning/debugging.
    """
    data = await request.json()
    job_id = data.get("job_id")
    batch_id = data.get("batch_id")
    forecast_horizon = data.get("forecast_horizon", 30)
    conn = db_pool.getconn()
    update_batch_status(batch_id, "Submitted.",conn)
    print(f"[FastAPI] Received batch submission: job_id={job_id}, batch_id={batch_id}")

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

        update_batch_status(batch_id, "Completed",cur)
        print(f"[FastAPI] Completed forecasting for batch {batch_id}.")

        update_forecasts(forecasts)
        return {"batch_id": batch_id, "forecasts": forecasts}
    except Exception as e:
        update_batch_status(batch_id, "Failed")
        print(f"[FastAPI] Error forecasting for batch {batch_id}: {e}")
        return {"batch_id": batch_id, "error": str(e)}

def get_sales_history(job_id, batch_id):
    try:
        conn = db_pool.getconn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            update_batch_status(batch_id, "Obtaining sales history",cur)
            df_hist = pd.read_sql("""
                SELECT item_nbr, wk_end_dt AS ds, unit_sales AS y 
                FROM fcst_app.sales
                join fcst_app.job_items 
                ON fcst_app.sales.item_nbr = fcst_app.job_items.item_nbr
                WHERE job_id = %s AND batch_id = %s
            """, conn, params=(job_id, batch_id))
            update_batch_status(batch_id, "Sales history obtained",cur)
            return df_hist
    except Exception as e:
        update_batch_status(batch_id, "Obtaining sales history failed")
        print(f"[FastAPI] Errorgetting sales history: {e}")
    finally:
        db_pool.putconn(conn)

def update_forecasts(forecasts):
    """
    Insert or update forecasts for all items in the batch into the database at once.
    'forecasts' is a list of dicts as returned by forecast_one_item.
    """
    try:
        conn = db_pool.getconn()
        update_batch_status(batch_id, "Inserting forecast data", cur)
        # Flatten all forecast results into a list of (item_nbr, wk_end_dt, forecast) tuples
        forecast_rows = []
        for item in forecasts:
            if "forecast" in item and isinstance(item["forecast"], list):
                for entry in item["forecast"]:
                    forecast_rows.append((item["item_nbr"], entry["ds"], entry["yhat"]))
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
            update_batch_status(batch_id, "Completed",cur)
            print(f"[FastAPI] Inserted/updated {len(forecast_rows)} forecasts") 
    except Exception as e:
        update_batch_status(batch_id, "Inserting forecast data failed")
        print(f"[FastAPI] Error updating forecasts: {e}")
    finally:
        db_pool.putconn(conn)
        

