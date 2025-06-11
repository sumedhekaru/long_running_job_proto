# Initialize the FastAPI application

from fastapi import FastAPI

app = FastAPI(
    title="Forecasting API",
    description="API for managing forecasting jobs",
    version="0.1.0"
)

# Import and include API routes
from .routes import router
app.include_router(router)

