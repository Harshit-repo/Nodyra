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
