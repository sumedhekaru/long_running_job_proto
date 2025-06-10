# Business Requirements: Cloud-based Forecasting System

## Overview
A system for users to submit large-scale forecasting jobs via a web interface, with processing handled in the cloud. Supports multiple users, job tracking, and result delivery.

## Functional Requirements

1. **Job Submission**
   - Users can submit forecasting jobs (thousands of items) via a Flask-based web interface.
   - Each submission creates a job and enqueues it in Google Cloud Tasks.

2. **Job Processing**
   - Jobs are picked up by a Google Cloud Run service.
   - The backend splits large jobs into manageable batches for processing.
   - Forecasting is performed using Prophet.

3. **Multi-User Support**
   - Multiple users can submit and track jobs concurrently.
   - Each job is associated with a user account.

4. **Job Status & Control**
   - Users can view job status (queued, running, completed, failed, stopped) in the frontend.
   - Users can cancel jobs; cancellation is handled gracefully by the backend.

5. **Result Delivery**
   - Upon completion, results are saved as downloadable files (e.g., CSV).
   - Users receive a secure download link.

6. **Storage**
   - Job metadata and status are stored in SQL Server (or cloud SQL equivalent).
   - Result files are stored in cloud storage.

## Non-Functional Requirements

- **Scalability:** System must handle multiple concurrent jobs and large datasets.
- **Reliability:** Jobs must be processed reliably, with failure handling and retries.
- **Security:** User authentication and secure result delivery.
- **Extensibility:** Easy to switch result delivery to database in the future.

## Deployment

- **Frontend:** Flask app running locally.
- **Backend:** Google Cloud Run, Google Cloud Tasks, Cloud SQL, Cloud Storage.

## Future Enhancements

- Deliver results directly to SQL Server for downstream analytics.
- Enhanced real-time progress updates (e.g., websockets).
- Advanced user management and permissions.
