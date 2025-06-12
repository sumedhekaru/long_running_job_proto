# What We Did

## Step 1: Download and Prepare Forecast Data
- Downloaded the Corporación Favorita Grocery Sales Forecasting dataset from Kaggle (source 1).
- Saved the extracted files (e.g., `stores.csv`) in the `forecast_data` directory for use in prototyping and testing the forecasting pipeline.

---

## Step 2: Backend API Setup and Testing
- Created a FastAPI backend with endpoints to:
    - Submit jobs (`POST /jobs/`)
    - Get job status (`GET /jobs/{job_id}`)
    - Cancel jobs (`POST /jobs/{job_id}/cancel`)
- Used in-memory job storage for initial development.
- Added automated tests using pytest and FastAPI TestClient for job creation, status retrieval, and cancellation.
- Fixed dependency and import issues; ensured all tests pass.

---

## Step 3: Frontend Flask App (Prototype)
- Created a `frontend` folder with its own Python virtual environment and requirements.
- Built a basic Flask app (`frontend/app.py`) with:
    - A form to input the number of items to forecast and a submit button.
    - On submit, sends a job request to the backend and starts polling for job status.
    - The submit button changes to "Cancel" after submission, allowing the user to cancel the job.
    - Displays live job status updates.
- Created Bootstrap-styled HTML templates for the form and results.

---

## Step 4: Data Aggregation and Database Integration
- Processed the raw daily sales data to create weekly item-level sales summaries:
    - Filtered out promotional sales and dropped unnecessary columns (e.g., `id`).
    - Converted daily sales to weekly sales, using Saturday as the week-ending date (`wk_end_dt`).
    - Aggregated unit sales by item and week.
- Imported the resulting weekly sales data (`wkly_sales_data.csv`) into the Neon (Postgres) database for use in backend processing and testing.

---

## Step 5: Batch-Based Job Submission and Tracking
- Updated the database model to support batch processing:
    - Added a `batch_status` table with a globally unique `batch_id` (SERIAL PRIMARY KEY) for each batch, linked to `job_id`.
    - Modified the `job_items` table to associate each item with a specific `batch_id` and `job_id`.
- Refactored the frontend Flask app (`frontend/app.py`) to:
    - Collect user input for the number of items to forecast.
    - Select all items and create all batches up front in the database, assigning items to batches and generating unique `batch_id`s.
    - Submit batches to the backend one by one (via POST to `/jobs/batch`), waiting for each batch to complete before submitting the next.
    - Include clear comments and print/logging statements throughout the code for learning and debugging purposes.
- This new approach enables scalable, trackable, and eventually parallelizable batch processing for forecasting jobs.

---


---

## Step 6: Backend Refactor for Batch Forecasting (June 2025)
- Refactored the FastAPI backend to fully support batch-based forecasting jobs:
    - Implemented `/jobs/batch` endpoint to accept `job_id` and `batch_id` for batch processing.
    - Backend now fetches batch items and their sales history directly from the database, not from the frontend.
    - Forecasting for items in a batch is parallelized using `ProcessPoolExecutor` for efficient, scalable processing.
    - Batch status is tracked in the database and updated at every key stage (submitted, obtaining sales, processing, completed, failed, etc.).
    - Used a PostgreSQL connection pool (`psycopg2.pool.SimpleConnectionPool`) for efficient DB access and resource management.
    - All forecast results are upserted into the `fcst_app.forecasts` table in a single batch operation.
    - Robust error handling: batch status is updated to 'failed' on any error, and errors are logged for debugging.
    - Removed all legacy endpoints and in-memory job logic from the backend for clarity and maintainability.
    - The backend is now fully DB-driven, scalable, and ready for integration with the batch-oriented frontend.

Further steps and decisions will be documented here as the project progresses.
