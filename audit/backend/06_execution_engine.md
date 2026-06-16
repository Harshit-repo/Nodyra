# 06 — Workflow Execution Engine

Independent review, 2026-06-16. Files: `packages/core/noodle/engine/scheduler.py`,
`engine/validation.py`, `engine/loops.py`, `engine/node_exec.py` (timeout paths),
`apps/api/app/services/runner.py` (timeout/cancellation wiring),
`packages/nodes/noodle_nodes/builtin.py` (map node).

## What is solid (verified)
- **Cycle detection**: Kahn's algorithm in `_topo_order` (`scheduler.py:90-126`);
  `len(order) != len(nodes)` → `GraphError("Workflow graph has a cycle")`. Run by
  `_execute_impl` before any node executes (`scheduler.py:440`).
- **Deterministic ordering**: ties broken by `graph.nodes` insertion index, never
  by node id, and **explicitly never by canvas position** (documented contract).
  Makes runs reproducible and exports stable.
- **Dependency-counting concurrency** (`_execute_nodes`): each unit starts the
  instant its in-set predecessors finish (not coarse level-barriers); ready units
  start in insertion order.
- **Cancellation safety (REL-2)**: on any `BaseException` (run cancel / escaped
  node error) all in-flight node tasks are cancelled and awaited
  (`scheduler.py:332-339`) — no detached tasks survive a cancelled run.
- **Status aggregation**: worst-of (`error > waiting > success`) so a late
  approval-`waiting` node can't mask an `error` (`scheduler.py:42-51`).
- **Timeouts wired correctly**: run-level wall-clock via
  `asyncio.wait_for(coro, _eff_timeout)` where `_eff_timeout` = per-workflow
  `run_timeout_seconds` else `settings.workflow_run_timeout_seconds`
  (`runner.py:1067-1086`); per-node via `asyncio.wait_for` in `node_exec.py:378/391`
  using `DEFAULT_NODE_TIMEOUTS` + per-node `timeout_seconds`.
- **Dynamic fan-out is bounded**: loops cap rows (`MAX_LOOP_ROWS`), parallelism
  (`MAX_LOOP_CONCURRENCY`, default 1), and `while/until` iterations (≤1000); the
  map node caps `MAX_MAP_CONCURRENCY` (default 5). Data-driven explosions can't
  spawn unbounded work.
- **Subworkflow safety**: depth + cycle invariants seeded from `SubworkflowMeta`
  via `call_chain`/`workflow_caller` ContextVars; `max_subworkflow_depth=16`.

## Findings

### ENGINE-1 — Statically-wide DAGs run with unbounded node concurrency (LOW–MEDIUM)
`execute(..., max_node_concurrency=None)` is the only call shape in production:
grep shows **no caller in `apps/api` ever passes `max_node_concurrency`**, so
`node_sem` is always `None` and every ready node in a flat graph starts at once
(`scheduler.py:442-446`, `311-318`). `max_concurrent_runs` caps *top-level runs*,
not nodes within one run.
- **Impact:** a workflow with many sibling nodes fed from one source (e.g. 200
  HTTP/DB nodes off one trigger) opens that many concurrent sockets/subprocesses
  in a single run — file-descriptor/connection/memory pressure on the host.
  Bounded in practice by how many nodes a human places on the canvas (loops/map,
  the data-driven paths, are already bounded — see above), which is why this is
  Low–Medium not High.
- **Fix:** add `settings.max_node_concurrency` (default e.g. 16; 0 = unlimited)
  and thread it through the executor `RunExecutionContext` into
  `execute(max_node_concurrency=…)`. The machinery already exists and is
  deadlock-safe (loop/metanode drivers deliberately don't hold a slot).
- **Test:** a 50-node fan-out graph with `max_node_concurrency=4` never has >4
  nodes in `running` simultaneously (instrument via `on_event`).
- **Status:** Reviewed — recommend wiring.

### ENGINE-2 — No default run-level wall-clock cap (INFO / operational)
`workflow_run_timeout_seconds=0` (unlimited) and `code_node_timeout_seconds=0`
are the defaults (`config.py:174-180`) — deliberate for long data jobs, but it
means a runaway/​hung node (e.g. a blocking network call with no library timeout)
keeps a warm worker occupied indefinitely unless a per-workflow override exists.
- **Recommendation:** ship a non-zero default (or strongly document setting one)
  for multi-tenant/SaaS so one tenant can't pin a worker forever; pair with
  ENGINE-1's node cap.
- **Status:** Doc / needs decision.

### ENGINE-3 — Failure semantics are "skip downstream", not "fail branch" (INFO — confirm intent)
A node that errors is recorded as `error` and produces no output; its dependents
become ready (indegree hits 0) and then **skip** via the node-exec "upstream
produced no output" check (per `_build_plan` docstring). The run's overall status
is the worst seen. This is n8n-like continue-on-fail-with-skip, which is
reasonable — but it means a failed node does not *halt* unrelated branches, and a
downstream node with another satisfied input could still run.
- **Recommendation:** confirm this is the intended product contract and document
  it (users coming from "stop on first error" engines will be surprised); consider
  a per-workflow "stop on error" toggle.
- **Status:** Needs decision (document).

## Verdict
The execution core is **correct and carefully engineered** — cycle detection,
determinism, cancellation hygiene, and bounded dynamic fan-out are all present and
tested-looking. The only concrete gap is ENGINE-1 (defence-in-depth node cap for
hand-authored wide graphs); ENGINE-2/-3 are product/ops decisions to document.
