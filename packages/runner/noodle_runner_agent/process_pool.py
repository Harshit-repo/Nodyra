"""Run a single workflow in a freshly-spawned ``noodle_runtime`` subprocess.

v1 spawns one short-lived process per run (simple, robust). The process runs
inside the env's venv so workflow imports resolve against that env's
site-packages. We drive the same newline-delimited JSON protocol the host's
``runtime_pool`` uses, forwarding every node event to ``on_event``.

Sub-workflow ``call_workflow`` events from the runtime are brokered back to the
API via the optional ``call_workflow`` handler (which round-trips over the
agent WS); the resolved result is written back into the subprocess stdin.

Cancellation: cancel the asyncio task awaiting this coroutine — the ``finally``
block terminates the subprocess.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

EventCallback = Callable[[dict], Awaitable[None]]
CallWorkflow = Callable[[str, Any], Awaitable[Any]]


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
    call_workflow: CallWorkflow | None = None,
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

    write_lock = asyncio.Lock()
    callbacks: set[asyncio.Task] = set()

    async def write_msg(msg: dict[str, Any]) -> None:
        async with write_lock:
            proc.stdin.write(json.dumps(msg).encode() + b"\n")
            await proc.stdin.drain()

    async def handle_call_workflow(event: dict[str, Any]) -> None:
        callback_id = event.get("callback_id", "")
        try:
            if call_workflow is None:
                raise RuntimeError("no sub-workflow broker on this runner")
            result = await call_workflow(
                event.get("workflow_id", ""), event.get("input")
            )
            await write_msg({
                "type": "call_workflow_response",
                "callback_id": callback_id,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001 - surface back to the runtime
            await write_msg({
                "type": "call_workflow_error",
                "callback_id": callback_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

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
        ready_line = await proc.stdout.readline()
        if not ready_line:
            raise RuntimeError("noodle_runtime did not emit a ready event")
        ready = json.loads(ready_line)
        if ready.get("type") != "ready":
            raise RuntimeError(f"unexpected first event: {ready}")

        await write_msg(run_msg)

        while True:
            line = await proc.stdout.readline()
            if not line:
                raise RuntimeError("noodle_runtime closed stdout before result")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Sub-workflow callback — broker it on a task so the read loop
            # keeps draining the pipe while the API resolves the sub.
            if event.get("type") == "call_workflow":
                task = asyncio.create_task(handle_call_workflow(event))
                callbacks.add(task)
                task.add_done_callback(callbacks.discard)
                continue

            etype = event.get("type")
            if etype == "result":
                if callbacks:
                    await asyncio.gather(*callbacks, return_exceptions=True)
                status = str(event.get("status", "success"))
                break
            if etype == "error":
                await on_event({"type": "run_error", "error": event.get("error", "")})
                status = "error"
                break
            clean = {k: v for k, v in event.items() if k != "request_id"}
            await on_event(clean)
    finally:
        for task in callbacks:
            task.cancel()
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
