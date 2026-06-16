# 01 — Frontend Architecture & State Management

Independent review, 2026-06-16. Files: `apps/web/src/editor/store/` (sliced Zustand
store — `index.ts` 2428 LOC + `graphSlice`/`runSlice`/`clipboardSlice`/`childWorkflowSlice`),
`apps/web/src/App.tsx`, `apps/web/src/EditorPage.tsx`, `apps/web/src/queries/index.ts`,
`apps/web/src/api.ts`.

## What is solid (verified)
- **Sliced Zustand store**: the editor state is split into cohesive slices
  (`graphSlice`, `runSlice`, `clipboardSlice`, `childWorkflowSlice`) re-exported
  through `store/index.ts`. This is the right call for a store this central — each
  slice owns its initial state and action surface.
- **Atomic selectors everywhere**: components subscribe with single-value
  selectors (`useEditor((s) => s.nodes)`, `…s.running`, …) rather than pulling the
  whole store object. This is the idiomatic Zustand pattern that keeps re-renders
  surgical — verified across `Canvas.tsx`, `EditorPage.tsx`.
- **Structural-vs-cosmetic dirty tracking**: `STRUCTURAL = {position, remove, add,
  replace}` (`store/index.ts:826`) — the graph is only marked `dirty` on
  structural changes, so selection/hover churn doesn't trip the unsaved-changes
  state. Child (map-body) workflows track their own `dirty` independently.
- **Dual unsaved-changes guard**: `beforeunload` for real tab-close/refresh
  (`EditorPage.tsx:649-656`) **and** an in-app SPA navigation guard
  (`:658-663`) — covers both escape routes, including dirty child workflows. Many
  builders only do one.
- **Bounded undo/redo history**: `HISTORY_LIMIT = 50` with
  `[...state._past, snapshot].slice(-HISTORY_LIMIT)` on each mutation
  (`store/index.ts:827, 1113…`). Memory is bounded; snapshots are shallow
  (node/edge object identity is shared until a node actually changes).
- **Server state via TanStack Query** (`queries/index.ts`) cleanly separated from
  client/editor state (Zustand) — the canonical React data-layer split.
- **Route-level error isolation**: `App.tsx` wraps routed views in
  `<ErrorBoundary resetKey={location.pathname}>` (`:155, :189`) so a crash in one
  page doesn't white-screen the app and auto-recovers on navigation.

## Findings

### FE-3 — Undo snapshots are full node/edge arrays, not diffs (LOW)
Each structural mutation pushes a `{nodes, edges}` snapshot. With the 50-entry cap
this is fine for normal graphs, but a very large graph (hundreds of nodes) edited
rapidly holds up to 50× the array of references. Object *contents* are shared, so
real cost is the arrays themselves — acceptable today.
- **Recommendation:** leave as-is; revisit with a patch/diff history only if
  profiling on large graphs shows GC pressure. Documented so it's a known tradeoff,
  not a latent surprise.
- **Status:** Reviewed — no action needed now.

### FE-4 — `store/index.ts` action layer is 2428 LOC in one file (LOW)
The slices are split, but the bulk of the action implementations still live in
`index.ts`. It's navigable (clear section grouping) but large for one module.
- **Recommendation:** continue migrating action bodies into their owning slice
  files (graph mutations → `graphSlice`, run/stream → `runSlice`) so `index.ts`
  becomes assembly + shared types. Incremental, low-risk.
- **Status:** Reviewed.

## Verdict
The frontend state architecture is **a strength**: sliced store, atomic selectors,
careful dirty semantics, dual save-guards, bounded history, and a clean
server/client state split. The only items are housekeeping (further slice
extraction) and a theoretical large-graph history optimization — neither blocks
release.
