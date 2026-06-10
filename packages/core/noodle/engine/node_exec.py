"""Single-node execution: input wiring, expression eval, retries, timeouts,
process isolation for code nodes, log capture, and output normalization."""

import asyncio
import contextvars
import json
import random
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from noodle.ai_runtime import AgentActionRequest, AgentApprovalRequired
from noodle.context import current_node_id, node_debug
from noodle.expr import build_context, evaluate
from noodle.models import NodeRunResult, NodeStatus, RunStatus
from noodle.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
from noodle.process_isolation import ProcessIsolator, default_isolator
from noodle.sdk import NodeRegistry

from noodle.engine.agent import (
    _MAX_AGENT_LOOP_ITERATIONS,
    _dispatch_agent_action_request,
)
from noodle.engine.datasets import (
    AUTO_PROMOTE_NODE_TYPES,
    _auto_expand_dataset_inputs,
    _auto_promote_outputs,
)
from noodle.engine.types import EventCallback
from noodle.engine.validation import _validate_input_kinds, _validate_output_kinds


PROCESS_ISOLATED_NODE_TYPES: frozenset[str] = frozenset({"code"})


class _LengthCountingSink:
    """A minimal write-only sink that counts characters instead of buffering
    them. Used to measure ``json.dump`` output size without materializing the
    whole encoded string in memory — important for multi-MB node outputs that
    are checked against ``max_node_output_bytes`` on every run."""

    __slots__ = ("length",)

    def __init__(self) -> None:
        self.length = 0

    def write(self, chunk: str) -> None:
        self.length += len(chunk)


def _approx_encoded_length(value: Any) -> int:
    """Character length of ``value`` encoded as JSON, computed by streaming
    into a counting sink. Equivalent to ``len(json.dumps(value, default=str))``
    but without holding the full string."""
    sink = _LengthCountingSink()
    json.dump(value, sink, default=str)
    return sink.length


def _encoded_upper_bound(value: Any, depth: int = 2) -> int | None:
    """Cheap, conservative upper bound on ``value``'s JSON-encoded length,
    or None when no cheap bound exists (large/unknown shapes must be measured
    for real). Never underestimates: a str char encodes to at most 6 chars
    (``\\uXXXX``) plus the surrounding quotes."""
    if value is None or isinstance(value, bool):
        return 5
    if isinstance(value, int):
        return 25 if -(10**18) < value < 10**18 else None
    if isinstance(value, float):
        return 32
    if isinstance(value, str):
        return 6 * len(value) + 2
    if depth <= 0:
        return None
    if isinstance(value, (list, tuple)) and len(value) <= 64:
        total = 2
        for item in value:
            bound = _encoded_upper_bound(item, depth - 1)
            if bound is None:
                return None
            total += bound + 1
        return total
    if isinstance(value, dict) and len(value) <= 64:
        total = 2
        for key, item in value.items():
            if not isinstance(key, str):
                return None
            bound = _encoded_upper_bound(item, depth - 1)
            if bound is None:
                return None
            total += 6 * len(key) + 3 + bound + 1
        return total
    return None


# Per-node-type default timeouts (seconds). ``code`` is intentionally
# *absent* so heavy/long-running Python isn't capped by an arbitrary default;
# it's bounded only by the overall workflow timeout. Callers (the API runner /
# runtime server) can inject a code default via the ``default_timeouts`` arg,
# and any node may still set its own ``timeout_seconds``.
DEFAULT_NODE_TIMEOUTS: dict[str, float] = {
    "http_request": 45.0,
    "ai_agent_v2": 300.0,
}

# Per-node log capture. Writes to stdout/stderr are diverted into this
# context-local buffer *only* while a node is executing, so capture is safe
# under concurrent runs / sub-workflows sharing one process — unlike a global
# ``redirect_stdout`` which would cross-capture other tasks' output.
_log_capture: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "noodle_log_capture", default=None
)


class _CaptureProxy:
    """stdout/stderr proxy: divert into the active buffer, else pass through."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def write(self, s: str) -> int:
        buf = _log_capture.get()
        if buf is not None:
            buf.append(s)
            return len(s)
        return self._wrapped.write(s)

    def flush(self) -> None:
        try:
            self._wrapped.flush()
        except Exception:  # noqa: BLE001 - flushing must never raise into the engine
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _install_capture() -> None:
    """Install the capture proxies once. Idempotent and process-wide-safe:
    when no buffer is active for the current task, writes pass straight through.
    """
    if not isinstance(sys.stdout, _CaptureProxy):
        sys.stdout = _CaptureProxy(sys.stdout)
    if not isinstance(sys.stderr, _CaptureProxy):
        sys.stderr = _CaptureProxy(sys.stderr)


def _normalize_outputs(raw: Any, output_names: list[str], node_id: str) -> dict[str, Any]:
    if len(output_names) == 1:
        return {output_names[0]: raw}
    if not isinstance(raw, dict):
        raise ValueError(
            f"node '{node_id}' declares multiple outputs and must return a dict"
        )
    return {name: raw[name] for name in output_names if name in raw}


def _node_timeout(
    node_type: str,
    timeout: float | None,
    default_timeouts: dict[str, float],
) -> float | None:
    if timeout is not None:
        return timeout
    return default_timeouts.get(node_type)


async def _run_one_node(
    *,
    nid: str,
    nodes_by_id: dict[str, Any],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    registry: NodeRegistry,
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
    process_isolator: ProcessIsolator | None = None,
) -> RunStatus:
    """Execute a single node against ``node_outputs`` and return the worst
    RunStatus it produced. Extracted verbatim from the old ``execute()`` inner
    closure so the loop driver can reuse the exact same per-node logic."""
    run_status = RunStatus.success
    graph_node = nodes_by_id[nid]

    if nid in cache:
        node_outputs[nid] = dict(cache[nid])
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=node_outputs[nid]
            )
        )
        return run_status

    edges_in = incoming.get(nid, {})

    skip_reason = None
    for source, source_output in edges_in.values():
        if source not in node_outputs:
            skip_reason = f"upstream node '{source}' produced no output"
            break
        if source_output not in node_outputs[source]:
            skip_reason = f"branch '{source_output}' of node '{source}' was not taken"
            break
    if skip_reason is not None:
        await finish(
            NodeRunResult(node_id=nid, status=NodeStatus.skipped, error=skip_reason)
        )
        return run_status

    await emit({"type": "node_started", "node_id": nid})
    started = time.time()

    try:
        node_def = registry.get(graph_node.type)
    except KeyError as exc:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=str(exc),
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    output_names = (
        graph_node.outputs_override
        or [o.name for o in node_def.manifest.outputs]
        or ["main"]
    )

    if graph_node.disabled:
        passthrough: Any = None
        for port in node_def.manifest.inputs:
            if port.name in edges_in:
                source, source_output = edges_in[port.name]
                passthrough = node_outputs[source][source_output]
                break
        outputs = {output_names[0]: passthrough}
        node_outputs[nid] = outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=outputs,
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    if getattr(graph_node, "tool_mode", False):
        # Tool-mode node: don't run in the data flow. Emit a ToolAdapter on
        # the `tool` output so it flows into the AI Agent's tool port; the
        # node's function runs deferred when the agent invokes the tool.
        try:
            adapter = build_node_tool_adapter(node_def, graph_node)
        except Exception as exc:  # noqa: BLE001 - surface a clean node error
            run_status = RunStatus.error
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.error,
                    error=f"{type(exc).__name__}: {exc}",
                    started_at=started, finished_at=time.time(),
                )
            )
            return run_status
        tool_outputs = {TOOL_MODE_OUTPUT: adapter}
        node_outputs[nid] = tool_outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=tool_outputs,
                started_at=started, finished_at=time.time(),
                node_type_version=node_def.manifest.version,
            )
        )
        return run_status

    kwargs: dict[str, Any] = {}
    for port in node_def.manifest.inputs:
        if port.name in edges_in:
            source, source_output = edges_in[port.name]
            kwargs[port.name] = node_outputs[source][source_output]

    try:
        _auto_expand_dataset_inputs(node_def, kwargs, graph_node.type)
        _validate_input_kinds(node_def, kwargs, nid)
    except (ValueError, RuntimeError) as exc:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=str(exc),
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    missing: list[str] = []
    for spec in node_def.manifest.params:
        if spec.name in kwargs:
            continue
        if spec.name in graph_node.params:
            kwargs[spec.name] = graph_node.params[spec.name]
        elif spec.required:
            missing.append(spec.name)

    if missing:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error,
                error=f"missing required parameters: {', '.join(missing)}",
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    first_input_name = (
        node_def.manifest.inputs[0].name
        if node_def.manifest.inputs
        else None
    )
    expr_context = build_context(
        first_input=kwargs.get(first_input_name) if first_input_name else None,
        inputs={
            p.name: kwargs.get(p.name)
            for p in node_def.manifest.inputs
            if p.name in kwargs
        },
        node_outputs=node_outputs,
    )
    for spec in node_def.manifest.params:
        if spec.name in kwargs:
            kwargs[spec.name] = evaluate(kwargs[spec.name], expr_context)

    timeout = _node_timeout(
        graph_node.type, graph_node.timeout_seconds, default_timeouts
    )
    attempts = (
        max(1, graph_node.retries + 1) if graph_node.retry_on_fail else 1
    )
    caught: Exception | None = None
    outputs: dict[str, Any] | None = None
    debug: dict[str, Any] = {}
    log_buf: list[str] = []
    log_token = _log_capture.set(log_buf)
    debug_token = node_debug.set(debug)
    node_token = current_node_id.set(nid)
    if node_def.accepts_var_keyword or not node_def.param_names:
        base_call_kwargs = dict(kwargs)
    else:
        base_call_kwargs = {
            k: v for k, v in kwargs.items() if k in node_def.param_names
        }

    async def invoke_node(current_kwargs: dict[str, Any]) -> Any:
        if node_def.is_async:
            if timeout is not None:
                return await asyncio.wait_for(
                    node_def.func(**current_kwargs), timeout
                )
            return await node_def.func(**current_kwargs)
        if graph_node.type in PROCESS_ISOLATED_NODE_TYPES:
            # Timeout-evict and broken-pool translation live inside the
            # isolator; TimeoutError/ValueError surface here unchanged.
            isolator = (
                process_isolator if process_isolator is not None
                else default_isolator()
            )
            return await isolator.run(node_def.func, current_kwargs, timeout=timeout)
        if timeout is not None:
            return await asyncio.wait_for(
                asyncio.to_thread(node_def.func, **current_kwargs), timeout
            )
        # Always run synchronous nodes in a thread — even without a timeout.
        # Calling node_def.func() directly blocks the event loop for the
        # node's full duration, stalling all concurrent runs and heartbeats.
        return await asyncio.to_thread(node_def.func, **current_kwargs)

    async def resolve_agent_actions(raw: Any, current_kwargs: dict[str, Any]) -> Any:
        next_raw = raw
        call_kwargs = current_kwargs
        _loop_iter = 0
        while isinstance(next_raw, AgentActionRequest):
            response = await _dispatch_agent_action_request(
                next_raw,
                agent_node_id=nid,
                tool_values=call_kwargs.values(),
                emit=emit,
                pause_on_approval=pause_on_approval,
            )
            if not (
                node_def.accepts_var_keyword or "agent_resume" in node_def.param_names
            ):
                return response
            _loop_iter += 1
            if _loop_iter >= _MAX_AGENT_LOOP_ITERATIONS:
                raise RuntimeError(
                    f"agent node {nid!r} exceeded {_MAX_AGENT_LOOP_ITERATIONS} "
                    "resolve iterations — the node appears to return an "
                    "AgentActionRequest without advancing its step counter"
                )
            call_kwargs = dict(call_kwargs)
            call_kwargs["agent_resume"] = response.as_resume_input()
            next_raw = await invoke_node(call_kwargs)
        return next_raw

    try:
        for attempt in range(attempts):
            try:
                call_kwargs = dict(base_call_kwargs)
                raw = agent_action_resume.get(nid)
                if raw is None:
                    raw = await invoke_node(call_kwargs)
                raw = await resolve_agent_actions(raw, call_kwargs)
                outputs = _normalize_outputs(raw, output_names, graph_node.type)
                if graph_node.type in AUTO_PROMOTE_NODE_TYPES:
                    outputs = _auto_promote_outputs(outputs)
                _validate_output_kinds(node_def, outputs, nid)
                if max_node_output_bytes is not None and max_node_output_bytes > 0:
                    bound = _encoded_upper_bound(outputs)
                    if bound is None or bound > max_node_output_bytes:
                        try:
                            approx = _approx_encoded_length(outputs)
                        except (TypeError, ValueError):
                            approx = 0
                        if approx > max_node_output_bytes:
                            raise ValueError(
                                f"node output of {approx} bytes exceeds limit of "
                                f"{max_node_output_bytes} bytes"
                            )
                caught = None
                break
            except AgentApprovalRequired as exc:
                caught = exc
                break
            except Exception as exc:  # noqa: BLE001
                caught = exc
                if attempt + 1 < attempts and graph_node.retry_wait_seconds > 0:
                    wait = graph_node.retry_wait_seconds
                    delay = wait * (2**attempt if graph_node.retry_backoff else 1)
                    delay += random.uniform(0, wait * 0.1)
                    await asyncio.sleep(delay)
    finally:
        current_node_id.reset(node_token)
        node_debug.reset(debug_token)
        _log_capture.reset(log_token)
    logs = "".join(log_buf).splitlines()

    if caught is None and outputs is not None:
        node_outputs[nid] = outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=outputs,
                logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
                node_type_version=node_def.manifest.version,
            )
        )
        return run_status

    if isinstance(caught, AgentApprovalRequired):
        run_status = RunStatus.waiting
        debug["agent_approval_state"] = {
            "agent_node_id": nid,
            "approval_key": caught.approval_key,
            "tool_call_id": caught.tool_call.id,
            "tool_name": caught.tool_call.name,
            "request": caught.request.model_dump(mode="json"),
        }
        await finish(
            NodeRunResult(
                node_id=nid,
                status=NodeStatus.waiting,
                error=caught.message,
                logs=logs,
                debug=debug,
                started_at=started,
                finished_at=time.time(),
                node_type_version=node_def.manifest.version,
            )
        )
        return run_status

    if isinstance(caught, (TimeoutError, asyncio.TimeoutError)):
        error_msg = f"node timed out after {timeout}s"
    else:
        error_msg = f"{type(caught).__name__}: {caught}"
        notes = getattr(caught, "__notes__", None) or ()
        if notes:
            error_msg = "\n\n".join((error_msg, *notes))
    continue_on_error = (
        graph_node.on_error == "continue" or graph_node.always_output_data
    )
    if continue_on_error:
        fallback_outputs = {output_names[0]: None}
        node_outputs[nid] = fallback_outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=error_msg,
                outputs=fallback_outputs, logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
            )
        )
    else:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=error_msg,
                logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
            )
        )
    return run_status
