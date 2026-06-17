# Editor Task Bar Redesign — Design

**Date:** 2026-06-14
**Branch:** feat/arch-program-phase5
**Scope:** The editor top bar (`apps/web/src/EditorPage.tsx` `header.toolbar`) and its styles (`apps/web/src/editor.css`). Frontend-led; one small backend touch for unpublish.

## Goal

Replace the cluttered, manual save/publish bar with a calmer, status-driven design:

- **Autosave** replaces the manual "Save draft" button, with an animated save indicator.
- A **single Publish button** carries a status dot that communicates the workflow's live/draft state at a glance.
- Crowded secondary actions collapse into a **⋯ (kebab) overflow menu**.
- **Run** leaves the bar entirely (the canvas already has a "Test workflow" execute pill).

The result is a bar that reads, left to right: identity → status → primary actions → overflow.

## Non-goals / Out of scope

- **The environment dropdown (`RunSettingsChip`) is NOT changed.** Recent work touched it; leave its markup, behavior, and styling exactly as-is. It simply keeps its slot on the bar.
- No new Run control — the existing canvas execute pill covers running. Run is just removed from the bar.
- No changes to the version-history panel internals (`WorkflowHistory`) beyond relocating its entry point into the ⋯ menu.
- No backend changes to versioning, diffing, or the publish endpoint's core behavior.

## Current state (reference)

Right-side bar today (`EditorPage.tsx` ~1351–1556): `RunSettingsChip` · timeout input · MCP toggle (+ tool name/description inputs) · Active/Inactive toggle · Runs ▾ · ƒ Functions · Chat (conditional) · ? · AI Draft · Export ▾ · Run · Stop (conditional) · **Save draft** (with dirty-dot) · **Publish** · **History**.

Relevant backend facts (`apps/api/app/routers/workflows.py`):
- `workflow.active` (bool) — whether triggers fire in production. **Orthogonal** to publishing.
- `workflow.published_version` (int) — bumped by `POST /{id}/publish`.
- `_has_unpublished_changes(workflow)` — draft graph differs from latest published graph; surfaced as `has_unpublished_changes` on `WorkflowDetail`.
- The publish endpoint already accepts `notes` and `update_deployments`. The editor currently hardcodes `notes: "Published from editor"`.
- `updateWorkflow` (PUT `/workflows/{id}`) accepts `active` in its patch. **There is no dedicated unpublish endpoint** — unpublish is modeled as `active=false` via this existing PUT.

## New bar — final layout

Left (unchanged structure): logo · editable name · **status-aware meta line**.

Right, in order:
1. **Save indicator** (animated, non-interactive)
2. **`RunSettingsChip`** — env dropdown, untouched
3. **✨ AI Draft**
4. **Runs ▾**
5. **Publish pill** (single button, status dot)
6. **⋯ overflow menu**

Removed from the bar: Save draft → autosave; Run/Stop → canvas pill; History, Functions, Export, Shortcuts (?), MCP toggle+inputs, timeout input → ⋯ menu; Active/Inactive toggle → folded into publish/unpublish model (see below). Chat button keeps its existing conditional behavior; place it left of AI Draft (still conditional on `hasChatTrigger`).

## State model

The Publish pill and meta line are driven by three existing values: `active`, `published_version`, `has_unpublished_changes` (`dirty` local edits count as unpublished for indicator purposes once autosaved).

| Condition | Pill | Dot | Meta line |
|---|---|---|---|
| Never published (`published_version` is null/0) | **Publish** | none / neutral grey | `draft · N nodes` |
| Published, `active`, no unpublished changes | **Published** | 🟢 green (glow) | `v{n} · N nodes` |
| Has unpublished changes (regardless of active) | **Publish changes** | 🟡 amber, pulsing | `draft · based on v{n} · N nodes` |
| Published but `active=false` (unpublished/paused) | **Republish** | ⚪ neutral grey | `v{n} (unpublished) · N nodes` |

Notes:
- The amber state means the saved draft diverges from the live version; the live version keeps running if `active`.
- "Publish changes" and "Republish" both open the same publish-review modal.

## Autosave + save indicator

- **Trigger:** debounce ~1.5s after the last graph/name/setting change, then call the existing `save({ notifySuccess: false })`. Keep the existing save-on-name-blur. Reuse the current `dirty` / `markClean()` / `saveInProgressRef` machinery — do not introduce a parallel save path.
- **No success toast** on autosave (the indicator is the feedback). Errors still toast via the existing `notify(...)` path, and the indicator shows a retry/error state.
- **Indicator states** (small, right-aligned, non-interactive text + glyph):
  - `⟳ Saving…` — blue spinner, while a save is in flight.
  - `✓ Saved` / `All changes saved` — green check pops in, then settles to grey.
  - `• Unsaved edits` — brief amber blink between an edit and the debounce firing (optional/subtle).
  - `⚠ Save failed — retry` — on error; clicking retries the save.
- Guard against autosave while a run/publish is in progress consistent with current disabled-state logic.

## Publish flow

- Clicking the Publish pill opens the **existing** `publish-review-modal` (`EditorPage.tsx` ~1745). No change to the diff summary grid.
- Add a real **notes/comment textarea** to the modal, replacing the hardcoded `notes: "Published from editor"`. Field is optional; placeholder e.g. "What changed in this version?". Pass its value as `notes` to `api.publishWorkflow`.
- Keep the existing `update_deployments` toggle.
- On success, `active` is set true by the publish path (publish implies live). Refresh `WorkflowDetail` as today so the dot turns green.

## Unpublish

- New item at the bottom of the ⋯ menu: **Unpublish workflow** (danger styling), shown only when the workflow is published and active.
- Action: confirm (lightweight inline confirm or the existing modal pattern), then `api.updateWorkflow(id, { active: false })`. Non-destructive — version history is untouched; the pill switches to **Republish**.
- No new backend endpoint required.

## ⋯ Overflow menu

A new dropdown component (mirror the existing `export-menu` / `runs-menu` patterns: button toggles, `onMouseLeave`/outside-click closes). Contents, top to bottom:

1. **History & versions** — opens `setShowHistory(true)` (the existing `WorkflowHistory`).
2. **Functions** — opens `setFunctionsOpen(true)`.
3. **Export ▸** — submenu or nested items: Python script (.py), Docker bundle (.zip), Python module (.py). Reuse existing `triggerExport` calls.
4. — divider —
5. **MCP settings** — opens a small popover/modal hosting the existing MCP enable toggle + tool name + description inputs (moved out of the bar).
6. **Run timeout** — the existing timeout input, in a small popover.
7. **Shortcuts** — opens `setShortcutsOpen(true)`.
8. — divider —
9. **Unpublish workflow** (danger) — conditional, as above.

MCP and timeout move off the bar but their state and save-on-change behavior are unchanged; they just live in popovers now.

## Components & files

- `apps/web/src/EditorPage.tsx` — restructure the `toolbar-right` JSX; add autosave effect; add ⋯ menu state + MCP/timeout popovers; add notes field to publish modal; add unpublish handler. The toolbar JSX is large — extract the overflow menu (and ideally the save indicator and publish pill) into small focused components under `apps/web/src/editor/` to keep `EditorPage.tsx` manageable.
- `apps/web/src/editor.css` — styles for the save indicator (spinner/check/blink animations), the publish pill states (green/amber/neutral, pulsing amber dot), and the ⋯ menu + popovers. Match existing dark toolbar tokens.
- `apps/web/src/api.ts` — no new endpoints; `publishWorkflow` already supports `notes`, `updateWorkflow` already supports `active`.
- No backend changes.

## Testing

- **Indicator/state mapping (unit):** given `active` / `published_version` / `has_unpublished_changes` / `dirty`, the pill label, dot color, and meta line match the state table.
- **Autosave (component):** editing triggers a debounced save after ~1.5s; rapid edits coalesce into one save; no success toast fires; failure shows the error indicator.
- **Publish modal:** notes textarea value is passed through to `api.publishWorkflow`; empty notes is allowed.
- **Unpublish:** menu item calls `updateWorkflow({ active: false })` and the pill switches to Republish.
- **⋯ menu:** each item invokes the same handler the old bar button did (History, Functions, Export variants, Shortcuts); MCP/timeout popovers preserve existing save-on-change behavior.
- Manual verification in the running app across the four states, including the env dropdown remaining visually and behaviorally unchanged.

## Open questions

None blocking. Visual polish (exact glow/pulse timing, neutral vs grey for never-published) to be tuned during implementation against the agreed mockups in `.superpowers/brainstorm/`.
