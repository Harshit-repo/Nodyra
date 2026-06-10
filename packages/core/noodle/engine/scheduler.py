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
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any

from noodle.ai_runtime import AgentActionRequest
from noodle.context import iteration_path
from noodle.models import NodeRunResult, RunResult, RunStatus, WorkflowGraph
from noodle.sdk import NodeRegistry

from noodle.engine.node_exec import (
    DEFAULT_NODE_TIMEOUTS,
    _install_capture,
    _run_one_node,
)
from noodle.engine.types import EventCallback, GraphError
from noodle.engine.validation import _validate_connection_kinds

if TYPE_CHECKING:
    from noodle.engine.loops import LoopRegion


# Severity ranking for run-status aggregation: when parallel nodes finish with
# different statuses, the run reports the *worst* one — error must never be
# masked by a waiting (approval-paused) node that happens to finish later.
_STATUS_RANK: dict[RunStatus, int] = {
    RunStatus.success: 0,
    RunStatus.waiting: 1,
    RunStatus.error: 2,
}


def _worse_status(a: RunStatus, b: RunStatus) -> RunStatus:
    """The more severe of two run statuses: error > waiting > success."""
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


def _predecessors(graph: WorkflowGraph) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for edge in graph.edges:
        if edge.source in preds and edge.target in preds and edge.source != edge.target:
            preds[edge.target].add(edge.source)
    return preds


def _descendants(graph: WorkflowGraph, root: str) -> set[str]:
    succ: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        succ[e.source].add(e.target)
    seen: set[str] = set()
    stack = list(succ[root])
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(succ[n])
    return seen


def _ancestors(graph: WorkflowGraph, root: str) -> set[str]:
    preds = _predecessors(graph)
    seen: set[str] = set()
    stack = list(preds.get(root, ()))
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(preds.get(n, ()))
    return seen


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


async def _execute_nodes(
    *,
    node_ids: set[str],
    levels: list[list[str]],
    graph: WorkflowGraph,
    registry: NodeRegistry,
    nodes_by_id: dict[str, Any],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
    loop_regions: dict[str, "LoopRegion"],
    owned: set[str],
) -> RunStatus:
    """Run ``node_ids`` in ``levels`` order against ``node_outputs``. Returns the
    worst RunStatus seen. Loop Start nodes are intercepted and driven via
    ``_run_loop``; ``owned`` nodes (loop body + end) are skipped here — their
    loop populates them."""
    # Lazy: loops.py and metanodes.py import this module at module level,
    # so importing them here (not at the top) breaks the cycle.
    from noodle.engine.loops import _run_conditional_loop, _run_loop
    from noodle.engine.metanodes import _run_metanode

    run_status = RunStatus.success
    for level in levels:
        async def _one(nid: str) -> None:
            nonlocal run_status
            if nid in owned:
                return
            gn = nodes_by_id[nid]
            if gn.type == "meta_node":
                st = await _run_metanode(
                    node=gn, incoming=incoming, node_outputs=node_outputs,
                    registry=registry, emit=emit, finish=finish,
                    default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                )
                run_status = _worse_status(run_status, st)
                return
            if gn.type == "loop_start" and nid in loop_regions:
                mode = str(gn.params.get("mode", "each") or "each")
                driver = (
                    _run_conditional_loop
                    if mode in ("while", "until")
                    else _run_loop
                )
                st = await driver(
                    region=loop_regions[nid],
                    graph=graph, registry=registry, nodes_by_id=nodes_by_id,
                    incoming=incoming, node_outputs=node_outputs, cache=cache,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=owned,
                )
            else:
                st = await _run_one_node(
                    nid=nid, nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=node_outputs, cache=cache, registry=registry,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                )
            run_status = _worse_status(run_status, st)
        await asyncio.gather(*[_one(nid) for nid in level if nid in node_ids])
    return run_status


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
    # Lazy: loops.py and metanodes.py import this module at module level,
    # so importing them here (not at the top) breaks the cycle.
    from noodle.engine.loops import _loop_regions, _validate_loop_regions
    from noodle.engine.metanodes import _expand_metanodes

    _install_capture()
    default_timeouts = DEFAULT_NODE_TIMEOUTS if default_timeouts is None else default_timeouts
    cache = cache or {}
    agent_action_resume = agent_action_resume or {}
    # Transparent metanodes are purely organizational: inline them before any
    # planning so the rest of the engine sees an ordinary flat graph.
    graph = _expand_metanodes(graph)
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

    async def emit(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        path = iteration_path.get()
        if path and "iteration_path" not in event:
            event = {**event, "iteration_path": list(path)}
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

    loop_regions = _loop_regions(graph)
    if loop_regions:
        _validate_loop_regions(graph, loop_regions)
    owned: set[str] = set()
    for r in loop_regions.values():
        owned |= set(r.body_ids)
        owned.add(r.end_id)

    # Execute level by level; nodes within a level have no interdependencies
    # and can run in parallel via asyncio.gather. Loop Start nodes are
    # intercepted by _execute_nodes and driven over their body sub-DAG.
    run_status = await _execute_nodes(
        node_ids=needed,
        levels=levels,
        graph=graph,
        registry=registry,
        nodes_by_id=nodes_by_id,
        incoming=incoming,
        node_outputs=node_outputs,
        cache=cache,
        emit=emit,
        finish=finish,
        default_timeouts=default_timeouts,
        max_node_output_bytes=max_node_output_bytes,
        pause_on_approval=pause_on_approval,
        agent_action_resume=agent_action_resume,
        loop_regions=loop_regions,
        owned=owned,
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
