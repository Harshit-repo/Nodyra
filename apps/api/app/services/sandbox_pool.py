"""Warm per-(org, environment) sandbox container pool (MT Phase D slice 1).

SandboxWorker wraps one hardened container running ``noodle_runtime`` plus
its attach socket. SandboxPool hands a worker to at most one run at a time
and returns clean workers to a bounded warm list keyed by
``(org_id, environment_id)`` — reuse is strictly within one key, so
cross-tenant container reuse is impossible by construction.

The wire protocol is the same newline-framed JSON ``noodle_runtime`` speaks
to the subprocess pool (see packages/runtime/noodle_runtime/server.py),
including the ``call_workflow`` host callback that runtime_pool brokers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app.config import settings
from app.services.container_runtime import (
    ensure_docker_image,
    hardening_kwargs,
    image_tag_for,
)
from noodle.serialization import deserialize_value, serialize_value

logger = logging.getLogger(__name__)

# Events forwarded verbatim to the host's on_event (same set as the docker
# runner-pool provider).
_FORWARDED_EVENTS = frozenset({
    "node_started", "node_finished", "agent_action_requested",
    "agent_tool_started", "agent_tool_approval_required",
    "agent_tool_auto_approved", "agent_tool_finished",
    "agent_action_completed", "agent_tool_approval_decided",
    "run_error", "run_cancelled", "module_error",
})


class SandboxWorker:
    """One container + attach socket; drives one run at a time."""

    def __init__(self, client: Any, container: Any, sock: Any, *,
                 key: tuple[str | None, str | None], image_tag: str) -> None:
        self.client = client
        self.container = container
        self._sock = sock
        self.key = key
        self.image_tag = image_tag
        self.runs_completed = 0
        self.idle_since = time.monotonic()
        self.dead = False
        self._buf = b""
        self._write_lock = asyncio.Lock()

    @classmethod
    async def spawn(cls, client: Any, *, key: tuple[str | None, str | None],
                    env_payload: dict, runtime: str, network: str) -> "SandboxWorker":
        loop = asyncio.get_running_loop()
        tag = image_tag_for(env_payload)
        await loop.run_in_executor(None, ensure_docker_image, client, tag, env_payload)
        name = f"noodle-sbx-{uuid.uuid4().hex[:12]}"
        spawn_kwargs = hardening_kwargs(runtime=runtime, network=network)
        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                tag, detach=True, stdin_open=True, remove=False,
                name=name, **spawn_kwargs,
            ),
        )
        worker: SandboxWorker | None = None
        try:
            sock = await loop.run_in_executor(None, lambda: container.attach_socket(
                params={"stdin": True, "stdout": True, "stderr": False, "stream": True}
            ))
            worker = cls(client, container, sock, key=key, image_tag=tag)
            await worker._await_ready(loop)
            return worker
        except BaseException:
            if worker is not None:
                await worker.close()
            else:
                try:
                    await loop.run_in_executor(
                        None, lambda: container.remove(force=True)
                    )
                except Exception:  # noqa: BLE001
                    pass
            raise

    async def _await_ready(self, loop: asyncio.AbstractEventLoop) -> None:
        await loop.run_in_executor(
            None, self._sock._sock.settimeout, settings.sandbox_ready_timeout_seconds
        )
        try:
            event = await self._read_event(loop)
        except RuntimeError as exc:
            raise RuntimeError(
                f"sandbox container {self.container.name} did not become "
                f"ready within {settings.sandbox_ready_timeout_seconds}s: {exc}"
            ) from exc
        if event.get("type") != "ready":
            raise RuntimeError(
                f"sandbox container {self.container.name} sent "
                f"{event.get('type')!r} before ready"
            )

    async def _read_event(self, loop: asyncio.AbstractEventLoop) -> dict:
        """Next JSON event from the attach socket (skips undecodable lines)."""
        while True:
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue  # node stdout noise on the protocol stream
            try:
                chunk = await loop.run_in_executor(None, self._sock._sock.recv, 4096)
            except Exception as exc:  # noqa: BLE001 — socket.timeout et al.
                self.dead = True
                raise RuntimeError(f"sandbox socket read failed: {exc}") from exc
            if not chunk:
                self.dead = True
                raise RuntimeError("sandbox container closed its output stream")
            self._buf += chunk

    async def _send(self, message: dict, loop: asyncio.AbstractEventLoop) -> None:
        payload = json.dumps(serialize_value(message)).encode() + b"\n"
        async with self._write_lock:
            try:
                await loop.run_in_executor(None, self._sock._sock.sendall, payload)
            except Exception as exc:  # noqa: BLE001 — broken pipe = dead container
                self.dead = True
                raise RuntimeError(f"sandbox socket write failed: {exc}") from exc

    async def close(self) -> None:
        """Force-remove the container. Idempotent; never raises."""
        self.dead = True
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: self.container.remove(force=True))
        except Exception:  # noqa: BLE001 — already gone
            pass
