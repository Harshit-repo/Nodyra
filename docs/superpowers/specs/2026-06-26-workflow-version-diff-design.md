# Workflow Version Diff — Design Spec

**Date:** 2026-06-26
**Feature:** Visual version diffing — side-by-side diff of workflow versions showing added/removed/changed nodes
**Motivation:** Product differentiation vs n8n/Windmill; neither offers a visual canvas-level diff

---

## 1. Architecture

The diff view is a **full-page overlay** rendered as a React portal above the editor canvas. It is opened from the existing `WorkflowHistory` modal via a "Compare" button (added alongside the existing "Restore" button). No new route is needed. Dismissed with × or Escape.

Three layers:

**Version picker bar (top)** — two dropdowns labelled "Base" (older) and "Compare" (newer). Swapping re-runs the diff instantly. Default on open: Compare = the version the user clicked in history, Base = the version immediately before it. "Current draft (unsaved)" is available as a synthetic first entry in the Compare picker, backed by the in-memory editor store graph.

**Diff canvas (centre)** — a read-only ReactFlow instance rendering the *newer* graph. Each node is wrapped in a `DiffNode` component that applies a coloured ring based on diff status. Removed nodes from the base graph are injected as ghosts (semi-transparent, red ring, `pointer-events: none`). Edges follow the same treatment. On open, `fitView` fires after first render to zoom to the bounding box of all changed nodes (not the full graph).

**Param diff panel (right, 280px)** — appears when a changed (amber) node is clicked. Shows a two-column before/after table of every param that differs. Unchanged params hidden by default behind a "Show all X params" toggle.

---

## 2. Components

### `WorkflowDiffView`
Top-level overlay. Owns version selection state, graph fetch lifecycle, `DiffResult` computation, and selected-node state. Renders picker bar, summary bar, canvas, and param panel.

### `diffWorkflowGraphs(base, compare) → DiffResult`
Pure function. The sole source of diff truth.

```ts
type DiffResult = {
  added: string[]           // node IDs in compare, not in base
  removed: string[]         // node IDs in base, not in compare
  changed: string[]         // same ID, same type, non-position param differs
  unchanged: string[]
  removedNodes: Node[]      // full node objects from base (for ghost rendering)
  addedEdges: string[]
  removedEdges: string[]
  changedParams: Record<string, { key: string; before: unknown; after: unknown }[]>
}
```

Rules:
- `position` (`x`, `y`) is **excluded** from comparison — position-only moves are not "changes"
- Same ID + different `node.type` → treated as remove + add (not a change)
- Null/missing `nodes` or `edges` → defaults to `[]`, never throws

### `diffNodeTypes`
Generated via `useMemo` by mapping every key in the existing `nodeTypes` registry to a `DiffNode`-wrapped version. Same pattern for `diffEdgeTypes`. This avoids manually listing every node type.

```ts
const diffNodeTypes = useMemo(() =>
  Object.fromEntries(
    Object.entries(nodeTypes).map(([type, Component]) => [
      type,
      (props) => <DiffNode {...props} WrappedComponent={Component} />,
    ])
  ), []);
```

### `DiffNode`
Thin wrapper around the existing node component. Reads diff status from `DiffContext`. Applies ring style:
- Added → `box-shadow: 0 0 0 2px #22c55e`
- Removed → `box-shadow: 0 0 0 2px #f87171`, opacity 40%, `pointer-events: none`
- Changed → `box-shadow: 0 0 0 2px #f59e0b`, clickable
- Unchanged → no ring, normal opacity

### `DiffEdge`
Wraps `NoodleEdge`. Removed edges render with a dashed red stroke **only if both source and target node IDs exist in `renderNodes`** — prevents the dangling-edge ReactFlow crash.

### `DiffSummaryBar`
Sticky strip below the picker bar:
`● 3 added  ● 1 removed  ● 2 changed  · 47 unchanged`
with coloured dots. Includes a **"Zoom to changes"** button that calls:
```ts
reactFlow.fitView({ nodes: [...added, ...removed, ...changed].map(id => ({ id })), padding: 0.3 })
```
Fires in a `useEffect` (after render), not in `useMemo`. Hidden when `added.length + removed.length + changed.length === 0` (nothing to zoom to).

### `NodeParamDiffPanel`
Right sidebar, 280px. Two-column before/after table per changed param. Unchanged params collapsed. Closes on × or outside click.

### `VersionLabelModal`
Shown on publish before the existing `POST /workflows/{id}/publish` call. Prompts for an optional label (empty = publish without label). On publish failure, overlay stays open with inline error and label value preserved in local state.

---

## 3. Data Flow

### Entry point
`WorkflowHistory` modal gains a "Compare" button on each version row. Clicking it passes the selected version to `WorkflowDiffView` and closes the modal.

### Graph loading
`GET /workflows/{id}/versions` is updated to return **metadata only** (no graph). A new endpoint `GET /workflows/{id}/versions/{version_id}/graph` returns the full graph for a single version. Graphs are lazy-fetched when a version is selected in either picker, not upfront.

Each picker manages its own fetch state. While fetching, the picker shows a spinner and the canvas shows a low-opacity (30%) overlay of whichever graph is already loaded — not a CSS blur (expensive on large canvases).

### Ghost node injection
After `diffWorkflowGraphs` runs, `WorkflowDiffView` builds the final ReactFlow node array:

```ts
const statusMap = new Map<string, DiffStatus>([
  ...result.added.map(id => [id, 'added'] as const),
  ...result.changed.map(id => [id, 'changed'] as const),
  ...result.unchanged.map(id => [id, 'unchanged'] as const),
])

const renderNodes = [
  ...compare.nodes.map(n => ({ ...n, data: { ...n.data, diffStatus: statusMap.get(n.id) ?? 'unchanged' } })),
  ...result.removedNodes.map(n => ({ ...n, data: { ...n.data, diffStatus: 'removed' as DiffStatus } })),
]
```

Ghost node positions come from the base graph. Ghost edges are filtered to only those where both endpoints exist in `renderNodes`.

### Version picker constraints
- The "Current draft (unsaved)" synthetic entry appears first in the Compare picker, sourced from the editor store (no fetch).
- Each picker disables whichever version the other picker has selected (prevents same-version diff).
- Versions with `graph: null` (old rows pre-dating graph storage) are shown with a `(no graph)` suffix and disabled.

### Publish label flow
`VersionLabelModal` captures an optional label before publish. The label is sent as a new optional field in the existing publish request body. `WorkflowVersion.label` is a new nullable `VARCHAR(255)` column (one migration, no breaking change to existing publish calls).

---

## 4. Error Handling

| Scenario | Handling |
|---|---|
| Graph fetch fails (network) | Version shows inline "Failed · Retry" link. Retry re-triggers the fetch. |
| Graph fetch in-flight on unmount | `AbortController` tied to `useEffect` cleanup cancels the request. |
| `graph: null` on a version | Version disabled in picker with `(no graph)` suffix. |
| Null/missing `nodes` or `edges` | `diffWorkflowGraphs` defaults to `[]`, renders empty-state message: "No nodes found in this version." |
| Same version in both pickers | Prevented by picker (other version disabled). Guard in `diffWorkflowGraphs`: returns all-unchanged if `base.id === compare.id`. |
| Only one version exists | "Compare" button hidden in `WorkflowHistory`. Diff view unreachable. |
| Node count > 300 after ghost injection | Warning banner: "Workflow too large to show all nodes — displaying changed nodes only." Unchanged nodes hidden. "Show all" toggle to override. |
| Ghost node with `NaN`/`null` position | Sanitised to `{x: 0, y: 0}` before passing to ReactFlow. Console warning emitted. |
| Publish API failure after label modal | Modal stays open with inline error. Label value preserved in input. |
| Dangling ghost edge (endpoint missing) | Edge skipped silently (filtered before ReactFlow receives it). |

---

## 5. Testing

### Unit tests — `diffWorkflowGraphs` (Vitest)
- Added node → `added[]`, correct ID
- Removed node → `removed[]`, present in `removedNodes[]`
- Changed node → `changed[]`, correct `changedParams` entry
- **Position-only change → `unchanged[]`** (key correctness test)
- Type change (same ID, different type) → remove + add, not changed
- Both versions empty → empty diff, no crash
- Null `nodes`/`edges` → defaults to `[]`, no crash
- Same version both sides → all `unchanged`, empty `changedParams`

### Unit tests — components (Vitest + React Testing Library)
**`NodeParamDiffPanel`**
- Renders before/after per changed param
- Unchanged params hidden by default, visible after "Show all" toggle
- Closes on × click

**`VersionLabelModal`**
- Empty label → publishes without label field
- Non-empty label → label included in request body
- Publish failure → error shown, label value preserved

### Integration tests — `WorkflowDiffView` (Vitest + RTL, ReactFlow mocked)
- Default versions correct (compare = clicked, base = one before)
- "Current draft" appears first in compare picker
- Same version disabled in opposite picker
- Summary bar shows correct counts
- Clicking changed node opens param panel
- Graph fetch failure → retry button visible, re-triggers fetch on click
- `AbortController.abort()` called on unmount during fetch
- Node count > 300 → warning banner, only diff'd nodes rendered

### E2E — `workflow-diff.e2e.ts` (Playwright)
- Publish workflow with node A; add node B; publish again with label "Added node B"
- Open history → click Compare on v2
- Assert diff canvas visible
- Assert node B has green ring (added)
- Assert summary bar reads "1 added"
- Assert version label "Added node B" appears in picker
- Assert "Zoom to changes" button triggers `fitView`

---

## 6. Backend Changes

| Change | Details |
|---|---|
| Migration | `WorkflowVersion.label VARCHAR(255) NULL` |
| `GET /workflows/{id}/versions` | Strip `graph` from response (metadata only) |
| `GET /workflows/{id}/versions/{version_id}/graph` | New endpoint returning `{ graph: WorkflowGraph }` |
| `POST /workflows/{id}/publish` | Accept optional `label: str` in request body, write to `WorkflowVersion.label` |

---

## 7. Out of Scope

- Line-level param diffs (showing which character in a string changed) — a word-level before/after is sufficient
- Diff between workflows (cross-workflow comparison)
- Sharing a diff view via URL (no new route)
- Real-time version list updates while diff view is open
