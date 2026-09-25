"""Tests for the production-readiness audit, Wave 1.

Each test maps to a finding ID in docs/production-readiness-audit.md.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings

# ---------------------------------------------------------------------------
# SEC-2 — internal API token must be compared in constant time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_token_constant_time_and_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "s3cret-token")
    with patch("app.routers.internal._tick", new=AsyncMock()) as mock_tick:
        # Wrong token → 401, handler never runs.
        r = await client.post(
            "/internal/scheduler/tick",
            headers={"x-nodyra-internal-token": "wrong"},
        )
        assert r.status_code == 401
        # Missing token → 401.
        r = await client.post("/internal/scheduler/tick")
        assert r.status_code == 401
        mock_tick.assert_not_awaited()
        # Correct token → passes the gate.
        r = await client.post(
            "/internal/scheduler/tick",
            headers={"x-nodyra-internal-token": "s3cret-token"},
        )
        assert r.status_code == 200
        mock_tick.assert_awaited_once()


def test_internal_uses_compare_digest():
    import inspect

    from app.routers import internal

    source = inspect.getsource(internal._check_token)
    assert "compare_digest" in source, (
        "internal._check_token must use hmac.compare_digest for the token check"
    )


# ---------------------------------------------------------------------------
# SEC-3 — OAuth callback must be reachable when auth_required is on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_callback_auth_exempt(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    # No bearer token (mirrors the provider's top-level redirect). The callback
    # is authenticated by the signed state param, so the auth gate must not
    # reject it. With no code/state it returns the popup HTML (200), NOT the
    # gate's 401 "Authentication required".
    r = await client.get("/credentials/oauth/callback")
    assert r.status_code == 200
    assert "Authentication required" not in r.text
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["cross-origin-embedder-policy"] == "require-corp"
    assert r.headers["cross-origin-opener-policy"] == "same-origin-allow-popups"

    schema = (await client.get("/openapi.json")).json()
    callback_content = schema["paths"]["/credentials/oauth/callback"]["get"]["responses"]["200"][
        "content"
    ]
    assert "text/html" in callback_content


# ---------------------------------------------------------------------------
# REL-1 — CORS headers must be present on error (short-circuit) responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cors_headers_on_401(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    origin = settings.cors_origin_list[0]
    r = await client.get("/workflows", headers={"Origin": origin})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == origin, (
        "401 responses from the auth gate must carry CORS headers so the SPA "
        "can read the status (CORSMiddleware must be the outermost middleware)"
    )


# ---------------------------------------------------------------------------
# PERF-2 — derived Fernet is cached and rebuilds when the secret changes
# ---------------------------------------------------------------------------


def test_fernet_cached_and_rekeys(monkeypatch):
    from app.services import crypto

    monkeypatch.setattr(settings, "secret_key", "secret-a")
    crypto._fernet_cache = None
    f1 = crypto._fernet()
    f2 = crypto._fernet()
    assert f1 is f2, "Fernet should be cached across calls for the same secret"

    # Round-trip still works.
    token = crypto.encrypt_data({"x": 1})
    assert crypto.decrypt_data(token) == {"x": 1}

    monkeypatch.setattr(settings, "secret_key", "secret-b")
    f3 = crypto._fernet()
    assert f3 is not f1, "changing the secret must rebuild the Fernet"


def test_credential_dek_roundtrip_after_cache():
    """SEC-6/PERF-2 regression: per-credential DEK path still works."""
    from app.services import crypto

    enc, wrapped = crypto.encrypt_credential({"token": "abc"})
    assert crypto.decrypt_credential(enc, wrapped) == {"token": "abc"}


# ---------------------------------------------------------------------------
# REL-2 — runtime worker cancels orphaned sub-workflow callbacks on error
# ---------------------------------------------------------------------------


class _FakeStdin:
    def write(self, data):  # noqa: D401 - sync write like asyncio StreamWriter
        pass

    async def drain(self):
        pass


class _FakeStdout:
    def __init__(self, lines, *, before_eof=None):
        self._lines = list(lines)
        self._before_eof = before_eof

    async def readline(self):
        await asyncio.sleep(0)  # always yield so callback tasks can progress
        if self._lines:
            return self._lines.pop(0)
        # Let the real host callback finish its parent lookup and enter the
        # parked resolver before simulating a broken worker pipe.
        if self._before_eof is not None:
            await asyncio.wait_for(self._before_eof.wait(), timeout=5)
        return b""


class _FakeProc:
    def __init__(self, stdout):
        self.returncode = None
        self.stdin = _FakeStdin()
        self.stdout = stdout

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_runtime_run_cancels_callbacks_on_error(client):
    from app.models import Run
    from app.services import runtime_pool
    from app.services.runtime_pool import _RuntimeProcess

    workflow = (await client.post("/workflows", json={"name": "Callback cancellation"})).json()
    async with runtime_pool.SessionLocal() as session:
        parent = Run(workflow_id=workflow["id"], org_id="default", status="running")
        session.add(parent)
        await session.commit()
        run_id = parent.id

    caller_started = asyncio.Event()
    caller_cancelled = asyncio.Event()

    async def hanging_caller(call, *, parent_env_id=None):
        assert call.parent_run_id == run_id
        assert call.org_id == "default"
        caller_started.set()
        try:
            await asyncio.Event().wait()  # never resolves
        except asyncio.CancelledError:
            caller_cancelled.set()
            raise

    call_workflow_event = (
        json.dumps(
            {
                "type": "call_workflow",
                "callback_id": "c1",
                "workflow_id": "w1",
                "input": None,
            }
        ).encode()
        + b"\n"
    )
    stdout = _FakeStdout([call_workflow_event], before_eof=caller_started)
    proc = _RuntimeProcess(_FakeProc(stdout), None)

    with pytest.raises(RuntimeError):
        await proc.run(
            run_id,
            {"nodes": [], "edges": []},
            None,
            None,
            AsyncMock(),
            subworkflow_resolver=hanging_caller,
        )

    assert caller_started.is_set(), "callback should have started"
    assert caller_cancelled.is_set(), (
        "orphaned sub-workflow callback must be cancelled when the run loop "
        "exits via error (REL-2) — otherwise it leaks and corrupts the next "
        "run's stdio"
    )


# ---------------------------------------------------------------------------
# ART-1 — artifact upload must not allow path traversal via the filename
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_artifact_rejects_path_traversal(client):
    from sqlalchemy import select

    from app.models import Artifact
    from app.services import retention

    malicious = "../../../../../../tmp/nodyra_pwned.txt"
    r = await client.post(
        "/artifacts/upload",
        files={"file": (malicious, b"owned", "text/plain")},
    )
    assert r.status_code == 200, r.text
    # Name is reduced to a safe basename — no directory components survive.
    assert r.json()["name"] == "nodyra_pwned.txt"

    # The persisted storage_key must stay under the org's uploads/ prefix
    # (Phase F namespacing; "default" org while multi-tenancy is off) with no
    # traversal segments, so the bytes can only ever land inside the root.
    async with retention.SessionLocal() as session:
        row = (await session.scalars(select(Artifact))).first()
    assert row is not None
    assert row.storage_key.startswith("default/uploads/")
    assert ".." not in row.storage_key
