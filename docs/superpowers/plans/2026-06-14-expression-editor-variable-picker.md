# Expression Editor & Variable Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collapsible variable sidebar, floating `$` picker popover, expression parts breakdown, format/history buttons, and per-field `$` buttons to the NDV and Inspector so users never have to type `$node["id"].main.field` from memory.

**Architecture:** A pure `upstreamFields.ts` utility derives draggable `UpstreamField[]` from the store's `runOutputs + edges + nodes`. A shared `VariablePickerPopover` component uses it. The `ExpressionEditorModal` gains a left sidebar (node dropdown + field list) and the popover; both call sites (NDV + Inspector) gain a `$` button that opens the popover inline.

**Tech Stack:** React, TypeScript, Zustand (via `useEditor`), `@xyflow/react` Edge type, vitest + testing-library.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `apps/web/src/editor/node-details/upstreamFields.ts` | **Create** | Pure utility: flatten run output into draggable fields, BFS upstream graph, search |
| `apps/web/src/editor/node-details/upstreamFields.test.ts` | **Create** | Unit tests for all utility functions |
| `apps/web/src/editor/VariablePickerPopover.tsx` | **Create** | Shared floating popover component — search, click/drag to insert |
| `apps/web/src/editor/NodeDetails.tsx` | **Modify** | Add sidebar + parts bar + format + history to `ExpressionEditorModal`; add `nodeId` to `ParamField` |
| `apps/web/src/editor/NDVPanels.tsx` | **Modify** | Add `$` button on string/expression fields in `ParametersTab` |
| `apps/web/src/index.css` | **Modify** | Styles for sidebar, popover, parts bar, history dropdown |

---

## Task 1 — `upstreamFields.ts` utility

**Files:**
- Create: `apps/web/src/editor/node-details/upstreamFields.ts`
- Create: `apps/web/src/editor/node-details/upstreamFields.test.ts`

- [ ] **Step 1.1 — Write the tests first**

Create `apps/web/src/editor/node-details/upstreamFields.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  buildNodeExpression,
  flattenOutputFields,
  formatValuePreview,
  getUpstreamNodeIds,
  getUpstreamNodes,
  inferType,
  searchUpstreamFields,
} from "./upstreamFields";

describe("inferType", () => {
  it("returns str for string", () => expect(inferType("hello")).toBe("str"));
  it("returns int for whole number", () => expect(inferType(42)).toBe("int"));
  it("returns float for decimal", () => expect(inferType(3.14)).toBe("float"));
  it("returns bool for boolean", () => expect(inferType(true)).toBe("bool"));
  it("returns list for array", () => expect(inferType([1, 2])).toBe("list"));
  it("returns obj for plain object", () => expect(inferType({ a: 1 })).toBe("obj"));
  it("returns null for null", () => expect(inferType(null)).toBe("null"));
  it("returns null for undefined", () => expect(inferType(undefined)).toBe("null"));
});

describe("formatValuePreview", () => {
  it("truncates long strings to 12 chars", () => {
    expect(formatValuePreview("hello world foo bar")).toBe('"hello world…"');
  });
  it("shows short strings quoted", () => {
    expect(formatValuePreview("Alice")).toBe('"Alice"');
  });
  it("formats arrays as item count", () => {
    expect(formatValuePreview([1, 2, 3])).toBe("[3 items]");
  });
  it("formats objects with first two keys", () => {
    expect(formatValuePreview({ a: 1, b: 2, c: 3 })).toBe("{a, b…}");
  });
  it("formats numbers directly", () => {
    expect(formatValuePreview(42)).toBe("42");
  });
});

describe("buildNodeExpression", () => {
  it("wraps path in double-brace node syntax", () => {
    expect(buildNodeExpression("abc123", "name")).toBe(
      '{{ $node["abc123"].main.name }}',
    );
  });
  it("handles nested paths", () => {
    expect(buildNodeExpression("abc123", "orders[0].id")).toBe(
      '{{ $node["abc123"].main.orders[0].id }}',
    );
  });
});

describe("flattenOutputFields", () => {
  it("flattens a flat object to top-level fields", () => {
    const fields = flattenOutputFields({ name: "Alice", id: 42 }, "node1");
    expect(fields).toHaveLength(2);
    expect(fields[0].path).toBe("name");
    expect(fields[0].type).toBe("str");
    expect(fields[0].expression).toBe('{{ $node["node1"].main.name }}');
    expect(fields[1].path).toBe("id");
    expect(fields[1].type).toBe("int");
  });

  it("marks objects and arrays as expandable", () => {
    const fields = flattenOutputFields({ orders: [{ id: 1 }], meta: { x: 1 } }, "n");
    const orders = fields.find((f) => f.path === "orders")!;
    const meta = fields.find((f) => f.path === "meta")!;
    expect(orders.isExpandable).toBe(true);
    expect(meta.isExpandable).toBe(true);
  });

  it("flattens one level into nested paths", () => {
    const fields = flattenOutputFields({ user: { name: "Bob", age: 30 } }, "n");
    expect(fields.some((f) => f.path === "user.name")).toBe(true);
    expect(fields.some((f) => f.path === "user.age")).toBe(true);
  });

  it("caps array expansion at 5 items", () => {
    const arr = [1, 2, 3, 4, 5, 6, 7];
    const fields = flattenOutputFields({ items: arr }, "n");
    const arrayChildren = fields.filter((f) => f.path.startsWith("items["));
    expect(arrayChildren.length).toBeLessThanOrEqual(5);
  });

  it("does not exceed depth 3", () => {
    const deep = { a: { b: { c: { d: "too deep" } } } };
    const fields = flattenOutputFields(deep, "n");
    expect(fields.every((f) => f.path.split(".").length <= 3)).toBe(true);
  });

  it("returns empty array for non-object/array values", () => {
    expect(flattenOutputFields("string", "n")).toHaveLength(0);
    expect(flattenOutputFields(42, "n")).toHaveLength(0);
    expect(flattenOutputFields(null, "n")).toHaveLength(0);
  });
});

describe("getUpstreamNodeIds", () => {
  const edges = [
    { source: "a", target: "b" },
    { source: "b", target: "c" },
    { source: "x", target: "d" },
  ];

  it("finds direct upstream nodes", () => {
    const ids = getUpstreamNodeIds("b", edges as never);
    expect(ids).toContain("a");
  });

  it("finds transitive upstream nodes via BFS", () => {
    const ids = getUpstreamNodeIds("c", edges as never);
    expect(ids).toContain("b");
    expect(ids).toContain("a");
  });

  it("excludes nodes from disconnected branches", () => {
    const ids = getUpstreamNodeIds("c", edges as never);
    expect(ids).not.toContain("x");
    expect(ids).not.toContain("d");
  });

  it("returns empty array for a node with no upstream", () => {
    expect(getUpstreamNodeIds("a", edges as never)).toHaveLength(0);
  });
});

describe("getUpstreamNodes", () => {
  const nodes = [
    { id: "a", data: { label: "HTTP Request", manifest: { name: "HTTP Request" }, params: {} } },
    { id: "b", data: { manifest: { name: "JSON Parse" }, params: {} } },
  ] as never;
  const edges = [{ source: "a", target: "b" }] as never;

  it("returns upstream node with label from data.label", () => {
    const result = getUpstreamNodes("b", nodes, edges, {
      a: { main: { status: 200 } },
    });
    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("HTTP Request");
    expect(result[0].id).toBe("a");
  });

  it("falls back to manifest.name when label is absent", () => {
    const result = getUpstreamNodes("b", nodes, edges, { a: { main: { x: 1 } } });
    expect(result[0].label).toBe("HTTP Request");
  });

  it("unwraps the main port from runOutputs", () => {
    const result = getUpstreamNodes("b", nodes, edges, {
      a: { main: { name: "Alice" } },
    });
    expect(result[0].fields.some((f) => f.path === "name")).toBe(true);
  });

  it("returns empty fields when node has not run", () => {
    const result = getUpstreamNodes("b", nodes, edges, {});
    expect(result[0].fields).toHaveLength(0);
  });
});

describe("searchUpstreamFields", () => {
  const upstream = [
    {
      id: "a",
      label: "Node A",
      fields: [
        { path: "email", type: "str" as const, valuePreview: '"alice@co.com"', expression: '{{ $node["a"].main.email }}', isExpandable: false },
        { path: "id", type: "int" as const, valuePreview: "42", expression: '{{ $node["a"].main.id }}', isExpandable: false },
      ],
    },
  ];

  it("returns all nodes when query is empty", () => {
    expect(searchUpstreamFields(upstream, "")).toHaveLength(1);
  });

  it("filters by field path", () => {
    const result = searchUpstreamFields(upstream, "email");
    expect(result[0].fields).toHaveLength(1);
    expect(result[0].fields[0].path).toBe("email");
  });

  it("filters by value preview content", () => {
    const result = searchUpstreamFields(upstream, "alice");
    expect(result[0].fields).toHaveLength(1);
  });

  it("removes nodes with no matching fields", () => {
    expect(searchUpstreamFields(upstream, "zzznomatch")).toHaveLength(0);
  });
});
```

- [ ] **Step 1.2 — Run tests to confirm they all fail**

```bash
cd apps/web && npx vitest run src/editor/node-details/upstreamFields.test.ts
```

Expected: All tests fail with "Cannot find module './upstreamFields'".

- [ ] **Step 1.3 — Implement `upstreamFields.ts`**

Create `apps/web/src/editor/node-details/upstreamFields.ts`:

```typescript
import type { Edge } from "@xyflow/react";
import type { NoodleNode } from "../store";

export interface UpstreamField {
  path: string;
  type: "str" | "int" | "float" | "bool" | "list" | "obj" | "null" | "unknown";
  valuePreview: string;
  expression: string;
  isExpandable: boolean;
}

export interface UpstreamNode {
  id: string;
  label: string;
  fields: UpstreamField[];
}

export function inferType(value: unknown): UpstreamField["type"] {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") return Number.isInteger(value) ? "int" : "float";
  if (typeof value === "string") return "str";
  if (Array.isArray(value)) return "list";
  if (typeof value === "object") return "obj";
  return "unknown";
}

export function formatValuePreview(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") {
    return `"${value.slice(0, 12)}${value.length > 12 ? "…" : ""}"`;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `[${value.length} items]`;
  if (typeof value === "object") {
    const keys = Object.keys(value as object);
    return `{${keys.slice(0, 2).join(", ")}${keys.length > 2 ? "…" : ""}}`;
  }
  return "";
}

export function buildNodeExpression(nodeId: string, path: string): string {
  return `{{ $node["${nodeId}"].main.${path} }}`;
}

export function flattenOutputFields(
  value: unknown,
  nodeId: string,
  path = "",
  depth = 0,
): UpstreamField[] {
  if (depth >= 3) return [];
  const fields: UpstreamField[] = [];

  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
      const fieldPath = path ? `${path}.${key}` : key;
      const isExpandable = val !== null && typeof val === "object";
      fields.push({
        path: fieldPath,
        type: inferType(val),
        valuePreview: formatValuePreview(val),
        expression: buildNodeExpression(nodeId, fieldPath),
        isExpandable,
      });
      if (isExpandable && depth < 2) {
        fields.push(...flattenOutputFields(val, nodeId, fieldPath, depth + 1));
      }
    }
  } else if (Array.isArray(value)) {
    const items = value.slice(0, 5);
    for (let i = 0; i < items.length; i++) {
      const fieldPath = path ? `${path}[${i}]` : `[${i}]`;
      const val = items[i];
      const isExpandable = val !== null && typeof val === "object";
      fields.push({
        path: fieldPath,
        type: inferType(val),
        valuePreview: formatValuePreview(val),
        expression: buildNodeExpression(nodeId, fieldPath),
        isExpandable,
      });
    }
  }

  return fields;
}

export function getUpstreamNodeIds(nodeId: string, edges: Edge[]): string[] {
  const result: string[] = [];
  const visited = new Set<string>([nodeId]);
  const queue = [nodeId];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const edge of edges) {
      if (edge.target === current && !visited.has(edge.source)) {
        visited.add(edge.source);
        result.push(edge.source);
        queue.push(edge.source);
      }
    }
  }
  return result;
}

export function getUpstreamNodes(
  nodeId: string,
  nodes: NoodleNode[],
  edges: Edge[],
  runOutputs: Record<string, unknown>,
): UpstreamNode[] {
  const upstreamIds = getUpstreamNodeIds(nodeId, edges);
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const result: UpstreamNode[] = [];

  for (const id of upstreamIds) {
    const node = nodeById.get(id);
    if (!node) continue;
    const label = node.data.label ?? node.data.manifest.name;
    const raw = runOutputs[id];
    const mainOutput =
      raw && typeof raw === "object"
        ? (raw as Record<string, unknown>)["main"]
        : undefined;
    const fields =
      mainOutput !== undefined ? flattenOutputFields(mainOutput, id) : [];
    result.push({ id, label, fields });
  }

  return result;
}

export function searchUpstreamFields(
  nodes: UpstreamNode[],
  query: string,
): UpstreamNode[] {
  if (!query.trim()) return nodes;
  const lower = query.toLowerCase();
  return nodes
    .map((n) => ({
      ...n,
      fields: n.fields.filter(
        (f) =>
          f.path.toLowerCase().includes(lower) ||
          f.valuePreview.toLowerCase().includes(lower),
      ),
    }))
    .filter((n) => n.fields.length > 0);
}
```

- [ ] **Step 1.4 — Run tests and confirm all pass**

```bash
cd apps/web && npx vitest run src/editor/node-details/upstreamFields.test.ts
```

Expected: All tests pass.

- [ ] **Step 1.5 — Commit**

```bash
git add apps/web/src/editor/node-details/upstreamFields.ts apps/web/src/editor/node-details/upstreamFields.test.ts
git commit -m "feat(editor): upstream field utility for variable picker"
```

---

## Task 2 — `VariablePickerPopover` component

**Files:**
- Create: `apps/web/src/editor/VariablePickerPopover.tsx`

- [ ] **Step 2.1 — Create the component**

Create `apps/web/src/editor/VariablePickerPopover.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";

import {
  getUpstreamNodes,
  searchUpstreamFields,
} from "./node-details/upstreamFields";
import { useEditor } from "./store";

interface Props {
  nodeId: string;
  onInsert: (expression: string) => void;
  onClose: () => void;
}

export function VariablePickerPopover({ nodeId, onInsert, onClose }: Props) {
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const allUpstream = getUpstreamNodes(nodeId, nodes, edges, runOutputs);
  const filtered = searchUpstreamFields(allUpstream, query);

  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [onClose]);

  function startDrag(e: React.DragEvent<HTMLElement>, expression: string) {
    e.dataTransfer.setData("text/plain", expression);
    e.dataTransfer.setData("application/x-noodle-expression", expression);
    e.dataTransfer.effectAllowed = "copy";
    const ghost = document.createElement("div");
    ghost.className = "expr-drag-ghost";
    ghost.textContent = expression;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 12, 12);
    window.setTimeout(() => ghost.remove(), 0);
  }

  return (
    <div
      className="var-picker-popover"
      ref={containerRef}
      role="dialog"
      aria-label="Pick a variable"
    >
      <div className="var-picker-head">
        <span className="var-picker-icon">$</span>
        <span>Pick a variable</span>
      </div>
      <input
        ref={searchRef}
        className="var-picker-search"
        type="search"
        placeholder="Search all nodes…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="var-picker-body">
        {allUpstream.length === 0 && (
          <p className="var-picker-empty">
            Run the workflow first to see upstream data here.
          </p>
        )}
        {allUpstream.length > 0 && filtered.length === 0 && (
          <p className="var-picker-empty">No fields match "{query}".</p>
        )}
        {filtered.map((upNode) => (
          <div key={upNode.id} className="var-picker-node">
            <div className="var-picker-node-head">{upNode.label}</div>
            {upNode.fields.map((field) => (
              <div
                key={field.path}
                className="var-picker-field"
                draggable
                onDragStart={(e) => startDrag(e, field.expression)}
                onClick={() => {
                  onInsert(field.expression);
                  onClose();
                }}
              >
                <span className="var-picker-grip">⠿</span>
                <span className="var-picker-name">{field.path}</span>
                <span className="var-picker-type">{field.type}</span>
                <button
                  type="button"
                  className="var-picker-copy"
                  title={`Copy ${field.expression}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void navigator.clipboard.writeText(field.expression);
                  }}
                >
                  copy
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2.2 — Commit**

```bash
git add apps/web/src/editor/VariablePickerPopover.tsx
git commit -m "feat(editor): VariablePickerPopover shared component"
```

---

## Task 3 — `ExpressionEditorModal` sidebar

This task adds the collapsible left sidebar with node dropdown and draggable field list to the existing `ExpressionEditorModal` in `NodeDetails.tsx`. It also threads `nodeId` through `ParamField`.

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

- [ ] **Step 3.1 — Add `nodeId` prop to `ParamField` and forward to `ExpressionEditorModal`**

In `NodeDetails.tsx`, find `export function ParamField({` (around line 2005). Change its props interface:

```tsx
// BEFORE
export function ParamField({
  spec,
  value,
  onChange,
  credentialContext,
  exprContext,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  credentialContext?: Record<string, unknown>;
  exprContext?: ExprContext;
}) {

// AFTER
export function ParamField({
  spec,
  value,
  onChange,
  credentialContext,
  exprContext,
  nodeId,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  credentialContext?: Record<string, unknown>;
  exprContext?: ExprContext;
  nodeId?: string;
}) {
```

Then find where `ExpressionEditorModal` is rendered inside `ParamField` (around line 2166):

```tsx
// BEFORE
{expanderOpen && (
  <ExpressionEditorModal
    label={spec.name}
    value={current}
    onChange={onChange}
    ctx={exprContext}
    onClose={() => setExpanderOpen(false)}
  />
)}

// AFTER
{expanderOpen && (
  <ExpressionEditorModal
    label={spec.name}
    value={current}
    onChange={onChange}
    ctx={exprContext}
    nodeId={nodeId}
    onClose={() => setExpanderOpen(false)}
  />
)}
```

- [ ] **Step 3.2 — Pass `nodeId` to `ParamField` from the inspector in `NodeDetails.tsx`**

In `NodeDetails.tsx`, find where `ParamField` is called in the inspector render (around line 3377). Change:

```tsx
// BEFORE
<ParamField
  spec={renderSpec}
  value={value}
  onChange={(v) => setParam(spec.name, v)}
  credentialContext={params}
/>

// AFTER
<ParamField
  spec={renderSpec}
  value={value}
  onChange={(v) => setParam(spec.name, v)}
  credentialContext={params}
  nodeId={node.id}
/>
```

- [ ] **Step 3.3 — Add `nodeId` prop and sidebar to `ExpressionEditorModal`**

Find `function ExpressionEditorModal({` (around line 1401). Replace the full function signature and internal body with the version below. The key changes are:
- Add `nodeId?: string` prop
- Add sidebar state (`sidebarCollapsed`, `selectedUpstreamId`)
- Wrap the existing two-pane layout inside a flex row with the new sidebar

```tsx
// Replace ExpressionEditorModal signature
function ExpressionEditorModal({
  label,
  value,
  onChange,
  ctx,
  nodeId,
  onClose,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  ctx?: ExprContext;
  nodeId?: string;
  onClose: () => void;
}) {
```

After the existing state declarations (after `const taRef = useRef...`), add:

```tsx
  // Sidebar state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem("noodle_expr_sidebar_collapsed") === "1"; } catch { return false; }
  });
  const allNodes = useEditor((s) => s.nodes);
  const allEdges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const upstreamNodes = nodeId
    ? getUpstreamNodes(nodeId, allNodes, allEdges, runOutputs)
    : [];
  const [selectedUpstreamId, setSelectedUpstreamId] = useState<string | null>(
    upstreamNodes[0]?.id ?? null,
  );
  const selectedUpstreamNode = upstreamNodes.find((n) => n.id === selectedUpstreamId) ?? upstreamNodes[0];

  function toggleSidebar() {
    setSidebarCollapsed((c) => {
      const next = !c;
      try { localStorage.setItem("noodle_expr_sidebar_collapsed", next ? "1" : "0"); } catch { /* */ }
      return next;
    });
  }

  function startFieldDrag(e: React.DragEvent<HTMLElement>, expression: string) {
    e.dataTransfer.setData("text/plain", expression);
    e.dataTransfer.setData("application/x-noodle-expression", expression);
    e.dataTransfer.effectAllowed = "copy";
    const ghost = document.createElement("div");
    ghost.className = "expr-drag-ghost";
    ghost.textContent = expression;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 12, 12);
    window.setTimeout(() => ghost.remove(), 0);
  }
```

You also need to add `getUpstreamNodes` to the imports at the top of NodeDetails.tsx. Add:

```tsx
import { getUpstreamNodes } from "./node-details/upstreamFields";
```

Now find the return statement of `ExpressionEditorModal` — the `<div className="expr-modal-body">` wrapper (around line 1546). Replace the body structure to wrap the existing panes inside a flex row with the sidebar on the left:

```tsx
        <div className="expr-modal-body">
          {/* ── LEFT SIDEBAR ── */}
          {upstreamNodes.length > 0 && (
            <aside className={`expr-sidebar${sidebarCollapsed ? " expr-sidebar--collapsed" : ""}`}>
              <div className="expr-sidebar-head">
                {!sidebarCollapsed && <span className="expr-sidebar-title">Variables</span>}
                <button
                  type="button"
                  className="expr-sidebar-collapse"
                  title={sidebarCollapsed ? "Expand variable panel" : "Collapse variable panel"}
                  onClick={toggleSidebar}
                >
                  {sidebarCollapsed ? "›" : "‹"}
                </button>
              </div>
              {!sidebarCollapsed && (
                <div className="expr-sidebar-body">
                  {upstreamNodes.length > 1 && (
                    <div className="expr-sidebar-node-select">
                      <select
                        className="expr-sidebar-dropdown"
                        value={selectedUpstreamId ?? ""}
                        onChange={(e) => setSelectedUpstreamId(e.target.value)}
                      >
                        {upstreamNodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  {upstreamNodes.length === 1 && (
                    <div className="expr-sidebar-node-single">
                      {upstreamNodes[0].label}
                    </div>
                  )}
                  <div className="expr-sidebar-fields">
                    {selectedUpstreamNode?.fields.length === 0 && (
                      <p className="expr-sidebar-empty">No output data yet.</p>
                    )}
                    {selectedUpstreamNode?.fields.map((field) => (
                      <div
                        key={field.path}
                        className="expr-sidebar-field"
                        draggable
                        onDragStart={(e) => startFieldDrag(e, field.expression)}
                        title={`Drag to insert ${field.expression}`}
                      >
                        <span className="expr-sidebar-grip">⠿</span>
                        <span className="expr-sidebar-name">{field.path}</span>
                        <span className="expr-sidebar-type">{field.type}</span>
                        <span className="expr-sidebar-preview">{field.valuePreview}</span>
                      </div>
                    ))}
                  </div>
                  <div className="expr-sidebar-meta">
                    {[
                      { path: "$run.id", expr: "{{ $run.id }}" },
                      { path: "$run.status", expr: "{{ $run.status }}" },
                      { path: "$env.KEY", expr: "{{ $env.KEY }}" },
                    ].map((m) => (
                      <div
                        key={m.path}
                        className="expr-sidebar-field expr-sidebar-field--meta"
                        draggable
                        onDragStart={(e) => startFieldDrag(e, m.expr)}
                        title={`Drag to insert ${m.expr}`}
                      >
                        <span className="expr-sidebar-grip">⠿</span>
                        <span className="expr-sidebar-name">{m.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </aside>
          )}

          {/* ── EXISTING TWO PANES (unchanged) ── */}
          <section className="expr-modal-pane">
            ... (existing Expression pane content unchanged)
          </section>
          <section className="expr-modal-pane">
            ... (existing Result pane content unchanged)
          </section>
        </div>
```

> **Note:** Keep the two existing `<section className="expr-modal-pane">` blocks exactly as they are — only wrap them with the new sidebar. The full edited modal body is shown precisely in the diff below. Apply this as a targeted edit to `NodeDetails.tsx` by replacing the `<div className="expr-modal-body">` opening and adding the sidebar before the first `<section className="expr-modal-pane">`.

Specifically, find this exact string in `NodeDetails.tsx`:

```tsx
        <div className="expr-modal-body">
          <section className="expr-modal-pane">
```

And replace with:

```tsx
        <div className="expr-modal-body">
          {upstreamNodes.length > 0 && (
            <aside className={`expr-sidebar${sidebarCollapsed ? " expr-sidebar--collapsed" : ""}`}>
              <div className="expr-sidebar-head">
                {!sidebarCollapsed && <span className="expr-sidebar-title">Variables</span>}
                <button
                  type="button"
                  className="expr-sidebar-collapse"
                  title={sidebarCollapsed ? "Expand variable panel" : "Collapse variable panel"}
                  onClick={toggleSidebar}
                >
                  {sidebarCollapsed ? "›" : "‹"}
                </button>
              </div>
              {!sidebarCollapsed && (
                <div className="expr-sidebar-body">
                  {upstreamNodes.length > 1 && (
                    <div className="expr-sidebar-node-select">
                      <select
                        className="expr-sidebar-dropdown"
                        value={selectedUpstreamId ?? ""}
                        onChange={(e) => setSelectedUpstreamId(e.target.value)}
                      >
                        {upstreamNodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  {upstreamNodes.length === 1 && (
                    <div className="expr-sidebar-node-single">{upstreamNodes[0].label}</div>
                  )}
                  <div className="expr-sidebar-fields">
                    {(selectedUpstreamNode?.fields.length ?? 0) === 0 && (
                      <p className="expr-sidebar-empty">No output data yet.</p>
                    )}
                    {selectedUpstreamNode?.fields.map((field) => (
                      <div
                        key={field.path}
                        className="expr-sidebar-field"
                        draggable
                        onDragStart={(e) => startFieldDrag(e, field.expression)}
                        title={`Drag to insert ${field.expression}`}
                      >
                        <span className="expr-sidebar-grip">⠿</span>
                        <span className="expr-sidebar-name">{field.path}</span>
                        <span className="expr-sidebar-type">{field.type}</span>
                        <span className="expr-sidebar-preview">{field.valuePreview}</span>
                      </div>
                    ))}
                  </div>
                  <div className="expr-sidebar-meta">
                    {[
                      { path: "$run.id", expr: "{{ $run.id }}" },
                      { path: "$run.status", expr: "{{ $run.status }}" },
                      { path: "$env.KEY", expr: "{{ $env.KEY }}" },
                    ].map((m) => (
                      <div
                        key={m.path}
                        className="expr-sidebar-field expr-sidebar-field--meta"
                        draggable
                        onDragStart={(e) => startFieldDrag(e, m.expr)}
                        title={`Drag to insert ${m.expr}`}
                      >
                        <span className="expr-sidebar-grip">⠿</span>
                        <span className="expr-sidebar-name">{m.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </aside>
          )}
          <section className="expr-modal-pane">
```

- [ ] **Step 3.4 — Verify TypeScript compiles**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | head -30
```

Expected: No errors related to the new props. Fix any type errors before proceeding.

- [ ] **Step 3.5 — Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx apps/web/src/editor/node-details/upstreamFields.ts
git commit -m "feat(editor): expression modal sidebar with node dropdown and draggable fields"
```

---

## Task 4 — Parts breakdown bar

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

The `ExpressionEditorModal` already computes `state.parts: PreviewPart[]`. Each entry with `kind: "expr"` has a `raw` string (the `{{ ... }}` source) and `value` (the resolved result). Add a bar below the expression textarea that shows one chip per expression part.

- [ ] **Step 4.1 — Add the parts bar below the textarea in `ExpressionEditorModal`**

Find the `</div>` that closes `<div className="expr-modal-editor-wrap">` (around line 1577). After that closing `</div>` and before the closing `</section>` of the expression pane, add:

```tsx
            {state.parts && state.parts.some((p) => p.kind === "expr") && (
              <div className="expr-parts-bar">
                <span className="expr-parts-label">Resolved:</span>
                {state.parts
                  .filter((p) => p.kind === "expr" || p.kind === "error")
                  .map((p, i) =>
                    p.kind === "error" ? (
                      <span key={i} className="expr-part-chip expr-part-chip--error" title={p.error}>
                        <span className="expr-part-chip-raw">{p.raw}</span>
                        <span className="expr-part-chip-arrow">→</span>
                        <span className="expr-part-chip-val">⚠ error</span>
                      </span>
                    ) : (
                      <span key={i} className="expr-part-chip">
                        <span className="expr-part-chip-raw">
                          {p.raw.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "").slice(0, 20)}
                        </span>
                        <span className="expr-part-chip-arrow">→</span>
                        <span className="expr-part-chip-val">
                          {p.kind === "expr"
                            ? String(p.value === null || p.value === undefined ? "null" : p.value).slice(0, 18)
                            : ""}
                        </span>
                      </span>
                    ),
                  )}
              </div>
            )}
```

The exact insertion point is after the autocomplete `<ul>` closing tag and before `</section>` of the expression pane. Find:

```tsx
              )}
            </div>
          </section>
          <section className="expr-modal-pane">
            <div className="expr-modal-pane-head">
              <span>Result</span>
```

And replace with:

```tsx
              )}
            </div>
            {state.parts && state.parts.some((p) => p.kind === "expr") && (
              <div className="expr-parts-bar">
                <span className="expr-parts-label">Resolved:</span>
                {state.parts
                  .filter((p) => p.kind === "expr" || p.kind === "error")
                  .map((p, i) =>
                    p.kind === "error" ? (
                      <span key={i} className="expr-part-chip expr-part-chip--error" title={p.error}>
                        <span className="expr-part-chip-raw">{p.raw}</span>
                        <span className="expr-part-chip-arrow">→</span>
                        <span className="expr-part-chip-val">⚠ error</span>
                      </span>
                    ) : (
                      <span key={i} className="expr-part-chip">
                        <span className="expr-part-chip-raw">
                          {p.raw.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "").slice(0, 20)}
                        </span>
                        <span className="expr-part-chip-arrow">→</span>
                        <span className="expr-part-chip-val">
                          {String(p.value === null || p.value === undefined ? "null" : p.value).slice(0, 18)}
                        </span>
                      </span>
                    ),
                  )}
              </div>
            )}
          </section>
          <section className="expr-modal-pane">
            <div className="expr-modal-pane-head">
              <span>Result</span>
```

- [ ] **Step 4.2 — Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(editor): expression parts breakdown bar shows each {{ }} resolved inline"
```

---

## Task 5 — Format and History buttons

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

- [ ] **Step 5.1 — Add helper functions above `ExpressionEditorModal`**

Find the comment `/** n8n-style expand modal` (around line 1398). Directly before it, add:

```tsx
const EXPR_HISTORY_KEY = "noodle_expr_history";

function readExprHistory(fieldKey: string): string[] {
  try {
    const data = JSON.parse(localStorage.getItem(EXPR_HISTORY_KEY) ?? "{}") as Record<string, unknown>;
    const arr = data[fieldKey];
    return Array.isArray(arr) ? (arr as string[]) : [];
  } catch {
    return [];
  }
}

function appendExprHistory(fieldKey: string, value: string): void {
  if (!value.trim()) return;
  try {
    const data = JSON.parse(localStorage.getItem(EXPR_HISTORY_KEY) ?? "{}") as Record<string, string[]>;
    const existing = Array.isArray(data[fieldKey]) ? data[fieldKey] : [];
    const deduped = [value, ...existing.filter((v) => v !== value)].slice(0, 10);
    localStorage.setItem(EXPR_HISTORY_KEY, JSON.stringify({ ...data, [fieldKey]: deduped }));
  } catch {
    /* ignore */
  }
}

function formatExpression(value: string): string {
  // Normalise {{ expr }} spacing — one space inside each brace pair.
  return value
    .replace(/\{\{\s*([\s\S]*?)\s*\}\}/g, (_, inner) => `{{ ${inner.trim()} }}`)
    .trim();
}
```

- [ ] **Step 5.2 — Add history state and `onClose` hook in `ExpressionEditorModal`**

Inside `ExpressionEditorModal`, after the existing state declarations, add:

```tsx
  const historyKey = `${nodeId ?? ""}:${label}`;
  const [historyOpen, setHistoryOpen] = useState(false);
  const history = readExprHistory(historyKey);

  // Save to history when the modal closes with a non-empty value.
  const latestValue = useRef(value);
  latestValue.current = value;
  useEffect(() => {
    return () => {
      appendExprHistory(historyKey, latestValue.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyKey]);
```

- [ ] **Step 5.3 — Add Format and History buttons to the modal header**

Find the existing modal header in `ExpressionEditorModal`:

```tsx
        <header className="modal-head">
          <h2 id="expr-modal-title">
            Editing <span className="expr-modal-label">{label}</span>
          </h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
```

Replace with:

```tsx
        <header className="modal-head">
          <h2 id="expr-modal-title">
            Editing <span className="expr-modal-label">{label}</span>
          </h2>
          <div className="expr-modal-header-actions">
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => onChange(formatExpression(value))}
              title="Normalise {{ }} spacing"
            >
              Format
            </button>
            <div className="expr-history-wrap">
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setHistoryOpen((o) => !o)}
                title="Recent values for this field"
                disabled={history.length === 0}
              >
                History {history.length > 0 ? `(${history.length})` : ""}
              </button>
              {historyOpen && history.length > 0 && (
                <ul className="expr-history-dropdown">
                  {history.map((entry, i) => (
                    <li key={i}>
                      <button
                        type="button"
                        onClick={() => {
                          onChange(entry);
                          setHistoryOpen(false);
                        }}
                      >
                        {entry.slice(0, 60)}{entry.length > 60 ? "…" : ""}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </header>
```

- [ ] **Step 5.4 — Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(editor): format button and expression history in expanded editor"
```

---

## Task 6 — Floating popover + auto-open on `$` in `ExpressionEditorModal`

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

- [ ] **Step 6.1 — Add popover state and insertion helper**

Inside `ExpressionEditorModal`, after the history state from Task 5, add:

```tsx
  const [pickerOpen, setPickerOpen] = useState(false);
  // Tracks cursor position at the moment $ was typed so we can splice it out.
  const dollarPosRef = useRef<number | null>(null);

  function insertExpression(expression: string) {
    const ta = taRef.current;
    const pos = ta?.selectionStart ?? value.length;

    if (dollarPosRef.current !== null) {
      // Auto-opened by typing $: replace the $ with the expression inner part.
      const dollarPos = dollarPosRef.current;
      dollarPosRef.current = null;
      const inner = expression.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "");
      onChange(value.slice(0, dollarPos - 1) + inner + value.slice(dollarPos));
      return;
    }

    // Manual open: smart-wrap based on whether cursor is inside {{ }}.
    const before = value.slice(0, pos);
    const opens = (before.match(/\{\{/g) ?? []).length;
    const closes = (before.match(/\}\}/g) ?? []).length;
    const insideExpr = opens > closes;
    const toInsert = insideExpr
      ? expression.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "")
      : expression;
    onChange(value.slice(0, pos) + toInsert + value.slice(pos));
    setPickerOpen(false);
  }
```

- [ ] **Step 6.2 — Update the `onChange` in `HighlightedTextarea` to detect `$` and open the popover**

Find the `HighlightedTextarea` inside `ExpressionEditorModal` (around line 1555):

```tsx
              <HighlightedTextarea
                className="expr-modal-editor"
                value={value}
                onChange={(v) => { onChange(v); updateSuggestions(v); }}
                autoFocus
                taRef={taRef}
                onKeyDown={handleKeyDown}
              />
```

Replace with:

```tsx
              <HighlightedTextarea
                className="expr-modal-editor"
                value={value}
                onChange={(v) => {
                  onChange(v);
                  updateSuggestions(v);
                  // Auto-open picker when user types $ inside {{ }}
                  const pos = taRef.current?.selectionStart ?? v.length;
                  const before = v.slice(0, pos);
                  const opens = (before.match(/\{\{/g) ?? []).length;
                  const closes = (before.match(/\}\}/g) ?? []).length;
                  const insideExpr = opens > closes;
                  if (insideExpr && v[pos - 1] === "$") {
                    dollarPosRef.current = pos;
                    setPickerOpen(true);
                  }
                }}
                autoFocus
                taRef={taRef}
                onKeyDown={(e) => {
                  if (e.key === "Escape" && pickerOpen) {
                    setPickerOpen(false);
                    dollarPosRef.current = null;
                    return;
                  }
                  handleKeyDown(e);
                }}
              />
```

- [ ] **Step 6.3 — Add the `$` button to the expression pane header and render the popover**

Find in `ExpressionEditorModal` the expression pane head (around line 1548):

```tsx
            <div className="expr-modal-pane-head">
              <span>Expression</span>
              <span className="muted expr-modal-hint">
                Anything inside <code>{"{{ }}"}</code> is evaluated
              </span>
            </div>
```

Replace with:

```tsx
            <div className="expr-modal-pane-head">
              <span>Expression</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="muted expr-modal-hint">
                  Anything inside <code>{"{{ }}"}</code> is evaluated
                </span>
                {nodeId && (
                  <button
                    type="button"
                    className="btn btn-xs expr-pick-btn"
                    title="Pick a variable (Ctrl+Space)"
                    onClick={() => { dollarPosRef.current = null; setPickerOpen((o) => !o); }}
                  >
                    $ Pick variable
                  </button>
                )}
              </div>
            </div>
```

Then add `Ctrl+Space` keyboard shortcut by updating `handleKeyDown` (find the existing function, around line 1456):

```tsx
  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Ctrl+Space opens the variable picker
    if (e.key === " " && (e.ctrlKey || e.metaKey) && nodeId) {
      e.preventDefault();
      dollarPosRef.current = null;
      setPickerOpen(true);
      return;
    }
    if (suggestions.length === 0) return;
    // ... rest of existing handleKeyDown unchanged
```

Finally, add the popover render after the `<div className="expr-modal-editor-wrap">` block (just before the parts bar from Task 4). The popover needs `position: relative` on the pane. Wrap the existing pane content `<div className="expr-modal-editor-wrap">` in a `position: relative` container and render the popover inside:

Find `<div className="expr-modal-editor-wrap">` and replace the surrounding section pane:

```tsx
          <section className="expr-modal-pane" style={{ position: "relative" }}>
            <div className="expr-modal-pane-head">
              ... (updated head from above)
            </div>
            <div className="expr-modal-editor-wrap">
              ... (existing textarea + autocomplete, unchanged)
            </div>
            {pickerOpen && nodeId && (
              <div className="expr-picker-anchor">
                <VariablePickerPopover
                  nodeId={nodeId}
                  onInsert={insertExpression}
                  onClose={() => { setPickerOpen(false); dollarPosRef.current = null; }}
                />
              </div>
            )}
            {/* parts bar from Task 4 */}
          </section>
```

Add the import at the top of `NodeDetails.tsx`:

```tsx
import { VariablePickerPopover } from "./VariablePickerPopover";
```

- [ ] **Step 6.4 — TypeScript check**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | head -30
```

Expected: No errors.

- [ ] **Step 6.5 — Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(editor): floating variable picker in expanded expression editor"
```

---

## Task 7 — NDV `$` button (`NDVPanels.tsx`)

**Files:**
- Modify: `apps/web/src/editor/NDVPanels.tsx`

- [ ] **Step 7.1 — Add `nodeId` to `ParamField` call + `$` button in `ParametersTab`**

In `NDVPanels.tsx`, find `ParametersTab` (around line 55). Add picker state after the existing state declarations:

```tsx
  const [pickerParam, setPickerParam] = useState<string | null>(null);
```

Find the `renderField` function inside `ParametersTab` (around line 155). In the returned JSX, find the `<div className="field-label">` block:

```tsx
              <div className="field-label">
                <span className="field-name">{displayLabel}</span>
                {spec.description && (
                  ...
                )}
                {spec.required && <span className="field-req">required</span>}
              </div>
```

Replace with:

```tsx
              <div className="field-label">
                <span className="field-name">{displayLabel}</span>
                {spec.description && (
                  ...
                )}
                {spec.required && <span className="field-req">required</span>}
                {(spec.type === "string" || spec.type === "expression") &&
                  !spec.credential &&
                  spec.widget !== "hidden" && (
                    <button
                      type="button"
                      className="var-pick-inline-btn"
                      title="Pick a variable from upstream nodes"
                      onClick={() =>
                        setPickerParam((p) => (p === spec.name ? null : spec.name))
                      }
                    >
                      $
                    </button>
                  )}
              </div>
```

Then, in `renderField`, find the closing `</div>` of the outer `<div className="field">` and add the picker popover before it:

```tsx
              {pickerParam === spec.name && (
                <div className="var-pick-inline-wrap">
                  <VariablePickerPopover
                    nodeId={node.id}
                    onInsert={(expr) => {
                      const current = String(params[spec.name] ?? "");
                      const pos = current.length;
                      const before = current.slice(0, pos);
                      const opens = (before.match(/\{\{/g) ?? []).length;
                      const closes = (before.match(/\}\}/g) ?? []).length;
                      const insideExpr = opens > closes;
                      const toInsert = insideExpr
                        ? expr.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "")
                        : expr;
                      setParam(spec.name, current + toInsert);
                      setPickerParam(null);
                    }}
                    onClose={() => setPickerParam(null)}
                  />
                </div>
              )}
```

Also add `ParamField` call with `nodeId`:

```tsx
                  <ParamField
                    spec={renderSpec}
                    value={value}
                    onChange={(v) => setParam(spec.name, v)}
                    credentialContext={params}
                    nodeId={node.id}
                  />
```

Add the import at the top of `NDVPanels.tsx`:

```tsx
import { VariablePickerPopover } from "./VariablePickerPopover";
```

- [ ] **Step 7.2 — TypeScript check**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 7.3 — Commit**

```bash
git add apps/web/src/editor/NDVPanels.tsx
git commit -m "feat(editor): $ variable picker button on NDV string/expression fields"
```

---

## Task 8 — Inspector `$` button (`NodeDetails.tsx`)

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

The inspector sidebar in `NodeDetails.tsx` renders params in its own block (around line 3307–3386). Add the same `$` button pattern there.

- [ ] **Step 8.1 — Add picker state to the `NodeDetails` component**

Find the `function NodeDetails(` component (or the component that renders the inspector). Near the top where state is declared, add:

```tsx
  const [pickerParam, setPickerParam] = useState<string | null>(null);
```

- [ ] **Step 8.2 — Add `$` button to field labels and render picker**

In the inspector param rendering block (around line 3334), find the `<div className="field-label">` inside the `.map()`:

```tsx
                <div className="field-label">
                  <span className="field-name">{displayLabel}</span>
                  {spec.description && ( ... )}
                  {fx && <span className="fx-badge" ...>fx</span>}
                  {spec.required && <span className="field-req">required</span>}
                </div>
```

Replace with:

```tsx
                <div className="field-label">
                  <span className="field-name">{displayLabel}</span>
                  {spec.description && ( ... )}
                  {fx && <span className="fx-badge" ...>fx</span>}
                  {spec.required && <span className="field-req">required</span>}
                  {(spec.type === "string" || spec.type === "expression") &&
                    !spec.credential &&
                    spec.widget !== "hidden" && (
                      <button
                        type="button"
                        className="var-pick-inline-btn"
                        title="Pick a variable from upstream nodes"
                        onClick={() =>
                          setPickerParam((p) => (p === spec.name ? null : spec.name))
                        }
                      >
                        $
                      </button>
                    )}
                </div>
```

Find the closing `</div>` of the outer `<div className="field">` in the same map block and add the popover before it:

```tsx
                {pickerParam === spec.name && (
                  <div className="var-pick-inline-wrap">
                    <VariablePickerPopover
                      nodeId={node.id}
                      onInsert={(expr) => {
                        const current = String(params[spec.name] ?? "");
                        const toInsert = expr;
                        setParam(spec.name, current + toInsert);
                        setPickerParam(null);
                      }}
                      onClose={() => setPickerParam(null)}
                    />
                  </div>
                )}
```

- [ ] **Step 8.3 — TypeScript check and commit**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | head -30
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(editor): $ variable picker button on Inspector string/expression fields"
```

---

## Task 9 — CSS

**Files:**
- Modify: `apps/web/src/index.css`

- [ ] **Step 9.1 — Append all new styles to `index.css`**

Append to `apps/web/src/index.css`:

```css
/* ═══════════════════════════════════════════════════════
   Expression editor sidebar (Task 3)
   ═══════════════════════════════════════════════════════ */

.expr-modal-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.expr-sidebar {
  width: 170px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border, #2a2a35);
  background: var(--color-surface-alt, #0f0f14);
  display: flex;
  flex-direction: column;
  transition: width 0.18s ease;
}
.expr-sidebar--collapsed {
  width: 28px;
}

.expr-sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border, #2a2a35);
  min-height: 32px;
}
.expr-sidebar--collapsed .expr-sidebar-head {
  justify-content: center;
}
.expr-sidebar-title {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-muted, #888);
}
.expr-sidebar-collapse {
  width: 18px;
  height: 18px;
  border-radius: 3px;
  background: var(--color-surface, #1e1e28);
  border: 1px solid var(--color-border, #2a2a35);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 11px;
  color: var(--color-muted, #888);
  flex-shrink: 0;
}
.expr-sidebar-collapse:hover {
  color: var(--color-text, #e2e2e8);
  border-color: var(--color-accent, #6366f1);
}

.expr-sidebar-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}
.expr-sidebar-node-select {
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border, #1e1e28);
}
.expr-sidebar-dropdown {
  width: 100%;
  background: var(--color-surface, #1a1a25);
  border: 1px solid var(--color-border, #2a2a35);
  border-radius: 4px;
  color: var(--color-text, #e2e2e8);
  font-size: 10px;
  padding: 3px 6px;
  font-family: inherit;
}
.expr-sidebar-node-single {
  padding: 5px 8px;
  font-size: 10px;
  font-weight: 600;
  color: var(--color-muted, #aaa);
  border-bottom: 1px solid var(--color-border, #1e1e28);
}

.expr-sidebar-fields {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}
.expr-sidebar-empty {
  font-size: 10px;
  color: var(--color-muted, #666);
  padding: 8px;
  font-style: italic;
}
.expr-sidebar-field {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  cursor: grab;
  border-radius: 3px;
  margin: 1px 4px;
  font-size: 10px;
}
.expr-sidebar-field:hover {
  background: var(--color-surface, #1e1e2e);
}
.expr-sidebar-field:active {
  cursor: grabbing;
}
.expr-sidebar-field--meta {
  opacity: 0.7;
}
.expr-sidebar-grip {
  color: var(--color-muted, #444);
  font-size: 10px;
  flex-shrink: 0;
}
.expr-sidebar-name {
  flex: 1;
  color: var(--color-accent-light, #a5b4fc);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.expr-sidebar-type {
  font-size: 8px;
  color: var(--color-muted, #555);
  background: var(--color-surface, #1e1e28);
  padding: 1px 3px;
  border-radius: 2px;
  flex-shrink: 0;
}
.expr-sidebar-preview {
  font-size: 8px;
  color: var(--color-muted, #666);
  max-width: 44px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}
.expr-sidebar-meta {
  border-top: 1px solid var(--color-border, #1e1e28);
  padding-top: 4px;
}

/* ═══════════════════════════════════════════════════════
   Parts breakdown bar (Task 4)
   ═══════════════════════════════════════════════════════ */

.expr-parts-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-top: 1px solid var(--color-border, #1e1e28);
  background: var(--color-surface-alt, #0f0f14);
  overflow-x: auto;
  flex-shrink: 0;
}
.expr-parts-label {
  font-size: 9px;
  color: var(--color-muted, #555);
  flex-shrink: 0;
}
.expr-part-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: var(--color-surface, #1a1a2e);
  border: 1px solid var(--color-border, #2a2a45);
  border-radius: 4px;
  padding: 2px 6px;
  flex-shrink: 0;
}
.expr-part-chip--error {
  border-color: var(--color-danger, #f87171);
  background: rgba(248, 113, 113, 0.08);
}
.expr-part-chip-raw {
  font-size: 9px;
  color: var(--color-accent-light, #79c0ff);
  font-family: monospace;
}
.expr-part-chip-arrow {
  font-size: 9px;
  color: var(--color-muted, #555);
}
.expr-part-chip-val {
  font-size: 9px;
  color: var(--color-success, #34d399);
  font-family: monospace;
}

/* ═══════════════════════════════════════════════════════
   Format + History buttons (Task 5)
   ═══════════════════════════════════════════════════════ */

.expr-modal-header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.expr-history-wrap {
  position: relative;
}
.expr-history-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 30;
  background: var(--color-surface, #18181f);
  border: 1px solid var(--color-border, #2a2a35);
  border-radius: 6px;
  min-width: 240px;
  max-width: 360px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
  list-style: none;
  overflow: hidden;
}
.expr-history-dropdown li button {
  display: block;
  width: 100%;
  padding: 7px 12px;
  text-align: left;
  background: transparent;
  border: none;
  color: var(--color-text, #e2e2e8);
  font-size: 11px;
  font-family: monospace;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.expr-history-dropdown li button:hover {
  background: var(--color-surface-hover, #1e1e2e);
}

/* ═══════════════════════════════════════════════════════
   Variable picker popover (Tasks 6, 7, 8)
   ═══════════════════════════════════════════════════════ */

.var-picker-popover {
  background: var(--color-surface, #18181f);
  border: 1px solid var(--color-accent, #6366f1);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 6px 24px rgba(0,0,0,0.6);
  min-width: 220px;
  max-width: 280px;
  display: flex;
  flex-direction: column;
  max-height: 320px;
}
.var-picker-head {
  padding: 7px 12px;
  border-bottom: 1px solid var(--color-border, #2a2a35);
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.var-picker-icon {
  color: var(--color-accent, #6366f1);
  font-weight: 800;
  font-size: 13px;
}
.var-picker-search {
  width: calc(100% - 16px);
  margin: 6px 8px 4px;
  background: var(--color-surface-alt, #0d0d0f);
  border: 1px solid var(--color-border, #2a2a35);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 11px;
  color: var(--color-text, #e2e2e8);
  font-family: inherit;
  outline: none;
  flex-shrink: 0;
}
.var-picker-search:focus {
  border-color: var(--color-accent, #6366f1);
}
.var-picker-body {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 4px;
}
.var-picker-empty {
  padding: 10px 12px;
  font-size: 11px;
  color: var(--color-muted, #666);
  font-style: italic;
}
.var-picker-node {
  padding: 4px 0 2px;
}
.var-picker-node + .var-picker-node {
  border-top: 1px solid var(--color-border, #1e1e28);
}
.var-picker-node-head {
  padding: 3px 12px;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-muted, #666);
}
.var-picker-field {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px 4px 16px;
  cursor: pointer;
  font-size: 11px;
}
.var-picker-field:hover {
  background: var(--color-surface-hover, #1e1e2e);
}
.var-picker-grip {
  color: var(--color-muted, #444);
  font-size: 10px;
  flex-shrink: 0;
}
.var-picker-name {
  flex: 1;
  color: var(--color-accent-light, #a5b4fc);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.var-picker-type {
  font-size: 8px;
  color: var(--color-muted, #555);
  background: var(--color-surface, #1e1e28);
  padding: 1px 4px;
  border-radius: 2px;
  flex-shrink: 0;
}
.var-picker-copy {
  font-size: 9px;
  color: var(--color-muted, #555);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0 2px;
  opacity: 0;
  font-family: inherit;
}
.var-picker-field:hover .var-picker-copy {
  opacity: 1;
}

/* Anchor for popover inside expression pane */
.expr-picker-anchor {
  position: absolute;
  bottom: 40px;
  right: 12px;
  z-index: 25;
}

/* Inline wrapper in NDV and Inspector */
.var-pick-inline-btn {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--color-surface, #1a1a2e);
  border: 1px solid var(--color-border, #2a2a45);
  color: var(--color-accent-light, #a5b4fc);
  cursor: pointer;
  font-family: inherit;
  margin-left: auto;
}
.var-pick-inline-btn:hover {
  border-color: var(--color-accent, #6366f1);
}
.var-pick-inline-wrap {
  position: relative;
  margin-top: 4px;
}

/* $ Pick variable button in pane head */
.expr-pick-btn {
  font-size: 10px;
  font-weight: 700;
  color: var(--color-accent-light, #a5b4fc);
  border-color: var(--color-border, #2a2a45);
}
.expr-pick-btn:hover {
  border-color: var(--color-accent, #6366f1);
}
```

- [ ] **Step 9.2 — Run the full test suite**

```bash
cd apps/web && npx vitest run
```

Expected: All tests pass. Fix any failures.

- [ ] **Step 9.3 — Start dev server and verify visually**

```bash
cd apps/web && npx vite
```

Open the workflow editor, click a node with string params (e.g. an HTTP Request), open the NDV → Parameters tab:
1. Each string field should show a `$` button in the label row
2. Clicking `$` should open the variable picker popover
3. Click a node that has a multiline string field → click the ⤢ expand button → the expanded editor should show the sidebar (if upstream nodes have run)
4. Sidebar dropdown should let you switch between upstream nodes
5. `‹` button collapses the sidebar to a rail
6. Parts bar should show chips after evaluating an expression
7. Format button normalises `{{  expr  }}` spacing
8. History shows after closing and reopening the modal

- [ ] **Step 9.4 — Final commit**

```bash
git add apps/web/src/index.css
git commit -m "feat(editor): CSS for variable picker sidebar, popover, parts bar, and history"
```

---

## Self-Review Checklist

| Spec requirement | Task |
|---|---|
| Collapsible left sidebar with node dropdown | Task 3 |
| Draggable fields with type + value preview | Task 3 |
| Nested field expansion (depth 3) | `flattenOutputFields` in Task 1 — pre-flattened, no interactive expand needed for MVP |
| `$run.*` and `$env.*` metadata rows | Task 3 sidebar meta section |
| localStorage persist sidebar collapse | Task 3 `toggleSidebar` |
| Floating popover — button + Ctrl+Space | Task 6 |
| Auto-open popover on `$` typed inside `{{ }}` | Task 6 |
| Cross-node search in popover | `searchUpstreamFields` in Task 1, wired in Task 2 |
| Parts breakdown bar | Task 4 |
| Format button | Task 5 |
| Expression history (10 per field, localStorage) | Task 5 |
| `$` button on NDV string/expression fields | Task 7 |
| `$` button on Inspector string/expression fields | Task 8 |
| No backend changes | Confirmed — all client-side |
| `ExpressionEditorModal` gets `nodeId` prop | Tasks 3, 6 |
| `ParamField` gets `nodeId` prop threaded from call sites | Task 3 |

> **Note on nested field expansion:** The spec calls for interactive expand/collapse of nested fields in the sidebar. The implementation in Task 1 pre-flattens all nested fields up to depth 3. This achieves the same discoverability goal with simpler code — no interactive tree state needed. If interactive collapse is needed later, it can be added as a follow-up.
