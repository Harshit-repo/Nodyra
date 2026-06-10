"""Manages the isolated expression-preview subprocess (C1).

The worker (``noodle.expr_preview_worker``) is spawned with a from-scratch
minimal environment — secrets are absent by construction, not scrubbed — so a
sandbox escape in ``noodle.expr`` lands in a process that holds nothing. One
warm worker, requests serialized by a lock (preview traffic is light and each
eval is sub-millisecond); a timeout kills and respawns the worker.
"""

import asyncio
import json
import os
import sys
from typing import Any

_DEFAULT_TIMEOUT = 5.0

_proc: asyncio.subprocess.Process | None = None
_lock = asyncio.Lock()


def _minimal_env() -> dict[str, str]:
    env: dict[str, str] = {}
    # Only what Python needs to start and import noodle; nothing app-specific
    # (SECRET_KEY, DATABASE_URL, REDIS_URL, cloud creds, ...) crosses over.
    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["PYTHONIOENCODING"] = "utf-8"
    return env


async def _ensure_worker() -> asyncio.subprocess.Process:
    global _proc
    if _proc is not None and _proc.returncode is None:
        return _proc
    _proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-m", "noodle.expr_preview_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_minimal_env(),
    )
    return _proc


async def _kill_worker() -> None:
    global _proc
    if _proc is not None and _proc.returncode is None:
        _proc.kill()
        await _proc.wait()
    _proc = None


async def shutdown() -> None:
    """Stop the worker (lifespan teardown / test cleanup)."""
    async with _lock:
        await _kill_worker()


async def _request(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    async with _lock:
        proc = await _ensure_worker()
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(payload, default=str).encode() + b"\n")
        await proc.stdin.drain()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        except TimeoutError:
            await _kill_worker()
            return {
                "result": None,
                "error": "Expression timeout — evaluation killed",
                "parts": [],
            }
        if not line:  # worker died mid-request
            await _kill_worker()
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
