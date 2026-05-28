"""Slice 8 — remote runner pools, dispatch, queueing, and batch runs."""

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Run, Runner, Workflow
from app.schemas import SSHOnboardRequest
from app.services.crypto import create_payload_token, decode_payload_token, decrypt_data
from app.services.remote_dispatch import build_env_payload
from app.services.ssh_onboard import _install_script


def _graph_with_trigger() -> dict:
    return {
        "nodes": [
            {"id": "trig", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "echo", "type": "code", "params": {"code": "output = input"},
             "position": {"x": 250, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "trig", "source_output": "main",
             "target": "echo", "target_input": "input"}
        ],
    }


async def _create_published_workflow(client: AsyncClient) -> str:
    workflow_id = (await client.post("/workflows", json={"name": "WF"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": _graph_with_trigger()})
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    return workflow_id


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def test_runner_pool_crud(client: AsyncClient) -> None:
    resp = await client.post(
        "/runner-pools",
        json={"name": "prod-pool", "provider": "agent", "max_concurrent_runs": 8},
    )
    assert resp.status_code == 201
    pool = resp.json()
    assert pool["name"] == "prod-pool"
    assert pool["provider"] == "agent"
    assert pool["runner_count"] == 0

    pool_id = pool["id"]
    listed = (await client.get("/runner-pools")).json()
    assert len(listed) == 1 and listed[0]["id"] == pool_id

    got = (await client.get(f"/runner-pools/{pool_id}")).json()
    assert got["max_concurrent_runs"] == 8

    patched = await client.patch(
        f"/runner-pools/{pool_id}", json={"max_concurrent_runs": 16}
    )
    assert patched.json()["max_concurrent_runs"] == 16

    deleted = await client.delete(f"/runner-pools/{pool_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/runner-pools/{pool_id}")).status_code == 404


# ---------------------------------------------------------------------------
# Registration token
# ---------------------------------------------------------------------------


async def test_registration_token_creates_runner(client: AsyncClient) -> None:
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
    resp = await client.post(f"/runner-pools/{pool_id}/registration-tokens")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runner_id"]
    # The token round-trips and carries the runner id + kind.
    payload = decode_payload_token(body["token"])
    assert payload is not None
    assert payload["sub"] == body["runner_id"]
    assert payload["kind"] == "runner_registration"
    assert payload["pool_id"] == pool_id

    # The runner row exists, offline, listed under the pool.
    runners = (await client.get(f"/runner-pools/{pool_id}/runners")).json()
    assert len(runners) == 1
    assert runners[0]["status"] == "offline"


def test_payload_token_roundtrip_and_expiry() -> None:
    token = create_payload_token({"sub": "r1", "kind": "runner_registration"}, 3600)
    payload = decode_payload_token(token)
    assert payload is not None and payload["sub"] == "r1"

    # Tampered token fails.
    assert decode_payload_token(token + "x") is None
    assert decode_payload_token("garbage") is None

    # Expired token fails.
    expired = create_payload_token({"sub": "r1"}, -1)
    assert decode_payload_token(expired) is None


def test_build_env_payload_hash_is_stable() -> None:
    a = build_env_payload("env1", "3.12", ["pandas", "numpy"])
    b = build_env_payload("env1", "3.12", ["numpy", "pandas"])
    # Order-independent hash.
    assert a["packages_hash"] == b["packages_hash"]
    c = build_env_payload("env1", "3.12", ["pandas"])
    assert a["packages_hash"] != c["packages_hash"]


# ---------------------------------------------------------------------------
# Queue behaviour
# ---------------------------------------------------------------------------


async def test_run_queues_when_pool_has_no_runners(client: AsyncClient) -> None:
    """A workflow pinned to a pool with no online runners queues its run.

    Remote dispatch only engages in subprocess mode (in-process is
    cooperative-only), so the test flips that flag for the duration.
    """
    from app.config import settings

    pool_id = (await client.post("/runner-pools", json={"name": "empty"})).json()["id"]
    workflow_id = await _create_published_workflow(client)

    from app.services.runner import SessionLocal  # patched in conftest

    async with SessionLocal() as session:
        wf = await session.get(Workflow, workflow_id)
        wf.default_runner_pool_id = pool_id
        await session.commit()

    old = settings.use_subprocess_runner
    settings.use_subprocess_runner = True
    try:
        resp = await client.post(f"/workflows/{workflow_id}/run", json={})
        assert resp.status_code in (200, 201, 202)
        run_id = resp.json()["run_id"]
    finally:
        settings.use_subprocess_runner = old

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run.status == "queued"
        assert run.runner_pool_id == pool_id


# ---------------------------------------------------------------------------
# Batch runs
# ---------------------------------------------------------------------------


async def test_batch_runs_dispatch_one_run_per_parameter(client: AsyncClient) -> None:
    workflow_id = await _create_published_workflow(client)
    resp = await client.post(
        f"/runner-pools/workflows/{workflow_id}/batch-runs",
        json={"parameters": [{"city": "NYC"}, {"city": "LA"}, {"city": "SF"}]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == 3
    assert len(body["run_ids"]) == 3

    batch = (await client.get(f"/runner-pools/run-batches/{body['batch_id']}")).json()
    assert batch["total_runs"] == 3

    from app.services.runner import SessionLocal  # patched in conftest

    async with SessionLocal() as session:
        runs = (
            await session.scalars(
                select(Run).where(Run.batch_id == body["batch_id"])
            )
        ).all()
        assert len(runs) == 3


async def test_batch_run_requires_trigger(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "NoTrig"})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": {"nodes": [
            {"id": "n", "type": "code", "params": {"code": "output = 1"},
             "position": {"x": 0, "y": 0}}
        ], "edges": []}},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    resp = await client.post(
        f"/runner-pools/workflows/{workflow_id}/batch-runs",
        json={"parameters": [{"x": 1}]},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Artifact upload auth
# ---------------------------------------------------------------------------


async def test_artifact_upload_rejects_missing_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": "r", "node_id": "n", "artifact_id": "a",
            "name": "f.txt", "storage_key": "runs/r/n/a-f.txt",
        },
        files={"data": ("f.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 401


async def test_artifact_upload_writes_bytes_and_row(client: AsyncClient) -> None:
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
    token_resp = (
        await client.post(f"/runner-pools/{pool_id}/registration-tokens")
    ).json()

    # A run row must exist for the FK / list endpoint.
    workflow_id = await _create_published_workflow(client)
    from app.services.runner import SessionLocal  # patched in conftest

    async with SessionLocal() as session:
        run = Run(
            workflow_id=workflow_id, workflow_version=1, mode="manual",
            trigger_type="manual", status="success",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    storage_key = f"runs/{run_id}/node1/abc123-report.txt"
    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": run_id, "node_id": "node1", "artifact_id": "abc123",
            "name": "report.txt", "storage_key": storage_key,
            "content_type": "text/plain", "kind": "text", "size_bytes": 5,
        },
        files={"data": ("report.txt", b"hello", "text/plain")},
        headers={"Authorization": f"Bearer {token_resp['token']}"},
    )
    assert resp.status_code == 201
    assert resp.json()["artifact_id"] == "abc123"

    # The artifact is now downloadable through the normal API.
    listed = (await client.get(f"/runs/{run_id}/artifacts")).json()
    assert any(a["id"] == "abc123" for a in listed)
    download = await client.get("/artifacts/abc123/download")
    assert download.status_code == 200
    assert download.content == b"hello"


async def test_artifact_upload_rejects_path_escape(client: AsyncClient) -> None:
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
    token = (
        await client.post(f"/runner-pools/{pool_id}/registration-tokens")
    ).json()["token"]
    resp = await client.post(
        "/runner-pools/artifact-upload",
        params={
            "run_id": "r", "node_id": "n", "artifact_id": "a",
            "name": "f.txt", "storage_key": "../../etc/passwd",
        },
        files={"data": ("f.txt", b"x", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# SSH onboarding
# ---------------------------------------------------------------------------


async def test_ssh_onboard_creates_and_stores_encrypted(client, monkeypatch) -> None:
    pool_id = (await client.post("/runner-pools", json={"name": "ssh-pool"})).json()["id"]

    async def fake_onboard(req, api_url, token, name):
        assert api_url == "http://noodle.example:8000"
        return "[noodle] registered runner ok"

    monkeypatch.setattr("app.routers.runner_pools.onboard_machine", fake_onboard)

    resp = await client.post(
        f"/runner-pools/{pool_id}/ssh-onboard",
        json={
            "host": "10.0.0.5", "username": "ubuntu",
            "auth_method": "password", "password": "hunter2",
            "api_url": "http://noodle.example:8000",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "registered runner" in body["install_log"]

    from app.services.runner import SessionLocal  # patched in conftest

    async with SessionLocal() as session:
        runner = await session.get(Runner, body["runner_id"])
        assert runner.ssh_host == "ubuntu@10.0.0.5:22"
        assert runner.ssh_credentials  # encrypted, non-empty
        creds = decrypt_data(runner.ssh_credentials)
        assert creds["username"] == "ubuntu"
        assert creds["password"] == "hunter2"


async def test_ssh_onboard_failure_cleans_up_runner(client, monkeypatch) -> None:
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]

    async def boom(req, api_url, token, name):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.routers.runner_pools.onboard_machine", boom)

    resp = await client.post(
        f"/runner-pools/{pool_id}/ssh-onboard",
        json={
            "host": "h", "username": "u", "auth_method": "password",
            "password": "p", "api_url": "http://x",
        },
    )
    assert resp.status_code == 400
    assert "connection refused" in resp.json()["detail"]
    # The placeholder runner row was rolled back.
    runners = (await client.get(f"/runner-pools/{pool_id}/runners")).json()
    assert len(runners) == 0


async def test_ssh_onboard_rejects_non_agent_pool(client) -> None:
    pool_id = (
        await client.post("/runner-pools", json={"name": "d", "provider": "docker"})
    ).json()["id"]
    resp = await client.post(
        f"/runner-pools/{pool_id}/ssh-onboard",
        json={"host": "h", "username": "u", "auth_method": "password",
              "password": "p", "api_url": "http://x"},
    )
    assert resp.status_code == 400


async def test_ssh_onboard_requires_api_url(client, monkeypatch) -> None:
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
    monkeypatch.setattr(
        "app.routers.runner_pools.onboard_machine",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not connect")),
    )
    resp = await client.post(
        f"/runner-pools/{pool_id}/ssh-onboard",
        json={"host": "h", "username": "u", "auth_method": "password", "password": "p"},
    )
    assert resp.status_code == 400


def test_install_script_quotes_injection() -> None:
    req = SSHOnboardRequest(
        host="h", username="ubuntu", auth_method="password", password="p",
        use_systemd=False,
    )
    script = _install_script(req, "http://api", "tok", "evil; rm -rf /")
    # The malicious name is shell-quoted (single-quoted), not interpolated raw,
    # so the `;` can't break out into a second command.
    assert "'evil; rm -rf /'" in script
    assert "register --api-url http://api --token tok" in script
