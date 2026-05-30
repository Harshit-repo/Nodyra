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

from noodle.context import current_node_id, node_debug
from noodle.expr import build_context, evaluate
from noodle.models import (
    NodeRunResult,
    NodeStatus,
    RunResult,
    RunStatus,
    WorkflowGraph,
)
from noodle.sdk import NodeRegistry

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

PROCESS_ISOLATED_NODE_TYPES: frozenset[str] = frozenset({"code"})

_process_pool: concurrent.futures.ProcessPoolExecutor | None = None


def _get_process_pool(max_workers: int = 4) -> concurrent.futures.ProcessPoolExecutor:
    global _process_pool
    if _process_pool is None:
        _process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
    return _process_pool


DEFAULT_NODE_TIMEOUTS: dict[str, float] = {
    "code": 60.0,
    "http_request": 45.0,
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


def _node_timeout(
    node_type: str,
    timeout: float | None,
    default_timeouts: dict[str, float],
) -> float | None:
    if timeout is not None:
        return timeout
    return default_timeouts.get(node_type)


async def execute(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
    on_event: EventCallback | None = None,
    default_timeouts: dict[str, float] | None = None,
    max_node_output_bytes: int | None = None,
) -> RunResult:
    """Run a workflow graph and return per-node results."""
    _install_capture()
    default_timeouts = DEFAULT_NODE_TIMEOUTS if default_timeouts is None else default_timeouts
    cache = cache or {}
    target_set = set(targets) if targets is not None else None
    needed = _needed_nodes(graph, target_set, cache)
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

        kwargs: dict[str, Any] = {}
        for port in node_def.manifest.inputs:
            if port.name in edges_in:
                source, source_output = edges_in[port.name]
                kwargs[port.name] = node_outputs[source][source_output]

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
            call_kwargs = kwargs
        else:
            call_kwargs = {
                k: v for k, v in kwargs.items() if k in node_def.param_names
            }
        try:
            for attempt in range(attempts):
                try:
                    if node_def.is_async:
                        if timeout is not None:
                            raw = await asyncio.wait_for(
                                node_def.func(**call_kwargs), timeout
                            )
                        else:
                            raw = await node_def.func(**call_kwargs)
                    elif graph_node.type in PROCESS_ISOLATED_NODE_TYPES:
                        loop = asyncio.get_event_loop()
                        fn_with_kwargs = functools.partial(node_def.func, **call_kwargs)
                        fut = loop.run_in_executor(_get_process_pool(), fn_with_kwargs)
                        try:
                            raw = await asyncio.wait_for(fut, timeout)
                        except (asyncio.TimeoutError, TimeoutError):
                            global _process_pool
                            if _process_pool is not None:
                                _process_pool.shutdown(wait=False, cancel_futures=True)
                                _process_pool = None
                            raise
                    elif timeout is not None:
                        raw = await asyncio.wait_for(
                            asyncio.to_thread(node_def.func, **call_kwargs), timeout
                        )
                    else:
                        raw = node_def.func(**call_kwargs)
                    outputs = _normalize_outputs(raw, output_names, graph_node.type)
                    if max_node_output_bytes is not None and max_node_output_bytes > 0:
                        try:
                            approx = len(json.dumps(outputs, default=str))
                        except (TypeError, ValueError):
                            approx = 0
                        if approx > max_node_output_bytes:
                            raise ValueError(
                                f"node output of {approx} bytes exceeds limit of "
                                f"{max_node_output_bytes} bytes"
                            )
                    caught = None
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
