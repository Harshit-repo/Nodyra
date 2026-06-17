# Loop Nodes — Phase 3 (Editor Authoring) Implementation Plan

**Goal:** Make loops authorable and inspectable in the web editor: dropping a loop drops a pre-paired
`loop_start`/`loop_end`, deleting one cleanly unpairs the other, and the run inspector shows which
iteration a node run belongs to.

**Builds on:** Phase 1 (engine) + Phase 2 (`iteration_path` events/persistence, surfaced via
`NodeRunInfo`). Tests: vitest (`apps/web`, `node_modules/.bin/vitest run`).

---

## Task 1: Paired add — dropping Loop Start also drops a linked Loop End

**Files:** `apps/web/src/editor/store.ts`, `apps/web/src/editor/store.loops.test.ts` (create)

- [ ] Failing vitest: `addNode("loop_start", pos)` yields two nodes — a `loop_start` and a
  `loop_end` whose `params.loop_start_id` equals the new `loop_start` id; the end is offset to the
  right; the start is selected.
- [ ] Implement: special-case `manifestId === "loop_start"` in `addNode` to append both nodes
  (mirrors the `map_group` special-case), seeding `loop_end` from `defaultParams` then overriding
  `loop_start_id`. Requires the `loop_end` manifest to be present (`manifestsById`).
- [ ] Run vitest; commit `feat(web): drop loop_start/loop_end pre-paired`.

## Task 2: Paired unpair on delete

**Files:** `apps/web/src/editor/store.ts`, `store.loops.test.ts`

- [ ] Failing vitest: deleting the `loop_start` clears the surviving `loop_end`'s `loop_start_id`
  (so it no longer points at a missing node); `deleteSelection` does the same.
- [ ] Implement a shared helper that, given the set of deleted ids, rewrites any `loop_end` whose
  `loop_start_id` is in that set to `""`, and `console.warn`s. Call it from `deleteNode` and
  `deleteSelection`.
- [ ] Run vitest; commit `feat(web): unpair loop_end when its loop_start is deleted`.

## Task 3: Inspector shows the iteration path

**Files:** `apps/web/src/editor/NodeDetails.tsx` (+ wherever `NodeRunInfo` is consumed), test if a
component test harness exists; otherwise a small pure-helper unit.

- [ ] Surface `iteration_path` on a node run in the inspector — a compact label like `iteration 2`
  / `iteration [1, 0]` when present, nothing for non-loop runs.
- [ ] Run vitest; commit `feat(web): show loop iteration on node runs in the inspector`.

## Deferred (polish / later)

- Full client-side SESE + well-nestedness connection validation (the engine already enforces this
  with precise errors at run time; client lint can come later).
- Custom resizable loop container rendering (like MapGroupNode) and a per-iteration breadcrumb that
  swaps the displayed outputs per iteration.
