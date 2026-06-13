# Runner UI Polish Design

**Date:** 2026-06-14
**Status:** Draft

## Overview

Two surfaces for runner pool selection need polishing. The env edit modal currently uses a plain `<select>` with no status information. The workflow editor toolbar has no runner selector at all, requiring users to navigate to the env settings to change runner pools per-workflow.

This spec covers both surfaces with consistent styling and a clear interaction model.

---

## Surface 1 — Env Edit Modal (PoolSelect polish)

### Current state

`EnvironmentsPage.tsx` renders a plain `<select>` element inside `EditEnvModal` and the create modal. No status dots, no runner count, no provider badge.

### Design

Replace the `<select>` with a **radio tile list**:

- One tile per runner pool option (plus a "Local (in-process)" tile at the top as the default/none option)
- Each tile shows: pool name, concurrency limit (sub-text), online runner count (right-aligned, green dot), provider tag badge (agent / k8s)
- Selected tile gets a teal outline + filled radio dot
- **Max-height constraint**: list is capped at ~3 tiles visible (≈ 160px); overflows with a thin scrollbar. Selected tile scrolls into view on open.
- When there are no pools other than local, only the local tile is shown (no scrollbar needed)

### Tile anatomy

```
┌─────────────────────────────────────────────────┐
│  ◉  prod-pool                        ● 2/3      │
│     max 4 concurrent                   agent    │
└─────────────────────────────────────────────────┘
```

- Left: radio button (filled when selected)
- Center: pool name (bold), sub-text with concurrency cap
- Right: `● N/M` online count (green = any online, gray = none) + provider tag

### Data

The tile list is built from the existing `GET /runner-pools` response (already fetched on `EnvironmentsPage`). The "Local" option is always prepended and represents `runner_pool_id: null`.

---

## Surface 2 — Workflow Editor Toolbar (new runner selector)

### Current state

The toolbar has an env `<select>` and a timeout input. There is no per-workflow runner override. The backend `Workflow` model has a `default_runner_pool_id` field (nullable), and `WorkflowUpdate` already accepts it, but `WorkflowDetail` does not expose it and no frontend type or API call references it.

### Design

**Toolbar chip (always visible):**

- Replaces the raw env `<select>` with a stacked chip
- When a runner override is set: chip shows env name on top line, runner name on bottom line with a green status dot
- When no runner override (local / not set): chip shows only env name — no sub-label, no runner indicator
- Chip has a subtle caret (`▾`) on the env line to indicate it's clickable

```
┌─────────────────────┐      ┌─────────────────────┐
│  Global env       ▾ │      │  Global env       ▾ │
│  ● prod-pool        │      │                     │  ← local: no sub-label
└─────────────────────┘      └─────────────────────┘
   runner set                   local / not set
```

**Popover (opens on chip click):**

- Title: "Run settings"
- Section 1 — **Environment**: styled dropdown (custom `<select>` replacement — simple list, no tiles, envs have no status metadata) listing all available envs
- Divider
- Section 2 — **Runner override**: styled dropdown listing runner pools + a "— No override (use env default) —" first option. When "no override" is selected and the effective runner resolves to local, it shows `● Local (in-process)` grayed out as a hint.
- Popover closes on outside click or Escape

**Persistence:**

- Changing env in the popover triggers the existing `PATCH /workflows/:id` with `{ environment_id }` (already wired)
- Changing runner override triggers `PATCH /workflows/:id` with `{ default_runner_pool_id: <id> | null }`
- Both changes are auto-saved (no save button); a subtle spinner appears on the chip while the request is in flight

### Backend changes required

1. **`schemas.py` — `WorkflowDetail`**: add `default_runner_pool_id: str | None = None`
2. **`api.ts` — `WorkflowDetail` type**: add `default_runner_pool_id: string | null`
3. **`api.ts` — `WorkflowPatch`**: add `default_runner_pool_id: string | null | undefined`

No changes to `WorkflowUpdate` (already has the field) or the runner service (already resolves the chain).

---

## Component plan

### `RunnerPoolSelect` (new shared component)

A reusable component used in **both** surfaces:

```tsx
interface RunnerPoolSelectProps {
  value: string | null;           // current pool id, null = local
  onChange: (id: string | null) => void;
  pools: RunnerPool[];            // from GET /runner-pools
  includeLocalOption?: boolean;   // default true
  maxVisibleTiles?: number;       // default 3
}
```

Renders the tile list described in Surface 1. Used directly in `EditEnvModal` (replacing `PoolSelect`) and inside the `RunSettingsPopover` on the editor toolbar.

### `RunSettingsChip` (new, EditorPage toolbar)

The stacked chip + popover. Manages its own open/close state. Reads `workflow.default_runner_pool_id` and `environmentId` from editor state. Calls `updateWorkflow` on change.

---

## States and edge cases

| Scenario | Chip display | Popover runner field |
|---|---|---|
| No pools exist | env name only | Local only, no dropdown |
| Runner set, all offline | env name + `● prod-pool` (dot gray) | Shows pool with gray dot |
| Runner override cleared | env name only | "— No override —" selected |
| Saving in flight | env name + spinner | Fields disabled |

---

## What is not in scope

- Live runner health polling (use existing data from query cache; no new WebSocket or polling interval)
- Per-run runner override at run-time (the chip sets a workflow-level default; the deployment/run-specific override path is separate)
- Env modal layout changes beyond the runner pool field
