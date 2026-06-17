# Phase 4: Sub-workflow Resolution via Engine Callback (A3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move sub-workflow calling semantics (cycle detection, depth limiting, inline-child execution, leaf extraction) into the engine behind a `SubworkflowRunner` callback, so the API host, runtime subprocess, remote runner, and exporter all resolve `execute_workflow`/`map_*` children through one contract — retiring the `runner.InlineSubWorkflow` splice.

**Architecture:** The engine's `execute()` gains `subworkflow_runner` (host resolver callable) + `subworkflow_meta` (root-run facts: draft preference, parent run id, depth, call chain, max depth). The engine sets the existing `noodle.context.workflow_caller` ContextVar to an adapter that enforces cycle/depth invariants, builds a `SubworkflowCall`, and — when the resolver answers with an `InlineSubworkflow` directive — recursively executes the prepared child graph itself. Hosts shrink to pure resolvers: the API resolver does DB lookup + child Run rows + org caps (unchanged `subworkflow_slot`/`dispatch_subworkflow` internals preserve the global-cap bypass), the runtime subprocess RPCs the call to the host, the exporter answers from bundled graphs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + Alembic, asyncio subprocess stdio protocol, pytest.

**Branch:** `feat/arch-program-phase4` off `feat/multi-tenancy` (same pattern as Phases 2–3).

**Validation commands (Windows dev box — `uv run` broken, use the built venv):**

```powershell
cd packages\core;   ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\nodes;  ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\exporter; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd apps\api;        ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd apps\web;        npm run test:e2e
```

---

## Locked design decisions (deviations from the master-plan sketch, with reasons)

1. **`SubworkflowRunner` returns the child's *leaf value* (`Any`), not `dict[str, Any]` node outputs.** Nodes (`execute_workflow`, `map_items`, `map_group`, `map_dataset`) consume the leaf value today; returning raw node outputs would force leaf-extraction knowledge into every node. Leaf extraction stays with whoever ran the child — via the shared `extract_leaf_value` helper (today duplicated as `runner._extract_sub_leaf` + `server._resolve_inline`).
2. **`SubworkflowCall` gains a `call_chain: frozenset[str]` field** beyond the master sketch (`workflow_id, use_published, parameters, parent_run_id, depth`). Cycle detection must survive process hops (parent subprocess → host → fresh child subprocess); the chain travels explicitly instead of living in host-side ContextVar threading.
3. **The inline fast path survives — as an engine-owned directive, not a runner.py splice.** `InlineSubworkflow` (graph/cache/targets/sources prepared by the host) is a legal resolver return value; the *engine* executes it via recursion with correct depth/chain meta. This keeps map-node fan-out fast (no subprocess spawn per same-env item) while lifting today's "no nested workflow calls" inline restriction — chain/depth now travel explicitly, so nested calls inside inline children are safe.
4. **Child Run rows (new):** the API resolver creates a real `Run` row per spawned/in-process child (`mode="subworkflow"`, new `parent_run_id` column, org inherited from the parent run). This fixes a real MT hole: today children run under synthetic `sub_*` ids, so `_resolve_run_org` falls back to the default org — wrong artifact prefix and uncapped org limits for non-default orgs. Inline children get no row (they execute inside the parent's process — anonymous fast path). No node-run persistence and no event streaming for children in this phase.
5. **Depth limiting is new but required by the master plan** ("must preserve … depth limiting" — today only cycle detection exists). Default `max_subworkflow_depth = 16`, configurable in `app/config.py`, enforced by the engine adapter.
6. **Children never pause on approval** (`pause_on_approval` not passed to child executes) — identical to today's behavior on all three paths.
7. **Engine param naming:** `execute(..., subworkflow_runner=..., subworkflow_meta=...)` — the callable is host behavior, the meta is root-run facts. Master sketch showed a single `subworkflow_runner` param; the split keeps `SubworkflowRunner` a plain callable.
8. **Exported scripts always embed the resolver** (with `SUBWORKFLOWS = {}` when nothing is referenced) — a missing bundle yields a clear "not bundled in this export" error instead of "no host caller is configured". Bundled children run un-gated (`targets=None`) — multi-trigger sub-workflows in exports are out of scope; the first trigger is seeded.

**Tests that pin the old splice** (`apps/api/tests/test_architecture_fixes.py` ~lines 180–500: `InlineSubWorkflow` isinstance checks, `_call_sub_workflow` calls, nested-call spawn restriction) are *implementation* tests and get rewritten against the new resolver in Task 6. Behavior tests (`tests/test_subworkflows.py`) must stay green **with zero edits** — they are the contract.

---

## File map

| File | Change |
|---|---|
| `packages/core/noodle/engine/subworkflows.py` | **Create** — `SubworkflowCall`, `SubworkflowMeta`, `SubworkflowRunner`, `InlineSubworkflow`, `extract_leaf_value`, `make_workflow_caller` |
| `packages/core/noodle/engine/scheduler.py` | `execute()`/`run()` gain `subworkflow_runner`/`subworkflow_meta`; sets `workflow_caller` + `call_chain` |
| `packages/core/noodle/engine/__init__.py` | Re-export the new public names |
| `packages/core/tests/test_subworkflow_callback.py` | **Create** — stub-resolver acceptance tests |
| `apps/api/alembic/versions/0048_run_parent.py` | **Create** — `runs.parent_run_id` |
| `apps/api/app/models.py` | `Run.parent_run_id` |
| `apps/api/app/schemas.py` | expose `parent_run_id` on the run read schema |
| `apps/api/app/config.py` | `max_subworkflow_depth: int = 16` |
| `apps/api/app/services/subworkflows.py` | **Create** — host resolver + child Run rows + meta builder |
| `apps/api/app/services/runner.py` | Delete `_call_sub_workflow` cluster (~250 lines); wire meta through `_execute_run` |
| `apps/api/app/services/executors/base.py` | `RunExecutionContext` + `subworkflow_meta` key |
| `apps/api/app/services/executors/local.py` | pass resolver + meta to pool |
| `apps/api/app/services/runtime_pool.py` | `dispatch`/`dispatch_subworkflow`/`_RuntimeProcess.run`/`_handle_call_workflow` new signatures + protocol fields |
| `packages/runtime/noodle_runtime/server.py` | RPC runner + meta from request; delete `_resolve_inline` |
| `apps/api/app/services/providers/agent.py` | `run_assigned` ships meta; `resolve_remote_subworkflow` builds `SubworkflowCall` |
| `apps/api/app/services/providers/k8s.py` | `run_assigned` ships meta |
| `packages/runner/noodle_runner_agent/process_pool.py` | forward full call payload + meta |
| `packages/runner/noodle_runner_agent/agent.py` | broker forwards call fields |
| `packages/runner/noodle_runner_agent/k8s_entrypoint.py` | pass meta through |
| `packages/exporter/noodle_exporter/codegen.py` | bundled-subworkflow template |
| `apps/api/app/routers/export.py` | recursive sub-graph collection |
| `apps/api/tests/test_subworkflow_resolution.py` | **Create** — resolver + child Run row tests |
| `apps/api/tests/test_architecture_fixes.py` | rewrite the four splice-pinning tests |
| `packages/exporter/tests/test_codegen.py` | round-trip test with a bundled sub |
| `apps/api/tests/test_export.py` (or new `test_export_subworkflows.py`) | router bundling test |

---

## Task 0: Branch

- [ ] **Step 1: Create the working branch**

```powershell
git checkout -b feat/arch-program-phase4
```

(Repo state must be clean, on `feat/multi-tenancy`, HEAD = 39bb351 or later.)

## Task 1: Core types + leaf extraction (`noodle/engine/subworkflows.py`)

**Files:**
- Create: `packages/core/noodle/engine/subworkflows.py`
- Modify: `packages/core/noodle/engine/__init__.py`
- Test: `packages/core/tests/test_subworkflow_callback.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/core/tests/test_subworkflow_callback.py
"""Phase 4 (A3): sub-workflow resolution through the engine callback."""

from noodle.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    extract_leaf_value,
)


def test_extract_leaf_value_single_leaf():
    value = extract_leaf_value(
        sources={"t"},
        node_status={"t": "success", "c": "success"},
        node_outputs={"t": {"main": 1}, "c": {"main": 42}},
    )
    assert value == 42


def test_extract_leaf_value_multiple_leaves_keyed_by_id():
    value = extract_leaf_value(
        sources={"t"},
        node_status={"t": "success", "a": "success", "b": "success"},
        node_outputs={"a": {"main": 1}, "b": {"main": 2}},
    )
    assert value == {"a": 1, "b": 2}


def test_extract_leaf_value_no_successful_leaf_is_none():
    assert (
        extract_leaf_value(
            sources={"t"},
            node_status={"t": "success", "c": "error"},
            node_outputs={},
        )
        is None
    )


def test_call_payload_round_trip():
    call = SubworkflowCall(
        workflow_id="wf2",
        parameters={"x": 1},
        use_published=False,
        parent_run_id="run1",
        depth=2,
        call_chain=frozenset({"wf1", "wf2"}),
    )
    assert SubworkflowCall.from_payload(call.to_payload()) == call


def test_meta_payload_round_trip():
    meta = SubworkflowMeta(
        use_published=False,
        parent_run_id="run1",
        depth=1,
        call_chain=frozenset({"wf1"}),
        max_depth=5,
    )
    assert SubworkflowMeta.from_payload(meta.to_payload()) == meta


def test_meta_from_empty_payload_uses_defaults():
    meta = SubworkflowMeta.from_payload({})
    assert meta == SubworkflowMeta()
    assert meta.use_published is True and meta.depth == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests/test_subworkflow_callback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'noodle.engine.subworkflows'`

- [ ] **Step 3: Write the module**

```python
# packages/core/noodle/engine/subworkflows.py
"""Sub-workflow resolution callback (A3).

The engine owns the *semantics* of calling another workflow — cycle
detection, depth limiting, inline-child execution, leaf extraction — while
each host supplies a :data:`SubworkflowRunner` that owns *resolution*:
looking up the child graph, deciding where it executes, and enforcing
host-side caps (org sub-workflow quotas, spawn throttles, child Run rows).

Hosts:

* API (``apps/api/app/services/subworkflows.py``) — DB lookup, child Run
  rows, org caps via the runtime pool's ``subworkflow_slot``.
* Runtime subprocess (``noodle_runtime.server``) — RPC back to the host over
  the stdio protocol.
* Exporter (``noodle_exporter``) — bundled graphs resolved locally as
  :class:`InlineSubworkflow` directives.

A resolver may answer a call two ways:

* a concrete **leaf value** — the child ran wherever the host chose;
* an :class:`InlineSubworkflow` directive — "run this prepared child graph
  yourself". The engine executes it recursively with correct depth/chain
  metadata, which is what makes inline children semantically identical to
  spawned ones (the old runner.py splice skipped chain tracking and had to
  forbid nested calls).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noodle.context import call_chain

if TYPE_CHECKING:
    from noodle.engine.types import ProcessIsolator
    from noodle.sdk import NodeRegistry


@dataclass(frozen=True)
class SubworkflowCall:
    """One request to run another workflow, built by the engine adapter."""

    workflow_id: str
    parameters: Any  # input payload seeded into the child's trigger
    use_published: bool  # False = prefer draft graphs (editor iteration)
    parent_run_id: str | None
    depth: int  # 1 = direct child of the root run
    call_chain: frozenset[str] = frozenset()  # ancestors + this workflow

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "input": self.parameters,
            "use_published": self.use_published,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
            "call_chain": sorted(self.call_chain),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SubworkflowCall":
        return cls(
            workflow_id=str(payload.get("workflow_id") or ""),
            parameters=payload.get("input"),
            use_published=bool(payload.get("use_published", True)),
            parent_run_id=payload.get("parent_run_id") or None,
            depth=int(payload.get("depth") or 1),
            call_chain=frozenset(payload.get("call_chain") or ()),
        )


@dataclass(frozen=True)
class SubworkflowMeta:
    """Root-run facts the host injects so calls carry correct context."""

    use_published: bool = True
    parent_run_id: str | None = None
    depth: int = 0  # depth of THIS graph (0 = root run)
    call_chain: frozenset[str] = frozenset()  # must include this graph's id
    max_depth: int = 16  # 0 = unlimited

    def to_payload(self) -> dict[str, Any]:
        return {
            "use_published": self.use_published,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
            "call_chain": sorted(self.call_chain),
            "max_depth": self.max_depth,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SubworkflowMeta":
        return cls(
            use_published=bool(payload.get("use_published", True)),
            parent_run_id=payload.get("parent_run_id") or None,
            depth=int(payload.get("depth") or 0),
            call_chain=frozenset(payload.get("call_chain") or ()),
            max_depth=int(
                payload.get("max_depth", cls.max_depth)
                if payload.get("max_depth") is not None
                else cls.max_depth
            ),
        )


@dataclass(frozen=True)
class InlineSubworkflow:
    """Resolver directive: execute this prepared child graph in-engine.

    ``graph``/``cache``/``targets`` are exactly what the host would have
    passed to a fresh child engine (credentials resolved, trigger seeded,
    targets gated). ``sources`` is the set of edge-source node ids, used for
    leaf extraction.
    """

    graph: dict
    cache: dict | None
    targets: list[str] | None
    sources: tuple[str, ...] = ()


# Returns the child's leaf value, or an InlineSubworkflow directive.
SubworkflowRunner = Callable[[SubworkflowCall], Awaitable[Any]]


def extract_leaf_value(
    sources: set[str],
    node_status: dict[str, str],
    node_outputs: dict[str, dict],
) -> Any:
    """Leaf-node output(s) of a finished child run.

    The "leaf" is any successful node no edge originates from. One leaf →
    its ``main`` output; multiple → dict keyed by node id; none → ``None``.
    (Single shared implementation of the rule previously duplicated in
    ``runner._extract_sub_leaf`` and ``noodle_runtime.server._resolve_inline``.)
    """
    leaves = [
        nid
        for nid, status in node_status.items()
        if status == "success" and nid not in sources
    ]
    if len(leaves) == 1:
        return (node_outputs.get(leaves[0]) or {}).get("main")
    if leaves:
        return {nid: (node_outputs.get(nid) or {}).get("main") for nid in leaves}
    return None


def make_workflow_caller(
    runner: SubworkflowRunner,
    meta: SubworkflowMeta,
    registry: "NodeRegistry",
    *,
    default_timeouts: dict[str, float] | None = None,
    process_isolator: "ProcessIsolator | None" = None,
) -> Callable[[str, Any], Awaitable[Any]]:
    """Build the ``noodle.context.workflow_caller`` adapter for one run.

    The adapter enforces depth + cycle invariants, then delegates to the
    host resolver. Inline directives are executed here, recursively, with
    the child's meta — so every execution path shares one rulebook.
    """

    async def _run_inline(directive: InlineSubworkflow, call: SubworkflowCall) -> Any:
        # Lazy import: scheduler imports this module at top level.
        from noodle.engine.scheduler import execute
        from noodle.models import WorkflowGraph

        child_meta = SubworkflowMeta(
            use_published=meta.use_published,
            parent_run_id=meta.parent_run_id,
            depth=call.depth,
            call_chain=call.call_chain,
            max_depth=meta.max_depth,
        )
        result = await execute(
            WorkflowGraph.model_validate(directive.graph),
            registry,
            cache=dict(directive.cache) if directive.cache else None,
            targets=list(directive.targets) if directive.targets else None,
            default_timeouts=default_timeouts,
            process_isolator=process_isolator,
            subworkflow_runner=runner,
            subworkflow_meta=child_meta,
        )
        node_status = {nid: str(r.status) for nid, r in result.nodes.items()}
        node_outputs = {nid: dict(r.outputs) for nid, r in result.nodes.items()}
        return extract_leaf_value(set(directive.sources), node_status, node_outputs)

    async def _call(workflow_id: str, input_value: Any) -> Any:
        depth = meta.depth + 1
        if meta.max_depth and depth > meta.max_depth:
            raise RuntimeError(
                f"sub-workflow depth limit ({meta.max_depth}) exceeded "
                f"at '{workflow_id}'"
            )
        chain = call_chain.get()
        if workflow_id in chain:
            raise RuntimeError(
                f"sub-workflow cycle detected — '{workflow_id}' is already running"
            )
        call = SubworkflowCall(
            workflow_id=workflow_id,
            parameters=input_value,
            use_published=meta.use_published,
            parent_run_id=meta.parent_run_id,
            depth=depth,
            call_chain=frozenset(chain | {workflow_id}),
        )
        token = call_chain.set(chain | {workflow_id})
        try:
            outcome = await runner(call)
            if isinstance(outcome, InlineSubworkflow):
                return await _run_inline(outcome, call)
            return outcome
        finally:
            call_chain.reset(token)

    return _call
```

> Executor note: confirm the actual registry type name used in `scheduler.py`'s signature (`NodeRegistry` import source) and the `ProcessIsolator` type location (`noodle.process_isolation` or `noodle.engine.types`) — adjust the `TYPE_CHECKING` imports to match what `scheduler.py` already imports. The cycle-error message must stay byte-identical to the one in `runner._call_sub_workflow` (existing tests may assert on "cycle").

- [ ] **Step 4: Re-export from the facade**

In `packages/core/noodle/engine/__init__.py` add:

```python
from noodle.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    SubworkflowRunner,
    extract_leaf_value,
    make_workflow_caller,
)
```

and append to `__all__`: `"InlineSubworkflow", "SubworkflowCall", "SubworkflowMeta", "SubworkflowRunner", "extract_leaf_value", "make_workflow_caller"`. (Keep `test_engine_exports.py`'s contract in mind — if it asserts an exact export list, extend it.)

- [ ] **Step 5: Run the tests**

Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests/test_subworkflow_callback.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/core/noodle/engine/subworkflows.py packages/core/noodle/engine/__init__.py packages/core/tests/test_subworkflow_callback.py
git commit -m "feat(core): SubworkflowCall/Meta/Runner types + shared leaf extraction (A3)"
```

## Task 2: Engine wiring — `execute(..., subworkflow_runner, subworkflow_meta)`

**Files:**
- Modify: `packages/core/noodle/engine/scheduler.py` (`execute` ~line 346, `run` ~line 456)
- Test: `packages/core/tests/test_subworkflow_callback.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test_subworkflow_callback.py`)

```python
import pytest

import noodle_nodes  # noqa: F401 - registers execute_workflow
from noodle.engine import execute
from noodle.models import WorkflowGraph
from noodle.sdk import registry


def _parent_graph(sub_id: str = "wf-child") -> WorkflowGraph:
    return WorkflowGraph.model_validate({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {"data": 7},
             "position": {"x": 0, "y": 0}},
            {"id": "sub", "type": "execute_workflow",
             "params": {"workflow_id": sub_id}, "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"id": "e", "source": "t", "source_output": "main",
             "target": "sub", "target_input": "input"},
        ],
    })


CHILD_GRAPH = {
    "nodes": [
        {"id": "ct", "type": "manual_trigger", "params": {},
         "position": {"x": 0, "y": 0}},
        {"id": "cc", "type": "code", "params": {"code": "output = input['v'] * 2"},
         "position": {"x": 200, "y": 0}},
    ],
    "edges": [
        {"id": "ce", "source": "ct", "source_output": "main",
         "target": "cc", "target_input": "input"},
    ],
}


async def test_engine_runs_subworkflow_through_stub_resolver():
    """Master-plan acceptance: a sub-workflow exercised via a stub resolver."""
    seen: list[SubworkflowCall] = []

    async def resolver(call: SubworkflowCall):
        seen.append(call)
        return {"doubled": True}

    meta = SubworkflowMeta(
        use_published=False, parent_run_id="run-1",
        call_chain=frozenset({"wf-root"}),
    )
    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver, subworkflow_meta=meta,
    )
    assert str(result.nodes["sub"].status) == "success"
    assert result.nodes["sub"].outputs["main"] == {"doubled": True}
    call = seen[0]
    assert call.workflow_id == "wf-child"
    assert call.parameters == 7
    assert call.use_published is False
    assert call.parent_run_id == "run-1"
    assert call.depth == 1
    assert call.call_chain == frozenset({"wf-root", "wf-child"})


async def test_engine_detects_cycle_from_meta_chain():
    async def resolver(call):  # pragma: no cover - must not be reached
        raise AssertionError("resolver must not run for a cyclic call")

    meta = SubworkflowMeta(call_chain=frozenset({"wf-child"}))
    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver, subworkflow_meta=meta,
    )
    assert str(result.nodes["sub"].status) == "error"
    assert "cycle" in (result.nodes["sub"].error or "")


async def test_engine_enforces_depth_limit():
    async def resolver(call):  # pragma: no cover - must not be reached
        raise AssertionError("resolver must not run past the depth limit")

    meta = SubworkflowMeta(depth=3, max_depth=3)
    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver, subworkflow_meta=meta,
    )
    assert str(result.nodes["sub"].status) == "error"
    assert "depth limit" in (result.nodes["sub"].error or "")


async def test_engine_executes_inline_directive():
    async def resolver(call: SubworkflowCall):
        return InlineSubworkflow(
            graph=CHILD_GRAPH,
            cache={"ct": {"main": {"v": call.parameters}}},
            targets=None,
            sources=("ct",),
        )

    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver,
        subworkflow_meta=SubworkflowMeta(call_chain=frozenset({"wf-root"})),
    )
    assert str(result.nodes["sub"].status) == "success"
    assert result.nodes["sub"].outputs["main"] == 14  # 7 * 2


async def test_inline_child_nested_call_carries_extended_chain():
    """Nested calls inside inline children are allowed and chain-checked."""
    calls: list[SubworkflowCall] = []

    nested_child = {
        "nodes": [
            {"id": "ct", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "csub", "type": "execute_workflow",
             "params": {"workflow_id": "wf-grandchild"},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"id": "ce", "source": "ct", "source_output": "main",
             "target": "csub", "target_input": "input"},
        ],
    }

    async def resolver(call: SubworkflowCall):
        calls.append(call)
        if call.workflow_id == "wf-child":
            return InlineSubworkflow(
                graph=nested_child, cache={"ct": {"main": {}}},
                targets=None, sources=("ct",),
            )
        return "leaf"

    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver,
        subworkflow_meta=SubworkflowMeta(call_chain=frozenset({"wf-root"})),
    )
    assert str(result.nodes["sub"].status) == "success"
    assert [c.workflow_id for c in calls] == ["wf-child", "wf-grandchild"]
    assert calls[1].depth == 2
    assert calls[1].call_chain == frozenset({"wf-root", "wf-child", "wf-grandchild"})
```

> Executor note: check how existing core tests configure pytest-asyncio (`asyncio_mode = auto` in `pyproject.toml`/`pytest.ini` vs explicit `@pytest.mark.asyncio`) and match it. Also confirm the `code` node param/behavior used here against an existing core test (e.g. in `test_engine.py`) — adjust `CHILD_GRAPH` to whatever minimal pattern those tests use for a compute node; the assertion that matters is leaf value `14`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests/test_subworkflow_callback.py -v`
Expected: the five new tests FAIL with `TypeError: execute() got an unexpected keyword argument 'subworkflow_runner'`.

- [ ] **Step 3: Implement in `scheduler.py`**

Add the parameters to `execute` (after `process_isolator`):

```python
    subworkflow_runner: "SubworkflowRunner | None" = None,
    subworkflow_meta: "SubworkflowMeta | None" = None,
```

(Quote the annotations and add a `TYPE_CHECKING` import of `SubworkflowMeta, SubworkflowRunner` from `noodle.engine.subworkflows` — a runtime top-level import would be circular.)

Immediately after `_install_capture()` (before graph expansion), install the adapter:

```python
    caller_token = chain_token = None
    if subworkflow_runner is not None:
        from noodle.context import call_chain, workflow_caller
        from noodle.engine.subworkflows import SubworkflowMeta, make_workflow_caller

        _sub_meta = subworkflow_meta or SubworkflowMeta()
        chain_token = call_chain.set(_sub_meta.call_chain)
        caller_token = workflow_caller.set(
            make_workflow_caller(
                subworkflow_runner,
                _sub_meta,
                registry,
                default_timeouts=default_timeouts,
                process_isolator=process_isolator,
            )
        )
```

Wrap the remainder of the function body in `try:` and reset in `finally:`:

```python
    finally:
        if caller_token is not None:
            from noodle.context import workflow_caller
            workflow_caller.reset(caller_token)
        if chain_token is not None:
            from noodle.context import call_chain
            call_chain.reset(chain_token)
```

> Executor note: re-indenting the whole body is mechanical but noisy; alternatively extract the existing body into `_execute_inner(...)` and have `execute` do setup/try/finally around one call — pick whichever yields the smaller diff. `default_timeouts` is already resolved to a concrete dict by this point, so passing it onward is safe. Hosts must construct the adapter ContextVars per-execute (NOT module-level) — this matches how `process_isolator` is threaded.

And extend `run` (the sync wrapper):

```python
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
```

- [ ] **Step 4: Run the full core + nodes suites**

Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Run: `cd packages\nodes; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS (332 + 720 + the new tests; zero regressions — the new params default to None/no-op).

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/engine/scheduler.py packages/core/tests/test_subworkflow_callback.py
git commit -m "feat(core): engine executes sub-workflows through an injected resolver callback (A3)"
```

## Task 3: API groundwork — migration, model, config

**Files:**
- Create: `apps/api/alembic/versions/0048_run_parent.py`
- Modify: `apps/api/app/models.py` (Run, ~line 489), `apps/api/app/schemas.py` (run read schema), `apps/api/app/config.py`

- [ ] **Step 1: Add the model column**

In `app/models.py`, inside `class Run`, after `triggered_by_error_run_id`:

```python
    # A3: set when this run is a sub-workflow child spawned by another run's
    # execute_workflow / map_* node. Children carry mode="subworkflow".
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

- [ ] **Step 2: Write the migration**

```python
# apps/api/alembic/versions/0048_run_parent.py
"""runs.parent_run_id — sub-workflow child runs (A3).

Revision ID: 0048
Revises: 0047
"""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("parent_run_id", sa.String(32), nullable=True))
        batch.create_foreign_key(
            "fk_runs_parent_run_id", "runs", ["parent_run_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_parent_run_id", table_name="runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_parent_run_id", type_="foreignkey")
        batch.drop_column("parent_run_id")
```

> Executor note: open `0047_run_meters.py` first and mirror its exact conventions (revision id literals, naming, whether batch mode is used). If existing migrations don't use batch mode for `runs`, match them.

- [ ] **Step 3: Expose on the read schema + add the config knob**

In `app/schemas.py`, find the run read model (grep `triggered_by_error_run_id` — add the new field beside it):

```python
    parent_run_id: str | None = None
```

In `app/config.py`, next to `max_concurrent_subworkflows`:

```python
    # A3: hard ceiling on sub-workflow nesting depth (root = 0). Cycle
    # detection catches A→B→A; this catches runaway A→B→C→… chains. 0 = off.
    max_subworkflow_depth: int = 16
```

- [ ] **Step 4: Run the API suite (sanity — nothing consumes the column yet)**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: same pass count as baseline (556 passed + 1 known pre-existing failure `test_dispatch_webhook_passes_shared_session_to_resolve_node_auth`).

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0048_run_parent.py apps/api/app/models.py apps/api/app/schemas.py apps/api/app/config.py
git commit -m "feat(api): runs.parent_run_id column + max_subworkflow_depth setting (A3)"
```

## Task 4: API host resolver (`app/services/subworkflows.py`)

**Files:**
- Create: `apps/api/app/services/subworkflows.py`
- Test: `apps/api/tests/test_subworkflow_resolution.py`

The resolver is a port of `runner._call_sub_workflow` + `runner._load_workflow_graph` with these deltas: cycle detection removed (engine owns it), `call.use_published` replaces the `_prefer_draft_graphs` ContextVar, the `_has_nested_workflow_call` inline restriction removed, and child Run rows added around both non-inline paths.

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_subworkflow_resolution.py
"""A3: host-side sub-workflow resolver — child Run rows, draft selection."""

from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Run
from app.services.subworkflows import resolve_subworkflow
from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowCall


def _doubler_graph() -> dict:
    return {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "c", "type": "code", "params": {"code": "output = input * 2"},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"id": "e", "source": "t", "source_output": "main",
             "target": "c", "target_input": "input"},
        ],
    }


async def _make_sub(client: AsyncClient, code: str = "output = input * 2") -> str:
    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    graph = _doubler_graph()
    graph["nodes"][1]["params"]["code"] = code
    await client.put(f"/workflows/{sub['id']}", json={"graph": graph})
    return sub["id"]


def _call(workflow_id: str, value, **kw) -> SubworkflowCall:
    defaults = dict(
        parameters=value, use_published=True, parent_run_id=None,
        depth=1, call_chain=frozenset({workflow_id}),
    )
    defaults.update(kw)
    return SubworkflowCall(workflow_id=workflow_id, **defaults)


async def test_resolver_runs_child_in_process(client: AsyncClient) -> None:
    sub_id = await _make_sub(client)
    result = await resolve_subworkflow(_call(sub_id, 21))
    assert result == 42


async def test_resolver_creates_child_run_row(client: AsyncClient) -> None:
    sub_id = await _make_sub(client)

    # Parent run row to inherit org from / link to.
    parent = (await client.post("/workflows", json={"name": "Parent"})).json()
    await client.put(f"/workflows/{parent['id']}", json={"graph": _doubler_graph()})
    parent_run_id = (
        await client.post(f"/workflows/{parent['id']}/run", json={})
    ).json()["run_id"]

    await resolve_subworkflow(_call(sub_id, 1, parent_run_id=parent_run_id))

    async with SessionLocal() as session:
        child = await session.scalar(
            select(Run).where(Run.parent_run_id == parent_run_id)
        )
    assert child is not None
    assert child.workflow_id == sub_id
    assert child.mode == "subworkflow"
    assert child.status == "success"
    assert child.finished_at is not None


async def test_resolver_prefers_draft_when_not_published_mode(
    client: AsyncClient,
) -> None:
    sub_id = await _make_sub(client, code="output = input * 2")
    # Save a NEW draft (PUT updates draft_graph) with different behavior,
    # without publishing.
    draft = _doubler_graph()
    draft["nodes"][1]["params"]["code"] = "output = input * 10"
    await client.put(f"/workflows/{sub_id}", json={"graph": draft})

    published = await resolve_subworkflow(_call(sub_id, 3, use_published=True))
    draft_result = await resolve_subworkflow(_call(sub_id, 3, use_published=False))
    assert draft_result == 30
    assert published in (6, 30)  # see executor note below


async def test_resolver_returns_inline_directive_for_same_env(
    client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    sub_id = await _make_sub(client)
    monkeypatch.setattr(settings, "use_subprocess_runner", True)
    outcome = await resolve_subworkflow(
        _call(sub_id, 1), parent_env_id="_default_env_sentinel"
    )
    # Sub has no environment binding (env None) — parent env differs → spawn
    # path would be taken; force the match instead:
    assert not isinstance(outcome, InlineSubworkflow) or outcome.graph
```

> Executor note (important): this last test needs the *actual* env-matching rule. Port the old test `test_architecture_fixes.py::test_subworkflow_inlines_when_same_env...` (~line 345) — it already constructs parent/sub on matching envs and asserts the sentinel; keep its setup, change the assertion to `isinstance(outcome, InlineSubworkflow)` (core class) and the call site to `resolve_subworkflow(call, parent_env_id=...)`. Likewise confirm the draft-selection mechanics: check how `_load_workflow_graph` resolves published vs draft (PUT vs publish endpoints) against an existing test before finalizing `test_resolver_prefers_draft...` — the assertion that matters is `use_published=False → 30`; pin the published expectation to whatever the publish flow actually yields (publish the first graph via the publish endpoint if one exists, mirroring existing tests in `test_architecture_fixes.py` ~line 220).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_subworkflow_resolution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.subworkflows'`

- [ ] **Step 3: Implement the resolver**

```python
# apps/api/app/services/subworkflows.py
"""Host-side sub-workflow resolver (A3).

Implements ``SubworkflowRunner`` for the API: looks up the child graph,
resolves credentials, seeds the trigger, creates a child Run row, and runs
the child on the right substrate. Cycle detection and depth limits live in
the ENGINE adapter (``noodle.engine.subworkflows``) — not here.

Concurrency invariants preserved from the pre-A3 code (HANDOFF.md §7):

* Child runs NEVER acquire the global ``max_concurrent_runs`` slot — the
  parent already holds one; waiting would deadlock at the cap.
  ``runtime_pool.dispatch_subworkflow`` (subprocess) and the direct
  ``execute`` call below (in-process) both bypass it.
* Org sub-workflow caps (``max_inflight_subworkflows``) are enforced by the
  soft ``subworkflow_slot`` throttle inside ``dispatch_subworkflow`` —
  unchanged by this refactor.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import PinnedData, Run, Workflow
from app.services.credentials import resolve_credential_refs
from app.services.graph_utils import first_trigger_node, resolve_trigger_targets
from noodle.engine import execute
from noodle.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    extract_leaf_value,
)
from noodle.models import WorkflowGraph
from noodle.serialization import deserialize_value

logger = logging.getLogger(__name__)


def meta_for_root_run(
    *, run_id: str, workflow_id: str, prefer_draft: bool
) -> SubworkflowMeta:
    """The SubworkflowMeta a root run hands to the engine / run protocol."""
    return SubworkflowMeta(
        use_published=not prefer_draft,
        parent_run_id=run_id,
        depth=0,
        call_chain=frozenset({workflow_id}),
        max_depth=settings.max_subworkflow_depth,
    )


async def _load_workflow_graph(
    session: AsyncSession, workflow_id: str, *, use_published: bool
) -> tuple[dict, dict[str, dict]]:
    """(graph_dict, pinned_cache) for a child workflow.

    Production runs execute the most recently published version (Slice 11
    contract); manual editor runs (``use_published=False``) propagate "use
    draft" so iteration works without publishing every dependent workflow.
    """
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise ValueError(f"workflow '{workflow_id}' not found")
    if not workflow.versions:
        raise ValueError(f"workflow '{workflow_id}' has no published versions")
    latest = workflow.versions[-1]
    pinned_rows = await session.scalars(
        select(PinnedData).where(PinnedData.workflow_id == workflow_id)
    )
    pinned = {row.node_id: row.payload for row in pinned_rows.all()}
    published = latest.graph or {"nodes": [], "edges": []}
    if not use_published and workflow.draft_graph:
        return workflow.draft_graph, pinned
    return published, pinned


async def _create_child_run(call: SubworkflowCall, workflow_id: str) -> str:
    """Persist a Run row for a spawned/in-process child.

    Gives the child a real identity: ``_resolve_run_org`` keys artifact
    prefixes and org limits off the run row, so children inherit the
    parent's org instead of falling back to the default org (pre-A3 bug for
    multi-tenant deployments). Inline children skip this — they execute
    inside the parent's process under the parent's run.
    """
    from app.tenancy import DEFAULT_ORG_ID, run_as_system

    child_run_id = uuid.uuid4().hex[:32]
    with run_as_system():
        async with SessionLocal() as session:
            org_id = DEFAULT_ORG_ID
            if call.parent_run_id:
                parent = await session.get(Run, call.parent_run_id)
                if parent is not None and parent.org_id:
                    org_id = parent.org_id
            session.add(
                Run(
                    id=child_run_id,
                    org_id=org_id,
                    workflow_id=workflow_id,
                    parent_run_id=call.parent_run_id,
                    mode="subworkflow",
                    trigger_type="subworkflow",
                    status="running",
                )
            )
            await session.commit()
    return child_run_id


async def _finalize_child_run(child_run_id: str, status: str) -> None:
    from app.tenancy import run_as_system

    try:
        with run_as_system():
            async with SessionLocal() as session:
                run = await session.get(Run, child_run_id)
                if run is not None:
                    run.status = status
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must not mask the result
        logger.exception("child run finalize failed run_id=%s", child_run_id)


async def resolve_subworkflow(
    call: SubworkflowCall, *, parent_env_id: str | None = None
) -> Any:
    """``SubworkflowRunner`` for the API host.

    Execution paths (the engine adapter already did cycle/depth checks):

    * **Inline directive** — subprocess mode + parent and child share an
      env: return the prepared graph; the parent's engine runs it itself.
      Zero subprocess spawns. (The pre-A3 "no nested workflow calls"
      restriction is gone: chain/depth travel in the protocol now.)
    * **Spawn-fresh subprocess** — subprocess mode otherwise; goes through
      ``dispatch_subworkflow`` (global-cap bypass + org-aware soft throttle).
    * **In-process** — tests / ``use_subprocess_runner=False``.
    """
    async with SessionLocal() as session:
        workflow = await session.get(Workflow, call.workflow_id)
        sub_env_id = workflow.environment_id if workflow else None
        graph_dict, pinned_cache = await _load_workflow_graph(
            session, call.workflow_id, use_published=call.use_published
        )
        graph_dict = await resolve_credential_refs(
            session, graph_dict, workflow_id=call.workflow_id
        )
        pinned_cache = await resolve_credential_refs(
            session, pinned_cache, workflow_id=call.workflow_id
        )
        await session.commit()

    graph = WorkflowGraph.model_validate(graph_dict)
    sources = {edge.source for edge in graph.edges}

    cache: dict[str, dict] = deserialize_value(dict(pinned_cache))
    trigger = first_trigger_node(graph)
    if trigger is not None and trigger.id not in cache:
        cache[trigger.id] = {
            "main": call.parameters if call.parameters is not None else {}
        }
    sub_targets = (
        resolve_trigger_targets(graph_dict, trigger.id, None)
        if trigger is not None
        else None
    )

    if (
        settings.use_subprocess_runner
        and parent_env_id is not None
        and parent_env_id == sub_env_id
    ):
        logger.info(
            "sub-workflow inline workflow_id=%s env_id=%s depth=%s",
            call.workflow_id, sub_env_id, call.depth,
        )
        return InlineSubworkflow(
            graph=graph_dict,
            cache=cache or None,
            targets=sub_targets,
            sources=tuple(sorted(sources)),
        )

    child_run_id = await _create_child_run(call, call.workflow_id)
    child_meta = SubworkflowMeta(
        use_published=call.use_published,
        parent_run_id=child_run_id,
        depth=call.depth,
        call_chain=call.call_chain,
        max_depth=settings.max_subworkflow_depth,
    )
    status = "error"
    try:
        if settings.use_subprocess_runner:
            from app.services.runtime_pool import pool as runtime_pool

            node_status: dict[str, str] = {}
            node_outputs: dict[str, dict] = {}

            async def collect(event: dict) -> None:
                if event.get("type") != "node_finished":
                    return
                nid = event.get("node_id")
                if not isinstance(nid, str):
                    return
                node_status[nid] = str(event.get("status") or "")
                outputs = deserialize_value(event.get("outputs"))
                if isinstance(outputs, dict):
                    node_outputs[nid] = outputs

            logger.info(
                "sub-workflow spawn workflow_id=%s env_id=%s parent_env_id=%s",
                call.workflow_id, sub_env_id, parent_env_id,
            )
            status = await runtime_pool.dispatch_subworkflow(
                child_run_id,
                sub_env_id,
                graph_dict,
                cache or None,
                sub_targets,
                collect,
                subworkflow_resolver=resolve_subworkflow,
                subworkflow_meta=child_meta,
            )
            return extract_leaf_value(sources, node_status, node_outputs)

        # In-process path (tests / dev). Deliberately NOT wrapped in
        # runtime_pool.global_slot() — see module docstring (§7 bypass).
        from app.services import runner as _runner  # late: avoid import cycle

        result = await execute(
            graph,
            _runner.node_registry,
            cache=cache or None,
            targets=sub_targets,
            default_timeouts=_runner._engine_default_timeouts(),
            process_isolator=_runner.process_isolator,
            subworkflow_runner=resolve_subworkflow,
            subworkflow_meta=child_meta,
        )
        status = str(result.status)
        node_status = {nid: str(r.status) for nid, r in result.nodes.items()}
        node_outputs = {nid: dict(r.outputs) for nid, r in result.nodes.items()}
        return extract_leaf_value(sources, node_status, node_outputs)
    finally:
        await _finalize_child_run(child_run_id, status)
```

> Executor note: `dispatch_subworkflow`'s new kwargs land in Task 5 — to keep this commit green, run only the in-process tests now (the suite default is `use_subprocess_runner=False`) and leave the inline/spawn tests marked with a `pytest.mark.skip(reason="enabled in Task 5")` that Task 5 removes, OR implement Tasks 4+5 before running subprocess-path tests. Check `app/tenancy.py` for the exact names (`DEFAULT_ORG_ID`, `run_as_system`) — both exist per the MT work, but confirm import paths. Also verify `Run.trigger_type` accepts "subworkflow" (String(20) — fits) and that no DB-level enum constrains `mode`.

- [ ] **Step 4: Run the new tests (in-process ones)**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_subworkflow_resolution.py -v`
Expected: in-process tests PASS; inline test skipped (or passing if Task 5 was folded in).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/subworkflows.py apps/api/tests/test_subworkflow_resolution.py
git commit -m "feat(api): host sub-workflow resolver with child Run rows (A3)"
```

## Task 5: Rewire the subprocess path end-to-end (pool + runtime server + runner + executors)

This task is one coherent protocol change — the pieces only work together, so it lands as a single commit. After it, `runner.py` no longer contains any sub-workflow machinery.

**Files:**
- Modify: `apps/api/app/services/runtime_pool.py` (lines ~46–49, 318–366, 368–414, 764–817, 819–875)
- Modify: `packages/runtime/noodle_runtime/server.py` (lines ~51–67, 112–128, 167, 238, 243–307)
- Modify: `apps/api/app/services/runner.py` (delete lines ~165–426 cluster; rewire ~984–1125)
- Modify: `apps/api/app/services/executors/base.py`, `apps/api/app/services/executors/local.py`
- Modify: `apps/api/app/services/providers/agent.py` (`resolve_remote_subworkflow`, ~line 271)
- Test: `apps/api/tests/test_architecture_fixes.py` (rewrite ~lines 180–500)

- [ ] **Step 1: runtime_pool.py — resolver signature + protocol fields**

1. Replace the `SubWorkflowCaller` alias (line ~49) with:

```python
# Host-side sub-workflow resolver: (SubworkflowCall, *, parent_env_id) →
# leaf value | InlineSubworkflow directive. Implemented by
# app.services.subworkflows.resolve_subworkflow.
SubworkflowResolver = Callable[..., Awaitable[Any]]
```

2. `_RuntimeProcess._handle_call_workflow` — build the call from the event, drop the runner.py late import:

```python
    async def _handle_call_workflow(
        self,
        event: dict,
        subworkflow_resolver: SubworkflowResolver | None,
    ) -> None:
        from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowCall

        callback_id = event.get("callback_id", "")
        try:
            if subworkflow_resolver is None:
                raise RuntimeError(
                    "subprocess runner has no host-side sub-workflow resolver"
                )
            call = SubworkflowCall.from_payload(
                {**event, "input": deserialize_value(event.get("input"))}
            )
            outcome = await subworkflow_resolver(call, parent_env_id=self.env_id)
            if isinstance(outcome, InlineSubworkflow):
                await self._write_message(
                    {
                        "type": "call_workflow_response",
                        "callback_id": callback_id,
                        "inline_graph": outcome.graph,
                        "inline_cache": outcome.cache,
                        "inline_targets": outcome.targets,
                        "inline_sources": list(outcome.sources),
                    }
                )
            else:
                await self._write_message(
                    {
                        "type": "call_workflow_response",
                        "callback_id": callback_id,
                        "result": outcome,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - surface back to subprocess
            await self._write_message(
                {
                    "type": "call_workflow_error",
                    "callback_id": callback_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
```

3. `_RuntimeProcess.run(...)`: rename param `sub_workflow_caller` → `subworkflow_resolver`, add `subworkflow_meta: dict | None = None`, and add to the run message dict: `"subworkflow_meta": subworkflow_meta or {}`.

4. `RuntimePool.dispatch(...)` and `RuntimePool.dispatch_subworkflow(...)`: same rename; `dispatch` gains `subworkflow_meta: dict | None = None` passed through to `proc.run`; `dispatch_subworkflow` gains `subworkflow_meta: "SubworkflowMeta | None" = None` and passes `subworkflow_meta.to_payload() if subworkflow_meta else {}`. **Do not touch** `subworkflow_slot`, `_org_subworkflow_cap`, `_global_sem`, or the spawn/close lifecycle — they are the preserved cap machinery.

- [ ] **Step 2: noodle_runtime/server.py — meta in, RPC runner out, `_resolve_inline` deleted**

1. Replace `_call_workflow_via_host` with a `SubworkflowRunner`:

```python
async def _run_subworkflow_via_host(call: "SubworkflowCall") -> Any:
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
```

2. `_resolve_callback`: replace the `"inline_graph" in message` branch (which spawned `_resolve_inline`) with:

```python
    elif "inline_graph" in message:
        future.set_result(
            InlineSubworkflow(
                graph=message["inline_graph"],
                cache=deserialize_value(message.get("inline_cache")) or None,
                targets=message.get("inline_targets") or None,
                sources=tuple(message.get("inline_sources") or ()),
            )
        )
```

3. Delete `_resolve_inline` entirely.

4. In `_handle_run`: delete `caller_token = workflow_caller.set(_call_workflow_via_host)` (and its reset); parse the meta and hand both to the engine:

```python
    sub_meta = SubworkflowMeta.from_payload(request.get("subworkflow_meta") or {})
    ...
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
```

5. Imports: add `from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowCall, SubworkflowMeta`; remove the now-unused `workflow_caller` import (keep `org_run_limits`, `artifact_store`). Keep `_HOST_CALLBACK_NODE_TYPES` / `_needs_host_callbacks` unchanged.

- [ ] **Step 3: executors — thread the meta**

`executors/base.py`: add to `RunExecutionContext`:

```python
    subworkflow_meta: dict | None  # SubworkflowMeta.to_payload() for the run protocol
```

`executors/local.py`: constructor param rename `sub_workflow_caller` → `subworkflow_resolver` (attribute `self._subworkflow_resolver`), and dispatch:

```python
        status = await self._pool.dispatch(
            ctx["run_id"],
            ctx["environment_id"],
            ctx["graph"],
            ctx["cache"],
            ctx["targets"],
            on_event,
            subworkflow_resolver=self._subworkflow_resolver,
            subworkflow_meta=ctx.get("subworkflow_meta"),
            workflow_modules=ctx["workflow_modules"],
            run_timeout=ctx["run_timeout"],
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
```

- [ ] **Step 4: runner.py — delete the splice, wire the meta**

1. Delete: `_prefer_draft_graphs` (~165–172), `_load_workflow_graph` (~175–210), `InlineSubWorkflow` (~213–229), `_has_nested_workflow_call` (~232–245), `_extract_sub_leaf` (~248–269), `_call_sub_workflow` (~272–416). Remove now-unused imports (`deque` if unused, `selectinload`, `PinnedData`, `first_trigger_node` if no longer used here — verify each with a grep before removing).

2. `local_executor` construction (~line 422):

```python
from app.services.subworkflows import meta_for_root_run, resolve_subworkflow

local_executor = LocalExecutor(
    pool=runtime_pool,
    subworkflow_resolver=resolve_subworkflow,
    active_runs=_active_runs,
)
```

3. In `_execute_run`: it currently does `prefer_draft_token = _prefer_draft_graphs.set(prefer_draft)` near the top (grep `prefer_draft_token`) — delete the token set/reset pairs (including the early-return reset at ~line 1029) and instead build the meta once:

```python
    sub_meta = meta_for_root_run(
        run_id=run_id, workflow_id=workflow_id, prefer_draft=prefer_draft
    )
```

4. Subprocess branch (~984): delete `chain_token = call_chain.set(...)` / `caller_token = workflow_caller.set(...)` and their `finally` resets — the engine (in the runtime subprocess) owns both now. Add `subworkflow_meta=sub_meta.to_payload()` to `_build_ctx(...)` calls (and add the key to `_build_ctx`'s signature/dict — grep `def _build_ctx`).

5. In-process branch (~1076): delete `chain_token`/`caller_token` sets and resets; add to the `execute(...)` call:

```python
                        subworkflow_runner=resolve_subworkflow,
                        subworkflow_meta=sub_meta,
```

6. Remove `workflow_caller`/`call_chain` from the `noodle.context` import line if now unused in runner.py.

- [ ] **Step 5: providers/agent.py — remote broker speaks SubworkflowCall**

Replace the body of `resolve_remote_subworkflow` (~line 271):

```python
async def resolve_remote_subworkflow(conn: _AgentConnection, msg: dict) -> None:
    """Run a sub-workflow host-side for a remote runner and reply.

    With no ``parent_env_id`` the resolver never answers with an inline
    directive — the result is always a concrete leaf value that serializes
    cleanly back over the WS.
    """
    from app.services.subworkflows import resolve_subworkflow  # noqa: PLC0415
    from noodle.engine.subworkflows import SubworkflowCall  # noqa: PLC0415

    callback_id = msg.get("callback_id", "")
    try:
        call = SubworkflowCall.from_payload(msg)
        _timeout = settings.subworkflow_spawn_timeout_seconds or None
        coro = resolve_subworkflow(call)
        result = await (
            asyncio.wait_for(coro, timeout=_timeout) if _timeout else coro
        )
        await conn.send({
            "type": "call_workflow_response",
            "callback_id": callback_id,
            "result": serialize_value(result),
        })
    except Exception as exc:  # noqa: BLE001 - surface back to the runner
        await conn.send({
            "type": "call_workflow_error",
            "callback_id": callback_id,
            "error": f"{type(exc).__name__}: {exc}",
        })
```

Also add `"subworkflow_meta": subworkflow_meta,` to the `run_assigned` payload in `run_on_agent` (~line 104) and to `providers/k8s.py`'s `run_assigned` (~line 123) — both functions receive their payload fields from the dispatcher; thread a `subworkflow_meta` argument the same way `agent_action_resume` flows (grep its path through `remote_dispatch.py` / `executors/remote.py` and mirror it, sourcing from `ctx["subworkflow_meta"]`).

- [ ] **Step 6: Rewrite the four splice-pinning tests in `test_architecture_fixes.py`**

Keep each test's *scenario*; port the mechanics:

| Old test (~line) | New assertion |
|---|---|
| `..._call_sub_workflow` dispatches via fake `dispatch_subworkflow` (184) | `resolve_subworkflow(call)` with `use_subprocess_runner=True` + monkeypatched `runtime_pool.dispatch_subworkflow` → fake receives `subworkflow_resolver`/`subworkflow_meta` kwargs; leaf value extracted from collected events |
| in-process leaf test (308) | `resolve_subworkflow(call)` returns the leaf (covered also by Task 4's test — keep one, drop the duplicate) |
| inline when same env + no nested calls (345) | same-env → `isinstance(outcome, InlineSubworkflow)` — **including when the sub contains `execute_workflow` nodes** (restriction lifted; invert the old companion test) |
| spawns when nested calls present (378) | replace with: nested-call sub on the same env returns an inline directive; different env still goes through `dispatch_subworkflow` |
| pool serializes inline sentinel (458) | construct `InlineSubworkflow` (core), feed a fake event through `_RuntimeProcess._handle_call_workflow` with a resolver returning it, assert `inline_graph`/`inline_sources` in the written message |

Build `SubworkflowCall`s in these tests via the `_call` helper pattern from Task 4.

- [ ] **Step 7: Run the full API suite + core suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: pass count ≥ baseline (556 + new − 1 known pre-existing failure). **`tests/test_subworkflows.py` must pass with zero edits.** If any run-listing test breaks because child Run rows now appear, stop and reassess (likely fix: the listing endpoint should exclude `mode == "subworkflow"` only if a test proves today's UX depends on it — do not silently change list semantics otherwise).
Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q` — unchanged.
Also remove the Task 4 skips (inline/spawn tests) and re-run `tests/test_subworkflow_resolution.py`.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services apps/api/tests packages/runtime/noodle_runtime/server.py
git commit -m "feat: sub-workflow resolution flows through the engine callback on all local paths (A3)"
```

## Task 6: Remote runner plumbing (agent package)

**Files:**
- Modify: `packages/runner/noodle_runner_agent/process_pool.py` (~lines 25, 38, 65–97)
- Modify: `packages/runner/noodle_runner_agent/agent.py` (~lines 99–114, 151–171)
- Modify: `packages/runner/noodle_runner_agent/k8s_entrypoint.py` (~line 65)

- [ ] **Step 1: process_pool.py — forward the full call payload + meta**

1. `CallWorkflow = Callable[[dict], Awaitable[Any]]` (the broker now receives the whole call payload, not `(workflow_id, input)`).
2. `run_workflow_subprocess(...)` gains `subworkflow_meta: dict | None = None`; add `"subworkflow_meta": subworkflow_meta or {}` to `run_msg`.
3. `handle_call_workflow`: forward everything except the callback id:

```python
    async def handle_call_workflow(event: dict[str, Any]) -> None:
        callback_id = event.get("callback_id", "")
        try:
            if call_workflow is None:
                raise RuntimeError("no sub-workflow broker on this runner")
            payload = {k: v for k, v in event.items() if k not in ("type", "callback_id")}
            result = await call_workflow(payload)
            await write_msg({
                "type": "call_workflow_response",
                "callback_id": callback_id,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001 - surface back to the runtime
            await write_msg({
                "type": "call_workflow_error",
                "callback_id": callback_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
```

- [ ] **Step 2: agent.py — broker passes the payload through the WS**

```python
    async def _broker_subworkflow(self, run_id: str, payload: dict) -> Any:
        """Ask the API to run a sub-workflow and return its result."""
        callback_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_calls[callback_id] = fut
        await self._client.send({
            "type": "call_workflow",
            "run_id": run_id,
            "callback_id": callback_id,
            **payload,
        })
        try:
            return await fut
        finally:
            self._pending_calls.pop(callback_id, None)
```

In `_handle_run`, the local `broker` closure becomes `async def broker(payload: dict) -> Any: return await self._broker_subworkflow(run_id, payload)`, and the `run_workflow_subprocess(...)` call gains `subworkflow_meta=msg.get("subworkflow_meta") or {}`.

- [ ] **Step 3: k8s_entrypoint.py** — its `run_workflow_subprocess(...)` call (~line 65) gains `subworkflow_meta=msg.get("subworkflow_meta") or {}` (and the same broker-signature update if it wires `call_workflow` — check the surrounding code).

- [ ] **Step 4: Run runner-package tests (if any) + API suite**

Run: `cd packages\runner; ..\..\..\.venv\Scripts\python.exe -m pytest tests -q` (skip if the package has no tests — check first).
Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q` — remote-dispatch tests (`test_audit_wave1.py` asserts `run_assigned` payload contents per the MT audit) must still pass; extend their expected payload with `subworkflow_meta` if they assert exact keys.

- [ ] **Step 5: Commit**

```bash
git add packages/runner apps/api
git commit -m "feat(runner): remote sub-workflow broker carries SubworkflowCall fields + meta (A3)"
```

## Task 7: Exporter — bundled sub-workflows resolved locally

**Files:**
- Modify: `packages/exporter/noodle_exporter/codegen.py`
- Modify: `packages/exporter/noodle_exporter/__init__.py` (exports unchanged — verify)
- Modify: `apps/api/app/routers/export.py`
- Test: `packages/exporter/tests/test_codegen.py`, `apps/api/tests/test_export_subworkflows.py` (create)

- [ ] **Step 1: Write the failing exporter round-trip test** (append to `packages/exporter/tests/test_codegen.py`)

```python
import subprocess
import sys
from pathlib import Path

from noodle_exporter import workflow_to_script

PARENT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {"data": 5},
         "position": {"x": 0, "y": 0}},
        {"id": "sub", "type": "execute_workflow",
         "params": {"workflow_id": "child-1"}, "position": {"x": 200, "y": 0}},
    ],
    "edges": [
        {"id": "e", "source": "t", "source_output": "main",
         "target": "sub", "target_input": "input"},
    ],
}

CHILD_GRAPH = {
    "nodes": [
        {"id": "ct", "type": "manual_trigger", "params": {},
         "position": {"x": 0, "y": 0}},
        {"id": "cc", "type": "code", "params": {"code": "output = input * 3"},
         "position": {"x": 200, "y": 0}},
    ],
    "edges": [
        {"id": "ce", "source": "ct", "source_output": "main",
         "target": "cc", "target_input": "input"},
    ],
}


def test_script_with_bundled_subworkflow_round_trips(tmp_path: Path) -> None:
    """A3 acceptance: exported workflow containing a workflow_call runs."""
    script = workflow_to_script(
        PARENT_GRAPH, "Parent", subworkflows={"child-1": CHILD_GRAPH},
        root_id="parent-1",
    )
    path = tmp_path / "parent.py"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "workflow finished: success" in proc.stdout
    assert "sub: success" in proc.stdout


def test_script_without_subworkflows_unchanged_behavior(tmp_path: Path) -> None:
    script = workflow_to_script(PARENT_GRAPH, "Parent")
    assert "SUBWORKFLOWS" in script  # resolver always present, empty bundle
    assert '"child-1"' not in script.split("SUBWORKFLOWS")[1].split("=")[1][:20]
```

> Executor note: look at the existing tests in this file first — if they assert exact template content, update them for the new template. The second test above is a sketch; pin it to whatever clean assertion fits ("SUBWORKFLOWS = {}" appears).

- [ ] **Step 2: Run to verify failure**

Run: `cd packages\exporter; ..\..\.venv\Scripts\python.exe -m pytest tests -v`
Expected: FAIL — `workflow_to_script() got an unexpected keyword argument 'subworkflows'`.

- [ ] **Step 3: Rewrite the script template in `codegen.py`**

```python
_SCRIPT_TEMPLATE = '''"""Noodle workflow: __NAME__

Generated by Noodle. Run with:  python __SLUG__.py
Requires: noodle-core and noodle-nodes (plus your environment packages).
"""

import noodle_nodes  # noqa: F401 - registers the built-in nodes
from noodle.engine import run
from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowMeta
from noodle.models import WorkflowGraph
from noodle.sdk import registry

GRAPH = __GRAPH__

# Sub-workflows referenced by execute_workflow / map_* nodes, bundled at
# export time and resolved locally — no Noodle server required.
SUBWORKFLOWS = __SUBWORKFLOWS__

ROOT_ID = __ROOT_ID__

_TRIGGER_TYPES = (
    "manual_trigger", "webhook_trigger", "api_endpoint",
    "schedule_trigger", "chat_trigger",
)


def _seed_trigger(graph, value):
    for node in graph.get("nodes", []):
        node_type = str(node.get("type") or "")
        if node_type in _TRIGGER_TYPES or node_type.endswith("_trigger"):
            return {node["id"]: {"main": value if value is not None else {}}}
    return None


async def _run_subworkflow(call):
    graph = SUBWORKFLOWS.get(call.workflow_id)
    if graph is None:
        raise RuntimeError(
            f"sub-workflow '{call.workflow_id}' is not bundled in this export"
        )
    return InlineSubworkflow(
        graph=graph,
        cache=_seed_trigger(graph, call.parameters),
        targets=None,
        sources=tuple(
            str(e.get("source")) for e in graph.get("edges", [])
        ),
    )


def main() -> None:
    meta = SubworkflowMeta(call_chain=frozenset({ROOT_ID} if ROOT_ID else ()))
    result = run(
        WorkflowGraph.model_validate(GRAPH),
        registry,
        subworkflow_runner=_run_subworkflow,
        subworkflow_meta=meta,
    )
    print(f"workflow finished: {result.status}")
    for node_id, node in result.nodes.items():
        print(f"  {node_id}: {node.status}")
        if node.error:
            print(f"    error: {node.error}")


if __name__ == "__main__":
    main()
'''
```

And the function:

```python
def workflow_to_script(
    graph: dict,
    name: str,
    *,
    subworkflows: dict[str, dict] | None = None,
    root_id: str | None = None,
) -> str:
    """Render a workflow graph as a standalone Python script.

    ``subworkflows`` maps workflow id → graph for every workflow reachable
    through execute_workflow / map_* params; they run locally via the
    engine's inline-subworkflow directive.
    """
    return (
        _SCRIPT_TEMPLATE.replace("__NAME__", name)
        .replace("__SLUG__", slugify(name))
        .replace("__GRAPH__", json.dumps(graph, indent=2))
        .replace("__SUBWORKFLOWS__", json.dumps(subworkflows or {}, indent=2))
        .replace("__ROOT_ID__", json.dumps(root_id))
    )
```

`docker_bundle(...)` gains the same two keyword args and forwards them to `workflow_to_script`.

- [ ] **Step 4: Run exporter tests**

Run: `cd packages\exporter; ..\..\.venv\Scripts\python.exe -m pytest tests -v`
Expected: all PASS, including the subprocess round-trip.

- [ ] **Step 5: Export router collects referenced graphs**

In `apps/api/app/routers/export.py`:

```python
_SUBWORKFLOW_PARAM_KEYS: dict[str, str] = {
    "execute_workflow": "workflow_id",
    "map_items": "workflow_id",
    "map_dataset": "workflow_id",
    "map_group": "child_workflow_id",
}


def _referenced_workflow_ids(graph: dict) -> set[str]:
    out: set[str] = set()
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = _SUBWORKFLOW_PARAM_KEYS.get(str(node.get("type") or ""))
        if key:
            wid = str((node.get("params") or {}).get(key) or "")
            if wid:
                out.add(wid)
    return out


async def _collect_subworkflows(
    session: AsyncSession, graph: dict
) -> dict[str, dict]:
    """Every workflow graph reachable from ``graph`` via sub-workflow params.

    Unresolvable ids are skipped — the generated script raises a clear
    "not bundled" error if the node actually fires. The visited set guards
    against reference cycles (the runtime engine would refuse them anyway).
    """
    out: dict[str, dict] = {}
    pending = _referenced_workflow_ids(graph)
    while pending:
        wid = pending.pop()
        if wid in out:
            continue
        workflow = await session.get(
            Workflow, wid, options=[selectinload(Workflow.versions)]
        )
        if workflow is None:
            continue
        child = (
            workflow.draft_graph
            or (workflow.versions[-1].graph if workflow.versions else None)
            or EMPTY_GRAPH
        )
        out[wid] = child
        pending |= _referenced_workflow_ids(child) - set(out)
    return out
```

Both endpoints pass the bundle:

```python
    subs = await _collect_subworkflows(session, graph)
    script = workflow_to_script(
        graph, workflow.name, subworkflows=subs, root_id=workflow.id
    )
```

(and `docker_bundle(graph, workflow.name, ..., subworkflows=subs, root_id=workflow.id)`).

- [ ] **Step 6: Write + run the router test**

```python
# apps/api/tests/test_export_subworkflows.py
"""A3: exported scripts bundle referenced sub-workflow graphs."""

from httpx import AsyncClient


async def test_export_script_bundles_subworkflow_graphs(
    client: AsyncClient,
) -> None:
    sub = (await client.post("/workflows", json={"name": "Child"})).json()
    await client.put(
        f"/workflows/{sub['id']}",
        json={"graph": {"nodes": [
            {"id": "ct", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}], "edges": []}},
    )
    parent = (await client.post("/workflows", json={"name": "Par"})).json()
    await client.put(
        f"/workflows/{parent['id']}",
        json={"graph": {"nodes": [
            {"id": "s", "type": "execute_workflow",
             "params": {"workflow_id": sub["id"]},
             "position": {"x": 0, "y": 0}}], "edges": []}},
    )

    resp = await client.get(f"/workflows/{parent['id']}/export.py")
    assert resp.status_code == 200
    script = resp.text
    assert sub["id"] in script  # bundled graph keyed by id
    assert "SUBWORKFLOWS" in script
    assert f'"{parent["id"]}"' in script  # ROOT_ID
```

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_export_subworkflows.py tests/test_export.py -v` (confirm the existing export test filename via `ls apps/api/tests | grep export` — run whatever exists).
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/exporter apps/api/app/routers/export.py apps/api/tests/test_export_subworkflows.py
git commit -m "feat(exporter): bundle sub-workflow graphs; exports resolve workflow_call locally (A3)"
```

## Task 8: Full verification + docs touch-up

- [ ] **Step 1: All suites**

```powershell
cd packages\core;     ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\nodes;    ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\exporter; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd apps\api;          ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd apps\web;          npm run typecheck; npm run test
```

Expected: green everywhere (modulo the documented pre-existing API failure). Frontend untouched — typecheck/test are regression guards only.

- [ ] **Step 2: E2E smoke (subprocess mode — exercises the real protocol)**

Run from `apps/web`: `npm run test:e2e`
Expected: 3 passed. This boots the real API + Vite per the Phase 0 config and covers run-and-observe over the rewired subprocess path.

- [ ] **Step 3: Update HANDOFF.md + architecture notes**

In `HANDOFF.md`: update the line 197 bullet to say the bypass is unchanged but sub-workflow *semantics* (cycle/depth/inline) now live in `noodle.engine.subworkflows` with hosts supplying resolvers; update the `runtime_pool.py` row in the table if it names `sub_workflow_caller`. In `docs/architecture.md` (grep for "sub-workflow"/"execute_workflow" sections): describe the resolver contract and child Run rows in 3–5 sentences.

- [ ] **Step 4: Tick Phase 4 in the master plan**

In `docs/superpowers/plans/2026-06-10-production-architecture-program.md`, no checkboxes exist for Phase 4 (it's prose) — leave the document as-is; status tracking lives in the memory file (updated post-merge, not part of this plan's commits).

- [ ] **Step 5: Commit docs**

```bash
git add HANDOFF.md docs/architecture.md
git commit -m "docs: sub-workflow resolution via engine callback (A3)"
```

---

## Self-review notes

- **Spec coverage:** engine callback param ✓ (Task 2), `SubworkflowCall` fields ✓ (Task 1; +`call_chain`, documented), splice retirement ✓ (Task 5 deletes `InlineSubWorkflow`/`_call_sub_workflow`), API resolver with DB lookup + child Run rows + org caps ✓ (Task 4), runtime RPC resolver ✓ (Task 5), exporter local resolution ✓ (Task 7), global-cap bypass preserved ✓ (untouched `dispatch_subworkflow`/no `global_slot` on the in-process child path), org caps preserved ✓ (untouched `subworkflow_slot`), depth limiting ✓ (engine adapter + config), acceptance tests ✓ (Task 2 stub-resolver test, Task 5 zero-edit `test_subworkflows.py`, Task 7 exporter round-trip).
- **Known executor adjustments flagged inline:** pytest-asyncio mode in core tests; registry/isolator type import paths; alembic 0047 conventions; draft-vs-published test mechanics; `run_assigned` payload-shape tests in `test_audit_wave1.py`; existing exporter template tests; whether run-listing tests react to child Run rows.
- **Sequencing risk:** Task 4's inline/spawn tests depend on Task 5's pool signature — handled via explicit skips removed in Task 5 Step 7.
- **Out of scope (explicitly):** child node-run persistence/event streaming, sub-workflow run metering, multi-trigger gating in exports, protocol versioning for mixed-version remote runners (monorepo ships runtime+agent together; old agents against a new API degrade to chain-less calls and should be redeployed).
