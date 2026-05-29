# Noodle UI n8n-parity Plan
_Written by UI-Agent-B (claude-sonnet-4.6)_

## Already Done (by previous agent sessions)
- Empty state with 3 starter cards on canvas
- NodeCard hover toolbar (run, open, disable, delete)
- NodeCard error callout + duration badge
- NodePalette: categories, search, favorites, recents, badges, recommendations
- MiniMap + canvas controls (zoom/fit/auto-layout, Shift+L)
- DocsTab in NDV (3rd tab: Parameters / Settings / Docs)
- Port labels on multi-output nodes
- Credential warning badges on NodeCard

## Remaining Tasks

### Batch A: NDV Enhancements (NDVPanels.tsx)
A1. Add "Credentials" tab - show credential fields, inline "Create credential", "Test credential" button
A2. Add "Logs" tab - show node run logs from runMeta
A3. Improve I/O viewer (DataPanel) - add JSON tree/table/raw mode toggle + search/filter
A4. Expression autocomplete - $json, $node, $env, $run completions in string fields

### Batch B: Canvas Extras (Canvas.tsx + editor.css)
B1. Edge labels on conditional branches - show If/Switch output names on edges visually
B2. Sticky notes on canvas - draggable colored markdown note nodes
B3. Command palette (Cmd+K) - search nodes/workflows/commands

### Batch C: Workflow History (new WorkflowHistory.tsx + api.ts)
C1. Wire up GET /workflows/{id}/versions in api.ts
C2. WorkflowHistory panel in EditorPage - list versions, preview graph diff, restore

### Batch D: Execution / Backpressure polish
D1. Execution timeline already exists - verify it shows queue wait split
D2. Backpressure visibility - show why run is queued (already has partial: "backpressure" label)

## Implementation Order
1. NDV tabs (A1 + A2) - highest user value
2. Command palette (B3) - high discoverability
3. Edge labels (B1)
4. Sticky notes (B2)
5. Workflow history (C1 + C2)
6. I/O viewer modes (A3)
7. Expression autocomplete (A4)

## TypeScript Rule
tsc --noEmit must pass clean after all changes.
