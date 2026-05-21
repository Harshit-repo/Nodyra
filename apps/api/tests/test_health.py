from fastapi.testclient import TestClient

from app.main import app


def test_liveness_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_metadata() -> None:
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Noodle API"
