"""Slice 8 — remote runner pools, dispatch, queueing, and batch runs."""

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Run, RunBatch, Runner, Workflow
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
    # A4: the response carries the URL the runner should dial — never empty,
    # and an http(s) URL (the request base), not the SPA origin.
    assert body["api_url"].startswith("http")

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


async def test_fleet_health_flags_pool_without_dispatcher(
    client: AsyncClient, monkeypatch
) -> None:
    """A6: an agent pool with a queued run but no live dispatcher heartbeat is
    reported unreachable + stuck — the signal behind the 'no dispatcher' banner.
    A pool whose provider has a heartbeat is reachable."""
    from app.services import dispatcher_health
    from app.services.runner import SessionLocal

    dispatcher_health.reset_local()

    agent_pool = (
        await client.post("/runner-pools", json={"name": "agents", "provider": "agent"})
    ).json()["id"]
    docker_pool = (
        await client.post(
            "/runner-pools", json={"name": "dock", "provider": "docker"}
        )
    ).json()["id"]

    # A queued run on each pool.
    from app.models import Run, RunQueueEntry, Workflow

    async with SessionLocal() as session:
        session.add(
            Workflow(
                id="wf",
                name="Fleet health fixture",
                draft_graph={"nodes": [], "edges": []},
            )
        )
        await session.flush()
        session.add_all(
            [
                Run(
                    id="q-agent",
                    workflow_id="wf",
                    runner_pool_id=agent_pool,
                    status="queued",
                ),
                Run(
                    id="q-docker",
                    workflow_id="wf",
                    runner_pool_id=docker_pool,
                    status="queued",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                RunQueueEntry(run_id="q-agent", workflow_id="wf",
                              runner_pool_id=agent_pool, status="queued"),
                RunQueueEntry(run_id="q-docker", workflow_id="wf",
                              runner_pool_id=docker_pool, status="queued"),
            ]
        )
        await session.commit()

    # Only docker has a live dispatcher (worker); agent has none.
    async def fake_live() -> set[str]:
        return {"local", "docker"}

    monkeypatch.setattr(dispatcher_health, "live_providers", fake_live)

    health = (await client.get("/runner-pools/health")).json()
    by_id = {p["pool_id"]: p for p in health["pools"]}
    assert by_id[agent_pool]["dispatcher_reachable"] is False
    assert by_id[agent_pool]["queue_depth"] == 1
    assert by_id[docker_pool]["dispatcher_reachable"] is True
    assert "agent" in health["fleet"]["providers_stuck"]
    assert "docker" not in health["fleet"]["providers_stuck"]


async def test_wheel_index_serves_built_wheels(
    client: AsyncClient, monkeypatch, tmp_path
) -> None:
    """A2: the wheel index page lists the nodyra-* wheels and each is
    downloadable, with a path-traversal guard. The actual ``uv build`` is
    stubbed — that round-trip is covered by the live clean-machine test."""
    from app.services import wheel_index

    fake = tmp_path / "_runner_wheels"
    fake.mkdir()
    for name in ("nodyra_core", "nodyra_runtime", "nodyra_nodes"):
        (fake / f"{name}-0.0.1-py3-none-any.whl").write_bytes(b"PK\x03\x04 stub")

    monkeypatch.setattr(wheel_index, "wheels_dir", lambda: fake)

    async def fake_ensure(*, force: bool = False):
        return wheel_index.list_wheels()

    monkeypatch.setattr(wheel_index, "ensure_wheels", fake_ensure)

    index = await client.get("/runner-pools/wheels/")
    assert index.status_code == 200
    assert "nodyra_core-0.0.1-py3-none-any.whl" in index.text
    assert "nodyra_runtime-0.0.1-py3-none-any.whl" in index.text

    whl = await client.get("/runner-pools/wheels/nodyra_core-0.0.1-py3-none-any.whl")
    assert whl.status_code == 200
    assert whl.content.startswith(b"PK")

    # Path-traversal / non-wheel requests are refused.
    assert (await client.get("/runner-pools/wheels/..%2fconfig.py")).status_code == 404
    assert (await client.get("/runner-pools/wheels/evil.txt")).status_code == 404


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


async def test_implicit_global_workflow_honours_env_pool_binding(
    client: AsyncClient,
) -> None:
    """A3: a workflow with NO explicit environment_id still routes to the pool
    bound to the global environment. Before the fix, pool resolution skipped the
    env entirely for these workflows, so the binding shown on the Environments
    page ("runs here execute on <pool>") was silently ignored."""
    from sqlalchemy import select as _select

    from app.config import settings
    from app.models import Environment
    from app.services.runner import SessionLocal

    pool_id = (
        await client.post("/runner-pools", json={"name": "global-bound"})
    ).json()["id"]
    workflow_id = await _create_published_workflow(client)

    # Bind the pool to the GLOBAL env; leave the workflow's environment_id unset.
    async with SessionLocal() as session:
        global_env = await session.scalar(
            _select(Environment).where(Environment.is_global.is_(True))
        )
        if global_env is None:
            global_env = Environment(
                name="Global", is_global=True, status="ready"
            )
            session.add(global_env)
        global_env.runner_pool_id = pool_id
        wf = await session.get(Workflow, workflow_id)
        assert wf.environment_id is None
        await session.commit()

    old = settings.use_subprocess_runner
    settings.use_subprocess_runner = True
    try:
        resp = await client.post(f"/workflows/{workflow_id}/run", json={})
        run_id = resp.json()["run_id"]
    finally:
        settings.use_subprocess_runner = old

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run.runner_pool_id == pool_id


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
    pool_id = (await client.post("/runner-pools", json={"name": "batch"})).json()["id"]
    parameters = [{"city": "NYC"}, {"city": "LA"}, {"city": "SF"}]
    resp = await client.post(
        f"/runner-pools/workflows/{workflow_id}/batch-runs",
        json={"runner_pool_id": pool_id, "parameters": parameters},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == 3
    assert len(body["run_ids"]) == 3

    batch = (await client.get(f"/runner-pools/run-batches/{body['batch_id']}")).json()
    assert batch["total_runs"] == 3
    assert batch["runner_pool_id"] == pool_id
    assert batch["status"] == "completed"
    assert batch["succeeded_runs"] == 3

    from app.services.runner import SessionLocal  # patched in conftest

    async with SessionLocal() as session:
        runs = (
            await session.scalars(
                select(Run).where(Run.batch_id == body["batch_id"])
            )
        ).all()
        assert len(runs) == 3
        assert {run.runner_pool_id for run in runs} == {pool_id}
        batch_row = await session.get(RunBatch, body["batch_id"])
        assert batch_row is not None
        assert batch_row.parameters == parameters


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
            runner_id=token_resp["runner_id"],
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
        assert api_url == "http://nodyra.example:8000"
        return "[nodyra] registered runner ok"

    monkeypatch.setattr("app.routers.runner_pools.onboard_machine", fake_onboard)

    resp = await client.post(
        f"/runner-pools/{pool_id}/ssh-onboard",
        json={
            "host": "10.0.0.5", "username": "ubuntu",
            "auth_method": "password", "password": "hunter2",
            "api_url": "http://nodyra.example:8000",
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


# --- Task 7: runner heartbeats + lease expiry --------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from app.models import RunQueueEntry  # noqa: E402
from app.services.remote_dispatch import dispatcher  # noqa: E402


async def test_mark_stale_runners_offline_marks_and_requeues(client: AsyncClient) -> None:
    """A runner that hasn't sent a pong in N seconds is marked offline and
    its in-flight Run + RunQueueEntry are returned to ``queued``."""
    from app.db import get_session as _get_session
    override = client._transport.app.dependency_overrides[_get_session]

    # Create pool + an online runner that hasn't been seen in 5 minutes.
    pool_id = (await client.post(
        "/runner-pools",
        json={"name": "p1", "provider": "agent", "provider_config": {}},
    )).json()["id"]

    workflow_id = (await client.post("/workflows", json={"name": "WF"})).json()["id"]

    runner_id: str | None = None
    run_id: str | None = None
    async for session in override():
        runner = Runner(
            pool_id=pool_id,
            name="r-stale",
            status="online",
            last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
            current_runs=1,
            max_concurrent_runs=1,
        )
        session.add(runner)
        await session.flush()
        runner_id = runner.id

        run = Run(
            workflow_id=workflow_id,
            status="running",
            runner_id=runner_id,
            runner_pool_id=pool_id,
        )
        session.add(run)
        await session.flush()
        run_id = run.id

        entry = RunQueueEntry(
            run_id=run_id,
            workflow_id=workflow_id,
            runner_pool_id=pool_id,
            status="leased",
            leased_by="some-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=10),
            attempts=1,
        )
        session.add(entry)
        await session.commit()
        break

    marked = await dispatcher.mark_stale_runners_offline(offline_after_seconds=60)
    assert runner_id in marked

    async for session in override():
        runner = await session.get(Runner, runner_id)
        assert runner.status == "offline"
        assert runner.current_runs == 0
        run = await session.get(Run, run_id)
        assert run.status == "pending"
        assert run.runner_id is None
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry.status == "queued"
        assert entry.leased_by is None
        assert entry.lease_expires_at is None
        assert entry.queue_reason == "runner_offline"
        assert entry.attempts_log[-1]["event"] == "runner_offline"
        break


async def test_mark_stale_runners_offline_leaves_healthy_alone(client: AsyncClient) -> None:
    from app.db import get_session as _get_session
    override = client._transport.app.dependency_overrides[_get_session]

    pool_id = (await client.post(
        "/runner-pools",
        json={"name": "p2", "provider": "agent", "provider_config": {}},
    )).json()["id"]

    runner_id: str | None = None
    async for session in override():
        runner = Runner(
            pool_id=pool_id,
            name="r-fresh",
            status="online",
            last_seen_at=datetime.now(UTC),  # just heard
            current_runs=0,
            max_concurrent_runs=1,
        )
        session.add(runner)
        await session.flush()
        runner_id = runner.id
        await session.commit()
        break

    marked = await dispatcher.mark_stale_runners_offline(offline_after_seconds=60)
    assert runner_id not in marked

    async for session in override():
        runner = await session.get(Runner, runner_id)
        assert runner.status == "online"
        break


async def test_mark_stale_runners_handles_never_seen_runner(client: AsyncClient) -> None:
    """``last_seen_at`` is NULL for an online runner — treat it as offline."""
    from app.db import get_session as _get_session
    override = client._transport.app.dependency_overrides[_get_session]

    pool_id = (await client.post(
        "/runner-pools",
        json={"name": "p3", "provider": "agent", "provider_config": {}},
    )).json()["id"]

    runner_id: str | None = None
    async for session in override():
        runner = Runner(
            pool_id=pool_id,
            name="r-zombie",
            status="online",
            last_seen_at=None,
            current_runs=0,
            max_concurrent_runs=1,
        )
        session.add(runner)
        await session.flush()
        runner_id = runner.id
        await session.commit()
        break

    marked = await dispatcher.mark_stale_runners_offline(offline_after_seconds=60)
    assert runner_id in marked


async def test_ping_connected_agents_sends_to_each_connection() -> None:
    """``ping_connected_agents`` walks the in-memory registry without DB IO."""
    from app.services.remote_dispatch import _AgentConnection

    class _FakeWS:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_text(self, text: str) -> None:
            import json
            self.sent.append(json.loads(text))

    ws1 = _FakeWS()
    ws2 = _FakeWS()
    conn1 = _AgentConnection(runner_id="r-1", ws=ws1)
    conn2 = _AgentConnection(runner_id="r-2", ws=ws2)
    async with dispatcher._lock:
        dispatcher._agents["r-1"] = conn1
        dispatcher._agents["r-2"] = conn2
    try:
        sent = await dispatcher.ping_connected_agents()
        assert sent == 2
        assert ws1.sent[-1]["type"] == "ping"
        assert ws2.sent[-1]["type"] == "ping"
    finally:
        async with dispatcher._lock:
            dispatcher._agents.pop("r-1", None)
            dispatcher._agents.pop("r-2", None)


async def test_pick_agent_prefers_least_loaded(client: AsyncClient) -> None:
    """``_pick_agent`` spreads load by choosing the most-free connected runner."""
    from app.db import get_session as _get_session
    from app.services.remote_dispatch import _AgentConnection

    override = client._transport.app.dependency_overrides[_get_session]
    pool_id = (await client.post(
        "/runner-pools",
        json={"name": "spread", "provider": "agent", "max_concurrent_runs": 10},
    )).json()["id"]

    busy_id = loaded_id = ""
    async for session in override():
        busy = Runner(
            pool_id=pool_id, name="busy", status="online",
            current_runs=2, max_concurrent_runs=3,
        )
        free = Runner(
            pool_id=pool_id, name="free", status="online",
            current_runs=0, max_concurrent_runs=3,
        )
        session.add_all([busy, free])
        await session.flush()
        busy_id, loaded_id = busy.id, free.id
        await session.commit()
        break

    conn_busy = _AgentConnection(runner_id=busy_id, ws=object())
    conn_free = _AgentConnection(runner_id=loaded_id, ws=object())
    async with dispatcher._lock:
        dispatcher._agents[busy_id] = conn_busy
        dispatcher._agents[loaded_id] = conn_free
    try:
        picked = await dispatcher._pick_agent(pool_id)
        assert picked is conn_free  # 3 free slots beats 1
    finally:
        async with dispatcher._lock:
            dispatcher._agents.pop(busy_id, None)
            dispatcher._agents.pop(loaded_id, None)


async def test_pick_agent_honours_pool_ceiling(client: AsyncClient) -> None:
    """A pool at its ``max_concurrent_runs`` returns no agent (queue instead)."""
    from app.db import get_session as _get_session
    from app.services.remote_dispatch import _AgentConnection

    override = client._transport.app.dependency_overrides[_get_session]
    pool_id = (await client.post(
        "/runner-pools",
        json={"name": "capped", "provider": "agent", "max_concurrent_runs": 2},
    )).json()["id"]

    runner_id = ""
    async for session in override():
        runner = Runner(
            pool_id=pool_id, name="r", status="online",
            current_runs=2, max_concurrent_runs=5,
        )
        session.add(runner)
        await session.flush()
        runner_id = runner.id
        await session.commit()
        break

    conn = _AgentConnection(runner_id=runner_id, ws=object())
    async with dispatcher._lock:
        dispatcher._agents[runner_id] = conn
    try:
        # Pool is already at 2/2 even though the runner could take more.
        assert await dispatcher._pick_agent(pool_id) is None
    finally:
        async with dispatcher._lock:
            dispatcher._agents.pop(runner_id, None)


# ---------------------------------------------------------------------------
# Runner machine details (PATCH + token body)
# ---------------------------------------------------------------------------


async def test_registration_token_accepts_machine_details(client: AsyncClient) -> None:
    pool_id = (await client.post('/runner-pools', json={'name': 'p'})).json()['id']
    resp = await client.post(
        f'/runner-pools/{pool_id}/registration-tokens',
        json={
            'name': 'ci-worker-3',
            'max_concurrent_runs': 8,
            'capabilities': {'region': 'eu', 'gpu': 'a100'},
        },
    )
    assert resp.status_code == 200
    runners = (await client.get(f'/runner-pools/{pool_id}/runners')).json()
    assert len(runners) == 1
    assert runners[0]['name'] == 'ci-worker-3'
    assert runners[0]['max_concurrent_runs'] == 8
    assert runners[0]['capabilities'] == {'region': 'eu', 'gpu': 'a100'}


async def test_registration_token_without_body_still_uses_defaults(client: AsyncClient) -> None:
    pool_id = (await client.post('/runner-pools', json={'name': 'p'})).json()['id']
    resp = await client.post(f'/runner-pools/{pool_id}/registration-tokens')
    assert resp.status_code == 200
    runners = (await client.get(f'/runner-pools/{pool_id}/runners')).json()
    assert runners[0]['max_concurrent_runs'] == 1
    assert runners[0]['capabilities'] == {}


async def test_patch_runner_updates_editable_fields(client: AsyncClient) -> None:
    pool_id = (await client.post('/runner-pools', json={'name': 'p'})).json()['id']
    runner_id = (
        await client.post(f'/runner-pools/{pool_id}/registration-tokens')
    ).json()['runner_id']

    resp = await client.patch(
        f'/runner-pools/{pool_id}/runners/{runner_id}',
        json={
            'name': 'renamed',
            'max_concurrent_runs': 4,
            'capabilities': {'env': 'prod'},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['name'] == 'renamed'
    assert body['max_concurrent_runs'] == 4
    assert body['capabilities'] == {'env': 'prod'}


async def test_patch_runner_404_for_unknown_runner(client: AsyncClient) -> None:
    pool_id = (await client.post('/runner-pools', json={'name': 'p'})).json()['id']
    resp = await client.patch(
        f'/runner-pools/{pool_id}/runners/no-such',
        json={'name': 'x'},
    )
    assert resp.status_code == 404


async def test_patch_runner_404_when_runner_belongs_to_other_pool(client: AsyncClient) -> None:
    pool_a = (await client.post('/runner-pools', json={'name': 'a'})).json()['id']
    pool_b = (await client.post('/runner-pools', json={'name': 'b'})).json()['id']
    runner_id = (
        await client.post(f'/runner-pools/{pool_a}/registration-tokens')
    ).json()['runner_id']

    # Path uses pool_b but runner lives in pool_a -> 404.
    resp = await client.patch(
        f'/runner-pools/{pool_b}/runners/{runner_id}',
        json={'name': 'x'},
    )
    assert resp.status_code == 404
