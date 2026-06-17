# Metanode Drill-In Editing + Undo Edge-Restore — Design

**Date:** 2026-06-17
**Status:** approved (design); ready for implementation plan.
**Supersedes (UI portion):** the read-only drill-in preview from
`docs/superpowers/specs/2026-06-07-metanodes-design.md` §5 / §9.4
(the "Remaining (optional, larger)" in-place editing item).

---

## 0. Summary

Two independent deliverables, shippable in order:

1. **Undo edge-restore fix** (small, independent). Deleting a node currently
   leaves a single Ctrl+Z restoring the node but not its edges. Fix the
   double-history-commit in the React Flow change handlers.
2. **Editable metanode drill-in** (large). Replace the read-only boxed
   `MetanodePreview` with a full-canvas, fully-editable drill-in: real
   `NodeCard`s, KNIME-style input/output **bars** on the left/right with the
   boundary ports, breadcrumb navigation, **add/remove ports from inside**, and
   **nested** metanode editing. No backend changes — the persisted
   `params.subgraph` / `params.ports` shape is unchanged (verified against
   `packages/core/noodle/engine/metanodes.py`).

Both are frontend-only (`apps/web`). The engine already inlines/executes the
data shape we produce.

---

## 1. Deliverable 1 — Undo restores nodes *and* edges

### 1.1 Root cause

`apps/web/src/editor/store/index.ts` commits an undo snapshot inside both
`onNodesChange` and `onEdgesChange` (each does
`_past: [...state._past, { nodes, edges }]`). When React Flow deletes a node it
dispatches the node-removal and the connected-edge-removals as **two separate
handler calls in the same tick**:

- `onEdgesChange(remove edges)` fires first → snapshots the **pristine**
  `{nodes, edges}`, then removes the edges.
- `onNodesChange(remove node)` fires second → snapshots
  `{nodes, edges-already-removed}`, then removes the node.

`_past` now ends `[…, S_pristine, S_edgeless]`. One undo restores
`S_edgeless` → the node returns, the edges do not. (Symmetric if the order is
reversed; the *second* commit is always the lossy one.)

`deleteNode` / `deleteSelection` (the store actions used by the context menu and
the NodeCard delete button) are **not** affected — they remove nodes+edges in a
single `set` with one snapshot. The bug is specific to React Flow's built-in
delete path (Delete/Backspace key) that splits across the two change handlers.

### 1.2 Fix — coalesce one gesture into one history commit

Add a module-level tick guard used **only** by `onNodesChange` and
`onEdgesChange`:

```ts
// First commit within a synchronous tick captures the pristine pre-change
// state; later commits in the same tick (e.g. React Flow splitting a delete
// across onNodesChange + onEdgesChange) reuse it instead of pushing a second,
// half-mutated snapshot. Reset on the next microtask so distinct user gestures
// (always separated by an event-loop turn) each get their own history entry.
let _historyTickOpen = false;
function openHistoryTick(): boolean {
  if (_historyTickOpen) return false;
  _historyTickOpen = true;
  queueMicrotask(() => { _historyTickOpen = false; });
  return true;
}
```

In each of `onNodesChange` / `onEdgesChange`:

```ts
const commit = shouldCommitChanges(parentChanges);
const pushHistory = commit && openHistoryTick();   // short-circuits: guard only
                                                   // consumed when committing
set({
  …,
  ...(pushHistory
    ? { _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT), _future: [] }
    : {}),
});
```

Whichever handler fires first in the tick pushes the pristine snapshot; the
second sets its slice but does not push. `_future` is reset only by the pushing
handler (already `[]` for the second). Order-independent.

### 1.3 Edge cases (Deliverable 1)

- **Edge-only delete / node-only (no edges) delete:** one handler call → one
  commit. Unchanged.
- **Drag-drop position commit:** `dragging===false` commits; single
  `onNodesChange` call → one commit. Unchanged.
- **Drill-in:** undo/redo inside a metanode operate on that level's live
  `_past`/`_future` (see §2.4) — the same code path, so the fix applies at every
  depth.
- **Test determinism:** the guard is process-global, reset via `queueMicrotask`.
  Vitest awaits between `it` blocks, draining microtasks, so the guard is `false`
  at the start of each test. Within one synchronous test the guard correctly
  coalesces the simulated two-call delete.
- **No effect on explicit actions:** `deleteNode`, `cutSelection`,
  `pasteSelection`, `addNode`, `onConnect`, etc. keep their own single-snapshot
  commit and never call `openHistoryTick`.

### 1.4 Tests (Deliverable 1)

`store.undo.test.ts` (new):
1. Load `A→B→C→D`. Simulate RF delete of `B`: `onEdgesChange([remove A→B,
   remove B→C])` then `onNodesChange([remove B])`. Assert state has `A,C,D` +
   edge `C→D`. One `undo()` ⇒ all of `A,B,C,D` and edges `A→B,B→C,C→D` restored.
2. Reverse the call order (node first) ⇒ same single-undo restoration.
3. Edge-only delete then undo restores just that edge (no over-restore).
4. `redo()` after the single undo re-applies the full delete.

---

## 2. Deliverable 2 — Editable metanode drill-in

### 2.1 Goals / non-goals

**Goals:** double-click a metanode (any depth) → the whole editor canvas becomes
that metanode's interior; real `NodeCard`s; left **input bar** + right **output
bar** carrying the boundary ports; add/move/delete/rewire internal nodes;
add/remove boundary ports from inside; nested drill-in with a clickable
breadcrumb; round-trip-safe persistence; autosave/publish keep saving the real
root workflow throughout.

**Non-goals (explicitly out of scope, no follow-up expected for this work):**
port **renaming** (ports are identified by stable id, auto-labelled); shareable/
library metanodes; changing the runtime (engine already supports the shape);
step-running an individual node from inside a metanode (see §2.9).

### 2.2 Architecture — drill stack + live-graph swap

A new store slice `drillSlice` adds:

```ts
interface DrillFrame {
  metaId: string;
  nodes: NoodleNode[];
  edges: Edge[];
  _past: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  _future: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
}
drillStack: DrillFrame[];          // [] = at root
drillPath: { metaId: string; name: string }[];  // derived view for breadcrumb
enterMetanode(metaId: string): void;
exitMetanode(): void;              // pop one level
exitToDepth(depth: number): void;  // breadcrumb jump (0 = root)
```

`enterMetanode(metaId)`:
1. Resolve `meta` in the live `nodes`; guard `meta.data.manifest.id === "meta_node"`.
2. Push `{ metaId, nodes, edges, _past, _future }` onto `drillStack`.
3. Materialize the subgraph (§2.6) into `NoodleNode[]` + `Edge[]`, append the two
   `metaBar` nodes (§2.5) and the boundary proxy edges.
4. `set({ nodes, edges, _past: [], _future: [], selectedId: null })`. **Not**
   dirty — navigation is not an edit.
5. `fitView` to frame the interior.

`exitMetanode()`:
1. Fold the live graph back into `meta.params` (§2.7): derive `subgraph`
   (internal nodes/edges, bars and proxy edges excluded) and `ports` (from bar
   wiring), preserving stable port ids.
2. Pop the frame. Restore `nodes`/`edges`/`_past`/`_future` from the frame, with
   the metanode node replaced by the updated one (new `params.subgraph`,
   `params.ports`, and a rebuilt synthetic `manifest` whose `inputs`/`outputs`
   match the new port ids).
3. **Reconcile parent edges** for the metanode (§2.8).
4. `dirty: true` only if the fold actually changed the metanode (deep-equal
   compare of subgraph+ports); otherwise leave dirty untouched.

`exitToDepth(d)` repeatedly folds+pops (deepest first) until
`drillStack.length === d`. The breadcrumb root segment calls `exitToDepth(0)`.

All other editor actions are reused **unchanged** because they operate on the
live `nodes`/`edges`. This is the core reason for "the same nodes and NDV": it
is literally the same canvas.

### 2.3 Autosave / serialization safety (the critical invariant)

Autosave, publish, and AI-fix all serialize via `toGraph()` and persist the
result as the whole workflow (`apps/web/src/EditorPage.tsx`). With a live-graph
swap, the live `nodes`/`edges` are an *interior*, not the workflow. Therefore:

**`toGraph()` becomes drill-aware.** When `drillStack` is non-empty it folds the
live interior up the stack to reconstruct the **root** workflow:

```
materializeRoot(state):
  working = { nodes: liveNodesMinusBars, edges: liveEdgesMinusProxies }
  ports   = derivePortsFromBars(live)
  for frame in reverse(drillStack):        # deepest parent → root
     metaNode = frame.nodes.find(metaId)   # update its params
     metaNode.params.subgraph = serialize(working)
     metaNode.params.ports    = ports
     metaNode.manifest        = rebuildMetaManifest(ports)
     working = { nodes: frame.nodes(with metaNode updated), edges: frame.edges }
     ports   = <the frame's own bar-derived ports if that frame is itself an
                interior; for the outermost frame there are no bars>
  return serialize(working.root)
```

Practical simplification: each `DrillFrame` stores the *parent* graph, which for
non-root frames is itself a previously-swapped interior **including its own bar
nodes**. So the fold walks frames deepest-first, folding each level's working
graph into its `metaId` node and using the *next* frame's bars to derive that
next level's ports. The root frame (`drillStack[0]`) has no bars.

Consequences:
- Autosave/publish/AI-fix need **no changes**; they call `toGraph()` which now
  always returns the correct root. The `dirty` flag (already set by interior
  edits) drives autosave as usual.
- Navigating away mid-drill loses nothing already autosaved; an undebounced
  in-flight edit is the same pre-existing autosave caveat as at root.
- On a fresh editor mount `drillStack` is `[]`; `loadGraph` resets it (§2.10),
  so persisted workflows always open at root.

### 2.4 History per level

Each `DrillFrame` carries its own `_past`/`_future`. `enterMetanode` resets them
to `[]`; `exitMetanode` restores the parent's. Undo/redo (and the §1 coalescing)
work per level via the live history. `enter`/`exit`/`exitToDepth` are
**navigation, not undoable steps**.

### 2.5 Boundary bars (`metaBar` node type)

Two non-graph nodes added to the live graph while drilled:

- **Input bar** — pinned to the left of the interior bounding box; one **source**
  handle per `ports.inputs[]` (vertically distributed), labelled by port id; plus
  one **"＋ add input"** stub handle.
- **Output bar** — pinned to the right; one **target** handle per
  `ports.outputs[]`; plus a **"＋ add output"** stub.

Node `data` carries the ordered port descriptors so the component renders labels
and handle ids. Bars are: `draggable:false`, `deletable:false`, not selectable
for grouping, excluded from copy/paste, auto-layout, loop-frame computation,
`toGraph`/fold, and the empty-canvas onboarding count. They pan/zoom with the
canvas as ordinary positioned nodes (KNIME model) — no viewport-fixed handles.

Positions: on `enterMetanode`, compute the interior bounding box; place input bar
at `minX - GAP`, output bar at `maxX + GAP`, height spanning the box. Empty
interior ⇒ default span.

Centralize the identification predicate `isMetaBar(node)` (mirrors the existing
`LOOP_FRAME_ID_PREFIX` filtering) and apply it everywhere bars must be excluded.

### 2.6 Materializing the subgraph (round-trip-safe)

`subgraph.nodes` → `NoodleNode[]` via the existing `graphNodeToNode`, **extended**
to handle `type === "meta_node"` by rebuilding the synthetic manifest from the
child's own `params.ports` (the same logic `loadGraph` uses). This makes nested
metanodes render as `NodeCard`s and be enter-able.

**Unknown node types** (a `type` not in `manifestsById` and not `meta_node`):
`graphNodeToNode` returns `null` today, which would silently **drop** the node on
a drill round-trip. To prevent data loss:
- `enterMetanode` materializes each unknown node as a **read-only placeholder
  `NoodleNode`** (synthetic minimal manifest, no editable params) so it renders
  as a neutral "unavailable node" tile and keeps its canvas position, and records
  the original `subgraph.nodes`/`edges` on the `DrillFrame` (`origSubNodes`).
- The fold (§2.7) serializes each placeholder back from its **carried-forward
  original** (`type`/`params` verbatim), only adopting the placeholder's updated
  `position`. Unknown nodes therefore survive enter→exit byte-identical except for
  position, are never re-wired or dropped, and edges touching them round-trip via
  the normal internal-edge path.

### 2.7 Folding the interior back (`exitMetanode` / `toGraph`)

Given the live graph (interior):
- **internal nodes** = live nodes minus bars. Real nodes serialize via the
  existing `nodeToGraphNode`; placeholder (unknown-type) nodes serialize from
  their carried-forward original with only `position` updated (§2.6).
- **internal edges** = live edges where both endpoints are internal nodes (bars
  excluded) → `edgeToGraphEdge`.
- **ports.inputs** = for each input-bar handle (port id `p`): collect proxy edges
  `source === inputBarId && sourceHandle === p`; `targets = [{target,
  target_input}]`. Drop ports with no remaining bar handle.
- **ports.outputs** = for each output-bar handle (port id `p`): the single proxy
  edge `target === outputBarId && targetHandle === p` →
  `{ port: p, source, source_output }`. Output-bar handles accept exactly one
  incoming wire (enforced via the existing single-target `keepEdgesForConnection`
  semantics), so this is unambiguous.

Port **ordering** follows the bar's `data` order (insertion order). Port **ids
are never renumbered** — see §2.8.

### 2.8 Port lifecycle + parent-edge reconciliation

**Stable ids.** Port ids (`in_<n>` / `out_<n>`) are allocated from a persisted
monotonic counter `params._portSeq` (initialized from the existing max on first
edit) so a removed-then-re-added port never reuses an id and never collides with
a parent edge that referenced a since-removed port. Ids are stable across edit
sessions; existing parent edges keep pointing at the same internal mapping.

**Adding a port (from inside).** Dragging from a bar's "＋ add" stub to an
internal node:
- allocate `id = "in_"+(++_portSeq)` (or `out_`), append to the bar's `data`,
  create the proxy edge. On exit the new port appears in `params.ports`; the
  rebuilt synthetic manifest gains the handle; **no parent edge is created** — it
  surfaces as an **unconnected port** on the metanode in the parent canvas, ready
  to wire. (Input add: stub → internal target. Output add: internal source →
  stub.)

**Removing a port (from inside).** Deleting a bar handle (or all its proxy
wires) removes the port. On exit:
- the port is absent from `params.ports` and the rebuilt manifest;
- **parent edges referencing the removed port are dropped** during reconciliation
  (§ below). Internal-only proxy wires simply aren't serialized.

**Reconciliation on `exitMetanode`** (parent graph, for `metaId`):
```
validInputPorts  = new Set(newPorts.inputs.map(p => p.port))
validOutputPorts = new Set(newPorts.outputs.map(p => p.port))
parentEdges = parentEdges.filter(e =>
  !(e.target === metaId && !validInputPorts.has(e.targetHandle)) &&
  !(e.source === metaId && !validOutputPorts.has(e.sourceHandle)))
```
Ports that still exist keep their parent edges (ids unchanged). Added ports have
none yet. Removed ports' parent edges are pruned. This keeps the parent acyclic
guarantee intact (removing edges can't create a cycle; added ports are
unconnected).

**Degenerate cases (allowed, not errors):**
- Input port with **empty** `targets` (internal wire deleted) — kept; at runtime
  the fed value is simply unused (matches engine `port_in` empty list).
- Output port with **no** internal source — the engine yields `None` for it
  (`run is None`); kept. A subtle visual "unmapped" hint may be shown but is not
  a blocker.
- Empty interior (all internal nodes deleted) — allowed; metanode becomes a
  near-passthrough; ports persist per their bar handles.

### 2.9 In-canvas behavior while drilled

Reused unchanged: quick-add, drag-from-palette, copy/cut/paste, NDV
(double-click a non-meta node opens `NodeDetailModal` against the live node —
works because the node *is* in the live `nodes`), context menu, auto-layout,
minimap, connection validation/health, dataset banners, **collapse selection →
nested metanode** (creates a metanode inside the interior — a free bonus from
reuse), loop frames for internal loops.

Gated/changed while `drillStack.length > 0`:
- **Empty-canvas onboarding** (`nodes.length === 0` starter grid) is suppressed
  (an empty interior is normal).
- **Per-node step-run** affordances on `NodeCard` are hidden (interior node ids
  are namespaced at runtime and aren't independently runnable). NodeCard reads a
  `drilling` flag (store selector `drillStack.length > 0`) to hide the ▶/↻
  toolbar buttons; the "open details (⤢)" and disable/delete buttons remain.
- **Run badges** (`runStatus`, iteration, chunk) are top-level only and naturally
  absent inside.
- Bars are excluded from selection-driven grouping and from
  `selectedMetanodeCandidates`.

`isValidConnection` already unions child-workflow nodes; extend it to include the
live interior context so bar↔internal wires validate. Bar ports are kind `any`
(wildcard) so they connect to any internal handle.

### 2.10 Reset / lifecycle integration

- `loadGraph` (switch workflow / initial load) sets `drillStack: []`,
  `drillPath: []` alongside its existing `_past/_future/childWorkflows` reset.
- The breadcrumb component lives in `Canvas` (replacing the `metaPreviewId`
  state + `MetanodePreview`). `Escape` calls `exitMetanode()` (one level); a
  breadcrumb segment calls `exitToDepth(i)`.
- `onNodeDoubleClick`: if the node is `meta_node` → `enterMetanode(id)` (instead
  of opening the old preview); else open NDV as today.
- **Remove** `MetanodePreview.tsx` and its imports/usages (verify no other
  references).

### 2.11 UI / component inventory

- `editor/MetaBar.tsx` (new) — renders one bar (input|output) with labelled
  handles + add-port stub. Styled container, dark-canvas friendly.
- `editor/MetanodeBreadcrumb.tsx` (new) — `Workflow › A › B`, clickable segments,
  "Ungroup"/back affordance. Replaces the boxed `meta-preview` header.
- `editor/store/drillSlice.ts` (new) — `drillStack`, `enterMetanode`,
  `exitMetanode`, `exitToDepth`, materialize/fold/reconcile helpers, `isMetaBar`,
  stable-port-id allocation.
- `Canvas.tsx` — register `metaBar` node type; render breadcrumb when drilled;
  reroute double-click; suppress onboarding when drilled; exclude bars in
  `handleNodesChange`, selection, grouping.
- `store/index.ts` — extend `graphNodeToNode` (meta_node + unknown carry),
  drill-aware `toGraph`, reset in `loadGraph`, the §1 history coalescing.
- `NodeCard.tsx` — `drilling` flag to hide step-run buttons; "unavailable node"
  placeholder branch for unknown types.

### 2.12 Edge-case checklist (must all be covered by tests or explicit handling)

1. Enter→exit with no edits ⇒ byte-identical `params.subgraph`/`ports`; `dirty`
   unchanged.
2. Enter→exit round-trips a known graph (subgraph + ports equal).
3. Nested: enter A → enter B → edit → exit → exit folds correctly at both levels;
   `toGraph()` mid-nest returns the correct root.
4. Add input port inside → exit ⇒ new port on parent metanode, unconnected; wire
   it on the parent; re-enter shows the proxy.
5. Remove a port that had a parent edge ⇒ that parent edge is pruned on exit; no
   dangling handle.
6. Stable ids: add port, remove a different port, exit, re-enter ⇒ surviving
   ports keep ids and parent edges; no id reuse/collision.
7. Unknown node type in subgraph survives enter→exit (carry-forward), never
   re-wired or dropped.
8. Fan-out input port (one bar handle → many internal targets) round-trips.
9. Output-bar handle rejects a second incoming wire (single source per output).
10. Delete all internal nodes inside ⇒ empty subgraph persists; ports remain.
11. `loadGraph` while drilled (e.g., workflow switch) resets the stack → at root.
12. Collapse a selection *inside* a metanode ⇒ nested metanode created; bars
    excluded from the selection.
13. Bars never serialized: `toGraph()` and the fold contain no `metaBar` nodes /
    proxy-only edges as workflow edges.
14. Undo inside a metanode (incl. the §1 split-delete) restores nodes+edges at
    that level; navigation isn't on the undo stack.
15. Autosave while drilled persists the root with the in-progress interior folded
    into the metanode (no interior-as-workflow corruption).
16. Escape / breadcrumb-root exits all levels, folding each.
17. Ungroup of a metanode from the parent still works and equals the round-trip
    inverse (existing `store.metanodes.test.ts` stays green).

### 2.13 Tests (Deliverable 2)

**Store (vitest, `store.drill.test.ts` new):** edge cases 1–6, 8, 10–11, 14–15,
plus extend `store.metanodes.test.ts` to assert ungroup still round-trips after
the `graphNodeToNode` extension. Cover `toGraph()` drill-aware folding at depth
1 and 2.

**Component (RTL):** bar renders one handle per port + add stub; double-click a
meta node enters; breadcrumb segments pop to depth; NodeCard hides step-run when
`drilling`; unavailable-node placeholder renders for unknown type.

**Regression:** full `apps/web` `tsc`/lint + existing editor tests green.

---

## 3. Phasing (implementation order)

1. **Deliverable 1** — undo coalescing + `store.undo.test.ts`. Independently
   landable.
2. **Drill scaffolding** — `drillSlice` enter/exit/stack, `graphNodeToNode`
   extension, drill-aware `toGraph`, `loadGraph` reset; no bars yet (interior
   renders as plain nodes). Store tests for round-trip + nesting + autosave fold.
3. **Bars + port wiring** — `metaBar` type, `MetaBar.tsx`, materialize/fold of
   proxy edges, single-source output enforcement.
4. **Port lifecycle** — add/remove from inside, stable ids, parent-edge
   reconciliation, edge-case tests 4–9.
5. **Navigation UX** — `MetanodeBreadcrumb`, double-click reroute, Escape,
   onboarding suppression, NodeCard `drilling` gating; remove `MetanodePreview`.
6. **Hardening** — unknown-node carry-forward, degenerate ports, full edge-case
   sweep + `tsc`/lint.

---

## 4. Risks

- **`toGraph()` fold correctness** is the linchpin (autosave integrity). Mitigated
  by dedicated store tests asserting the folded root at depths 1–2 and a
  "no-edit round-trip is identical" test.
- **Bar exclusion completeness** — a missed exclusion site leaks a bar into the
  saved graph. Mitigated by a single `isMetaBar` predicate, a `toGraph` test
  asserting zero `metaBar` nodes, and a grep audit of every nodes/edges consumer.
- **Unknown-node data loss** — mitigated by carry-forward + an explicit test.
