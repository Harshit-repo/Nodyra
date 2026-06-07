# Loop Nodes — Phase 5 (Deferred Extras) Implementation Plan

**Goal:** Ship the four remaining loop deferrals: `range` start/step, a sliding `window` mode,
for-each `reduce` (accumulator over a fixed list), and a distinct loop-boundary node rendering.

**Builds on:** Phases 1–4. Spec to update: `docs/superpowers/specs/2026-06-07-loop-modes-design.md`.

**Param economy:** reuse one shared `step` param (range step + window step) and `batch_size` (window
size); add only `start` (range) and `accumulate` (reduce, reuses the existing `initial` seed +
`state` port). New `mode` choice: `window`.

---

## Task 1: Declare new params + window mode

**Files:** `packages/nodes/noodle_nodes/builtin.py`, `.../tests/test_loop_nodes_registration.py`
- [ ] Failing test: `mode` choices include `window`; params include `start`, `step`, `accumulate`.
- [ ] Add `start` (range), `step` (range/window, default 1), `accumulate` (bool) to `loop_start`;
  add `window` to the `mode` choices. Extend the signature.
- [ ] Commit `feat(nodes): loop window mode + range start/step + accumulate params`.

## Task 2: `_loop_items` range start/step + window

**Files:** `packages/core/noodle/engine.py`, `packages/core/tests/test_loops.py`
- [ ] Failing units: `range` with `start`/`step` (`start=10, step=5, count=4 -> [10,15,20,25]`);
  `window` (`size=batch_size`, `step`) over rows yields overlapping full windows only
  (`[1,2,3,4]`, size 2, step 1 -> `[[1,2],[2,3],[3,4]]`; size 2 step 2 -> `[[1,2],[3,4]]`;
  len<size -> `[]`).
- [ ] Extend `_loop_items` signature with `start`/`step` and a `window` branch.
- [ ] Commit `feat(engine): range start/step + sliding window loop units`.

## Task 3: For-each `reduce` (accumulator)

**Files:** `packages/core/noodle/engine.py`, `packages/core/tests/test_loops.py`
- [ ] Failing e2e: an `each` loop over `[1,2,3,4]` with `accumulate=true`,
  `initial={{ 0 }}`, body `output = state + item` (state on the `state` port) -> `results == 10`.
  Also a `batch` reduce; and assert `accumulate` forces sequential (correct result with
  `concurrency>1`).
- [ ] In `_run_loop`, when `accumulate` is truthy: seed `acc = evaluate(initial)`, force
  sequential, per iteration seed `loop_start` outputs `{item, index, state: acc}`, set
  `acc = value into loop_end.input`, and emit `loop_end.results = acc` (final accumulator; ignores
  `output_mode`). Reuse `_wrap`/`build_context`/`evaluate` for the seed.
- [ ] Commit `feat(engine): for-each reduce (accumulator over a fixed list)`.

## Task 4: Loop-boundary node rendering

**Files:** `apps/web/src/editor/LoopNode.tsx` (create), `Canvas.tsx` (register node type),
`store.ts` (route loop_start/loop_end to the type), `editor.css`, `store.loops.test.ts`
- [ ] A custom node renderer for `loop_start`/`loop_end`: a distinct "loop boundary" card with the
  repeat icon, a **mode badge** (each/batch/group/range/window/while/until), and the key per-mode
  hint (batch size / condition / count). Keeps the normal input/output handles.
- [ ] Route both ids to the new RF node `type` in `addNode` + `loadGraph` (mirrors `mapGroup`),
  register it in `Canvas` `nodeTypes`.
- [ ] vitest: `addNode("loop_start")` produces nodes whose RF `type` is the loop type and the pair
  still links (extends existing authoring test). tsc + web suite green.
- [ ] Commit `feat(web): distinct loop-boundary node rendering with mode badge`.

## Final verification

- [ ] `pytest packages/core/tests/ packages/nodes/tests/test_loop_nodes_registration.py
  packages/runtime/tests/test_server.py -q` + `apps/web` vitest + tsc — all green.
- [ ] Update the loop-modes spec to document window / start / step / accumulate.

## Note

Loop-boundary rendering here is a distinct, robust **styled node** (not a fragile auto-resizing
bounding box). A full auto-framing container around the body remains an optional later polish.
