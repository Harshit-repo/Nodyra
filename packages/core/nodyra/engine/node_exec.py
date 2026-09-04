"""Single-node execution: input wiring, expression eval, retries, timeouts,
process isolation for code nodes, log capture, node hooks, and output
normalization."""

import asyncio
import contextvars
import json
import logging
import random
import sys
import threading
import time
import types
from collections.abc import Awaitable, Callable
from typing import Any

from nodyra.ai_runtime import AgentActionRequest, AgentApprovalRequired
from nodyra.context import (
    cancel_event,
    current_node_id,
    iteration_path,
    node_debug,
    node_emitter,
    run_deadline,
)
from nodyra.engine.agent import (
    _MAX_AGENT_LOOP_ITERATIONS,
    _dispatch_agent_action_request,
)
from nodyra.engine.datasets import (
    AUTO_PROMOTE_NODE_TYPES,
    _auto_expand_dataset_inputs,
    _auto_promote_outputs,
)
from nodyra.engine.types import EventCallback, NodeValidationError, RuntimeContext
from nodyra.engine.validation import (
    _validate_input_kinds,
    _validate_input_schemas,
    _validate_output_kinds,
)
from nodyra.expr import build_context, evaluate
from nodyra.models import NodeRunResult, NodeStatus, RunStatus
from nodyra.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
from nodyra.process_isolation import ProcessIsolator, default_isolator
from nodyra.sdk import NodeRegistry

PROCESS_ISOLATED_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Virtual output port that every node implicitly has.  When ``on_error`` is set
# to ``"output"``, a failing node emits an error object on this port instead of
# halting the run, and downstream nodes wired to ``source_output="$error"`` can
# react — n8n-style visual error handling.
ERROR_OUTPUT_PORT: str = "$error"

# A node streaming chunks from a worker thread hops each one onto the engine's
# event loop and waits briefly so the chunk is delivered in order (before the
# node's own ``node_finished``). Bounded so a congested/stalled loop can never
# wedge the node — streaming is strictly best-effort.
_CHUNK_EMIT_TIMEOUT = 5.0


def _make_chunk_emitter(
    loop: asyncio.AbstractEventLoop,
    emit: EventCallback,
    nid: str,
    iter_path: tuple[int, ...],
) -> Callable[..., None]:
    """Build the ``nodyra.emit_chunk`` callback installed while ``nid`` runs.

    Works from both the engine loop (async nodes) and a worker thread (sync
    nodes run via ``asyncio.to_thread``). The loop-thread case schedules the
    emit without blocking; the worker-thread case bounces onto the loop and
    waits (bounded) so chunks stay ordered ahead of ``node_finished``. The
    iteration path is captured here, in the node's context, so a chunk emitted
    from inside a loop body is still attributed to its iteration even though the
    coroutine runs on the (loop-context) engine thread.
    """
    base: dict[str, Any] = {"type": "node_chunk", "node_id": nid}
    if iter_path:
        base["iteration_path"] = list(iter_path)

    def _emit(delta: str, *, channel: str = "output") -> None:
        event = {**base, "delta": str(delta), "channel": channel}
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is loop:
                loop.create_task(emit(event))
            else:
                future = asyncio.run_coroutine_threadsafe(emit(event), loop)
                future.result(timeout=_CHUNK_EMIT_TIMEOUT)
        except Exception:  # noqa: BLE001 - streaming must never break the node
            pass

    return _emit


IncomingConnection = tuple[str, str]
IncomingPortValue = IncomingConnection | list[IncomingConnection]
IncomingMap = dict[str, dict[str, IncomingPortValue]]


def _incoming_connections(value: IncomingPortValue) -> list[IncomingConnection]:
    return value if isinstance(value, list) else [value]


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
    "nodyra_log_capture", default=None
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


# Exceptions that should never be retried — they indicate fundamental
# programming errors or resource exhaustion, not transient failures. We do NOT
# include KeyboardInterrupt/SystemExit here: those must propagate so a Ctrl-C or
# process signal actually interrupts the run instead of being converted into an
# ordinary node error and retried/continued.
_FATAL_ERRORS: tuple[type[BaseException], ...] = (
    MemoryError,
    RecursionError,
    SystemError,
)


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove keys that could trigger external resource fetching during
    jsonschema validation.

    Strips ``$ref``, ``$schema``, and ``$id`` recursively so that a
    user-controlled schema (stored in node params) can never cause
    ``jsonschema.validate()`` to fetch external resources.

    Args:
        schema: A JSON Schema dict (possibly user-supplied).

    Returns:
        A new dict with dangerous keys removed at all nesting levels.
    """
    blocked = {"$ref", "$schema", "$id"}

    def _clean(val: Any) -> Any:
        if isinstance(val, dict):
            return {k: _clean(v) for k, v in val.items() if k not in blocked}
        if isinstance(val, list):
            return [_clean(item) for item in val]
        return val

    return _clean(schema)


def _validate_node_input_schema(params: dict[str, Any], node_input: dict[str, Any], node_id: str) -> None:
    """Validate wired inputs against the code node's optional ``input_schema``.

    ``input_schema`` is a JSON Schema dict stored in the node's params.
    Only code nodes set this; non-code nodes have no ``input_schema``
    and are skipped.

    Raises:
        NodeValidationError: when validation fails.
    """
    import jsonschema

    input_schema = params.get("input_schema")
    if input_schema and isinstance(node_input, dict):
        try:
            jsonschema.validate(node_input, _sanitize_schema(input_schema))
        except jsonschema.ValidationError as exc:
            raise NodeValidationError(
                f"Input validation failed for node '{node_id}': {exc.message}",
                node_id=node_id,
            ) from exc


def _validate_node_output_schema(params: dict[str, Any], outputs: dict[str, Any], node_id: str) -> None:
    """Validate node outputs against the code node's optional ``output_schema``.

    ``output_schema`` is a JSON Schema dict stored in the node's params.
    Only code nodes set this; non-code nodes have no ``output_schema``
    and are skipped.

    Raises:
        NodeValidationError: when validation fails.
    """
    import jsonschema

    output_schema = params.get("output_schema")
    if output_schema and isinstance(outputs, dict):
        try:
            jsonschema.validate(outputs, _sanitize_schema(output_schema))
        except jsonschema.ValidationError as exc:
            raise NodeValidationError(
                f"Output validation failed for node '{node_id}': {exc.message}",
                node_id=node_id,
            ) from exc


def _freeze_outputs(outputs: dict[str, Any]) -> types.MappingProxyType:
    """Wrap the outputs dict in a read-only proxy so that a downstream node
    cannot accidentally mutate another node's stored outputs via the shared
    ``node_outputs`` dict. Only the top-level dict is protected — nested
    values remain mutable (deep-freezing every value is too expensive and
    would break nodes that legitimately mutate their own input copies)."""
    return types.MappingProxyType(outputs)


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


_HOOK_LOGGER = logging.getLogger("nodyra.hooks")

# Hostname blocks that are NEVER safe webhook targets — loopback, RFC 1918
# private ranges, link-local, and cloud metadata endpoints.
_SSRF_BLOCKED_HOSTS: frozenset[str] = frozenset({
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "169.254.169.254",  # AWS / GCP / Azure metadata endpoint
    "metadata.google.internal",
    "metadata",
    "localhost",
})

_SSRF_BLOCKED_PREFIXES: tuple[str, ...] = (
    "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
    "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
    "172.30.", "172.31.", "192.168.", "169.254.", "fc00:",
    "fd00:",
)


def _is_safe_webhook_url(url_str: str) -> tuple[bool, str]:
    """Validate a webhook URL for SSRF safety.

    Returns ``(True, "")`` when the URL is safe, or ``(False, reason)``
    when it should be blocked.
    """
    import urllib.parse

    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception:
        return False, "invalid URL"

    # Only HTTPS.  Plain HTTP is a straight MITM risk for webhook payloads
    # that may contain node output data.
    if parsed.scheme not in ("https",):
        return False, f"unsupported scheme: {parsed.scheme!r}"

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "missing hostname"

    # Block exact matches (loopback, metadata endpoints).
    if hostname in _SSRF_BLOCKED_HOSTS:
        return False, f"blocked hostname: {hostname!r}"

    # Block RFC 1918 / link-local / ULA prefixes.
    for prefix in _SSRF_BLOCKED_PREFIXES:
        if hostname.startswith(prefix):
            return False, f"private-range hostname: {hostname!r}"

    return True, ""


def matches_display_when(
    display_when: Any, params: "dict[str, Any]"
) -> bool:
    """Is a param visible, given the node's current param values?

    Consolidated integration nodes hold the union of every operation's params
    and record which (resource, operation) pairs each belongs to in
    ``display_when``. A param that is not visible for the selected operation is
    not part of that operation's contract, so it cannot be required by it.

    Must stay in step with ``matchesDisplayWhen`` in
    ``apps/web/src/editor/node-details/displayRules.ts``: the editor deciding a
    field is irrelevant while the engine demands it is exactly the failure this
    exists to prevent.
    """
    if not isinstance(display_when, dict) or not display_when:
        return True
    any_groups = display_when.get("any")
    if isinstance(any_groups, list):
        return any(matches_display_when(group, params) for group in any_groups)
    conditions = display_when.get("conditions")
    if isinstance(conditions, list):
        return all(matches_display_when(cond, params) for cond in conditions)
    param_name = str(display_when.get("param") or "")
    if not param_name:
        return True
    current = str(params.get(param_name) if params.get(param_name) is not None else "")
    values = display_when.get("values")
    if isinstance(values, list):
        return current in [str(v) for v in values]
    expected = display_when.get("value")
    return current == str(expected if expected is not None else "")


async def _run_node_hooks(
    hooks: list[dict[str, Any]],
    trigger: str,
    *,
    node_id: str,
    node_type: str,
    status: str = "",
    error: str | None = None,
    outputs: dict[str, Any] | None = None,
    attempt: int = 0,
) -> None:
    """Execute all hooks matching ``trigger`` best-effort.

    A hook failure is logged and swallowed — it must never affect the node
    result. Supported hook types:

    * ``log`` — emit a structured log line at the configured level.
    * ``webhook`` — POST a JSON payload to ``config.url``.
    * ``call_workflow`` — trigger another workflow by ID (fire-and-forget).
    """
    payload: dict[str, Any] | None = None

    for hook in hooks:
        if hook.get("trigger") != trigger:
            continue
        hook_type = str(hook.get("type") or "")
        config = hook.get("config") or {}

        try:
            if hook_type == "log":
                level = str(config.get("level") or "info").lower()
                message = str(config.get("message") or "")
                if not message:
                    message = (
                        f"node {node_id} ({node_type}) {trigger}"
                        + (f": {error}" if error else "")
                    )
                log_kwargs: dict[str, Any] = {"extra": {
                    "hook": hook_type, "trigger": trigger,
                    "node_id": node_id, "node_type": node_type,
                    "status": status, "attempt": attempt,
                }}
                if error:
                    log_kwargs["extra"]["error"] = error
                getattr(_HOOK_LOGGER, level, _HOOK_LOGGER.info)(message, **log_kwargs)  # type: ignore[arg-type]

            elif hook_type == "webhook":
                url = str(config.get("url") or "")
                if not url:
                    continue
                # SSRF guard: reject URLs targeting internal / loopback hosts.
                safe, reason = _is_safe_webhook_url(url)
                if not safe:
                    _HOOK_LOGGER.warning(
                        "webhook hook blocked node_id=%s trigger=%s url=%s reason=%s",
                        node_id, trigger, url, reason,
                    )
                    continue
                if payload is None:
                    payload = {
                        "trigger": trigger,
                        "node_id": node_id,
                        "node_type": node_type,
                        "status": status,
                        "error": error,
                        "attempt": attempt,
                        "timestamp": time.time(),
                    }
                    if outputs is not None:
                        payload["output_keys"] = list(outputs.keys())
                # Best-effort: short timeout, no retry, no redirects (SSRF).
                try:
                    import httpx
                    async with httpx.AsyncClient(
                        timeout=5.0, follow_redirects=False,
                    ) as client:
                        await client.post(url, json=payload)
                except Exception:  # noqa: BLE001
                    pass

            elif hook_type == "call_workflow":
                target_wf_id = str(config.get("workflow_id") or "")
                if not target_wf_id:
                    continue
                if payload is None:
                    payload = {
                        "trigger": trigger,
                        "node_id": node_id,
                        "node_type": node_type,
                        "status": status,
                        "error": error,
                        "attempt": attempt,
                        "timestamp": time.time(),
                    }
                    if outputs is not None:
                        payload["outputs"] = {
                            k: str(v)[:200] for k, v in outputs.items()
                        }
                # Fire-and-forget: dispatch the target workflow with
                # the error context as its trigger parameters.
                try:
                    from nodyra.context import workflow_caller as _wf_caller_ctx
                    _caller = _wf_caller_ctx.get()
                    if _caller is not None:
                        await _caller(
                            target_wf_id,
                            {"main": payload},
                        )
                except Exception:  # noqa: BLE001
                    pass

        except Exception:  # noqa: BLE001
            _HOOK_LOGGER.debug(
                "hook failed node_id=%s trigger=%s type=%s",
                node_id, trigger, hook_type, exc_info=True,
            )


async def _run_one_node(
    *,
    nid: str,
    nodes_by_id: dict[str, Any],
    incoming: IncomingMap,
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
        outputs = dict(cache[nid])
        node_outputs[nid] = _freeze_outputs(outputs)
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=outputs
            )
        )
        return run_status

    edges_in = incoming.get(nid, {})

    skip_reason = None
    for incoming_value in edges_in.values():
        for source, source_output in _incoming_connections(incoming_value):
            if source not in node_outputs:
                skip_reason = f"upstream node '{source}' produced no output"
                break
            if source_output not in node_outputs[source]:
                skip_reason = f"branch '{source_output}' of node '{source}' was not taken"
                break
        if skip_reason is not None:
            break
    if skip_reason is not None:
        await finish(
            NodeRunResult(node_id=nid, status=NodeStatus.skipped, error=skip_reason)
        )
        return run_status

    node_hooks: list[dict[str, Any]] = getattr(graph_node, "hooks", None) or []
    await emit({"type": "node_started", "node_id": nid})
    if node_hooks:
        await _run_node_hooks(
            node_hooks, "on_start",
            node_id=nid, node_type=graph_node.type,
            status="running",
        )
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
                source, source_output = _incoming_connections(edges_in[port.name])[-1]
                passthrough = node_outputs[source][source_output]
                break
        outputs = {output_names[0]: passthrough}
        node_outputs[nid] = _freeze_outputs(outputs)
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
        node_outputs[nid] = _freeze_outputs(tool_outputs)
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
            connections = _incoming_connections(edges_in[port.name])
            values = [
                node_outputs[source][source_output]
                for source, source_output in connections
            ]
            if len(values) > 1 and getattr(port, "data_kind", "any") == "ai_tool":
                kwargs[port.name] = values
            else:
                kwargs[port.name] = values[-1]

    try:
        _auto_expand_dataset_inputs(node_def, kwargs, graph_node.type)
        _validate_input_kinds(node_def, kwargs, nid)
        _validate_input_schemas(node_def, kwargs, nid)
        # Validate input against code node's optional input_schema (typed I/O).
        _validate_node_input_schema(graph_node.params, kwargs, nid)
    except (ValueError, RuntimeError, NodeValidationError) as exc:
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
        elif spec.required and matches_display_when(
            getattr(spec, "display_when", None), graph_node.params
        ):
            # A param the editor hides for the selected resource/operation is
            # not part of that operation's contract, so it cannot be required
            # by it. Without this the engine demanded fields the user was never
            # shown - "missing required parameters: reaction_name" on a Slack
            # message send.
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
            val = kwargs[spec.name]
            # ``evaluate`` recurses into dicts/lists, so a structured param (e.g.
            # an HTTP headers/body object) may carry ``{{ }}`` templates in nested
            # values. Evaluate every container; for plain strings keep the cheap
            # "{{" fast-path and skip scalars that can never hold a template.
            if isinstance(val, (dict, list)) or (
                isinstance(val, str) and "{{" in val
            ):
                kwargs[spec.name] = evaluate(val, expr_context)

    timeout = _node_timeout(
        graph_node.type, graph_node.timeout_seconds, default_timeouts
    )
    _deadline = run_deadline.get()
    timeout_from_run_deadline = False
    if _deadline is not None:
        remaining = max(_deadline - time.monotonic(), 0.001)
        timeout_from_run_deadline = timeout is None or remaining <= timeout
        timeout = remaining if timeout is None else min(timeout, remaining)
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
    emitter_token = node_emitter.set(
        _make_chunk_emitter(
            asyncio.get_running_loop(), emit, nid, iteration_path.get()
        )
    )
    if node_def.accepts_var_keyword or not node_def.param_names:
        base_call_kwargs = dict(kwargs)
    else:
        base_call_kwargs = {
            k: v for k, v in kwargs.items() if k in node_def.param_names
        }

    # Inject RuntimeContext when the node function accepts a ``ctx`` parameter
    # (e.g., mcp_tool nodes that dispatch MCP calls through the platform hook).
    if "ctx" in node_def.param_names or node_def.accepts_var_keyword:
        node_ctx = RuntimeContext(
            run_id="",
            workflow_id="",
        )
        node_ctx.node_params = dict(kwargs)
        node_ctx.node_inputs = {
            p.name: (kwargs.get(p.name) if p.name in kwargs else None)
            for p in node_def.manifest.inputs
        }
        base_call_kwargs["ctx"] = node_ctx

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

        # Sync nodes run in a thread. Give each invocation its own
        # threading.Event so long-running nodes can check cancel_event.is_set()
        # and exit early when the run is cancelled.
        run_cancel_event = threading.Event()
        cancel_event.set(run_cancel_event)

        async def _run_in_thread() -> Any:
            try:
                if timeout is not None:
                    return await asyncio.wait_for(
                        asyncio.to_thread(node_def.func, **current_kwargs), timeout
                    )
                return await asyncio.to_thread(node_def.func, **current_kwargs)
            except (TimeoutError, asyncio.CancelledError):
                run_cancel_event.set()
                raise

        return await _run_in_thread()

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
            log_buf.clear()
            if attempt > 0:
                if node_hooks:
                    await _run_node_hooks(
                        node_hooks, "on_retry",
                        node_id=nid, node_type=graph_node.type,
                        status="retrying", error=str(caught) if caught else None,
                        attempt=attempt,
                    )
                # A previous attempt may have streamed ``node_chunk`` deltas to
                # the client. Tell it to discard them so a retry doesn't render
                # the failed attempt's partial output concatenated with the new
                # stream. Awaited (not fire-and-forget) so the reset is ordered
                # ahead of the retry's chunks.
                reset_event: dict[str, Any] = {
                    "type": "node_chunk",
                    "node_id": nid,
                    "reset": True,
                }
                if iteration_path.get():
                    reset_event["iteration_path"] = list(iteration_path.get())
                try:
                    await emit(reset_event)
                except Exception:  # noqa: BLE001 - streaming must never break the node
                    pass
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
                # Validate output against code node's optional output_schema.
                _validate_node_output_schema(graph_node.params, outputs, nid)
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
            except _FATAL_ERRORS:
                caught = sys.exc_info()[1]
                break
            except Exception as exc:  # noqa: BLE001
                caught = exc
                if attempt + 1 < attempts and graph_node.retry_wait_seconds > 0:
                    wait = graph_node.retry_wait_seconds
                    delay = wait * (2**attempt if graph_node.retry_backoff else 1)
                    delay += random.uniform(0, wait * 0.1)
                    await asyncio.sleep(delay)
    finally:
        node_emitter.reset(emitter_token)
        current_node_id.reset(node_token)
        node_debug.reset(debug_token)
        _log_capture.reset(log_token)
    logs = "".join(log_buf).splitlines()

    # Increment the global node-execution counter (metrics).
    # Lazy import so the engine has no hard dependency on the API's metrics module.
    try:
        from app.services.metrics import node_executions_total
        node_executions_total.inc(node_type=graph_node.type)
    except Exception:  # noqa: BLE001 — metrics are best-effort
        pass

    # ADR-0005 keeps node execution on a bounded final-output contract until a
    # real workload justifies durable streamed output events.
    if caught is None and outputs is not None:
        node_outputs[nid] = _freeze_outputs(outputs)
        if node_hooks:
            await _run_node_hooks(
                node_hooks, "on_success",
                node_id=nid, node_type=graph_node.type,
                status="success", outputs=outputs,
            )
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
        # Agent approval is a pause, not a failure — on_failure hooks do NOT fire.
        pass
    elif caught is not None and node_hooks:
        await _run_node_hooks(
            node_hooks, "on_failure",
            node_id=nid, node_type=graph_node.type,
            status="error", error=(
                f"timed out after {timeout}s"
                if isinstance(caught, (TimeoutError, asyncio.TimeoutError))
                else f"{type(caught).__name__}: {caught}"
            ),
            outputs=outputs,
        )

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
        timeout_status = (
            RunStatus.timed_out
            if timeout_from_run_deadline
            else RunStatus.error
        )
    else:
        error_msg = f"{type(caught).__name__}: {caught}"
        notes = getattr(caught, "__notes__", None) or ()
        if notes:
            error_msg = "\n\n".join((error_msg, *notes))
        timeout_status = RunStatus.error
    continue_on_error = (
        graph_node.on_error == "continue" or graph_node.always_output_data
    )
    if continue_on_error:
        fallback_outputs = {output_names[0]: None}
        node_outputs[nid] = _freeze_outputs(fallback_outputs)
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=error_msg,
                outputs=fallback_outputs, logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
            )
        )
    else:
        run_status = timeout_status
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=error_msg,
                logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
            )
        )
    return run_status
