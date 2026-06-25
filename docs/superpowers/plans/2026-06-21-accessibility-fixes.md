# Accessibility Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add missing `aria-modal="true"` to three `role="dialog"` elements and add `aria-label` to two unlabeled search inputs.

**Architecture:** Five attribute additions across four files. No logic changes, no new files, no new tests.

**Tech Stack:** React 18, TypeScript, existing JSX.

## Global Constraints

- Files: `FunctionsPanel.tsx`, `Canvas.tsx`, `NodePalette.tsx`, `VariablePickerPopover.tsx`
- No new test files, no new components
- Add only the missing attributes — do not reorganise existing JSX
- `npx tsc --noEmit` zero new errors
- Full test suite must pass

---

### Task 1: Add aria-modal and aria-label to four files

**Files:**
- Modify: `apps/web/src/editor/FunctionsPanel.tsx` (line 192)
- Modify: `apps/web/src/editor/Canvas.tsx` (line 1180 and the quick-add input ~line 1188)
- Modify: `apps/web/src/editor/NodePalette.tsx` (lines 487–494)
- Modify: `apps/web/src/editor/VariablePickerPopover.tsx` (line 72)

- [ ] **Step 1: Fix FunctionsPanel.tsx**

Read line 192 of `apps/web/src/editor/FunctionsPanel.tsx`. It currently reads:
```jsx
<div className="functions-panel" role="dialog" aria-label="Workflow functions">
```

Add `aria-modal="true"` after `role="dialog"`:
```jsx
<div className="functions-panel" role="dialog" aria-modal="true" aria-label="Workflow functions">
```

- [ ] **Step 2: Fix Canvas.tsx quick-add dialog**

Read `apps/web/src/editor/Canvas.tsx` lines 1177–1210 to confirm the quick-add block structure.

The quick-add wrapper div (line ~1180) currently has:
```jsx
<div
  className="canvas-quick-add"
  role="dialog"
  aria-label={quickAdd.insertEdgeId ? "Insert node into connection" : "Quick add node"}
  style={{ left: quickAdd.x, top: quickAdd.y }}
  onClick={(event) => event.stopPropagation()}
>
```

Add `aria-modal="true"` after `role="dialog"`:
```jsx
<div
  className="canvas-quick-add"
  role="dialog"
  aria-modal="true"
  aria-label={quickAdd.insertEdgeId ? "Insert node into connection" : "Quick add node"}
  style={{ left: quickAdd.x, top: quickAdd.y }}
  onClick={(event) => event.stopPropagation()}
>
```

Also find the `<input>` inside this block (approximately line 1188). It currently has `placeholder` but no `aria-label`. Add an `aria-label` that matches the `placeholder` logic:
```jsx
aria-label={quickAdd.insertEdgeId ? "Search compatible nodes" : "Search nodes"}
```

- [ ] **Step 3: Fix NodePalette.tsx search input**

Read `apps/web/src/editor/NodePalette.tsx` lines 487–494. The search input currently has no `aria-label`:
```jsx
<input
  ref={searchRef}
  className="palette-search"
  placeholder="Search nodes…"
  value={query}
  onChange={(e) => setQuery(e.target.value)}
  onKeyDown={handleSearchKeyDown}
/>
```

Add `aria-label="Search nodes"` after `className`:
```jsx
<input
  ref={searchRef}
  className="palette-search"
  aria-label="Search nodes"
  placeholder="Search nodes…"
  value={query}
  onChange={(e) => setQuery(e.target.value)}
  onKeyDown={handleSearchKeyDown}
/>
```

- [ ] **Step 4: Fix VariablePickerPopover.tsx**

Read `apps/web/src/editor/VariablePickerPopover.tsx` around line 72. The dialog div has `role="dialog"` but no `aria-modal="true"`. Add it:

Find the div with `role="dialog"` and add `aria-modal="true"` alongside it.

Also check whether the search input inside this popover (line ~79) has an `aria-label`. If not, add `aria-label="Search variables"`.

- [ ] **Step 5: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors (attribute additions don't affect TypeScript).

- [ ] **Step 6: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/FunctionsPanel.tsx apps/web/src/editor/Canvas.tsx apps/web/src/editor/NodePalette.tsx apps/web/src/editor/VariablePickerPopover.tsx
git commit -m "a11y(editor): add aria-modal to dialog elements, aria-label to search inputs"
```
