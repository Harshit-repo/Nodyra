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
        # host replies via stdin — three response shapes:
        {"type": "call_workflow_response", "callback_id": "x", "result": ...}
        {"type": "call_workflow_error",    "callback_id": "x", "error": "..."}
        # OR: "run this sub graph yourself in-process, return its leaf as
        # the result". Sent by the host when the sub shares the parent's
        # env and contains no nested execute_workflow nodes — avoids a
        # subprocess spawn + round-trip.
        {"type": "call_workflow_response", "callback_id": "x",
         "inline_graph": {...}, "inline_cache": {...} | null,
         "inline_targets": [...] | null, "inline_sources": [...]}

    Terminal:
        {"type": "result", "status": "success" | "error"}
        {"type": "error",  "error": "..."}      (on unrecoverable failure)

The very first line every process writes is ``{"type": "ready"}`` — the host
must wait for it before dispatching requests.
"""

import asyncio
import json
import os
import sys
import uuid
from typing import Any

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from noodle.ai_runtime import AgentActionRequest
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, workflow_caller
from noodle.engine import execute
from noodle.models import WorkflowGraph
from noodle.sdk import register_module_functions, registry, unregister_module
from noodle.serialization import deserialize_value, serialize_value

# Capture the REAL stdout BEFORE the engine ever installs its per-node
# capture proxy on ``sys.stdout`` (Slice 1, for log collection). Writing
# to the proxy from inside a running node sends the bytes into the node's
# log buffer instead of out to the host. ``_emit`` needs the protocol
# stream to always reach the host — including for the ``call_workflow``
# callback, which fires *inside* an executing node — so we hold a
# reference to the original stream that bypasses the proxy.
_PROTOCOL_OUT = sys.stdout

_pending_callbacks: dict[str, asyncio.Future] = {}


def _runtime_default_timeouts() -> dict[str, float]:
    """Per-node default timeouts for the runtime engine.

    The host sets ``NOODLE_CODE_NODE_TIMEOUT_SECONDS`` when an operator wants a
    default cap on ``code`` nodes. Unset / ``<= 0`` means code is uncapped so
    long-running Python isn't cancelled mid-flight (bounded only by the
    overall workflow timeout enforced host-side).
    """
    timeouts: dict[str, float] = {"http_request": 45.0}
    raw = os.environ.get("NOODLE_CODE_NODE_TIMEOUT_SECONDS")
    if raw:
        try:
            code_timeout = float(raw)
        except ValueError:
            code_timeout = 0.0
        if code_timeout > 0:
            timeouts["code"] = code_timeout
    return timeouts


_RUNTIME_DEFAULT_TIMEOUTS = _runtime_default_timeouts()


def _emit(event: dict) -> None:
    _PROTOCOL_OUT.write(json.dumps(serialize_value(event)))
    _PROTOCOL_OUT.write("\n")
    _PROTOCOL_OUT.flush()


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
            register_module_functions(
                module_id,
                source,
                registry,
                include_undecorated=bool(module.get("include_undecorated")),
            )
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
    artifacts_upload_url = request.get("artifacts_upload_url")
    artifacts_dir = request.get("artifacts_dir")
    artifact_key_prefix = str(request.get("artifact_key_prefix") or "")
    if artifacts_upload_url:
        from noodle_runtime.remote_artifacts import RemoteArtifactStore

        artifact_token = artifact_store.set(
            RemoteArtifactStore(
                str(artifacts_upload_url),
                str(request.get("artifacts_runner_token") or ""),
                run_id,
                max_bytes=int(request.get("max_artifact_bytes") or 0),
                max_count=int(request.get("max_artifacts_per_run") or 0),
                key_prefix=artifact_key_prefix,
            )
        )
    elif artifacts_dir:
        artifact_token = artifact_store.set(
            LocalArtifactStore(
                str(artifacts_dir),
                run_id,
                max_bytes=int(request.get("max_artifact_bytes") or 0),
                max_count=int(request.get("max_artifacts_per_run") or 0),
                key_prefix=artifact_key_prefix,
            )
        )
    try:
        graph = WorkflowGraph.model_validate(request["graph"])
        raw_agent_resume = request.get("agent_action_resume") or {}
        agent_action_resume = {
            str(node_id): AgentActionRequest.model_validate(action_request)
            for node_id, action_request in raw_agent_resume.items()
            if isinstance(action_request, dict)
        } if isinstance(raw_agent_resume, dict) else {}
        result = await execute(
            graph,
            registry,
            cache=deserialize_value(request.get("cache")) or None,
            targets=request.get("targets") or None,
            on_event=on_event,
            default_timeouts=_RUNTIME_DEFAULT_TIMEOUTS,
            pause_on_approval=bool(request.get("pause_on_approval")),
            agent_action_resume=agent_action_resume,
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
    elif "inline_graph" in message:
        # Inline path — host wants us to run this sub graph ourselves.
        # Spawn a task so the stdin reader loop keeps draining; the task
        # completes the future when the inline run finishes.
        asyncio.create_task(_resolve_inline(future, message))
    else:
        future.set_result(deserialize_value(message.get("result")))
    return True


async def _resolve_inline(
    future: asyncio.Future, message: dict[str, Any]
) -> None:
    """Run an inlined sub-workflow graph in this process and complete the
    awaiting ``execute_workflow`` callback with its leaf result.

    Uses the same engine + ``workflow_caller`` set up for top-level runs,
    so a nested ``execute_workflow`` node inside the inline graph still
    round-trips through the host (which then decides spawn vs nested
    inline based on its own rules — the host restricts inline to subs
    with no nested calls, so practically this won't recurse).
    """
    try:
        graph = WorkflowGraph.model_validate(message["inline_graph"])
        cache = deserialize_value(message.get("inline_cache")) or None
        targets = message.get("inline_targets") or None
        sources: set[str] = set(message.get("inline_sources") or [])
        result = await execute(
            graph,
            registry,
            cache=cache,
            targets=targets,
            default_timeouts=_RUNTIME_DEFAULT_TIMEOUTS,
        )
        leaves = [
            nid
            for nid, run in result.nodes.items()
            if str(run.status) == "success" and nid not in sources
        ]
        if len(leaves) == 1:
            value: Any = result.nodes[leaves[0]].outputs.get("main")
        elif leaves:
            value = {
                nid: result.nodes[nid].outputs.get("main") for nid in leaves
            }
        else:
            value = None
        if not future.done():
            future.set_result(value)
    except Exception as exc:  # noqa: BLE001 - surface to the awaiter
        if not future.done():
            future.set_exception(exc)


# Node types that call ``workflow_caller`` and therefore need the host-side
# stdin reader loop to stay alive so ``call_workflow_response`` messages can
# be dispatched. If a new node type uses ``workflow_caller``, add it here.
_HOST_CALLBACK_NODE_TYPES: frozenset[str] = frozenset({
    "execute_workflow",
    "map_items",
    "map_group",
    "map_dataset",
})


def _needs_host_callbacks(message: dict[str, Any]) -> bool:
    graph = message.get("graph") or {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, dict) and node.get("type") in _HOST_CALLBACK_NODE_TYPES
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
