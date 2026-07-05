"""Run a workflow in a disposable hardened container from the agent.

The agent cannot import ``app.*``; this is a deliberately small copy of the
platform's container hardening floor (see
apps/api/app/services/container_runtime.py::hardening_kwargs) plus the no-TTY
demuxer. Keep the two in sync when the security floor changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("nodyra_runner")

EventCallback = Callable[[dict], Awaitable[None]]


def sandbox_run_kwargs(*, cpu: float, memory_mb: int, pids: int) -> dict[str, Any]:
    return {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=256m"},
        "mem_limit": f"{int(memory_mb)}m",
        "nano_cpus": int(float(cpu) * 1_000_000_000),
        "pids_limit": int(pids),
        "network_mode": "none",
        "init": True,
        "environment": {"HOME": "/tmp"},
    }


def _client():
    import docker  # noqa: PLC0415
    return docker.from_env()


class _Demuxer:
    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> bytes:
        self._buf += chunk
        out = b""
        while len(self._buf) >= 8:
            size = int.from_bytes(self._buf[4:8], "big")
            if len(self._buf) < 8 + size:
                break
            out += self._buf[8:8 + size]
            self._buf = self._buf[8 + size:]
        return out


async def run_workflow_sandboxed(
    *,
    run_id: str,
    graph: dict,
    cache: dict | None,
    targets: list[str] | None,
    workflow_modules: list[dict],
    on_event: EventCallback,
    env_payload: dict,
    image_tag: str = "python:3.12-slim",
) -> str:
    """Execute a run in a hardened disposable container. Returns status."""
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        await on_event({"type": "run_error",
                        "error": f"sandbox requested but no Docker daemon: {exc}"})
        return "error"
    overrides = (env_payload or {}).get("sandbox") or {}
    kwargs = sandbox_run_kwargs(
        cpu=overrides.get("cpu", 1.0),
        memory_mb=overrides.get("memory_mb", 1024),
        pids=overrides.get("pids", 256),
    )
    return "error"
