# Run Sidecar Design

**Date:** 2026-06-20  
**Branch:** feat/node-expansion-plan  
**Status:** Approved — ready for implementation

---

## Overview

Replace the existing inline "Runs ▾" dropdown in `EditorPage.tsx` with a persistent right-side panel (the **Run Sidecar**) that shows workflow run history and renders any selected run as an execution view directly on the canvas. Users can click a past run to see node statuses, inspect outputs per node, compare runs, view the execution timeline, filter by reliability, and copy inputs back to the editor.

This is meaningfully more powerful than n8n's execution history because it integrates the execution view directly into the live canvas without page navigation, and adds analytical views (diff, waterfall, reliability heatmap, trigger payload) all in one panel.

---

## Visual Design

The sidecar is a 272px fixed-width right panel attached to the editor canvas. It contains:

- A **5-tab navigation bar** at the top (`Runs | Diff | Time | Stats | Info`)
- A **canvas area** (unchanged `Canvas.tsx`) that enters "execution view" when a run is selected — nodes gain status classes (`run-success`, `run-error`, `run-skipped`), glow rings, duration chips, and inline output chips on click
- An **"Exit run view"** banner at the top of the canvas while a run is loaded
- A **bottom action bar** on the Runs tab with `Re-run` and `Copy inputs →` buttons

Reference mockup: `D:\noodle\.superpowers\brainstorm\3511-1781913034\content\sidecar-final.html`

---

## Tab Breakdown

### Tab 1 — Runs (default)
The primary view. Always shown on first open.

- **Header:** "Run history" + total count badge + search + collapse icon
- **Filter chips:** All / Errors / Manual — filter the run list
- **Run list (scrollable):** Each row shows status pill, time, duration, trigger type, run ID. Clicking a row calls `applyRunInfo(run)` to load execution state onto the canvas. The selected row gets the animated indigo left-border trace.
- **Node results inspector:** Below the list, shows per-node status (`✓` / `✗` / `–`) and duration for the selected run. Clicking a node row pins that node on canvas (shows output chip).
- **Action bar:**
  - `Re-run` — re-triggers the selected run's workflow with the same inputs
  - `Copy inputs →` — copies the run's trigger payload into the editor's manual trigger input

### Tab 2 — Diff
Compare any two runs side by side.

- **Run selectors:** Two dropdowns (defaulting to the two most recent runs)
- **Diff table:** Split view, one column per run. Each row is a node. Rows with status changes are highlighted: green background for "now fixed", red for "newly broken", amber for regressions (e.g. "2× slower"). Delta badges show the change at a glance.
- **Footer:** Summary line ("2 nodes fixed · transform 2× slower")

### Tab 3 — Time (Waterfall)
Execution timeline for the selected run.

- **Header:** Selected run ID + total duration
- **Gantt rows:** One row per node. Bar width is proportional to `duration_ms`. Each bar shows the node name and duration. Color: green for success, red for error, grey/dashed for skipped.
- **Tick axis:** Time markers at 0ms, midpoint, and total
- **Critical path summary:** Text footer identifying the longest sequential chain and the bottleneck node (highest % of total time)

### Tab 4 — Stats (Reliability)
Filter runs by node outcome and view per-node reliability.

- **Search bar:** Free-text filter by node name
- **Run list with node tags:** Each run row shows per-node status badges inline (e.g. `webhook ✓`, `send email ✗`). Runs are filterable by clicking a node tag.
- **Heat strip:** Below the filter, a 10-run horizontal strip showing node reliability history (green = ok, red = error, grey = skipped) with a label ("email: 4/10 ok")
- **Pattern warning:** Runs that share the same error as a prior run show an amber "⚠ Same error as [date]" hint

### Tab 5 — Info (Trigger Payload + Annotations)
Details for the selected run.

- **Trigger payload:** JSON viewer showing the raw webhook/schedule/manual input that started the run
- **Note field:** Free-text annotation pinned to this run (stored in run metadata)
- **Multi-run selector:** Checkboxes to pick runs for the Diff tab; clicking "Open diff →" switches to Tab 2 with those runs pre-selected

---

## Component Architecture

### New components

```
apps/web/src/editor/
  RunSidecar/
    index.tsx               — main sidecar shell (tab state, layout)
    RunList.tsx             — scrollable run list + filter chips
    NodeInspector.tsx       — per-node status rows for selected run
    RunDiff.tsx             — two-run comparison view
    RunTimeline.tsx         — waterfall / Gantt chart
    RunStats.tsx            — reliability filter + heat strip
    RunInfo.tsx             — trigger payload + annotations
    useRunSidecar.ts        — local UI state (active tab, pinned node, diff pair)
```

### Modified components

| File | Change |
|------|--------|
| `apps/web/src/editor/EditorPage.tsx` | Remove inline "Runs ▾" dropdown; render `<RunSidecar>` as a sibling to `<Canvas>` |
| `apps/web/src/editor/Canvas.tsx` | Add `execBannerVisible` prop; show exit-run-view banner when a run is loaded |
| `apps/web/src/editor/NodeCard.tsx` | No change needed — already reads from `runStatus`, `runMeta`, `runOutputs` in Zustand |
| `apps/web/src/queries/index.ts` | Ensure `useRuns`, `useRun`, `useRunTimeline` are exported and used by sidecar components |

### State

All execution view state lives in the existing Zustand run slice:
- `applyRunInfo(run)` — bulk-loads a completed run onto canvas
- `clearRun()` — resets to live editing mode
- `runStatus`, `runMeta`, `runOutputs` — read by NodeCard already

The sidecar adds local UI state via `useRunSidecar.ts` (no Zustand needed):
- `activeTab: 'runs' | 'diff' | 'timeline' | 'stats' | 'info'`
- `pinnedNodeId: string | null`
- `diffPair: [string, string] | null`
- `selectedRunId: string | null`

### Data flow

```
User clicks run row
  → useRun(runId) fetches full RunInfo
  → applyRunInfo(run) updates Zustand
  → Canvas re-renders with NodeCard status classes
  → NodeInspector renders node rows from run.node_runs
  → User clicks node row → pinnedNodeId set → NodeCard output chip shown
```

---

## Layout

```
┌─────────────────────────────────────────────────────────┐
│ ● ● ●   Workflow Name — Noodle              [titlebar]   │
├─────────────────────────────────────────────────────────┤
│ noodle / Workflow Name         [Undo] [Save] [▶ Run]     │
├──────────────────────────────┬──────────────────────────┤
│ [exec banner — run #id]      │ [≡ Runs][⇄][◫][⬡][◎]   │
│                              │                          │
│   Canvas (React Flow)        │  (active tab content)   │
│   NodeCards with status      │                          │
│   classes + output chips     │  [Re-run] [Copy inputs→]│
└──────────────────────────────┴──────────────────────────┘
```

Sidecar width: 272px (fixed). Canvas flex: 1 (fills remaining space). No resizing in v1.

---

## API Usage

| Hook | Endpoint | Used by |
|------|----------|---------|
| `useRuns(workflowId)` | `GET /workflows/<id>/runs` | RunList — paginated list |
| `useRun(runId)` | `GET /runs/<id>` | on row click → applyRunInfo |
| `useRunTimeline(runId)` | `GET /runs/<id>/timeline` | RunTimeline tab |

`Re-run` calls: `POST /runs/<id>/rerun` (new endpoint needed).  
Run annotations: `PATCH /runs/<id>` with `{ note: string }` (new field + endpoint).

---

## What Is NOT in v1

- Resizable sidecar width
- Real-time live-run streaming in the sidecar (existing websocket `clearRun`/`startRun` flow unchanged)
- Run deletion from the sidecar
- Sharing a run permalink
- The Stats heat strip filtering runs by node click (shown in mockup but requires extra query)

---

## Acceptance Criteria

1. Clicking a run row loads the canvas into execution view — nodes show correct status colors, badges, and duration chips
2. Clicking a node row in the inspector (or on canvas) shows that node's output chip
3. "Exit run view" banner button clears execution state and returns to live editing
4. "Copy inputs →" populates the editor's manual trigger input with the run's trigger payload
5. Diff tab correctly highlights changed/improved/regressed nodes between two selected runs
6. Timeline tab renders a proportional Gantt chart for the selected run
7. Stats tab shows per-node tags and heat strip across recent runs
8. Info tab shows the trigger payload JSON and accepts a free-text note
9. Sidecar is absent (or collapsed) when no workflow is open
10. No regression to existing canvas interactions (node drag, box select, hand pan, zoom)
