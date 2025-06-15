from cgi import test
import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from google.cloud import pubsub_v1  
import json
from flask import request, flash, redirect, url_for, render_template
from sqlalchemy import text
import time
import requests
import uuid
# Load environment variables
load_dotenv()

pubsub = True

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000/jobs/")
#BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080/jobs/")
#BACKEND_URL = os.getenv("BACKEND_URL", "https://backend-api-855648496281.us-east4.run.app/jobs/")

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
print("DATABASE_URL:", DATABASE_URL)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC")
publisher = pubsub_v1.PublisherClient()
forecast_horizon = os.getenv("FORECAST_HORIZON", 30)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        t_start = time.time()
        num_items = int(request.form.get("num_items", 1))
        user_id = "test_user"  # Replace with actual user context
        batch_size = 50
        batch_ids = []

        with engine.begin() as conn:
            # 1. Create forecast_request job
            job_row = conn.execute(text("""
                INSERT INTO fcst_app.forecast_request (user_id, num_items)
                VALUES (:user_id, :num_items)
                RETURNING job_id
            """), {"user_id": user_id, "num_items": num_items}).fetchone()
            job_id = job_row[0]
            print(f"[INFO] Created job_id: {job_id}")

            # 2. Create temp_items with batch numbers
            temp_items_table = f"temp_items_{uuid.uuid4().hex[:8]}"
            conn.execute(text(f"DROP TABLE IF EXISTS {temp_items_table}"))
            conn.execute(text(f"""
                SELECT item_nbr,
                       row_number() OVER (ORDER BY rand) AS row_num,
                       CEIL(row_number() OVER (ORDER BY rand) / CAST(:batch_size AS FLOAT)) AS temp_batch_num
                INTO TEMP TABLE {temp_items_table}
                FROM (
                    SELECT item_nbr, RANDOM() AS rand
                    FROM fcst_app.items
                    ORDER BY rand
                    LIMIT :num_items
                ) sub
            """), {"num_items": num_items, "batch_size": batch_size})

            # 3. Create batch_map with real batch_ids
            batch_map_table = f"batch_map_{uuid.uuid4().hex[:8]}"
            conn.execute(text(f"DROP TABLE IF EXISTS {batch_map_table}"))
            conn.execute(text(f"""
                CREATE TEMP TABLE {batch_map_table} AS
                WITH ins AS (
                    INSERT INTO fcst_app.batch_status (job_id, status, started_at, updated_at)
                    SELECT :job_id, 'queued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM (SELECT DISTINCT temp_batch_num FROM {temp_items_table}) t
                    RETURNING batch_id
                )
                SELECT batch_id,
                       ROW_NUMBER() OVER (ORDER BY batch_id) AS temp_batch_num
                FROM ins
            """), {"job_id": job_id})

            # 4. Insert into job_items by joining batch_map
            conn.execute(text(f"""
                INSERT INTO fcst_app.job_items (job_id, batch_id, item_nbr)
                SELECT :job_id, bm.batch_id, ti.item_nbr
                FROM {temp_items_table} ti
                JOIN {batch_map_table} bm ON ti.temp_batch_num = bm.temp_batch_num
            """), {"job_id": job_id})

            # 5. Extract batch_ids to submit
            batch_id_rows = conn.execute(text(f"SELECT batch_id FROM {batch_map_table}")).fetchall()
            batch_ids = [row[0] for row in batch_id_rows]

            # Optional cleanup
            conn.execute(text(f"DROP TABLE IF EXISTS {temp_items_table}"))
            conn.execute(text(f"DROP TABLE IF EXISTS {batch_map_table}"))

        # 6. Submit batches (via REST or Pub/Sub)
        results = []
        print(f"[INFO] Submitting {len(batch_ids)} batches...")
        for idx, batch_id in enumerate(batch_ids):
            print(f"[INFO] Submitting batch {idx+1}/{len(batch_ids)} (batch_id={batch_id})...")
            try:
                if pubsub:
                    submit_job_to_pubsub(job_id, batch_id, forecast_horizon)
                else:
                    resp = requests.post(f"{BACKEND_URL}batch", json={"job_id": job_id, "batch_id": batch_id})
                    resp.raise_for_status()
                    results.append(resp.json())
            except Exception as e:
                print(f"[ERROR] Batch {batch_id} failed: {e}")
                results.append({"batch_id": batch_id, "error": str(e)})
                break  # Stop submitting on first failure

        flash(f"Created job {job_id}. Batch results: {results}", "success")
        return redirect(url_for("job_status", job_id=job_id))

    # For GET: show recent jobs
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT job_id, status, submitted_at
            FROM fcst_app.forecast_request
            ORDER BY job_id DESC
            LIMIT 10
        """)).fetchall()
        jobs = [{"job_id": r[0], "status": r[1], "submitted_at": r[2]} for r in rows]
    return render_template("index.html", jobs=jobs)



def submit_job_to_pubsub(job_id, batch_id, forecast_horizon):
    t_pubsub_start = time.time()
    message = {
        "job_id": job_id,
        "batch_id": batch_id,
        "forecast_horizon": forecast_horizon
    }
    data = json.dumps(message).encode("utf-8")
    topic_path = PUBSUB_TOPIC
    print(f"[TIMER] Before Pub/Sub publish: {time.time() - t_pubsub_start:.4f}s")
    future = publisher.publish(topic_path, data)
    print(f"[TIMER] After Pub/Sub publish: {time.time() - t_pubsub_start:.4f}s")
    return True
    #return future.result()
        

@app.route("/job/")
def job_list():
    # Show last 10 jobs as links if no job_id is given
    jobs = []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT job_id, status, submitted_at FROM fcst_app.forecast_request
            ORDER BY job_id DESC LIMIT 10
        """)).fetchall()
        jobs = [{"job_id": r[0], "status": r[1], "submitted_at": r[2]} for r in rows]
    return render_template("job_list.html", jobs=jobs)

@app.route("/results")
def results():
    job_id = request.args.get('job_id')
    item_ids = []
    first_item_hist = []
    first_item_fcst = []
    if job_id:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT item_nbr FROM fcst_app.job_items WHERE job_id = :job_id ORDER BY item_nbr
            """), {"job_id": job_id}).fetchall()
            item_ids = [row[0] for row in rows]
            if item_ids:
                # Fetch data for first item
                hist_rows = conn.execute(text("""
                    SELECT wk_end_dt, unit_sales FROM fcst_app.sales
                    WHERE item_nbr = :item_nbr AND wk_end_dt IS NOT NULL
                    ORDER BY wk_end_dt
                """), {"item_nbr": item_ids[0]}).fetchall()
                first_item_hist = [{"ds": str(r[0]), "y": float(r[1])} for r in hist_rows]
                fcst_rows = conn.execute(text("""
                    SELECT wk_end_dt, forecast FROM fcst_app.forecasts
                    WHERE item_nbr = :item_nbr AND wk_end_dt IS NOT NULL
                    ORDER BY wk_end_dt
                """), {"item_nbr": item_ids[0]}).fetchall()
                first_item_fcst = [{"ds": str(r[0]), "yhat": float(r[1])} for r in fcst_rows]
    return render_template(
        "results.html",
        job_id=job_id,
        item_ids=item_ids,
        first_item_hist=first_item_hist,
        first_item_fcst=first_item_fcst
    )

@app.route("/summary")
def summary():
    with engine.begin() as conn:
        jobs = conn.execute(text('''
            SELECT fr.job_id, 
                   fr.num_items, 
                   fr.submitted_at AS start_time, 
                   EXTRACT(EPOCH FROM (MAX(bs.updated_at) - fr.submitted_at)) AS elapsed,
                   CASE WHEN fr.num_items > 0 THEN EXTRACT(EPOCH FROM (MAX(bs.updated_at) - fr.submitted_at))/fr.num_items ELSE NULL END AS time_per_item,
                   COALESCE(SUM(bs.secs_get_data), 0) AS sum_get,
                   COALESCE(SUM(bs.secs_forecast), 0) AS sum_fcst,
                   COALESCE(SUM(bs.secs_insert), 0) AS sum_ins,
                   COUNT(bs.batch_id) AS num_batches
            FROM fcst_app.forecast_request fr
            LEFT JOIN fcst_app.batch_status bs ON fr.job_id = bs.job_id
            WHERE fr.status = 'completed'
            GROUP BY fr.job_id, fr.num_items, fr.submitted_at
            ORDER BY fr.submitted_at DESC
            LIMIT 30
        ''')).fetchall()
    return render_template(
        "summary.html",
        jobs=jobs
    )

@app.route("/job/<job_id>")
def job_status(job_id):
    return render_template("job.html", job_id=job_id)



@app.route("/api/items/<job_id>")
def api_items(job_id):
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT item_nbr FROM fcst_app.job_items WHERE job_id = :job_id ORDER BY item_nbr
            """), {"job_id": job_id}).fetchall()
            items = [row[0] for row in rows]
            return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/itemdata/<job_id>/<item_nbr>")
def api_itemdata(job_id, item_nbr):
    try:
        with engine.connect() as conn:
            # Historical data (from sales)
            hist_rows = conn.execute(text("""
                SELECT wk_end_dt, unit_sales FROM fcst_app.sales
                WHERE item_nbr = :item_nbr
                  AND wk_end_dt IS NOT NULL
                ORDER BY wk_end_dt
            """), {"item_nbr": item_nbr}).fetchall()
            historical = [{"ds": str(r[0]), "y": float(r[1])} for r in hist_rows]
            # Forecast data (from forecasts)
            fcst_rows = conn.execute(text("""
                SELECT wk_end_dt, forecast FROM fcst_app.forecasts
                WHERE item_nbr = :item_nbr
                  AND wk_end_dt IS NOT NULL
                ORDER BY wk_end_dt
            """), {"item_nbr": item_nbr}).fetchall()
            forecast = [{"ds": str(r[0]), "yhat": float(r[1])} for r in fcst_rows]
            return jsonify({"historical": historical, "forecast": forecast})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/status/<job_id>")
def status(job_id):
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT batch_id, status, updated_at
                FROM fcst_app.batch_status
                WHERE job_id = :job_id
                ORDER BY batch_id ASC
            """), {"job_id": job_id}).mappings().fetchall()
            total = len(rows)
            if total == 0:
                summary = "No batches found."
                status_val = "unknown"
                latest_msg = "No status found."
                status_breakdown = {}
            else:
                status_counts = {}
                for r in rows:
                    s = r['status']
                    status_counts[s] = status_counts.get(s, 0) + 1
                # Only count 'completed' as finished (customize if needed)
                completed_statuses = {"completed"}
                finished = sum(count for status, count in status_counts.items() if status.lower() in completed_statuses)
                summary = f"{finished} out of {total} batches completed."
                # Add all statuses regardless of value, with count
                details = []
                for status, count in status_counts.items():
                    details.append(f"{count} {status}")
                if details:
                    summary += " " + ", ".join(details) + "."
                # Latest message is the most recently updated batch
                latest_row = max(rows, key=lambda r: r['updated_at'])
                latest_msg = f"{latest_row['updated_at'].strftime('%Y-%m-%d %H:%M:%S')} Batch {latest_row['batch_id']}: {latest_row['status']}"
                status_val = latest_row['status']
                status_breakdown = status_counts
            response = {
                "status": status_val,
                "latest_msg": latest_msg,
                "summary": summary,
                "status_breakdown": status_breakdown,
            }
            print(f"[DEBUG] /status/{job_id} response: {response}")
            return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    try:
        from sqlalchemy import text
        cancel_query = text("""
            UPDATE fcst_app.forecast_request
            SET status = 'cancelled'
            WHERE job_id = :job_id
        """)
        with engine.connect() as conn:
            conn.execute(cancel_query, {"job_id": job_id})
            conn.commit()   
        return jsonify({"status": "cancellation requested"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
