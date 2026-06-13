# Expression Editor & Variable Picker — Design Spec

**Date:** 2026-06-14  
**Status:** Approved for implementation

---

## Overview

Two surfaces improved: the **expanded ExpressionEditorModal** (the full-screen editor opened via the ⤢ button on string fields) and the **normal NDV parameter view**. The goal is to make upstream node data visible and insertable without typing `$node["id"].main.field` from memory.

---

## 1. Expanded Expression Editor — Combined A+B Layout

### 1a. Collapsible left sidebar (Approach A)

Add a left sidebar to `ExpressionEditorModal`, sitting between the modal border and the existing two-pane editor.

`ExpressionEditorModal` gains a new required `nodeId: string` prop. The call site in `ExprField` (inside `NodeDetails.tsx`) already has `node.id` in scope — thread it through. The sidebar calls `getUpstreamNodeFields(nodeId, ...)` using store selectors accessed via `useEditor`.

**Structure:**
```
[ sidebar (170px) ][ expression pane (flex) ][ result pane (40%) ]
```

**Sidebar contents:**
- **Header row**: "Variables" label + collapse `‹` button (right-aligned)
- **Node dropdown**: `<select>` listing all upstream nodes by label/name. Defaults to the most direct upstream (first edge source). Changing selection re-renders the field list below.
- **Field list**: scrollable list of output fields for the selected node. Each row:
  - Drag grip `⠿`
  - Field name (e.g. `name`, `orders[0].id`)
  - Type badge (`str` / `int` / `list` / `obj` / `float`) derived from the actual run output value
  - Value preview (truncated to ~12 chars) shown in muted text — e.g. `"Alice"`, `42`, `[3 items]`
  - Each row is `draggable`. `dragstart` sets `text/plain` and `application/x-noodle-expression` to `{{ $node["NodeLabel"].main.fieldPath }}` — same format as the existing `DataPanel` drag system

**Nested field expansion:**
- If a field value is a plain object or array, show an expand arrow `▶` before the name
- Clicking expands it inline, rendering child keys as sub-rows with indentation (`orders` → `orders[0].id`, `orders[0].total`, etc.)
- Collapse re-hides sub-rows. Max depth: 3 levels.

**Collapsing:**
- Clicking `‹` sets sidebar to `width: 28px`, hides content, shows a vertical "Variables" rail label + `›` to re-expand
- State persisted to `localStorage` key `noodle_expr_sidebar_collapsed`

**Run metadata section** (always present at bottom of field list, after a divider):
- `$run.id`, `$run.status`, `$run.startedAt` — draggable, type `str`
- `$env.KEY` placeholder — draggable, inserts `{{ $env.KEY }}` as a reminder to substitute

---

### 1b. Floating variable picker (Approach B)

A floating popover triggered from a **"$ Pick variable"** button in the hint bar above the expression panes, and also by `Ctrl+Space` keyboard shortcut, and automatically when the user types `$` inside a `{{ }}` block.

**Popover structure:**
- Header: `$` icon + "Pick a variable"
- Search input: filters across **all upstream nodes** simultaneously by field name and value content
- Results grouped by node name (node label as section heading)
- Each field row: grip `⠿`, field name, type badge, copy button (copies expression to clipboard)
- Clicking a field row inserts `{{ $node["Label"].main.fieldPath }}` at the current cursor position and closes the popover
- Drag from any row also works (same `dragstart` data as sidebar rows)
- Popover is positioned near the cursor / near the `$` button; closes on Escape or outside click

**Auto-open on `$` typed:**
- In `HighlightedTextarea`, on every `onChange`, check if the text just before the cursor matches the pattern `\{\{[^}]*\$$` (user typed `$` inside `{{ }}`)
- If matched, open the popover automatically. This replaces the existing `computeSuggestions` inline list for that trigger.

---

### 1c. Expression parts breakdown bar

A thin bar rendered **below the expression textarea**, above the result pane divider.

- Shows one chip per `{{ expr }}` in the current expression value
- Each chip: `expr-text → resolved-value` (e.g. `name → "Alice"`, `id → 42`)
- Resolved values come from the same `parts` array already returned by `api.previewExpression` (the `PreviewPart[]` type already carries `kind: "expr"` with `value`)
- Error parts render with a red tint and show the error message on hover
- If the expression has no `{{ }}` parts, the bar is hidden

---

### 1d. Format / prettify button

A **"Format"** button in the modal header (already mocked in the design). On click:
- Trims leading/trailing whitespace
- Normalises `{{ expr }}` spacing: ensures exactly one space inside each `{{` and `}}` delimiter
- For pure JSON strings (no expressions), runs `JSON.stringify(JSON.parse(value), null, 2)` and wraps the result in a single `{{ }}` if it was already one
- No-op if the expression is invalid JSON and has no fixable spacing issues

---

### 1e. Expression history

A **"History"** button in the modal header opens a small dropdown listing the last 10 distinct values this specific field (identified by `nodeId:paramName`) held. On click, applies the historical value.

- History stored in `localStorage` key `noodle_expr_history` as `Record<string, string[]>` (keyed by `nodeId:paramName`, max 10 per field, deduped)
- A value is appended to history when the modal closes with a non-empty expression
- History dropdown rendered below the header, closes on outside click or Escape

---

## 2. Normal NDV — $ Button on Every Expression Field

In `NDVPanels.tsx` → `ParametersTab` → `renderField`, for every param where `spec.type === "string"` or `spec.type === "expression"` — these are the types that render a `HighlightedTextarea` inside `ParamField`. Exclude `spec.widget === "hidden"`, credential fields (`spec.credential`), and fields already hidden by `display_when`. The check: `(spec.type === "string" || spec.type === "expression") && !spec.credential && spec.widget !== "hidden"`.

- Render a small `$` icon button immediately to the right of the field label (same row as the field name)
- Clicking it opens the same floating variable picker popover anchored near that button
- Inserting a variable from the picker writes into that param's value via `setParam`
- This removes the need to open the expanded modal just to pick a variable

The popover used here is the same component as in 1b — a shared `<VariablePickerPopover>` extracted to its own file.

---

## 3. Data Architecture

### Upstream node data

Both the sidebar and the popover need:
- The list of upstream nodes (already available in `edges` + `nodes` from the editor store)
- The output values for those nodes (already in `runOutputs[nodeId]`)
- The node label (from `node.data.label ?? node.data.manifest.name`)

A helper `getUpstreamNodeFields(nodeId, nodes, edges, runOutputs)` is extracted — takes the current node ID and returns `UpstreamNode[]`:

```ts
interface UpstreamField {
  path: string;           // e.g. "name", "orders[0].id"
  type: string;           // "str" | "int" | "list" | "obj" | "float" | "bool" | "null"
  valuePreview: string;   // truncated string for display
  expression: string;     // full {{ $node["Label"].main.path }}
}
interface UpstreamNode {
  id: string;
  label: string;
  fields: UpstreamField[];
}
```

Field flattening walks the output value up to depth 3, expanding objects and arrays (max 5 array items expanded to `[0]...[4]`).

### Expression insertion

The `HighlightedTextarea` already exposes `taRef`. Insertion at cursor:

```ts
function insertAtCursor(ta: HTMLTextAreaElement, text: string): string {
  const pos = ta.selectionStart ?? 0;
  return value.slice(0, pos) + text + value.slice(pos);
}
```

If the cursor is inside an existing `{{ }}` block, insert without wrapping. If outside, wrap in `{{ text }}`.

---

## 4. Files to Create / Modify

| File | Change |
|---|---|
| `apps/web/src/editor/node-details/upstreamFields.ts` | New — `getUpstreamNodeFields` helper |
| `apps/web/src/editor/VariablePickerPopover.tsx` | New — shared floating popover component |
| `apps/web/src/editor/NodeDetails.tsx` | Modify `ExpressionEditorModal`: add sidebar, parts bar, format button, history button/dropdown; wire `VariablePickerPopover` |
| `apps/web/src/editor/NDVPanels.tsx` | Modify `renderField`: add `$` button wired to `VariablePickerPopover` |
| `apps/web/src/index.css` | New styles for sidebar, popover, parts bar, history dropdown |

---

## 5. Out of Scope

- No backend changes required — existing `api.previewExpression` is used as-is
- No changes to `ExprContext` or `computeSuggestions` (the inline autocomplete continues to work for typed completion; the popover is an additional entry point)
- Expression history is client-only (localStorage), not synced to server
- The sidebar and popover do not appear in the `NodeCodePanel` (Python editor) — that's a separate surface

---

## 6. Build Order

1. `upstreamFields.ts` — pure utility, no UI dependencies
2. `VariablePickerPopover.tsx` — uses upstream fields util, no store dependency
3. Expression editor sidebar + parts bar + format + history (all in `NodeDetails.tsx`)
4. NDV `$` button (in `NDVPanels.tsx`, imports `VariablePickerPopover`)
5. CSS updates alongside each step
