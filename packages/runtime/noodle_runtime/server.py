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
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, workflow_caller
from noodle.engine import execute
from noodle.models import WorkflowGraph
from noodle.sdk import register_module_functions, registry, unregister_module
from noodle.serialization import deserialize_value, serialize_value

_pending_callbacks: dict[str, asyncio.Future] = {}
_RUNTIME_DEFAULT_TIMEOUTS = {"http_request": 45.0}


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(serialize_value(event)))
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
    run_id = str(request.get("run_id") or request_id)

    async def on_event(event: dict) -> None:
        _emit({"request_id": request_id, **event})

    # Register any per-run user code modules into the local registry. The
    # ids are namespaced as ``user:<module_id>:<func>`` so they can't collide
    # with built-ins, and we drop them again in the ``finally`` block so a
    # warm process doesn't leak state across workflows.
    workflow_modules = request.get("workflow_modules") or []
    loaded_module_ids: list[str] = []
    for module in workflow_modules:
        module_id = str(module.get("id") or "")
        source = str(module.get("contents") or "")
        if not module_id or not source.strip():
            continue
        try:
            register_module_functions(module_id, source, registry)
            loaded_module_ids.append(module_id)
        except Exception as exc:  # noqa: BLE001 - bad user code shouldn't crash the runner
            _emit(
                {
                    "request_id": request_id,
                    "type": "module_error",
                    "module_id": module_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    caller_token = workflow_caller.set(_call_workflow_via_host)
    artifact_token = None
    artifacts_dir = request.get("artifacts_dir")
    if artifacts_dir:
        artifact_token = artifact_store.set(
            LocalArtifactStore(
                str(artifacts_dir),
                run_id,
                max_bytes=int(request.get("max_artifact_bytes") or 0),
                max_count=int(request.get("max_artifacts_per_run") or 0),
            )
        )
    try:
        graph = WorkflowGraph.model_validate(request["graph"])
        result = await execute(
            graph,
            registry,
            cache=deserialize_value(request.get("cache")) or None,
            targets=request.get("targets") or None,
            on_event=on_event,
            default_timeouts=_RUNTIME_DEFAULT_TIMEOUTS,
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
        if artifact_token is not None:
            artifact_store.reset(artifact_token)
        workflow_caller.reset(caller_token)
        for module_id in loaded_module_ids:
            unregister_module(module_id, registry)


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
        future.set_result(deserialize_value(message.get("result")))
    return True


def _needs_host_callbacks(message: dict[str, Any]) -> bool:
    graph = message.get("graph") or {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, dict) and node.get("type") == "execute_workflow"
        for node in nodes
    )


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

        if message.get("type") != "run":
            _emit({"type": "error", "error": "unknown message type"})
            continue

        # Most runs never call back to the host. Run those inline so the
        # process does not keep a background stdin reader thread alive while
        # user code imports heavy packages such as numpy/pandas on Windows.
        if _needs_host_callbacks(message):
            asyncio.create_task(_handle_run(message))
        else:
            await _handle_run(message)


def main() -> None:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
