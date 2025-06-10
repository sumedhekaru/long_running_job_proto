# Initialize the FastAPI application

from fastapi import FastAPI

app = FastAPI(
    title="Forecasting API",
    description="API for managing forecasting jobs",
    version="0.1.0"
)

# Import routes after app initialization to avoid circular imports
from . import routes  # noqa
