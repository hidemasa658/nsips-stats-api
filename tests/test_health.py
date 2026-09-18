import os

from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "test-token"
os.environ["DB_PATH"] = ":memory:"

from main import app  # noqa: E402


def test_health_ok():
    client = TestClient(app)
    r = client.get("/health", headers={"X-API-Token": "test-token"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_missing_token():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 401


def test_health_wrong_token():
    client = TestClient(app)
    r = client.get("/health", headers={"X-API-Token": "wrong"})
    assert r.status_code == 401
