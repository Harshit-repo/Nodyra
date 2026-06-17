# NDV Polish — Implementation Plan

Living plan for the Node Detail View (NDV) polish work. Every item links to a
rendered mockup so we agree on the look before code is written.

> ⚠️ **Environment note:** `C:` is at ~0 bytes free, which makes the Vite/Docker
> dev server unreliable and is why interactive features were not shipped blind.
> After a Windows restart frees space, the interactive items (Phase A) get built
> **and runtime-tested** in the real app. Pure CSS/structural items are verified
> by `tsc --noEmit` + the mockups.

---

## Renders (open these to review)

| File | Shows |
|------|-------|
| `design/ndv-polish-spec.html` / `.png` | Master before/after for items 1–8 + ideas summary |
| `design/ndv-polish-before-after.png` | Full NDV modal — before/after (3 node variants) |
| `design/ndv-fields-before-after.png` | Fields & dropdowns — default / hover / focus / open / invalid |
| `design/ndv-code-ideas.png` | Inline code editor — annotated, with feature legend |
| `design/ndv-code-modal-before-after.png` | Expand code modal — before/after |
| `design/ndv-backlog-preview.html` / `ndv-backlog.png` | **All 12 backlog ideas**, before/after |

---

## ✅ Already shipped (verified — `tsc --noEmit` exit 0)

Render: `ndv-polish-spec.html` items 1–5, `ndv-polish-before-after.png`, `ndv-fields-before-after.png`.

1. **Modal frame & header** — 14px radius, accent top-hairline, layered shadow, category-tinted glowing glyph, pill meta tags, action divider, green-gradient Execute, rotating red close. — `editor/NodeDetailModal.tsx`, `editor.css`
2. **Panel depth + direction arrows + count badges** — recessed Input/Output, lifted middle. — `editor/DataPanel.tsx`, `editor.css`
3. **Tabs** — rounded hover + glowing sliding underline. — `editor.css`
4. **Empty states** — centered medallion + copy. — `editor.css`
5. **Fields & dropdowns** — custom select chevron, accent hover/focus. — `editor.css`
6. **Code node — syntax-highlight editor (core of #6 + #7)** — both the inline Node-code editor and the Expand modal now use `HighlightedTextarea` (Python tokens + line-number gutter) on a dedicated code surface, replacing plain `<textarea>`s. — `editor/NodeDetails.tsx`, `editor.css`

---

## Phase A — Code node interactive features (the rest of "#6 full")

Render: `ndv-code-ideas.png` (markers 2/4/6), legend items.
**Needs the running app** (build after restart).

- **A1 · Active-line highlight** *(S)* — track caret line in `HighlightedTextarea` (`editor/NodeDetails.tsx` ~1537), render the band + accent gutter number. CSS hooks already designed in the render.
- **A2 · Autocomplete** *(L)* — popup suggesting (a) upstream input field paths via the existing `node-details/upstreamFields.ts` helpers, and (b) Python keywords/builtins; `Ctrl+Space` + type-trigger, keyboard nav, insert-at-caret. Reuse `VariablePickerPopover` patterns.
- **A3 · Inline lint** *(M)* — surface the node's last-run error onto the editor as a gutter marker + wavy underline on the failing line (render marker 4). True static linting is out of scope; we mark the run traceback line.

---

## Phase B — Variable picker discoverability (#8)

Render: `ndv-polish-spec.html` item 8.
- **B1** *(S)* — add a persistent `{ }` button next to `fx` on expression fields that opens the **existing** `VariablePickerPopover` (no new picker). — `editor/NodeDetails.tsx` (ParamField), `editor.css`

---

## Phase C — Backlog (all 12 ideas)

All rendered in `ndv-backlog-preview.html` (`ndv-backlog.png`). Grouped by effort so we can cherry-pick.

### Quick wins (S — CSS / small markup)
- **C1 · Status edge ribbon** — color the modal top-hairline by last-run status. Render: "Status edge ribbon" (3 states). — `NodeDetailModal.tsx`, `editor.css`
- **C2 · Run shortcut hints** — faint key chips on header buttons. Render: "Run shortcut hints". — `NodeDetailModal.tsx`, `editor.css`
- **C3 · Reset-to-default per field** — revert affordance when value ≠ default. Render: "Parameter search + reset-to-default" (right). — `NodeDetails.tsx` (ParamField)
- **C4 · Required-field summary** — banner of empty required fields as click-to-scroll chips. Render: "Required-field summary". — `NDVPanels.tsx`/`NodeDetails.tsx`

### Medium (M)
- **C5 · Inline value preview** — resolved-value chip under expression fields. Render: "Inline value preview". Reuses the existing expression-eval path. — `NodeDetails.tsx`
- **C6 · Parameter search** — filter box for param-heavy nodes. Render: "Parameter search…". — `NodeDetails.tsx` (ParametersTab)
- **C7 · Snippet templates** — starter menu in the empty code node. Render: "Snippet templates". — `NodeDetails.tsx`
- **C8 · Format on save** — ruff/black on save + "Format" button. Render: "Format on save". Needs a backend format endpoint or client formatter. — backend + `NodeDetails.tsx`
- **C9 · Output diff vs last run** — toggle highlighting changed keys/rows. Render: "Output diff vs last run". — `DataPanel.tsx` + diff util

### Larger (L)
- **C10 · Resizable columns** — draggable splitters between Input/Settings/Output, widths persisted per node. Render: "Resizable columns". — `NDVPanels.tsx`, store, `editor.css`
- **C11 · Schema tree input** — n8n-style expandable JSON schema view with draggable leaves, alongside Table/JSON. Render: "Schema tree input". — `DataPanel.tsx`
- **C12 · AI fix / explain code** — "Fix"/"Explain" on a code error, feeding traceback+code to the model. Render: "AI fix / explain code". — `NodeDetails.tsx` + LLM call (use latest Claude model)

---

## Suggested order
1. **Phase A** (after restart) — finishes the code node, the thing most in flight.
2. **Phase B** — tiny, high-value discoverability win.
3. **Phase C quick wins** (C1–C4) — cheap, visible polish.
4. **Phase C medium** (C5–C9).
5. **Phase C larger** (C10–C12) — schedule individually.

## Verification
- Every change: `cd apps/web && npx tsc --noEmit` must stay green.
- Interactive items (Phase A, C9–C12): smoke-test in the running app once disk is freed; capture a screenshot per item.
- No commits until you ask; these touch `editor.css`, `NodeDetailModal.tsx`, `DataPanel.tsx`, `NDVPanels.tsx`, `NodeDetails.tsx`, `VariablePickerPopover.tsx`.
