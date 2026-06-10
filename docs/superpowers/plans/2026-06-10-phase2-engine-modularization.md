# Phase 2: Engine Modularization + Dependency Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 2,160-line `packages/core/noodle/engine.py` monolith into a `noodle/engine/` package (B2), replace the level-barrier scheduler with dependency counting (B1), move process-pool ownership out of the engine behind an injectable `ProcessIsolator` (B4), and add a fast path to the node-output size check (B5).

**Architecture:** Pure-move package split first (existing tests are the contract), then three behavior changes layered on the new structure, each TDD'd. The scheduler keeps loop-region semantics by contracting each loop region into its `loop_start` node as a single scheduling unit. Pool ownership moves to a new `noodle/process_isolation.py` whose eviction is keyed off task completion (fixing the cold-pool-kill bug for long-running code nodes).

**Tech Stack:** Python 3.12, asyncio, pytest (asyncio_mode=auto via root pyproject.toml), hatchling packaging.

---

## Context for the engineer (read first)

- **Repo:** `D:\noodle`, branch off `feat/multi-tenancy`. Monorepo: `packages/core` (engine, SDK), `packages/nodes`, `packages/runtime` (warm env subprocess), `packages/exporter`, `apps/api` (FastAPI), `apps/web`.
- **Running tests on this machine:** `uv run` is broken. Use the repo-root venv directly, per package dir:
  - core: `cd D:\noodle\packages\core` then `..\..\.venv\Scripts\python.exe -m pytest tests -q` (304 tests, ~5s)
  - nodes: `cd D:\noodle\packages\nodes` then `..\..\.venv\Scripts\python.exe -m pytest tests -q`
  - api: `cd D:\noodle\apps\api` then `..\..\.venv\Scripts\python.exe -m pytest tests -q` (~6 min; **one pre-existing failure** `tests/test_architecture_fixes.py::test_dispatch_webhook_passes_shared_session_to_resolve_node_auth` — fails at the branch base too, ignore it)
  - e2e: `cd D:\noodle\apps\web` then `npm run test:e2e` (3 tests, ~35s; uses ports 8123/5191)
- **Never kill anything on port 8001** — that is the user's separate farm-report-api project.
- The core package installs editable into the root `.venv`; no reinstall needed after the split (hatchling `packages = ["noodle"]` includes subpackages).
- Function-level imports are idiomatic in this codebase (engine.py already does `from noodle.expr import ...` inside functions). Use them where noted to break import cycles.
- pytest async tests need no decorator (`asyncio_mode = "auto"` in root pyproject.toml).

### Current engine.py symbol map (line numbers at branch base)

| Lines | Symbols |
|---|---|
| 53 | `EventCallback` |
| 58–67 | `_STATUS_RANK`, `_worse_status` |
| 69 | `PROCESS_ISOLATED_NODE_TYPES` |
| 75–88 | `AUTO_PROMOTE_NODE_TYPES`, `DATASET_PASSTHROUGH_NODE_TYPES`, `DATASET_AUTO_EXPAND_CAP` |
| 93–98 | `_process_pools`, `_pool_last_used`, `_POOL_IDLE_SECONDS` |
| 104 | `_MAX_AGENT_LOOP_ITERATIONS` |
| 107–128 | `_LengthCountingSink`, `_approx_encoded_length` |
| 131–169 | `_get_process_pool`, `_evict_pool` |
| 175–177 | `pool_key` (ContextVar) |
| 184–187 | `DEFAULT_NODE_TIMEOUTS` |
| 193–228 | `_log_capture`, `_CaptureProxy`, `_install_capture` |
| 231–233 | `GraphError` |
| 235–373 | `AI_PORT_KINDS`, `_port_kind`, `_find_port`, `_kind_label`, `_connection_kind_error`, `_validate_connection_kinds` |
| 376–417 | `_predecessors`, `_descendants`, `_ancestors` |
| 384–389 | `LoopRegion` (dataclass, between _predecessors and _descendants) |
| 420–522 | `_expand_graph_dict`, `_expand_metanodes` |
| 525–601 | `_loop_regions`, `_validate_loop_regions` |
| 604–695 | `_topo_order`, `_topo_levels`, `_needed_nodes` |
| 698–705 | `_normalize_outputs` |
| 708–734 | `_auto_expand_dataset_inputs` |
| 737–795 | `_validate_input_kinds`, `_validate_output_kinds` |
| 798–829 | `_auto_promote_outputs` |
| 832–839 | `_node_timeout` |
| 842–884 | `_collect_tool_adapters`, `_tool_index`, `_tool_content`, `_agent_approval_key` |
| 887–1058 | `_dispatch_agent_action_request` |
| 1061–1416 | `_run_one_node` |
| 1419–1423 | `_LoopRowError` |
| 1426–1449 | `_restricted_levels` |
| 1452–1514 | `_as_loop_rows`, `_loop_items` |
| 1517–1738 | `_run_loop` |
| 1741–1745 | `_expr_error` |
| 1748–1883 | `_run_conditional_loop` |
| 1886–1964 | `_run_metanode` |
| 1967–2034 | `_execute_nodes` |
| 2037–2129 | `execute` |
| 2132–2140 | `run` |

### External consumers of `noodle.engine` (must keep working unmodified through Task 1–2)

- `from noodle.engine import GraphError, execute, run` — `packages/core/noodle/__init__.py`
- `from noodle.engine import DEFAULT_NODE_TIMEOUTS, execute, pool_key as engine_pool_key` — `apps/api/app/services/runner.py:92` (pool_key import changes in Task 3)
- `from noodle.engine import execute` — `packages/runtime/noodle_runtime/server.py:52`
- `from noodle.engine import run` — `packages/exporter/noodle_exporter/codegen.py:18`
- Tests import: `_worse_status`, `_topo_order`, `_loop_items`, `_loop_regions`, `_validate_loop_regions`, `_expand_metanodes`, `_validate_input_kinds`, `_auto_expand_dataset_inputs`, `_MAX_AGENT_LOOP_ITERATIONS`, `_get_process_pool`, `_evict_pool`, `_process_pools`, `_pool_last_used`, `DEFAULT_NODE_TIMEOUTS`
- The only monkeypatching is **in-place mutation** (`monkeypatch.setitem(engine.DEFAULT_NODE_TIMEOUTS, ...)`, `engine._process_pools.clear()`), which survives re-export of the same objects. No `monkeypatch.setattr` on engine module attributes exists.

### Import-cycle rule for the new package

`loops.py` and `metanodes.py` import from `scheduler.py` at module level (`_execute_nodes`, `_build_plan`, graph helpers, `execute`). `scheduler.py` must therefore **never** import `loops`/`metanodes` at module level — it imports their driver functions lazily inside `_execute_nodes` and `execute` (one cached-module lookup per call, negligible). All other engine submodules (`types`, `pools`, `validation`, `datasets`, `agent`, `node_exec`) import nothing from `scheduler`/`loops`/`metanodes`, so they are cycle-free.

---

## Task 1: Split engine.py into the `noodle/engine/` package (B2 — pure move, zero behavior change)

**Files:**
- Create: `packages/core/tests/test_engine_exports.py`
- Create: `packages/core/noodle/engine/__init__.py`
- Create: `packages/core/noodle/engine/types.py`
- Create: `packages/core/noodle/engine/pools.py`
- Create: `packages/core/noodle/engine/validation.py`
- Create: `packages/core/noodle/engine/datasets.py`
- Create: `packages/core/noodle/engine/agent.py`
- Create: `packages/core/noodle/engine/node_exec.py`
- Create: `packages/core/noodle/engine/loops.py`
- Create: `packages/core/noodle/engine/metanodes.py`
- Create: `packages/core/noodle/engine/scheduler.py`
- Delete: `packages/core/noodle/engine.py`

- [ ] **Step 1: Create the branch**

```powershell
cd D:\noodle
git checkout feat/multi-tenancy
git checkout -b feat/arch-program-phase2
```

- [ ] **Step 2: Write the export-contract test (the refactor contract)**

Create `packages/core/tests/test_engine_exports.py`:

```python
"""B2 refactor contract: every name historically importable from
``noodle.engine`` (public API + host/test-consumed internals) must remain
importable from the package facade after the monolith split."""


def test_engine_facade_exports():
    from noodle import engine

    for name in (
        # public API
        "execute", "run", "GraphError", "EventCallback",
        # host-consumed (runner.py / runtime server)
        "DEFAULT_NODE_TIMEOUTS", "pool_key", "PROCESS_ISOLATED_NODE_TYPES",
        # test-consumed internals
        "_worse_status", "_topo_order", "_needed_nodes", "_execute_nodes",
        "_loop_items", "_loop_regions", "_validate_loop_regions", "LoopRegion",
        "_expand_metanodes",
        "_validate_input_kinds", "_validate_output_kinds",
        "_validate_connection_kinds", "AI_PORT_KINDS",
        "_auto_expand_dataset_inputs", "_auto_promote_outputs",
        "AUTO_PROMOTE_NODE_TYPES", "DATASET_PASSTHROUGH_NODE_TYPES",
        "DATASET_AUTO_EXPAND_CAP",
        "_MAX_AGENT_LOOP_ITERATIONS", "_dispatch_agent_action_request",
        "_approx_encoded_length",
        "_get_process_pool", "_evict_pool", "_process_pools", "_pool_last_used",
    ):
        assert hasattr(engine, name), f"noodle.engine.{name} missing"


def test_package_init_reexports_engine_api():
    import noodle

    assert noodle.execute is not None
    assert noodle.run is not None
    assert noodle.GraphError is not None
```

- [ ] **Step 3: Run it against the monolith — must pass**

```powershell
cd D:\noodle\packages\core
..\..\.venv\Scripts\python.exe -m pytest tests/test_engine_exports.py -q
```
Expected: `2 passed` (every symbol already exists in the monolith).

- [ ] **Step 4: Create `noodle/engine/types.py`**

```python
"""Shared engine types with no internal dependencies."""

from collections.abc import Awaitable, Callable
from typing import Any

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""
```

(Then delete the originals — engine.py lines 53 and 231–233 — when their sections move in later steps.)

- [ ] **Step 5: Create `noodle/engine/pools.py`**

Move lines 90–177 of engine.py verbatim (the two dicts, `_POOL_IDLE_SECONDS`, `_get_process_pool`, `_evict_pool`, `pool_key` with all their comments). Module header:

```python
"""Per-environment process pools for process-isolated node types.

NOTE: this module is deleted in B4 (Task 3) — pool ownership moves to
``noodle.process_isolation`` with host injection. Do not extend it."""

import concurrent.futures
import contextvars
import time
```

- [ ] **Step 6: Create `noodle/engine/validation.py`**

Move verbatim: lines 235–373 (`AI_PORT_KINDS`, `_port_kind`, `_find_port`, `_kind_label`, `_connection_kind_error`, `_validate_connection_kinds`) and lines 737–795 (`_validate_input_kinds`, `_validate_output_kinds`; both keep their function-local `from noodle.artifacts import ...` / `from noodle.datasets import ...` imports). Module header:

```python
"""Port-kind validation: connection compatibility and input/output kinds."""

from typing import Any

from noodle.models import PortSpec, WorkflowGraph
from noodle.sdk import NodeRegistry

from noodle.engine.types import GraphError
```

(`_validate_connection_kinds` raises `GraphError`; check the moved body and keep exactly the names it references.)

- [ ] **Step 7: Create `noodle/engine/datasets.py`**

Move verbatim: lines 71–88 (`AUTO_PROMOTE_NODE_TYPES`, `DATASET_PASSTHROUGH_NODE_TYPES`, `DATASET_AUTO_EXPAND_CAP` with comments), lines 708–734 (`_auto_expand_dataset_inputs`), lines 798–829 (`_auto_promote_outputs`). Both functions keep their function-local noodle.datasets/artifacts/dataset_promote imports. Module header:

```python
"""Dataset-aware input expansion and output auto-promotion for engine nodes."""

from typing import Any
```

- [ ] **Step 8: Create `noodle/engine/agent.py`**

Move verbatim: line 100–104 (`_MAX_AGENT_LOOP_ITERATIONS` with comment), lines 842–1058 (`_collect_tool_adapters`, `_tool_index`, `_tool_content`, `_agent_approval_key`, `_dispatch_agent_action_request`). Module header:

```python
"""Agent action-request dispatch: tool indexing, approval gating, step events."""

import json
import time
from collections.abc import Iterable
from typing import Any

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AgentApprovalRequired,
    AgentStepEvent,
    ToolAdapter,
    ToolResult,
)

from noodle.engine.types import EventCallback
```

- [ ] **Step 9: Create `noodle/engine/node_exec.py`**

Move verbatim: line 69 (`PROCESS_ISOLATED_NODE_TYPES`), lines 107–128 (`_LengthCountingSink`, `_approx_encoded_length`), lines 179–228 (`DEFAULT_NODE_TIMEOUTS` with comment, `_log_capture`, `_CaptureProxy`, `_install_capture`), lines 698–705 (`_normalize_outputs`), lines 832–839 (`_node_timeout`), lines 1061–1416 (`_run_one_node`). Module header:

```python
"""Single-node execution: input wiring, expression eval, retries, timeouts,
process isolation for code nodes, log capture, and output normalization."""

import asyncio
import concurrent.futures
import contextvars
import functools
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
from noodle.sdk import NodeRegistry

from noodle.engine.agent import _MAX_AGENT_LOOP_ITERATIONS, _dispatch_agent_action_request
from noodle.engine.datasets import (
    AUTO_PROMOTE_NODE_TYPES,
    _auto_expand_dataset_inputs,
    _auto_promote_outputs,
)
from noodle.engine.pools import _evict_pool, _get_process_pool, pool_key
from noodle.engine.types import EventCallback
from noodle.engine.validation import _validate_input_kinds, _validate_output_kinds
```

- [ ] **Step 10: Create `noodle/engine/metanodes.py`**

Move verbatim: lines 420–522 (`_expand_graph_dict`, `_expand_metanodes`), lines 1886–1964 (`_run_metanode`). `_run_metanode` calls `execute` — import it from scheduler at module level (safe: scheduler never imports metanodes at module level). Module header:

```python
"""Metanode handling: transparent inlining at plan time, isolated execution
as a nested engine run."""

import time
from typing import Any

from noodle.models import NodeRunResult, NodeStatus, RunStatus, WorkflowGraph

from noodle.engine.scheduler import execute
from noodle.engine.types import EventCallback
```

- [ ] **Step 11: Create `noodle/engine/loops.py`**

Move verbatim: lines 384–389 (`LoopRegion`), lines 525–601 (`_loop_regions`, `_validate_loop_regions`), lines 1419–1449 (`_LoopRowError`, `_restricted_levels`), lines 1452–1514 (`_as_loop_rows`, `_loop_items`), lines 1517–1745 (`_run_loop`, `_expr_error`), lines 1748–1883 (`_run_conditional_loop`). Keep the function-local imports inside `_run_loop`/`_run_conditional_loop`. Module header:

```python
"""Loop regions: discovery, validation, and the for-each / while / until
loop drivers that re-run a body sub-DAG per iteration."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from noodle.ai_runtime import AgentActionRequest
from noodle.context import iteration_path, org_run_limits
from noodle.models import NodeRunResult, NodeStatus, RunStatus, WorkflowGraph
from noodle.sdk import NodeRegistry

from noodle.engine.scheduler import (
    _ancestors,
    _descendants,
    _execute_nodes,
    _predecessors,
)
from noodle.engine.types import EventCallback, GraphError
```

(`_restricted_levels` uses `_predecessors`-style helpers — check the moved body and import exactly what it references from scheduler.)

- [ ] **Step 12: Create `noodle/engine/scheduler.py`**

Move verbatim: lines 55–67 (`_STATUS_RANK`, `_worse_status` with comment), lines 376–381 (`_predecessors`), lines 392–417 (`_descendants`, `_ancestors`), lines 604–695 (`_topo_order`, `_topo_levels`, `_needed_nodes`), lines 1967–2034 (`_execute_nodes`), lines 2037–2140 (`execute`, `run` — including the module docstring content of engine.py, which becomes this module's docstring or moves to `__init__`). Module header:

```python
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
```

Two **required edits** to the moved bodies (the only non-verbatim changes in this task):

In `_execute_nodes`, the inner `_one` references the loop/metanode drivers — make them lazy:

```python
    run_status = RunStatus.success
    # Lazy: loops.py and metanodes.py import this module at module level,
    # so importing them here (not at the top) breaks the cycle.
    from noodle.engine.loops import _run_conditional_loop, _run_loop
    from noodle.engine.metanodes import _run_metanode
    for level in levels:
        ...
```

In `execute`, the calls to `_expand_metanodes`, `_loop_regions`, `_validate_loop_regions` — make them lazy the same way (one import line each at the top of the function body):

```python
    from noodle.engine.loops import _loop_regions, _validate_loop_regions
    from noodle.engine.metanodes import _expand_metanodes
```

- [ ] **Step 13: Create `noodle/engine/__init__.py`**

```python
"""Workflow execution engine (package facade).

Public API: ``execute``, ``run``, ``GraphError``. The underscore names are
re-exported because tests and hosts imported them from ``noodle.engine``
before the monolith split; keep them working."""

from noodle.engine.agent import (
    _MAX_AGENT_LOOP_ITERATIONS,
    _dispatch_agent_action_request,
)
from noodle.engine.datasets import (
    AUTO_PROMOTE_NODE_TYPES,
    DATASET_AUTO_EXPAND_CAP,
    DATASET_PASSTHROUGH_NODE_TYPES,
    _auto_expand_dataset_inputs,
    _auto_promote_outputs,
)
from noodle.engine.loops import (
    LoopRegion,
    _loop_items,
    _loop_regions,
    _validate_loop_regions,
)
from noodle.engine.metanodes import _expand_metanodes
from noodle.engine.node_exec import (
    DEFAULT_NODE_TIMEOUTS,
    PROCESS_ISOLATED_NODE_TYPES,
    _approx_encoded_length,
    _install_capture,
)
from noodle.engine.pools import (
    _evict_pool,
    _get_process_pool,
    _pool_last_used,
    _process_pools,
    pool_key,
)
from noodle.engine.scheduler import (
    _execute_nodes,
    _needed_nodes,
    _topo_levels,
    _topo_order,
    _worse_status,
    execute,
    run,
)
from noodle.engine.types import EventCallback, GraphError
from noodle.engine.validation import (
    AI_PORT_KINDS,
    _validate_connection_kinds,
    _validate_input_kinds,
    _validate_output_kinds,
)

__all__ = [
    "AI_PORT_KINDS",
    "AUTO_PROMOTE_NODE_TYPES",
    "DATASET_AUTO_EXPAND_CAP",
    "DATASET_PASSTHROUGH_NODE_TYPES",
    "DEFAULT_NODE_TIMEOUTS",
    "EventCallback",
    "GraphError",
    "LoopRegion",
    "PROCESS_ISOLATED_NODE_TYPES",
    "execute",
    "pool_key",
    "run",
    # test-consumed internals (compat with pre-split import paths)
    "_MAX_AGENT_LOOP_ITERATIONS",
    "_approx_encoded_length",
    "_auto_expand_dataset_inputs",
    "_auto_promote_outputs",
    "_dispatch_agent_action_request",
    "_evict_pool",
    "_execute_nodes",
    "_expand_metanodes",
    "_get_process_pool",
    "_install_capture",
    "_loop_items",
    "_loop_regions",
    "_needed_nodes",
    "_pool_last_used",
    "_process_pools",
    "_topo_levels",
    "_topo_order",
    "_validate_connection_kinds",
    "_validate_input_kinds",
    "_validate_loop_regions",
    "_validate_output_kinds",
    "_worse_status",
]
```

Import-order note: `loops`/`metanodes` import `noodle.engine.scheduler` directly (not the package root), so whichever submodule loads first pulls its dependencies in cleanly; `__init__` order above doesn't matter.

- [ ] **Step 14: Delete the monolith**

```powershell
git rm packages/core/noodle/engine.py
```
Every line of engine.py must now live in exactly one submodule. Verify nothing was dropped: the concatenated submodules should contain every function/class from the symbol map table above.

- [ ] **Step 15: Run all suites**

```powershell
cd D:\noodle\packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\packages\nodes; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q
```
Expected: core 306 passed (304 + 2 new), nodes all passed, api 548 passed + the 1 known pre-existing failure. If an ImportError/AttributeError fires, a symbol landed in the wrong module or a re-export is missing — fix the module, not the test.

- [ ] **Step 16: Commit**

```powershell
cd D:\noodle
git add -A
git commit -m "refactor(core): split engine.py monolith into noodle/engine/ package (B2)"
```

---

## Task 2: Dependency-counting scheduler (B1)

Replaces level barriers in `_execute_nodes`: a node starts the moment all its in-set predecessors complete, instead of waiting for its whole level. Loop regions contract into their `loop_start` node as one scheduling unit (validation already guarantees single-entry/single-exit, so the only owned→outside edges come from `loop_end`).

**Files:**
- Modify: `packages/core/noodle/engine/scheduler.py`
- Modify: `packages/core/noodle/engine/loops.py` (loop drivers build a body plan instead of body levels)
- Modify: `packages/core/noodle/engine/__init__.py` (re-export `_build_plan`; drop `_topo_levels`)
- Modify: `packages/core/tests/test_engine_exports.py`
- Test: `packages/core/tests/test_engine.py`, `packages/core/tests/test_scheduler_plan.py` (new)

- [ ] **Step 1: Write the failing overlap test**

Append to `packages/core/tests/test_engine.py`:

```python
async def test_independent_branches_are_not_level_barriered() -> None:
    """src→slow→c and src→fast→d: d must finish before c starts.

    Under level barriers c and d share a level, so d waits for slow (0.4s)
    even though its own parent finished at 0.05s. Dependency counting starts
    d as soon as fast completes."""
    reg = NodeRegistry()

    @node(name="One", id="one", inputs=[], registry=reg)
    def one() -> int:
        return 1

    @node(name="SlowEcho", id="slow_echo", registry=reg)
    async def slow_echo(input: int = 0) -> int:
        await asyncio.sleep(0.4)
        return input

    @node(name="FastEcho", id="fast_echo", registry=reg)
    async def fast_echo(input: int = 0) -> int:
        await asyncio.sleep(0.05)
        return input

    @node(name="Echo", id="echo", registry=reg)
    def echo(input: int = 0) -> int:
        return input

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="src", type="one"),
            GraphNode(id="slow", type="slow_echo"),
            GraphNode(id="fast", type="fast_echo"),
            GraphNode(id="c", type="echo"),
            GraphNode(id="d", type="echo"),
        ],
        edges=[
            Edge(source="src", target="slow"),
            Edge(source="src", target="fast"),
            Edge(source="slow", target="c"),
            Edge(source="fast", target="d"),
        ],
    )
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    result = await execute(graph, reg, on_event=on_event)
    assert result.status == RunStatus.success
    d_finished = next(
        i for i, e in enumerate(events)
        if e["type"] == "node_finished" and e["node_id"] == "d"
    )
    c_started = next(
        i for i, e in enumerate(events)
        if e["type"] == "node_started" and e["node_id"] == "c"
    )
    assert d_finished < c_started, "d should complete before the slow branch unblocks c"
```

- [ ] **Step 2: Run it — must FAIL**

```powershell
cd D:\noodle\packages\core
..\..\.venv\Scripts\python.exe -m pytest tests/test_engine.py::test_independent_branches_are_not_level_barriered -q
```
Expected: FAIL on the final assert (level barrier holds d until slow finishes).

- [ ] **Step 3: Write the plan-construction unit tests**

Create `packages/core/tests/test_scheduler_plan.py`:

```python
"""_build_plan: dependency graph over scheduling units, with loop regions
contracted into their loop_start node."""

from noodle.engine import _loop_regions
from noodle.engine.scheduler import _build_plan
from noodle.models import Edge, GraphNode, WorkflowGraph


def _loop_graph() -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(id="pre", type="const", params={"value": [1, 2]}),
            GraphNode(id="ls", type="loop_start"),
            GraphNode(id="body", type="double"),
            GraphNode(id="le", type="loop_end", params={"loop_start_id": "ls"}),
            GraphNode(id="after", type="double"),
        ],
        edges=[
            Edge(source="pre", target="ls", target_input="input"),
            Edge(source="ls", target="body", source_output="item"),
            Edge(source="body", target="le", target_input="input"),
            Edge(source="le", target="after", source_output="results"),
        ],
    )


def test_plan_contracts_loop_region_into_start_unit():
    graph = _loop_graph()
    regions = _loop_regions(graph)
    owned = set(regions["ls"].body_ids) | {regions["ls"].end_id}
    node_ids = {n.id for n in graph.nodes}

    plan = _build_plan(graph, node_ids, owned, regions)

    assert plan.units == ["pre", "ls", "after"]  # insertion order, owned excluded
    assert plan.deps["after"] == {"ls"}  # le is owned → dep maps to the ls unit
    assert plan.deps["ls"] == {"pre"}
    assert plan.deps["pre"] == set()


def test_plan_for_loop_body_units():
    graph = _loop_graph()
    regions = _loop_regions(graph)
    body_plan = _build_plan(graph, set(regions["ls"].body_ids), set(), regions)

    # ls is outside the body set: the body node has no in-set deps and is
    # immediately ready (its input comes from iter_outputs).
    assert body_plan.units == ["body"]
    assert body_plan.deps["body"] == set()


def test_plan_drops_deps_on_nodes_outside_the_executed_set():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", type="const", params={"value": 1}),
            GraphNode(id="b", type="double"),
        ],
        edges=[Edge(source="a", target="b")],
    )
    # Targeted run where 'a' is cached out of the set: b must not deadlock.
    plan = _build_plan(graph, {"b"}, set(), {})
    assert plan.units == ["b"]
    assert plan.deps["b"] == set()
```

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_scheduler_plan.py -q` — Expected: FAIL with `ImportError: cannot import name '_build_plan'`.

- [ ] **Step 4: Implement `_Plan` / `_build_plan` in scheduler.py**

Add above `_execute_nodes` (and add `from dataclasses import dataclass, field` to the imports):

```python
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


def _owner_unit(
    nid: str,
    loop_regions: dict[str, "LoopRegion"],
    unit_set: set[str],
) -> str | None:
    """The scheduling unit that produces ``nid``'s output when ``nid`` is
    loop-owned: walk to the owning region's start (hopping outward through
    nested regions) until a unit is found."""
    cur = nid
    while True:
        region = next(
            (
                r for r in loop_regions.values()
                if cur == r.end_id or cur in r.body_ids
            ),
            None,
        )
        if region is None:
            return None
        if region.start_id in unit_set:
            return region.start_id
        cur = region.start_id


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
    units = sorted(unit_set, key=index.__getitem__)
    deps: dict[str, set[str]] = {u: set() for u in units}
    for edge in graph.edges:
        if edge.target not in unit_set or edge.source == edge.target:
            continue
        source = edge.source
        if source not in unit_set:
            if source not in owned:
                continue
            source = _owner_unit(source, loop_regions, unit_set)
            if source is None or source == edge.target:
                continue
        deps[edge.target].add(source)
    dependents: dict[str, list[str]] = {u: [] for u in units}
    for target, sources in deps.items():
        for source in sources:
            dependents[source].append(target)
    return _Plan(units=units, deps=deps, dependents=dependents, index=index)
```

- [ ] **Step 5: Rewrite `_execute_nodes` as the dependency-counting loop**

Replace the whole function. The `_one` dispatch body (metanode / loop_start interception / `_run_one_node`) is **identical to today's** — only the driving loop changes:

```python
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
) -> RunStatus:
    """Run the plan's units with dependency counting: each unit starts the
    moment its in-set predecessors complete. Simultaneously-ready units are
    started in graph insertion order. ``node_sem`` (when set) bounds how many
    plain nodes execute concurrently; loop/metanode *drivers* never hold a
    slot (their body nodes acquire their own), so a capped run cannot
    deadlock on nested regions. Returns the worst RunStatus seen."""
    # Lazy: loops.py and metanodes.py import this module at module level.
    from noodle.engine.loops import _run_conditional_loop, _run_loop
    from noodle.engine.metanodes import _run_metanode

    run_status = RunStatus.success

    async def _one(nid: str) -> tuple[str, RunStatus]:
        gn = nodes_by_id[nid]
        if gn.type == "meta_node":
            st = await _run_metanode(
                node=gn, incoming=incoming, node_outputs=node_outputs,
                registry=registry, emit=emit, finish=finish,
                default_timeouts=default_timeouts,
                max_node_output_bytes=max_node_output_bytes,
            )
            return nid, st
        if gn.type == "loop_start" and nid in loop_regions:
            mode = str(gn.params.get("mode", "each") or "each")
            driver = (
                _run_conditional_loop if mode in ("while", "until") else _run_loop
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
                node_sem=node_sem,
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
        return nid, st

    indegree = {u: len(plan.deps[u]) for u in plan.units}
    ready = [u for u in plan.units if indegree[u] == 0]  # insertion order
    pending: set[asyncio.Task] = set()
    completed = 0
    try:
        while ready or pending:
            for nid in ready:  # task creation order == start order
                pending.add(asyncio.ensure_future(_one(nid)))
            ready = []
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            newly_ready: list[str] = []
            for task in done:
                nid, st = task.result()  # re-raises node-task exceptions
                run_status = _worse_status(run_status, st)
                completed += 1
                for dep in plan.dependents[nid]:
                    indegree[dep] -= 1
                    if indegree[dep] == 0:
                        newly_ready.append(dep)
            ready = sorted(newly_ready, key=plan.index.__getitem__)
    except BaseException:
        # Run cancellation (or an escaped node exception) must not leave
        # in-flight node tasks running detached (REL-2 class).
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        raise
    if completed != len(plan.units):
        raise GraphError(
            "scheduler stalled: a unit's dependencies never completed"
        )  # defensive — _topo_order() in execute() should make this unreachable
    return run_status
```

Add `Callable`/`Awaitable` to the `collections.abc` import in scheduler.py if not already present.

- [ ] **Step 6: Update `execute()` in scheduler.py**

Replace the `levels = _topo_levels(graph)` line and the `_execute_nodes` call:

```python
    target_set = set(targets) if targets is not None else None
    needed = _needed_nodes(graph, target_set, cache)
    _validate_connection_kinds(graph, registry, needed)
    _topo_order(graph)  # cycle detection — raises GraphError
    nodes_by_id = {n.id: n for n in graph.nodes}
```

and after the existing `owned` computation:

```python
    plan = _build_plan(graph, needed, owned, loop_regions)

    # Dependency-counting execution: each node starts as soon as its in-set
    # predecessors complete. Loop Start nodes are intercepted by
    # _execute_nodes and driven over their body sub-DAG.
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
    )
```

Add the concurrency knob to `execute`'s signature and body:

```python
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
) -> RunResult:
```
```python
    node_sem = (
        asyncio.Semaphore(max_node_concurrency)
        if max_node_concurrency is not None and max_node_concurrency > 0
        else None
    )
```

Delete `_topo_levels` from scheduler.py (its only caller is gone).

- [ ] **Step 7: Update the loop drivers in loops.py**

In **both** `_run_loop` and `_run_conditional_loop`:
1. Add `node_sem: asyncio.Semaphore | None = None,` to the keyword-only parameters.
2. Replace `body_levels = _restricted_levels(graph, region.body_ids)` with:
   ```python
   body_plan = _build_plan(graph, set(region.body_ids), child_owned, loop_regions)
   ```
   (note: `child_owned` is computed just above in both drivers — the plan must exclude nested-loop internals exactly as the old `owned=child_owned` argument did).
3. In every `_execute_nodes(...)` call inside the drivers (three sites: reduce path + fan-out path in `_run_loop`, one in `_run_conditional_loop`), replace `node_ids=set(region.body_ids), levels=body_levels,` with `plan=body_plan,` and add `node_sem=node_sem,`. The `owned=child_owned` argument stays (nested drivers need it).

Then delete `_restricted_levels` from loops.py (no callers remain) and update the loops.py import from scheduler to:

```python
from noodle.engine.scheduler import (
    _ancestors,
    _build_plan,
    _descendants,
    _execute_nodes,
    _predecessors,
)
```
(drop names no longer used; keep whatever `_loop_regions`/`_validate_loop_regions` actually reference).

- [ ] **Step 8: Update the facade and export test**

In `engine/__init__.py`: remove `_topo_levels` from the scheduler import block and `__all__`; add `_build_plan`. In `tests/test_engine_exports.py`: replace `"_topo_levels"` with `"_build_plan"` in the name list.

- [ ] **Step 9: Run the new tests, then the full suites**

```powershell
cd D:\noodle\packages\core
..\..\.venv\Scripts\python.exe -m pytest tests/test_scheduler_plan.py tests/test_engine.py -q
..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\packages\nodes; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q
```
Expected: all green (plus the 1 known API pre-existing failure). **Decision rule for failures:** if a test fails on *event ordering* (it asserted nodes finish in level order), inspect whether the assertion encodes level-barrier timing rather than data-flow correctness; if so, relax the assertion to data-flow order and note it in the commit message. Any failure about *wrong outputs, missing skips, or loop results* is a scheduler bug — fix the scheduler.

- [ ] **Step 10: Commit**

```powershell
cd D:\noodle
git add -A
git commit -m "feat(engine): dependency-counting scheduler replaces level barriers (B1)"
```

---

## Task 3: ProcessIsolator — pool ownership moves to the host (B4)

The engine stops owning `ProcessPoolExecutor`s. A `ProcessIsolator` is injected via `execute(..., process_isolator=...)`; `noodle/process_isolation.py` provides the pooled implementation whose eviction is keyed off **task completion** and in-flight counts, so a pool running a 30-minute code node is never reaped mid-task (the old `_get_process_pool` swept by last-*get* time). When nothing is injected (unit tests, exported scripts), a lazily created module default keeps today's behavior.

**Files:**
- Create: `packages/core/noodle/process_isolation.py`
- Create: `packages/core/tests/test_process_isolation.py`
- Delete: `packages/core/noodle/engine/pools.py`
- Modify: `packages/core/noodle/engine/node_exec.py`, `scheduler.py`, `loops.py`, `metanodes.py`, `__init__.py`
- Modify: `apps/api/app/services/runner.py`, `apps/api/app/main.py`
- Modify: `packages/runtime/noodle_runtime/server.py`
- Modify: `packages/core/tests/test_engine.py`, `packages/core/tests/test_production_fixes.py`, `packages/core/tests/test_engine_exports.py`

- [ ] **Step 1: Write the failing process_isolation tests**

Create `packages/core/tests/test_process_isolation.py`:

```python
"""B4: host-owned process isolation. Pools are keyed per environment,
reused per key, and evicted only when idle with no in-flight tasks —
a long-running code node must never have its pool reaped mid-task."""

import time

from noodle.process_isolation import PooledProcessIsolator


def test_same_key_reuses_pool():
    iso = PooledProcessIsolator()
    try:
        a1 = iso._checkout("env-alpha"); iso._checkin("env-alpha")
        a2 = iso._checkout("env-alpha"); iso._checkin("env-alpha")
        assert a1 is a2
    finally:
        iso.shutdown()


def test_different_keys_get_different_pools():
    iso = PooledProcessIsolator()
    try:
        a = iso._checkout("env-x"); iso._checkin("env-x")
        b = iso._checkout("env-y"); iso._checkin("env-y")
        assert a is not b
    finally:
        iso.shutdown()


def test_none_key_is_its_own_pool():
    iso = PooledProcessIsolator()
    try:
        none_pool = iso._checkout(None); iso._checkin(None)
        named = iso._checkout("env-z"); iso._checkin("env-z")
        assert none_pool is not named
    finally:
        iso.shutdown()


def test_idle_pool_is_evicted_on_next_checkout():
    iso = PooledProcessIsolator(idle_seconds=0.01)
    try:
        iso._checkout("idle-env"); iso._checkin("idle-env")
        iso._last_activity["idle-env"] = time.monotonic() - 700
        iso._checkout("active-env"); iso._checkin("active-env")
        assert "idle-env" not in iso._pools
    finally:
        iso.shutdown()


def test_in_flight_pool_survives_idle_sweep():
    """The cold-pool fix: a pool with a running task is never reaped, no
    matter how stale its last-activity timestamp looks."""
    iso = PooledProcessIsolator(idle_seconds=0.01)
    try:
        busy = iso._checkout("busy-env")  # in flight — no checkin yet
        iso._last_activity["busy-env"] = time.monotonic() - 700
        iso._checkout("other-env"); iso._checkin("other-env")
        assert iso._pools.get("busy-env") is busy  # survived the sweep

        iso._checkin("busy-env")  # task completes
        iso._last_activity["busy-env"] = time.monotonic() - 700
        iso._checkout("other-env2"); iso._checkin("other-env2")
        assert "busy-env" not in iso._pools  # idle now → reaped
    finally:
        iso.shutdown()
```

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_process_isolation.py -q` — Expected: FAIL with `ModuleNotFoundError: No module named 'noodle.process_isolation'`.

- [ ] **Step 2: Implement `noodle/process_isolation.py`**

```python
"""Host-owned process isolation for engine nodes (B4).

The engine no longer owns ProcessPoolExecutors. Hosts construct a
ProcessIsolator and inject it via ``execute(..., process_isolator=...)``;
when none is injected (unit tests, exported scripts) a lazily created module
default is used.

Eviction is keyed off task *completion* and in-flight counts: a pool running
a long code node is never reaped mid-task. The old engine-owned pools were
swept by last-``get`` time, so another environment requesting a pool could
kill a 30-minute code node halfway through.
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

# Hosts (runner.py) set this to the workflow's environment id so each
# environment gets its own ProcessPoolExecutor and worker state cannot
# bleed across environments. None is the shared default bucket used by
# tests and the in-process dev path.
pool_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "noodle_pool_key", default=None
)


class ProcessIsolator(Protocol):
    """Runs a synchronous node function outside the calling process."""

    async def run(
        self,
        fn: Callable[..., Any],
        kwargs: dict[str, Any],
        *,
        timeout: float | None,
    ) -> Any: ...


class PooledProcessIsolator:
    """One ProcessPoolExecutor per isolation key (environment id).

    A pool is evicted only when it has no in-flight tasks AND has seen no
    submit/completion activity for ``idle_seconds``. Timeouts and broken
    pools evict immediately so the next attempt gets a fresh pool."""

    def __init__(self, *, max_workers: int = 4, idle_seconds: float = 600.0) -> None:
        self._max_workers = max_workers
        self._idle_seconds = idle_seconds
        self._pools: dict[str | None, concurrent.futures.ProcessPoolExecutor] = {}
        self._last_activity: dict[str | None, float] = {}
        self._in_flight: dict[str | None, int] = {}
        self._mutex = threading.Lock()

    async def run(
        self,
        fn: Callable[..., Any],
        kwargs: dict[str, Any],
        *,
        timeout: float | None,
    ) -> Any:
        key = pool_key.get()
        pool = self._checkout(key)
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(pool, functools.partial(fn, **kwargs))
        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout)
            return await future
        except TimeoutError:
            self.evict(key)  # kill the wedged worker; next run gets a fresh pool
            raise
        except concurrent.futures.process.BrokenProcessPool as exc:
            self.evict(key)
            raise ValueError(
                "code node crashed: subprocess died (possible "
                "out-of-memory, segfault, or unpicklable value)"
            ) from exc
        finally:
            self._checkin(key)

    def _checkout(self, key: str | None) -> concurrent.futures.ProcessPoolExecutor:
        with self._mutex:
            now = time.monotonic()
            idle = [
                k for k, last in self._last_activity.items()
                if k != key
                and self._in_flight.get(k, 0) == 0
                and now - last > self._idle_seconds
            ]
            for k in idle:
                self._evict_locked(k)
            pool = self._pools.get(key)
            if pool is None:
                pool = concurrent.futures.ProcessPoolExecutor(
                    max_workers=self._max_workers
                )
                self._pools[key] = pool
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
            self._last_activity[key] = now
            return pool

    def _checkin(self, key: str | None) -> None:
        with self._mutex:
            self._in_flight[key] = max(0, self._in_flight.get(key, 0) - 1)
            self._last_activity[key] = time.monotonic()

    def evict(self, key: str | None) -> None:
        with self._mutex:
            self._evict_locked(key)

    def _evict_locked(self, key: str | None) -> None:
        pool = self._pools.pop(key, None)
        self._last_activity.pop(key, None)
        self._in_flight.pop(key, None)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def shutdown(self) -> None:
        with self._mutex:
            for key in list(self._pools):
                self._evict_locked(key)


_default: PooledProcessIsolator | None = None


def default_isolator() -> PooledProcessIsolator:
    """Lazily created fallback for callers that don't inject an isolator
    (unit tests, exported scripts)."""
    global _default
    if _default is None:
        _default = PooledProcessIsolator()
    return _default
```

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_process_isolation.py -q` — Expected: 5 passed.

- [ ] **Step 3: Write the failing engine-injection test**

Append to `packages/core/tests/test_engine.py`:

```python
async def test_execute_routes_isolated_nodes_through_injected_isolator() -> None:
    """Sync nodes of PROCESS_ISOLATED_NODE_TYPES must run via the injected
    ProcessIsolator, not an engine-owned pool."""
    calls: list[tuple] = []

    class StubIsolator:
        async def run(self, fn, kwargs, *, timeout=None):
            calls.append((fn.__name__, dict(kwargs), timeout))
            return fn(**kwargs)

    reg = NodeRegistry()

    @node(name="CodeLike", id="code", inputs=[], registry=reg)
    def add_one(value: int = 0) -> int:
        return value + 1

    graph = WorkflowGraph(
        nodes=[GraphNode(id="c", type="code", params={"value": 41})]
    )
    result = await execute(graph, reg, process_isolator=StubIsolator())
    assert result.status == RunStatus.success
    assert result.nodes["c"].outputs["main"] == 42
    assert len(calls) == 1 and calls[0][0] == "add_one"
```

Run it — Expected: FAIL with `TypeError: execute() got an unexpected keyword argument 'process_isolator'`.

- [ ] **Step 4: Thread `process_isolator` through the engine**

1. **node_exec.py** — replace the pools import with `from noodle.process_isolation import ProcessIsolator, default_isolator`; drop `concurrent.futures`/`functools` from imports if now unused. Add `process_isolator: "ProcessIsolator | None"` to `_run_one_node`'s keyword params. Replace the process-isolated branch of `invoke_node` (the block from `if graph_node.type in PROCESS_ISOLATED_NODE_TYPES:` through the `BrokenProcessPool` handler) with:

```python
        if graph_node.type in PROCESS_ISOLATED_NODE_TYPES:
            isolator = (
                process_isolator if process_isolator is not None
                else default_isolator()
            )
            return await isolator.run(node_def.func, current_kwargs, timeout=timeout)
```
(Timeout-evict and broken-pool translation now live inside the isolator — same errors surface to the retry/timeout handling below, unchanged.)

2. **scheduler.py** — `execute()` gains `process_isolator: "ProcessIsolator | None" = None` (import the Protocol under `TYPE_CHECKING`); pass it into `_execute_nodes`; `_execute_nodes` gains the param and forwards it to `_run_one_node` and both loop drivers.
3. **loops.py** — both drivers gain `process_isolator` param and forward it in their `_execute_nodes(...)` calls.
4. **metanodes.py** — `_run_metanode` gains `process_isolator` param, forwards it to its inner `execute(...)` call; scheduler's `_one` passes it.
5. Delete `packages/core/noodle/engine/pools.py` (`git rm`). In `engine/__init__.py`: remove the pools import block and the five pool names from `__all__`.

- [ ] **Step 5: Update the relocated tests**

In `packages/core/tests/test_engine.py`: delete the three `_get_process_pool` tests (`test_process_pool_returns_same_pool_for_same_key`, `..._different_pools_for_different_keys`, `..._none_key_is_its_own_pool`) — their intent now lives in `test_process_isolation.py`.

In `packages/core/tests/test_production_fixes.py`: delete `test_process_pools_idle_eviction` (superseded by `test_idle_pool_is_evicted_on_next_checkout` + `test_in_flight_pool_survives_idle_sweep`) and add a pointer comment in its place:

```python
# test_process_pools_idle_eviction moved: pool ownership left the engine in B4.
# See tests/test_process_isolation.py for idle-eviction + in-flight coverage.
```

In `packages/core/tests/test_engine_exports.py`: remove `"pool_key"`, `"_get_process_pool"`, `"_evict_pool"`, `"_process_pools"`, `"_pool_last_used"` from the name list.

- [ ] **Step 6: Update the hosts**

1. **apps/api/app/services/runner.py** line 92 becomes:
```python
from noodle.engine import DEFAULT_NODE_TIMEOUTS, execute
from noodle.process_isolation import PooledProcessIsolator, pool_key as engine_pool_key
```
Below the module's other module-level state, add:
```python
# B4: the API host owns the code-node process pools. One isolator for the
# whole process; pools inside it are still keyed per environment via
# engine_pool_key, exactly as before.
process_isolator = PooledProcessIsolator()
```
Add `process_isolator=process_isolator,` to **both** `execute(` call sites (the sub-workflow path near line 546 and the main run path near line 1363). The existing `engine_pool_key.set(env_id)` lines stay — the contextvar moved modules but the mechanism is identical.

2. **apps/api/app/main.py** — in the lifespan teardown, after the `expr_preview.shutdown()` line, add (matching the surrounding style; `shutdown()` is sync and fast — `wait=False`):
```python
    runner.process_isolator.shutdown()
```
(`runner` is already imported in main.py; if only specific names are imported, add `from app.services import runner`.)

3. **packages/runtime/noodle_runtime/server.py** — add near the other module-level state:
```python
from noodle.process_isolation import PooledProcessIsolator

# This warm runner process is already per-environment; one isolator with the
# default (None) pool key is correct.
_PROCESS_ISOLATOR = PooledProcessIsolator()
```
and add `process_isolator=_PROCESS_ISOLATOR,` to both `execute(` calls (lines ~203 and ~276).

- [ ] **Step 7: Run all suites**

```powershell
cd D:\noodle\packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\packages\nodes; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q
```
Expected: all green (+1 known API pre-existing failure). The nodes suite exercises real `code` nodes through the default isolator — it proves the fallback path end to end.

- [ ] **Step 8: Commit**

```powershell
cd D:\noodle
git add -A
git commit -m "feat(engine): host-owned ProcessIsolator replaces engine process pools (B4)"
```

---

## Task 4: Output-measure fast path (B5)

`_run_one_node` measures every node output with a streaming JSON encode (`_approx_encoded_length`) before comparing to `max_node_output_bytes`. For a 10k-iteration loop of small outputs that's 10k full encodes. Add a conservative O(small) upper-bound check that skips the real encode when the output obviously fits.

**Files:**
- Modify: `packages/core/noodle/engine/node_exec.py`
- Test: `packages/core/tests/test_engine.py`, `packages/core/tests/test_output_cap_fastpath.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_output_cap_fastpath.py`:

```python
"""B5: the node-output size cap must not pay a full JSON encode for outputs
that are obviously small. The bound is conservative: it may overestimate
(falling back to a real measure) but must never underestimate."""

import json

import pytest

from noodle.engine import execute
from noodle.engine import node_exec
from noodle.engine.node_exec import _encoded_upper_bound
from noodle.models import GraphNode, NodeStatus, RunStatus, WorkflowGraph
from noodle.sdk import NodeRegistry, node


@pytest.mark.parametrize(
    "value",
    [
        None, True, False, 0, 42, -7, 3.14, 1e300,
        "hello", "",
        [1, 2, 3], {"a": 1, "b": "x"},
        {"main": [1.5, None, "ok"]},
    ],
)
def test_bound_never_underestimates(value):
    bound = _encoded_upper_bound(value)
    assert bound is not None
    assert bound >= len(json.dumps(value, default=str))


@pytest.mark.parametrize(
    "value",
    [
        10**40,                          # huge int — no cheap bound
        list(range(100)),                # over the 64-element scan cap
        {"a": {"b": {"c": 1}}},          # deeper than the 2-level scan
        {1: "non-string-key"},
        object(),
    ],
)
def test_unbounded_shapes_fall_back_to_full_measure(value):
    assert _encoded_upper_bound(value) is None


async def test_small_outputs_skip_the_full_encode(monkeypatch):
    calls = {"n": 0}
    real = node_exec._approx_encoded_length

    def counting(value):
        calls["n"] += 1
        return real(value)

    monkeypatch.setattr(node_exec, "_approx_encoded_length", counting)

    reg = NodeRegistry()

    @node(name="Small", id="small", inputs=[], registry=reg)
    def small() -> dict:
        return {"value": 7, "label": "ok"}

    graph = WorkflowGraph(nodes=[GraphNode(id="s", type="small")])
    result = await execute(graph, reg, max_node_output_bytes=10_000)
    assert result.status == RunStatus.success
    assert calls["n"] == 0, "small output paid a full JSON encode"


async def test_oversized_output_still_errors():
    reg = NodeRegistry()

    @node(name="Big", id="big", inputs=[], registry=reg)
    def big() -> str:
        return "x" * 5000

    graph = WorkflowGraph(nodes=[GraphNode(id="b", type="big")])
    result = await execute(graph, reg, max_node_output_bytes=100)
    assert result.nodes["b"].status == NodeStatus.error
    assert "exceeds limit" in result.nodes["b"].error
```

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_output_cap_fastpath.py -q` — Expected: FAIL with `ImportError: cannot import name '_encoded_upper_bound'`.

- [ ] **Step 2: Implement the bound in node_exec.py**

Add directly below `_approx_encoded_length`:

```python
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
```

In `_run_one_node`, replace the cap-check block:

```python
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
```

- [ ] **Step 3: Run the new tests, then full core + nodes + api suites**

```powershell
cd D:\noodle\packages\core
..\..\.venv\Scripts\python.exe -m pytest tests/test_output_cap_fastpath.py -q
..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\packages\nodes; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q
```
Expected: all green (+1 known API pre-existing failure).

- [ ] **Step 4: Commit**

```powershell
cd D:\noodle
git add -A
git commit -m "perf(engine): conservative upper-bound fast path for the output-size cap (B5)"
```

---

## Task 5: Docs + final verification

**Files:**
- Modify: `docs/architecture.md` (engine section references `packages/core/noodle/engine.py` and level-based execution)

- [ ] **Step 1: Update docs/architecture.md**

Find the engine section (search for `engine.py` and "level"). Update: (1) the file path becomes the `noodle/engine/` package with a one-line map of the nine modules; (2) the scheduling description becomes dependency counting ("a node starts when its in-set predecessors complete; loop regions are contracted into their loop_start unit"); (3) note `ProcessIsolator` injection and that hosts own code-node pools (`noodle/process_isolation.py`). Keep edits surgical — only sentences that are now wrong.

- [ ] **Step 2: Full verification — all four suites**

```powershell
cd D:\noodle\packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\packages\nodes; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\apps\web; npm run test:e2e
cd D:\noodle\apps\web; npx vitest run
```
Expected: core/nodes green; api 548+ passed with only the known pre-existing failure; e2e 3 passed; vitest 147 passed. The e2e run-observe test exercises the new scheduler + isolator through the real API.

- [ ] **Step 3: Commit**

```powershell
cd D:\noodle
git add -A
git commit -m "docs: architecture.md reflects engine package split, dependency scheduler, ProcessIsolator"
```

---

## Acceptance summary (from the master plan)

- [B2] `from noodle.engine import execute` works everywhere unchanged; engine.py deleted; nine focused modules; zero behavior change (full suites green with only the export-test addition).
- [B1] `test_branch_order_depends_on_insertion_not_position` stays green; loop `owned` nodes never scheduled by the outer scheduler; `loop_start`/`meta_node` interception identical; skipped-branch propagation unchanged; new overlap test proves a fast branch completes before a slow sibling unblocks (impossible under level barriers).
- [B4] Engine owns no pools; hosts inject `ProcessIsolator`; eviction keyed off completion + in-flight (new test proves a busy pool survives the sweep).
- [B5] Small outputs pay zero full JSON encodes (call-counting test); oversized outputs still rejected; bound proven conservative against `json.dumps` across shapes.
