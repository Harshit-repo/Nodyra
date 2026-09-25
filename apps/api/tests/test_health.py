import json
from pathlib import Path

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
    assert resp.json()["name"] == "Nodyra API"


# ── F-04: readiness must assert schema head, not just connectivity ──────────


class _FakeConn:
    """Minimal async connection stand-in for the readiness DB probe."""

    def __init__(self, revision: str | None) -> None:
        self._revision = revision

    async def execute(self, _statement):
        return None

    async def scalar(self, _statement):
        return self._revision

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, revision: str | None) -> None:
        self._revision = revision

    def connect(self):
        return _FakeConn(self._revision)


def _single_process(monkeypatch) -> None:
    monkeypatch.setattr(settings, "queue_backend", "none")
    monkeypatch.setattr(settings, "dispatch_role", "inline")
    monkeypatch.setattr(settings, "execution_sandbox", "off")


async def test_readiness_fails_when_schema_is_not_at_head(monkeypatch) -> None:
    """A reachable database whose migrations have not run must NOT read Ready.

    Regression for F-04: ``readiness_checks`` used to run ``SELECT 1`` and
    nothing more, so an unmigrated (or mid-migration) database reported
    ``database: ok`` while every write returned 500. In Kubernetes the chart
    migrates in a *separate* job, so a lagging or failed job would still let
    API pods go Ready and take production traffic.
    """
    _single_process(monkeypatch)
    monkeypatch.setattr(health, "_expected_schema_head", lambda: "0092_head")
    monkeypatch.setattr(health, "engine", _FakeEngine("0088_stale"))

    healthy, checks = await health.readiness_checks()

    assert healthy is False
    assert checks["database"].startswith("error: schema")
    assert "0088_stale" in checks["database"]
    # The detail must stay actionable without leaking the DSN.
    assert "://" not in checks["database"]


async def test_readiness_fails_when_alembic_version_is_empty(monkeypatch) -> None:
    """A schema created without migrations (no stamped revision) is not ready."""
    _single_process(monkeypatch)
    monkeypatch.setattr(health, "_expected_schema_head", lambda: "0092_head")
    monkeypatch.setattr(health, "engine", _FakeEngine(None))

    healthy, checks = await health.readiness_checks()

    assert healthy is False
    assert "none" in checks["database"]


async def test_readiness_ok_when_schema_matches_head(monkeypatch) -> None:
    _single_process(monkeypatch)
    monkeypatch.setattr(health, "_expected_schema_head", lambda: "0092_head")
    monkeypatch.setattr(health, "engine", _FakeEngine("0092_head"))

    healthy, checks = await health.readiness_checks()

    assert checks["database"] == "ok", checks
    assert healthy is True


async def test_readiness_skips_schema_check_when_head_is_unresolvable(monkeypatch) -> None:
    """Alembic trimmed from a slim image must degrade to the old connectivity
    check, never to a permanently-unready pod."""
    _single_process(monkeypatch)
    monkeypatch.setattr(health, "_expected_schema_head", lambda: "unknown")
    monkeypatch.setattr(health, "engine", _FakeEngine(None))

    healthy, checks = await health.readiness_checks()

    assert checks["database"] == "ok"
    assert healthy is True


async def test_readiness_reports_unreachable_database(monkeypatch) -> None:
    """Connectivity failure keeps its distinct, non-leaking message."""
    _single_process(monkeypatch)

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("postgresql+asyncpg://user:pw@host/db unreachable")

        async def __aexit__(self, *exc):
            return False

    class _Engine:
        def connect(self):
            return _Boom()

    monkeypatch.setattr(health, "engine", _Engine())

    healthy, checks = await health.readiness_checks()

    assert healthy is False
    assert checks["database"] == "error: unreachable"


def test_expected_schema_head_matches_the_shipped_migrations() -> None:
    """The head must come from the shipped migration scripts, not a constant,
    so the probe can never drift from the migrations in the same image."""
    head = health._expected_schema_head()
    assert head and head != "unknown"
    revisions = {
        path.stem
        for path in (
            Path(__file__).resolve().parents[1] / "alembic" / "versions"
        ).glob("[0-9]*.py")
    }
    assert head in revisions
