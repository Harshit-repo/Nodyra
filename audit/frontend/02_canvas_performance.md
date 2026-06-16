# 02 — Workflow Canvas & Rendering Performance

Independent review, 2026-06-16. Files: `apps/web/src/editor/Canvas.tsx` (1150 LOC),
`apps/web/src/editor/NodeCard.tsx` (949), `apps/web/src/editor/connectionValidation.ts`,
the `@xyflow/react` integration.

## What is solid (verified)
- **`nodeTypes`/`edgeTypes` defined at module scope** (`Canvas.tsx:47, 54`) — the
  single most important React Flow perf rule. Defining these inline causes a full
  node remount on every render; here they're stable module constants. Correct.
- **Heavy derivations memoized**: `labeledEdges`, `loopFrames`, `allNodes`,
  `allEdges`, `visibleManifests`, `quickAddResults`, dataset-connection `issues`,
  `selectedMetanodeIds` are all `useMemo`'d on their real deps; event handlers
  (`onDrop`, `handleConnect`, `isValidConnection`, `handleNodesChange`, context-menu
  handlers) are `useCallback`'d. Verified `Canvas.tsx:205-763`.
- **Connection validation is pure + client-side** (`connectionValidation.ts`):
  port-kind compatibility is checked before an edge is created
  (`isValidConnection`), so invalid wires never enter state — matches the backend
  `engine/validation.py` contract, giving immediate UX feedback.
- **Live run overlay without re-mounting nodes**: run status/logs flow through the
  store (`runSlice`) and are read by `NodeCard` via atomic selectors, so streaming
  run events repaint node chrome without rebuilding the graph.

## Findings

### FE-5 — No virtualization / `onlyRenderVisibleElements` for very large graphs (LOW)
React Flow renders all nodes in the DOM. For typical workflows (tens of nodes)
this is correct and avoids virtualization bugs. A 500+-node graph would tax the
DOM, but that's well outside the current product's target and would trade
correctness (edge routing, fit-view) for throughput.
- **Recommendation:** if large-graph customers appear, evaluate React Flow's
  `onlyRenderVisibleElements`. Until then, **do not** add it speculatively — it has
  known edge-rendering caveats.
- **Status:** Reviewed — no action now.

### FE-6 — `NodeCard` (949 LOC) is not wrapped in `React.memo` (LOW — verify)
Because parent re-renders are already minimized by atomic store selectors, custom
nodes mostly repaint only when their own slice of state changes. But React Flow
will re-render node components on viewport/selection churn; a `React.memo` with a
prop comparator on `NodeCard` is cheap insurance for big graphs.
- **Recommendation:** confirm whether React Flow already memoizes node renderers
  internally; if not, wrap `NodeCard` in `memo`. Low effort, measure first.
- **Status:** Verify.

## Verdict
Canvas performance fundamentals are **done right** — the module-scope
`nodeTypes`/`edgeTypes` rule (the one everyone gets wrong) is correct, derivations
and handlers are memoized, and validation is pure and client-side. Remaining items
are large-graph optimizations that should be driven by profiling, not added
speculatively.
