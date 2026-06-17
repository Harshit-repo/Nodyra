# Metanode Drill-In Editing + Undo Edge-Restore — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the read-only boxed metanode preview with a full-canvas, fully-editable, nested drill-in (real `NodeCard`s, KNIME-style input/output bars, add/remove ports from inside, breadcrumb nav, working step-run), and fix Ctrl+Z so deleting a node restores its edges too.

**Architecture:** A drill stack swaps the live `nodes`/`edges` to a metanode's interior so every existing editor action is reused unchanged; `toGraph()` folds the interior back up the stack so autosave always persists the real root. Boundary "bars" are special non-graph nodes whose wiring defines `params.ports`. Step-run inside transparent metanodes targets the runtime-namespaced id.

**Tech Stack:** TypeScript, React, `@xyflow/react` (React Flow), Zustand, Vitest, React Testing Library. All changes are in `apps/web`. No backend changes.

**Spec:** `docs/superpowers/specs/2026-06-17-metanode-drill-in-editing-design.md`

**Run tests from `apps/web`:** `npm run test -- <file>` (Vitest). Type-check: `npm run build` or `npx tsc -p tsconfig.json --noEmit`.

---

## File Structure

- **Create** `apps/web/src/editor/store/drillSlice.ts` — drill state types, initial state, constants (`META_BAR_INPUT_ID`, `META_BAR_OUTPUT_ID`, `META_BAR_ADD`), trivial pure helpers (`isMetaBar`, `drillPrefix`, `maxPortSuffix`, `MetaPortDescriptor`, `MetaPorts`).
- **Modify** `apps/web/src/editor/store/index.ts` — undo coalescing; extend `graphNodeToNode` (meta_node + unknown placeholder); drill actions (`enterMetanode`/`exitMetanode`/`exitToDepth`/`addMetaPort`/`removeMetaPort`) + module helpers (`materializeInterior`/`foldInterior`/`reconcileParentEdges`/`updateMetaNode`); drill-aware `toGraph`; bar-aware `onConnect`; `loadGraph` reset; run-prefix targeting + `runKeyFor`.
- **Create** `apps/web/src/editor/MetaBar.tsx` — renders one input/output bar node (labelled handles, per-port remove, add-port button).
- **Create** `apps/web/src/editor/MetanodeBreadcrumb.tsx` — clickable `Workflow › A › B` breadcrumb.
- **Modify** `apps/web/src/editor/Canvas.tsx` — register `metaBar` node type; double-click enters metanode; render breadcrumb; suppress onboarding while drilled; exclude bars from selection/grouping/change filter; bar-aware double-click guard.
- **Modify** `apps/web/src/editor/NodeCard.tsx` — read run state via `runKeyFor(id)`; transparent-vs-isolated step-run gating; unavailable-node placeholder branch.
- **Modify** `apps/web/src/editor/editor.css` — bar + breadcrumb styles; remove `.meta-preview*` rules.
- **Delete** `apps/web/src/editor/MetanodePreview.tsx` (after Canvas no longer imports it).
- **Create tests** `store.undo.test.ts`, `store.drill.test.ts`, `store.drillRun.test.ts`, `MetaBar.test.tsx`, `MetanodeBreadcrumb.test.tsx`; **extend** `store.metanodes.test.ts`.

---

## PHASE 1 — Undo restores nodes and edges

### Task 1: Coalesce one delete gesture into one history commit

**Files:**
- Test: `apps/web/src/editor/store.undo.test.ts` (create)
- Modify: `apps/web/src/editor/store/index.ts` (add `openHistoryTick`; edit `onNodesChange` ~line 1098-1135 and `onEdgesChange` ~line 1137-1174)

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/editor/store.undo.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}
function codeManifest(): NodeManifest {
  return {
    id: "code", name: "Code", category: "Core", version: "1", description: "",
    icon: null, inputs: [port("input")], outputs: [port("main")], params: [],
  };
}
function chainGraph(): WorkflowGraph {
  const mk = (id: string) => ({
    id, type: "code", params: {}, position: { x: 0, y: 0 },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
    always_output_data: false, timeout_seconds: null,
  });
  const ed = (s: string, t: string) => ({
    id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
  });
  return { nodes: [mk("A"), mk("B"), mk("C")], edges: [ed("A", "B"), ed("B", "C")] };
}
function load(graph: WorkflowGraph) {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([codeManifest()]);
  useEditor.getState().loadGraph(graph);
}

describe("undo restores nodes and their edges together", () => {
  it("single undo brings back a deleted node AND its edges (edges removed first)", () => {
    load(chainGraph());
    // React Flow splits a node delete into two same-tick change calls.
    useEditor.getState().onEdgesChange([
      { type: "remove", id: "A->B" },
      { type: "remove", id: "B->C" },
    ]);
    useEditor.getState().onNodesChange([{ type: "remove", id: "B" }]);

    let s = useEditor.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["A", "C"]);
    expect(s.edges.map((e) => e.id).sort()).toEqual([]);

    useEditor.getState().undo();
    s = useEditor.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["A", "B", "C"]);
    expect(s.edges.map((e) => e.id).sort()).toEqual(["A->B", "B->C"]);
  });

  it("works when the node removal arrives before the edge removals", () => {
    load(chainGraph());
    useEditor.getState().onNodesChange([{ type: "remove", id: "B" }]);
    useEditor.getState().onEdgesChange([
      { type: "remove", id: "A->B" },
      { type: "remove", id: "B->C" },
    ]);
    useEditor.getState().undo();
    const s = useEditor.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["A", "B", "C"]);
    expect(s.edges.map((e) => e.id).sort()).toEqual(["A->B", "B->C"]);
  });

  it("an edge-only delete is restored by one undo without over-restoring", () => {
    load(chainGraph());
    useEditor.getState().onEdgesChange([{ type: "remove", id: "A->B" }]);
    expect(useEditor.getState().edges.map((e) => e.id).sort()).toEqual(["B->C"]);
    useEditor.getState().undo();
    expect(useEditor.getState().edges.map((e) => e.id).sort()).toEqual(["A->B", "B->C"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- store.undo.test.ts`
Expected: the first two tests FAIL — after one `undo()` the nodes return but `edges` is still `[]` (only the node snapshot was restored).

- [ ] **Step 3: Add the tick guard**

In `apps/web/src/editor/store/index.ts`, immediately after the `shouldCommitChanges` function (~line 847), add:

```ts
// Coalesce the history commit when one user gesture is split across separate
// change handlers in the same tick (React Flow deletes a node by emitting the
// node-removal and its connected-edge-removals as two calls). The first commit
// in a tick captures the pristine pre-change state; later commits in the same
// tick reuse it instead of pushing a second, half-mutated snapshot. Reset on the
// next microtask so distinct user gestures each get their own history entry.
let _historyTickOpen = false;
function openHistoryTick(): boolean {
  if (_historyTickOpen) return false;
  _historyTickOpen = true;
  queueMicrotask(() => {
    _historyTickOpen = false;
  });
  return true;
}
```

- [ ] **Step 4: Use the guard in `onNodesChange`**

In `onNodesChange`, find:

```ts
    const structural = parentChanges.some((c) => STRUCTURAL.has(c.type));
    const commit = shouldCommitChanges(parentChanges as Array<{ type: string; dragging?: boolean }>);
```

Add directly after it:

```ts
    const pushHistory = commit && openHistoryTick();
```

Then in that function's `set({ ... })`, replace:

```ts
      ...(commit
        ? {
            _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
            _future: [],
          }
        : {}),
```

with:

```ts
      ...(pushHistory
        ? {
            _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
            _future: [],
          }
        : {}),
```

- [ ] **Step 5: Use the guard in `onEdgesChange`**

Apply the identical change in `onEdgesChange`: add `const pushHistory = commit && openHistoryTick();` after its `commit` line, and swap its `...(commit ? {...} : {})` for `...(pushHistory ? {...} : {})`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm run test -- store.undo.test.ts`
Expected: PASS (all three).

- [ ] **Step 7: Run the existing store tests to confirm no regression**

Run: `npm run test -- store.clipboard.test.ts store.metanodes.test.ts store.datasetref.test.ts`
Expected: PASS (the `_past).toHaveLength(1)` assertions still hold — explicit actions are untouched).

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/editor/store/index.ts apps/web/src/editor/store.undo.test.ts
git commit -m "fix(editor): undo restores deleted node's edges (coalesce split-delete history)"
```

---

## PHASE 2 — Drill scaffolding (no bars yet)

### Task 2: Drill slice state + constants + trivial helpers

**Files:**
- Create: `apps/web/src/editor/store/drillSlice.ts`

- [ ] **Step 1: Create the slice file**

```ts
import type { Edge } from "@xyflow/react";

import type { NoodleNode } from "./index";

export const META_BAR_INPUT_ID = "__meta_input_bar__";
export const META_BAR_OUTPUT_ID = "__meta_output_bar__";

/** A boundary port shown on a bar: stable id + display label. */
export interface MetaPortDescriptor {
  id: string;
  label: string;
}

/** Persisted boundary mapping on a metanode's `params.ports`. */
export interface MetaPorts {
  inputs: { port: string; targets: { target: string; target_input: string }[] }[];
  outputs: { port: string; source: string; source_output: string }[];
}

/** A suspended parent level while the user edits a nested interior. */
export interface DrillFrame {
  metaId: string;
  name: string;
  nodes: NoodleNode[];
  edges: Edge[];
  _past: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  _future: Array<{ nodes: NoodleNode[]; edges: Edge[] }>;
  /** origSubNodes of the PARENT level being suspended (for unknown carry-forward). */
  orig: Record<string, GraphNodeShape>;
  /** next-port-seq of the PARENT level. */
  portSeq: number;
}

/** Minimal shape of a stored sub-graph node (mirrors GraphNodeLike in index.ts). */
export interface GraphNodeShape {
  id: string;
  type: string;
  params: Record<string, unknown>;
  position: { x: number; y: number };
  [key: string]: unknown;
}

export interface DrillSliceState {
  drillStack: DrillFrame[];
  drillOrig: Record<string, GraphNodeShape>;
  drillPortSeq: number;
}

export const drillInitialState: DrillSliceState = {
  drillStack: [],
  drillOrig: {},
  drillPortSeq: 1,
};

export function isMetaBar(node: { id?: string; type?: string } | null | undefined): boolean {
  return (
    !!node &&
    (node.type === "metaBar" ||
      node.id === META_BAR_INPUT_ID ||
      node.id === META_BAR_OUTPUT_ID)
  );
}

/** Runtime id prefix for the current interior, e.g. path [A,B] -> "A/B/". */
export function drillPrefix(stack: DrillFrame[]): string {
  return stack.length === 0 ? "" : stack.map((f) => f.metaId).join("/") + "/";
}

/** 1 + the highest numeric suffix across all existing port ids (in_3 -> 3). */
export function maxPortSuffix(ports: MetaPorts | undefined): number {
  let max = 0;
  for (const p of ports?.inputs ?? []) {
    const n = Number(/(\d+)$/.exec(p.port)?.[1] ?? -1);
    if (n > max) max = n;
  }
  for (const p of ports?.outputs ?? []) {
    const n = Number(/(\d+)$/.exec(p.port)?.[1] ?? -1);
    if (n > max) max = n;
  }
  return max;
}
```

- [ ] **Step 2: Type-check**

Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: PASS (file is self-contained; `NoodleNode` is a type-only import).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/editor/store/drillSlice.ts
git commit -m "feat(editor): drill slice state, constants and pure helpers"
```

---

### Task 3: Extend `graphNodeToNode` (nested metanodes + unknown placeholders)

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (`graphNodeToNode` ~line 215-243)
- Test: extend `apps/web/src/editor/store.metanodes.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `apps/web/src/editor/store.metanodes.test.ts` (inside the existing `describe`):

```ts
  it("round-trips ungroup of a metanode whose subgraph contains a nested metanode", () => {
    load(chainGraph());
    // collapse B,C -> meta1; then collapse meta1 + D would be odd, so:
    const meta1 = useEditor.getState().collapseToMetanode(["B", "C"])!;
    // collapse meta1 + A into meta2 so meta2's subgraph contains meta1 (a meta_node)
    const meta2 = useEditor.getState().collapseToMetanode([meta1, "A"])!;
    expect(meta2).toBeTruthy();
    // ungroup meta2 — the nested meta1 must be restored as a meta_node, not dropped
    useEditor.getState().ungroupMetanode(meta2);
    const ids = useEditor.getState().nodes.map((n) => n.id);
    expect(ids).toContain(meta1);
    const restored = useEditor.getState().nodes.find((n) => n.id === meta1)!;
    expect(restored.data.manifest.id).toBe("meta_node");
  });
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- store.metanodes.test.ts`
Expected: FAIL — `graphNodeToNode` returns `null` for `type === "meta_node"` (no registered manifest), so the nested metanode is dropped on ungroup.

- [ ] **Step 3: Implement the extension**

In `apps/web/src/editor/store/index.ts`, replace the body of `graphNodeToNode` up to the `const manifest` resolution. Replace:

```ts
function graphNodeToNode(
  gn: GraphNodeLike,
  byId: Record<string, NodeManifest>,
): NoodleNode | null {
  const manifest = byId[gn.type];
  if (!manifest) return null;
  return {
    id: gn.id,
    type: gn.type === "map_group" ? "mapGroup" : "noodle",
```

with:

```ts
function placeholderManifest(typeId: string): NodeManifest {
  return {
    id: typeId,
    name: typeId,
    category: "Unavailable",
    version: "0",
    description: "This node type isn't available in this editor build.",
    icon: "warning",
    inputs: [{ name: "input", description: "", data_kind: "any" }],
    outputs: [{ name: "main", description: "", data_kind: "any" }],
    params: [],
  } as NodeManifest;
}

function graphNodeToNode(
  gn: GraphNodeLike,
  byId: Record<string, NodeManifest>,
): NoodleNode | null {
  let manifest = byId[gn.type];
  let unavailableType: string | undefined;
  if (!manifest && gn.type === "meta_node") {
    const ports = ((gn.params ?? {}).ports ?? {}) as {
      inputs?: { port: string }[];
      outputs?: { port: string }[];
    };
    manifest = buildMetanodeManifest(
      (ports.inputs ?? []).map((p) => p.port),
      (ports.outputs ?? []).map((p) => p.port),
    );
  }
  if (!manifest) {
    manifest = placeholderManifest(gn.type);
    unavailableType = gn.type;
  }
  return {
    id: gn.id,
    type: gn.type === "map_group" ? "mapGroup" : "noodle",
```

Then, in the returned object's `data: { ... }`, add `unavailableType` after `label`:

```ts
      label: gn.label || undefined,
      ...(unavailableType ? { unavailableType } : {}),
    },
  } as NoodleNode;
}
```

(`buildMetanodeManifest` is already defined above `graphNodeToNode` in the same file.)

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- store.metanodes.test.ts`
Expected: PASS (existing tests + the new nested round-trip).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/store/index.ts apps/web/src/editor/store.metanodes.test.ts
git commit -m "feat(editor): graphNodeToNode handles nested metanodes and unknown placeholders"
```

---

### Task 4: Wire drill state into the store + `loadGraph` reset

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (imports; `EditorStore` interface; `create()` spread; `loadGraph` set)

- [ ] **Step 1: Import the slice**

Near the other slice imports (~line 29-32) add:

```ts
import {
  drillInitialState,
  type DrillFrame,
  type DrillSliceState,
  type GraphNodeShape,
  type MetaPorts,
  isMetaBar,
  drillPrefix,
  maxPortSuffix,
  META_BAR_INPUT_ID,
  META_BAR_OUTPUT_ID,
} from "./drillSlice";
```

And re-export the type alongside the others (~line 34-37):

```ts
export type { DrillSliceState } from "./drillSlice";
```

- [ ] **Step 2: Add drill fields + action signatures to `EditorStore`**

In the `EditorStore` interface, after the history block (`_future` / `undo` / `redo`, ~line 778-780) add:

```ts
  // Metanode drill-in editing.
  drillStack: DrillFrame[];
  drillOrig: Record<string, GraphNodeShape>;
  drillPortSeq: number;
  enterMetanode: (metaId: string) => void;
  exitMetanode: () => void;
  exitToDepth: (depth: number) => void;
  addMetaPort: (side: "input" | "output") => void;
  removeMetaPort: (side: "input" | "output", portId: string) => void;
  runKeyFor: (nodeId: string) => string;
  drillStepRunDisabledReason: () => string | null;
```

- [ ] **Step 3: Spread the initial state into `create()`**

In the `create<EditorStore>((set, get) => ({` object, alongside `...graphInitialState, ...runInitialState,` (~line 970-973) add:

```ts
  ...drillInitialState,
```

- [ ] **Step 4: Reset drill state in `loadGraph`**

In `loadGraph`'s final `set({ ... })` (~line 1049-1057), add to the object:

```ts
      drillStack: [],
      drillOrig: {},
      drillPortSeq: 1,
```

- [ ] **Step 5: Type-check (expect failures only for unimplemented actions)**

Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: errors that `enterMetanode`/`exitMetanode`/`exitToDepth`/`addMetaPort`/`removeMetaPort`/`runKeyFor`/`drillStepRunDisabledReason` are missing from the object literal. These are implemented in Tasks 5–9. Do not commit yet.

---

### Task 5: `materializeInterior`, `enterMetanode`, `exitMetanode`, `exitToDepth`

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (add module helpers near the other metanode helpers ~line 295; add actions in `create()` after `ungroupMetanode` ~line 1632)
- Test: `apps/web/src/editor/store.drill.test.ts` (create)

- [ ] **Step 1: Write the failing test (round-trip, no bars asserted yet)**

Create `apps/web/src/editor/store.drill.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";
import { isMetaBar } from "./store/drillSlice";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}
function codeManifest(): NodeManifest {
  return {
    id: "code", name: "Code", category: "Core", version: "1", description: "",
    icon: null, inputs: [port("input")], outputs: [port("main")], params: [],
  };
}
function chainGraph(): WorkflowGraph {
  const mk = (id: string) => ({
    id, type: "code", params: {}, position: { x: 0, y: 0 },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
    always_output_data: false, timeout_seconds: null,
  });
  const ed = (s: string, t: string) => ({
    id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
  });
  return { nodes: [mk("A"), mk("B"), mk("C"), mk("D")], edges: [ed("A", "B"), ed("B", "C"), ed("C", "D")] };
}
function load(graph: WorkflowGraph) {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([codeManifest()]);
  useEditor.getState().loadGraph(graph);
}

describe("metanode drill-in: enter / exit", () => {
  it("enter shows interior nodes + two bars; exit with no edits round-trips", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const before = JSON.stringify(useEditor.getState().toGraph());

    useEditor.getState().enterMetanode(meta);
    const s = useEditor.getState();
    expect(s.drillStack).toHaveLength(1);
    const interior = s.nodes.filter((n) => !isMetaBar(n));
    expect(interior.map((n) => n.id).sort()).toEqual(["B", "C"]);
    expect(s.nodes.filter((n) => isMetaBar(n))).toHaveLength(2);

    useEditor.getState().exitMetanode();
    expect(useEditor.getState().drillStack).toHaveLength(0);
    // metanode params unchanged ⇒ whole workflow identical
    expect(JSON.stringify(useEditor.getState().toGraph())).toEqual(before);
  });

  it("edits inside survive exit (delete C inside, exit, ungroup ⇒ only B left)", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().deleteNode("C");
    useEditor.getState().exitMetanode();
    useEditor.getState().ungroupMetanode(meta);
    const ids = useEditor.getState().nodes.map((n) => n.id).sort();
    expect(ids).toContain("B");
    expect(ids).not.toContain("C");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- store.drill.test.ts`
Expected: FAIL/throw — `enterMetanode` not implemented.

- [ ] **Step 3: Add module helpers**

In `apps/web/src/editor/store/index.ts`, after `buildMetanodeManifest` (~line 294) add:

```ts
function makeBar(
  side: "input" | "output",
  ports: { id: string; label: string }[],
  position: { x: number; y: number },
): NoodleNode {
  return {
    id: side === "input" ? META_BAR_INPUT_ID : META_BAR_OUTPUT_ID,
    type: "metaBar",
    position,
    draggable: false,
    deletable: false,
    selectable: false,
    data: { bar: side, ports },
  } as unknown as NoodleNode;
}

interface InteriorMaterial {
  nodes: NoodleNode[];
  edges: Edge[];
}

/** Build the live interior (real nodes + 2 bars) and proxy edges for a metanode. */
function materializeInterior(
  meta: NoodleNode,
  byId: Record<string, NodeManifest>,
): InteriorMaterial {
  const params = meta.data.params as {
    subgraph?: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] };
    ports?: MetaPorts;
  };
  const sub = params.subgraph ?? { nodes: [], edges: [] };
  const ports = params.ports ?? { inputs: [], outputs: [] };

  const interiorNodes = sub.nodes.map((gn) => graphNodeToNode(gn, byId)!);
  const interiorEdges = sub.edges.map(graphEdgeToEdge);

  const xs = interiorNodes.map((n) => n.position.x);
  const ys = interiorNodes.map((n) => n.position.y);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 200;
  const minY = ys.length ? Math.min(...ys) : 0;
  const GAP = 240;

  const inputBar = makeBar(
    "input",
    ports.inputs.map((p) => ({ id: p.port, label: p.port })),
    { x: minX - GAP, y: minY },
  );
  const outputBar = makeBar(
    "output",
    ports.outputs.map((p) => ({ id: p.port, label: p.port })),
    { x: maxX + GAP, y: minY },
  );

  const proxyEdges: Edge[] = [];
  for (const p of ports.inputs) {
    for (const t of p.targets) {
      proxyEdges.push({
        id: `proxy_in_${p.port}_${t.target}_${t.target_input}`,
        source: META_BAR_INPUT_ID,
        sourceHandle: p.port,
        target: t.target,
        targetHandle: t.target_input,
      } as Edge);
    }
  }
  for (const p of ports.outputs) {
    proxyEdges.push({
      id: `proxy_out_${p.port}_${p.source}_${p.source_output}`,
      source: p.source,
      sourceHandle: p.source_output,
      target: META_BAR_OUTPUT_ID,
      targetHandle: p.port,
    } as Edge);
  }

  return {
    nodes: [inputBar, ...interiorNodes, outputBar],
    edges: [...interiorEdges, ...proxyEdges],
  };
}

function origByIdOf(meta: NoodleNode): Record<string, GraphNodeShape> {
  const sub = (meta.data.params as { subgraph?: { nodes: GraphNodeShape[] } }).subgraph;
  return Object.fromEntries((sub?.nodes ?? []).map((gn) => [gn.id, gn]));
}
```

- [ ] **Step 4: Add the actions**

In `create()`, immediately after the `ungroupMetanode` action (~line 1632) add:

```ts
  enterMetanode: (metaId) => {
    const state = get();
    const meta = state.nodes.find((n) => n.id === metaId);
    if (!meta || meta.data.manifest.id !== "meta_node") return;
    const material = materializeInterior(meta, state.manifestsById);
    const ports = (meta.data.params as { ports?: MetaPorts }).ports ?? { inputs: [], outputs: [] };
    set({
      drillStack: [
        ...state.drillStack,
        {
          metaId,
          name: String((meta.data.params as { name?: string }).name || "Metanode"),
          nodes: state.nodes,
          edges: state.edges,
          _past: state._past,
          _future: state._future,
          orig: state.drillOrig,
          portSeq: state.drillPortSeq,
        },
      ],
      drillOrig: origByIdOf(meta),
      drillPortSeq: maxPortSuffix(ports) + 1,
      nodes: material.nodes,
      edges: material.edges,
      _past: [],
      _future: [],
      selectedId: null,
      ndvOpenId: null,
    });
  },

  exitMetanode: () => {
    const state = get();
    if (state.drillStack.length === 0) return;
    const frame = state.drillStack[state.drillStack.length - 1];
    const meta = frame.nodes.find((n) => n.id === frame.metaId);
    if (!meta) {
      // Defensive: parent lost the metanode somehow — just pop.
      set({
        nodes: frame.nodes, edges: frame.edges, _past: frame._past, _future: frame._future,
        drillStack: state.drillStack.slice(0, -1), drillOrig: frame.orig, drillPortSeq: frame.portSeq,
      });
      return;
    }
    const { subgraph, ports } = foldInterior(state.nodes, state.edges, state.drillOrig);
    const prev = meta.data.params as { subgraph?: unknown; ports?: unknown };
    const changed =
      JSON.stringify(prev.subgraph ?? null) !== JSON.stringify(subgraph) ||
      JSON.stringify(prev.ports ?? null) !== JSON.stringify(ports);
    const updatedMeta = updateMetaNode(meta, subgraph, ports);
    const parentNodes = frame.nodes.map((n) => (n.id === frame.metaId ? updatedMeta : n));
    const parentEdges = reconcileParentEdges(frame.edges, frame.metaId, ports);
    set({
      nodes: parentNodes,
      edges: parentEdges,
      _past: frame._past,
      _future: frame._future,
      drillStack: state.drillStack.slice(0, -1),
      drillOrig: frame.orig,
      drillPortSeq: frame.portSeq,
      selectedId: frame.metaId,
      dirty: state.dirty || changed,
    });
  },

  exitToDepth: (depth) => {
    while (get().drillStack.length > depth) {
      get().exitMetanode();
    }
  },
```

`foldInterior`, `updateMetaNode`, and `reconcileParentEdges` are added in Task 6 — this task will not type-check until then. Implement Task 6 before running.

- [ ] **Step 5: (deferred) run after Task 6**

---

### Task 6: `foldInterior`, `updateMetaNode`, `reconcileParentEdges`

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (add module helpers after `origByIdOf`)

- [ ] **Step 1: Add the helpers**

After `origByIdOf` in `apps/web/src/editor/store/index.ts` add:

```ts
/** Serialize the live interior back into a metanode's subgraph + ports.
 *  Bars are excluded; unknown (placeholder) nodes are taken verbatim from the
 *  carried-forward originals (only their position is updated). Input ports keep
 *  their bar order and may have empty targets; output ports are kept only when a
 *  single internal source wire exists. */
function foldInterior(
  liveNodes: NoodleNode[],
  liveEdges: Edge[],
  origById: Record<string, GraphNodeShape>,
): { subgraph: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] }; ports: MetaPorts } {
  const inputBar = liveNodes.find((n) => n.id === META_BAR_INPUT_ID);
  const outputBar = liveNodes.find((n) => n.id === META_BAR_OUTPUT_ID);
  const interior = liveNodes.filter((n) => !isMetaBar(n));
  const interiorIds = new Set(interior.map((n) => n.id));

  const subNodes: GraphNodeLike[] = interior.map((n) => {
    if (n.data?.unavailableType) {
      const orig = origById[n.id];
      return {
        ...(orig as unknown as GraphNodeLike),
        position: { x: n.position.x, y: n.position.y },
      };
    }
    return nodeToGraphNode(n);
  });

  const subEdges: GraphEdgeLike[] = liveEdges
    .filter((e) => interiorIds.has(e.source) && interiorIds.has(e.target))
    .map(edgeToGraphEdge);

  const inputDescriptors =
    (inputBar?.data as { ports?: { id: string }[] } | undefined)?.ports ?? [];
  const inputs = inputDescriptors.map((d) => ({
    port: d.id,
    targets: liveEdges
      .filter((e) => e.source === META_BAR_INPUT_ID && (e.sourceHandle ?? "") === d.id)
      .map((e) => ({ target: e.target, target_input: e.targetHandle ?? "input" })),
  }));

  const outputDescriptors =
    (outputBar?.data as { ports?: { id: string }[] } | undefined)?.ports ?? [];
  const outputs = outputDescriptors
    .map((d) => {
      const wire = liveEdges.find(
        (e) => e.target === META_BAR_OUTPUT_ID && (e.targetHandle ?? "") === d.id,
      );
      return wire
        ? { port: d.id, source: wire.source, source_output: wire.sourceHandle ?? "main" }
        : null;
    })
    .filter((p): p is MetaPorts["outputs"][number] => p !== null);

  return { subgraph: { nodes: subNodes, edges: subEdges }, ports: { inputs, outputs } };
}

function updateMetaNode(
  meta: NoodleNode,
  subgraph: { nodes: GraphNodeLike[]; edges: GraphEdgeLike[] },
  ports: MetaPorts,
): NoodleNode {
  return {
    ...meta,
    data: {
      ...meta.data,
      manifest: buildMetanodeManifest(
        ports.inputs.map((p) => p.port),
        ports.outputs.map((p) => p.port),
      ),
      params: { ...meta.data.params, subgraph, ports },
    },
  };
}

/** Drop parent edges that reference a port the metanode no longer has. */
function reconcileParentEdges(parentEdges: Edge[], metaId: string, ports: MetaPorts): Edge[] {
  const validIn = new Set(ports.inputs.map((p) => p.port));
  const validOut = new Set(ports.outputs.map((p) => p.port));
  return parentEdges.filter((e) => {
    if (e.target === metaId && !validIn.has(e.targetHandle ?? "")) return false;
    if (e.source === metaId && !validOut.has(e.sourceHandle ?? "")) return false;
    return true;
  });
}
```

- [ ] **Step 2: Run the Task 5 tests**

Run: `npm run test -- store.drill.test.ts`
Expected: PASS (enter shows 2 bars + interior; no-edit exit round-trips; delete-inside survives).

- [ ] **Step 3: Run undo + metanode suites**

Run: `npm run test -- store.undo.test.ts store.metanodes.test.ts`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/editor/store/index.ts apps/web/src/editor/store.drill.test.ts
git commit -m "feat(editor): enter/exit metanode drill-in with interior fold-back"
```

---

### Task 7: Drill-aware `toGraph` (autosave folds interior up to root)

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (`toGraph` ~line 1060-1096)
- Test: extend `apps/web/src/editor/store.drill.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `store.drill.test.ts`:

```ts
describe("metanode drill-in: toGraph folds to root while drilled", () => {
  it("toGraph mid-drill returns the root workflow with edits folded in", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().deleteNode("C"); // edit inside, still drilled

    const g = useEditor.getState().toGraph();
    // Root still has A, D and the metanode; never the interior C or any bar.
    const ids = g.nodes.map((n) => n.id).sort();
    expect(ids).toContain("A");
    expect(ids).toContain("D");
    expect(ids).toContain(meta);
    expect(ids).not.toContain("C");
    expect(g.nodes.some((n) => n.type === "metaBar")).toBe(false);
    // The metanode's subgraph reflects the deletion.
    const metaNode = g.nodes.find((n) => n.id === meta)!;
    const sub = (metaNode.params as { subgraph: { nodes: { id: string }[] } }).subgraph;
    expect(sub.nodes.map((n) => n.id)).toEqual(["B"]);
  });

  it("nested drill folds both levels in toGraph", () => {
    load(chainGraph());
    const meta1 = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const meta2 = useEditor.getState().collapseToMetanode([meta1, "A"])!;
    useEditor.getState().enterMetanode(meta2);
    useEditor.getState().enterMetanode(meta1);
    expect(useEditor.getState().drillStack).toHaveLength(2);
    const g = useEditor.getState().toGraph();
    expect(g.nodes.map((n) => n.id)).toContain(meta2);
    expect(g.nodes.some((n) => n.type === "metaBar")).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- store.drill.test.ts`
Expected: FAIL — current `toGraph` serializes the live interior (returns `B`/bars, not the root).

- [ ] **Step 3: Make `toGraph` drill-aware**

In `apps/web/src/editor/store/index.ts`, extract the existing serialization into a pure helper and branch on the drill stack. Replace the whole `toGraph: () => { ... },` with:

```ts
  toGraph: () => {
    const state = get();
    if (state.drillStack.length === 0) {
      return serializeGraph(state.nodes, state.edges);
    }
    // Fold the live interior up the stack to reconstruct the root workflow.
    let nodes = state.nodes;
    let edges = state.edges;
    let orig = state.drillOrig;
    for (let i = state.drillStack.length - 1; i >= 0; i -= 1) {
      const frame = state.drillStack[i];
      const { subgraph, ports } = foldInterior(nodes, edges, orig);
      const meta = frame.nodes.find((n) => n.id === frame.metaId);
      const parentNodes = meta
        ? frame.nodes.map((n) => (n.id === frame.metaId ? updateMetaNode(meta, subgraph, ports) : n))
        : frame.nodes;
      nodes = parentNodes;
      edges = reconcileParentEdges(frame.edges, frame.metaId, ports);
      orig = frame.orig;
    }
    return serializeGraph(nodes, edges);
  },
```

Add the `serializeGraph` module helper (after `reconcileParentEdges`), containing the body that `toGraph` used to inline, but filtering out bar nodes defensively:

```ts
function serializeGraph(nodes: NoodleNode[], edges: Edge[]): WorkflowGraph {
  return {
    nodes: nodes
      .filter((n) => n.data?.manifest && !isMetaBar(n))
      .map((n) => ({
        id: n.id,
        type: n.data.manifest.id,
        params: n.data.params,
        position: { x: n.position.x, y: n.position.y },
        disabled: Boolean(n.data.disabled),
        outputs_override: n.data.outputsOverride,
        on_error: n.data.onError ?? "stop",
        retry_on_fail: Boolean(n.data.retryOnFail),
        retries: typeof n.data.retries === "number" ? n.data.retries : 1,
        retry_wait_seconds: typeof n.data.retryWaitSeconds === "number" ? n.data.retryWaitSeconds : 0,
        retry_backoff: Boolean(n.data.retryBackoff),
        always_output_data: Boolean(n.data.alwaysOutputData),
        timeout_seconds: typeof n.data.timeoutSeconds === "number" ? n.data.timeoutSeconds : null,
        tool_mode: Boolean(n.data.toolMode),
        tool_name: n.data.toolName ?? null,
        tool_description: n.data.toolDescription ?? "",
        label: n.data.label || undefined,
      })),
    edges: edges
      .filter((e) => !isMetaBar({ id: e.source }) && !isMetaBar({ id: e.target }))
      .map((e) => ({
        id: e.id,
        source: e.source,
        source_output: e.sourceHandle ?? "main",
        target: e.target,
        target_input: e.targetHandle ?? "input",
      })),
  };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- store.drill.test.ts`
Expected: PASS (mid-drill fold + nested fold; no bars leak).

- [ ] **Step 5: Run the full store suite + type-check**

Run: `npm run test -- store.drill.test.ts store.metanodes.test.ts store.undo.test.ts store.clipboard.test.ts`
Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: tests PASS; tsc still errors only on the not-yet-added `addMetaPort`/`removeMetaPort`/`runKeyFor`/`drillStepRunDisabledReason` (Tasks 8–9).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/store/index.ts apps/web/src/editor/store.drill.test.ts
git commit -m "feat(editor): drill-aware toGraph folds interior up to root for autosave"
```

---

## PHASE 3 — Bars: wiring, add/remove ports

### Task 8: Bar-aware `onConnect` + `addMetaPort`/`removeMetaPort`

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (`onConnect` ~line 1176-1218; add the two actions)
- Test: extend `apps/web/src/editor/store.drill.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `store.drill.test.ts`:

```ts
import { META_BAR_INPUT_ID, META_BAR_OUTPUT_ID } from "./store/drillSlice";

describe("metanode drill-in: port lifecycle", () => {
  it("add an input port inside ⇒ surfaces as an unconnected port on the parent", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!; // 1 input, 1 output
    useEditor.getState().enterMetanode(meta);
    const inBar = useEditor.getState().nodes.find((n) => n.id === META_BAR_INPUT_ID)!;
    const before = (inBar.data as { ports: unknown[] }).ports.length;

    useEditor.getState().addMetaPort("input");
    const after = useEditor.getState().nodes.find((n) => n.id === META_BAR_INPUT_ID)!;
    expect((after.data as { ports: unknown[] }).ports.length).toBe(before + 1);

    useEditor.getState().exitMetanode();
    const metaNode = useEditor.getState().nodes.find((n) => n.id === meta)!;
    const ports = (metaNode.data.params as { ports: { inputs: unknown[] } }).ports;
    expect(ports.inputs.length).toBe(before + 1);
    // new port has no parent edge (unconnected)
    const handles = useEditor.getState().edges.filter((e) => e.target === meta).map((e) => e.targetHandle);
    expect(handles.length).toBe(before); // only the original input is wired
  });

  it("removing an input port drops its parent edge on exit", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const inputPort = (useEditor.getState().nodes.find((n) => n.id === meta)!
      .data.params as { ports: { inputs: { port: string }[] } }).ports.inputs[0].port;
    expect(useEditor.getState().edges.some((e) => e.target === meta && e.targetHandle === inputPort)).toBe(true);

    useEditor.getState().enterMetanode(meta);
    useEditor.getState().removeMetaPort("input", inputPort);
    useEditor.getState().exitMetanode();
    expect(useEditor.getState().edges.some((e) => e.target === meta && e.targetHandle === inputPort)).toBe(false);
  });

  it("output bar accepts only one incoming wire per port", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const outPort = (useEditor.getState().nodes.find((n) => n.id === META_BAR_OUTPUT_ID)!
      .data as { ports: { id: string }[] }).ports[0].id;
    // wire B.main -> outPort, then C.main -> outPort; only the last survives
    useEditor.getState().onConnect({ source: "B", sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    useEditor.getState().onConnect({ source: "C", sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    const into = useEditor.getState().edges.filter((e) => e.target === META_BAR_OUTPUT_ID && e.targetHandle === outPort);
    expect(into).toHaveLength(1);
    expect(into[0].source).toBe("C");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- store.drill.test.ts`
Expected: FAIL — `addMetaPort`/`removeMetaPort` missing; `onConnect` throws/validates against bar nodes (no manifest).

- [ ] **Step 3: Bar-aware branch in `onConnect`**

In `onConnect`, at the very top of the function (before `const check = validateConnection(...)`), insert:

```ts
    // Boundary-bar connections bypass manifest validation (bar ports are `any`).
    if (
      connection.source === META_BAR_INPUT_ID ||
      connection.target === META_BAR_OUTPUT_ID
    ) {
      const s = get();
      const dupe = (e: Edge) =>
        e.source === connection.source &&
        (e.sourceHandle ?? null) === (connection.sourceHandle ?? null) &&
        e.target === connection.target &&
        (e.targetHandle ?? null) === (connection.targetHandle ?? null);
      // Output-bar handles take a single incoming wire; input-bar handles fan out.
      const kept =
        connection.target === META_BAR_OUTPUT_ID
          ? s.edges.filter(
              (e) =>
                !(e.target === META_BAR_OUTPUT_ID &&
                  (e.targetHandle ?? null) === (connection.targetHandle ?? null)),
            )
          : s.edges.filter((e) => !dupe(e));
      set({
        edges: addEdge(connection, kept),
        dirty: true,
        _past: [...s._past, { nodes: s.nodes, edges: s.edges }].slice(-HISTORY_LIMIT),
        _future: [],
      });
      return { ok: true, message: "", severity: "info" } as ConnectionCheck;
    }
```

(Confirm the `ConnectionCheck` success shape matches the type — if `severity`/`message` differ, mirror what `validateConnection` returns for an OK check. Inspect `apps/web/src/editor/connectionValidation.ts` if tsc complains.)

- [ ] **Step 4: Add `addMetaPort` / `removeMetaPort` actions**

In `create()`, after `exitToDepth` add:

```ts
  addMetaPort: (side) => {
    const state = get();
    const barId = side === "input" ? META_BAR_INPUT_ID : META_BAR_OUTPUT_ID;
    const seq = state.drillPortSeq;
    const newId = `${side === "input" ? "in" : "out"}_${seq}`;
    set({
      nodes: state.nodes.map((n) =>
        n.id === barId
          ? {
              ...n,
              data: {
                ...n.data,
                ports: [...((n.data as { ports: { id: string; label: string }[] }).ports), { id: newId, label: newId }],
              },
            }
          : n,
      ),
      drillPortSeq: seq + 1,
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },

  removeMetaPort: (side, portId) => {
    const state = get();
    const barId = side === "input" ? META_BAR_INPUT_ID : META_BAR_OUTPUT_ID;
    set({
      nodes: state.nodes.map((n) =>
        n.id === barId
          ? {
              ...n,
              data: {
                ...n.data,
                ports: ((n.data as { ports: { id: string }[] }).ports).filter((p) => p.id !== portId),
              },
            }
          : n,
      ),
      edges: state.edges.filter((e) =>
        side === "input"
          ? !(e.source === barId && (e.sourceHandle ?? "") === portId)
          : !(e.target === barId && (e.targetHandle ?? "") === portId),
      ),
      dirty: true,
      _past: [...state._past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
      _future: [],
    });
  },
```

- [ ] **Step 5: Run to verify it passes**

Run: `npm run test -- store.drill.test.ts`
Expected: PASS (add surfaces unconnected parent port; remove drops parent edge; output single-wire).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/store/index.ts apps/web/src/editor/store.drill.test.ts
git commit -m "feat(editor): bar-aware connections + add/remove metanode ports from inside"
```

---

## PHASE 4 — Step-run inside

### Task 9: `runKeyFor`, drill-prefix targeting, isolated gating

**Files:**
- Modify: `apps/web/src/editor/store/index.ts` (`runFromNode` ~line 1970; `runFromTrigger` ~line 1974; add `runKeyFor`, `drillStepRunDisabledReason`)
- Test: `apps/web/src/editor/store.drillRun.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/editor/store.drillRun.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}
function codeManifest(): NodeManifest {
  return {
    id: "code", name: "Code", category: "Core", version: "1", description: "",
    icon: null, inputs: [port("input")], outputs: [port("main")], params: [],
  };
}
function chainGraph(): WorkflowGraph {
  const mk = (id: string) => ({
    id, type: "code", params: {}, position: { x: 0, y: 0 },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
    always_output_data: false, timeout_seconds: null,
  });
  const ed = (s: string, t: string) => ({
    id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
  });
  return { nodes: [mk("A"), mk("B"), mk("C"), mk("D")], edges: [ed("A", "B"), ed("B", "C"), ed("C", "D")] };
}
function load(graph: WorkflowGraph) {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([codeManifest()]);
  useEditor.getState().loadGraph(graph);
}

describe("step-run inside a metanode", () => {
  it("runKeyFor namespaces interior ids by the drill path", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    expect(useEditor.getState().runKeyFor("B")).toBe("B"); // root
    useEditor.getState().enterMetanode(meta);
    expect(useEditor.getState().runKeyFor("B")).toBe(`${meta}/B`);
  });

  it("runFromNode inside a transparent metanode targets the namespaced id", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const handler = vi.fn().mockResolvedValue(undefined);
    useEditor.getState().setRunHandler(handler);
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().runFromNode("B");
    expect(handler).toHaveBeenCalledWith([`${meta}/B`], expect.objectContaining({ reuseUpstream: true }));
  });

  it("step-run is disabled inside an isolated metanode", () => {
    load(chainGraph());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    // mark isolated
    useEditor.getState().updateParams(meta, {
      ...(useEditor.getState().nodes.find((n) => n.id === meta)!.data.params as object),
      execution: "isolated",
    } as Record<string, unknown>);
    useEditor.getState().enterMetanode(meta);
    expect(useEditor.getState().drillStepRunDisabledReason()).toMatch(/isolated/i);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- store.drillRun.test.ts`
Expected: FAIL — `runKeyFor` / `drillStepRunDisabledReason` missing; `runFromNode` doesn't namespace.

- [ ] **Step 3: Add `runKeyFor` and `drillStepRunDisabledReason`**

In `create()`, add (near `runFromNode`):

```ts
  runKeyFor: (nodeId) => drillPrefix(get().drillStack) + nodeId,

  drillStepRunDisabledReason: () => {
    const state = get();
    if (state.drillStack.length === 0) return null;
    // Every metanode in the path must be transparent for the interior to be
    // flattened to the root run (and thus individually targetable).
    for (const frame of state.drillStack) {
      const meta = frame.nodes.find((n) => n.id === frame.metaId);
      const exec = String((meta?.data.params as { execution?: string } | undefined)?.execution ?? "transparent");
      if (exec === "isolated") {
        return "Step-run isn't available inside an isolated metanode — run the metanode from the parent, or set its execution to transparent.";
      }
    }
    return null;
  },
```

- [ ] **Step 4: Namespace `runFromNode` / `runFromTrigger`**

Replace `runFromNode` and `runFromTrigger` with:

```ts
  runFromNode: (id, options = { reuseUpstream: true }) => {
    const state = get();
    if (state.drillStepRunDisabledReason()) return;
    const handler = state.runHandler;
    if (handler) void handler([state.runKeyFor(id)], options);
  },
  runFromTrigger: (id) => {
    const state = get();
    if (state.drillStepRunDisabledReason()) return;
    const handler = state.runHandler;
    if (handler) void handler(undefined, { triggerNodeId: state.runKeyFor(id) });
  },
```

- [ ] **Step 5: Run to verify it passes**

Run: `npm run test -- store.drillRun.test.ts`
Expected: PASS.

- [ ] **Step 6: Full store type-check**

Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: PASS (all `EditorStore` actions now implemented).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/store/index.ts apps/web/src/editor/store.drillRun.test.ts
git commit -m "feat(editor): step-run inside transparent metanodes via namespaced targets"
```

---

## PHASE 5 — UI: bars, breadcrumb, canvas wiring, NodeCard

### Task 10: `MetaBar` component + register node type

**Files:**
- Create: `apps/web/src/editor/MetaBar.tsx`
- Modify: `apps/web/src/editor/Canvas.tsx` (`nodeTypes` ~line 47-53)
- Modify: `apps/web/src/editor/editor.css` (append bar styles)
- Test: `apps/web/src/editor/MetaBar.test.tsx` (create)

- [ ] **Step 1: Write the component**

Create `apps/web/src/editor/MetaBar.tsx`:

```tsx
import { Handle, type NodeProps, Position } from "@xyflow/react";
import { Plus, X } from "@phosphor-icons/react";

import { useEditor } from "./store";

interface MetaBarData {
  bar: "input" | "output";
  ports: { id: string; label: string }[];
}

const ROW_H = 40;
const HEAD_H = 34;

export function MetaBar({ data }: NodeProps) {
  const { bar, ports } = data as unknown as MetaBarData;
  const addMetaPort = useEditor((s) => s.addMetaPort);
  const removeMetaPort = useEditor((s) => s.removeMetaPort);
  const isInput = bar === "input";
  const height = HEAD_H + ports.length * ROW_H + ROW_H; // + add-row

  return (
    <div className={`meta-bar meta-bar-${bar}`} style={{ height }}>
      <div className="meta-bar-head">{isInput ? "Inputs" : "Outputs"}</div>
      {ports.map((p, i) => (
        <div className="meta-bar-row" key={p.id} style={{ top: HEAD_H + i * ROW_H }}>
          <span className="meta-bar-label">{p.label}</span>
          <button
            type="button"
            className="meta-bar-remove nodrag"
            aria-label={`Remove ${bar} port ${p.label}`}
            title="Remove port"
            onClick={() => removeMetaPort(bar, p.id)}
          >
            <X size={11} weight="bold" />
          </button>
          <Handle
            type={isInput ? "source" : "target"}
            position={isInput ? Position.Right : Position.Left}
            id={p.id}
            style={{ top: HEAD_H + i * ROW_H + ROW_H / 2 }}
          />
        </div>
      ))}
      <button
        type="button"
        className="meta-bar-add nodrag"
        style={{ top: HEAD_H + ports.length * ROW_H }}
        onClick={() => addMetaPort(bar)}
      >
        <Plus size={11} weight="bold" /> Add {isInput ? "input" : "output"}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Register the node type in Canvas**

In `apps/web/src/editor/Canvas.tsx`, import and register:

```ts
import { MetaBar } from "./MetaBar";
```

```ts
const nodeTypes = {
  noodle: NodeCard,
  sticky: StickyNote,
  group: NodeGroup,
  mapGroup: MapGroupNode,
  loopFrame: LoopFrame,
  metaBar: MetaBar,
};
```

- [ ] **Step 3: Add styles**

Append to `apps/web/src/editor/editor.css`:

```css
.meta-bar {
  position: relative;
  width: 168px;
  background: rgba(20, 24, 34, 0.92);
  border: 1px solid rgba(120, 130, 160, 0.35);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
}
.meta-bar-head {
  height: 34px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #9aa4bf;
  border-bottom: 1px solid rgba(120, 130, 160, 0.25);
}
.meta-bar-row { position: absolute; left: 0; right: 0; height: 40px; display: flex; align-items: center; padding: 0 12px; }
.meta-bar-input .meta-bar-row { justify-content: flex-start; }
.meta-bar-output .meta-bar-row { justify-content: flex-end; }
.meta-bar-label { font-size: 12px; color: #d6dbe8; }
.meta-bar-remove {
  background: none; border: none; color: #7b8398; cursor: pointer;
  display: inline-flex; padding: 2px; margin: 0 4px;
}
.meta-bar-remove:hover { color: #f06f6f; }
.meta-bar-add {
  position: absolute; left: 12px; right: 12px; height: 30px;
  display: inline-flex; align-items: center; gap: 4px; justify-content: center;
  background: rgba(99, 102, 241, 0.12); border: 1px dashed rgba(120, 130, 160, 0.4);
  border-radius: 6px; color: #9aa4bf; font-size: 11px; cursor: pointer;
}
.meta-bar-add:hover { color: #c7ccda; border-color: rgba(150, 160, 190, 0.6); }
```

- [ ] **Step 4: Write a component test**

Create `apps/web/src/editor/MetaBar.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";

import { MetaBar } from "./MetaBar";

function renderBar(bar: "input" | "output", ports: { id: string; label: string }[]) {
  return render(
    <ReactFlowProvider>
      <MetaBar
        id={bar}
        type="metaBar"
        data={{ bar, ports }}
        selected={false}
        zIndex={0}
        isConnectable
        xPos={0}
        yPos={0}
        dragging={false}
      />
    </ReactFlowProvider>,
  );
}

describe("MetaBar", () => {
  it("renders one labelled row per port and an add button", () => {
    renderBar("input", [{ id: "in_0", label: "in_0" }, { id: "in_1", label: "in_1" }]);
    expect(screen.getByText("in_0")).toBeTruthy();
    expect(screen.getByText("in_1")).toBeTruthy();
    expect(screen.getByText(/add input/i)).toBeTruthy();
  });
});
```

(If `NodeProps` requires different props than the test passes, align the test object to the project's existing node-component test pattern — see `NodeCard.test.tsx`.)

- [ ] **Step 5: Run the component test + type-check**

Run: `npm run test -- MetaBar.test.tsx`
Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/MetaBar.tsx apps/web/src/editor/MetaBar.test.tsx apps/web/src/editor/Canvas.tsx apps/web/src/editor/editor.css
git commit -m "feat(editor): MetaBar boundary-bar node component"
```

---

### Task 11: Breadcrumb + double-click-to-enter + replace `MetanodePreview`

**Files:**
- Create: `apps/web/src/editor/MetanodeBreadcrumb.tsx`
- Modify: `apps/web/src/editor/Canvas.tsx` (double-click handler ~line 982-989; `metaPreviewId` state + render block ~line 442, 1133-1147; onboarding gate ~line 1020; `handleNodesChange` filter ~line 739-747; selection/grouping memos ~line 242-248, 763-769)
- Modify: `apps/web/src/editor/editor.css` (breadcrumb styles; remove `.meta-preview*`)
- Delete: `apps/web/src/editor/MetanodePreview.tsx`
- Test: `apps/web/src/editor/MetanodeBreadcrumb.test.tsx` (create)

- [ ] **Step 1: Write the breadcrumb component**

Create `apps/web/src/editor/MetanodeBreadcrumb.tsx`:

```tsx
import { CaretRight } from "@phosphor-icons/react";

import { useEditor } from "./store";

export function MetanodeBreadcrumb() {
  const drillStack = useEditor((s) => s.drillStack);
  const exitToDepth = useEditor((s) => s.exitToDepth);
  if (drillStack.length === 0) return null;
  return (
    <nav className="meta-breadcrumb" aria-label="Metanode path">
      <button type="button" onClick={() => exitToDepth(0)}>Workflow</button>
      {drillStack.map((frame, i) => (
        <span key={frame.metaId} className="meta-breadcrumb-seg">
          <CaretRight size={11} weight="bold" aria-hidden />
          <button
            type="button"
            onClick={() => exitToDepth(i + 1)}
            aria-current={i === drillStack.length - 1 ? "page" : undefined}
          >
            {frame.name}
          </button>
        </span>
      ))}
    </nav>
  );
}
```

- [ ] **Step 2: Route double-click to enter; render breadcrumb; drop preview**

In `apps/web/src/editor/Canvas.tsx`:

1. Add import `import { MetanodeBreadcrumb } from "./MetanodeBreadcrumb";` and remove `import { MetanodePreview } from "./MetanodePreview";`.
2. Add store hooks near the others: `const enterMetanode = useEditor((s) => s.enterMetanode);` and `const drillDepth = useEditor((s) => s.drillStack.length);`.
3. Replace the `onNodeDoubleClick` handler body:

```ts
        onNodeDoubleClick={(_, node) => {
          const sn = nodes.find((n) => n.id === node.id);
          if (sn?.data?.manifest?.id === "meta_node") {
            enterMetanode(node.id);
            return;
          }
          if (node.type === "noodle" || node.type === "mapGroup") openNdv(node.id);
        }}
```

4. Remove the `const [metaPreviewId, setMetaPreviewId] = useState<string | null>(null);` line.
5. Delete the entire `{metaPreviewId && (() => { ... <MetanodePreview .../> ... })()}` block (~line 1133-1147).
6. Render the breadcrumb just inside the `ReactFlow` children, before `<Background ...>`:

```tsx
        <MetanodeBreadcrumb />
```

- [ ] **Step 3: Gate onboarding while drilled**

Change the empty-onboarding condition (~line 1020) from `{nodes.length === 0 && (` to:

```tsx
        {nodes.length === 0 && drillDepth === 0 && (
```

- [ ] **Step 4: Exclude bars from the change filter and selection memos**

In `handleNodesChange` (~line 739-747), extend the filter to also drop changes for bar ids:

```ts
  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) =>
      onNodesChange(
        changes.filter((c) => {
          if (!("id" in c) || typeof c.id !== "string") return true;
          if (c.id.startsWith(LOOP_FRAME_ID_PREFIX)) return false;
          // Bars are fixed: never let RF remove or reposition them.
          if (c.id === META_BAR_INPUT_ID || c.id === META_BAR_OUTPUT_ID) {
            return c.type !== "remove" && c.type !== "position";
          }
          return true;
        }),
      ),
    [onNodesChange],
  );
```

Add the import in Canvas: `import { META_BAR_INPUT_ID, META_BAR_OUTPUT_ID } from "./store/drillSlice";`.

In both `selectedMetanodeCandidates` (~line 242) and `selectedMetanodeIds` (~line 763) memos, the `.filter((node) => node.selected && node.data?.manifest && node.data.manifest.id !== "meta_node")` already excludes bars (bars have no `data.manifest`). No change needed — verify by inspection.

- [ ] **Step 5: Add breadcrumb styles; remove preview styles**

Append to `editor.css`:

```css
.meta-breadcrumb {
  position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
  z-index: 6; display: inline-flex; align-items: center; gap: 2px;
  padding: 4px 10px; border-radius: 999px;
  background: rgba(16, 20, 28, 0.9); border: 1px solid rgba(120, 130, 160, 0.3);
  font-size: 12px; color: #c7ccda;
}
.meta-breadcrumb button { background: none; border: none; color: #9aa4bf; cursor: pointer; padding: 2px 4px; font-size: 12px; }
.meta-breadcrumb button:hover { color: #fff; }
.meta-breadcrumb button[aria-current="page"] { color: #fff; font-weight: 600; }
.meta-breadcrumb-seg { display: inline-flex; align-items: center; gap: 2px; }
```

Delete every `.meta-preview`, `.meta-preview-overlay`, `.meta-preview-head`, `.meta-preview-crumb`, `.meta-preview-sep`, `.meta-preview-tag`, `.meta-preview-actions`, `.meta-preview-canvas` rule block from `editor.css`.

- [ ] **Step 6: Delete `MetanodePreview.tsx` and confirm no references**

Run: `git grep -n "MetanodePreview"`
Expected: no matches after removing the import. Then:

```bash
git rm apps/web/src/editor/MetanodePreview.tsx
```

- [ ] **Step 7: Add the Escape-to-exit key handler**

In Canvas's keyboard `useEffect` (~line 620-657), inside `onKey`, before the `const meta = e.ctrlKey || e.metaKey;` line add:

```ts
      if (e.key === "Escape" && useEditor.getState().drillStack.length > 0) {
        e.preventDefault();
        useEditor.getState().exitMetanode();
        return;
      }
```

- [ ] **Step 8: Write the breadcrumb test**

Create `apps/web/src/editor/MetanodeBreadcrumb.test.tsx`:

```tsx
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { useEditor } from "./store";
import { MetanodeBreadcrumb } from "./MetanodeBreadcrumb";
import type { DrillFrame } from "./store/drillSlice";

beforeEach(() => {
  useEditor.setState({ drillStack: [], drillOrig: {}, drillPortSeq: 1 });
});

function frame(metaId: string, name: string): DrillFrame {
  return { metaId, name, nodes: [], edges: [], _past: [], _future: [], orig: {}, portSeq: 1 };
}

describe("MetanodeBreadcrumb", () => {
  it("renders Workflow + each level and pops on click", () => {
    useEditor.setState({ drillStack: [frame("A", "Alpha"), frame("B", "Beta")] });
    render(<MetanodeBreadcrumb />);
    expect(screen.getByText("Workflow")).toBeTruthy();
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    fireEvent.click(screen.getByText("Workflow"));
    expect(useEditor.getState().drillStack).toHaveLength(0);
  });
});
```

- [ ] **Step 9: Run tests + type-check**

Run: `npm run test -- MetanodeBreadcrumb.test.tsx`
Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/editor/MetanodeBreadcrumb.tsx apps/web/src/editor/MetanodeBreadcrumb.test.tsx apps/web/src/editor/Canvas.tsx apps/web/src/editor/editor.css
git rm apps/web/src/editor/MetanodePreview.tsx
git commit -m "feat(editor): metanode drill-in navigation (breadcrumb, double-click enter, Escape)"
```

---

### Task 12: NodeCard — drill-aware run keys, step-run gating, unavailable placeholder

**Files:**
- Modify: `apps/web/src/editor/NodeCard.tsx`

- [ ] **Step 1: Read run state through `runKeyFor`**

In `NodeCard`, replace the run-state selectors (~line 236-239) that key by `id`:

```ts
  const runKey = useEditor((s) => s.runKeyFor(id));
  const runStatus = useEditor((s) => s.runStatus[runKey]);
  const runMeta = useEditor((s) => s.runMeta[runKey]);
  const runIteration = useEditor((s) => s.runIterations[runKey]);
  const runChunk = useEditor((s) => s.runChunks[runKey]);
```

Leave `isPinned`/`agentActive` keyed by `id` (pins are interior-local and agent activity is root-level). Verify no other `s.runStatus[id]`/`s.runOutputs[id]` reads remain in this file via `git grep -n "runStatus\[id\]\|runOutputs\[id\]\|runMeta\[id\]" apps/web/src/editor/NodeCard.tsx`.

- [ ] **Step 2: Gate step-run buttons**

Add near the other store hooks:

```ts
  const stepRunDisabledReason = useEditor((s) => s.drillStepRunDisabledReason());
```

In the toolbar's two run buttons (the ▶ and ↻, ~line 394-447), change `disabled={running || !canRunStep}` to `disabled={running || !canRunStep || Boolean(stepRunDisabledReason)}` and append the reason to the title, e.g.:

```ts
        title={stepRunDisabledReason ?? (
          !canRunStep ? "Connect a trigger upstream to run this node" : isWebhook ? "Listen for test event" : isTrigger ? "Run this trigger and its downstream nodes" : "Run step using current upstream data"
        )}
```

(Apply the same `stepRunDisabledReason ??` wrapper to both buttons' `title`/`aria-label`.)

- [ ] **Step 3: Unavailable placeholder branch**

At the top of `NodeCard` after `const { manifest, disabled, outputsOverride } = data;`, add:

```ts
  const isUnavailable = Boolean((data as { unavailableType?: string }).unavailableType);
```

Just before the main `return (` for the standard node (~line 747), add an early return for placeholders:

```tsx
  if (isUnavailable) {
    return (
      <div className="node">
        <div className="node-tile node-unavailable" title={`Unavailable node type: ${(data as { unavailableType?: string }).unavailableType}`}>
          <Warning size={22} weight="bold" />
          <Handle type="target" position={Position.Left} id="input" />
          <Handle type="source" position={Position.Right} id="main" />
        </div>
        <div className="node-label">{(data as { unavailableType?: string }).unavailableType}</div>
      </div>
    );
  }
```

(`Warning` and `Handle`/`Position` are already imported in NodeCard.)

Append to `editor.css`:

```css
.node-unavailable { display: flex; align-items: center; justify-content: center; color: #d8a657; border: 1px dashed rgba(216, 166, 87, 0.6) !important; }
```

- [ ] **Step 4: Run NodeCard tests + type-check**

Run: `npm run test -- NodeCard.test.tsx`
Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Expected: PASS. (If `NodeCard.test.tsx` asserts run badges keyed by id, update those to set `runStatus` under the bare id — at root `runKeyFor(id) === id`, so existing tests keep working.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/NodeCard.tsx apps/web/src/editor/editor.css
git commit -m "feat(editor): NodeCard drill-aware run keys, step-run gating, unavailable placeholder"
```

---

## PHASE 6 — Hardening + full sweep

### Task 13: Edge-case sweep, manual verification, full suite

**Files:** none beyond fixes surfaced below.

- [ ] **Step 1: Bar-leak guard test**

Append to `store.drill.test.ts` a test asserting `toGraph()` while drilled contains zero `metaBar` nodes and zero edges referencing a bar id (covers spec edge case 13). Then run `npm run test -- store.drill.test.ts`.

```ts
it("never leaks bar nodes or proxy edges into the serialized graph", () => {
  load(chainGraph());
  const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
  useEditor.getState().enterMetanode(meta);
  const g = useEditor.getState().toGraph();
  expect(g.nodes.some((n) => n.type === "metaBar")).toBe(false);
  expect(
    g.edges.some((e) => e.source.startsWith("__meta_") || e.target.startsWith("__meta_")),
  ).toBe(false);
});
```

- [ ] **Step 2: `loadGraph` resets the stack test**

Append and run:

```ts
it("switching workflow (loadGraph) exits any drill", () => {
  load(chainGraph());
  const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
  useEditor.getState().enterMetanode(meta);
  expect(useEditor.getState().drillStack).toHaveLength(1);
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  expect(useEditor.getState().drillStack).toHaveLength(0);
});
```

- [ ] **Step 3: Collapse-inside (nested creation) test**

Append and run (bars excluded from selection; new nested metanode created inside):

```ts
it("collapsing a selection inside a metanode creates a nested metanode", () => {
  load(chainGraph());
  const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
  useEditor.getState().enterMetanode(meta);
  // select B and C inside, collapse
  const inner = useEditor.getState().collapseToMetanode(["B", "C"]);
  expect(inner).toBeTruthy();
  const interior = useEditor.getState().nodes.filter((n) => n.data?.manifest?.id === "meta_node");
  expect(interior).toHaveLength(1);
});
```

- [ ] **Step 4: Run the entire editor test suite**

Run: `npm run test`
Expected: all PASS. Fix any regressions (most likely: a test that read `runStatus[id]` directly — `runKeyFor` at root is identity, so behavior is unchanged; if a test stubs the store shape, add the three drill fields).

- [ ] **Step 5: Full type-check + lint**

Run: `npx tsc -p apps/web/tsconfig.json --noEmit`
Run: `npm run lint` (if present in `apps/web/package.json`)
Expected: clean.

- [ ] **Step 6: Manual smoke test (document results in the commit body)**

Start the app (per the project's run instructions). Verify by hand:
1. Group 3 nodes → double-click the metanode → the canvas becomes its interior with left/right bars and real node cards; breadcrumb shows `Workflow › Metanode`.
2. Add a node inside, wire it, run that one node (▶) → it executes and shows output.
3. Add an input port via the bar → exit → the metanode shows a new unconnected input port on the parent; wire it.
4. Re-enter → the new proxy is present; remove a port → exit → the matching parent edge is gone.
5. Nest: group inside, enter the inner one, breadcrumb shows 3 levels; Escape pops one level; clicking `Workflow` exits all.
6. Delete a node on the root canvas with Delete key → one Ctrl+Z restores the node AND its edges.
7. Reload the page → workflow reopens at root with the metanode intact (autosave persisted interior edits).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/store.drill.test.ts
git commit -m "test(editor): metanode drill-in edge-case sweep (bar leak, reset, nested collapse)"
```

---

## Self-Review Notes (addressed)

- **Spec coverage:** undo fix (Task 1 ↔ spec §1); drill stack/swap/fold (Tasks 4–7 ↔ §2.2/§2.3); bars + port lifecycle (Tasks 8, 10 ↔ §2.5–§2.8); nested + breadcrumb (Tasks 5, 11 ↔ §2.3/§2.10); step-run namespacing (Task 9 ↔ §2.9); unknown/nested materialize (Task 3 ↔ §2.6); NodeCard run keys + placeholder (Task 12). Edge cases 1–20 map to tests in Tasks 5–9, 13.
- **Type consistency:** `MetaPorts`, `DrillFrame`, `GraphNodeShape`, `runKeyFor`, `drillStepRunDisabledReason`, `materializeInterior`, `foldInterior`, `updateMetaNode`, `reconcileParentEdges`, `serializeGraph`, `META_BAR_INPUT_ID`/`META_BAR_OUTPUT_ID`, `isMetaBar`, `drillPrefix`, `maxPortSuffix` are defined once and reused by exact name.
- **Deviation from spec, intentional:** ports are added/removed via explicit bar buttons (`addMetaPort`/`removeMetaPort`) rather than a drag-from-stub (cleaner, KNIME-like); an output port with no internal source wire is dropped on fold (the engine's `port_out` requires a real source), while input ports may persist with empty targets — documented in `foldInterior`.
