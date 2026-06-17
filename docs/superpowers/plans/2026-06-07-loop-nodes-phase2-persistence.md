# Loop Nodes — Phase 2 (Iteration Events + Persistence) Implementation Plan

**Goal:** Make each loop iteration observable and durable. The engine tags per-node events with
an `iteration_path`; the API persists one `NodeRun` per `(node_id, iteration_path)` instead of
overwriting body nodes with only their last iteration.

**Architecture:** A single `iteration_path` ContextVar carries the current loop coordinates
(`(outer_i, inner_j, …)`). The loop driver sets it per iteration; `execute()`'s `emit` choke-point
stamps it onto every event (node_started, node_finished, agent events) so nothing else in the engine
changes. The API runner keeps the existing `node_events` map (keyed by node_id, last-wins) for
back-compat consumers (webhook response, error handlers, guardrails) and adds a parallel
`node_run_records` map keyed by `(node_id, iteration_path)` that drives `NodeRun` row creation. A new
nullable `node_runs.iteration_path` JSON column stores the coordinates.

**Tech Stack:** Python 3.12, asyncio, pytest, SQLAlchemy + Alembic, Pydantic.

**Spec:** `docs/superpowers/specs/2026-06-07-loop-nodes-design.md` (Persistence + Events sections).

**Builds on:** Phase 1 (`docs/superpowers/plans/2026-06-07-loop-nodes-phase1-engine.md`).

---

## File Structure

- `packages/core/noodle/context.py` — **modify**: add `iteration_path` ContextVar.
- `packages/core/noodle/engine.py` — **modify**: stamp events in `emit`; set/reset the ContextVar
  per iteration in `_run_loop`.
- `packages/core/tests/test_loops.py` — **modify**: event-tagging tests.
- `apps/api/app/models.py` — **modify**: `NodeRun.iteration_path` column.
- `apps/api/alembic/versions/0037_node_run_iteration_path.py` — **create**: migration.
- `apps/api/app/services/runner.py` — **modify**: accumulate + persist per-iteration NodeRun rows.
- `apps/api/tests/` — **modify/create**: persistence test (mirror existing run-persistence test).

Run core tests: `D:/noodle/.venv/Scripts/pytest packages/core/tests/`
Run API tests: `D:/noodle/.venv/Scripts/pytest apps/api/tests/`

---

## Task 1: `iteration_path` ContextVar + engine event stamping

**Files:** `packages/core/noodle/context.py`, `packages/core/noodle/engine.py`,
`packages/core/tests/test_loops.py`

- [ ] **Step 1 — failing test.** Append to `test_loops.py` an async test that runs the
  `[1,2,3]` doubling loop with an `on_event` collector and asserts the body node `b` emits three
  `node_finished` events with `iteration_path` `[0]`, `[1]`, `[2]`, and that `loop_end` emits with no
  (or empty) `iteration_path`.

```python
async def test_loop_events_are_iteration_tagged():
    events: list[dict] = []
    async def collect(ev): events.append(ev)
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    await execute(g, registry, on_event=collect)
    paths = sorted(
        ev.get("iteration_path")
        for ev in events
        if ev.get("type") == "node_finished" and ev.get("node_id") == "b"
    )
    assert paths == [[0], [1], [2]]
    e_paths = [
        ev.get("iteration_path")
        for ev in events
        if ev.get("type") == "node_finished" and ev.get("node_id") == "e"
    ]
    assert e_paths == [None]  # loop_end runs at parent scope
```

- [ ] **Step 2 — run, expect FAIL** (`iteration_path` is absent → `[None, None, None]`).

- [ ] **Step 3 — add the ContextVar.** In `packages/core/noodle/context.py`, beside the others:

```python
# Loop iteration coordinates for the currently executing node, outermost first.
# Empty tuple = not inside any loop. Set by the engine's loop driver.
iteration_path: ContextVar[tuple[int, ...]] = ContextVar("iteration_path", default=())
```

- [ ] **Step 4 — stamp events at the emit choke-point.** In `engine.py`, import the var
  (`from noodle.context import … , iteration_path`) and update `execute()`'s `emit`:

```python
    async def emit(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        path = iteration_path.get()
        if path and "iteration_path" not in event:
            event = {**event, "iteration_path": list(path)}
        await on_event(event)
```

`finish` already routes through `emit`, so node_finished gets tagged too. Non-loop runs keep
`path == ()` → no key added → byte-identical events.

- [ ] **Step 5 — set the var per iteration.** In `_run_loop._one_iteration`, wrap the body run:

```python
    async def _one_iteration(i: int, item: Any) -> None:
        async with sem:
            token = iteration_path.set(iteration_path.get() + (i,))
            try:
                iter_outputs = dict(node_outputs)
                iter_outputs[region.start_id] = {"item": item, "index": i}
                st = await _execute_nodes(... )   # unchanged kwargs
                ...
            finally:
                iteration_path.reset(token)
```

Nesting composes automatically: the inner driver reads the outer `(outer_i,)` and appends. The
`loop_end` `finish` call stays outside `_one_iteration`, so it runs at the parent scope (empty path
for a top-level loop). Concurrency is safe — each `asyncio.gather` task copies the context, so the
per-iteration `set` is task-local.

- [ ] **Step 6 — run the test, expect PASS.**

- [ ] **Step 7 — full core suite** (`packages/core/tests/ -q`): no regressions (non-loop events
  unchanged).

- [ ] **Step 8 — commit:** `feat(engine): tag per-node events with loop iteration_path`.

---

## Task 2: nested-loop event paths

**Files:** `packages/core/tests/test_loops.py`

- [ ] **Step 1 — test.** Append a test using the Task 8 nested graph with an `on_event` collector;
  assert the innermost body node emits `iteration_path` values `[0,0]`, `[0,1]`, `[1,0]` (outer
  `[[1,2],[3]]`).

- [ ] **Step 2 — run, expect PASS** (driver already composes paths).

- [ ] **Step 3 — commit:** `test(engine): nested loop events carry full iteration path`.

---

## Task 3: `NodeRun.iteration_path` column + migration

**Files:** `apps/api/app/models.py`, `apps/api/alembic/versions/0037_node_run_iteration_path.py`

- [ ] **Step 1 — model.** Add to `NodeRun` (after `duration_ms`):

```python
    iteration_path: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

Update the composite index to include iteration ordering if helpful — keep
`ix_node_runs_run_id_node_id` and rely on it; no new index required for Phase 2.

- [ ] **Step 2 — migration.** Create `0037_node_run_iteration_path.py` (revision id is 28 chars,
  ≤32 ✓; `down_revision = "0036_artifact_nullable_run"`). `upgrade()` adds a nullable JSON column
  `iteration_path` to `node_runs`; `downgrade()` drops it. Mirror the column-add style of a recent
  migration (e.g. `0036`).

- [ ] **Step 3 — apply on SQLite (smoke).** Run the app's test DB setup or
  `alembic upgrade head` against a scratch SQLite URL to confirm the migration applies. Note the
  ≤32-char revision-id footgun is only fatal on Postgres — still keep the id short.

- [ ] **Step 4 — commit:** `feat(api): add NodeRun.iteration_path column (migration 0037)`.

---

## Task 4: persist one NodeRun per (node_id, iteration_path)

**Files:** `apps/api/app/services/runner.py`

- [ ] **Step 1 — accumulate per-iteration records.** In `_execute_run`, beside
  `node_events: dict[str, dict] = {}`, add:

```python
    node_run_records: dict[tuple[str, tuple], dict] = {}
```

In the `on_event` handler where `clean.get("type") == "node_finished"` sets
`node_events[clean["node_id"]] = clean`, also do:

```python
        path = clean.get("iteration_path")
        key = (clean["node_id"], tuple(path) if isinstance(path, list) else ())
        node_run_records[key] = clean
```

Keeping `node_events` (last-wins by node_id) preserves webhook-response / error-handler / guardrail
behavior unchanged.

- [ ] **Step 2 — persist from the records map.** Replace the
  `for node_id, event in node_events.items():` NodeRun loop with one over
  `node_run_records.values()`, passing `iteration_path=event.get("iteration_path")` to `NodeRun(...)`.
  Non-loop nodes have `key=(node_id, ())` → exactly one row each (unchanged count).

- [ ] **Step 3 — test.** In the API tests, add/extend a run-persistence test: execute a workflow
  containing a 3-row loop through the runner, then query `NodeRun` rows and assert the body node has
  3 rows with `iteration_path` `[0]/[1]/[2]` and the loop_end has 1 row. (Mirror the existing runner
  persistence test harness; if none drives the engine end-to-end, assert at the
  `node_run_records`-keying level via a small unit on the handler.)

- [ ] **Step 4 — run API tests** (`apps/api/tests/ -q`, or the targeted module). Expect PASS.

- [ ] **Step 5 — commit:** `feat(api): persist a NodeRun per loop iteration`.

---

## Final verification

- [ ] `D:/noodle/.venv/Scripts/pytest packages/core/tests/ apps/api/tests/ -q` (mind the known
  pre-existing broker-test hang when local Redis is up, and the unrelated `test_map_nodes` event-loop
  flake).
- [ ] Confirm a non-loop workflow still produces exactly one NodeRun per node with
  `iteration_path = NULL`.

## Deferred to Phase 3

- Editor authoring (paired add/delete, connection validation, per-iteration inspector breadcrumb).
- Reading `iteration_path` back into the run inspector UI.
