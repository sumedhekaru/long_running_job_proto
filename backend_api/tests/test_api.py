from fastapi.testclient import TestClient
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app

client = TestClient(app)

def test_create_job():
    response = client.post("/jobs/", json={
        "user_id": "testuser",
        "items": [{"item_id": 1}],
        "forecast_horizon": 10
    })
    assert response.status_code == 201
    data = response.json()
    assert data["user_id"] == "testuser"
    assert data["status"] == "queued"
    assert data["items_count"] == 1
    assert data["forecast_horizon"] == 10
    assert "job_id" in data
    assert "created_at" in data
    # Save job_id for next tests
    global job_id
    job_id = data["job_id"]

def test_get_job_status():
    # Create a job first
    create_resp = client.post("/jobs/", json={
        "user_id": "testuser2",
        "items": [{"item_id": 2}],
        "forecast_horizon": 5
    })
    job_id = create_resp.json()["job_id"]
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == job_id
    assert data["user_id"] == "testuser2"
    assert data["status"] == "queued"

def test_cancel_job():
    # Create a job first
    create_resp = client.post("/jobs/", json={
        "user_id": "testuser3",
        "items": [{"item_id": 3}],
        "forecast_horizon": 7
    })
    job_id = create_resp.json()["job_id"]
    response = client.post(f"/jobs/{job_id}/cancel")
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "cancellation requested"
    # Check that status is updated
    status_resp = client.get(f"/jobs/{job_id}")
    assert status_resp.json()["status"] == "stopped"
