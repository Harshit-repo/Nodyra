"""Loop regions: discovery, validation, and the for-each / while / until
loop drivers that re-run a body sub-DAG per iteration."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noodle.ai_runtime import AgentActionRequest
from noodle.context import iteration_path, org_run_limits
from noodle.models import NodeRunResult, NodeStatus, RunStatus, WorkflowGraph
from noodle.sdk import NodeRegistry

from noodle.engine.scheduler import (
    _ancestors,
    _build_plan,
    _descendants,
    _execute_nodes,
)
from noodle.engine.types import EventCallback, GraphError

if TYPE_CHECKING:
    from noodle.process_isolation import ProcessIsolator


@dataclass(frozen=True)
class LoopRegion:
    start_id: str
    end_id: str
    body_ids: frozenset[str]      # nodes strictly between start and end
    parent_start_id: str | None   # enclosing loop's start_id, or None


def _loop_regions(graph: WorkflowGraph) -> dict[str, LoopRegion]:
    """Map each loop_start node id to its LoopRegion.

    Body = descendants(start) ∩ ancestors(end), excluding start and end.
    Pairing is read from loop_end.params['loop_start_id'].
    """
    starts = [n.id for n in graph.nodes if n.type == "loop_start"]
    ends_for_start: dict[str, str] = {}
    for n in graph.nodes:
        if n.type == "loop_end":
            sid = str(n.params.get("loop_start_id") or "")
            if sid:
                ends_for_start[sid] = n.id

    regions: dict[str, LoopRegion] = {}
    for sid in starts:
        eid = ends_for_start.get(sid)
        if eid is None:
            raise GraphError(f"Loop Start '{sid}' has no paired Loop End")
        body = (_descendants(graph, sid) & _ancestors(graph, eid)) - {sid, eid}
        regions[sid] = LoopRegion(
            start_id=sid, end_id=eid, body_ids=frozenset(body), parent_start_id=None
        )

    # Resolve nesting: a region whose start is inside another region's body is nested.
    for sid, r in list(regions.items()):
        for other_sid, other in regions.items():
            if other_sid != sid and sid in other.body_ids:
                regions[sid] = LoopRegion(
                    start_id=r.start_id, end_id=r.end_id,
                    body_ids=r.body_ids, parent_start_id=other_sid,
                )
                break
    return regions


def _validate_loop_regions(
    graph: WorkflowGraph, regions: dict[str, LoopRegion]
) -> None:
    """Raise GraphError unless every loop region is single-entry/single-exit
    and any two regions are disjoint or strictly nested."""
    for r in regions.values():
        body = set(r.body_ids)
        for e in graph.edges:
            into_body = e.target in body
            from_body = e.source in body
            # Single entry: the only edge entering the body comes from start.
            if into_body and e.source not in body and e.source != r.start_id:
                raise GraphError(
                    f"Loop '{r.start_id}': single entry violated — node "
                    f"'{e.target}' is fed from '{e.source}' outside the loop"
                )
            # Single exit: the only edge leaving the body goes to end.
            if from_body and e.target not in body and e.target != r.end_id:
                raise GraphError(
                    f"Loop '{r.start_id}': single exit violated — body node "
                    f"'{e.source}' wires to '{e.target}' outside the loop"
                )
        # Every body node must reach end (no dead-ends inside the region).
        for nid in body:
            if r.end_id not in (_descendants(graph, nid) | {r.end_id}):
                raise GraphError(
                    f"Loop '{r.start_id}': body node '{nid}' does not reach Loop End"
                )

    # Well-nestedness: regions overlap only by strict containment.
    items = list(regions.values())
    for i, a in enumerate(items):
        a_set = set(a.body_ids) | {a.start_id, a.end_id}
        for b in items[i + 1:]:
            b_set = set(b.body_ids) | {b.start_id, b.end_id}
            inter = a_set & b_set
            if inter and not (a_set <= b_set or b_set <= a_set):
                raise GraphError(
                    f"Loops '{a.start_id}' and '{b.start_id}' partially overlap; "
                    f"loops must be disjoint or strictly nested"
                )


class _LoopRowError(Exception):
    """Raised inside a loop iteration to abort the whole loop (on_error=fail)."""

    def __init__(self, index: int) -> None:
        self.index = index


def _as_loop_rows(value: Any, *, max_rows: int) -> list[Any]:
    """Resolve a loop_start input into an ordered list of rows (one per item)."""
    from noodle.datasets import is_dataset_ref, materialize_dataset_rows
    if is_dataset_ref(value):
        total_cap = max(1, int(max_rows or 10000))
        return materialize_dataset_rows(value, cap=total_cap, allow_truncate=False)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _loop_items(
    value: Any,
    *,
    mode: str = "each",
    batch_size: int = 1,
    group_key: str = "",
    count: int = 0,
    start: int = 0,
    step: int = 1,
    max_rows: int = 10000,
) -> list[Any]:
    """Resolve a loop_start input into the ordered list of iteration *units*.

    The unit shape depends on ``mode``:
      * each   -> one row per unit
      * batch  -> a list of up to ``batch_size`` rows per unit (non-overlapping)
      * group  -> {"key": k, "rows": [...]} per distinct ``group_key`` value
      * range  -> the integers start, start+step, ... (``count`` of them)
      * window -> overlapping sliding windows of ``batch_size`` rows, sliding
                  by ``step``; only full windows are produced
    """
    if mode == "range":
        n = max(0, int(count or 0))
        st = int(step or 1) or 1
        return [int(start) + k * st for k in range(n)]

    rows = _as_loop_rows(value, max_rows=max_rows)

    if mode == "batch":
        size = max(1, int(batch_size or 1))
        return [rows[i : i + size] for i in range(0, len(rows), size)]

    if mode == "window":
        size = max(1, int(batch_size or 1))
        slide = max(1, int(step or 1))
        return [rows[i : i + size] for i in range(0, len(rows) - size + 1, slide)]

    if mode == "group":
        groups: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []
        for row in rows:
            key = row.get(group_key) if isinstance(row, dict) else None
            if key not in groups:
                groups[key] = {"key": key, "rows": []}
                order.append(key)
            groups[key]["rows"].append(row)
        return [groups[k] for k in order]

    # "each" (and any unknown mode) -> one row per unit.
    return rows


async def _run_loop(
    *,
    region: "LoopRegion",
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
    process_isolator: "ProcessIsolator | None" = None,
) -> RunStatus:
    """Drive a loop region: resolve its input into items and run the body
    sub-DAG once per item, collecting each iteration's value flowing into
    Loop End in item order."""
    start = nodes_by_id[region.start_id]
    mode = str(start.params.get("mode", "each") or "each")
    concurrency = max(1, int(start.params.get("concurrency", 1) or 1))
    on_error = str(start.params.get("on_error", "fail") or "fail")
    max_rows = int(start.params.get("max_rows", 10000) or 10000)
    batch_size = int(start.params.get("batch_size", 1) or 1)
    group_key = str(start.params.get("group_key", "") or "")
    count = int(start.params.get("count", 0) or 0)
    range_start = int(start.params.get("start", 0) or 0)
    step = int(start.params.get("step", 1) or 1)

    # The loop_start input is the value on its 'input' port (from the graph).
    # range mode ignores the input.
    start_in = incoming.get(region.start_id, {})
    raw_input: Any = None
    if "input" in start_in:
        src, out = start_in["input"]
        raw_input = (node_outputs.get(src) or {}).get(out)
    items = _loop_items(
        raw_input, mode=mode, batch_size=batch_size, group_key=group_key,
        count=count, start=range_start, step=step, max_rows=max_rows,
    )

    if len(items) > max_rows:
        node_outputs[region.end_id] = {"results": [], "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error,
            error=f"loop received {len(items)} rows but max_rows is {max_rows}",
        ))
        return RunStatus.error

    # Multi-tenancy C5: the org's loop-iteration ceiling beats the node's own
    # max_rows. Reject (never truncate) so quota pressure is always visible.
    org_loop_cap = int((org_run_limits.get() or {}).get("max_loop_iterations") or 0)
    if org_loop_cap and len(items) > org_loop_cap:
        node_outputs[region.end_id] = {"results": [], "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error,
            error=(
                f"loop received {len(items)} rows but this organization's "
                f"iteration cap is {org_loop_cap}"
            ),
        ))
        return RunStatus.error

    # Skip-set for the body run: only nodes owned by *directly nested* loops
    # (their own driver handles them). This loop's own direct body nodes run.
    child_owned: set[str] = set()
    for other in loop_regions.values():
        if other.parent_start_id == region.start_id:
            child_owned |= set(other.body_ids)
            child_owned.add(other.end_id)

    body_plan = _build_plan(graph, set(region.body_ids), child_owned, loop_regions)
    # The node + port feeding loop_end.input, captured per iteration.
    end_in = incoming.get(region.end_id, {}).get("input")

    # Reduce: thread an accumulator across the (fixed) for-each units. Sequential
    # by nature; the `item` port carries {"acc": <accumulator>, "item": <unit>}
    # and the value into Loop End becomes the next accumulator.
    if bool(start.params.get("accumulate", False)):
        from noodle.expr import build_context, evaluate
        acc = evaluate(
            start.params.get("initial"),
            build_context(node_outputs=node_outputs),
        )
        err = _expr_error(acc)
        if err is not None:
            node_outputs[region.end_id] = {"results": None, "errors": []}
            await finish(NodeRunResult(
                node_id=region.end_id, status=NodeStatus.error,
                error=f"loop initial state {err}",
            ))
            return RunStatus.error
        for i, unit in enumerate(items):
            path_token = iteration_path.set(iteration_path.get() + (i,))
            try:
                iter_outputs = dict(node_outputs)
                iter_outputs[region.start_id] = {
                    "item": {"acc": acc, "item": unit}, "index": i, "state": acc,
                }
                st = await _execute_nodes(
                    plan=body_plan, graph=graph, registry=registry,
                    nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=iter_outputs, cache=cache,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=child_owned,
                    node_sem=node_sem,
                    process_isolator=process_isolator,
                )
            finally:
                iteration_path.reset(path_token)
            if st is RunStatus.error:
                node_outputs[region.end_id] = {"results": None, "errors": []}
                await finish(NodeRunResult(
                    node_id=region.end_id, status=NodeStatus.error,
                    error=f"loop reduce iteration {i} failed",
                ))
                return RunStatus.error
            if end_in is not None:
                esrc, eout = end_in
                acc = (iter_outputs.get(esrc) or {}).get(eout)
            iter_outputs.clear()
        out = {"results": acc, "errors": []}
        node_outputs[region.end_id] = out
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.success, outputs=out,
        ))
        return RunStatus.success

    collected: list[tuple[int, Any]] = []
    errors: list[dict] = []
    sem = asyncio.Semaphore(concurrency)

    async def _one_iteration(i: int, item: Any) -> None:
        async with sem:
            path_token = iteration_path.set(iteration_path.get() + (i,))
            try:
                iter_outputs = dict(node_outputs)  # inherit upstream values
                iter_outputs[region.start_id] = {"item": item, "index": i}
                st = await _execute_nodes(
                    plan=body_plan, graph=graph, registry=registry,
                    nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=iter_outputs, cache=cache,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=child_owned,
                    node_sem=node_sem,
                    process_isolator=process_isolator,
                )
                if st is RunStatus.error:
                    if on_error == "fail":
                        raise _LoopRowError(i)
                    errors.append({"index": i, "error": "row failed", "input": item})
                    return
                value = None
                if end_in is not None:
                    esrc, eout = end_in
                    value = (iter_outputs.get(esrc) or {}).get(eout)
                collected.append((i, value))
                # Release refs to upstream outputs so concurrent iterations
                # don't hold N copies of large upstream values simultaneously.
                iter_outputs.clear()
            finally:
                iteration_path.reset(path_token)

    if concurrency == 1:
        for i, item in enumerate(items):
            try:
                await _one_iteration(i, item)
            except _LoopRowError as exc:
                node_outputs[region.end_id] = {"results": [], "errors": errors}
                await finish(NodeRunResult(
                    node_id=region.end_id, status=NodeStatus.error,
                    error=f"loop row {exc.index} failed (on_error=fail)",
                ))
                return RunStatus.error
    else:
        # ENG-1: drive iterations as explicit tasks so an on_error="fail" abort
        # can CANCEL the still-in-flight ones. A bare ``asyncio.gather`` raises
        # the first _LoopRowError to us but leaves the other iteration coroutines
        # running detached — they keep executing body nodes (emitting events,
        # consuming compute, mutating shared state) for a run we've already marked
        # failed. Same orphaned-task class as REL-2.
        tasks = [
            asyncio.ensure_future(_one_iteration(i, it))
            for i, it in enumerate(items)
        ]
        try:
            await asyncio.gather(*tasks)
        except _LoopRowError as exc:
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Drain the cancellations so no iteration runs past this point.
            await asyncio.gather(*tasks, return_exceptions=True)
            node_outputs[region.end_id] = {"results": [], "errors": errors}
            await finish(NodeRunResult(
                node_id=region.end_id, status=NodeStatus.error,
                error=f"loop row {exc.index} failed (on_error=fail)",
            ))
            return RunStatus.error

    collected.sort(key=lambda t: t[0])
    values = [v for _, v in collected]
    end = nodes_by_id[region.end_id]
    if str(end.params.get("output_mode", "records") or "records") == "dataset":
        from noodle.datasets import dataset_from_records
        rows = [v if isinstance(v, dict) else {"result": v} for v in values]
        results_out: Any = dataset_from_records(rows, name="loop_output.parquet")
    else:
        results_out = values
    out = {"results": results_out, "errors": errors}
    node_outputs[region.end_id] = out
    await finish(NodeRunResult(
        node_id=region.end_id, status=NodeStatus.success, outputs=out,
    ))
    return RunStatus.success


def _expr_error(value: Any) -> str | None:
    """Return the message if ``value`` is an expression-eval error sentinel."""
    if isinstance(value, str) and value.startswith("[expr error"):
        return value
    return None


async def _run_conditional_loop(
    *,
    region: "LoopRegion",
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
    process_isolator: "ProcessIsolator | None" = None,
) -> RunStatus:
    """Drive a while/until loop: thread an accumulator (state) across iterations,
    re-checking an expression condition each time, until it stops or a safety cap
    is hit. Sequential by construction (each iteration depends on the previous
    state). Delivers the v2 accumulator + break behavior."""
    from noodle.expr import _wrap, build_context, evaluate

    start = nodes_by_id[region.start_id]
    end = nodes_by_id[region.end_id]
    mode = str(start.params.get("mode", "while") or "while")
    max_iterations = max(0, int(start.params.get("max_iterations", 1000) or 1000))
    # Multi-tenancy C5: the org's iteration ceiling clamps the node's own cap
    # (while/until loops have no row count to reject up front).
    org_loop_cap = int((org_run_limits.get() or {}).get("max_loop_iterations") or 0)
    if org_loop_cap and (max_iterations == 0 or max_iterations > org_loop_cap):
        max_iterations = org_loop_cap
    on_max = str(start.params.get("on_max_iterations", "fail") or "fail")
    condition_expr = start.params.get("condition", "")
    conditional_output = str(
        end.params.get("conditional_output", "final_state") or "final_state"
    )

    async def _fail(message: str) -> RunStatus:
        node_outputs[region.end_id] = {"results": None, "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error, error=message,
        ))
        return RunStatus.error

    def _ctx(state: Any, i: int) -> dict[str, Any]:
        ctx = build_context(first_input=state, node_outputs=node_outputs)
        ctx["state"] = _wrap(state)
        ctx["index"] = i
        return ctx

    # Seed state from the `initial` param (literal or expression).
    state = evaluate(start.params.get("initial"), _ctx(None, 0))
    err = _expr_error(state)
    if err is not None:
        return await _fail(f"loop initial state {err}")

    child_owned: set[str] = set()
    for other in loop_regions.values():
        if other.parent_start_id == region.start_id:
            child_owned |= set(other.body_ids)
            child_owned.add(other.end_id)

    body_plan = _build_plan(graph, set(region.body_ids), child_owned, loop_regions)
    end_in = incoming.get(region.end_id, {}).get("input")

    states: list[Any] = []
    i = 0
    hit_cap = False
    while True:
        if i >= max_iterations:
            hit_cap = True
            break
        keep = evaluate(condition_expr, _ctx(state, i)) if condition_expr else False
        err = _expr_error(keep)
        if err is not None:
            return await _fail(f"loop condition {err}")
        go = bool(keep) if mode == "while" else (not bool(keep))
        if not go:
            break

        path_token = iteration_path.set(iteration_path.get() + (i,))
        try:
            iter_outputs = dict(node_outputs)
            iter_outputs[region.start_id] = {"item": state, "index": i, "state": state}
            st = await _execute_nodes(
                plan=body_plan, graph=graph, registry=registry,
                nodes_by_id=nodes_by_id, incoming=incoming,
                node_outputs=iter_outputs, cache=cache,
                emit=emit, finish=finish, default_timeouts=default_timeouts,
                max_node_output_bytes=max_node_output_bytes,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
                loop_regions=loop_regions, owned=child_owned,
                node_sem=node_sem,
                process_isolator=process_isolator,
            )
        finally:
            iteration_path.reset(path_token)

        # A body failure makes the next state uncomputable; abort rather than
        # spin forever on the same state (on_error=continue does not apply here).
        if st is RunStatus.error:
            return await _fail(f"loop iteration {i} failed")

        new_state = None
        if end_in is not None:
            esrc, eout = end_in
            new_state = (iter_outputs.get(esrc) or {}).get(eout)
        state = new_state
        if conditional_output == "all_states":
            states.append(state)
        iter_outputs.clear()
        i += 1

    logs: list[str] = []
    if hit_cap:
        if on_max == "fail":
            return await _fail(
                f"loop exceeded max_iterations ({max_iterations})"
            )
        logs.append(
            f"loop stopped at the max_iterations cap ({max_iterations}); "
            "emitting the current state"
        )

    if conditional_output == "all_states":
        results_out: Any = {"final": state, "states": states}
    else:
        results_out = state
    out = {"results": results_out, "errors": []}
    node_outputs[region.end_id] = out
    await finish(NodeRunResult(
        node_id=region.end_id, status=NodeStatus.success, outputs=out, logs=logs,
    ))
    return RunStatus.success
