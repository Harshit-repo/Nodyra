# Canvas contextMenuItems useMemo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `contextMenuItems` in `useMemo` in `Canvas.tsx` so the array is not recreated on every render.

**Architecture:** Single targeted edit: wrap the existing `const contextMenuItems = ctxMenu ? ... : []` (lines 869–987) in `useMemo(...)`. `useMemo` is already imported.

**Tech Stack:** React 18, TypeScript. No new files, no new tests.

## Global Constraints

- Only `apps/web/src/editor/Canvas.tsx` is modified
- No new test files
- `useMemo` is already imported — do not add a duplicate import
- The `useMemo` dep array must list all values read inside the expression:
  `[ctxMenu, nodes, selectedMetanodeIds, openNdv, ungroupMetanode, collapseToMetanode, notify, copySelection, pasteSelection, toggleDisabled, cutSelection, deleteNode, openQuickAddAt, fitView, setCtxMenu]`
- Do NOT wrap the `handleContextMenuKeyDown` function — only `contextMenuItems`
- Full test suite must pass; `npx tsc --noEmit` zero new errors

---

### Task 1: Wrap contextMenuItems in useMemo

**Files:**
- Modify: `apps/web/src/editor/Canvas.tsx`

- [ ] **Step 1: Read the contextMenuItems block**

Read `apps/web/src/editor/Canvas.tsx` lines 867–990. Confirm exact start (`const contextMenuItems`) and end (the last `]` of the ternary, followed by a blank line) before editing.

- [ ] **Step 2: Wrap in useMemo**

Change the opening line from:
```ts
const contextMenuItems = ctxMenu
```

To:
```ts
const contextMenuItems = useMemo(() => ctxMenu
```

Then find the final closing bracket of the array (the lone `]` that ends the `else` branch of the outer ternary — the `: []` case). Change it from:
```ts
  : [];
```

To:
```ts
  : [], [ctxMenu, nodes, selectedMetanodeIds, openNdv, ungroupMetanode, collapseToMetanode, notify, copySelection, pasteSelection, toggleDisabled, cutSelection, deleteNode, openQuickAddAt, fitView, setCtxMenu]);
```

This wraps the full ternary expression `ctxMenu ? ... : []` as the body of the `useMemo` callback, with the dependency array as the second argument.

- [ ] **Step 3: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors.

- [ ] **Step 4: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/Canvas.tsx
git commit -m "perf(editor): memoize contextMenuItems in Canvas"
```
