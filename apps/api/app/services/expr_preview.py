"""Manages the isolated expression-preview subprocess (C1).

The worker (``noodle.expr_preview_worker``) is spawned with a from-scratch
minimal environment — secrets are absent by construction, not scrubbed — so a
sandbox escape in ``noodle.expr`` lands in a process that holds nothing. One
warm worker, requests serialized by a lock (preview traffic is light and each
eval is sub-millisecond); a timeout kills and respawns the worker.

State (process + lock) is held per event loop: asyncio transports and locks
are unusable from a different loop, so when the running loop changes (test
suites spin up a fresh loop per test) the stale worker is killed and a new
one is spawned lazily. In production there is exactly one loop, so this is a
no-op after startup.
"""

import asyncio
import contextlib
import json
import os
import sys
from typing import Any

_DEFAULT_TIMEOUT = 5.0


class _WorkerState:
    """Worker process + request lock, valid for exactly one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.lock = asyncio.Lock()
        self.proc: asyncio.subprocess.Process | None = None


_state: _WorkerState | None = None


def _current_state() -> _WorkerState:
    global _state
    loop = asyncio.get_running_loop()
    if _state is None or _state.loop is not loop:
        if _state is not None and _state.proc is not None:
            # The old loop is gone; transports can't be awaited. Signal-kill
            # the orphan so it doesn't linger.
            with contextlib.suppress(Exception):
                _state.proc.kill()
        _state = _WorkerState(loop)
    return _state


def _minimal_env() -> dict[str, str]:
    env: dict[str, str] = {}
    # Only what Python needs to start and import noodle; nothing app-specific
    # (SECRET_KEY, DATABASE_URL, REDIS_URL, cloud creds, ...) crosses over.
    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["PYTHONIOENCODING"] = "utf-8"
    return env


async def _ensure_worker(state: _WorkerState) -> asyncio.subprocess.Process:
    if state.proc is not None and state.proc.returncode is None:
        return state.proc
    state.proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-m", "noodle.expr_preview_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_minimal_env(),
    )
    return state.proc


async def _kill_worker(state: _WorkerState) -> None:
    if state.proc is not None and state.proc.returncode is None:
        state.proc.kill()
        await state.proc.wait()
    state.proc = None


async def shutdown() -> None:
    """Stop the worker (lifespan teardown / test cleanup)."""
    state = _current_state()
    async with state.lock:
        await _kill_worker(state)


async def _request(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    state = _current_state()
    async with state.lock:
        proc = await _ensure_worker(state)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(payload, default=str).encode() + b"\n")
        await proc.stdin.drain()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        except TimeoutError:
            await _kill_worker(state)
            return {
                "result": None,
                "error": "Expression timeout — evaluation killed",
                "parts": [],
            }
        if not line:  # worker died mid-request
            await _kill_worker(state)
            return {
                "result": None,
                "error": "Preview worker exited unexpectedly",
                "parts": [],
            }
        return json.loads(line)


async def preview(
    *,
    value: str,
    json_value: Any,
    inputs: dict,
    nodes: dict,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    return await _request(
        {"op": "eval", "value": value, "json": json_value, "inputs": inputs, "nodes": nodes},
        timeout,
    )


async def probe_env(key: str) -> str | None:
    """Diagnostics/test hook: read an env var from inside the worker process."""
    resp = await _request({"op": "probe_env", "key": key}, _DEFAULT_TIMEOUT)
    return resp.get("value")
