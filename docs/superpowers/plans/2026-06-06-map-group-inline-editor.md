# Map Group — Inline Editor Feature

## Goal

Allow users to build the per-item body of a map directly on the parent workflow
canvas, instead of navigating to a separate child workflow. The runtime model
(child workflow called once per item) stays completely unchanged.

A `Map Group` node looks like a resizable container with a **Map Start** header
at the top and a **Map End** footer at the bottom. Nodes placed inside the
container form the map body. At runtime the container is equivalent to the
existing `map_items` node.

## Non-Goals

- Do not change the execution engine.
- Do not store body nodes inside the parent workflow graph document. The
  canonical representation remains two separate workflow documents.
- Do not add per-node implicit iteration (the "map every node" toggle already
  rejected in the previous plan).
- Do not support streaming or partial results in this feature.
- Do not support nested inline groups in the first implementation. A Map Group
  body may contain an `execute_workflow` node that calls another map workflow,
  but you cannot place a Map Group inside another Map Group on the same canvas.
- Do not merge the existing `map_items` node with this feature. `map_items`
  remains available for users who want to wire a workflow reference manually.

## Visual Design

### What the user sees

A Map Group on the canvas looks like this:

```
┌─────────────────────────────────────────────────┐
│ ▶▶ MAP  concurrency: 5 · fail · ordered    ···  │  ← header (Map Start)
├─────────────────────────────────────────────────┤
│                                                 │
│   ○ Manual Trigger                              │
│          │                                      │
│   ○ Extract Text                                │
│          │                                      │
│   ○ Ask AI                                      │
│                                                 │
├─────────────────────────────────────────────────┤
│ ◀◀ COLLECT RESULTS                              │  ← footer (Map End)
└─────────────────────────────────────────────────┘
        │ main              │ errors
```

The container has:

- A **header bar** (Map Start) showing concurrency, on_error, and preserve_order.
  Clicking the header opens the inspector for those params.
- A resizable **body area** where the user places and wires nodes.
- A **footer bar** (Map End) with the `main` and `errors` output handles.
- An input handle on the left edge of the header.

The container uses the same amber/orange color family to signal fan-out, distinct
from the purple used by existing group nodes.

### Two modes

The user can switch the body between two modes via a toggle in the header:

**Inline mode** (default): The user builds the body by placing nodes inside the
container. The editor auto-creates and syncs a hidden child workflow. Nodes are
visually embedded.

**Reference mode**: The user picks an existing workflow from a dropdown. The
container shows a single `Execute Workflow` card inside (read-only preview). The
body is that workflow. This is equivalent to wiring `map_items` manually but
with the same visual container.

Switching from inline to reference does not delete the auto-created child
workflow — it is preserved and can be switched back to.

Switching from reference to inline creates a new child workflow (the previously
referenced workflow is not cloned — it remains untouched).

## Data Model

### Parent workflow graph

The parent graph stores a single `map_group` node:

```json
{
  "id": "mg-1",
  "type": "map_group",
  "params": {
    "child_workflow_id": "wf-abc123",
    "mode": "inline",
    "concurrency": 5,
    "on_error": "fail",
    "preserve_order": true,
    "max_items": 10000
  },
  "position": { "x": 300, "y": 200 }
}
```

The parent graph does **not** contain body nodes. They live in the child
workflow.

### Child workflow

A normal Noodle workflow document, auto-created when the Map Group is placed.
Its graph starts with a Manual Trigger. The user adds body nodes to it via the
inline editor. It can be opened independently and tested in isolation.

The child workflow is tagged with:

```json
{
  "meta": {
    "map_group_owner": "parent-workflow-id"
  }
}
```

This tag is informational — it lets the workflow list show "managed by Map
Group" rather than cluttering the user's workflow list. The child workflow is
still a fully normal workflow.

### React Flow canvas state (frontend only, not persisted)

When the editor renders the canvas, body nodes from the child workflow are
injected into the React Flow node list with:

```json
{
  "id": "body-node-1",
  "type": "noodle",
  "parentId": "mg-1",
  "extent": "parent",
  "position": { "x": 60, "y": 80 }
}
```

These injected nodes are never written to the parent workflow graph. They are
loaded from the child workflow on open, and any edits to them are saved back to
the child workflow.

## Backend Changes

### New node: `map_group`

Add to `builtin.py`. Hidden from the palette (`hidden=True`). Params are
identical to `map_items` except `workflow_id` is named `child_workflow_id` for
clarity, and `max_items` is added.

At runtime it calls `_map_call_child` via the same path as `map_items`. No
engine changes.

```python
@node(
    name="Map Group",
    id="map_group",
    hidden=True,
    category="Logic",
    icon="repeat",
    outputs=["main", "errors"],
    params={
        "child_workflow_id": {"widget": "hidden"},
        "mode": {"widget": "hidden"},
        "concurrency": {...},
        "on_error": {...},
        "preserve_order": {...},
        "max_items": {...},
    },
)
async def map_group_node(
    input=None,
    child_workflow_id="",
    mode="inline",
    concurrency=5,
    on_error="fail",
    preserve_order=True,
    max_items=10000,
):
    # identical to map_items — delegates to _map_call_child
    ...
```

### API: no new endpoints

Child workflow creation, update, and deletion all use existing workflow CRUD
endpoints. The editor calls them directly.

### Publish hook: auto-publish child workflow

When the parent workflow is published, the API must also publish the child
workflow (if in inline mode) at the same snapshot. The publish endpoint should
accept an optional `cascade_child_workflows: true` flag that triggers this.

If the child workflow has a validation error (syntax error in a code node, etc.),
the parent publish fails with a clear error naming the child.

## Editor Changes

### Store: dual-graph state

The editor store is extended to hold child workflow state alongside the parent:

```typescript
interface ChildWorkflowState {
  workflowId: string;
  nodes: Node[];
  edges: Edge[];
  dirty: boolean;
  loading: boolean;
  error: string | null;
}

// Added to editor store:
childWorkflows: Map<string, ChildWorkflowState>; // keyed by map_group node id
```

When the parent workflow is loaded, the store:

1. Scans for `map_group` nodes with `mode: "inline"`.
2. Fetches each child workflow's graph concurrently.
3. Injects child nodes into the React Flow node list with `parentId` set.

The parent React Flow node list is always a merge of:

```
parentNodes + flatten(childWorkflows.values().map(c => c.nodes))
```

When child nodes change, only `childWorkflows[mapGroupId].dirty` is set —
the parent's `dirty` flag is not touched.

### `MapGroupNode` React Flow component

A new `mapGroup` node type registered in Canvas.tsx alongside `noodle`, `group`,
`sticky`.

The component renders:
- A `NodeResizer` (min width 300, min height 200).
- A header bar (Map Start) with concurrency/on_error/preserve_order chips and a
  settings icon that opens the inspector.
- A footer bar (Map End) with `main` and `errors` output handles and item count
  from the last run (if available).
- A body area (the React Flow container — child nodes render here automatically
  via `parentId`).
- An "Add node" button in the body that opens the node palette scoped to the
  child workflow.
- A mode toggle (Inline / Reference) in the header.
- An "Open body →" link in the header that opens the child workflow in a new tab.

### Save logic

On user-triggered save (Ctrl+S or the Save button):

1. Save the parent workflow graph (contains only the `map_group` node for the
   map — no body nodes).
2. For each dirty child workflow: `PATCH /api/workflows/{id}/graph`.
3. All saves run concurrently. If any fails, show an error toast naming which
   workflow failed.
4. Auto-save (if enabled) follows the same logic.

### Load logic

On opening a workflow:

1. Load parent workflow graph normally.
2. Find all `map_group` nodes with `mode: "inline"`.
3. Load each child workflow's graph: `GET /api/workflows/{child_workflow_id}`.
4. If a child workflow is not found (deleted externally): mark that `map_group`
   node with an error badge and show a recovery prompt.
5. Inject child nodes into React Flow canvas with `parentId`.

### Undo / redo

Undo/redo operates on a **per-graph** basis:

- Actions on the parent graph (moving the Map Group container, wiring it to
  other nodes) are in the parent undo stack.
- Actions on child nodes (adding a node inside, wiring body nodes) are in a
  separate undo stack scoped to that child workflow.

This avoids the complexity of a merged multi-graph undo stack. The two stacks
are independent. A future iteration can merge them.

## Body Editing Interactions

### Dropping a node inside the container

When the user drags a node from the palette and drops it inside the Map Group
container bounds:

1. The node is created in the child workflow graph (not the parent).
2. The node gets `parentId = mapGroupId` and `extent = "parent"` in React Flow.
3. The child workflow's `dirty` flag is set.

Detection: use React Flow's `onNodeDragStop` with a point-in-bounds check
against the Map Group container rectangle.

### Dragging a node out of the container

When the user drags a body node outside the container bounds and releases:

1. Show a confirmation: "Move this node to the parent workflow?"
2. If confirmed:
   - Remove the node from the child workflow.
   - Add the node to the parent workflow.
   - Clear `parentId` and `extent`.
   - Any edges connecting this node to body nodes are deleted.
3. If the body becomes empty after the move, show a warning badge on the
   container.

Moving a node out of the container removes it from the map body permanently.
This cannot be undone across the graph boundary.

### Dragging a node from the parent into the container

Same as above in reverse. The node moves from the parent workflow to the child
workflow.

### Wiring nodes inside the container

Edges between body nodes are stored in the child workflow's edge list. The
connection validation rules apply identically to the child workflow.

### Wiring across the container boundary

An edge from a body node to a parent-level node (or vice versa) is **blocked**.
The connection validation layer rejects it with: "Nodes inside a Map Group
cannot connect to nodes outside it. Use the Map Group's input/output handles
instead."

The only valid cross-boundary connections are:
- Parent node → Map Group input handle (top of header)
- Map Group `main` output handle → parent node
- Map Group `errors` output handle → parent node

### Empty body warning

If the container has no nodes (or only a Manual Trigger and nothing else), the
footer shows: "Add nodes to the body to run something per item." The workflow
can still be saved but not published with an empty body.

### Manual Trigger in the body

When the editor auto-creates the child workflow, it inserts a Manual Trigger
as the first node. This trigger receives the per-item payload
`{"item": ..., "index": ...}`. It is shown inside the container and cannot be
deleted (the editor blocks deletion of this node with: "The Manual Trigger
receives the map item and cannot be removed.").

## Lifecycle Events

### Create

1. User drags "Map Group" from the node palette onto the canvas.
   The palette shows it in the Logic category.
2. Editor calls `POST /api/workflows` with body:
   ```json
   {
     "name": "[parent name] — map body",
     "meta": { "map_group_owner": "parent-workflow-id" }
   }
   ```
3. The returned workflow ID is stored in `map_group.params.child_workflow_id`.
4. The child workflow auto-contains a Manual Trigger.
5. The Map Group container is placed at the drop position.
6. The parent workflow is marked dirty.

If the child workflow creation API call fails, the Map Group is not placed and
a toast shows the error.

### Duplicate node

When the user copies and pastes a Map Group:

1. Editor calls `POST /api/workflows/{child_workflow_id}/duplicate`.
2. The duplicate node's `child_workflow_id` is set to the new workflow's ID.
3. The new child workflow is tagged with the same parent workflow ID.

Pasting must wait for the duplication API call to complete before placing the
new node. Show a loading indicator on the pasted node while it resolves.

Copying a Map Group without pasting does not create a child workflow until
paste happens.

### Duplicate workflow

When the user duplicates the entire parent workflow:

1. The workflow duplication endpoint already clones the graph.
2. The endpoint must detect `map_group` nodes and clone each child workflow.
3. The cloned parent's `map_group` nodes get new `child_workflow_id` values
   pointing to the cloned child workflows.
4. This must be atomic: if any child clone fails, the whole duplication fails
   and nothing is created.

### Delete Map Group node

When the user deletes a Map Group node:

1. Show a modal:
   > **Delete map body?**
   > The map body workflow "[name]" will be permanently deleted.
   > Alternatively, keep it as a standalone workflow you can reuse later.
   > [Delete body] [Keep as standalone] [Cancel]

2. **Delete body**: call `DELETE /api/workflows/{child_workflow_id}`, then
   remove the Map Group node from the canvas.
3. **Keep as standalone**: clear `meta.map_group_owner` on the child workflow
   (PATCH), then remove the Map Group node. The child workflow appears normally
   in the workflow list.
4. **Cancel**: nothing happens.

### Delete parent workflow

When the parent workflow is deleted:

1. Find all `map_group` nodes in its graph.
2. For each: call `DELETE /api/workflows/{child_workflow_id}` silently.
   Orphaned child workflows tagged with `map_group_owner` are also cleaned up
   in a background sweep (in case the deletion path was interrupted).

### Export / Import

When a workflow is exported to JSON:

1. The export includes the parent workflow graph.
2. For each `map_group` node, the child workflow is exported inline:
   ```json
   {
     "workflow": { ... parent ... },
     "embedded_workflows": {
       "wf-abc123": { ... child workflow ... }
     }
   }
   ```

When imported:

1. Create the child workflows first, get new IDs.
2. Rewrite `child_workflow_id` in all `map_group` nodes to the new IDs.
3. Create the parent workflow.

Importing into an instance where child workflow IDs already exist: always
create new workflows (do not merge/overwrite).

### Reference mode: child workflow deleted externally

If the user is in reference mode and the referenced workflow is deleted from
the workflow list:

1. On next canvas load, the child workflow fetch returns 404.
2. The Map Group shows an error badge: "Body workflow not found."
3. The inspector shows a recovery option: "Select a replacement workflow" or
   "Create a new inline body."

### Inline mode: child workflow edited externally

If the user opens the child workflow directly (via "Open body →" link) and edits
it there, then returns to the parent canvas:

1. On returning to the parent canvas (tab focus or manual refresh), the editor
   re-fetches the child workflow.
2. If the child has changed, inject the updated nodes.
3. If there are unsaved local changes to the child workflow in the parent editor
   AND the child has changed externally, show a conflict prompt:
   > "The body workflow was changed in another tab. Keep your changes or reload?"
   > [Keep mine] [Reload]

## Runtime Edge Cases

### Empty list input

Map Group receives an empty list `[]`. Output is `{"main": [], "errors": []}`.
No child workflow calls are made. Not an error.

### Non-list input

Map Group receives a dict, string, number, or `null`:

- `null` → treat as empty list. Output `{"main": [], "errors": []}`.
- A single dict `{"id": 1}` → wrap as `[{"id": 1}]` and process one item.
  Emit a warning in the run log: "Map Group received a single object; wrapped
  as a one-item list."
- A string or number → emit a run error: "Map Group requires a list input."

### Child workflow not found at runtime

`child_workflow_id` references a workflow that does not exist. The `map_group`
node fails immediately with: "Map Group: child workflow [id] not found."

### Child workflow has no published version

In production runs (deployments): the child workflow must have a published
version. If not, the parent publish fails (see publish hook above). In manual
test runs from the editor, the draft version is used.

### Timeout

Each child workflow call respects the parent run's timeout. If the parent run
times out while child calls are in flight, the in-flight calls are cancelled
(the semaphore exits). The map node emits the results collected so far on the
`main` output with an error indicating the run was cancelled.

### Item input is `null` or `undefined`

Body receives `{"item": null, "index": 0}`. This is valid — the body handles
it. The Manual Trigger passes it through. Body nodes may error on `null` if
they do not handle it, which is governed by `on_error`.

### `on_error: fail`

On the first child workflow error, the map node raises. All in-flight child
calls still complete (current behaviour of `asyncio.gather` without cancellation)
but their results are discarded. A future iteration can add `asyncio.TaskGroup`
for early cancellation.

### `on_error: continue`

All items run. Successful results go to `main`. Failed items go to `errors`
with `{"index": N, "error": "...", "input": {"item": ..., "index": N}}`.
The `errors` output is always present (empty list if no errors).

### Nested Map Groups

A Map Group body may contain a `map_items` or `map_group` call. This is valid.
Nesting depth is limited only by the existing `call_chain` cycle detection.
Cost and wall time grow multiplicatively — the run metadata shows estimated
remaining time per level.

The editor blocks placing a Map Group *visually* inside another Map Group
container on the same canvas (non-goal). But chaining via workflow reference
is fully supported.

### Circular reference

Parent workflow A contains a Map Group whose body calls workflow A. The existing
`call_chain` ContextVar detects this and raises: "Circular workflow call
detected." Same as today's `execute_workflow` cycle detection.

### Very large lists

If `max_items` is exceeded, the map node fails before any child calls with:
> "Map Group received 50,000 items but max_items is 10,000. Increase max_items
> explicitly or reduce the list upstream."

This matches the Map Dataset behaviour. The default cap is 10,000.

### Child workflow version mismatch

When the parent workflow is published, the child workflow version at publish
time is snapshotted in the parent's published version metadata. At runtime the
published version of the child is used — even if the child has been updated
since. This matches the `workflow_version_id` snapshotting that `execute_workflow`
already supports.

## Editor Edge Cases

### Multiple Map Groups in one workflow

Any number of `map_group` nodes may exist in the same workflow. Each has its
own child workflow. The editor loads all child workflows concurrently on open.
Save sends all dirty child workflows concurrently.

### Overlapping Map Group containers

React Flow allows nodes to visually overlap. If two Map Group containers
overlap, nodes inside each belong to their respective containers (determined by
which container was the drop target, not visual bounds). The editor does not
prevent visual overlap but the underlying data remains unambiguous.

### Undo after moving a node across the boundary

Undoing a "move node into map body" action is **not supported** in the first
implementation — a warning is shown when dragging across the boundary. A future
iteration can add cross-graph undo with a merged transaction log.

### Copy / paste a node inside a Map Group

Paste target detection:

- If the clipboard node had `parentId` of a Map Group and the user pastes while
  that Map Group is on the canvas: paste inside the same Map Group.
- If the Map Group is not present (pasted to a different workflow): paste as a
  parent-level node with no `parentId`.

Pasting a Mix Group node from another workflow: triggers the child workflow
duplication flow (same as duplicate node above).

### Selecting all nodes (Ctrl+A)

Select all selects all parent-level nodes and all body nodes from all child
workflows on the canvas. Body nodes can be selected and moved within their
container, but cannot be dragged to the parent canvas via select-all drag (only
via explicit drag-out).

### Canvas performance with large bodies

If a Map Group body has more than 30 nodes, the container shows a "Collapse
body" toggle. When collapsed, the body area is hidden and replaced with a
summary ("27 nodes"). The child workflow can still be edited via "Open body →".
This prevents performance degradation from large React Flow node counts.

### Map Group in a workflow that is used as a body

A workflow used as a reference-mode body may itself contain a Map Group. This
is valid at runtime. The inline editor does not recursively render the nested
map inline — it shows the outer workflow's nodes only. The inner map is shown
as a normal `map_group` card.

## Publishing and Versioning

### Pre-publish validation

Before the parent workflow can be published:

1. All child workflow bodies must pass graph validation (no disconnected nodes
   with required inputs, no missing credentials, etc.).
2. Child workflows that are in inline mode are auto-published atomically with
   the parent.
3. If any child fails validation, the parent publish fails with a clear message
   naming the child workflow and the failing node.

### Version snapshot

The published parent workflow version stores:

```json
{
  "map_group_snapshots": {
    "mg-1": {
      "child_workflow_id": "wf-abc123",
      "child_workflow_version_id": "ver-xyz"
    }
  }
}
```

At runtime, the specific child workflow version is used — not the latest draft.
This matches how `execute_workflow` already handles `workflow_version_id`.

### Rollback

Rolling back the parent workflow to an older published version restores the
`map_group_snapshots` references. The older child workflow version still exists
(versions are immutable). The rollback is consistent.

### Reference mode and versioning

In reference mode, the user explicitly picks which workflow to call. The Map
Group stores `child_workflow_id` but not a pinned version. The publish hook
resolves the latest published version of the referenced workflow and snapshots
it, same as inline mode.

## Run UI

### Map Group node result

The run panel shows the Map Group node result card with:

```
MAP GROUP  ✓ 98/100 items
─────────────────────────────
main: 98 results
errors: 2 items failed
avg child ms: 1,240
concurrency: 5
estimated run time at start: 24s
actual run time: 26s
```

### Child run links

Each child run is stored as a normal run record. The Map Group result card
shows: "View 100 child runs →" which opens a filtered run list for that parent
run ID.

The first failing child run is linked directly: "View first error →".

### Progress during long maps

For maps with more than 100 items, the run panel shows a live progress bar:
`completed N / total` — updated via the existing SSE run event stream. After
the first `concurrency` items complete, the estimated remaining time is shown.

## Migration from `map_items`

Existing `map_items` nodes are not automatically converted. They continue to
work unchanged.

An optional migration action is shown in the inspector for any `map_items` node:
"Convert to inline Map Group". This:

1. Creates a Map Group node at the same position.
2. Uses the same `workflow_id` as the Map Group's `child_workflow_id`.
3. Copies `concurrency`, `on_error`, `preserve_order` params.
4. Loads the referenced workflow's nodes into the inline view.
5. Removes the `map_items` node and rewires edges to the new Map Group.

This is reversible — the user can undo it before saving.

## Testing Requirements

### Backend

- `map_group` node calls child workflow once per item (same tests as `map_items`
  since the implementation delegates).
- `map_group` node with `max_items` cap raises before any calls.
- `map_group` publish hook auto-publishes child workflow.
- `map_group` publish fails if child has a validation error.
- Duplicate workflow clones child workflows and rewrites IDs atomically.
- Delete workflow cascades to orphaned child workflows.
- Export/import round-trip preserves body content.

### Frontend (unit)

- Map Group node placed on canvas triggers child workflow creation API call.
- Body nodes load with `parentId` set.
- Dropping a node inside the container adds it to the child workflow graph.
- Dragging a node outside the container prompts for confirmation.
- Cross-boundary edge creation is rejected.
- Save dispatches parent save + child save for dirty child.
- Deleting Map Group shows the modal and calls correct API based on choice.
- Reference mode toggle loads the workflow picker and does not delete the
  existing inline child workflow.

### Frontend (integration / E2E)

- Create Map Group → build inline body → save → reload → body nodes appear.
- Publish parent → child is auto-published → run the workflow → correct per-item
  output.
- Delete Map Group with "Keep as standalone" → child workflow appears in list.
- Duplicate workflow → new child workflows created → edits to one do not affect
  the other.
- Export workflow → JSON contains `embedded_workflows` → import → works.

## Implementation Order

1. **`map_group` backend node** — hidden alias for `map_items`, adds `max_items`
   cap. (1 hour)
2. **`MapGroupNode` component** — static visual shell with header/footer and
   resizer, no interactivity yet. (1 day)
3. **Child workflow auto-create** — on drop, call create API, store ID in params.
   (half day)
4. **Child workflow load + inject** — on canvas open, fetch child graph, inject
   nodes with `parentId`. (1 day)
5. **Dual-graph store** — extend store to hold `childWorkflows` map, split save
   and dirty tracking. (1–2 days)
6. **Drag-in / drag-out** — node boundary detection on drag stop. (1 day)
7. **Cross-boundary edge rejection** — extend connection validation. (half day)
8. **Delete lifecycle** — modal with delete/keep options, API calls. (half day)
9. **Duplicate and export/import** — API-level changes plus frontend coordination.
   (1 day)
10. **Publish hook** — auto-publish child + version snapshot. (1 day)
11. **Run UI** — progress, child run count, error link. (1 day)
12. **Migration action** — `map_items` → Map Group converter in inspector. (1 day)
13. **Reference mode** — workflow picker in header, mode toggle. (1 day)
14. **Collapse for large bodies** — performance guard at 30+ nodes. (half day)

Total: approximately 12–15 engineering days for a production-ready feature.
