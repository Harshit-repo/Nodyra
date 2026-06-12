"""SandboxWorker lifecycle: spawn, ready handshake, protocol, teardown."""
import asyncio

import pytest

from app.config import settings
from app.services.sandbox_pool import SandboxWorker
from tests.sandbox_fakes import FakeDockerClient

ENV = {"id": "env1", "packages_hash": "h1", "python_version": "3.12", "packages": []}


def test_spawn_waits_for_ready_and_is_hardened():
    client = FakeDockerClient(runtimes=("runc", "runsc"))

    async def scenario():
        return await SandboxWorker.spawn(
            client, key=("org1", "env1"), env_payload=ENV,
            runtime="runsc", network="noodle-sandbox",
        )

    worker = asyncio.run(scenario())
    assert worker.key == ("org1", "env1")
    assert not worker.dead
    call = client.run_calls[0]
    assert call["cap_drop"] == ["ALL"]
    assert call["runtime"] == "runsc"
    assert call["image"].endswith("-v2")
    assert call["name"].startswith("noodle-sbx-")


def test_spawn_ready_timeout_kills_container(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_ready_timeout_seconds", 0.2)
    client = FakeDockerClient(auto_ready=False)  # runtime never says ready

    async def scenario():
        await SandboxWorker.spawn(
            client, key=("org1", "env1"), env_payload=ENV,
            runtime="runc", network="noodle-sandbox",
        )

    with pytest.raises(RuntimeError, match="ready"):
        asyncio.run(scenario())
    assert client.containers_made[0].removed


def test_spawn_container_dies_before_ready():
    client = FakeDockerClient(auto_ready=False)

    async def scenario():
        task = asyncio.create_task(SandboxWorker.spawn(
            client, key=("o", "e"), env_payload=ENV,
            runtime="runc", network="noodle-sandbox",
        ))
        await asyncio.sleep(0.1)
        client.containers_made[0].sock._sock.feed_eof()
        await task

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
    assert client.containers_made[0].removed


# --- run protocol loop --------------------------------------------------------


async def _spawned_worker(client):
    return await SandboxWorker.spawn(
        client, key=("org1", "env1"), env_payload=ENV,
        runtime="runc", network="noodle-sandbox",
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
            "run1", graph={"nodes": []}, cache=None, targets=None,
            workflow_modules=[], on_event=on_event,
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
            await worker.run("run1", graph={}, cache=None, targets=None,
                             workflow_modules=[], on_event=on_event)
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
            await worker.run("run1", graph={}, cache=None, targets=None,
                             workflow_modules=[], on_event=on_event)
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

        status = await worker.run("run1", graph={}, cache=None, targets=None,
                                  workflow_modules=[], on_event=on_event)
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

        run_task = asyncio.create_task(worker.run(
            "run1", graph={}, cache=None, targets=None, workflow_modules=[],
            on_event=on_event, subworkflow_resolver=resolver,
        ))
        sock.feed({"type": "call_workflow", "callback_id": "cb1",
                   "request_id": "run1", "workflow_id": "wf2", "input": None,
                   "depth": 1, "call_chain": ["wf1"]})
        await asyncio.wait_for(resolved.wait(), 3)
        # wait until the response hits the wire, then finish the run
        for _ in range(50):
            if any(m.get("type") == "call_workflow_response"
                   for m in sock.sent_messages()):
                break
            await asyncio.sleep(0.05)
        sock.feed({"type": "result", "status": "success"})
        status = await run_task
        return status, sock.sent_messages()

    status, messages = asyncio.run(scenario())
    assert status == "success"
    responses = [m for m in messages if m.get("type") == "call_workflow_response"]
    assert responses == [{"type": "call_workflow_response", "callback_id": "cb1",
                          "result": {"answer": 42}}]


def test_call_workflow_resolver_error_replied():
    client = FakeDockerClient()

    async def scenario():
        worker = await _spawned_worker(client)
        sock = client.containers_made[0].sock._sock

        async def resolver(call, parent_env_id=None):
            raise ValueError("no such workflow")

        async def on_event(e):
            pass

        run_task = asyncio.create_task(worker.run(
            "run1", graph={}, cache=None, targets=None, workflow_modules=[],
            on_event=on_event, subworkflow_resolver=resolver,
        ))
        sock.feed({"type": "call_workflow", "callback_id": "cb2",
                   "request_id": "run1", "workflow_id": "missing", "input": None,
                   "depth": 1, "call_chain": []})
        for _ in range(50):
            if any(m.get("type") == "call_workflow_error"
                   for m in sock.sent_messages()):
                break
            await asyncio.sleep(0.05)
        sock.feed({"type": "result", "status": "error"})
        await run_task
        return sock.sent_messages()

    messages = asyncio.run(scenario())
    errors = [m for m in messages if m.get("type") == "call_workflow_error"]
    assert len(errors) == 1 and "no such workflow" in errors[0]["error"]
