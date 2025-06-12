# Forecasting Backend API

This is the backend service for the cloud-based forecasting system, built with FastAPI.

## Features

- Job submission with multiple items
- Job status tracking
- Job cancellation
- Result storage (to be implemented)
- Google Cloud integration (to be implemented)

## Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd backend_api
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

## How to Run the Backend Locally

1. **Navigate to the backend directory:**
   ```bash
   cd backend_api
   ```
2. **(Optional) Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```
5. **Run the FastAPI server:**
   ```bash
   uvicorn main:app --reload
   ```

The API will be available at `http://localhost:8000`

> **Troubleshooting:**
> If you see an error like `Address already in use`, another process may be using port 8000. Either stop the other process or run the server on a different port:
> ```bash
> uvicorn main:app --reload --port 8001
> ```

## Running the API

```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once running, access the interactive API documentation at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Available Endpoints

- `POST /jobs/` - Submit a new forecasting job
- `GET /jobs/{job_id}` - Get job status
- `POST /jobs/{job_id}/cancel` - Cancel a running job

## Development

### Running Tests
```bash
pytest
```

### Code Formatting
```bash
black .
```

### Linting
```bash
flake8
```

## Deployment

This application is designed to be deployed to Google Cloud Run. See the deployment guide for more details.
