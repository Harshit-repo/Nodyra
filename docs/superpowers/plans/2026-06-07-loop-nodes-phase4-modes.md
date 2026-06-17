# Loop Nodes — Phase 4 (Loop Modes + Conditional/v2) Implementation Plan

**Goal:** Add a `mode` dropdown to Loop Start — `each` (default) | `batch` | `group` | `range` |
`while` | `until` — extending the for-each driver for batch/group/range and adding a sequential,
state-threading driver for while/until (delivering the deferred v2 accumulator + break).

**Spec:** `docs/superpowers/specs/2026-06-07-loop-modes-design.md`
**Builds on:** Phases 1–3 (engine driver, iteration events/persistence, editor authoring).

**Decisions locked:** expression-based `condition` on Loop Start; `on_max_iterations` (`fail`|`stop`)
and `conditional_output` (`final_state`|`all_states`) are user-selectable params.

Run core tests: `D:/noodle/.venv/Scripts/pytest packages/core/tests/`
Run web tests: `apps/web` → `node_modules/.bin/vitest run`

---

## Task 1: Declare mode + per-mode params + `state` port

**Files:** `packages/nodes/noodle_nodes/builtin.py`,
`packages/nodes/tests/test_loop_nodes_registration.py`

- [ ] Failing test: `loop_start` manifest has outputs `["item","index","state"]` and params include
  `{mode, batch_size, group_key, count, initial, condition, max_iterations, on_max_iterations}`
  (plus existing concurrency/on_error/max_rows); `loop_end` params include `conditional_output`.
- [ ] Add the params to the `@node` decorators (with `choices` for `mode`, `on_error`,
  `on_max_iterations`, `conditional_output`) and extend `loop_start` outputs with `state`. Update the
  function signatures (still raise — engine-driven). Keep defaults: `mode="each"`,
  `max_iterations=1000`, `on_max_iterations="fail"`, `conditional_output="final_state"`.
- [ ] Run tests; commit `feat(nodes): loop mode + per-mode params + state port`.

## Task 2: `_loop_items` for batch / group / range

**Files:** `packages/core/noodle/engine.py`, `packages/core/tests/test_loops.py`

- [ ] Failing unit tests for a new signature `_loop_items(value, *, mode, batch_size, group_key,
  count, max_rows)` returning the iteration units:
  - batch: `_loop_items([1,2,3,4,5], mode="batch", batch_size=2) == [[1,2],[3,4],[5]]`
  - group: rows grouped by key, stable order, unit `{"key": k, "rows": [...]}`
  - range: `_loop_items(None, mode="range", count=3) == [0,1,2]`
  - each: unchanged.
- [ ] Implement. Keep dataset materialization + `max_rows` guard for each/batch/group.
- [ ] Run tests; commit `feat(engine): batch/group/range loop item resolution`.

## Task 3: Wire for-each modes through `_run_loop`

**Files:** `packages/core/noodle/engine.py`, `packages/core/tests/test_loops.py`

- [ ] Failing end-to-end tests: a batch loop doubling each element of `[1,2,3,4,5]` with
  `batch_size=2` and a body that maps over the batch → results `[[2,4],[6,8],[10]]`; a range loop
  `count=3` summing index; a group loop over rows by key.
- [ ] `_run_loop` reads `mode` + per-mode params from `loop_start.params`, calls the extended
  `_loop_items`, and seeds `loop_start.item` with the unit (range still seeds `item=i`). No other
  changes (collection/ordering/output_mode/iteration_path all reused).
- [ ] Run tests + full core suite; commit `feat(engine): run batch/group/range loops`.

## Task 4: Conditional driver — `_run_conditional_loop` (while/until + v2)

**Files:** `packages/core/noodle/engine.py`, `packages/core/tests/test_loops.py`

- [ ] Failing tests:
  - `while` with `initial={{ {"count":0} }}`, `condition={{ state.count < 3 }}`, body increments
    `state.count` → final state `{"count":3}` after 3 iterations.
  - `until` with the negated condition reaches the same result.
  - zero-iteration case (condition already stops) → results == initial.
  - cap: `max_iterations=2`, non-terminating condition, `on_max_iterations="fail"` → loop_end error
    + run error; `on_max_iterations="stop"` → success with the 2nd state + a warning logged.
  - `conditional_output="all_states"` returns `{"final":…, "states":[…]}`.
  - concurrency is ignored (sequential) even if `concurrency>1` is set.
- [ ] Implement `_run_conditional_loop` per the spec pseudocode: seed `state` via `evaluate(initial)`,
  pre-test `condition` each iteration (`until` = negation), run the body via `_execute_nodes` with
  `iteration_path = parent+(i,)` (reuse the Phase 2 ContextVar), thread the value into
  `loop_end.input` as the next state, enforce `max_iterations`/`on_max_iterations`, and write
  `loop_end` outputs honoring `conditional_output`. Seed `loop_start.state`/`index` per iteration.
- [ ] Run tests + full core suite; commit `feat(engine): conditional while/until loop driver (v2 accumulator + break)`.

## Task 5: Driver dispatch by mode

**Files:** `packages/core/noodle/engine.py`, `packages/core/tests/test_loops.py`

- [ ] In `_execute_nodes`, branch on `nodes_by_id[start].params.get("mode")`: `while`/`until` →
  `_run_conditional_loop`, else `_run_loop`. Add a test that both families run in one graph and that
  a nested for-each-inside-while composes.
- [ ] Run tests; commit `feat(engine): dispatch loop driver by mode`.

## Task 6: Subprocess smoke (modes need no host callback)

**Files:** `packages/runtime/tests/test_server.py`

- [ ] Add a subprocess run of a `while` loop and a `batch` loop; assert terminal `result` success and
  that loops remain absent from `_HOST_CALLBACK_NODE_TYPES`.
- [ ] Run; commit `test(runtime): batch + while loops run in-subprocess`.

## Task 7: Web — mode dropdown + conditional params

**Files:** `apps/web/src/editor/store.*` / NDV params, `apps/web/src/editor/store.loops.test.ts`

- [ ] The new params auto-render from the manifest; add a vitest that loading/saving a `while` Loop
  Start round-trips `mode`/`condition`/`initial`/`max_iterations` via `toGraph()`. (Optional polish:
  conditional param visibility keyed on `mode`.)
- [ ] Run vitest + tsc; commit `feat(web): author loop modes (incl. while/until)`.

## Final verification

- [ ] `pytest packages/core/tests/ packages/nodes/tests/test_loop_nodes_registration.py
  packages/runtime/tests/test_server.py -q` and `apps/web` vitest — all green.
- [ ] A non-loop and a default `each` loop are behavior-identical to pre-Phase-4.

## Deferred

- For-each accumulator/reduce; `range` start/step; sliding windows; conditional param show-if UI if
  not done in Task 7.
