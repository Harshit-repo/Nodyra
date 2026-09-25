"""Worker HTTP listener (/metrics, /health/live, /health/ready) + readiness
error sanitization.

Regression coverage for two hardening fixes:

* The Helm chart's ``worker.healthPort`` readiness probe targets
  ``/health/ready`` on the worker — before ``worker_health_port`` existed the
  probe hit nothing and the rollout stalled with a permanently-unready pod.
* ``readiness_checks`` must never echo raw driver exceptions: the endpoint is
  auth-exempt (K8s probes), and driver errors can embed DSNs/credentials.
"""

import asyncio
import json

from app.config import settings
from app.routers import health
from app.worker_main import _serve_worker_http, _worker_http_response


class _Conn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        return None

    async def scalar(self, _query):
        # A migrated database answers the alembic_version probe that
        # readiness_checks runs alongside SELECT 1 (F-04).
        return health._expected_schema_head()


class _Engine:
    def connect(self):
        return _Conn()


class _BrokenConn:
    def __init__(self, message: str) -> None:
        self._message = message

    async def __aenter__(self):
        raise RuntimeError(self._message)

    async def __aexit__(self, *exc):
        return False


class _BrokenEngine:
    def __init__(self, message: str) -> None:
        self._message = message

    def connect(self):
        return _BrokenConn(self._message)


def _status(raw: bytes) -> int:
    return int(raw.split(b" ", 2)[1])


def _body(raw: bytes) -> bytes:
    return raw.split(b"\r\n\r\n", 1)[1]


async def test_worker_http_live():
    raw = await _worker_http_response(b"GET /health/live HTTP/1.1\r\n")
    assert _status(raw) == 200
    assert json.loads(_body(raw)) == {"status": "ok"}


async def test_worker_http_metrics():
    raw = await _worker_http_response(b"GET /metrics HTTP/1.1\r\n")
    assert _status(raw) == 200
    assert b"text/plain" in raw.split(b"\r\n\r\n", 1)[0]


async def test_worker_http_unknown_path_404():
    raw = await _worker_http_response(b"GET /nope HTTP/1.1\r\n")
    assert _status(raw) == 404


async def test_worker_http_non_get_405():
    raw = await _worker_http_response(b"POST /metrics HTTP/1.1\r\n")
    assert _status(raw) == 405


async def test_worker_http_ready_healthy(monkeypatch):
    monkeypatch.setattr(health, "engine", _Engine())
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    raw = await _worker_http_response(b"GET /health/ready HTTP/1.1\r\n")
    assert _status(raw) == 200
    payload = json.loads(_body(raw))
    assert payload["status"] == "ok"
    assert payload["checks"]["database"] == "ok"


async def test_worker_http_ready_query_string_ignored(monkeypatch):
    monkeypatch.setattr(health, "engine", _Engine())
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    raw = await _worker_http_response(b"GET /health/ready?verbose=1 HTTP/1.1\r\n")
    assert _status(raw) == 200


async def test_worker_http_ready_unhealthy_sanitized(monkeypatch):
    """A failing DB check 503s WITHOUT echoing the driver error (which can
    embed the DSN — hostname, user, password) to the anonymous prober."""
    secret_dsn = "postgresql+asyncpg://nodyra:sup3rs3cret@db.internal:5432/nodyra"
    monkeypatch.setattr(health, "engine", _BrokenEngine(f"cannot connect: {secret_dsn}"))
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    raw = await _worker_http_response(b"GET /health/ready HTTP/1.1\r\n")
    assert _status(raw) == 503
    assert b"sup3rs3cret" not in raw
    assert b"db.internal" not in raw
    payload = json.loads(_body(raw))
    assert payload["status"] == "degraded"
    assert payload["checks"]["database"] == "error: unreachable"


async def test_api_ready_route_sanitizes_db_error(monkeypatch):
    """Same sanitization contract on the API's /health/ready route."""
    monkeypatch.setattr(health, "engine", _BrokenEngine("password=hunter2 host=10.0.0.9"))
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    resp = await health.ready()
    assert resp.status_code == 503
    assert b"hunter2" not in resp.body
    assert b"10.0.0.9" not in resp.body
    assert json.loads(resp.body)["checks"]["database"] == "error: unreachable"


async def test_serve_worker_http_end_to_end(monkeypatch):
    """The real asyncio server answers a probe over a socket."""
    monkeypatch.setattr(health, "engine", _Engine())
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    server = await _serve_worker_http(0)
    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health/ready HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=5.0)
        writer.close()
        assert _status(raw) == 200
        assert json.loads(_body(raw))["status"] == "ok"
    finally:
        server.close()
        await server.wait_closed()
