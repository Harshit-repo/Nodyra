"""Run a single workflow in a freshly-spawned ``noodle_runtime`` subprocess.

v1 spawns one short-lived process per run (simple, robust). The process runs
inside the env's venv so workflow imports resolve against that env's
site-packages. We drive the same newline-delimited JSON protocol the host's
``runtime_pool`` uses, forwarding every node event to ``on_event``.

This v1 does not broker sub-workflow ``call_workflow`` callbacks — a remote
run with ``execute_workflow`` nodes returns an error event for that node
rather than hanging. Sub-workflow brokering over the remote WS is a
follow-up.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

EventCallback = Callable[[dict], Awaitable[None]]


async def run_workflow_subprocess(
    python: Path,
    run_id: str,
    graph: dict,
    cache: dict | None,
    targets: list[str] | None,
    workflow_modules: list[dict],
    on_event: EventCallback,
    artifacts_upload_url: str | None = None,
    artifacts_runner_token: str | None = None,
) -> str:
    """Spawn ``noodle_runtime``, run the workflow, return the status string."""
    proc = await asyncio.create_subprocess_exec(
        str(python),
        "-u",
        "-m",
        "noodle_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("noodle_runtime subprocess pipes were not opened")

    run_msg: dict[str, Any] = {
        "type": "run",
        "request_id": run_id,
        "run_id": run_id,
        "graph": graph,
        "cache": cache or None,
        "targets": targets or None,
        "workflow_modules": workflow_modules,
    }
    if artifacts_upload_url:
        run_msg["artifacts_upload_url"] = artifacts_upload_url
        run_msg["artifacts_runner_token"] = artifacts_runner_token or ""

    status = "error"
    try:
        # Wait for the runtime's ready event before dispatching.
        ready_line = await proc.stdout.readline()
        if not ready_line:
            raise RuntimeError("noodle_runtime did not emit a ready event")
        ready = json.loads(ready_line)
        if ready.get("type") != "ready":
            raise RuntimeError(f"unexpected first event: {ready}")

        proc.stdin.write(json.dumps(run_msg).encode() + b"\n")
        await proc.stdin.drain()

        while True:
            line = await proc.stdout.readline()
            if not line:
                raise RuntimeError("noodle_runtime closed stdout before result")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "result":
                status = str(event.get("status", "success"))
                break
            if etype == "error":
                await on_event({"type": "run_error", "error": event.get("error", "")})
                status = "error"
                break
            # Forward node_started / node_finished / module_error to the host.
            clean = {k: v for k, v in event.items() if k != "request_id"}
            await on_event(clean)
    finally:
        try:
            if proc.returncode is None:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    return status
