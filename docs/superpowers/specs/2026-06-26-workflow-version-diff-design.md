# Workflow Version Diff — Design Spec

**Date:** 2026-06-26
**Feature:** Visual version diffing — side-by-side diff of workflow versions showing added/removed/changed nodes
**Motivation:** Product differentiation vs n8n/Windmill; neither offers a visual canvas-level diff

---

## 1. Architecture

The diff view is a **full-page overlay** rendered as a React portal above the editor canvas (z-index 1000, above the toolbar and existing modals). It is opened from the existing `WorkflowHistory` modal via a "Compare" button added alongside the existing "Restore" button. No new route is needed. Dismissed with × or Escape.

Three layers:

**Version picker bar (top)** — two dropdowns labelled "Base" (older) and "Compare" (newer). Swapping re-runs the diff instantly. Default on open: Compare = the version the user clicked in history, Base = the version immediately before it. "Current draft (unsaved)" is available as a synthetic first entry in the Compare picker, backed by the in-memory editor store graph.

**Diff canvas (centre)** — a read-only ReactFlow instance rendering the *newer* graph. Each node is wrapped in a `DiffNode` component that applies a coloured ring based on diff status. Removed nodes from the base graph are injected as ghosts (semi-transparent, red ring, `pointer-events: none`). Edges follow the same treatment. On open, `fitView` fires after first render to zoom to the bounding box of all changed nodes (not the full graph).

**Param diff panel (right, 280px)** — appears when a changed (amber) node is clicked. Shows a two-column before/after table of every param that differs. Unchanged params hidden by default behind a "Show all X params" toggle.

---

## 2. Types & Shared Definitions

```ts
type DiffStatus = 'added' | 'removed' | 'changed' | 'unchanged'

type ParamDiff = { key: string; before: unknown; after: unknown }

type DiffResult = {
  added: string[]           // node IDs in compare, not in base
  removed: string[]         // node IDs in base, not in compare
  changed: string[]         // same ID, same type, non-position param differs
  unchanged: string[]
  removedNodes: Node[]      // full node objects from base (for ghost rendering)
  addedEdges: string[]
  removedEdges: string[]
  changedParams: Record<string, ParamDiff[]>
}

// React context provided by WorkflowDiffView, consumed by DiffNode
// Provides O(1) status lookup per node without prop-drilling
const DiffContext = createContext<Map<string, DiffStatus>>(new Map())
```

---

## 3. Components

### `WorkflowDiffView`
Top-level overlay. Owns version selection state, graph fetch lifecycle, `DiffResult` computation, and selected-node state. Renders picker bar, summary bar, canvas, and param panel. Wraps the ReactFlow instance in `<DiffContext.Provider value={statusMap}>` so every `DiffNode` can look up its own status.

### `diffWorkflowGraphs(base, compare) → DiffResult`
Pure function. The sole source of diff truth.

Rules:
- `position` (`x`, `y`) is **excluded** from comparison — position-only moves are not "changes"
- Same ID + different `node.type` → treated as remove + add (not a change)
- Null/missing `nodes` or `edges` → defaults to `[]`, never throws
- **Deep comparison uses a stable recursive equal function, not `JSON.stringify`** — `JSON.stringify` does not guarantee key order, so `{a:1, b:2}` and `{b:2, a:1}` would be falsely flagged as "changed". Use a key-sorted deep-equal (e.g. `import { deepEqual } from './diffUtils'` — a small utility that recursively compares values after sorting object keys).

### `diffNodeTypes` / `diffEdgeTypes`
Generated via `useMemo` at module scope (outside the component) by mapping over the existing `nodeTypes` and `edgeTypes` registries. `nodeTypes` in Noodle is a stable module-level constant, so the `useMemo` dep array is `[]`.

```ts
const diffNodeTypes = Object.fromEntries(
  Object.entries(nodeTypes).map(([type, Component]) => [
    type,
    (props: NodeProps) => <DiffNode {...props} WrappedComponent={Component} />,
  ])
)
```

### `DiffNode`
Thin wrapper around the existing node component. Reads its own `DiffStatus` from `DiffContext` via `useContext(DiffContext).get(props.id)`. Applies ring style:
- Added → `box-shadow: 0 0 0 2px #22c55e`
- Removed → `box-shadow: 0 0 0 2px #f87171`, opacity 40%, `pointer-events: none`
- Changed → `box-shadow: 0 0 0 2px #f59e0b`, clickable
- Unchanged → no ring, normal opacity

### `DiffEdge`
Wraps `NoodleEdge`. Removed edges render with a dashed red stroke **only if both source and target node IDs exist in the `renderNodes` id set** — prevents the dangling-edge ReactFlow crash.

### `DiffSummaryBar`
Sticky strip below the picker bar:
`● 3 added  ● 1 removed  ● 2 changed  · 47 unchanged`
with coloured dots. Includes a **"Zoom to changes"** button:
```ts
reactFlow.fitView({
  nodes: [...added, ...removed, ...changed].map(id => ({ id })),
  padding: 0.3,
})
```
This fires in a `useEffect` (after render), not synchronously. The button is **hidden** when `added.length + removed.length + changed.length === 0`.

When all nodes are unchanged, the canvas shows a centred message: **"No differences — this workflow matches the selected version."**

### `NodeParamDiffPanel`
Right sidebar, 280px. Two-column before/after table per changed param. Unchanged params collapsed. Closes on × or outside click.

### `VersionLabelModal`
**Triggered in `Canvas.tsx`**, which owns the publish handler passed to `PublishPill`. The existing `handlePublish` function is wrapped: before calling `api.publishWorkflow(...)`, it sets `showLabelModal: true`. The modal resolves with a `label: string | null`. If the user dismisses without entering a label, publish proceeds with `label: null`. On publish failure, the modal stays open with an inline error and the label value preserved.

Published versions show a **"published" badge** in both version pickers — this already exists on `WorkflowVersionInfo.published` and simply needs to be rendered in the picker option.

---

## 4. Data Flow

### Entry point
`WorkflowHistory` modal gains a "Compare" button on each version row (visible only when `versions.length >= 2`). Clicking it passes the selected version to `WorkflowDiffView` and closes the modal.

### Graph loading
`GET /workflows/{id}/versions` is updated to return **metadata only** (no `graph` field). To preserve the existing node count display in `WorkflowHistory`, the response adds a `node_count: int` field computed server-side. The existing `nodeDiff` helper in `WorkflowHistory` is updated to use `v.node_count` instead of `v.graph?.nodes?.length`.

A new endpoint `GET /workflows/{id}/versions/{version_id}/graph` returns `{ graph: WorkflowGraph }`. Graphs are lazy-fetched when a version is selected in either picker.

Each picker manages its own fetch state (`idle | loading | error | loaded`). While loading, the picker shows a spinner and the canvas shows a 30% opacity overlay of whichever graph is already loaded.

### Ghost node injection
After `diffWorkflowGraphs` runs, `WorkflowDiffView` builds:

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

const renderNodeIds = new Set(renderNodes.map(n => n.id))

const renderEdges = [
  ...compare.edges.map(e => ({ ...e, data: { ...e.data, diffStatus: result.addedEdges.includes(e.id) ? 'added' : 'unchanged' } })),
  ...result.removedEdges
    .filter(id => {
      const edge = base.edges.find(e => e.id === id)
      return edge && renderNodeIds.has(edge.source) && renderNodeIds.has(edge.target)
    })
    .map(id => ({ ...base.edges.find(e => e.id === id)!, data: { diffStatus: 'removed' as DiffStatus } })),
]
```

### Version picker constraints
- "Current draft (unsaved)" appears first in the Compare picker; sourced from the editor store (no fetch needed).
- Each picker disables whichever version the other picker has selected.
- Versions with `graph: null` show `(no graph)` suffix and are disabled.
- Published versions show a `published` badge in the option.

### Publish label flow
`VersionLabelModal` is triggered in `Canvas.tsx` before `api.publishWorkflow(...)`. Label sent as optional `label?: string` in the request body. `WorkflowVersion.label` is a new nullable `VARCHAR(255)` column — no breaking change to existing publish calls.

---

## 5. Error Handling

| Scenario | Handling |
|---|---|
| Graph fetch fails (network) | Version picker shows inline "Failed · Retry". Retry re-triggers the fetch. |
| Graph fetch in-flight on unmount | `AbortController` tied to `useEffect` cleanup cancels the in-flight request. |
| `graph: null` on a version | Version disabled in picker with `(no graph)` suffix. |
| Null/missing `nodes` or `edges` | `diffWorkflowGraphs` defaults to `[]`; canvas shows "No nodes found in this version." |
| Same version in both pickers | Prevented by picker. Guard in `diffWorkflowGraphs`: returns all-unchanged if `base.id === compare.id`. |
| Only one version exists | "Compare" button hidden in `WorkflowHistory`. Diff view unreachable. |
| All nodes unchanged | Canvas shows "No differences — this workflow matches the selected version." "Zoom to changes" hidden. |
| Node count > 300 after ghost injection | Warning banner shown; unchanged nodes hidden. "Show all" toggle to override. |
| Ghost node with `NaN`/`null` position | Sanitised to `{x: 0, y: 0}` before passing to ReactFlow. Console warning emitted. |
| Publish API failure after label modal | Modal stays open with inline error. Label value preserved. |
| Dangling ghost edge (endpoint not in renderNodes) | Edge filtered out before ReactFlow receives it. |

---

## 6. Testing

### Unit tests — `diffWorkflowGraphs` (Vitest)
- Added node → in `added[]`, correct ID
- Removed node → in `removed[]`, in `removedNodes[]`
- Changed node → in `changed[]`, correct `changedParams` entry
- **Position-only change → `unchanged[]`** (key correctness test)
- Key-order-only change `{a:1,b:2}` vs `{b:2,a:1}` → `unchanged[]` (deep-equal correctness test)
- Type change (same ID, different type) → remove + add, not changed
- Both versions empty → empty diff, no crash
- Null `nodes`/`edges` → defaults to `[]`, no crash
- Same ID both sides → all `unchanged`, empty `changedParams`

### Unit tests — components (Vitest + React Testing Library)

**`NodeParamDiffPanel`**
- Renders before/after per changed param
- Unchanged params hidden by default, visible after "Show all" toggle
- Closes on × click

**`VersionLabelModal`**
- Empty label → publishes without `label` field in request
- Non-empty label → `label` included in request body
- Publish failure → error shown, label value preserved in input

### Integration tests — `WorkflowDiffView` (Vitest + RTL, ReactFlow mocked)
- Default versions correct (compare = clicked, base = one before)
- "Current draft" appears first in compare picker
- Published badge visible on published versions in picker
- Same version disabled in opposite picker
- Summary bar shows correct counts
- All-unchanged diff → "No differences" message shown, "Zoom to changes" hidden
- Clicking changed node opens param panel
- Graph fetch failure → retry button visible, re-triggers fetch on click
- `AbortController.abort()` called on unmount during in-flight fetch
- Node count > 300 → warning banner, only diff'd nodes rendered

### E2E — `workflow-diff.e2e.ts` (Playwright)
- Publish workflow with node A; add node B; publish again with label "Added node B"
- Open history → click Compare on v2
- Assert diff canvas visible
- Assert node B has green ring (added)
- Assert summary bar reads "1 added"
- Assert version label "Added node B" appears in picker with published badge
- Assert "Zoom to changes" button triggers fitView
- Assert comparing v1 to v1 shows "No differences" message

---

## 7. Backend Changes

| Change | Details |
|---|---|
| Migration | `WorkflowVersion.label VARCHAR(255) NULL` |
| `GET /workflows/{id}/versions` | Strip `graph` from response; add `node_count: int` computed server-side |
| `GET /workflows/{id}/versions/{version_id}/graph` | New endpoint returning `{ graph: WorkflowGraph }` |
| `POST /workflows/{id}/publish` | Accept optional `label: str \| None` in request body, write to `WorkflowVersion.label` |
| `WorkflowVersionInfo` schema | Add `node_count: int`, `label: str | None` fields |

---

## 8. Out of Scope

- Line-level param diffs (character-level diffing within a string value)
- Diff between different workflows (cross-workflow comparison)
- Sharing a diff view via URL (no new route)
- Real-time version list updates while diff view is open
- Keyboard navigation between diff'd nodes (arrow keys)
