"""Long-lived env-runner process. One per environment.

Reads newline-delimited JSON messages from stdin, runs each through the
Nodyra engine, and writes newline-delimited JSON events to stdout. stderr is
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
        # env — avoids a subprocess spawn + round-trip. The ENGINE executes
        # the directive (with correct depth/chain meta), so nested
        # workflow calls inside inline children are allowed.
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

import nodyra_nodes  # noqa: F401 - importing registers the built-in nodes
from nodyra.ai_runtime import AgentActionRequest
from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, org_run_limits
from nodyra.engine import execute
from nodyra.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
)
from nodyra.models import WorkflowGraph
from nodyra.process_isolation import PooledProcessIsolator
from nodyra.sdk import register_module_functions, registry, unregister_module
from nodyra.serialization import deserialize_value, serialize_value

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

    The host sets ``NODYRA_CODE_NODE_TIMEOUT_SECONDS`` when an operator wants a
    default cap on ``code`` nodes. Unset / ``<= 0`` means code is uncapped so
    long-running Python isn't cancelled mid-flight (bounded only by the
    overall workflow timeout enforced host-side).
    """
    timeouts: dict[str, float] = {"http_request": 45.0}
    raw = os.environ.get("NODYRA_CODE_NODE_TIMEOUT_SECONDS")
    if raw:
        try:
            code_timeout = float(raw)
        except ValueError:
            code_timeout = 0.0
        if code_timeout > 0:
            timeouts["code"] = code_timeout
    return timeouts


_RUNTIME_DEFAULT_TIMEOUTS = _runtime_default_timeouts()

# This warm runner process is already per-environment; one isolator with the
# default (None) pool key is correct.
_PROCESS_ISOLATOR = PooledProcessIsolator()


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


async def _run_subworkflow_via_host(call: SubworkflowCall) -> Any:
    """``SubworkflowRunner`` that round-trips through the host.

    The host answers with either a concrete result or an inline directive;
    directives are returned as ``InlineSubworkflow`` for the ENGINE adapter
    to execute (the engine owns inline semantics now — the old
    ``_resolve_inline`` duplication is gone).
    """
    callback_id = uuid.uuid4().hex
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_callbacks[callback_id] = future
    _emit({"type": "call_workflow", "callback_id": callback_id, **call.to_payload()})
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

    # Per-org amplification caps (multi-tenancy C5); empty = uncapped.
    raw_limits = request.get("org_limits")
    limits_token = org_run_limits.set(
        raw_limits if isinstance(raw_limits, dict) else {}
    )
    artifact_token = None
    artifacts_upload_url = request.get("artifacts_upload_url")
    artifacts_dir = request.get("artifacts_dir")
    artifact_key_prefix = str(request.get("artifact_key_prefix") or "")
    if artifacts_upload_url:
        from nodyra_runtime.remote_artifacts import RemoteArtifactStore

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
        sub_meta = SubworkflowMeta.from_payload(
            request.get("subworkflow_meta") or {}
        )
        result = await execute(
            graph,
            registry,
            cache=deserialize_value(request.get("cache")) or None,
            targets=request.get("targets") or None,
            on_event=on_event,
            default_timeouts=_RUNTIME_DEFAULT_TIMEOUTS,
            pause_on_approval=bool(request.get("pause_on_approval")),
            agent_action_resume=agent_action_resume,
            process_isolator=_PROCESS_ISOLATOR,
            subworkflow_runner=_run_subworkflow_via_host,
            subworkflow_meta=sub_meta,
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
        org_run_limits.reset(limits_token)
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
        # Inline directive — the ENGINE adapter executes it with correct
        # depth/chain meta (nodyra.engine.subworkflows.make_workflow_caller).
        future.set_result(
            InlineSubworkflow(
                graph=message["inline_graph"],
                cache=deserialize_value(message.get("inline_cache")) or None,
                targets=message.get("inline_targets") or None,
                sources=tuple(message.get("inline_sources") or ()),
            )
        )
    else:
        future.set_result(deserialize_value(message.get("result")))
    return True


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
