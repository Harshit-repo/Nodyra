"""DAG execution engine.

Executes a :class:`WorkflowGraph` in topological order. Each edge feeds an
upstream node's output into a downstream node's input port. Supports:

* branching — multi-output nodes; consumers of an untaken branch are skipped;
* partial execution — ``cache`` supplies precomputed outputs for some nodes;
* targeted runs — ``targets`` restricts execution to a subset and its ancestors;
* live events — ``on_event`` is awaited with per-node start/finish events.

The same engine runs inside env runners and inside exported scripts.
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import json
import random
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AgentApprovalRequired,
    AgentStepEvent,
    ToolAdapter,
    ToolResult,
)
from noodle.context import current_node_id, node_debug
from noodle.expr import build_context, evaluate
from noodle.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
from noodle.models import (
    NodeRunResult,
    NodeStatus,
    PortSpec,
    RunResult,
    RunStatus,
    WorkflowGraph,
)
from noodle.sdk import NodeRegistry

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

PROCESS_ISOLATED_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Node types whose outputs the engine will inspect and auto-promote heavy
# values (DataFrames, large row lists, big bytes/text) into Dataset/Artifact
# refs before the output-size cap is applied. Used for Code-like nodes where
# the user might intentionally produce table-shaped data.
AUTO_PROMOTE_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Node types that handle DatasetRefs natively and therefore should *not* have
# their generic inputs auto-expanded into rows. The Code node can call dataset
# SDK helpers on the ref directly, and dataset_* nodes declare their ports as
# ``dataset`` (which are skipped anyway). Every other node receiving a
# DatasetRef on an ``any`` port gets the rows materialized so per-item logic
# (Loop Over Items, Filter, Edit Fields, …) works as authored.
DATASET_PASSTHROUGH_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Hard cap on rows the engine will expand inline from a DatasetRef before a
# generic node runs. Beyond this the node errors with guidance to reduce rows
# upstream — datasets are meant to stay artifact-backed, not materialized whole.
DATASET_AUTO_EXPAND_CAP: int = 50_000

_process_pool: concurrent.futures.ProcessPoolExecutor | None = None


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


def _get_process_pool(max_workers: int = 4) -> concurrent.futures.ProcessPoolExecutor:
    global _process_pool
    if _process_pool is None:
        _process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
    return _process_pool


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


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""


AI_PORT_KINDS: frozenset[str] = frozenset(
    {
        "ai_language_model",
        "ai_embedding_model",
        "ai_memory",
        "ai_tool",
        "ai_output_parser",
        "ai_retriever",
        "ai_vector_store",
        "ai_document_loader",
        "ai_guardrail",
    }
)


def _port_kind(port: PortSpec | None) -> str:
    return str(getattr(port, "data_kind", "any") or "any")


def _find_port(
    ports: list[PortSpec],
    name: str | None,
    default_name: str,
) -> PortSpec | None:
    wanted = name or default_name
    return next((port for port in ports if port.name == wanted), None) or (
        ports[0] if ports else None
    )


def _kind_label(kind: str) -> str:
    labels = {
        "any": "any data",
        "main": "main data",
        "control": "control",
        "dataset": "DatasetRef",
        "artifact": "ArtifactRef",
        "file": "FileRef",
        "ai_language_model": "AI language model",
        "ai_embedding_model": "AI embedding model",
        "ai_memory": "AI memory",
        "ai_tool": "AI tool",
        "ai_output_parser": "AI output parser",
        "ai_retriever": "AI retriever",
        "ai_vector_store": "AI vector store",
        "ai_document_loader": "AI document loader",
        "ai_guardrail": "AI guardrail",
    }
    return labels.get(kind, kind)


def _connection_kind_error(source_kind: str, target_kind: str) -> str | None:
    if source_kind == target_kind:
        return None

    if source_kind in AI_PORT_KINDS or target_kind in AI_PORT_KINDS:
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )

    # DatasetRef ports are strict: they may only connect to a port that
    # explicitly accepts them ("dataset") or to the permissive "any" escape
    # hatch (where _auto_expand_dataset_inputs handles runtime expansion).
    if source_kind == "dataset":
        if target_kind in {"dataset", "any"}:
            return None
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )
    if target_kind == "dataset":
        if source_kind in {"dataset", "any"}:
            return None
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )

    # `main` is the explicit form of ordinary item/data flow; `any` remains the
    # permissive escape hatch for legacy and generic nodes.
    if source_kind in {"any", "main"} or target_kind in {"any", "main"}:
        return None

    return (
        f"{_kind_label(source_kind)} cannot connect to "
        f"{_kind_label(target_kind)}"
    )


def _validate_connection_kinds(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    needed: set[str] | None = None,
) -> None:
    nodes_by_id = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        if needed is not None and (
            edge.source not in needed or edge.target not in needed
        ):
            continue
        source_node = nodes_by_id.get(edge.source)
        target_node = nodes_by_id.get(edge.target)
        if source_node is None or target_node is None:
            continue
        try:
            source_def = registry.get(source_node.type)
            target_def = registry.get(target_node.type)
        except KeyError:
            # Preserve existing unknown-node behavior: execution reports the
            # missing node as a node error instead of failing graph validation.
            continue

        source_port = _find_port(
            source_def.manifest.outputs,
            edge.source_output,
            "main",
        )
        target_port = _find_port(
            target_def.manifest.inputs,
            edge.target_input,
            "input",
        )
        source_kind = _port_kind(source_port)
        # A tool-mode node exposes a single `tool` output of kind ai_tool,
        # regardless of its normal (data-flow) manifest outputs.
        if (
            getattr(source_node, "tool_mode", False)
            and edge.source_output == TOOL_MODE_OUTPUT
        ):
            source_kind = "ai_tool"
        target_kind = _port_kind(target_port)
        error = _connection_kind_error(source_kind, target_kind)
        if error:
            raise GraphError(
                "Port kind mismatch on edge "
                f"{edge.source}.{edge.source_output} -> "
                f"{edge.target}.{edge.target_input}: {error}."
            )


def _predecessors(graph: WorkflowGraph) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for edge in graph.edges:
        if edge.source in preds and edge.target in preds and edge.source != edge.target:
            preds[edge.target].add(edge.source)
    return preds


def _topo_order(graph: WorkflowGraph) -> list[str]:
    """Return a deterministic topological order of node ids.

    Contract:
      * Order depends only on edges and node insertion order in ``graph.nodes``.
      * Canvas position (``GraphNode.position`` / x,y) is never read here and
        must never influence execution order — the editor may reorder nodes
        visually without changing semantics.
      * Among nodes with equal indegree, ties are broken by their index in
        ``graph.nodes`` (stable insertion order), not by node id.
    """
    node_index = {n.id: i for i, n in enumerate(graph.nodes)}
    preds = _predecessors(graph)
    successors: dict[str, set[str]] = defaultdict(set)
    for target, sources in preds.items():
        for source in sources:
            successors[source].add(target)

    indegree = {nid: len(sources) for nid, sources in preds.items()}
    by_index = lambda nid: node_index[nid]  # noqa: E731
    ready = sorted(
        (nid for nid, deg in indegree.items() if deg == 0), key=by_index
    )
    order: list[str] = []

    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for succ in sorted(successors[nid], key=by_index):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
        ready.sort(key=by_index)

    if len(order) != len(graph.nodes):
        raise GraphError("Workflow graph has a cycle")
    return order


def _topo_levels(graph: WorkflowGraph) -> list[list[str]]:
    """Return nodes grouped by depth level.

    All nodes in one level have all their predecessors in earlier levels, so
    they can safely execute in parallel via ``asyncio.gather``. Ties within a
    level are broken by insertion order (same rule as ``_topo_order``).
    """
    node_index = {n.id: i for i, n in enumerate(graph.nodes)}
    preds = _predecessors(graph)
    successors: dict[str, set[str]] = defaultdict(set)
    for target, sources in preds.items():
        for source in sources:
            successors[source].add(target)

    indegree = {nid: len(sources) for nid, sources in preds.items()}
    remaining = set(indegree.keys())
    levels: list[list[str]] = []

    while remaining:
        level = sorted(
            [nid for nid in remaining if indegree[nid] == 0],
            key=lambda nid: node_index[nid],
        )
        if not level:
            raise GraphError("Cycle detected")
        levels.append(level)
        for nid in level:
            remaining.remove(nid)
            for succ in successors[nid]:
                indegree[succ] -= 1
    return levels


def _needed_nodes(
    graph: WorkflowGraph,
    targets: set[str] | None,
    cache: dict[str, dict[str, Any]],
) -> set[str]:
    if targets is None:
        return {n.id for n in graph.nodes}

    preds = _predecessors(graph)
    needed: set[str] = set()
    stack = list(targets)
    while stack:
        nid = stack.pop()
        if nid in needed:
            continue
        needed.add(nid)
        if nid in cache:
            continue  # cached node supplies a value; its ancestors aren't needed
        stack.extend(preds.get(nid, ()))
    return needed


def _normalize_outputs(raw: Any, output_names: list[str], node_id: str) -> dict[str, Any]:
    if len(output_names) == 1:
        return {output_names[0]: raw}
    if not isinstance(raw, dict):
        raise ValueError(
            f"node '{node_id}' declares multiple outputs and must return a dict"
        )
    return {name: raw[name] for name in output_names if name in raw}


def _auto_expand_dataset_inputs(
    node_def: Any,
    kwargs: dict[str, Any],
    node_type: str,
) -> None:
    """Expand DatasetRef inputs into rows for generic per-item nodes.

    A DatasetRef is a single envelope dict. Without this, item-processing
    nodes (Loop Over Items, Filter, …) would treat the whole dataset as one
    item and run once. Here we materialize the rows so those nodes operate on
    the records. Dataset-native node types (``DATASET_PASSTHROUGH_NODE_TYPES``)
    and ports explicitly declared as ``dataset``/``artifact`` are left as the
    raw ref.
    """
    if node_type in DATASET_PASSTHROUGH_NODE_TYPES:
        return
    from noodle.datasets import is_dataset_ref, materialize_dataset_rows

    for port in node_def.manifest.inputs:
        kind = getattr(port, "data_kind", "any")
        if kind != "any" or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if is_dataset_ref(value):
            kwargs[port.name] = materialize_dataset_rows(
                value, cap=DATASET_AUTO_EXPAND_CAP
            )


def _validate_input_kinds(
    node_def: Any,
    kwargs: dict[str, Any],
    node_id: str,
) -> None:
    """Validate inputs declared as dataset/artifact actually receive that kind."""
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    for port in node_def.manifest.inputs:
        kind = getattr(port, "data_kind", "any")
        if kind in ("any", "control") or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if value is None:
            continue
        if kind == "dataset" and not is_dataset_ref(value):
            raise ValueError(
                f"node '{node_id}' input '{port.name}' expected a DatasetRef. "
                f"Add a Records To Dataset or CSV Parse node upstream."
            )
        if kind in ("artifact", "file") and not is_artifact_ref(value):
            raise ValueError(
                f"node '{node_id}' input '{port.name}' expected an ArtifactRef."
            )


def _validate_output_kinds(
    node_def: Any,
    outputs: dict[str, Any],
    node_id: str,
) -> None:
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    for port in node_def.manifest.outputs:
        kind = getattr(port, "data_kind", "any")
        if kind in ("any", "control") or port.name not in outputs:
            continue
        value = outputs[port.name]
        if value is None:
            continue
        if kind == "dataset" and not is_dataset_ref(value):
            raise ValueError(
                f"node '{node_id}' output '{port.name}' was declared as a "
                f"dataset port but produced {type(value).__name__}."
            )
        if kind in ("artifact", "file") and not is_artifact_ref(value):
            raise ValueError(
                f"node '{node_id}' output '{port.name}' was declared as an "
                f"artifact port but produced {type(value).__name__}."
            )


def _auto_promote_outputs(
    outputs: dict[str, Any],
    *,
    max_inline_rows: int = 1000,
    max_inline_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Promote large/typed values from auto-mode nodes to Dataset/Artifact refs.

    Only triggers when the value is clearly heavy (DataFrame, big row list,
    big bytes/text). Refs that exist already, small inline values, and
    control-shaped dicts are returned unchanged.
    """
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    try:
        from noodle.dataset_promote import promote_value
    except ImportError:
        return outputs

    promoted: dict[str, Any] = {}
    for name, value in outputs.items():
        if value is None or is_dataset_ref(value) or is_artifact_ref(value):
            promoted[name] = value
            continue
        promoted[name] = promote_value(
            value,
            port_name=name,
            max_inline_rows=max_inline_rows,
            max_inline_bytes=max_inline_bytes,
        )
    return promoted


def _node_timeout(
    node_type: str,
    timeout: float | None,
    default_timeouts: dict[str, float],
) -> float | None:
    if timeout is not None:
        return timeout
    return default_timeouts.get(node_type)


def _collect_tool_adapters(value: Any) -> list[ToolAdapter]:
    """Collect ToolAdapter instances from a wired agent input value."""
    if isinstance(value, ToolAdapter):
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        tools: list[ToolAdapter] = []
        for item in value:
            tools.extend(_collect_tool_adapters(item))
        return tools
    if isinstance(value, dict):
        tools: list[ToolAdapter] = []
        for key in ("tool", "tools"):
            if key in value:
                tools.extend(_collect_tool_adapters(value[key]))
        return tools
    return []


def _tool_index(tool_values: Iterable[Any]) -> dict[str, ToolAdapter]:
    index: dict[str, ToolAdapter] = {}
    for value in tool_values:
        for tool in _collect_tool_adapters(value):
            name = str(tool.schema.name or "").strip()
            if not name:
                continue
            if name in index and index[name] is not tool:
                raise ValueError(f"duplicate AI tool name: {name}")
            index[name] = tool
    return index


def _tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _agent_approval_key(agent_node_id: str, step: int, call_name: str, call_id: str) -> str:
    raw = "|".join([agent_node_id, str(step), call_id, call_name])
    return raw[:240]


async def _dispatch_agent_action_request(
    request: AgentActionRequest,
    *,
    agent_node_id: str,
    tool_values: Iterable[Any],
    emit: EventCallback,
    pause_on_approval: bool = False,
) -> AgentActionResponse:
    max_steps = max(1, int(request.max_steps or 1))
    step = max(0, int(request.step or 0))
    if step >= max_steps:
        raise RuntimeError(f"agent reached max_steps={max_steps}")

    tools = _tool_index(tool_values)
    await emit(
        {
            **AgentStepEvent(
                type="agent_action_requested",
                agent_node_id=agent_node_id,
                step=step,
                max_steps=max_steps,
            ).model_dump(exclude_none=True),
            "tool_calls": [
                call.model_dump(exclude_none=True) for call in request.tool_calls
            ],
        }
    )

    results: list[ToolResult] = []
    for call in request.tool_calls:
        started = time.time()
        await emit(
            {
                **AgentStepEvent(
                    type="agent_tool_started",
                    agent_node_id=agent_node_id,
                    step=step,
                    max_steps=max_steps,
                    tool_call_id=call.id,
                    tool_name=call.name,
                ).model_dump(exclude_none=True),
                "arguments": call.arguments,
            }
        )

        tool = tools.get(call.name)
        approval_key = _agent_approval_key(agent_node_id, step, call.name, call.id)
        approved_call_ids = set(request.approved_tool_call_ids or [])
        if tool is None:
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Unknown tool: {call.name}",
                is_error=True,
            )
        elif (
            tool.side_effecting
            and not request.allow_side_effects
            and call.id not in approved_call_ids
        ):
            message = f"Tool {call.name!r} requires approval before running."
            await emit(
                {
                    **AgentStepEvent(
                        type="agent_tool_approval_required",
                        agent_node_id=agent_node_id,
                        step=step,
                        max_steps=max_steps,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        approval_key=approval_key,
                        status="blocked",
                        message=message,
                    ).model_dump(exclude_none=True),
                    "arguments": call.arguments,
                }
            )
            if pause_on_approval:
                raise AgentApprovalRequired(
                    request=request,
                    tool_call=call,
                    approval_key=approval_key,
                    message=message,
                )
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=message,
                is_error=True,
            )
        else:
            if tool.side_effecting and request.allow_side_effects:
                await emit(
                    {
                        **AgentStepEvent(
                            type="agent_tool_auto_approved",
                            agent_node_id=agent_node_id,
                            step=step,
                            max_steps=max_steps,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            approval_key=approval_key,
                            status="approved",
                            message="Side-effecting tool auto-approved by agent setting.",
                        ).model_dump(exclude_none=True),
                        "arguments": call.arguments,
                    }
                )
            try:
                content = await tool.invoke_async(dict(call.arguments))
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=_tool_content(content),
                )
            except Exception as exc:  # noqa: BLE001 - tool failures go back to the model
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=f"{type(exc).__name__}: {exc}",
                    is_error=True,
                )

        duration_ms = int((time.time() - started) * 1000)
        await emit(
            {
                **AgentStepEvent(
                    type="agent_tool_finished",
                    agent_node_id=agent_node_id,
                    step=step,
                    max_steps=max_steps,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    status="error" if result.is_error else "success",
                ).model_dump(exclude_none=True),
                "tool_result": result.model_dump(),
                "duration_ms": duration_ms,
            }
        )
        results.append(result)

    next_step = step + 1
    response = AgentActionResponse(
        tool_results=results,
        messages_so_far=list(request.messages_so_far),
        step=next_step,
        max_steps=max_steps,
    )
    await emit(
        {
            **AgentStepEvent(
                type="agent_action_completed",
                agent_node_id=agent_node_id,
                step=next_step,
                max_steps=max_steps,
                status="success",
            ).model_dump(exclude_none=True),
            "tool_results": [result.model_dump() for result in results],
        }
    )
    return response


async def execute(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
    on_event: EventCallback | None = None,
    default_timeouts: dict[str, float] | None = None,
    max_node_output_bytes: int | None = None,
    pause_on_approval: bool = False,
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
) -> RunResult:
    """Run a workflow graph and return per-node results."""
    _install_capture()
    default_timeouts = DEFAULT_NODE_TIMEOUTS if default_timeouts is None else default_timeouts
    cache = cache or {}
    agent_action_resume = agent_action_resume or {}
    target_set = set(targets) if targets is not None else None
    needed = _needed_nodes(graph, target_set, cache)
    _validate_connection_kinds(graph, registry, needed)
    levels = _topo_levels(graph)
    nodes_by_id = {n.id: n for n in graph.nodes}

    incoming: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for edge in graph.edges:
        incoming[edge.target][edge.target_input] = (edge.source, edge.source_output)

    node_outputs: dict[str, dict[str, Any]] = {}
    results: dict[str, NodeRunResult] = {}
    run_status = RunStatus.success

    async def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            await on_event(event)

    async def finish(result: NodeRunResult) -> None:
        results[result.node_id] = result
        duration_ms = None
        if result.started_at is not None and result.finished_at is not None:
            duration_ms = int((result.finished_at - result.started_at) * 1000)
        await emit(
            {
                "type": "node_finished",
                "node_id": result.node_id,
                "status": str(result.status),
                "outputs": result.outputs,
                "error": result.error,
                "logs": result.logs,
                "debug": result.debug,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "duration_ms": duration_ms,
                "node_type_version": result.node_type_version,
            }
        )

    async def _run_node(nid: str) -> None:
        nonlocal run_status
        if nid not in needed:
            return
        graph_node = nodes_by_id[nid]

        if nid in cache:
            node_outputs[nid] = dict(cache[nid])
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.success, outputs=node_outputs[nid]
                )
            )
            return

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
            return

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
            return

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
            return

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
                return
            tool_outputs = {TOOL_MODE_OUTPUT: adapter}
            node_outputs[nid] = tool_outputs
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.success, outputs=tool_outputs,
                    started_at=started, finished_at=time.time(),
                    node_type_version=node_def.manifest.version,
                )
            )
            return

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
            return

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
            return

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
            global _process_pool
            if node_def.is_async:
                if timeout is not None:
                    return await asyncio.wait_for(
                        node_def.func(**current_kwargs), timeout
                    )
                return await node_def.func(**current_kwargs)
            if graph_node.type in PROCESS_ISOLATED_NODE_TYPES:
                loop = asyncio.get_event_loop()
                fn_with_kwargs = functools.partial(node_def.func, **current_kwargs)
                fut = loop.run_in_executor(_get_process_pool(), fn_with_kwargs)
                try:
                    return await asyncio.wait_for(fut, timeout)
                except TimeoutError:
                    if _process_pool is not None:
                        _process_pool.shutdown(wait=False, cancel_futures=True)
                        _process_pool = None
                    raise
                except concurrent.futures.process.BrokenProcessPool as exc:
                    # Child died (segfault / OOM / unpicklable arg). Recycle
                    # the pool so the next attempt gets a fresh one and
                    # re-raise as a normal ValueError so the node fails cleanly.
                    if _process_pool is not None:
                        _process_pool.shutdown(wait=False, cancel_futures=True)
                        _process_pool = None
                    raise ValueError(
                        "code node crashed: subprocess died (possible "
                        "out-of-memory, segfault, or unpicklable value)"
                    ) from exc
            if timeout is not None:
                return await asyncio.wait_for(
                    asyncio.to_thread(node_def.func, **current_kwargs), timeout
                )
            return node_def.func(**current_kwargs)

        async def resolve_agent_actions(raw: Any, current_kwargs: dict[str, Any]) -> Any:
            next_raw = raw
            call_kwargs = current_kwargs
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
            return

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
            return

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

    # Execute level by level; nodes within a level have no interdependencies
    # and can run in parallel via asyncio.gather.
    for level in levels:
        await asyncio.gather(*[_run_node(nid) for nid in level])

    return RunResult(status=run_status, nodes=results)


def run(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
) -> RunResult:
    """Synchronous wrapper around :func:`execute` for scripts and exports."""
    return asyncio.run(execute(graph, registry, cache=cache, targets=targets))
