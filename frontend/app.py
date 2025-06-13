import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load environment variables
load_dotenv()

#BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000/jobs/")
#BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080/jobs/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://backend-api-855648496281.us-east4.run.app/jobs/")

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
print("DATABASE_URL:", DATABASE_URL)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        import requests
        num_items = int(request.form.get("num_items", 1))
        user_id = "test_user"  # In production, get from session/auth
        batch_size = 10  # Number of items per batch
        batch_ids = []   # To store all created batch IDs

        with engine.begin() as conn:
            # 1. Select N random items
            print(f"Selecting {num_items} random items from items table...")
            item_rows = conn.execute(text("""
                SELECT item_nbr FROM fcst_app.items ORDER BY RANDOM() LIMIT :num_items
            """), {"num_items": num_items}).fetchall()
            item_nbrs = [row[0] for row in item_rows]
            print(f"Selected item_nbrs: {item_nbrs}")

            # 2. Create a new job
            print("Creating new forecast_request job...")
            job_row = conn.execute(text("""
                INSERT INTO fcst_app.forecast_request (user_id, num_items) VALUES (:user_id, :num_items)
                RETURNING job_id
            """), {"user_id": user_id, "num_items": num_items}).fetchone()
            job_id = job_row[0]
            print(f"Created job_id: {job_id}")

            # 3. Create all batches up front and get their batch_ids
            batches = [item_nbrs[i:i+batch_size] for i in range(0, len(item_nbrs), batch_size)]
            for batch_items in batches:
                # Insert into batch_status to get a globally unique batch_id
                batch_status_row = conn.execute(text("""
                    INSERT INTO fcst_app.batch_status (job_id, status) VALUES (:job_id, :status)
                    RETURNING batch_id
                """), {"job_id": job_id, "status": "queued"}).fetchone()
                batch_id = batch_status_row[0]
                batch_ids.append(batch_id)
                print(f"Created batch_id: {batch_id} for items: {batch_items}")
                # Insert items for this batch into job_items
                job_items = [
                    {"job_id": job_id, "batch_id": batch_id, "item_nbr": item_nbr}
                    for item_nbr in batch_items
                ]
                conn.execute(
                    text("INSERT INTO fcst_app.job_items (job_id, batch_id, item_nbr) VALUES (:job_id, :batch_id, :item_nbr)"),
                    job_items
                )

        # 4. Submit the batches one by one to the backend, waiting for each to finish
        print(f"Submitting {len(batch_ids)} batches to backend one by one...")
        results = []
        for idx, batch_id in enumerate(batch_ids):
            print(f"Submitting batch {idx+1}/{len(batch_ids)} (batch_id={batch_id}) to backend...")
            try:
                resp = requests.post(f"{BACKEND_URL}batch", json={"job_id": job_id, "batch_id": batch_id})
                print(f"Backend response status: {resp.status_code}")
                resp.raise_for_status()
                result = resp.json()
                print(f"Batch {batch_id} result: {result}")
                results.append(result)
            except Exception as e:
                print(f"Error submitting batch {batch_id}: {e}")
                results.append({"batch_id": batch_id, "error": str(e)})
                break  # Stop submitting further batches on error
        flash(f"Created job {job_id} with items: {item_nbrs}. Batch submission results: {results}", "success")
        return redirect(url_for("job_status", job_id=job_id))

    # Fetch last 10 jobs for navigation
    jobs = []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT job_id, status, submitted_at FROM fcst_app.forecast_request
            ORDER BY job_id DESC LIMIT 10
        """)).fetchall()
        jobs = [{"job_id": r[0], "status": r[1], "submitted_at": r[2]} for r in rows]
    return render_template("index.html", jobs=jobs)

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
            SELECT 
                job_id, 
                num_items, 
                submitted_at AS start_time, 
                updated_at AS end_time, 
                EXTRACT(EPOCH FROM (updated_at - submitted_at)) AS elapsed,
                CASE WHEN num_items > 0 THEN EXTRACT(EPOCH FROM (updated_at - submitted_at))/num_items ELSE NULL END AS time_per_item
            FROM fcst_app.forecast_request
            WHERE status = 'completed'  
            ORDER BY submitted_at DESC
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
            # Get all batches for this job
            rows = conn.execute(text("""
                SELECT batch_id, status, updated_at
                FROM fcst_app.batch_status
                WHERE job_id = :job_id
                ORDER BY batch_id ASC
            """), {"job_id": job_id}).mappings().fetchall()
            total = len(rows)
            if total == 0:
                summary = "No batches found"
                status_val = "unknown"
                latest_msg = "No status found"
                status_breakdown = {}
            else:
                # Count completed and breakdown by status
                completed_statuses = {"completed", "failed"}
                finished = sum(1 for r in rows if r['status'].lower() in completed_statuses)
                status_counts = {}
                for r in rows:
                    s = r['status'].lower()
                    status_counts[s] = status_counts.get(s, 0) + 1
                summary = f"{finished} out of {total} batches completed. "
                summary += ", ".join(f"{count} batch{'es' if count > 1 else ''} {status}" for status, count in status_counts.items() if status not in completed_statuses)
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
