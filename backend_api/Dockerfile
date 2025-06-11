# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy the backend code
COPY . /app

# Upgrade pip and install dependencies
RUN pip install --upgrade pip \
    && pip install -r backend_api/requirements.txt

# Expose the port (Cloud Run uses $PORT env var)
EXPOSE 8080

# Start the FastAPI app with uvicorn
CMD exec uvicorn backend_api.main:app --host 0.0.0.0 --port $PORT
