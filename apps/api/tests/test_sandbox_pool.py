"""SandboxWorker lifecycle: spawn, ready handshake, protocol, teardown."""

import asyncio

import pytest

from app.config import settings
from app.services.container_runtime import IMAGE_SCHEMA_VERSION
from app.services.sandbox_pool import SandboxWorker
from tests.sandbox_fakes import FakeDockerClient

ENV = {"id": "env1", "packages_hash": "h1", "python_version": "3.12", "packages": []}


def test_spawn_waits_for_ready_and_is_hardened():
    client = FakeDockerClient(runtimes=("runc", "runsc"))

    async def scenario():
        return await SandboxWorker.spawn(
            client,
            key=("org1", "env1"),
            env_payload=ENV,
            runtime="runsc",
            network="nodyra-sandbox",
        )

    worker = asyncio.run(scenario())
    assert worker.key == ("org1", "env1")
    assert not worker.dead
    call = client.run_calls[0]
    assert call["cap_drop"] == ["ALL"]
    assert call["runtime"] == "runsc"
    assert call["image"].endswith(f"-{IMAGE_SCHEMA_VERSION}")
    assert call["name"].startswith("nodyra-sbx-")


def test_spawn_ready_timeout_kills_container(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_ready_timeout_seconds", 0.2)
    client = FakeDockerClient(auto_ready=False)  # runtime never says ready

    async def scenario():
        await SandboxWorker.spawn(
            client,
            key=("org1", "env1"),
            env_payload=ENV,
            runtime="runc",
            network="nodyra-sandbox",
        )

    with pytest.raises(RuntimeError, match="ready"):
        asyncio.run(scenario())
    assert client.containers_made[0].removed


def test_spawn_container_dies_before_ready():
    client = FakeDockerClient(auto_ready=False)

    async def scenario():
        task = asyncio.create_task(
            SandboxWorker.spawn(
                client,
                key=("o", "e"),
                env_payload=ENV,
                runtime="runc",
                network="nodyra-sandbox",
            )
        )
        for _ in range(50):
            if client.containers_made:
                break
            await asyncio.sleep(0.02)
        assert client.containers_made
        client.containers_made[0].sock._sock.feed_eof()
        await task

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
    assert client.containers_made[0].removed


# --- run protocol loop --------------------------------------------------------


async def _spawned_worker(client):
    return await SandboxWorker.spawn(
        client,
        key=("org1", "env1"),
        env_payload=ENV,
        runtime="runc",
        network="nodyra-sandbox",
    )


def test_run_forwards_events_and_returns_status():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock
        sock.feed({"type": "node_started", "node_id": "n1"})
        sock.feed({"type": "node_finished", "node_id": "n1", "status": "success"})
        sock.feed({"type": "result", "status": "success"})
        events = []

        async def on_event(e):
            events.append(e)

        status = await worker.run(
            "run1",
            graph={"nodes": []},
            cache=None,
            targets=None,
            workflow_modules=[],
            on_event=on_event,
        )
        return status, events, worker

    status, events, worker = asyncio.run(scenario())
    assert status == "success"
    assert [e["type"] for e in events] == ["node_started", "node_finished"]
    assert not worker.dead
    assert worker.runs_completed == 1
    # the run message went over the wire with the run id
    sock = client.containers_made[0].sock._sock
    run_msgs = [m for m in sock.sent_messages() if m.get("type") == "run"]
    assert len(run_msgs) == 1 and run_msgs[0]["request_id"] == "run1"


def test_run_container_death_marks_dead():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        client.containers_made[0].sock._sock.feed_eof()  # dies mid-run

        async def on_event(e):
            pass

        with pytest.raises(RuntimeError):
            await worker.run(
                "run1", graph={}, cache=None, targets=None, workflow_modules=[], on_event=on_event
            )
        return worker

    worker = asyncio.run(scenario())
    assert worker.dead


def test_run_timeout_marks_dead(monkeypatch):
    monkeypatch.setattr(settings, "workflow_run_timeout_seconds", 0.2)
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        # feed nothing after ready: recv times out

        async def on_event(e):
            pass

        with pytest.raises(RuntimeError, match="read failed"):
            await worker.run(
                "run1", graph={}, cache=None, targets=None, workflow_modules=[], on_event=on_event
            )
        return worker

    worker = asyncio.run(scenario())
    assert worker.dead


def test_run_runtime_error_event_is_dirty():
    """A terminal {"type":"error"} (unrecoverable runtime failure) surfaces as
    run_error and the container is not reusable."""
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock
        sock.feed({"type": "error", "error": "import explosion"})
        events = []

        async def on_event(e):
            events.append(e)

        status = await worker.run(
            "run1", graph={}, cache=None, targets=None, workflow_modules=[], on_event=on_event
        )
        return status, events, worker

    status, events, worker = asyncio.run(scenario())
    assert status == "error"
    assert events[0]["type"] == "run_error" and "import explosion" in events[0]["error"]
    assert worker.dead  # no result event → never pooled again


# --- call_workflow host-callback bridging -------------------------------------


def test_call_workflow_bridged_to_resolver():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock
        resolved = asyncio.Event()

        async def resolver(call, parent_env_id=None):
            assert parent_env_id == "env1"
            assert call.workflow_id == "wf2"
            resolved.set()
            return {"answer": 42}

        async def on_event(e):
            pass

        run_task = asyncio.create_task(
            worker.run(
                "run1",
                graph={},
                cache=None,
                targets=None,
                workflow_modules=[],
                on_event=on_event,
                subworkflow_resolver=resolver,
            )
        )
        sock.feed(
            {
                "type": "call_workflow",
                "callback_id": "cb1",
                "request_id": "run1",
                "workflow_id": "wf2",
                "input": None,
                "depth": 1,
                "call_chain": ["wf1"],
            }
        )
        await asyncio.wait_for(resolved.wait(), 3)
        # wait until the response hits the wire, then finish the run
        for _ in range(50):
            if any(m.get("type") == "call_workflow_response" for m in sock.sent_messages()):
                break
            await asyncio.sleep(0.05)
        sock.feed({"type": "result", "status": "success"})
        status = await run_task
        return status, sock.sent_messages()

    status, messages = asyncio.run(scenario())
    assert status == "success"
    responses = [m for m in messages if m.get("type") == "call_workflow_response"]
    assert responses == [
        {"type": "call_workflow_response", "callback_id": "cb1", "result": {"answer": 42}}
    ]


def test_call_workflow_resolver_error_replied():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock

        async def resolver(call, parent_env_id=None):
            raise ValueError("no such workflow")

        async def on_event(e):
            pass

        run_task = asyncio.create_task(
            worker.run(
                "run1",
                graph={},
                cache=None,
                targets=None,
                workflow_modules=[],
                on_event=on_event,
                subworkflow_resolver=resolver,
            )
        )
        sock.feed(
            {
                "type": "call_workflow",
                "callback_id": "cb2",
                "request_id": "run1",
                "workflow_id": "missing",
                "input": None,
                "depth": 1,
                "call_chain": [],
            }
        )
        for _ in range(50):
            if any(m.get("type") == "call_workflow_error" for m in sock.sent_messages()):
                break
            await asyncio.sleep(0.05)
        sock.feed({"type": "result", "status": "error"})
        await run_task
        return sock.sent_messages()

    messages = asyncio.run(scenario())
    errors = [m for m in messages if m.get("type") == "call_workflow_error"]
    assert len(errors) == 1 and "no such workflow" in errors[0]["error"]


# --- SandboxPool: warm reuse, eviction, recycling ------------------------------

from app.services.sandbox_pool import SandboxPool  # noqa: E402


def _make_pool(client) -> SandboxPool:
    p = SandboxPool()
    p.configure(client, runtime="runc", network="nodyra-sandbox")
    return p


def _dispatch(pool, run_id, org="org1", env="env1"):
    async def on_event(e):
        pass

    return pool.dispatch(
        run_id,
        org_id=org,
        env_id=env,
        env_payload={"id": env, "packages_hash": "h1", "python_version": "3.12", "packages": []},
        graph={},
        cache=None,
        targets=None,
        workflow_modules=[],
        on_event=on_event,
    )


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    """Wait for an async dispatch milestone without fixed-sleep flakes."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("sandbox dispatch did not reach the expected state")
        await asyncio.sleep(0.01)


def test_warm_reuse_within_key():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1"))
        await _wait_until(lambda: len(client.containers_made) >= 1)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        assert await t1 == "success"
        # second run, same key: reuses the warm container
        t2 = asyncio.create_task(_dispatch(pool, "r2"))
        await _wait_until(lambda: "r2" in pool._active)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        assert await t2 == "success"

    asyncio.run(scenario())
    assert len(client.containers_made) == 1  # one container served both runs


def test_no_cross_key_reuse():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1", org="orgA"))
        await _wait_until(lambda: len(client.containers_made) >= 1)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        await t1
        t2 = asyncio.create_task(_dispatch(pool, "r2", org="orgB"))  # different org!
        await _wait_until(lambda: len(client.containers_made) >= 2)
        client.containers_made[1].sock._sock.feed({"type": "result", "status": "success"})
        await t2

    asyncio.run(scenario())
    assert len(client.containers_made) == 2  # orgB never got orgA's container


def test_dirty_exit_not_pooled():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1"))
        await _wait_until(lambda: len(client.containers_made) >= 1)
        client.containers_made[0].sock._sock.feed_eof()  # crash
        assert await t1 == "error"
        t2 = asyncio.create_task(_dispatch(pool, "r2"))
        await _wait_until(lambda: len(client.containers_made) >= 2)
        client.containers_made[1].sock._sock.feed({"type": "result", "status": "success"})
        await t2

    asyncio.run(scenario())
    assert client.containers_made[0].removed
    assert len(client.containers_made) == 2


def test_stale_image_not_reused():
    """Env packages changed (new packages_hash) → warm container discarded."""
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t1 = asyncio.create_task(_dispatch(pool, "r1"))
        await _wait_until(lambda: len(client.containers_made) >= 1)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        await t1

        async def on_event(e):
            pass

        t2 = asyncio.create_task(
            pool.dispatch(
                "r2",
                org_id="org1",
                env_id="env1",
                env_payload={
                    "id": "env1",
                    "packages_hash": "CHANGED",
                    "python_version": "3.12",
                    "packages": [],
                },
                graph={},
                cache=None,
                targets=None,
                workflow_modules=[],
                on_event=on_event,
            )
        )
        await _wait_until(lambda: len(client.containers_made) >= 2)
        client.containers_made[1].sock._sock.feed({"type": "result", "status": "success"})
        await t2

    asyncio.run(scenario())
    assert client.containers_made[0].removed  # stale-image worker destroyed
    assert len(client.containers_made) == 2


def test_recycle_after_max_runs(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max_runs_per_container", 1)
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        for expected_count, rid in enumerate(("r1", "r2"), start=1):
            t = asyncio.create_task(_dispatch(pool, rid))
            await _wait_until(
                lambda count=expected_count: len(client.containers_made) >= count
            )
            client.containers_made[-1].sock._sock.feed({"type": "result", "status": "success"})
            await t

    asyncio.run(scenario())
    assert len(client.containers_made) == 2  # recycled after every run
    assert client.containers_made[0].removed


def test_warm_total_cap_evicts_lru(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_warm_total", 1)
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        for expected_count, (rid, org) in enumerate(
            (("r1", "orgA"), ("r2", "orgB")), start=1
        ):
            t = asyncio.create_task(_dispatch(pool, rid, org=org))
            await _wait_until(
                lambda count=expected_count: len(client.containers_made) >= count
            )
            client.containers_made[-1].sock._sock.feed({"type": "result", "status": "success"})
            await t

    asyncio.run(scenario())
    # orgA's idle worker was evicted to make room for orgB's
    assert client.containers_made[0].removed
    assert not client.containers_made[1].removed


def test_cancel_force_removes():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t = asyncio.create_task(_dispatch(pool, "r1"))
        await _wait_until(lambda: "r1" in pool._active)
        assert await pool.cancel("r1") is True
        # the read loop now sees EOF/error from the removed container
        client.containers_made[0].sock._sock.feed_eof()
        status = await t
        assert status == "error"
        assert await pool.cancel("r1") is False  # already gone

    asyncio.run(scenario())
    assert client.containers_made[0].removed


def test_flush_closes_idle():
    client = FakeDockerClient()

    async def scenario():
        pool = _make_pool(client)
        t = asyncio.create_task(_dispatch(pool, "r1"))
        await _wait_until(lambda: len(client.containers_made) >= 1)
        client.containers_made[0].sock._sock.feed({"type": "result", "status": "success"})
        await t
        await pool.flush()

    asyncio.run(scenario())
    assert client.containers_made[0].removed


# --- init_sandbox startup probe -------------------------------------------------

from app.services import sandbox_pool as sp  # noqa: E402


def test_init_off_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)
    assert asyncio.run(sp.init_sandbox()) is None
    assert not fresh.enabled


def test_init_auto_falls_back_without_daemon(monkeypatch, caplog):
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)

    def no_daemon():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sp, "_make_docker_client", no_daemon)
    with caplog.at_level("WARNING"):
        assert asyncio.run(sp.init_sandbox()) is None
    assert not fresh.enabled
    assert any("falling back" in r.message for r in caplog.records)


def test_init_required_raises_without_daemon(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)

    def no_daemon():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sp, "_make_docker_client", no_daemon)
    with pytest.raises(RuntimeError, match="execution_sandbox=required"):
        asyncio.run(sp.init_sandbox())


def test_init_required_with_daemon(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    monkeypatch.setattr(settings, "sandbox_runtime", "auto")
    fresh = SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)
    client = FakeDockerClient(runtimes=("runc", "runsc"))
    monkeypatch.setattr(sp, "_make_docker_client", lambda: client)
    assert asyncio.run(sp.init_sandbox()) == "runsc"
    assert fresh.enabled
    assert "nodyra-sandbox" in client.networks.existing


async def test_execute_run_passes_org_to_impl_when_tracing_disabled(monkeypatch):
    """Sandbox pooling keys must not depend on OpenTelemetry being enabled."""
    from app.services import runner
    from app.tenancy import current_org_id

    seen = {}
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(runner.tracing, "enabled", lambda: False)

    async def fake_resolve_run_org(run_id):
        assert run_id == "run-org"
        return "org-sandbox"

    async def fake_execute_run_impl(*args, trace_org=None, **kwargs):
        seen["trace_org"] = trace_org
        seen["ambient_org"] = current_org_id.get()
        return "success"

    monkeypatch.setattr(runner, "_resolve_run_org", fake_resolve_run_org)
    monkeypatch.setattr(runner, "_execute_run_impl", fake_execute_run_impl)
    token = current_org_id.set(None)
    try:
        await runner._execute_run("run-org", "wf", {}, None)
        assert seen == {"trace_org": "org-sandbox", "ambient_org": "org-sandbox"}
        assert current_org_id.get() is None
    finally:
        current_org_id.reset(token)
