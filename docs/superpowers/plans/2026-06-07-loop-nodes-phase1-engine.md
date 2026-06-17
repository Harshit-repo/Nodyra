# Loop Nodes — Phase 1 (Core Engine + Nodes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add native in-graph looping — `loop_start` / `loop_end` nodes whose body sub-DAG the engine executes once per item — working end-to-end in-process and in the subprocess runner, fully unit-tested. No DB/UI changes (those are Phases 2–3).

**Architecture:** The two loop nodes are **declarative** (manifest/ports/params only); the engine never calls their functions in the normal path. Instead `execute()` detects loop **regions** (single-entry/single-exit sub-DAGs between a Loop Start and its paired Loop End), excludes body+end nodes from the main pass, and a **driver** runs the body sub-DAG once per item via a shared `_run_subgraph` helper (the same per-node execution logic used by the top-level pass). Loop End's outputs are written by the driver; downstream reads them like any node output. Sequential by default, bounded-concurrent optionally; nesting via a recursive driver and an iteration **path**.

**Tech Stack:** Python 3.12, asyncio, pytest (`.venv/Scripts/pytest`), Pydantic models, the existing `@node` SDK decorator.

**Spec:** `docs/superpowers/specs/2026-06-07-loop-nodes-design.md`

**Phase-1 deviations from spec (deliberate, YAGNI):**
- `loop_end` has an `output_mode` toggle (`records` default | `dataset`). Core stays decoupled from the nodes layer via a **registered dataset-writer hook** mirroring the existing `register_materializer` pattern (Task 11). In `records` mode `results` is a list; in `dataset` mode it is a DatasetRef (each result must be a dict).
- Per-iteration *events* and `NodeRun.iteration_path` *persistence* are **Phase 2**. Phase 1 emits normal per-node events for the **last** iteration only (enough for in-process correctness) and proves the driver via direct `execute()` assertions.

---

## File Structure

- `packages/nodes/noodle_nodes/builtin.py` — **modify**: add `loop_start` and `loop_end` declarative nodes; mark `loop_over_items` deprecated.
- `packages/core/noodle/engine.py` — **modify**: add `LoopRegion` dataclass, `_loop_regions()`, `_validate_loop_regions()`, the `_run_subgraph()` helper (extracted from the current `_run_node` logic), and the `_run_loop()` driver wired into `execute()`.
- `packages/core/tests/test_loops.py` — **create**: all engine-level loop tests.
- `packages/nodes/tests/test_loop_nodes_registration.py` — **create**: node registration + deprecation tests.

Run tests with: `D:/noodle/.venv/Scripts/pytest` (the `.venv` has all deps; `.venv-test` is missing `httpx`).

---

## Task 1: Declare the `loop_start` and `loop_end` nodes

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py`
- Test: `packages/nodes/tests/test_loop_nodes_registration.py`

- [ ] **Step 1: Write the failing test**

Create `packages/nodes/tests/test_loop_nodes_registration.py`:

```python
"""Loop node registration + deprecation."""
from __future__ import annotations

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.sdk import registry


def test_loop_start_registered_with_ports_and_params():
    nd = registry.get("loop_start")
    m = nd.manifest
    assert [o.name for o in m.outputs] == ["item", "index"]
    pnames = {p.name for p in m.params}
    assert {"concurrency", "on_error", "max_rows"} <= pnames


def test_loop_end_registered_with_outputs_and_hidden_pair_param():
    nd = registry.get("loop_end")
    m = nd.manifest
    assert [o.name for o in m.outputs] == ["results", "errors"]
    pnames = {p.name for p in m.params}
    assert {"loop_start_id", "output_mode"} <= pnames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:/noodle/.venv/Scripts/pytest packages/nodes/tests/test_loop_nodes_registration.py -v`
Expected: FAIL — `KeyError: 'loop_start'` (node not registered).

- [ ] **Step 3: Add the node definitions**

In `packages/nodes/noodle_nodes/builtin.py`, near the other Logic nodes (e.g. after `map_group_node`), add:

```python
@node(
    name="Loop Start",
    id="loop_start",
    category="Logic",
    icon="repeat",
    outputs=["item", "index"],
    params={
        "concurrency": {
            "description": "How many rows to process at once (default 1 = one at a time).",
        },
        "on_error": {
            "description": "fail = stop the loop on the first failing row; continue = collect errors and keep going.",
            "choices": ["fail", "continue"],
        },
        "max_rows": {
            "description": "Maximum rows allowed before the loop fails (default 10000). Never silently truncates.",
        },
    },
)
def loop_start(
    input: Any = None,
    concurrency: int = 1,
    on_error: str = "fail",
    max_rows: int = 10000,
) -> dict[str, Any]:
    """Start of a loop region. The engine drives this node and runs the
    nodes between it and the paired Loop End once per row; this function is
    never called directly."""
    raise RuntimeError(
        "loop_start is executed by the engine's loop driver, not called directly"
    )


@node(
    name="Loop End",
    id="loop_end",
    category="Logic",
    icon="repeat",
    outputs=["results", "errors"],
    params={
        "loop_start_id": {
            "widget": "hidden",
            "description": "Auto-managed id of the paired Loop Start.",
        },
        "output_mode": {
            "description": "records = a list of each row's result; dataset = a DatasetRef (each result must be a dict / object).",
            "choices": ["records", "dataset"],
            "display_name": "Output",
        },
    },
)
def loop_end(
    input: Any = None, loop_start_id: str = "", output_mode: str = "records"
) -> dict[str, Any]:
    """End of a loop region. The engine collects each row's value here; this
    function is never called directly."""
    raise RuntimeError(
        "loop_end is executed by the engine's loop driver, not called directly"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `D:/noodle/.venv/Scripts/pytest packages/nodes/tests/test_loop_nodes_registration.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_loop_nodes_registration.py
git commit -m "feat(nodes): declare loop_start/loop_end (declarative loop region nodes)"
```

---

## Task 2: Region detection — `_loop_regions()`

**Files:**
- Modify: `packages/core/noodle/engine.py`
- Test: `packages/core/tests/test_loops.py`

A region is the SESE sub-DAG between a Loop Start and its paired Loop End. Body = nodes that are
descendants of Start **and** ancestors of End. Pairing is by `loop_end.params["loop_start_id"]`.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_loops.py`:

```python
"""Engine-level loop region detection, validation, and execution."""
from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers loop_start/loop_end/code
from noodle.engine import _loop_regions, _validate_loop_regions, execute
from noodle.models import WorkflowGraph
from noodle.sdk import registry


def _g(nodes, edges) -> WorkflowGraph:
    return WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})


def _n(nid, ntype, params=None):
    return {"id": nid, "type": ntype, "params": params or {}, "position": {"x": 0, "y": 0}}


def _e(src, tgt, src_out="main", tgt_in="input"):
    return {"id": f"{src}->{tgt}", "source": src, "source_output": src_out,
            "target": tgt, "target_input": tgt_in}


def test_loop_regions_simple_body():
    # start -> body(code) -> end
    g = _g(
        [
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("s", "b", src_out="item"),
            _e("b", "e"),
        ],
    )
    regions = _loop_regions(g)
    assert set(regions.keys()) == {"s"}
    r = regions["s"]
    assert r.start_id == "s"
    assert r.end_id == "e"
    assert r.body_ids == {"b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py::test_loop_regions_simple_body -v`
Expected: FAIL — `ImportError: cannot import name '_loop_regions'`.

- [ ] **Step 3: Implement `LoopRegion` + `_loop_regions`**

In `packages/core/noodle/engine.py`, after `_predecessors` (around line 306), add:

```python
@dataclass(frozen=True)
class LoopRegion:
    start_id: str
    end_id: str
    body_ids: frozenset[str]      # nodes strictly between start and end
    parent_start_id: str | None   # enclosing loop's start_id, or None


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


def _loop_regions(graph: WorkflowGraph) -> dict[str, LoopRegion]:
    """Map each loop_start node id to its LoopRegion.

    Body = descendants(start) ∩ ancestors(end), excluding start and end.
    Pairing is read from loop_end.params['loop_start_id'].
    """
    nodes_by_id = {n.id: n for n in graph.nodes}
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
```

Ensure `from dataclasses import dataclass` is imported at the top of `engine.py` (add if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py::test_loop_regions_simple_body -v`
Expected: PASS.

- [ ] **Step 5: Add a branched-body test and a nesting test**

Append to `test_loops.py`:

```python
def test_loop_regions_branched_body():
    # s -> a -> {b, c} -> m -> e   (branch + merge inside the loop)
    g = _g(
        [
            _n("s", "loop_start"),
            _n("a", "code", {"code": "output = input"}),
            _n("b", "code", {"code": "output = input"}),
            _n("c", "code", {"code": "output = input"}),
            _n("m", "merge"),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("s", "a", src_out="item"), _e("a", "b"), _e("a", "c"),
            _e("b", "m", tgt_in="input"), _e("c", "m", tgt_in="input2"),
            _e("m", "e"),
        ],
    )
    r = _loop_regions(g)["s"]
    assert r.body_ids == {"a", "b", "c", "m"}


def test_loop_regions_nesting_parent_link():
    # outer s1 ... inner s2/e2 ... e1
    g = _g(
        [
            _n("s1", "loop_start"),
            _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("s1", "s2", src_out="item", tgt_in="input"),
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),
        ],
    )
    regions = _loop_regions(g)
    assert regions["s2"].parent_start_id == "s1"
    assert regions["s1"].parent_start_id is None
    assert "s2" in regions["s1"].body_ids and "b" in regions["s1"].body_ids
```

(The `merge` node is an existing builtin; if its id differs, use any two-input builtin — confirm with `registry.get("merge")`.)

- [ ] **Step 6: Run tests**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add packages/core/noodle/engine.py packages/core/tests/test_loops.py
git commit -m "feat(engine): detect loop regions (SESE body + nesting parent links)"
```

---

## Task 3: Region validation — `_validate_loop_regions()`

**Files:**
- Modify: `packages/core/noodle/engine.py`
- Test: `packages/core/tests/test_loops.py`

Enforce the SESE + well-nestedness rules so a malformed loop fails fast with a clear message.

- [ ] **Step 1: Write the failing tests**

Append to `test_loops.py`:

```python
def test_validate_rejects_edge_crossing_into_body_from_outside():
    # 'x' (outside) wires directly into body node 'b' — not allowed.
    g = _g(
        [
            _n("s", "loop_start"), _n("x", "code", {"code": "output = 1"}),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("s", "b", src_out="item"), _e("x", "b", tgt_in="input2"),
            _e("b", "e"),
        ],
    )
    with pytest.raises(Exception) as ei:
        _validate_loop_regions(g, _loop_regions(g))
    assert "single entry" in str(ei.value).lower() or "outside" in str(ei.value).lower()


def test_validate_rejects_body_node_leaking_out():
    # body node 'b' wires to 'y' outside the region (not via loop_end).
    g = _g(
        [
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
            _n("y", "code", {"code": "output = input"}),
        ],
        [
            _e("s", "b", src_out="item"), _e("b", "e"), _e("b", "y"),
        ],
    )
    with pytest.raises(Exception) as ei:
        _validate_loop_regions(g, _loop_regions(g))
    assert "single exit" in str(ei.value).lower() or "outside" in str(ei.value).lower()


def test_validate_rejects_missing_pair():
    g = _g([_n("e", "loop_end", {"loop_start_id": "nope"})], [])
    with pytest.raises(Exception):
        _loop_regions(g)  # missing start raises in detection


def test_validate_accepts_well_nested():
    g = _g(
        [
            _n("s1", "loop_start"), _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("s1", "s2", src_out="item"), _e("s2", "b", src_out="item"),
            _e("b", "e2"), _e("e2", "e1", src_out="results"),
        ],
    )
    _validate_loop_regions(g, _loop_regions(g))  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k validate -v`
Expected: FAIL — `_validate_loop_regions` not defined.

- [ ] **Step 3: Implement `_validate_loop_regions`**

In `engine.py`, after `_loop_regions`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k validate -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/engine.py packages/core/tests/test_loops.py
git commit -m "feat(engine): validate loop regions (SESE + well-nestedness)"
```

---

## Task 4: Extract `_run_subgraph` from the per-node execution logic

**Files:**
- Modify: `packages/core/noodle/engine.py`
- Test: `packages/core/tests/test_loops.py` (indirect — Task 5 exercises it; here we guard the refactor with the full existing suite)

The current per-node execution lives in the `_run_node` closure inside `execute()`. To run the body
once per iteration we need that same logic callable against a **per-iteration** `node_outputs`
scratch dict and a restricted node set. Factor the body of `_run_node` into a module-level
coroutine the driver can call.

- [ ] **Step 1: Characterize current behavior (safety net)**

Run the full engine suite and record the baseline (must stay green after the refactor):

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/ -q`
Expected: all pass. Note the count.

- [ ] **Step 2: Introduce `_execute_nodes` used by `execute()`**

Refactor `execute()` so the level loop delegates to a reusable coroutine. Replace the inline
`_run_node` closure with a module-level `_execute_nodes` that takes everything it needs explicitly:

```python
async def _execute_nodes(
    *,
    node_ids: set[str],            # the nodes to consider (the "needed" set / body set)
    levels: list[list[str]],       # topo levels restricted to node_ids
    graph: WorkflowGraph,
    registry: NodeRegistry,
    nodes_by_id: dict[str, GraphNode],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],   # scratch; seeded by caller
    results: dict[str, NodeRunResult],
    emit: EventCallback,
    finish,                        # async fn(NodeRunResult) -> None
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
    loop_regions: dict[str, LoopRegion],
    owned: set[str],               # body+end ids owned by some loop (skipped here)
) -> RunStatus:
    """Run `node_ids` in `levels` order against `node_outputs`. Returns the
    worst RunStatus seen. Loop Start nodes are intercepted and driven via
    `_run_loop`; owned nodes are skipped (their loop populates them)."""
    run_status = RunStatus.success
    for level in levels:
        async def _one(nid: str) -> None:
            nonlocal run_status
            if nid in owned:
                return
            gn = nodes_by_id[nid]
            if gn.type == "loop_start" and nid in loop_regions:
                st = await _run_loop(
                    region=loop_regions[nid],
                    graph=graph, registry=registry, nodes_by_id=nodes_by_id,
                    incoming=incoming, node_outputs=node_outputs, results=results,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=owned,
                )
                if st is RunStatus.error:
                    run_status = RunStatus.error
                return
            st = await _run_one_node(
                nid=nid, graph=graph, registry=registry, nodes_by_id=nodes_by_id,
                incoming=incoming, node_outputs=node_outputs, results=results,
                emit=emit, finish=finish, default_timeouts=default_timeouts,
                max_node_output_bytes=max_node_output_bytes,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
            )
            if st is RunStatus.error:
                run_status = RunStatus.error
        await asyncio.gather(*[_one(nid) for nid in level if nid in node_ids])
    return run_status
```

Then move the **entire current body** of the existing `_run_node` closure into a module-level
`async def _run_one_node(...) -> RunStatus` with the same parameters as above (minus loop fields),
returning `RunStatus.error` when it sets an error and `RunStatus.success`/`waiting` otherwise.
Mechanical extraction: every variable the closure captured (`node_outputs`, `results`, `emit`,
`finish`, `registry`, `default_timeouts`, etc.) becomes a parameter. Keep all existing logic
(cache hit, skip, disabled, tool_mode, kwargs build, expr eval, retries, timeout, approval, error
handling) byte-for-byte; only the variable *sources* change from closure to parameters.

Have `execute()` build `levels`, `incoming`, `node_outputs`, define `emit`/`finish` as today, then
call `_execute_nodes(...)` once with `node_ids=needed`, `owned=<body+end ids>`,
`loop_regions=<from Task 2/3>`.

- [ ] **Step 3: Run the full suite (refactor must be behavior-preserving)**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/ -q`
Expected: same pass count as Step 1 (no regressions). `_run_loop` can be a temporary stub
(`async def _run_loop(**_): return RunStatus.success`) until Task 5 — no graph uses a loop yet.

- [ ] **Step 4: Commit**

```bash
git add packages/core/noodle/engine.py
git commit -m "refactor(engine): extract _run_one_node/_execute_nodes for reuse by loop driver"
```

---

## Task 5: The loop driver — sequential iteration + collection

**Files:**
- Modify: `packages/core/noodle/engine.py`
- Test: `packages/core/tests/test_loops.py`

- [ ] **Step 1: Write the failing test**

Append to `test_loops.py`:

```python
async def test_loop_runs_body_once_per_item_in_order():
    # items [1,2,3] -> body doubles -> results [2,4,6]
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("trig", "s"),
            _e("s", "b", src_out="item"),
            _e("b", "e"),
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [2, 4, 6]
    assert result.nodes["e"].outputs["errors"] == []
```

(Confirm `manual_trigger`'s param key for its payload — it is `data` in existing tests; adjust if
the trigger uses a different key.)

- [ ] **Step 2: Run test to verify it fails**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k once_per_item -v`
Expected: FAIL (stub `_run_loop` leaves `e` unset / wrong output).

- [ ] **Step 3: Implement `_run_loop` (sequential)**

Replace the stub `_run_loop` with:

```python
def _loop_items(value: Any, *, max_rows: int) -> list[Any]:
    """Resolve a loop_start input into an ordered list of items."""
    from noodle.datasets import is_dataset_ref, materialize_dataset_rows
    if is_dataset_ref(value):
        total_cap = max(1, int(max_rows or 10000))
        return materialize_dataset_rows(value, cap=total_cap, allow_truncate=False)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


async def _run_loop(
    *, region: LoopRegion, graph: WorkflowGraph, registry: NodeRegistry,
    nodes_by_id: dict, incoming: dict, node_outputs: dict, results: dict,
    emit, finish, default_timeouts, max_node_output_bytes, pause_on_approval,
    agent_action_resume, loop_regions, owned,
) -> RunStatus:
    start = nodes_by_id[region.start_id]
    # Resolve params (defaults match the node manifest).
    concurrency = max(1, int(start.params.get("concurrency", 1) or 1))
    on_error = str(start.params.get("on_error", "fail") or "fail")
    max_rows = int(start.params.get("max_rows", 10000) or 10000)

    # The loop_start input is the value on its 'input' port (from the main graph).
    start_in = incoming.get(region.start_id, {})
    items: list[Any] = []
    if "input" in start_in:
        src, out = start_in["input"]
        items = _loop_items((node_outputs.get(src) or {}).get(out), max_rows=max_rows)

    if len(items) > max_rows:
        node_outputs[region.end_id] = {"results": [], "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error,
            error=f"loop received {len(items)} rows but max_rows is {max_rows}",
        ))
        return RunStatus.error

    # Topo levels restricted to the body, computed once.
    body_levels = _restricted_levels(graph, region.body_ids)
    # The node + port feeding loop_end.input, captured per iteration.
    end_in = incoming.get(region.end_id, {}).get("input")

    collected: list[tuple[int, Any]] = []
    errors: list[dict] = []
    sem = asyncio.Semaphore(concurrency)

    async def _one_iteration(i: int, item: Any) -> None:
        async with sem:
            iter_outputs = dict(node_outputs)  # inherit upstream values
            iter_outputs[region.start_id] = {"item": item, "index": i}
            iter_results: dict[str, NodeRunResult] = {}
            st = await _execute_nodes(
                node_ids=set(region.body_ids),
                levels=body_levels, graph=graph, registry=registry,
                nodes_by_id=nodes_by_id, incoming=incoming,
                node_outputs=iter_outputs, results=iter_results,
                emit=emit, finish=finish, default_timeouts=default_timeouts,
                max_node_output_bytes=max_node_output_bytes,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
                loop_regions=loop_regions, owned=owned,
            )
            # Persist last-iteration results for the inspector baseline (Phase 1).
            results.update(iter_results)
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

    if concurrency == 1:
        for i, item in enumerate(items):
            try:
                await _one_iteration(i, item)
            except _LoopRowError:
                node_outputs[region.end_id] = {"results": [], "errors": errors}
                await finish(NodeRunResult(
                    node_id=region.end_id, status=NodeStatus.error,
                    error=f"loop row {i} failed (on_error=fail)",
                ))
                return RunStatus.error
    else:
        try:
            await asyncio.gather(*[_one_iteration(i, it) for i, it in enumerate(items)])
        except _LoopRowError as exc:
            node_outputs[region.end_id] = {"results": [], "errors": errors}
            await finish(NodeRunResult(
                node_id=region.end_id, status=NodeStatus.error,
                error=f"loop row {exc.index} failed (on_error=fail)",
            ))
            return RunStatus.error

    collected.sort(key=lambda t: t[0])
    out = {"results": [v for _, v in collected], "errors": errors}
    node_outputs[region.end_id] = out
    await finish(NodeRunResult(
        node_id=region.end_id, status=NodeStatus.success, outputs=out,
    ))
    return RunStatus.success
```

Add the helper exception and the restricted-levels helper:

```python
class _LoopRowError(Exception):
    def __init__(self, index: int) -> None:
        self.index = index


def _restricted_levels(graph: WorkflowGraph, node_ids: frozenset[str]) -> list[list[str]]:
    """Topo levels over the induced subgraph on `node_ids` only."""
    node_index = {n.id: i for i, n in enumerate(graph.nodes)}
    preds = {nid: set() for nid in node_ids}
    for e in graph.edges:
        if e.source in node_ids and e.target in node_ids:
            preds[e.target].add(e.source)
    indeg = {nid: len(p) for nid, p in preds.items()}
    succ: dict[str, set[str]] = defaultdict(set)
    for nid, p in preds.items():
        for s in p:
            succ[s].add(nid)
    remaining = set(node_ids)
    levels: list[list[str]] = []
    while remaining:
        level = sorted([n for n in remaining if indeg[n] == 0], key=lambda n: node_index[n])
        if not level:
            raise GraphError("cycle inside loop body")
        levels.append(level)
        for n in level:
            remaining.remove(n)
            for s in succ[n]:
                indeg[s] -= 1
    return levels
```

- [ ] **Step 4: Wire region detection + validation + owned-set into `execute()`**

In `execute()`, after `nodes_by_id` is built and before running nodes, add:

```python
loop_regions = _loop_regions(graph)
if loop_regions:
    _validate_loop_regions(graph, loop_regions)
owned: set[str] = set()
for r in loop_regions.values():
    owned |= set(r.body_ids)
    owned.add(r.end_id)
```

Pass `loop_regions` and `owned` into the top-level `_execute_nodes(...)` call. (Non-loop graphs get
empty `loop_regions`/`owned`, so behavior is unchanged.)

- [ ] **Step 5: Run the test**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k once_per_item -v`
Expected: PASS.

- [ ] **Step 6: Run the full core suite (no regressions)**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/ -q`
Expected: all pass (same baseline as Task 4).

- [ ] **Step 7: Commit**

```bash
git add packages/core/noodle/engine.py packages/core/tests/test_loops.py
git commit -m "feat(engine): loop driver — sequential per-item body execution + ordered collection"
```

---

## Task 6: Per-iteration error handling (`fail` / `continue`)

**Files:**
- Test: `packages/core/tests/test_loops.py` (driver already implements both modes; this task pins them)

- [ ] **Step 1: Write the failing tests**

Append to `test_loops.py`:

```python
async def test_loop_on_error_continue_collects_errors():
    # row value 2 raises in the body; continue -> results [1,3]-ish + 1 error
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start", {"on_error": "continue"}),
            _n("b", "code", {"code": "assert input != 2, 'boom'\noutput = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    e = result.nodes["e"]
    assert str(e.status) == "success"
    assert sorted(e.outputs["results"]) == [1, 3]
    assert len(e.outputs["errors"]) == 1
    assert e.outputs["errors"][0]["index"] == 1


async def test_loop_on_error_fail_aborts():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start", {"on_error": "fail"}),
            _n("b", "code", {"code": "assert input != 2, 'boom'\noutput = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "error"
    assert str(result.status) == "error"
```

- [ ] **Step 2: Run tests**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k "on_error" -v`
Expected: PASS (driver from Task 5 already implements both modes). If `continue` mis-collects, the
fix is in `_one_iteration`'s error branch — keep error rows ordered by appending `{index,...}`.

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_loops.py
git commit -m "test(engine): pin loop on_error fail/continue semantics"
```

---

## Task 7: Concurrency preserves output order; empty input

**Files:**
- Test: `packages/core/tests/test_loops.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_loops.py`:

```python
async def test_loop_concurrency_preserves_order():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [0, 1, 2, 3, 4]}),
            _n("s", "loop_start", {"concurrency": 3}),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert result.nodes["e"].outputs["results"] == [0, 1, 2, 3, 4]


async def test_loop_empty_input_yields_empty_results():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": []}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == []
```

- [ ] **Step 2: Run tests**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k "concurrency or empty" -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_loops.py
git commit -m "test(engine): loop concurrency ordering + empty input"
```

---

## Task 8: Nested loops produce correct results

**Files:**
- Test: `packages/core/tests/test_loops.py`

The recursive driver already handles nesting (a body Loop Start is intercepted by `_execute_nodes`
inside the iteration). This task proves it end-to-end.

- [ ] **Step 1: Write the failing test**

Append to `test_loops.py`:

```python
async def test_nested_loops_flatten_correctly():
    # outer items [[1,2],[3]] ; inner doubles each -> results [[2,4],[6]]
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [[1, 2], [3]]}),
            _n("s1", "loop_start"),
            _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("trig", "s1"),
            _e("s1", "s2", src_out="item"),       # outer item (a sublist) -> inner loop input
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),    # inner results -> outer collected value
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e1"].status) == "success"
    assert result.nodes["e1"].outputs["results"] == [[2, 4], [6]]
```

- [ ] **Step 2: Run test**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k nested -v`
Expected: PASS. If the inner loop's `input` isn't resolved per outer iteration, verify `_run_loop`
reads `incoming[start_id]["input"]` from the **iteration** `node_outputs` (the scratch dict passed
in), not the top-level one — the inner driver receives the per-outer-iteration `node_outputs` via
`_execute_nodes`, so `s1.item` is visible to `s2`'s input resolution.

- [ ] **Step 3: Run full core suite**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/ -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/test_loops.py
git commit -m "test(engine): nested loops collect correctly via recursive driver"
```

---

## Task 9: Deprecate `loop_over_items`

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py:495-513`
- Test: `packages/nodes/tests/test_loop_nodes_registration.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/nodes/tests/test_loop_nodes_registration.py`:

```python
def test_loop_over_items_is_deprecated_pointing_to_loop_start():
    nd = registry.get("loop_over_items")
    assert nd.manifest.deprecated is True
    assert nd.manifest.replacement_id == "loop_start"


def test_loop_over_items_still_executes():
    # Deprecated, but must keep working for existing graphs.
    from noodle_nodes.builtin import loop_over_items
    out = loop_over_items(input=[1, 2, 3])
    assert out["item"] == [1, 2, 3]
    assert out["done"]["count"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:/noodle/.venv/Scripts/pytest packages/nodes/tests/test_loop_nodes_registration.py -k loop_over_items -v`
Expected: FAIL — `deprecated` is `False`.

- [ ] **Step 3: Add the deprecation flags**

In `packages/nodes/noodle_nodes/builtin.py`, change the `loop_over_items` decorator (line ~495):

```python
@node(name="Loop Over Items", id="loop_over_items", category="Logic", icon="repeat",
      deprecated=True, replacement_id="loop_start",
      outputs=["item", "done"], params={
          "max_items": {
              "description": "Optional maximum number of items to emit (0 = all).",
          },
      })
```

Also update the docstring's first line to nudge users:

```python
    """DEPRECATED — use Loop Start / Loop End for real per-item iteration.

    Noodle's current DAG engine executes a node once per run rather than once
    per item. This node therefore emits the selected items as a list on the
    ``item`` output while also emitting a completion summary on ``done``.
    """
```

- [ ] **Step 4: Run tests**

Run: `D:/noodle/.venv/Scripts/pytest packages/nodes/tests/test_loop_nodes_registration.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_loop_nodes_registration.py
git commit -m "feat(nodes): deprecate loop_over_items in favor of loop_start/loop_end"
```

---

## Task 10: Subprocess-runner smoke test (no host callback)

**Files:**
- Test: `packages/runtime/tests/test_server.py`

Prove a looped workflow runs end-to-end through `noodle_runtime` and that loops do **not** require
the host-callback path (`_needs_host_callbacks` stays `False` for a loop-only graph).

- [ ] **Step 1: Write the failing/guard tests**

Append to `packages/runtime/tests/test_server.py`:

```python
def test_loop_graph_does_not_need_host_callbacks():
    msg = {
        "type": "run",
        "graph": {
            "nodes": [
                {"id": "s", "type": "loop_start", "params": {}, "position": {"x": 0, "y": 0}},
                {"id": "b", "type": "code", "params": {"code": "output = input"}, "position": {"x": 1, "y": 0}},
                {"id": "e", "type": "loop_end", "params": {"loop_start_id": "s"}, "position": {"x": 2, "y": 0}},
            ],
            "edges": [],
        },
    }
    assert _needs_host_callbacks(msg) is False
```

- [ ] **Step 2: Run test**

Run: `D:/noodle/.venv/Scripts/pytest packages/runtime/tests/test_server.py -k loop_graph -v`
Expected: PASS (loops aren't in `_HOST_CALLBACK_NODE_TYPES`, by design).

- [ ] **Step 3: Add an end-to-end subprocess run test**

Append a real subprocess run mirroring the existing `test_runtime_subprocess_executes_a_graph`
pattern in the same file (spawn `python -m noodle_runtime`, send a loop graph with a
`manual_trigger` feeding `[1,2,3]`, assert a terminal `result` event with `status == "success"`).
Reuse that test's spawn/teardown boilerplate verbatim, swapping in the loop graph from Step 1 plus a
`manual_trigger` node and the `trig->s`, `s->b (item)`, `b->e` edges.

- [ ] **Step 4: Run test**

Run: `D:/noodle/.venv/Scripts/pytest packages/runtime/tests/test_server.py -k loop -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/runtime/tests/test_server.py
git commit -m "test(runtime): loops run in-subprocess without host callbacks"
```

---

## Task 11: `loop_end` `output_mode="dataset"` via a registered writer hook

**Files:**
- Modify: `packages/core/noodle/datasets.py` (add the writer-hook registry)
- Modify: `packages/nodes/noodle_nodes/datasets.py` (register the impl)
- Modify: `packages/core/noodle/engine.py` (`_run_loop` honors `output_mode`)
- Test: `packages/core/tests/test_loops.py`

Core can't import `noodle_nodes`, so mirror the existing materializer hook: core exposes a
registration point + a `dataset_from_records` function; the nodes layer registers the real
DuckDB-backed `records_to_dataset` at import time.

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_loops.py`:

```python
async def test_loop_output_mode_dataset_returns_ref():
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.datasets import dataset_to_records  # materializes back to rows
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [10, 20]}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = {'v': input}"}),
            _n("e", "loop_end", {"loop_start_id": "s", "output_mode": "dataset"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    ref = result.nodes["e"].outputs["results"]
    assert is_dataset_ref(ref)
    rows = dataset_to_records(input=ref, max_rows=10)
    assert sorted(r["v"] for r in rows) == [10, 20]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k output_mode_dataset -v`
Expected: FAIL — `output_mode="dataset"` still returns a list (no writer registered).

- [ ] **Step 3: Add the writer-hook registry in core**

In `packages/core/noodle/datasets.py`, beside `register_materializer` / `_materializer`, add:

```python
_dataset_writer = None  # type: ignore[var-annotated]


def register_dataset_writer(fn) -> None:
    """Register the DuckDB-backed records->DatasetRef writer (called by noodle_nodes)."""
    global _dataset_writer
    _dataset_writer = fn


def dataset_from_records(records: list[dict], *, name: str = "loop_output.parquet") -> dict:
    """Write a list of dicts to a DatasetRef using the registered writer."""
    if _dataset_writer is None:
        raise RuntimeError(
            "no dataset writer registered; import noodle_nodes.datasets"
        )
    return _dataset_writer(records, name=name)
```

- [ ] **Step 4: Register the impl in the nodes layer**

In `packages/nodes/noodle_nodes/datasets.py`, next to `register_materializer(materialize_dataset)`,
add:

```python
from noodle.datasets import register_dataset_writer  # add to the existing import block

def _records_writer(records: list[dict], *, name: str = "loop_output.parquet") -> dict:
    return records_to_dataset(records, name=name)

register_dataset_writer(_records_writer)
```

(`records_to_dataset` already exists in this module.)

- [ ] **Step 5: Honor `output_mode` in `_run_loop`**

In `engine.py` `_run_loop`, replace the final success block that builds `out` with:

```python
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
```

- [ ] **Step 6: Run the test**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/test_loops.py -k output_mode_dataset -v`
Expected: PASS.

- [ ] **Step 7: Run the full core + nodes suites (no regressions)**

Run: `D:/noodle/.venv/Scripts/pytest packages/core/tests/ packages/nodes/tests/ -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add packages/core/noodle/datasets.py packages/nodes/noodle_nodes/datasets.py packages/core/noodle/engine.py packages/core/tests/test_loops.py
git commit -m "feat(engine): loop_end output_mode=dataset via registered writer hook"
```

---

## Final verification

- [ ] Run everything touched:

Run:
```
D:/noodle/.venv/Scripts/pytest packages/core/tests/ packages/nodes/tests/test_loop_nodes_registration.py packages/runtime/tests/test_server.py -q
```
Expected: all green.

- [ ] Confirm no regression in the broader nodes suite (loops/Map coexist):

Run: `D:/noodle/.venv/Scripts/pytest packages/nodes/tests/ -q`
Expected: all green.

---

## Self-review notes (spec coverage)

- Nodes + ports + params → Task 1. ✅
- SESE body + region detection → Tasks 2–3. ✅
- Driver / shared execution path / sequential + concurrency / collection / errors → Tasks 4–7. ✅
- Nesting (iteration recursion) → Task 8. ✅ (iteration-*path persistence* is Phase 2)
- Dataset input via `materialize_dataset_rows` + `max_rows` guard → Task 5 (`_loop_items`). ✅
- `loop_end` `output_mode` records|dataset via registered writer hook → Task 11. ✅
- Loops stay in-subprocess, no host callback → Task 10. ✅
- Deprecate `loop_over_items` → Task 9. ✅
- **Deferred to later phases:** iteration-tagged events + `NodeRun.iteration_path` migration +
  runner persistence (Phase 2); editor authoring + per-iteration inspector (Phase 3); accumulator +
  break (v2).

## Open risks for the implementer

- **Task 4 is the riskiest** (behavior-preserving extraction of `_run_node`). Keep the full core
  suite green at every step; do the extraction mechanically (closure vars → params) without changing
  logic.
- Confirm `manual_trigger`'s payload param key (`data` vs other) and the `merge` node id against the
  live registry before relying on them in tests; adjust the test graphs if they differ.
