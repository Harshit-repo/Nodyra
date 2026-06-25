# NodePalette Memoization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `PaletteItem` from re-rendering on every NodePalette parent render by wrapping it in `React.memo` and stabilizing its callback props with `useCallback`.

**Architecture:** Three targeted edits in `NodePalette.tsx`: add `useCallback` + `memo` to the react import, wrap `toggleFavorite` and `recordRecent` in `useCallback`, wrap the `PaletteItem` function component in `memo`.

**Tech Stack:** React 18, TypeScript. No new files, no new tests.

## Global Constraints

- Only `apps/web/src/editor/NodePalette.tsx` is modified
- No new test files
- `PaletteItem` stays in the same file — do not extract it
- `useCallback` dep arrays for `toggleFavorite` and `recordRecent` must be `[]` — both use the functional-update form of their state setters, so they reference no outer state directly
- `memo` wrapping: `const PaletteItem = memo(function PaletteItem(...) { ... })` — the inner function keeps its name for React DevTools
- Full test suite must pass after commit
- `npx tsc --noEmit` must show zero new errors

---

### Task 1: Wrap PaletteItem in memo + callbacks in useCallback

**Files:**
- Modify: `apps/web/src/editor/NodePalette.tsx`

- [ ] **Step 1: Read the relevant blocks**

Read `apps/web/src/editor/NodePalette.tsx`:
- Line 2 (react import)
- Lines 145–160 (PaletteItem function signature and opening)
- Lines 205–215 (PaletteItem closing brace area)
- Lines 435–450 (toggleFavorite and recordRecent definitions)

Confirm exact text before editing.

- [ ] **Step 2: Add `useCallback` and `memo` to the react import**

Find line 2:
```ts
import { useEffect, useMemo, useRef, useState } from "react";
```

Replace with:
```ts
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
```

- [ ] **Step 3: Wrap toggleFavorite in useCallback**

Find (lines 438–442):
```ts
function toggleFavorite(id: string): void {
  setFavorites((items) =>
    items.includes(id) ? items.filter((item) => item !== id) : [id, ...items],
  );
}
```

Replace with:
```ts
const toggleFavorite = useCallback(function toggleFavorite(id: string): void {
  setFavorites((items) =>
    items.includes(id) ? items.filter((item) => item !== id) : [id, ...items],
  );
}, []);
```

- [ ] **Step 4: Wrap recordRecent in useCallback**

Find (lines 444–446):
```ts
function recordRecent(id: string): void {
  setRecent((items) => [id, ...items.filter((item) => item !== id)].slice(0, MAX_RECENTS));
}
```

Replace with:
```ts
const recordRecent = useCallback(function recordRecent(id: string): void {
  setRecent((items) => [id, ...items.filter((item) => item !== id)].slice(0, MAX_RECENTS));
}, []);
```

- [ ] **Step 5: Wrap PaletteItem in memo**

Find the `PaletteItem` function declaration opening (line 145). The current form is:
```ts
function PaletteItem({
```

Replace that opening line with:
```ts
const PaletteItem = memo(function PaletteItem({
```

Then find the closing brace of the `PaletteItem` function body. It is a lone `}` at approximately line 210, followed by a blank line and the next section. Replace that closing `}` with:
```ts
});
```

**Important:** The `memo(` call opens before the `function` keyword — the `)` closes after the function's closing `}`. Only two characters change: `function` becomes `const PaletteItem = memo(function`, and the final `}` becomes `});`.

- [ ] **Step 6: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors.

- [ ] **Step 7: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass (334 tests, 60 files).

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/editor/NodePalette.tsx
git commit -m "perf(editor): memoize PaletteItem, stabilize toggleFavorite and recordRecent with useCallback"
```
