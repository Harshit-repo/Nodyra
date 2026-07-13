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
import heapq
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nodyra.ai_runtime import AgentActionRequest
from nodyra.context import iteration_path
from nodyra.context import run_deadline as run_deadline_var
from nodyra.engine.node_exec import (
    DEFAULT_NODE_TIMEOUTS,
    _install_capture,
    _run_one_node,
)
from nodyra.engine.types import EventCallback, GraphError
from nodyra.engine.validation import _validate_connection_kinds
from nodyra.models import NodeRunResult, NodeStatus, RunResult, RunStatus, WorkflowGraph
from nodyra.sdk import NodeRegistry

LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nodyra.engine.loops import LoopRegion
    from nodyra.engine.subworkflows import SubworkflowMeta, SubworkflowRunner
    from nodyra.process_isolation import ProcessIsolator


# Severity ranking for run-status aggregation: when parallel nodes finish with
# different statuses, the run reports the *worst* one — failure statuses must
# never be masked by a waiting (approval-paused) node that finishes later.
_STATUS_RANK: dict[RunStatus, int] = {
    RunStatus.success: 0,
    RunStatus.waiting: 1,
    RunStatus.timed_out: 2,
    # A concrete node/runtime error is more informative than a concurrent
    # timeout, so it must win regardless of task-completion order.
    RunStatus.error: 3,
}

# Default per-node output cap (10 MiB).  A node that produces more than this
# raises a ValueError unless the caller explicitly sets a higher limit via
# ``max_node_output_bytes``.  Pass 0 or a negative value to disable the cap.
DEFAULT_MAX_NODE_OUTPUT_BYTES: int = 10 * 1024 * 1024


def _worse_status(a: RunStatus, b: RunStatus) -> RunStatus:
    """The more severe of two run statuses: failure > waiting > success."""
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
    ready = [
        (node_index[nid], nid) for nid, degree in indegree.items() if degree == 0
    ]
    heapq.heapify(ready)
    order: list[str] = []

    while ready:
        _, nid = heapq.heappop(ready)
        order.append(nid)
        for succ in successors[nid]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                heapq.heappush(ready, (node_index[succ], succ))

    if len(order) != len(graph.nodes):
        raise GraphError("Workflow graph has a cycle")
    return order


@dataclass
class _Plan:
    """Dependency-counting schedule over a set of executable node ids.

    ``units`` excludes loop-owned nodes (body + end): a whole loop region is
    one unit, represented by its ``loop_start`` node — the driver populates
    the owned nodes' outputs before the unit completes."""

    units: list[str]                      # executable ids, graph insertion order
    deps: dict[str, set[str]]             # unit -> units it must wait for
    dependents: dict[str, list[str]]      # unit -> units waiting on it
    index: dict[str, int]                 # node id -> graph.nodes position


def _loop_owner_index(loop_regions: dict[str, "LoopRegion"]) -> dict[str, str]:
    """Map every loop-owned node to its immediate (innermost) loop start."""
    owner_by_node: dict[str, str] = {}
    # Nested bodies can also be members of an outer region. The smallest body
    # is the immediate owner; setdefault preserves it when outer regions follow.
    regions = sorted(loop_regions.values(), key=lambda region: len(region.body_ids))
    for region in regions:
        owner_by_node.setdefault(region.end_id, region.start_id)
        for node_id in region.body_ids:
            owner_by_node.setdefault(node_id, region.start_id)
    return owner_by_node


def _owner_unit(
    nid: str,
    owner_by_node: dict[str, str],
    unit_set: set[str],
) -> str | None:
    """The scheduling unit that produces ``nid``'s output when ``nid`` is
    loop-owned: walk to the owning region's start (hopping outward through
    nested regions) until a unit is found."""
    cur = nid
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        owner = owner_by_node.get(cur)
        if owner is None:
            return None
        if owner in unit_set:
            return owner
        cur = owner
    raise GraphError("Nested loop ownership contains a cycle")


def _build_plan(
    graph: WorkflowGraph,
    node_ids: set[str],
    owned: set[str],
    loop_regions: dict[str, "LoopRegion"],
) -> _Plan:
    """Compute the dependency graph for ``node_ids``.

    Edges from nodes outside the executed set are dropped: their outputs are
    either already present (cache / iter_outputs) or absent forever, in which
    case the consumer's skip check ('upstream produced no output') handles it
    at execution time — exactly as it did under level barriers."""
    index = {n.id: i for i, n in enumerate(graph.nodes)}
    unit_set = {nid for nid in node_ids if nid not in owned}
    owner_by_node = _loop_owner_index(loop_regions)
    units = sorted(unit_set, key=index.__getitem__)
    deps: dict[str, set[str]] = {u: set() for u in units}
    for edge in graph.edges:
        if edge.target not in unit_set or edge.source == edge.target:
            continue
        source = edge.source
        if source not in unit_set:
            if source not in owned:
                continue
            source = _owner_unit(source, owner_by_node, unit_set)
            if source is None or source == edge.target:
                continue
        deps[edge.target].add(source)
    dependents: dict[str, list[str]] = {u: [] for u in units}
    for target, sources in deps.items():
        for source in sources:
            dependents[source].append(target)
    return _Plan(units=units, deps=deps, dependents=dependents, index=index)


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
    plan: _Plan,
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
    node_sem: asyncio.Semaphore | None = None,
    type_sems: dict[str, asyncio.Semaphore] | None = None,
    run_deadline: float | None = None,
    process_isolator: "ProcessIsolator | None" = None,
) -> RunStatus:
    """Run the plan's units with dependency counting: each unit starts the
    moment its in-set predecessors complete. Simultaneously-ready units are
    started in graph insertion order. ``node_sem`` (when set) bounds how many
    plain nodes execute concurrently; loop/metanode *drivers* never hold a
    slot (their body nodes acquire their own), so a capped run cannot
    deadlock on nested regions. ``type_sems`` bounds concurrency per node
    type (e.g. max 3 http_request nodes at once). ``run_deadline`` is a
    monotonic timestamp; nodes that haven't started by the deadline are
    marked as timed-out. Returns the worst RunStatus seen."""
    # Lazy: loops.py and metanodes.py import this module at module level,
    # so importing them here (not at the top) breaks the cycle.
    from nodyra.engine.loops import _run_conditional_loop, _run_loop
    from nodyra.engine.metanodes import _run_metanode

    run_status = RunStatus.success

    async def _one(nid: str) -> tuple[str, RunStatus]:
        gn = nodes_by_id[nid]

        # Run-level deadline: skip nodes that haven't started before the clock
        # runs out.  Already-running nodes are allowed to finish (we don't
        # cancel mid-flight), but no new work begins past the deadline.
        if run_deadline is not None and time.monotonic() > run_deadline:
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.error,
                    error="workflow run timed out before this node could start",
                    started_at=time.time(), finished_at=time.time(),
                )
            )
            return nid, RunStatus.timed_out

        # Per-type semaphore, acquired outside the global semaphore so a type-
        # saturated node type doesn't consume a global slot while waiting.
        type_sem = (type_sems or {}).get(gn.type)
        if type_sem is not None:
            await type_sem.acquire()

        try:
            if gn.type == "meta_node":
                st = await _run_metanode(
                    node=gn, incoming=incoming, node_outputs=node_outputs,
                    registry=registry, emit=emit, finish=finish,
                    default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    process_isolator=process_isolator,
                )
                return nid, st
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
                    node_sem=node_sem, type_sems=type_sems,
                    process_isolator=process_isolator,
                )
                return nid, st
            if node_sem is not None:
                async with node_sem:
                    st = await _run_one_node(
                        nid=nid, nodes_by_id=nodes_by_id, incoming=incoming,
                        node_outputs=node_outputs, cache=cache, registry=registry,
                        emit=emit, finish=finish, default_timeouts=default_timeouts,
                        max_node_output_bytes=max_node_output_bytes,
                        pause_on_approval=pause_on_approval,
                        agent_action_resume=agent_action_resume,
                        process_isolator=process_isolator,
                    )
            else:
                st = await _run_one_node(
                    nid=nid, nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=node_outputs, cache=cache, registry=registry,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    process_isolator=process_isolator,
                )
            return nid, st
        finally:
            if type_sem is not None:
                type_sem.release()

    indegree = {u: len(plan.deps[u]) for u in plan.units}
    ready = [u for u in plan.units if indegree[u] == 0]  # insertion order

    # Semaphore-gated worker pool: each worker pulls a node from the shared
    # queue, executes it, and pushes any newly-unblocked dependents.  This
    # avoids the per-batch ``asyncio.wait(FIRST_COMPLETED)`` call that created
    # O(N) event-loop round-trips for N scheduling rounds — the semaphore
    # already bounds concurrency (each node holds a slot).
    total = len(plan.units)
    completed = 0

    # Unbounded queue so a ready node is never blocked from being posted.
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    for nid in ready:
        queue.put_nowait(nid)

    # Workers are cheap coroutines that block on queue.get() when idle.
    # The per-node semaphore (node_sem) still gates actual execution inside
    # _one(); the worker count here just determines how many coroutines can
    # pull from the queue in parallel.  50 is generous for any realistic
    # graph width and avoids scheduling overhead for tiny graphs.
    worker_count = min(total, 50)

    async def _worker() -> None:
        nonlocal completed, run_status
        while True:
            nid = await queue.get()
            try:
                if nid is None:  # sentinel
                    return
                _, st = await _one(nid)
                run_status = _worse_status(run_status, st)
                completed += 1
                for dep in plan.dependents[nid]:
                    indegree[dep] -= 1
                    if indegree[dep] == 0:
                        queue.put_nowait(dep)
            finally:
                # Always decrement — even on exception — so queue.join()
                # never hangs waiting for a task_done() that will never come.
                # The exception propagates naturally; asyncio.gather(*workers)
                # surfaces it after queue.join() drains the remaining work.
                queue.task_done()

    workers = [
        asyncio.create_task(_worker(), name=f"nodyra-scheduler-{index}")
        for index in range(worker_count)
    ]
    join_task = asyncio.create_task(queue.join(), name="nodyra-scheduler-join")
    try:
        # Supervise queue completion and every worker together. Waiting on
        # queue.join() alone deadlocks if one worker raises while queued items
        # remain: no surviving worker can necessarily make those dependencies
        # ready. Surface the first failed/early worker immediately instead.
        done, _ = await asyncio.wait(
            {join_task, *workers}, return_when=asyncio.FIRST_COMPLETED
        )
        if join_task not in done:
            worker = next(task for task in workers if task in done)
            await worker  # re-raise its original exception, including cancellation
            raise GraphError("scheduler worker exited before the queue drained")

        await join_task
        # A final worker and queue.join() may complete in the same loop tick.
        # Observe that worker before posting sentinels so its exception cannot
        # be mistaken for successful queue drainage.
        for worker in workers:
            if worker.done():
                await worker

        for _ in workers:
            queue.put_nowait(None)
        await asyncio.gather(*workers)
    except BaseException:
        # Cancel and observe the complete task group so no node continues after
        # the run has failed or its caller has been cancelled.
        join_task.cancel()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(join_task, *workers, return_exceptions=True)
        raise

    if completed != total:
        raise GraphError(
            "scheduler stalled: a unit's dependencies never completed"
        )  # defensive — _topo_order() in execute() should make this unreachable
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
    max_node_concurrency: int | None = None,
    max_concurrency_per_type: dict[str, int] | None = None,
    run_timeout_seconds: float | None = None,
    process_isolator: "ProcessIsolator | None" = None,
    subworkflow_runner: "SubworkflowRunner | None" = None,
    subworkflow_meta: "SubworkflowMeta | None" = None,
) -> RunResult:
    """Run a workflow graph and return per-node results.

    When ``subworkflow_runner`` is given, ``workflow_call``-style nodes
    resolve children through it: the engine installs a ``workflow_caller``
    adapter that enforces cycle/depth invariants (seeded from
    ``subworkflow_meta``) before delegating to the host resolver.
    """
    if subworkflow_runner is None:
        return await _execute_impl(
            graph, registry, cache=cache, targets=targets, on_event=on_event,
            default_timeouts=default_timeouts,
            max_node_output_bytes=max_node_output_bytes,
            pause_on_approval=pause_on_approval,
            agent_action_resume=agent_action_resume,
            max_node_concurrency=max_node_concurrency,
            max_concurrency_per_type=max_concurrency_per_type,
            run_timeout_seconds=run_timeout_seconds,
            process_isolator=process_isolator,
        )

    # Lazy: subworkflows.py recursively imports execute from this module.
    from nodyra.context import call_chain, workflow_caller
    from nodyra.engine.subworkflows import SubworkflowMeta, make_workflow_caller

    meta = subworkflow_meta or SubworkflowMeta()
    chain_token = call_chain.set(meta.call_chain)
    caller_token = workflow_caller.set(
        make_workflow_caller(
            subworkflow_runner,
            meta,
            registry,
            default_timeouts=default_timeouts,
            process_isolator=process_isolator,
        )
    )
    try:
        return await _execute_impl(
            graph, registry, cache=cache, targets=targets, on_event=on_event,
            default_timeouts=default_timeouts,
            max_node_output_bytes=max_node_output_bytes,
            pause_on_approval=pause_on_approval,
            agent_action_resume=agent_action_resume,
            max_node_concurrency=max_node_concurrency,
            max_concurrency_per_type=max_concurrency_per_type,
            run_timeout_seconds=run_timeout_seconds,
            process_isolator=process_isolator,
        )
    finally:
        workflow_caller.reset(caller_token)
        call_chain.reset(chain_token)


async def _execute_impl(
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
    max_node_concurrency: int | None = None,
    max_concurrency_per_type: dict[str, int] | None = None,
    run_timeout_seconds: float | None = None,
    process_isolator: "ProcessIsolator | None" = None,
) -> RunResult:
    # Lazy: loops.py and metanodes.py import this module at module level,
    # so importing them here (not at the top) breaks the cycle.
    from nodyra.engine.loops import _loop_regions, _validate_loop_regions
    from nodyra.engine.metanodes import _expand_metanodes
    from nodyra.engine.validation import validate_graph

    _install_capture()
    default_timeouts = DEFAULT_NODE_TIMEOUTS if default_timeouts is None else default_timeouts
    # Apply the default output cap (10 MiB) when the caller doesn't set one.
    # 0 or negative means "unlimited" — pass those through as None to disable
    # the check inside _run_one_node.
    if max_node_output_bytes is None:
        max_node_output_bytes = DEFAULT_MAX_NODE_OUTPUT_BYTES
    elif max_node_output_bytes <= 0:
        max_node_output_bytes = None
    cache = cache or {}
    agent_action_resume = agent_action_resume or {}
    # Transparent metanodes are purely organizational: inline them before any
    # planning so the rest of the engine sees an ordinary flat graph.
    graph = _expand_metanodes(graph)
    # Structural validation — empty graph, duplicate ids, self-loops, unknown
    # node types, missing edge references. Must run BEFORE _topo_order so the
    # error message is specific ("Duplicate node id 'x'") rather than a generic
    # cycle error.
    # `validate_graph` also runs schema compatibility checks and returns
    # `ValidationWarning` objects that the runner logs without blocking.
    _graph_warnings = validate_graph(graph, registry)
    for _w in _graph_warnings:
        LOG.warning("validation: node %s: %s", _w.node_id, _w.message)
    target_set = set(targets) if targets is not None else None
    needed = _needed_nodes(graph, target_set, cache)
    _validate_connection_kinds(graph, registry, needed)
    _topo_order(graph)  # cycle detection — raises GraphError
    nodes_by_id = {n.id: n for n in graph.nodes}
    node_sem = (
        asyncio.Semaphore(max_node_concurrency)
        if max_node_concurrency is not None and max_node_concurrency > 0
        else None
    )
    # Per-type concurrency limits: each entry becomes an asyncio.Semaphore.
    # A type not listed is unlimited. 0 or negative → skip (unlimited).
    type_sems: dict[str, asyncio.Semaphore] | None = None
    if max_concurrency_per_type:
        type_sems = {
            typ: asyncio.Semaphore(limit)
            for typ, limit in max_concurrency_per_type.items()
            if limit > 0
        }
        if not type_sems:
            type_sems = None

    incoming: dict[
        str,
        dict[str, tuple[str, str] | list[tuple[str, str]]],
    ] = defaultdict(dict)
    for edge in graph.edges:
        connection = (edge.source, edge.source_output)
        current = incoming[edge.target].get(edge.target_input)
        if current is None:
            incoming[edge.target][edge.target_input] = connection
        elif isinstance(current, list):
            current.append(connection)
        else:
            incoming[edge.target][edge.target_input] = [current, connection]

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

    plan = _build_plan(graph, needed, owned, loop_regions)

    # Compute the run-level deadline once, from ``run_timeout_seconds`` when set.
    # Individual nodes may have their own (shorter) timeouts, but the run itself
    # cannot exceed this wall clock.  Computed here — not inside the worker loop —
    # so the deadline is fixed at the start and isn't affected by queue-wait time.
    run_deadline: float | None = None
    if run_timeout_seconds is not None and run_timeout_seconds > 0:
        run_deadline = time.monotonic() + run_timeout_seconds

    # Dependency-counting execution: each node starts as soon as its in-set
    # predecessors complete. Loop Start nodes are intercepted by
    # _execute_nodes and driven over their body sub-DAG.
    deadline_token = run_deadline_var.set(run_deadline)
    try:
        run_status = await _execute_nodes(
            plan=plan,
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
            node_sem=node_sem,
            type_sems=type_sems,
            run_deadline=run_deadline,
            process_isolator=process_isolator,
        )
    finally:
        run_deadline_var.reset(deadline_token)

    return RunResult(status=run_status, nodes=results)


def run(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
    subworkflow_runner: "SubworkflowRunner | None" = None,
    subworkflow_meta: "SubworkflowMeta | None" = None,
) -> RunResult:
    """Synchronous wrapper around :func:`execute` for scripts and exports."""
    return asyncio.run(
        execute(
            graph,
            registry,
            cache=cache,
            targets=targets,
            subworkflow_runner=subworkflow_runner,
            subworkflow_meta=subworkflow_meta,
        )
    )
