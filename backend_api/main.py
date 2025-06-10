import uvicorn
from fastapi import FastAPI
from app import app as fastapi_app

# This is needed for uvicorn to run the app
app = fastapi_app

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        workers=1
    )
