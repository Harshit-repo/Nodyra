# Metanodes — Design

**Status:** design.

**Goal:** Select N nodes on the canvas and collapse them into a single **metanode** that contains
them (KNIME-style). The metanode's ports are the connections that crossed the selection boundary.
Open it to edit the inside; ungroup to dissolve it back. Both the **execution model** and the
**edit experience** are user-selectable.

---

## 1. Concept

A metanode encapsulates a **sub-graph** (its own `nodes` + `edges`). Its **boundary ports** are
derived from edges that crossed the original selection:
- every edge entering the selection from outside → one **input port** on the metanode;
- every edge leaving the selection → one **output port**.

The sub-graph is **embedded in the metanode** (self-contained; travels with the workflow), not a
reference to a separate stored workflow. This differs from Map Group (which references a child
workflow) but reuses the same editor inline-editing machinery (`childWorkflows`, `addBodyNode`,
boundary handles).

## 2. Execution model — user choice (`execution` param)

- **`transparent`** (default) — *KNIME metanode*. Purely organizational. A pre-run pass
  **flattens** the metanode: its sub-graph nodes are inlined into the parent (ids namespaced
  `meta_<id>/<child_id>`), boundary input ports rewired to the internal consumers, output ports to
  the internal producers. The flat graph then runs on the **unchanged engine** — identical results,
  caching, ordering; works with loops/branches for free. Group/ungroup never changes output.
- **`isolated`** — *KNIME component*. The metanode runs its sub-graph as a **nested execution** with
  its own scope (cache/retry/timeout boundary), via the existing sub-workflow path
  (`workflow_caller` / inline-graph execution, the same mechanism Map Group + execute_workflow use).
  Inputs are fed to the sub-graph's boundary; the leaf outputs map back to the metanode's output
  ports.

Engine surface:
- `_expand_metanodes(graph)` — pure transform applied at the top of `execute()` (and in the runtime
  server before running). Recursively inlines every `transparent` metanode. Isolated metanodes are
  left as nodes and driven like a sub-workflow.
- Namespacing keeps run inspection traceable (a flattened node id shows its metanode prefix).

## 3. Boundary ports

Computed at **collapse** time and recomputed when the inside changes:
- Input port `in_<k>` for each distinct (external source node, external output) feeding a selected
  node; the metanode forwards it to the internal target(s) via an internal **input-proxy** mapping.
- Output port `out_<k>` for each selected node output consumed outside.
- Ports are auto-named from the internal node/port (e.g. `Agent.main`) and renameable.
- KNIME-style **input/output bars** inside the metanode editor represent these proxies.

## 4. Authoring (store)

- **Collapse selection → metanode**: move selected nodes + internal edges into a new metanode's
  embedded sub-graph; derive boundary ports; rewire parent edges from/to the metanode's ports;
  replace the selection with the single metanode (placed at the selection's centroid).
- **Ungroup**: inverse — splice the sub-graph back into the parent, restore the original boundary
  edges, delete the metanode.
- **Edit**: open the metanode (see §5).
- Undo/redo: collapse/ungroup are single history steps.
- Validation: collapsing must keep the parent acyclic (a selection that would create a cycle through
  the metanode boundary is rejected with a clear message).

## 5. Edit experience — user choice

Both available; a per-metanode/default preference picks the double-click behavior:
- **Drill-in canvas** (default) — opens the sub-graph as its own canvas with a breadcrumb
  (`Workflow › Meta: "name"`); scales to large/nested metanodes. New editor "path" state.
- **Inline expand-in-place** — a resizable container edited on the main canvas, reusing the Map
  Group container rendering.
Nesting (metanode inside metanode) is supported by both; the breadcrumb shows the full path.

## 6. Persistence

- The metanode node carries `params.subgraph = {nodes, edges}`, `params.ports` (boundary mapping),
  `params.execution`, and a display `name`.
- `WorkflowGraph` is unchanged structurally (params is free-form); the embedded sub-graph is just
  data. Editor serializes its inline `childWorkflows`-style state into `params.subgraph` on save and
  hydrates on load.

## 7. Relationship to existing nodes

- **Map Group** = a metanode that *iterates* (it already does inline sub-graph + boundary handles).
  Post-metanode, Map Group could be reframed as "metanode + loop," but stays as-is for now.
- **Loops** compose: a metanode may contain a Loop Start/End pair (must be fully inside — SESE region
  can't cross the boundary), and a metanode may sit inside a loop body.
- **execute_workflow** stays the option for *referencing a separately stored* workflow; metanodes are
  *embedded*.

## 8. Testing strategy

- **Core:** `_expand_metanodes` flattening (boundary rewire, id namespacing, nested metanodes,
  acyclic result); transparent metanode end-to-end equals the ungrouped graph; isolated metanode runs
  as a sub-graph and maps ports; a metanode containing a loop.
- **Store (vitest):** collapse derives correct boundary ports + rewires edges; ungroup is the exact
  inverse (round-trip); cycle rejection; nested collapse; undo/redo.
- **Runtime:** a transparent-metanode graph runs in-subprocess (flatten happens before dispatch).
- **UI:** drill-in breadcrumb nav; inline expand; port bars.

## 9. Phasing

1. **Engine** — `_expand_metanodes` transparent flatten + tests (foundational, pure).
2. **Store** — collapse / ungroup / boundary-port derivation + vitest (the core authoring).
3. **Execution choice** — isolated sub-run driver + `execution` param.
4. **UI** — container rendering + drill-in canvas + inline expand + port bars.
5. **Polish** — rename ports, nesting breadcrumb, validation messages.

## Out of scope (later)

- Shareable/library metanodes (save a metanode to reuse across workflows — KNIME "shared
  components"). Metanodes here are per-workflow/embedded.
