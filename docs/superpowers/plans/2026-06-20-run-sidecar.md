# Run Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inline "Runs ▾" dropdown in `EditorPage.tsx` with a persistent 272px right-side panel — the Run Sidecar — that renders run history, execution view on the canvas, and five analytical tabs.

**Architecture:** The sidecar is a new `RunSidecar/` component directory that lives next to `Canvas.tsx`. It uses the existing Zustand run slice (`applyRunInfo`, `clearRun`, `runId`, `runStatus`) for canvas execution state, and TanStack Query hooks (`useRuns`, `useRun`) for data fetching. Local tab/selection state lives in a `useRunSidecar` hook — no Zustand changes required. The sidecar is mounted inside `EditorPage.tsx`'s `editor-body` flex layout, between `editor-stage` and `<Inspector>`.

**Tech Stack:** React 18, TypeScript, Zustand, TanStack Query, Vitest + @testing-library/react, CSS (global `editor.css`)

## Global Constraints

- Test runner: `vitest` — import `describe`, `it`, `expect`, `vi` from `"vitest"`
- Component tests use `@testing-library/react` — `render`, `screen`, `fireEvent`
- All new CSS goes in `apps/web/src/editor.css` — no CSS modules, no inline styles for layout
- The Zustand store is `useEditor` from `"./store"` (relative, inside `editor/`) or `"../editor/store"` (from `src/`)
- `isTriggerManifest` is exported from `"../store"` (relative from `RunSidecar/`) — use it to identify trigger nodes
- TanStack Query hooks are imported from `"../../queries"` (relative from `editor/RunSidecar/`)
- `api` is imported from `"../../api"`
- `RunInfo`, `NodeRunResult` types from `"../../types"`
- No new Zustand slice — use existing `runId`, `runStatus`, `runOutputs`, `runMeta`, `applyRunInfo`, `clearRun`
- `useRerunRunMutation` already exists in `queries/index.ts` — use it, don't re-implement
- Width of sidecar: `272px` fixed, no resize in v1
- Use `safeGetItem`/`safeSetItem` from `"../../safeStorage"` instead of raw `localStorage` — browser storage can be unavailable under restrictive privacy settings
- Use `useMountedRef` from `"../../hooks/useMountedRef"` to guard async continuations against unmounted components
- **EditorPage.tsx already has ChatGPT changes (uncommitted):** `NodeDetailModal` is lazy-loaded with `Suspense`, `creatingChildIdsRef` guards Map Group creation, Map Group `useEffect` has an explicit deps array — Task 9 must NOT revert any of these

---

### Task 1: `useRunSidecar.ts` — local sidecar state hook

**Files:**
- Create: `apps/web/src/editor/RunSidecar/useRunSidecar.ts`
- Test: `apps/web/src/editor/RunSidecar/useRunSidecar.test.ts`

**Interfaces:**
- Produces:
  ```ts
  type SidecarTab = 'runs' | 'diff' | 'timeline' | 'stats' | 'info';
  interface RunSidecarState {
    activeTab: SidecarTab;
    setActiveTab: (tab: SidecarTab) => void;
    selectedRunId: string | null;
    setSelectedRunId: (id: string | null) => void;
    pinnedNodeId: string | null;
    setPinnedNodeId: (id: string | null) => void;
    diffPair: [string, string] | null;
    setDiffPair: (pair: [string, string] | null) => void;
  }
  function useRunSidecar(): RunSidecarState
  ```

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/editor/RunSidecar/useRunSidecar.test.ts
import { renderHook, act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useRunSidecar } from "./useRunSidecar";

describe("useRunSidecar", () => {
  it("initialises with runs tab and no selection", () => {
    const { result } = renderHook(() => useRunSidecar());
    expect(result.current.activeTab).toBe("runs");
    expect(result.current.selectedRunId).toBeNull();
    expect(result.current.pinnedNodeId).toBeNull();
    expect(result.current.diffPair).toBeNull();
  });

  it("updates activeTab", () => {
    const { result } = renderHook(() => useRunSidecar());
    act(() => result.current.setActiveTab("diff"));
    expect(result.current.activeTab).toBe("diff");
  });

  it("updates selectedRunId and clears pinnedNodeId", () => {
    const { result } = renderHook(() => useRunSidecar());
    act(() => result.current.setPinnedNodeId("node-1"));
    act(() => result.current.setSelectedRunId("run-abc"));
    expect(result.current.selectedRunId).toBe("run-abc");
    expect(result.current.pinnedNodeId).toBeNull();
  });

  it("stores diffPair", () => {
    const { result } = renderHook(() => useRunSidecar());
    act(() => result.current.setDiffPair(["run-a", "run-b"]));
    expect(result.current.diffPair).toEqual(["run-a", "run-b"]);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```
cd apps/web && npx vitest run src/editor/RunSidecar/useRunSidecar.test.ts
```
Expected: `Cannot find module './useRunSidecar'`

- [ ] **Step 3: Implement the hook**

```ts
// apps/web/src/editor/RunSidecar/useRunSidecar.ts
import { useState } from "react";

export type SidecarTab = "runs" | "diff" | "timeline" | "stats" | "info";

export interface RunSidecarState {
  activeTab: SidecarTab;
  setActiveTab: (tab: SidecarTab) => void;
  selectedRunId: string | null;
  setSelectedRunId: (id: string | null) => void;
  pinnedNodeId: string | null;
  setPinnedNodeId: (id: string | null) => void;
  diffPair: [string, string] | null;
  setDiffPair: (pair: [string, string] | null) => void;
}

export function useRunSidecar(): RunSidecarState {
  const [activeTab, setActiveTab] = useState<SidecarTab>("runs");
  const [selectedRunId, setSelectedRunIdRaw] = useState<string | null>(null);
  const [pinnedNodeId, setPinnedNodeId] = useState<string | null>(null);
  const [diffPair, setDiffPair] = useState<[string, string] | null>(null);

  function setSelectedRunId(id: string | null) {
    setSelectedRunIdRaw(id);
    setPinnedNodeId(null);
  }

  return {
    activeTab,
    setActiveTab,
    selectedRunId,
    setSelectedRunId,
    pinnedNodeId,
    setPinnedNodeId,
    diffPair,
    setDiffPair,
  };
}
```

- [ ] **Step 4: Run to confirm pass**

```
cd apps/web && npx vitest run src/editor/RunSidecar/useRunSidecar.test.ts
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/RunSidecar/useRunSidecar.ts apps/web/src/editor/RunSidecar/useRunSidecar.test.ts
git commit -m "feat(sidecar): add useRunSidecar state hook"
```

---

### Task 2: CSS — sidecar styles

**Files:**
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Produces: CSS classes `.run-sidecar`, `.sc-tabs`, `.sc-tab`, `.sc-tab.active`, `.sc-panel`, `.sc-run-row`, `.sc-run-row.selected`, `.run-exec-banner`, `.sc-node-row`, `.sc-node-row.pinned`, `.sc-actions`, `.sc-filter-row`, `.sc-filter-chip`, `.sc-filter-chip.on`

- [ ] **Step 1: Remove the old dropdown CSS**

In `apps/web/src/editor.css`, find and delete the block `/* ---- runs dropdown ---- */` through the end of `.runs-item-meta { ... }`. This is approximately lines 5777–5825 (search for `/* ---- runs dropdown ----`).

- [ ] **Step 2: Add sidecar CSS at the end of `editor.css`**

Append to `apps/web/src/editor.css`:

```css
/* ---- run sidecar ---- */
.run-sidecar {
  width: 272px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-left: 1px solid var(--border);
  overflow: hidden;
}

.sc-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg);
}

.sc-tab {
  flex: 1;
  height: 40px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--ink-3);
  cursor: pointer;
  border: none;
  border-right: 1px solid var(--border);
  background: transparent;
  transition: background 0.12s, color 0.12s;
  font-family: inherit;
}
.sc-tab:last-child { border-right: none; }
.sc-tab:hover { background: var(--surface-2); color: var(--ink); }
.sc-tab.active {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  box-shadow: inset 0 -2px 0 var(--accent);
}
.sc-tab-icon { font-size: 13px; line-height: 1; }
.sc-tab-lbl { font-size: 9px; }

.sc-panel {
  display: none;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}
.sc-panel.active { display: flex; }

.sc-head {
  height: 40px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 12px;
  gap: 8px;
  flex-shrink: 0;
}
.sc-head-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}
.sc-head-badge {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
}
.sc-head-end { margin-left: auto; display: flex; gap: 4px; }

.sc-filter-row {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 5px;
  flex-shrink: 0;
}
.sc-filter-chip {
  font-size: 10px;
  font-family: var(--font-mono);
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--ink-3);
  cursor: pointer;
  font-weight: 500;
  transition: all 0.12s;
}
.sc-filter-chip.on {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
}

.sc-run-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.sc-run-list::-webkit-scrollbar { width: 3px; }
.sc-run-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 99px; }

.sc-run-row {
  position: relative;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.1s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sc-run-row:hover { background: var(--surface-2); }
.sc-run-row.selected { background: color-mix(in srgb, var(--accent) 6%, transparent); }
.sc-run-row.selected::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: linear-gradient(to bottom, transparent, var(--accent), transparent);
  background-size: 100% 200%;
  animation: sc-trace 1.4s ease-in-out;
}
@keyframes sc-trace {
  0%   { background-position: 0 -100%; opacity: 0; }
  15%  { opacity: 1; }
  100% { background-position: 0 100%; }
}
.sc-run-top {
  display: flex;
  align-items: center;
  gap: 7px;
}
.sc-run-time {
  flex: 1;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--ink);
}
.sc-run-dur {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
}
.sc-run-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
}
.sc-trigger-chip {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--surface-2);
  color: var(--ink-3);
  border: 1px solid var(--border);
}

/* Node inspector (runs tab) */
.sc-inspect { flex-shrink: 0; border-top: 1px solid var(--border); overflow-y: auto; max-height: 160px; }
.sc-insp-head {
  padding: 7px 12px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.sc-insp-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--ink-3);
  font-family: var(--font-mono);
}
.sc-insp-run-ref {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--accent);
}

.sc-node-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  cursor: pointer;
  transition: background 0.1s;
}
.sc-node-row:hover { background: var(--surface-2); }
.sc-node-row.pinned { background: color-mix(in srgb, var(--accent) 8%, transparent); }
.sc-node-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.sc-node-name {
  flex: 1;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  transition: color 0.12s;
}
.sc-node-row:hover .sc-node-name,
.sc-node-row.pinned .sc-node-name { color: var(--ink); }
.sc-node-status { font-size: 10px; font-family: var(--font-mono); }
.sc-node-ms { font-size: 10px; font-family: var(--font-mono); color: var(--border); min-width: 34px; text-align: right; }

/* Actions bar */
.sc-actions {
  padding: 10px 12px;
  border-top: 1px solid var(--border);
  display: flex;
  gap: 7px;
  flex-shrink: 0;
}
.sc-action-btn {
  flex: 1;
  padding: 8px 0;
  border-radius: 7px;
  font-size: 11px;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--ink-3);
  transition: all 0.15s;
  text-align: center;
}
.sc-action-btn:hover { color: var(--ink); }
.sc-action-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.sc-action-btn.primary:hover { opacity: 0.9; }

/* Diff tab */
.sc-diff-selectors {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
}
.sc-diff-select {
  flex: 1;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 4px 8px;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink);
  cursor: pointer;
}
.sc-diff-body { flex: 1; overflow-y: auto; display: flex; }
.sc-diff-col { flex: 1; overflow: hidden; }
.sc-diff-divider { width: 1px; background: var(--border); flex-shrink: 0; }
.sc-diff-col-head {
  padding: 6px 10px;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg);
  flex-shrink: 0;
}
.sc-diff-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 10px;
  font-family: var(--font-mono);
}
.sc-diff-row.improved { background: color-mix(in srgb, var(--ok) 5%, transparent); }
.sc-diff-row.worse    { background: color-mix(in srgb, var(--error) 5%, transparent); }
.sc-diff-row.changed  { background: color-mix(in srgb, var(--warn) 4%, transparent); }
.sc-diff-node-name { flex: 1; color: var(--ink-3); }
.sc-diff-status { font-size: 10px; }
.sc-diff-ms { color: var(--border); min-width: 34px; text-align: right; }
.sc-diff-footer {
  padding: 8px 12px;
  border-top: 1px solid var(--border);
  background: var(--bg);
  flex-shrink: 0;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  display: flex;
  gap: 10px;
}

/* Timeline tab */
.sc-wf-body { flex: 1; padding: 12px; overflow-y: auto; }
.sc-wf-row { display: flex; align-items: center; margin-bottom: 8px; }
.sc-wf-name {
  width: 88px;
  flex-shrink: 0;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 6px;
}
.sc-wf-track { flex: 1; height: 18px; position: relative; }
.sc-wf-bar {
  position: absolute;
  height: 14px;
  top: 2px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  padding: 0 5px;
  font-size: 9px;
  font-family: var(--font-mono);
  overflow: hidden;
  white-space: nowrap;
  min-width: 4px;
}
.sc-wf-bar.ok    { background: color-mix(in srgb, var(--ok) 18%, transparent); border: 1px solid color-mix(in srgb, var(--ok) 40%, transparent); color: var(--ok); }
.sc-wf-bar.error { background: color-mix(in srgb, var(--error) 18%, transparent); border: 1px solid color-mix(in srgb, var(--error) 40%, transparent); color: var(--error); }
.sc-wf-bar.skip  { background: var(--surface-2); border: 1px dashed var(--border); color: var(--ink-3); opacity: 0.5; }
.sc-wf-ticks-row { display: flex; margin-bottom: 4px; }
.sc-wf-tick-spacer { width: 88px; flex-shrink: 0; }
.sc-wf-ticks { flex: 1; position: relative; height: 14px; border-bottom: 1px solid var(--border); }
.sc-wf-tick {
  position: absolute;
  transform: translateX(-50%);
  font-size: 9px;
  font-family: var(--font-mono);
  color: var(--ink-3);
}
.sc-wf-critical {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  border-top: 1px solid var(--border);
  padding: 8px 0 0;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Stats tab */
.sc-stats-body { flex: 1; overflow-y: auto; }
.sc-stats-row {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 5px;
  cursor: pointer;
}
.sc-stats-row:hover { background: var(--surface-2); }
.sc-stats-top { display: flex; align-items: center; gap: 7px; }
.sc-node-tags { display: flex; gap: 4px; flex-wrap: wrap; }
.sc-node-tag {
  font-size: 9px;
  font-family: var(--font-mono);
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid;
}
.sc-node-tag.ok   { background: color-mix(in srgb, var(--ok) 8%, transparent); color: var(--ok); border-color: color-mix(in srgb, var(--ok) 35%, transparent); }
.sc-node-tag.err  { background: color-mix(in srgb, var(--error) 8%, transparent); color: var(--error); border-color: color-mix(in srgb, var(--error) 35%, transparent); }
.sc-node-tag.skip { background: var(--surface-2); color: var(--ink-3); border-color: var(--border); }
.sc-heat-strip { display: flex; gap: 2px; align-items: center; }
.sc-heat-block { width: 12px; height: 5px; border-radius: 2px; }
.sc-heat-block.ok   { background: var(--ok); }
.sc-heat-block.err  { background: var(--error); }
.sc-heat-block.skip { background: var(--border); }
.sc-heat-label { font-size: 9px; font-family: var(--font-mono); margin-left: 5px; color: var(--ink-3); }

/* Info tab */
.sc-info-body { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
.sc-info-section { border-bottom: 1px solid var(--border); }
.sc-info-sec-head {
  padding: 7px 12px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.sc-info-payload {
  padding: 10px 12px;
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  line-height: 1.8;
  white-space: pre;
  overflow-x: auto;
}
.sc-info-note {
  margin: 10px 12px;
  padding: 7px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 11px;
  color: var(--ink);
  font-family: inherit;
  resize: vertical;
  min-height: 60px;
  width: calc(100% - 24px);
}

/* Exec banner (over the canvas) */
.run-exec-banner {
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 30px;
  z-index: 20;
  background: linear-gradient(90deg, var(--bg), var(--surface));
  border-bottom: 1px solid var(--accent);
  display: flex;
  align-items: center;
  padding: 0 12px;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 11px;
}
.run-exec-banner-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 6px var(--accent);
  animation: sc-blink 2s ease-in-out infinite;
}
@keyframes sc-blink { 0%,100%{opacity:1}50%{opacity:0.35} }
.run-exec-banner-label { color: var(--accent); }
.run-exec-banner-id    { color: var(--ink); }
.run-exec-banner-meta  { color: var(--ink-3); }
.run-exec-banner-end   { margin-left: auto; }
.run-exec-banner-exit {
  font-size: 10px;
  color: var(--ink-3);
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
  background: transparent;
  font-family: inherit;
  transition: all 0.15s;
}
.run-exec-banner-exit:hover { color: var(--ink); }
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```
cd apps/web && npx vitest run
```
Expected: all existing tests pass (CSS-only change, no regressions)

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/editor.css
git commit -m "feat(sidecar): add run sidecar CSS, remove old runs-dropdown styles"
```

---

### Task 3: `RunList.tsx` — Tab 1: run list + node inspector + actions

**Files:**
- Create: `apps/web/src/editor/RunSidecar/RunList.tsx`
- Test: `apps/web/src/editor/RunSidecar/RunList.test.tsx`

**Interfaces:**
- Consumes from Task 1: `SidecarTab`, `useRunSidecar`
- Props:
  ```ts
  interface RunListProps {
    workflowId: string;
    selectedRunId: string | null;
    onSelectRun: (runId: string) => void;
    pinnedNodeId: string | null;
    onPinNode: (nodeId: string) => void;
    onSwitchTab: (tab: SidecarTab) => void;
  }
  ```
- Produces: `<RunList>` component

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/RunSidecar/RunList.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { RunList } from "./RunList";
import * as queries from "../../queries";
import * as editorStore from "../store";

vi.mock("../../queries", () => ({
  useRuns: vi.fn(),
  useRun: vi.fn(),
  useRerunRunMutation: vi.fn(),
}));
vi.mock("../store", () => ({
  useEditor: vi.fn(),
}));

const mockRuns = [
  {
    id: "run-1",
    status: "error",
    trigger_type: "manual",
    started_at: "2026-06-19T14:14:00Z",
    finished_at: "2026-06-19T14:14:01Z",
    node_runs: [
      { node_id: "n1", status: "success", output: null, error: null, duration_ms: 82 },
      { node_id: "n2", status: "error", output: null, error: "SMTP timeout", duration_ms: 920 },
    ],
  },
];

beforeEach(() => {
  vi.mocked(queries.useRuns).mockReturnValue({ data: mockRuns, isLoading: false } as ReturnType<typeof queries.useRuns>);
  vi.mocked(queries.useRun).mockReturnValue({ data: mockRuns[0], isLoading: false } as ReturnType<typeof queries.useRun>);
  vi.mocked(queries.useRerunRunMutation).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof queries.useRerunRunMutation>);
  vi.mocked(editorStore.useEditor).mockImplementation((selector) =>
    selector({ applyRunInfo: vi.fn(), clearRun: vi.fn(), runId: null, nodes: [] } as never)
  );
});

describe("RunList", () => {
  const defaultProps = {
    workflowId: "wf-1",
    selectedRunId: null,
    onSelectRun: vi.fn(),
    pinnedNodeId: null,
    onPinNode: vi.fn(),
    onSwitchTab: vi.fn(),
  };

  it("renders run rows from useRuns", () => {
    render(<RunList {...defaultProps} />);
    expect(screen.getByText("2:14 pm")).toBeTruthy();
    expect(screen.getByText("error")).toBeTruthy();
  });

  it("calls onSelectRun when a row is clicked", () => {
    const onSelectRun = vi.fn();
    render(<RunList {...defaultProps} onSelectRun={onSelectRun} />);
    fireEvent.click(screen.getByText("2:14 pm").closest(".sc-run-row")!);
    expect(onSelectRun).toHaveBeenCalledWith("run-1");
  });

  it("shows node inspector when a run is selected", () => {
    render(<RunList {...defaultProps} selectedRunId="run-1" />);
    expect(screen.getByText("n1")).toBeTruthy();
    expect(screen.getByText("n2")).toBeTruthy();
  });

  it("filters to errors when Errors chip is clicked", () => {
    const successRun = { ...mockRuns[0], id: "run-2", status: "success" };
    vi.mocked(queries.useRuns).mockReturnValue({ data: [mockRuns[0], successRun], isLoading: false } as ReturnType<typeof queries.useRuns>);
    render(<RunList {...defaultProps} />);
    fireEvent.click(screen.getByText("Errors"));
    expect(screen.getByText("error")).toBeTruthy();
    expect(screen.queryAllByText("success")).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunList.test.tsx
```
Expected: `Cannot find module './RunList'`

- [ ] **Step 3: Implement `RunList.tsx`**

```tsx
// apps/web/src/editor/RunSidecar/RunList.tsx
import { useState } from "react";
import { useRuns, useRerunRunMutation } from "../../queries";
import { useEditor, isTriggerManifest } from "../store";
import { useMountedRef } from "../../hooks/useMountedRef";
import type { RunInfo } from "../../types";
import type { SidecarTab } from "./useRunSidecar";

interface RunListProps {
  workflowId: string;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  pinnedNodeId: string | null;
  onPinNode: (nodeId: string) => void;
  onSwitchTab: (tab: SidecarTab) => void;
}

type Filter = "all" | "errors" | "manual";

function fmt(isoString: string): string {
  return new Date(isoString).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function durMs(run: RunInfo): string {
  if (!run.finished_at) return "…";
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

export function RunList({ workflowId, selectedRunId, onSelectRun, pinnedNodeId, onPinNode, onSwitchTab }: RunListProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const runsQuery = useRuns(workflowId, { staleTime: 5000 });
  const runs: RunInfo[] = (runsQuery.data ?? []) as RunInfo[];

  const applyRunInfo = useEditor((s) => s.applyRunInfo);
  const clearRun = useEditor((s) => s.clearRun);
  const rerunMutation = useRerunRunMutation();

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;

  const filtered = runs.filter((r) => {
    if (filter === "errors") return r.status === "error";
    if (filter === "manual") return r.trigger_type === "manual";
    return true;
  });

  function selectRun(run: RunInfo) {
    onSelectRun(run.id);
    applyRunInfo(run);
  }

  function copyInputs() {
    if (!selectedRun) return;
    const { nodes } = useEditor.getState();
    const triggerId = nodes.find((n) => isTriggerManifest(n.data.manifest))?.id;
    const triggerOut = selectedRun.node_runs.find((nr) => nr.node_id === triggerId)?.output
      ?? selectedRun.node_runs[0]?.output
      ?? {};
    void navigator.clipboard.writeText(JSON.stringify(triggerOut, null, 2));
  }

  const statusColor: Record<string, string> = {
    success: "var(--ok)",
    error: "var(--error)",
    running: "var(--accent)",
  };

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Run history</span>
        <span className="sc-head-badge">{runs.length} runs</span>
      </div>
      <div className="sc-filter-row">
        {(["all", "errors", "manual"] as const).map((f) => (
          <button
            key={f}
            className={`sc-filter-chip${filter === f ? " on" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>
      <div className="sc-run-list">
        {runsQuery.isLoading && <div style={{ padding: "12px", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>}
        {!runsQuery.isLoading && filtered.length === 0 && (
          <div style={{ padding: "12px", color: "var(--ink-3)", fontSize: 12 }}>No runs yet.</div>
        )}
        {filtered.map((r) => (
          <div
            key={r.id}
            className={`sc-run-row${selectedRunId === r.id ? " selected" : ""}`}
            onClick={() => selectRun(r)}
          >
            <div className="sc-run-top">
              <span className={`run-pill status-run-${r.status}`}>{r.status}</span>
              <span className="sc-run-time">{fmt(r.started_at)}</span>
              <span className="sc-run-dur">{durMs(r)}</span>
            </div>
            <div className="sc-run-meta">
              <span className="sc-trigger-chip">{r.trigger_type}</span>
              {new Date(r.started_at).toLocaleDateString()} · #{r.id.slice(0, 4)}
            </div>
          </div>
        ))}
      </div>

      {selectedRun && (
        <div className="sc-inspect">
          <div className="sc-insp-head">
            <span className="sc-insp-label">Node results</span>
            <span className="sc-insp-run-ref">#{selectedRun.id.slice(0, 4)}</span>
          </div>
          {selectedRun.node_runs.map((nr) => (
            <div
              key={nr.node_id}
              className={`sc-node-row${pinnedNodeId === nr.node_id ? " pinned" : ""}`}
              onClick={() => onPinNode(nr.node_id)}
            >
              <div
                className="sc-node-dot"
                style={{ background: statusColor[nr.status] ?? "var(--border)" }}
              />
              <span className="sc-node-name">{nr.node_id}</span>
              <span className="sc-node-status" style={{ color: statusColor[nr.status] ?? "var(--ink-3)" }}>
                {nr.status === "success" ? "✓" : nr.status === "error" ? "✗" : "–"}
              </span>
              <span className="sc-node-ms">
                {nr.duration_ms != null ? `${nr.duration_ms}ms` : "—"}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="sc-actions">
        <button
          className="sc-action-btn"
          disabled={!selectedRunId || rerunMutation.isPending}
          onClick={() => selectedRunId && rerunMutation.mutate(selectedRunId)}
        >
          ↺ Re-run
        </button>
        <button
          className="sc-action-btn primary"
          disabled={!selectedRun}
          onClick={copyInputs}
        >
          Copy inputs →
        </button>
      </div>
    </>
  );
}
```

**Note on `copyInputs`:** The trigger node ID lookup via `window.__noodleEditorNodes` is a workaround. In Task 9 when wiring `EditorPage`, pass `nodes` as a prop to `RunSidecar` instead. Replace this with `props.nodes.find(n => n.data.manifest?.role === 'trigger')?.id`.

- [ ] **Step 4: Remove `nodes` prop from `RunListProps` (no longer needed)**

`copyInputs` reads from `useEditor.getState()` directly (a store snapshot, safe to call outside render). Remove `nodes` from the props interface — `RunSidecar/index.tsx` does not need to pass it.

- [ ] **Step 5: Run tests**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunList.test.tsx
```
Expected: 4 tests pass

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/RunSidecar/RunList.tsx apps/web/src/editor/RunSidecar/RunList.test.tsx
git commit -m "feat(sidecar): add RunList component with filter, node inspector, actions"
```

---

### Task 4: `RunDiff.tsx` — Tab 2: two-run comparison

**Files:**
- Create: `apps/web/src/editor/RunSidecar/RunDiff.tsx`
- Test: `apps/web/src/editor/RunSidecar/RunDiff.test.tsx`

**Interfaces:**
- Consumes: `RunInfo`, `NodeRunResult` from `"../../types"`, `useRuns`, `useRun` from `"../../queries"`
- Props:
  ```ts
  interface RunDiffProps {
    workflowId: string;
    diffPair: [string, string] | null;
    onChangePair: (pair: [string, string]) => void;
  }
  ```
- Produces:
  - `diffNodeRows(a: RunInfo, b: RunInfo): DiffRow[]` — exported for testing
  - `<RunDiff>` component

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/RunSidecar/RunDiff.test.tsx
import { describe, expect, it } from "vitest";
import { diffNodeRows } from "./RunDiff";
import type { RunInfo } from "../../types";

function makeRun(nodeRuns: Array<{ node_id: string; status: string; duration_ms: number | null }>): RunInfo {
  return {
    id: "r",
    workflow_id: "wf",
    workflow_version: 1,
    mode: "manual",
    status: "success",
    trigger_type: "manual",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    node_runs: nodeRuns.map((n) => ({ ...n, output: null, error: null })),
  };
}

describe("diffNodeRows", () => {
  it("marks a node that went from error to success as improved", () => {
    const a = makeRun([{ node_id: "email", status: "error", duration_ms: 920 }]);
    const b = makeRun([{ node_id: "email", status: "success", duration_ms: 650 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("improved");
  });

  it("marks a node that went from success to error as worse", () => {
    const a = makeRun([{ node_id: "smtp", status: "success", duration_ms: 100 }]);
    const b = makeRun([{ node_id: "smtp", status: "error", duration_ms: 200 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("worse");
  });

  it("marks a node with same status but 2× slower duration as changed", () => {
    const a = makeRun([{ node_id: "fetch", status: "success", duration_ms: 100 }]);
    const b = makeRun([{ node_id: "fetch", status: "success", duration_ms: 210 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("changed");
  });

  it("marks unchanged nodes as none", () => {
    const a = makeRun([{ node_id: "webhook", status: "success", duration_ms: 80 }]);
    const b = makeRun([{ node_id: "webhook", status: "success", duration_ms: 82 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("none");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunDiff.test.tsx
```
Expected: `Cannot find module './RunDiff'`

- [ ] **Step 3: Implement `RunDiff.tsx`**

```tsx
// apps/web/src/editor/RunSidecar/RunDiff.tsx
import { useRuns, useRun } from "../../queries";
import type { RunInfo, NodeRunResult } from "../../types";

interface RunDiffProps {
  workflowId: string;
  diffPair: [string, string] | null;
  onChangePair: (pair: [string, string]) => void;
}

export type RowChange = "improved" | "worse" | "changed" | "none";

export interface DiffRow {
  nodeId: string;
  aStatus: string | null;
  bStatus: string | null;
  aDurationMs: number | null;
  bDurationMs: number | null;
  change: RowChange;
}

export function diffNodeRows(a: RunInfo, b: RunInfo): DiffRow[] {
  const allIds = Array.from(
    new Set([...a.node_runs.map((n) => n.node_id), ...b.node_runs.map((n) => n.node_id)])
  );
  return allIds.map((nodeId) => {
    const aNode = a.node_runs.find((n) => n.node_id === nodeId) ?? null;
    const bNode = b.node_runs.find((n) => n.node_id === nodeId) ?? null;

    const aStatus = aNode?.status ?? null;
    const bStatus = bNode?.status ?? null;
    const aDurationMs = aNode?.duration_ms ?? null;
    const bDurationMs = bNode?.duration_ms ?? null;

    let change: RowChange = "none";
    if (aStatus !== bStatus) {
      if (aStatus === "error" && bStatus === "success") change = "improved";
      else if (aStatus === "success" && bStatus === "error") change = "worse";
      else change = "changed";
    } else if (aDurationMs != null && bDurationMs != null && bDurationMs > aDurationMs * 1.5) {
      change = "changed";
    }
    return { nodeId, aStatus, bStatus, aDurationMs, bDurationMs, change };
  });
}

function statusSymbol(s: string | null): string {
  if (s === "success") return "✓";
  if (s === "error") return "✗";
  if (s == null) return "—";
  return s.slice(0, 1);
}

function statusColor(s: string | null): string {
  if (s === "success") return "var(--ok)";
  if (s === "error") return "var(--error)";
  return "var(--ink-3)";
}

function fmtMs(ms: number | null): string {
  return ms != null ? `${ms}ms` : "—";
}

export function RunDiff({ workflowId, diffPair, onChangePair }: RunDiffProps) {
  const runsQuery = useRuns(workflowId, { staleTime: 30_000 });
  const runs: RunInfo[] = (runsQuery.data ?? []) as RunInfo[];

  const [aId, bId] = diffPair ?? [runs[0]?.id ?? "", runs[1]?.id ?? ""];
  const aQuery = useRun(aId || null);
  const bQuery = useRun(bId || null);
  const aRun = aQuery.data as RunInfo | undefined;
  const bRun = bQuery.data as RunInfo | undefined;

  const rows = aRun && bRun ? diffNodeRows(aRun, bRun) : [];

  const improved = rows.filter((r) => r.change === "improved").length;
  const worse    = rows.filter((r) => r.change === "worse").length;
  const changed  = rows.filter((r) => r.change === "changed").length;

  return (
    <>
      <div className="sc-head"><span className="sc-head-title">Run diff</span></div>
      <div className="sc-diff-selectors">
        <select
          className="sc-diff-select"
          value={aId}
          onChange={(e) => onChangePair([e.target.value, bId])}
        >
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              #{r.id.slice(0, 6)} · {r.status}
            </option>
          ))}
        </select>
        <span style={{ color: "var(--border)" }}>vs</span>
        <select
          className="sc-diff-select"
          value={bId}
          onChange={(e) => onChangePair([aId, e.target.value])}
        >
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              #{r.id.slice(0, 6)} · {r.status}
            </option>
          ))}
        </select>
      </div>
      <div className="sc-diff-body">
        <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
          <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
            {/* Column A */}
            <div className="sc-diff-col">
              <div className="sc-diff-col-head">
                <div className="sc-node-dot" style={{ background: aRun?.status === "success" ? "var(--ok)" : "var(--error)" }} />
                #{aId.slice(0, 6)} · {aRun?.status ?? "—"}
              </div>
              {rows.map((row) => (
                <div key={row.nodeId} className={`sc-diff-row${row.change !== "none" ? ` ${row.change}` : ""}`}>
                  <div className="sc-node-dot" style={{ background: statusColor(row.aStatus) }} />
                  <span className="sc-diff-node-name">{row.nodeId}</span>
                  <span className="sc-diff-status" style={{ color: statusColor(row.aStatus) }}>{statusSymbol(row.aStatus)}</span>
                  <span className="sc-diff-ms">{fmtMs(row.aDurationMs)}</span>
                </div>
              ))}
            </div>
            <div className="sc-diff-divider" />
            {/* Column B */}
            <div className="sc-diff-col">
              <div className="sc-diff-col-head">
                <div className="sc-node-dot" style={{ background: bRun?.status === "success" ? "var(--ok)" : "var(--error)" }} />
                #{bId.slice(0, 6)} · {bRun?.status ?? "—"}
              </div>
              {rows.map((row) => (
                <div key={row.nodeId} className={`sc-diff-row${row.change !== "none" ? ` ${row.change}` : ""}`}>
                  <div className="sc-node-dot" style={{ background: statusColor(row.bStatus) }} />
                  <span className="sc-diff-node-name">{row.nodeId}</span>
                  <span className="sc-diff-status" style={{ color: statusColor(row.bStatus) }}>{statusSymbol(row.bStatus)}</span>
                  <span className="sc-diff-ms">{fmtMs(row.bDurationMs)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="sc-diff-footer">
        {improved > 0 && <span style={{ color: "var(--ok)" }}>+{improved} fixed</span>}
        {worse > 0    && <span style={{ color: "var(--error)" }}>-{worse} broke</span>}
        {changed > 0  && <span style={{ color: "var(--warn)" }}>{changed} changed</span>}
        {rows.length > 0 && improved === 0 && worse === 0 && changed === 0 && (
          <span>No differences</span>
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run tests**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunDiff.test.tsx
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/RunSidecar/RunDiff.tsx apps/web/src/editor/RunSidecar/RunDiff.test.tsx
git commit -m "feat(sidecar): add RunDiff component with diffNodeRows logic"
```

---

### Task 5: `RunTimeline.tsx` — Tab 3: node Gantt chart

**Files:**
- Create: `apps/web/src/editor/RunSidecar/RunTimeline.tsx`
- Test: `apps/web/src/editor/RunSidecar/RunTimeline.test.tsx`

**Interfaces:**
- Consumes: `RunInfo` from `"../../types"`, `useRun` from `"../../queries"`
- Props: `{ selectedRunId: string | null }`
- Produces:
  - `buildGanttBars(run: RunInfo): GanttBar[]` — exported for testing
  - `<RunTimeline>` component

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/RunSidecar/RunTimeline.test.tsx
import { describe, expect, it } from "vitest";
import { buildGanttBars } from "./RunTimeline";
import type { RunInfo } from "../../types";

function makeRun(overrides: Partial<RunInfo> = {}): RunInfo {
  return {
    id: "r1",
    workflow_id: "wf",
    workflow_version: 1,
    mode: "manual",
    status: "success",
    trigger_type: "manual",
    started_at: "2026-01-01T00:00:00.000Z",
    finished_at: "2026-01-01T00:00:01.000Z",
    node_runs: [
      { node_id: "webhook", status: "success", output: null, error: null, duration_ms: 100 },
      { node_id: "fetch",   status: "success", output: null, error: null, duration_ms: 400 },
      { node_id: "email",   status: "error",   output: null, error: "timeout", duration_ms: 500 },
    ],
    ...overrides,
  };
}

describe("buildGanttBars", () => {
  it("returns one bar per node_run", () => {
    expect(buildGanttBars(makeRun())).toHaveLength(3);
  });

  it("bar widths sum close to 100 when total = sum of durations", () => {
    const bars = buildGanttBars(makeRun());
    const total = bars.reduce((s, b) => s + b.widthPct, 0);
    expect(total).toBeCloseTo(100, 0);
  });

  it("assigns status to each bar", () => {
    const bars = buildGanttBars(makeRun());
    expect(bars.map((b) => b.status)).toEqual(["success", "success", "error"]);
  });

  it("handles missing duration_ms gracefully with zero width", () => {
    const run = makeRun();
    run.node_runs[0].duration_ms = null;
    const bars = buildGanttBars(run);
    expect(bars[0].widthPct).toBe(0);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunTimeline.test.tsx
```
Expected: `Cannot find module './RunTimeline'`

- [ ] **Step 3: Implement `RunTimeline.tsx`**

```tsx
// apps/web/src/editor/RunSidecar/RunTimeline.tsx
import { useRun } from "../../queries";
import type { RunInfo } from "../../types";

interface RunTimelineProps {
  selectedRunId: string | null;
}

export interface GanttBar {
  nodeId: string;
  status: string;
  leftPct: number;
  widthPct: number;
  durationMs: number | null;
}

export function buildGanttBars(run: RunInfo): GanttBar[] {
  const totalMs = run.node_runs.reduce((s, nr) => s + (nr.duration_ms ?? 0), 0);
  let cursor = 0;
  return run.node_runs.map((nr) => {
    const w = totalMs > 0 ? ((nr.duration_ms ?? 0) / totalMs) * 100 : 0;
    const bar: GanttBar = {
      nodeId: nr.node_id,
      status: nr.status,
      leftPct: cursor,
      widthPct: w,
      durationMs: nr.duration_ms ?? null,
    };
    cursor += w;
    return bar;
  });
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function bottleneck(bars: GanttBar[]): GanttBar | null {
  return bars.reduce<GanttBar | null>((max, b) => {
    if (max == null || (b.durationMs ?? 0) > (max.durationMs ?? 0)) return b;
    return max;
  }, null);
}

export function RunTimeline({ selectedRunId }: RunTimelineProps) {
  const runQuery = useRun(selectedRunId);
  const run = runQuery.data as RunInfo | undefined;

  if (!selectedRunId) {
    return (
      <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>
        Select a run to view its timeline.
      </div>
    );
  }
  if (runQuery.isLoading || !run) {
    return <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>;
  }

  const bars = buildGanttBars(run);
  const totalMs = run.node_runs.reduce((s, nr) => s + (nr.duration_ms ?? 0), 0);
  const neck = bottleneck(bars);

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Timeline</span>
        <span className="sc-head-badge">#{run.id.slice(0, 4)} · {fmtMs(totalMs)}</span>
      </div>
      <div className="sc-wf-body">
        <div className="sc-wf-ticks-row">
          <div className="sc-wf-tick-spacer" />
          <div className="sc-wf-ticks">
            <span className="sc-wf-tick" style={{ left: "0%" }}>0</span>
            <span className="sc-wf-tick" style={{ left: "50%" }}>{fmtMs(totalMs / 2)}</span>
            <span className="sc-wf-tick" style={{ left: "100%" }}>{fmtMs(totalMs)}</span>
          </div>
        </div>
        {bars.map((bar) => (
          <div key={bar.nodeId} className="sc-wf-row">
            <div className="sc-wf-name" title={bar.nodeId}>{bar.nodeId}</div>
            <div className="sc-wf-track">
              <div
                className={`sc-wf-bar ${bar.status === "success" ? "ok" : bar.status === "error" ? "error" : "skip"}`}
                style={{ left: `${bar.leftPct}%`, width: `${Math.max(bar.widthPct, 2)}%` }}
              >
                {bar.durationMs != null ? fmtMs(bar.durationMs) : ""}
              </div>
            </div>
          </div>
        ))}
        {neck && (
          <div className="sc-wf-critical">
            <div>Bottleneck: <span style={{ color: neck.status === "error" ? "var(--error)" : "var(--ink)" }}>{neck.nodeId}</span></div>
            <div style={{ color: "var(--ink-3)" }}>{Math.round(neck.widthPct)}% of total time</div>
          </div>
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run tests**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunTimeline.test.tsx
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/RunSidecar/RunTimeline.tsx apps/web/src/editor/RunSidecar/RunTimeline.test.tsx
git commit -m "feat(sidecar): add RunTimeline Gantt component"
```

---

### Task 6: `RunStats.tsx` — Tab 4: reliability filter + heat strip

**Files:**
- Create: `apps/web/src/editor/RunSidecar/RunStats.tsx`
- Test: `apps/web/src/editor/RunSidecar/RunStats.test.tsx`

**Interfaces:**
- Consumes: `useRuns` from `"../../queries"`, `RunInfo` from `"../../types"`
- Props: `{ workflowId: string }`
- Produces:
  - `nodeReliability(runs: RunInfo[], nodeId: string): { ok: number; err: number; skip: number }` — exported
  - `<RunStats>` component

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/RunSidecar/RunStats.test.tsx
import { describe, expect, it } from "vitest";
import { nodeReliability } from "./RunStats";
import type { RunInfo } from "../../types";

function makeRun(id: string, nodeStatuses: Record<string, string>): RunInfo {
  return {
    id,
    workflow_id: "wf",
    workflow_version: 1,
    mode: "manual",
    status: "success",
    trigger_type: "manual",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    node_runs: Object.entries(nodeStatuses).map(([node_id, status]) => ({
      node_id, status, output: null, error: null, duration_ms: null,
    })),
  };
}

describe("nodeReliability", () => {
  it("counts successes and errors across runs", () => {
    const runs = [
      makeRun("r1", { email: "success" }),
      makeRun("r2", { email: "error" }),
      makeRun("r3", { email: "success" }),
    ];
    const rel = nodeReliability(runs, "email");
    expect(rel).toEqual({ ok: 2, err: 1, skip: 0 });
  });

  it("counts skipped nodes", () => {
    const runs = [
      makeRun("r1", { slack: "skipped" }),
      makeRun("r2", { slack: "success" }),
    ];
    const rel = nodeReliability(runs, "slack");
    expect(rel).toEqual({ ok: 1, err: 0, skip: 1 });
  });

  it("returns zeros for a node not present in any run", () => {
    const runs = [makeRun("r1", { webhook: "success" })];
    expect(nodeReliability(runs, "missing-node")).toEqual({ ok: 0, err: 0, skip: 0 });
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunStats.test.tsx
```

- [ ] **Step 3: Implement `RunStats.tsx`**

```tsx
// apps/web/src/editor/RunSidecar/RunStats.tsx
import { useState } from "react";
import { useRuns } from "../../queries";
import type { RunInfo } from "../../types";

interface RunStatsProps {
  workflowId: string;
}

export function nodeReliability(
  runs: RunInfo[],
  nodeId: string
): { ok: number; err: number; skip: number } {
  let ok = 0, err = 0, skip = 0;
  for (const run of runs) {
    const nr = run.node_runs.find((n) => n.node_id === nodeId);
    if (!nr) continue;
    if (nr.status === "success") ok++;
    else if (nr.status === "error") err++;
    else skip++;
  }
  return { ok, err, skip };
}

const HEAT_WINDOW = 10;

function HeatStrip({ statuses }: { statuses: string[] }) {
  const recent = statuses.slice(-HEAT_WINDOW);
  return (
    <div className="sc-heat-strip">
      {recent.map((s, i) => (
        <div
          key={i}
          className={`sc-heat-block ${s === "success" ? "ok" : s === "error" ? "err" : "skip"}`}
          title={s}
        />
      ))}
    </div>
  );
}

export function RunStats({ workflowId }: RunStatsProps) {
  const [nodeFilter, setNodeFilter] = useState("");
  const runsQuery = useRuns(workflowId, { staleTime: 10_000 });
  const runs: RunInfo[] = (runsQuery.data ?? []) as RunInfo[];

  const allNodeIds = Array.from(
    new Set(runs.flatMap((r) => r.node_runs.map((n) => n.node_id)))
  ).filter((id) => !nodeFilter || id.includes(nodeFilter));

  const statusColor: Record<string, string> = {
    success: "var(--ok)",
    error: "var(--error)",
  };

  function fmt(iso: string) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function durLabel(run: RunInfo) {
    if (!run.finished_at) return "…";
    const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
    return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
  }

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Reliability</span>
        <span className="sc-head-badge">{runs.length} runs</span>
      </div>
      <div className="sc-head" style={{ height: "auto", padding: "6px 12px" }}>
        <input
          style={{ flex: 1, background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 5, padding: "4px 8px", fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--ink)" }}
          placeholder="filter by node…"
          value={nodeFilter}
          onChange={(e) => setNodeFilter(e.target.value)}
        />
      </div>
      <div className="sc-stats-body">
        {runs.map((run) => {
          const visibleNodeRuns = run.node_runs.filter(
            (nr) => !nodeFilter || nr.node_id.includes(nodeFilter)
          );
          if (visibleNodeRuns.length === 0 && nodeFilter) return null;
          return (
            <div key={run.id} className="sc-stats-row">
              <div className="sc-stats-top">
                <span className={`run-pill status-run-${run.status}`}>{run.status}</span>
                <span style={{ flex: 1, fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
                  {fmt(run.started_at)}
                </span>
                <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--ink-3)" }}>
                  {durLabel(run)}
                </span>
              </div>
              <div className="sc-node-tags">
                {visibleNodeRuns.map((nr) => (
                  <span
                    key={nr.node_id}
                    className={`sc-node-tag ${nr.status === "success" ? "ok" : nr.status === "error" ? "err" : "skip"}`}
                  >
                    {nr.node_id} {nr.status === "success" ? "✓" : nr.status === "error" ? "✗" : "–"}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
        {nodeFilter && allNodeIds.length > 0 && (
          <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border)" }}>
            {allNodeIds.map((nodeId) => {
              const rel = nodeReliability(runs, nodeId);
              const statuses = runs
                .map((r) => r.node_runs.find((n) => n.node_id === nodeId)?.status ?? "skip")
                .filter(Boolean);
              return (
                <div key={nodeId} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--ink)", marginBottom: 4 }}>
                    {nodeId} — {rel.ok}/{rel.ok + rel.err + rel.skip} ok
                  </div>
                  <HeatStrip statuses={statuses} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run tests**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunStats.test.tsx
```
Expected: 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/RunSidecar/RunStats.tsx apps/web/src/editor/RunSidecar/RunStats.test.tsx
git commit -m "feat(sidecar): add RunStats reliability/heat-strip component"
```

---

### Task 7: `RunInfo.tsx` — Tab 5: trigger payload + note

**Files:**
- Create: `apps/web/src/editor/RunSidecar/RunInfo.tsx`

**Interfaces:**
- Consumes: `useRun` from `"../../queries"`, `RunInfo` types
- Props: `{ selectedRunId: string | null; onOpenDiff: () => void }`
- Note: stored in `localStorage` at key `run-note-${runId}`

- [ ] **Step 1: Implement `RunInfo.tsx`**

No separate test file — this component is entirely UI rendering (localStorage + display). Manual testing in browser covers it.

```tsx
// apps/web/src/editor/RunSidecar/RunInfo.tsx
import { useState, useEffect } from "react";
import { useRun } from "../../queries";
import { safeGetItem, safeSetItem } from "../../safeStorage";
import type { RunInfo as RunInfoType } from "../../types";

interface RunInfoProps {
  selectedRunId: string | null;
  onOpenDiff: () => void;
}

function getNoteKey(runId: string) {
  return `run-note-${runId}`;
}

export function RunInfo({ selectedRunId, onOpenDiff }: RunInfoProps) {
  const runQuery = useRun(selectedRunId);
  const run = runQuery.data as RunInfoType | undefined;
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!selectedRunId) return;
    setNote(safeGetItem(getNoteKey(selectedRunId)) ?? "");
  }, [selectedRunId]);

  function saveNote(value: string) {
    if (!selectedRunId) return;
    setNote(value);
    safeSetItem(getNoteKey(selectedRunId), value);
  }

  if (!selectedRunId) {
    return (
      <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>
        Select a run to view details.
      </div>
    );
  }

  if (runQuery.isLoading || !run) {
    return <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>;
  }

  const triggerOutput = run.node_runs[0]?.output ?? null;
  const payloadStr = triggerOutput != null
    ? JSON.stringify(triggerOutput, null, 2)
    : `// trigger_type: ${run.trigger_type}\n// No payload captured`;

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Run info</span>
        <span className="sc-head-badge">#{run.id.slice(0, 6)}</span>
      </div>
      <div className="sc-info-body">
        <div className="sc-info-section">
          <div className="sc-info-sec-head">
            <span>Trigger payload</span>
            <span style={{ color: "var(--accent)", fontSize: "9px" }}>{run.trigger_type}</span>
          </div>
          <pre className="sc-info-payload">{payloadStr}</pre>
        </div>
        <div className="sc-info-section">
          <div className="sc-info-sec-head">Note</div>
          <textarea
            className="sc-info-note"
            placeholder="Add a note to this run…"
            value={note}
            onChange={(e) => saveNote(e.target.value)}
          />
        </div>
        <div className="sc-info-section" style={{ padding: "10px 12px" }}>
          <button
            className="sc-action-btn"
            style={{ width: "100%" }}
            onClick={onOpenDiff}
          >
            Compare with another run →
          </button>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/editor/RunSidecar/RunInfo.tsx
git commit -m "feat(sidecar): add RunInfo tab with trigger payload and local note"
```

---

### Task 8: `RunSidecar/index.tsx` — shell with tab navigation

**Files:**
- Create: `apps/web/src/editor/RunSidecar/index.tsx`
- Test: `apps/web/src/editor/RunSidecar/RunSidecar.test.tsx`

**Interfaces:**
- Consumes: All tabs from Tasks 3–7, `useRunSidecar` from Task 1
- Props:
  ```ts
  interface RunSidecarProps {
    workflowId: string;
  }
  ```
- Produces: `<RunSidecar>` component; also exports `RunSidecar`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/RunSidecar/RunSidecar.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { RunSidecar } from "./index";
import * as queries from "../../queries";

vi.mock("../../queries", () => ({
  useRuns: vi.fn(),
  useRun: vi.fn(),
  useRerunRunMutation: vi.fn(),
  useRunTimeline: vi.fn(),
}));
vi.mock("../store", () => ({
  useEditor: vi.fn((selector) =>
    selector({ applyRunInfo: vi.fn(), clearRun: vi.fn(), runId: null, nodes: [] })
  ),
}));

beforeEach(() => {
  vi.mocked(queries.useRuns).mockReturnValue({ data: [], isLoading: false } as ReturnType<typeof queries.useRuns>);
  vi.mocked(queries.useRun).mockReturnValue({ data: null, isLoading: false } as ReturnType<typeof queries.useRun>);
  vi.mocked(queries.useRerunRunMutation).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof queries.useRerunRunMutation>);
});

describe("RunSidecar", () => {
  const props = { workflowId: "wf-1" };

  it("renders 5 tabs", () => {
    render(<RunSidecar {...props} />);
    expect(screen.getByText("Runs")).toBeTruthy();
    expect(screen.getByText("Diff")).toBeTruthy();
    expect(screen.getByText("Time")).toBeTruthy();
    expect(screen.getByText("Stats")).toBeTruthy();
    expect(screen.getByText("Info")).toBeTruthy();
  });

  it("shows the Runs panel by default", () => {
    render(<RunSidecar {...props} />);
    expect(screen.getByText("Run history")).toBeTruthy();
  });

  it("switches to Diff panel on tab click", () => {
    render(<RunSidecar {...props} />);
    fireEvent.click(screen.getByText("Diff"));
    expect(screen.getByText("Run diff")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunSidecar.test.tsx
```

- [ ] **Step 3: Implement `RunSidecar/index.tsx`**

```tsx
// apps/web/src/editor/RunSidecar/index.tsx
import { useRunSidecar, type SidecarTab } from "./useRunSidecar";
import { RunList } from "./RunList";
import { RunDiff } from "./RunDiff";
import { RunTimeline } from "./RunTimeline";
import { RunStats } from "./RunStats";
import { RunInfo } from "./RunInfo";

interface RunSidecarProps {
  workflowId: string;
}

const TABS: Array<{ id: SidecarTab; icon: string; label: string; title: string }> = [
  { id: "runs",     icon: "≡", label: "Runs",  title: "Run history" },
  { id: "diff",     icon: "⇄", label: "Diff",  title: "Compare two runs" },
  { id: "timeline", icon: "◫", label: "Time",  title: "Execution waterfall" },
  { id: "stats",    icon: "⬡", label: "Stats", title: "Reliability" },
  { id: "info",     icon: "◎", label: "Info",  title: "Trigger payload & notes" },
];

export function RunSidecar({ workflowId }: RunSidecarProps) {
  const {
    activeTab, setActiveTab,
    selectedRunId, setSelectedRunId,
    pinnedNodeId, setPinnedNodeId,
    diffPair, setDiffPair,
  } = useRunSidecar();

  const applyRunInfo = useEditor((s) => s.applyRunInfo);

  return (
    <aside className="run-sidecar">
      {/* Tab bar */}
      <nav className="sc-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`sc-tab${activeTab === tab.id ? " active" : ""}`}
            title={tab.title}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="sc-tab-icon">{tab.icon}</span>
            <span className="sc-tab-lbl">{tab.label}</span>
          </button>
        ))}
      </nav>

      {/* Panels */}
      <div className={`sc-panel${activeTab === "runs" ? " active" : ""}`} role="tabpanel">
        <RunList
          workflowId={workflowId}
          selectedRunId={selectedRunId}
          onSelectRun={setSelectedRunId}
          pinnedNodeId={pinnedNodeId}
          onPinNode={setPinnedNodeId}
          onSwitchTab={setActiveTab}
        />
      </div>

      <div className={`sc-panel${activeTab === "diff" ? " active" : ""}`} role="tabpanel">
        <RunDiff
          workflowId={workflowId}
          diffPair={diffPair}
          onChangePair={setDiffPair}
        />
      </div>

      <div className={`sc-panel${activeTab === "timeline" ? " active" : ""}`} role="tabpanel">
        <RunTimeline selectedRunId={selectedRunId} />
      </div>

      <div className={`sc-panel${activeTab === "stats" ? " active" : ""}`} role="tabpanel">
        <RunStats workflowId={workflowId} />
      </div>

      <div className={`sc-panel${activeTab === "info" ? " active" : ""}`} role="tabpanel">
        <RunInfo
          selectedRunId={selectedRunId}
          onOpenDiff={() => setActiveTab("diff")}
        />
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: Run tests**

```
cd apps/web && npx vitest run src/editor/RunSidecar/RunSidecar.test.tsx
```
Expected: 3 tests pass

- [ ] **Step 5: Run full test suite**

```
cd apps/web && npx vitest run
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/RunSidecar/index.tsx apps/web/src/editor/RunSidecar/RunSidecar.test.tsx apps/web/src/editor/RunSidecar/RunInfo.tsx
git commit -m "feat(sidecar): add RunSidecar shell with 5 tab panels"
```

---

### Task 9: `EditorPage.tsx` integration — wire sidecar, remove old dropdown

**Files:**
- Modify: `apps/web/src/EditorPage.tsx`

**Interfaces:**
- Consumes: `RunSidecar` from `"./editor/RunSidecar"`, `useEditor` for `runId`, `running`, `clearRun`

The goals are:
1. Remove the `runs-menu` dropdown div and all related state (`runsOpen`, `runsList`, `runsMenuRef`, `openRuns`, the `viewRun` function body — but keep a simplified `viewRun` for `ChatPanel`)
2. Add `[sidecarOpen, setSidecarOpen]` state (default `false`)
3. Replace the "Runs ▾" button with a "Runs" toggle that opens/closes the sidecar
4. Add the `<RunSidecar>` component inside `editor-body` after `editor-stage`
5. Add the exec banner inside `editor-stage` when `runId && !running`

- [ ] **Step 1: Add `sidecarOpen` state and import `RunSidecar`**

At the top of `EditorPage.tsx`, add the import:
```tsx
import { RunSidecar } from "./editor/RunSidecar";
```

Near the other `useState` calls (around line 291), add:
```tsx
const [sidecarOpen, setSidecarOpen] = useState(false);
```

Near the `applyRunInfo` selector (around line 384), add:
```tsx
const runId = useEditor((s) => s.runId);
const running = useEditor((s) => s.running);
const clearRun = useEditor((s) => s.clearRun);
const runHasError = useEditor((s) => Object.values(s.runStatus).some((st) => st === "error"));
```
(`nodes` is not needed — `copyInputs` reads from `useEditor.getState()` inside the event handler.)

- [ ] **Step 2: Simplify `viewRun` — remove `setRunsOpen`**

Find the `viewRun` function (around line 1312). Remove the `setRunsOpen(false)` call. The simplified version:
```tsx
async function viewRun(runId: string): Promise<void> {
  try {
    const run = await api.getRun(runId);
    applyRunInfo(run);
    setSidecarOpen(true);
  } catch (err) {
    setMessage(String(err));
  }
}
```

- [ ] **Step 3: Remove old dropdown state**

Remove these lines from `EditorPage.tsx`:
- `const [runsOpen, setRunsOpen] = useState(false);` (around line 291)
- `const [runsList, setRunsList] = useState<RunInfo[]>([]);` (around line 292)
- `const runsMenuRef = useRef<HTMLDivElement>(null);` — find and remove
- The `useEffect` block that handles click-outside for `runsOpen`
- The `async function openRuns()` function entirely
- The `clearRun` selector (now added in Step 1 if not already present)

- [ ] **Step 4: Replace the toolbar "Runs ▾" button with a toggle**

Find the `<div className="runs-menu" ref={runsMenuRef}>` block (around line 1398) and replace the entire block (from the opening `<div className="runs-menu"` to its closing `</div>`) with:

```tsx
<button
  className={`btn${sidecarOpen ? " active" : ""}`}
  onClick={() => setSidecarOpen((v) => !v)}
  title="Run history"
>
  Runs
</button>
```

- [ ] **Step 5: Add exec banner inside `editor-stage`**

Inside `<div className="editor-stage">`, after `<Canvas />`, add:

```tsx
{runId && !running && (
  <div className="run-exec-banner">
    <div className="run-exec-banner-dot" />
    <span className="run-exec-banner-label">Viewing</span>
    <span className="run-exec-banner-id">#{runId.slice(0, 6)}</span>
    <span className="run-exec-banner-meta">
      {Object.values(useEditor.getState().runStatus).filter((s) => s === "error").length > 0
        ? "· error"
        : "· success"}
    </span>
    <div className="run-exec-banner-end">
      <button className="run-exec-banner-exit" onClick={clearRun}>
        Exit run view
      </button>
    </div>
  </div>
)}
```

**Note:** `useEditor.getState()` inside JSX is a Zustand anti-pattern (no reactivity). Use derived values from the selectors already at the top of the component. Replace the `runStatus` access with:

```tsx
const runHasError = useEditor((s) => Object.values(s.runStatus).some((st) => st === "error"));
```

Add that selector alongside the other `useEditor` calls in Step 1, then:

```tsx
{runId && !running && (
  <div className="run-exec-banner">
    <div className="run-exec-banner-dot" />
    <span className="run-exec-banner-label">Viewing</span>
    <span className="run-exec-banner-id">#{runId.slice(0, 6)}</span>
    <span className="run-exec-banner-meta">
      {runHasError ? " · error" : " · success"}
    </span>
    <div className="run-exec-banner-end">
      <button className="run-exec-banner-exit" onClick={clearRun}>
        Exit run view
      </button>
    </div>
  </div>
)}
```

- [ ] **Step 6: Add `<RunSidecar>` inside `editor-body`**

Inside `<div className="editor-body">`, after `</div>{/* /editor-stage */}` and before `<Inspector />`, add:

```tsx
{sidecarOpen && id && (
  <RunSidecar workflowId={id} />
)}
```

- [ ] **Step 7: Run the full test suite**

```
cd apps/web && npx vitest run
```
Expected: all tests pass

- [ ] **Step 8: Start the dev server and verify manually**

```
cd apps/web && npm run dev
```

Open a workflow with at least one run. Verify:
1. "Runs" button in toolbar toggles the sidecar open/closed
2. Sidecar shows run list; clicking a run loads execution view on canvas
3. Nodes on canvas show status colors, badges, duration chips
4. Exec banner appears at top of canvas with "Exit run view"
5. Clicking "Exit run view" calls `clearRun()` — canvas returns to live editing
6. All 5 tabs navigate correctly
7. Node inspector shows per-node status when a run is selected
8. "Copy inputs →" copies JSON to clipboard
9. Re-run button triggers `useRerunRunMutation`
10. Existing canvas interactions (drag, box-select, hand pan, zoom) are unaffected

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/EditorPage.tsx
git commit -m "feat(sidecar): wire RunSidecar into EditorPage, remove runs dropdown"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| Replace dropdown with persistent sidecar | Task 9 |
| Tab 1: run list + filter chips | Task 3 |
| Tab 1: node inspector with per-node status | Task 3 |
| Tab 1: Re-run action | Task 3 (`useRerunRunMutation`) |
| Tab 1: Copy inputs → | Task 3 |
| Tab 2: Diff two-run comparison | Task 4 |
| Tab 2: Delta badges (improved/worse/changed) | Task 4 (`diffNodeRows`) |
| Tab 3: Gantt waterfall proportional bars | Task 5 |
| Tab 3: Bottleneck summary | Task 5 |
| Tab 4: Node tags per run | Task 6 |
| Tab 4: Reliability heat strip | Task 6 |
| Tab 5: Trigger payload | Task 7 |
| Tab 5: Run note | Task 7 (localStorage) |
| Exec banner with "Exit run view" | Task 9 |
| Canvas execution view (node colors) | Existing NodeCard — no change needed |
| No regression to existing canvas interactions | Task 9 Step 8 manual test |
| CSS styles for all new elements | Task 2 |

**Type consistency check:**
- `SidecarTab` defined in `useRunSidecar.ts`, imported by `index.tsx` and `RunList.tsx` — consistent
- `RunInfo`, `NodeRunResult` imported from `"../../types"` in all components — consistent
- `diffNodeRows` accepts `RunInfo` and returns `DiffRow[]` — types match Task 4 test
- `buildGanttBars` accepts `RunInfo` and returns `GanttBar[]` — types match Task 5 test
- `nodeReliability` accepts `RunInfo[]` and `string`, returns `{ ok, err, skip }` — matches Task 6 test

**Placeholder scan:** None found — all steps have complete code.

**Scope check:** This is a single cohesive frontend feature. No backend changes required (trigger payload derived from `node_runs[0].output`, notes in localStorage).
