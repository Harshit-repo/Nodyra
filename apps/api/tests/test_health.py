import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routers import health


def test_liveness_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_skips_redis_when_not_required(monkeypatch) -> None:
    """H7: a single-process (queue_backend=none, dispatch inline) deployment
    has no Redis; a failing ping must not fail readiness."""
    monkeypatch.setattr(settings, "queue_backend", "none")
    monkeypatch.setattr(settings, "dispatch_role", "inline")

    async def _boom() -> None:
        raise RuntimeError("no redis here")

    monkeypatch.setattr(health.redis_client, "ping", _boom)
    resp = await health.ready()
    body = json.loads(resp.body)
    assert body["checks"]["redis"] == "not required"


async def test_readiness_requires_redis_when_queue_backend_redis(monkeypatch) -> None:
    monkeypatch.setattr(settings, "queue_backend", "redis")

    async def _boom() -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(health.redis_client, "ping", _boom)
    resp = await health.ready()
    body = json.loads(resp.body)
    assert "error" in body["checks"]["redis"]
    assert resp.status_code == 503


def test_root_metadata() -> None:
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Noodle API"
