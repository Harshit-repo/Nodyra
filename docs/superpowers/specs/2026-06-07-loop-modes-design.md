# Loop Modes — Design (Phase 4)

**Status:** design. Extends the shipped Loop Nodes (Phases 1–3).

**Problem.** The loop pair currently iterates once per row of a list/DatasetRef ("for-each"). Users
also want: process rows in **batches**, process **groups** of related rows together, loop a fixed
**count**, and loop **while/until** a condition holds (which requires carrying state across
iterations — the long-deferred "v2" accumulator + break).

**Decision.** Add a single **`mode`** dropdown on **Loop Start** rather than separate node types —
one configurable node, consistent with the rest of Noodle. `mode` changes *what one iteration is*;
everything downstream (concurrency, `on_error`, `loop_end.output_mode`, region detection, iteration
events/persistence) composes unchanged for the for-each family. The conditional family adds a
sequential, state-threading driver path.

---

## Modes

| `mode` | One iteration's value | Extra params | Notes |
|---|---|---|---|
| `each` (default) | one row | — | today's behavior |
| `batch` | a list of ≤N rows | `batch_size` | last batch may be short |
| `group` | all rows sharing a key | `group_key` | `item = {"key": k, "rows": [...]}` |
| `range` | the index `i` (int) | `count`, `start`, `step` | yields `start, start+step, …` (count of them) |
| `window` | overlapping list of rows | `batch_size` (size), `step` | sliding windows; full windows only |
| `while` | current **state** | `initial`, `condition`, `max_iterations`, `on_max_iterations` | pre-test; runs while condition truthy |
| `until` | current **state** | same as `while` | pre-test on negation; runs while condition falsy, stops when truthy |

**Rejected as a mode:** `top_k` — it changes *how many*, not iteration *shape*; express it as an
upstream limit / the existing `max_rows` guard.

---

## Loop Start

Ports: `item`, `index`, **`state`** (new). For-each modes drive `item` (+ `index`); conditional
modes drive `state` (+ `index`). Unused ports simply carry `None` in the other family.

Params:
- `mode` — choices above, default `each`.
- `concurrency` — for-each only; **forced to 1 for `while`/`until`** (each iteration depends on the
  previous state).
- `on_error` — `fail` | `continue` (per-iteration), as today.
- `max_rows` — input-size guard for `each`/`batch`/`group` (materialized list cap, default 10000).
- `batch_size` (mode=batch), `group_key` (mode=group), `count` (mode=range).
- `initial` (mode=while/until) — seed **state** expression (e.g. `{{ {"count": 0} }}` or any value).
- `condition` (mode=while/until) — expression evaluated each iteration against `state`/`index`
  (e.g. `{{ state.count < 10 }}`).
- `max_iterations` (mode=while/until) — runaway-loop safety cap, default 1000.
- `on_max_iterations` (mode=while/until) — `fail` (raise) | `stop` (emit current state + warn).

UI shows only the params relevant to the selected `mode` (conditional-visibility); without that
support, irrelevant params are simply ignored by the driver.

## Loop End

Ports: `results`, `errors` (unchanged). Params:
- `loop_start_id` (hidden, auto-paired).
- `output_mode` — `records` | `dataset` — applies to the **for-each** family (collected values as a
  list, or a DatasetRef via the registered writer hook). Unchanged from Phase 1.
- `conditional_output` — `final_state` | `all_states` — applies to **while/until**: emit the final
  accumulator value, or `{final, states: [...]}` with every iteration's state. The driver picks
  which output param applies based on the paired Loop Start's `mode`.

---

## Engine

**For-each family** — extend `_loop_items(value, *, mode, …)` to return the ordered list of iteration
*units*:
- `each`: rows (today).
- `batch`: `[rows[i:i+n] for …]`.
- `group`: stable-ordered groups by `group_key`; each unit `{"key": k, "rows": [...]}`.
- `range`: `list(range(count))` (units are ints; `item = i`).
The existing `_run_loop` driver is otherwise unchanged: it seeds `loop_start.item`/`index` per unit,
runs the body sub-DAG, collects the value flowing into `loop_end.input` in order, and honors
`output_mode`. Concurrency, `on_error`, iteration_path events/persistence all carry over.

**Conditional family** — a new `_run_conditional_loop` driver (selected when `mode in {while,until}`):

```
state = evaluate(initial)                 # seed; iteration 0 uses this
states = []
for i in range(max_iterations):
    keep = evaluate(condition, {state, index: i})
    go = keep if mode == "while" else (not keep)
    if not go:
        break
    seed loop_start.state = state, index = i
    run body sub-DAG (same _execute_nodes path, iteration_path = (..., i))
    state = value flowing into loop_end.input        # the new accumulator
    states.append(state)
else:
    if on_max_iterations == "fail":
        raise  -> loop_end error "exceeded {max_iterations} iterations"
    # else: fall through, emit current state + warn
results = state if conditional_output == "final_state" else {"final": state, "states": states}
```

- **Pre-test** semantics for both (`until` = `while not condition`); a loop can run zero times. For
  at-least-once, seed `initial` so the condition starts unsatisfied.
- **Sequential only** — state dependency forbids concurrency.
- The state feedback is in-memory (driver-held), so the **graph stays acyclic** — region detection
  and SESE validation are unchanged.

**Driver dispatch** — in `_execute_nodes`, when a `loop_start` in `loop_regions` is reached, branch
on `nodes_by_id[start].params["mode"]`: `while`/`until` → `_run_conditional_loop`, else `_run_loop`.

---

## Testing strategy

- **Core:** `_loop_items` for batch (incl. short last), group (ordering + key), range; for-each
  end-to-end per mode; conditional while (computes, respects cap fail/stop), until (negation
  semantics, zero-iteration case), `conditional_output` final vs all; concurrency forced to 1 for
  conditional; iteration_path still tagged.
- **Web:** `mode` dropdown round-trips; conditional params persist; (optional) param visibility.

## Reduce (Phase 5)

`accumulate` (bool) on Loop Start threads an accumulator across any for-each mode (each/batch/group/
range/window), seeded by `initial` (the same param while/until uses). Each iteration the `item` port
carries `{"acc": <accumulator>, "item": <unit>}` (single-input-node friendly) and the `state` port
carries the accumulator; the value into Loop End becomes the next accumulator. Sequential by nature;
Loop End returns the **final accumulator** (ignores `output_mode`).

## Implemented in Phase 5

- `range` `start`/`step`; sliding `window` mode; for-each `reduce` (`accumulate`); distinct
  loop-boundary node rendering (a mode badge on Loop Start/End).

## Out of scope / later

- A full auto-resizing **container frame** around the loop body (the current rendering is a styled
  boundary node, not a bounding box).
