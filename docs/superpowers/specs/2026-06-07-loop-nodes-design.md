# Loop Start / Loop End Nodes — Design

**Date:** 2026-06-07
**Status:** Approved design, pre-implementation
**Author:** Harry + Claude

## Problem

Users need to process a list/dataset **row by row through real downstream nodes** — e.g. a dataset
with a column of PDF artifacts where each artifact must be extracted, summarized, and saved. Today
the only per-item mechanism is the **Map** family (`map_items`, `map_group`, `map_dataset`), which
calls a *separate sub-workflow* once per item. That has three drawbacks for this use case:

1. The per-item body is a separate workflow, not the nodes you wired on the canvas.
2. Debugging a failing row happens in a separate child run, not in place.
3. In subprocess mode, Map spawns a subprocess **per row** and round-trips through the host
   (`workflow_caller` → `call_workflow`), which is heavy and was the source of a recent deadlock
   (`_needs_host_callbacks` omission).

We want true **in-graph looping**: wrap a region of the canvas in `Loop Start` / `Loop End` and have
the engine execute that region once per item, with per-iteration observability.

## The core constraint

Noodle's engine is a DAG where **each node executes exactly once per run** (`_run_node` →
`node_outputs[nid]`; one `NodeRunResult` per node id; one `node_started`/`node_finished` pair per
node). `loop_over_items` even documents this: *"the current DAG engine executes a node once per run
rather than once per item."* A loop region deliberately breaks that invariant for the nodes inside
it. The design contains that break to the loop body + the persistence/event layers, leaving the rest
of the engine and all existing tooling on the one-result-per-node model.

## Scope

### In v1
- `loop_start` / `loop_end` nodes.
- Body = an arbitrary **single-entry/single-exit (SESE)** sub-DAG (branches, multiple nodes, merges).
- **Configurable concurrency, sequential by default** (`concurrency=1`).
- **Iteration-aware** engine, event protocol, and run persistence.
- **Per-iteration inspector** in the editor (basic; reads per-iteration `NodeRun` rows).
- Input is a **list or a DatasetRef** (datasets materialized with the existing `max_rows` guard).
- **Per-iteration error handling** (`fail` | `continue`) with an `errors` output, mirroring Map.
- **Nesting** — loops inside loops, well-nested, with an iteration *path*.
- **Deprecate `loop_over_items`** (the fake-loop shim) — see "Relationship to Map & deprecations".

### Deferred to v2
- **Cross-iteration accumulator (reduce).** Covered for most needs by *collect at Loop End → one
  aggregation node after the loop*. Only meaningful at `concurrency=1`.
- **Early exit / break.** Thorny with concurrency (cancelling in-flight iterations) and nesting
  (which level breaks). Revisit once v1 lands.

## Approach (chosen: native sub-graph iteration)

Considered three approaches:

- **A. Native sub-graph iteration (chosen).** The engine treats the body as a SESE region, removes
  body-interior nodes + Loop End from the top-level pass, and makes Loop Start a *driver* that runs
  the body sub-DAG once per item via the same node-execution code path, then writes Loop End's
  aggregated outputs. True in-graph looping; one engine run; no subprocess-per-row; full
  per-iteration events.
- **B. Desugar to sub-workflow (rejected).** Extract body into a child workflow and replace
  Start/End with a Map-style call. This *is* Map — defeats the goal (separate child run, no in-place
  debugging, subprocess-per-row).
- **C. Unroll body into virtual nodes (rejected).** Duplicate body nodes N times into a flat DAG.
  N is unknown until Loop Start's input resolves at runtime, fighting the "levels computed once"
  model; run-result map balloons; nesting multiplies the blow-up.

## Relationship to Map & deprecations

Loop and Map are **complementary**, not redundant — Loop does not subsume Map:

- **Keep `map_items` and `map_dataset`.** They call a *named, reusable* sub-workflow per item, which
  Loop cannot express: a Map child can be shared across parents, versioned, tested and triggered on
  its own, runs in its **own subprocess** (per-item isolation), can target a **different
  environment** than the parent, and can fan out across **remote runners**. Loop's body is inline,
  local to one workflow, and bound to the parent's environment.
- **`map_group` — revisit post-Loop.** It overlaps with Loop the most (an inline-ish body via a
  hidden child workflow). Candidate for deprecation *after* Loop ships and proves out — **not** in
  this release.
- **Deprecate `loop_over_items` now.** It is a *fake* loop — its own docstring states it doesn't
  iterate; it just passes the list through plus a `done` summary. Next to a real Loop it is pure
  confusion. Mark it `@node(deprecated=True, replacement_id="loop_start")` (the same mechanism used
  across `integrations.py`). It keeps **functioning** for existing workflows but is hidden from the
  palette for new use and nudges users to Loop. No removal in this release (breaking change; would
  also affect existing graphs).

No part of the Map family is **removed** in this release. Removing a working, tested feature in favor
of an unbuilt one is premature; sunset decisions come after Loop has baked and we can see what users
reach for.

## Design

### 1. Nodes and region definition

**`loop_start`** (category Logic, icon `repeat`):
- Input `input`: a list **or** a DatasetRef (datasets materialized via `materialize_dataset` with a
  `max_rows` guard, default 10000 — same contract as Map Dataset).
- Params: `concurrency` (default 1 = sequential), `on_error` (`fail` | `continue`), `max_rows`.
- Output `item`: the current row during each iteration (also emits `index`). Body nodes wire from
  this port.

**`loop_end`** (category Logic, icon `repeat`):
- Input `input`: the per-iteration value to collect.
- Hidden param `loop_start_id`: node id of the paired Loop Start, **auto-managed by the editor**
  (same pattern `map_group` uses for `child_workflow_id`).
- Param `output_mode` (`records` | `dataset`, default `records`): controls the shape of `results`.
  - `records` — `results` is the collected Python list (captured values **in item order**).
  - `dataset` — the collected values are written to a new dataset and `results` is a `DatasetRef`,
    so downstream dataset-aware nodes (and the dataset viewer) can consume the loop output directly.
    Non-dict values are wrapped as `{"result": value}` before writing.
- Outputs `results` (list, or a `DatasetRef` when `output_mode=dataset`) and `errors`
  (failed rows when `on_error=continue`; same shape as Map's errors output).

The dataset write goes through a **registered writer hook** (`register_dataset_writer` /
`dataset_from_records` in `packages/core/noodle/datasets.py`, mirroring the existing
`register_materializer` / `materialize_dataset_rows` pair) so core stays decoupled from the nodes
package; `packages/nodes/noodle_nodes/datasets.py` registers the concrete writer at import time.

**Body region** = nodes on a path from Loop Start to Loop End (descendants of Start ∩ ancestors of
End).

**Validation — SESE region**, checked once at graph-validation time (alongside
`_validate_connection_kinds`):
1. Each Loop Start pairs with exactly one Loop End and vice-versa.
2. **Single entry / single exit:** the only edge into the region from outside is from Loop Start;
   the only edge out is into Loop End. No body node wires to/from a node outside the loop.
3. Loop Start's `item` output is consumed only inside its region.
4. Every body node lies on a Start→End path (no dead-ends inside the region).
5. Exactly one wire into `loop_end.input`.

### 2. Iteration model

**Planning (once):** compute loop regions; mark body-interior nodes **and Loop End** as
"owned by this loop" and remove them from the top-level pass. Loop Start and everything outside
loops run normally.

**Loop Start is the driver.** When the executor reaches Loop Start it:
1. Resolves `input` → an ordered item list (materializing a dataset with the `max_rows` guard).
2. For each item `i`: seeds `loop_start.item = items[i]`, `index = i`, then runs the body sub-DAG
   using the **same node-execution code path** as the main engine. We factor today's per-node logic
   into a shared `_run_subgraph` helper used by both `execute()` and the driver, so behavior
   (timeouts, retries, expression eval, logging, dataset auto-expand) is identical inside and outside
   a loop. It captures the value flowing into `loop_end.input` for this iteration.
3. After all iterations: writes `loop_end.results` (captured values **in item order**, or a
   `DatasetRef` when `output_mode=dataset`) and `loop_end.errors`. Downstream of Loop End runs
   normally, reading `loop_end.results` as an ordinary output — it has no idea a loop happened.

**Concurrency:** `concurrency=1` runs iterations strictly sequentially (deterministic; the only mode
that could host an accumulator later). `concurrency=N` runs up to N at once under an
`asyncio.Semaphore` (Map's mechanism); results still collected in item order.

**Per-iteration errors:** `fail` aborts the whole loop (Loop Start node errors, naming the offending
index); `continue` records `{index, error, input}` on `errors` and proceeds. Same semantics as Map's
`on_error`.

### 3. Iteration-aware events & run-result model

1. **Event protocol — `iteration` path.** `node_started`/`node_finished` gain an optional
   `iteration` field: a **list of ints** (a path, for nesting). Normal nodes omit it; body node in
   outer iteration 3 → `[3]`; nested → `[3, 7]`. Add lightweight loop-progress events:
   `loop_started {loop_id, total}`, `iteration_finished {loop_id, index, status}`,
   `loop_finished {loop_id, succeeded, failed}` (drives live "37 / 100" + grouping).
2. **`node_events` keying fix.** Today `node_events[node_id] = clean` would let iteration 2 overwrite
   iteration 1. Change the key to `(node_id, iteration_path)` so every iteration is retained.
3. **`NodeRun` table — add `iteration_path`.** One nullable column (`""` for normal nodes, `"3"`,
   `"3.7"` nested). Composite index becomes `(run_id, node_id, iteration_path)`. DB migration.
   **Footgun:** the Alembic **revision id must be ≤32 chars** or the Postgres upgrade crashes (SQLite
   tests won't catch it).
4. **Core `RunResult` stays shape-compatible.** `RunResult.nodes` remains `dict[node_id,
   NodeRunResult]`; for a body node it holds the **last** iteration's result, so existing consumers
   (sub-workflow leaf extraction, editor "last output", retry/replay) are unchanged. Per-iteration
   detail lives in `NodeRun` rows + the event stream, which the inspector reads.

### 4. Subprocess runner / `noodle_runtime`

Loops run **entirely inside one engine execution** (the body is the same graph), so in subprocess
mode the loop driver + body run inside the `noodle_runtime` subprocess's own engine.

- **No host callback, no subprocess-per-row.** Loops never use `workflow_caller`, so they do **not**
  touch `_needs_host_callbacks` — immune to that deadlock class by construction (unlike Map).
- **Iteration-tagged events cross the boundary for free** — they're JSON dicts on the existing
  protocol; only the host-side keying + `NodeRun.iteration_path` change.
- **Cancellation reused:** the driver `await`s each iteration, so `cancel_run` → `task.cancel()`
  unwinds via `CancelledError`. Per-node timeouts apply per body-node-execution;
  `workflow_run_timeout_seconds` bounds the whole loop.
- **Propagation caveat:** loop logic lives in **core engine** + the two **builtin nodes**, which are
  *copied* (non-editable) into each built environment via `uv pip install packages/core|nodes`.
  **Existing environments need a rebuild** to gain loop support — the standard propagation for any
  engine/node change. New envs get it automatically.

### 5. Editor & per-iteration inspector

**Key difference from Map Group:** loop body nodes are **real nodes in the main graph**, not a hidden
child workflow — so the `childWorkflows` machinery is *not* reused. Simpler editor state.

**Authoring:**
- Dragging "Loop" from the palette drops **both** nodes, pre-paired (`loop_end.loop_start_id`
  auto-set) and spaced apart.
- Wire `loop_start.item → … body … → loop_end.input` like normal nodes.
- **Live validation** extends `validateConnection` / `connectionValidation.ts`: block any wire that
  crosses the loop boundary, with a clear inline reason, so an invalid loop can't be built.
- **v1 visuals:** distinct Loop Start/End node styling (repeat icon, accent), a subtle tint/badge on
  region nodes, and a live progress chip on Loop Start (`37 / 100`). A full bounding-box frame is
  polish, not required for v1.

**Per-iteration inspector:**
- A body node's detail view gains an **iteration selector** (`Iteration 3 / 100`), showing that
  row's output/logs/error from the `(node_id, iteration_path)` `NodeRun` rows. Default selection =
  first failed iteration if any, else iteration 1.
- Loop Start's detail view shows item count, succeeded/failed, and the `errors` list.
- Streams live during a run.

### 6. Nesting

- **Recursive driver.** An inner Loop Start inside the outer body runs its own driver per inner item;
  the iteration path accumulates (`[i, j]`, persisted `"i.j"`) — reuses the Section 3 path model.
- **Well-nestedness rule (on top of SESE):** any two regions are **disjoint or strictly nested**,
  never partially overlapping; an inner region is fully contained in the outer body. Enforced live in
  the editor and re-validated in the engine.
- **Concurrency multiplies:** outer `C₀` × inner `Cᵢ` body executions in flight. Sequential default
  keeps it sane; document the multiplication and bound total in-flight iterations.
- **Inspector goes hierarchical:** breadcrumb selector (outer `3 / 100` → inner `7 / 12`).

### 7. Edge cases

1. **Empty input** → zero iterations; `results = []` (or an empty dataset when
   `output_mode=dataset`); not an error.
2. **Dataset over `max_rows`** → fail fast before any iteration (Map Dataset guard message).
3. **`on_error=fail` mid-loop** → loop aborts with offending index; failed iteration's `NodeRun` row
   still persisted for inspection.
4. **`on_error=continue`** → failures on `errors`; `results` holds successes in index order.
5. **Body dead-end** (node not reaching Loop End) → validation error.
6. **Loop End single input** → exactly one wire; merge upstream for multiple values.
7. **Deleting one of the pair** → unpair + warn (mirrors Map Group cleanup).
8. **Cancellation mid-loop** → `CancelledError` unwinds through nested iterations; partial `NodeRun`
   rows remain.
9. **Artifact refs in rows** pass through to the body unchanged (like Map).

## Testing strategy

- **Core engine (pytest):** region computation (linear, branched, nested, and *rejected*
  boundary-crossing / non-well-nested graphs); driver iteration order; sequential vs bounded
  concurrent; item seeding; collection ordering; empty input; dataset input + `max_rows`; `on_error`
  fail/continue; nested loops producing correct `[i, j]` paths; cancellation mid-iteration;
  `output_mode=records` returns a list and `output_mode=dataset` returns a `DatasetRef` (incl. the
  non-dict wrapping rule) via the registered writer hook.
- **Events:** iteration-tagged events; loop-progress events; the keying fix (iteration 2 does not
  overwrite iteration 1).
- **Persistence:** one `NodeRun` per `(node_id, iteration_path)`; migration applies on **Postgres**
  (explicitly — SQLite won't catch the ≤32-char revision-id footgun); new index works.
- **Subprocess/runtime:** a looped workflow runs end-to-end through `noodle_runtime` **without** a
  host callback (assert loops don't touch `_needs_host_callbacks`); iteration-tagged events stream
  across the boundary.
- **Web (vitest):** boundary + well-nestedness rules in `connectionValidation`; paired add/delete in
  `store`; iteration-selector inspector incl. nested breadcrumb.
- **Deprecation:** assert `loop_over_items` is registered with `deprecated=True` and
  `replacement_id="loop_start"`, still executes for existing graphs, and is hidden from the palette
  for new use.
- **Integration:** the running example — dataset of rows → `Extract → Summarize` body → collected
  results; plus a nested loop and a failing-row-with-continue scenario.

## Files likely touched

- `packages/core/noodle/engine.py` — region detection, `_run_subgraph` extraction, loop driver,
  iteration events.
- `packages/core/noodle/models.py` — any node/manifest support needed for loop ports/validation.
- `packages/core/noodle/datasets.py` — `register_dataset_writer` / `dataset_from_records` hook
  (mirror of the existing materializer hook) for `loop_end output_mode=dataset`.
- `packages/nodes/noodle_nodes/builtin.py` — `loop_start` / `loop_end` nodes; mark `loop_over_items`
  `deprecated=True, replacement_id="loop_start"`.
- `packages/nodes/noodle_nodes/datasets.py` — `register_dataset_writer(...)` concrete writer.
- `apps/api/app/models.py` — `NodeRun.iteration_path` + index.
- `apps/api/alembic/versions/` — migration (revision id ≤32 chars).
- `apps/api/app/services/runner.py` — `node_events` keying + per-iteration `NodeRun` persistence.
- `apps/web/src/editor/` — `store.ts` (paired add/delete), `connectionValidation.ts`, new node
  renderers, `NodeDetails.tsx`/`NDVPanels.tsx` (iteration selector).

## Open questions / follow-ups

- v2: accumulator and break.
- Full bounding-box region frame in the editor (polish).
- Resource bounds for deeply nested high-concurrency loops (pick concrete in-flight cap).
