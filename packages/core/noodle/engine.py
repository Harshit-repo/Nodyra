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
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

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


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""


def _predecessors(graph: WorkflowGraph) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for edge in graph.edges:
        if edge.source in preds and edge.target in preds and edge.source != edge.target:
            preds[edge.target].add(edge.source)
    return preds


def _topo_order(graph: WorkflowGraph) -> list[str]:
    preds = _predecessors(graph)
    successors: dict[str, set[str]] = defaultdict(set)
    for target, sources in preds.items():
        for source in sources:
            successors[source].add(target)

    indegree = {nid: len(sources) for nid, sources in preds.items()}
    ready = sorted(nid for nid, deg in indegree.items() if deg == 0)
    order: list[str] = []

    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for succ in sorted(successors[nid]):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
        ready.sort()

    if len(order) != len(graph.nodes):
        raise GraphError("Workflow graph has a cycle")
    return order


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


async def execute(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
    on_event: EventCallback | None = None,
) -> RunResult:
    """Run a workflow graph and return per-node results."""
    cache = cache or {}
    target_set = set(targets) if targets is not None else None
    needed = _needed_nodes(graph, target_set, cache)
    order = _topo_order(graph)
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
        await emit(
            {
                "type": "node_finished",
                "node_id": result.node_id,
                "status": str(result.status),
                "outputs": result.outputs,
                "error": result.error,
            }
        )

    for nid in order:
        if nid not in needed:
            continue
        graph_node = nodes_by_id[nid]

        if nid in cache:
            node_outputs[nid] = dict(cache[nid])
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.success, outputs=node_outputs[nid]
                )
            )
            continue

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
            continue

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
            continue

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
            continue

        kwargs: dict[str, Any] = {}
        for port in node_def.manifest.inputs:
            if port.name in edges_in:
                source, source_output = edges_in[port.name]
                kwargs[port.name] = node_outputs[source][source_output]

        missing: list[str] = []
        for spec in node_def.manifest.params:
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
            continue

        # Evaluate {{ ... }} expressions in config parameters before the call.
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

        attempts = (
            max(1, graph_node.retries + 1) if graph_node.retry_on_fail else 1
        )
        caught: Exception | None = None
        outputs: dict[str, Any] | None = None
        for _ in range(attempts):
            try:
                if node_def.is_async:
                    raw = await node_def.func(**kwargs)
                else:
                    raw = node_def.func(**kwargs)
                outputs = _normalize_outputs(raw, output_names, graph_node.type)
                caught = None
                break
            except Exception as exc:  # noqa: BLE001 - user code; surface anything
                caught = exc

        if caught is None and outputs is not None:
            node_outputs[nid] = outputs
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.success, outputs=outputs,
                    started_at=started, finished_at=time.time(),
                )
            )
            continue

        error_msg = f"{type(caught).__name__}: {caught}"
        continue_on_error = (
            graph_node.on_error == "continue" or graph_node.always_output_data
        )
        if continue_on_error:
            fallback_outputs = {output_names[0]: None}
            node_outputs[nid] = fallback_outputs
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.error, error=error_msg,
                    outputs=fallback_outputs,
                    started_at=started, finished_at=time.time(),
                )
            )
        else:
            run_status = RunStatus.error
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.error, error=error_msg,
                    started_at=started, finished_at=time.time(),
                )
            )

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
