"""Long-lived env-runner process. One per environment.

Reads newline-delimited JSON messages from stdin, runs each through the
Noodle engine, and writes newline-delimited JSON events to stdout. stderr is
reserved for free-form logs and is not parsed by the host.

Protocol — every streamed event includes the originating ``request_id``:

    Request from the host:
        {"type": "run", "request_id": "abc", "graph": {...},
         "cache": {...} | null, "targets": [...] | null}

    Streamed during the run:
        {"type": "node_started",  "node_id": "..."}
        {"type": "node_finished", "node_id": "...", "status": "...",
         "outputs": {...}, "error": null}

    Sub-workflow callback (subprocess → host → subprocess):
        # subprocess asks the host to run another workflow
        {"type": "call_workflow", "callback_id": "x", "request_id": "abc",
         "workflow_id": "...", "input": ...}
        # host replies via stdin
        {"type": "call_workflow_response", "callback_id": "x", "result": ...}
        {"type": "call_workflow_error",    "callback_id": "x", "error": "..."}

    Terminal:
        {"type": "result", "status": "success" | "error"}
        {"type": "error",  "error": "..."}      (on unrecoverable failure)

The very first line every process writes is ``{"type": "ready"}`` — the host
must wait for it before dispatching requests.
"""

import asyncio
import json
import sys
import uuid
from typing import Any

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from noodle.context import workflow_caller
from noodle.engine import execute
from noodle.models import WorkflowGraph
from noodle.sdk import registry

_pending_callbacks: dict[str, asyncio.Future] = {}


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


async def _read_line() -> str | None:
    """Read a line from stdin without blocking the event loop (cross-platform)."""
    loop = asyncio.get_event_loop()
    line = await loop.run_in_executor(None, sys.stdin.readline)
    if not line:
        return None
    return line.rstrip("\n")


async def _call_workflow_via_host(workflow_id: str, input_value: Any) -> Any:
    """``workflow_caller`` implementation that round-trips through the host."""
    callback_id = uuid.uuid4().hex
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_callbacks[callback_id] = future
    _emit(
        {
            "type": "call_workflow",
            "callback_id": callback_id,
            "workflow_id": workflow_id,
            "input": input_value,
        }
    )
    try:
        return await future
    finally:
        _pending_callbacks.pop(callback_id, None)


async def _handle_run(request: dict[str, Any]) -> None:
    request_id = request.get("request_id", "")

    async def on_event(event: dict) -> None:
        _emit({"request_id": request_id, **event})

    caller_token = workflow_caller.set(_call_workflow_via_host)
    try:
        graph = WorkflowGraph.model_validate(request["graph"])
        result = await execute(
            graph,
            registry,
            cache=request.get("cache") or None,
            targets=request.get("targets") or None,
            on_event=on_event,
        )
        _emit(
            {
                "request_id": request_id,
                "type": "result",
                "status": str(result.status),
            }
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure
        _emit(
            {
                "request_id": request_id,
                "type": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    finally:
        workflow_caller.reset(caller_token)


def _resolve_callback(message: dict[str, Any]) -> bool:
    """If ``message`` is a callback response, dispatch it. Return True if handled."""
    callback_id = message.get("callback_id")
    if not callback_id:
        return False
    future = _pending_callbacks.pop(callback_id, None)
    if future is None or future.done():
        return True
    if message.get("type") == "call_workflow_error":
        future.set_exception(
            RuntimeError(message.get("error", "remote call_workflow error"))
        )
    else:
        future.set_result(message.get("result"))
    return True


async def run_forever() -> None:
    _emit({"type": "ready"})
    while True:
        line = await _read_line()
        if line is None:
            return
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({"type": "error", "error": f"invalid JSON: {exc}"})
            continue

        if _resolve_callback(message):
            continue

        # Anything else is a new run request. Spawn a task so the read loop
        # keeps running — that lets sub-workflow callback responses from the
        # host come back while the run is suspended inside ``execute_workflow``.
        asyncio.create_task(_handle_run(message))


def main() -> None:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
