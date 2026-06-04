# Node Tool Mode — Phase 2a (Editor Data Model + Connection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the backend tool-mode model into the editor's data layer — `tool_mode`/`tool_name`/`tool_description` on graph nodes round-trip through the store, and a tool-mode node's `tool` output is accepted by the editor's connection validator into an AI Agent's tool port.

**Architecture:** Add the tool fields to the TS `GraphNode`/`NodeManifest` types and the store's `NoodleNodeData` (optional, so node-creation sites need no change); serialize them in `loadGraph`/`toGraph`; reuse the existing `updateNodeSettings` action to toggle them; teach `connectionValidation` that a tool-mode node's `tool` output is `ai_tool`.

**Tech Stack:** React + TypeScript + Vite + vitest. Frontend only (`apps/web`).

**Spec:** `docs/superpowers/specs/2026-06-04-node-tool-mode-design.md` (Phase 2)

**Scope:** This is Phase 2a — the data/connection foundation. Phase 2b (NDV "Use as tool" toggle + per-param Fixed/From-AI control + `NodeCard` `ai_tool` output handle) and Phase 2c (drag-from-port picker) are separate plans, written against `NodeDetails.tsx`'s current state when started.

**Concurrency note:** another effort edits `apps/api`/`apps/web` files. Commit only the specific files in each task — never `git add -A`.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `apps/web/src/types.ts` | `usable_as_tool` on `NodeManifest`; `tool_mode`/`tool_name`/`tool_description` on `GraphNode` | Modify |
| `apps/web/src/editor/store.ts` | `NoodleNodeData` + `NodeSettingsPatch` tool fields; `loadGraph`/`toGraph` serialization | Modify |
| `apps/web/src/editor/store.toolmode.test.ts` | Round-trip vitest | Create |
| `apps/web/src/editor/connectionValidation.ts` | Tool-mode `tool` output validates as `ai_tool` | Modify |
| `apps/web/src/editor/connectionValidation.test.ts` | Connection vitest cases | Modify |

Commands run from repo root `D:\noodle`. Frontend commands use `npm --prefix apps/web …`.

---

## Task 1: Tool fields in types + store round-trip

**Files:**
- Modify: `apps/web/src/types.ts` (`NodeManifest` ~line 68; `GraphNode` ~line 84)
- Modify: `apps/web/src/editor/store.ts` (`NoodleNodeData` ~line 22; `NodeSettingsPatch`; `loadGraph` ~line 389; `toGraph` ~line 430)
- Test: `apps/web/src/editor/store.toolmode.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/editor/store.toolmode.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function manifest(id: string): NodeManifest {
  return {
    id, name: id, category: "Core", version: "1", description: "", icon: null,
    inputs: [port("input")], outputs: [port("main")], params: [],
  };
}

const GRAPH: WorkflowGraph = {
  nodes: [
    {
      id: "n1", type: "http_request", params: { url: "https://x" },
      position: { x: 0, y: 0 }, disabled: false, outputs_override: null,
      on_error: "stop", retry_on_fail: false, retries: 1,
      retry_wait_seconds: 0, retry_backoff: false, always_output_data: false,
      timeout_seconds: null,
      tool_mode: true, tool_name: "fetch", tool_description: "Fetch a URL",
    },
  ],
  edges: [],
};

describe("store tool-mode round-trip", () => {
  it("loadGraph → toGraph preserves tool_mode fields", () => {
    useEditor.getState().setManifests([manifest("http_request")]);
    useEditor.getState().loadGraph(GRAPH);

    const out = useEditor.getState().toGraph();
    const node = out.nodes[0];
    expect(node.tool_mode).toBe(true);
    expect(node.tool_name).toBe("fetch");
    expect(node.tool_description).toBe("Fetch a URL");
  });

  it("updateNodeSettings can toggle tool_mode", () => {
    useEditor.getState().setManifests([manifest("http_request")]);
    useEditor.getState().loadGraph({
      ...GRAPH,
      nodes: [{ ...GRAPH.nodes[0], tool_mode: false, tool_name: null, tool_description: "" }],
    });

    useEditor.getState().updateNodeSettings("n1", {
      toolMode: true, toolName: "fetch", toolDescription: "Fetch a URL",
    });

    const node = useEditor.getState().toGraph().nodes[0];
    expect(node.tool_mode).toBe(true);
    expect(node.tool_name).toBe("fetch");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix apps/web test -- store.toolmode`
Expected: FAIL — TypeScript/assertion errors: `tool_mode` not on `GraphNode`, `toolMode` not on `NodeSettingsPatch`, and `toGraph()` output lacks the fields.

- [ ] **Step 3: Add the type fields**

In `apps/web/src/types.ts`, add to `NodeManifest` (after `replacement_id`):

```typescript
  usable_as_tool?: boolean;
```

Add to `GraphNode` (after `timeout_seconds`):

```typescript
  tool_mode?: boolean;
  tool_name?: string | null;
  tool_description?: string;
```

- [ ] **Step 4: Add the store fields + serialization**

In `apps/web/src/editor/store.ts`, add to `NoodleNodeData` (after `timeoutSeconds`):

```typescript
  toolMode?: boolean;
  toolName?: string | null;
  toolDescription?: string;
```

Add to `NodeSettingsPatch` (after `timeoutSeconds`):

```typescript
  toolMode?: boolean;
  toolName?: string | null;
  toolDescription?: string;
```

In `loadGraph`, inside the `data: { … }` object (after `timeoutSeconds:`):

```typescript
          toolMode: Boolean(n.tool_mode),
          toolName: typeof n.tool_name === "string" ? n.tool_name : null,
          toolDescription:
            typeof n.tool_description === "string" ? n.tool_description : "",
```

In `toGraph`, inside the mapped node object (after `timeout_seconds:`):

```typescript
        tool_mode: Boolean(n.data.toolMode),
        tool_name: n.data.toolName ?? null,
        tool_description: n.data.toolDescription ?? "",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm --prefix apps/web test -- store.toolmode`
Expected: PASS (2 tests).

- [ ] **Step 6: Typecheck**

Run: `npm --prefix apps/web run typecheck`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/editor/store.ts apps/web/src/editor/store.toolmode.test.ts
git commit -m "feat(web): tool_mode fields round-trip through the editor store"
```

---

## Task 2: Connection validation accepts tool-mode output

**Files:**
- Modify: `apps/web/src/editor/connectionValidation.ts` (`checkConnectionKinds` ~line 59; `validateConnection` ~line 133)
- Test: `apps/web/src/editor/connectionValidation.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `apps/web/src/editor/connectionValidation.test.ts` (reuse its existing `manifest`/`port`/node-building helpers; if it lacks them, construct minimal `NoodleNode` objects inline as below):

```typescript
import { validateConnection } from "./connectionValidation";
import type { NoodleNode } from "./store";
import type { NodeManifest, PortSpec } from "../types";

function p(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}
function m(id: string, over: Partial<NodeManifest> = {}): NodeManifest {
  return {
    id, name: id, category: "AI", version: "1", description: "", icon: null,
    inputs: [p("input")], outputs: [p("main")], params: [], ...over,
  };
}
function nn(id: string, manifest: NodeManifest, toolMode = false): NoodleNode {
  return {
    id, type: "noodle", position: { x: 0, y: 0 },
    data: { manifest, params: {}, disabled: false, outputsOverride: null,
      onError: "stop", retryOnFail: false, retries: 1, retryWaitSeconds: 0,
      retryBackoff: false, alwaysOutputData: false, timeoutSeconds: null,
      toolMode },
  } as NoodleNode;
}

describe("tool-mode connection", () => {
  const agent = m("ai_agent_v2", { inputs: [p("tool", "ai_tool")] });

  it("accepts a tool-mode node's `tool` output into an agent tool port", () => {
    const nodes = [nn("t", m("http_request"), true), nn("a", agent)];
    const check = validateConnection(nodes, {
      source: "t", sourceHandle: "tool", target: "a", targetHandle: "tool",
    });
    expect(check.ok).toBe(true);
  });

  it("rejects a non-tool-mode node's main output into an agent tool port", () => {
    const nodes = [nn("t", m("http_request"), false), nn("a", agent)];
    const check = validateConnection(nodes, {
      source: "t", sourceHandle: "main", target: "a", targetHandle: "tool",
    });
    expect(check.ok).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix apps/web test -- connectionValidation`
Expected: FAIL — the first case is `ok: false` (tool-mode `tool` output reads as `main`/`any`, rejected by the `ai_tool` target).

- [ ] **Step 3: Implement**

In `apps/web/src/editor/connectionValidation.ts`, add an optional source-kind override to `checkConnectionKinds`:

```typescript
export function checkConnectionKinds(
  source: NodeManifest,
  sourceHandle: string | null | undefined,
  target: NodeManifest,
  targetHandle: string | null | undefined,
  sourceKindOverride?: PortDataKind,
): ConnectionCheck {
  const sourceKind = sourceKindOverride ?? portKind(findOutputPort(source, sourceHandle));
  const targetKind = portKind(findInputPort(target, targetHandle));
```

(Leave the rest of the function body unchanged.)

In `validateConnection`, compute the override from the source node's tool mode and pass it:

```typescript
export function validateConnection(
  nodes: NoodleNode[],
  connection: Connection,
): ConnectionCheck {
  const sourceNode = nodes.find((node) => node.id === connection.source);
  const targetNode = nodes.find((node) => node.id === connection.target);
  if (!sourceNode || !targetNode) {
    return { ok: false, severity: "error", message: "Connection endpoint is missing." };
  }
  // A tool-mode node exposes a single `tool` output of kind ai_tool.
  const sourceKindOverride: PortDataKind | undefined =
    sourceNode.data.toolMode && connection.sourceHandle === "tool"
      ? "ai_tool"
      : undefined;
  return checkConnectionKinds(
    sourceNode.data.manifest,
    connection.sourceHandle,
    targetNode.data.manifest,
    connection.targetHandle,
    sourceKindOverride,
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix apps/web test -- connectionValidation`
Expected: PASS (existing cases + the 2 new ones).

- [ ] **Step 5: Typecheck + full vitest (no regressions)**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test`
Expected: typecheck exit 0; all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/connectionValidation.ts apps/web/src/editor/connectionValidation.test.ts
git commit -m "feat(web): connection validation accepts tool-mode node's ai_tool output"
```

---

## Notes for the implementer

- **Tool fields are optional** on `NoodleNodeData` so the many node-creation sites (`addNode`, paste, AI draft) need no change — `undefined` serializes as `false`/`null`/`""` in `toGraph`.
- **`updateNodeSettings` is reused** (it spreads the patch onto `node.data`), so Phase 2b's toggle just calls `updateNodeSettings(id, { toolMode: true, … })`.
- **The `tool` output handle itself** (rendering it on the node card) and the toggle UI are Phase 2b — this plan only makes the data + validation correct so those build on a solid base.
- Commit only the listed files per task; never `git add -A` (a parallel effort is editing other paths).
