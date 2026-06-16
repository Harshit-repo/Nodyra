# 03 — Node Config UI, Broken States & Accessibility

Independent review, 2026-06-16. Files: `apps/web/src/editor/NodeDetails.tsx`
(3880 LOC), `apps/web/src/editor/NDVPanels.tsx`, `apps/web/src/ErrorBoundary.tsx`,
`apps/web/src/ToastProvider.tsx`, `apps/web/src/ConfirmProvider.tsx`,
`apps/web/src/useModalA11y.ts`, `apps/web/src/BackendLoading.tsx`.

## What is solid (verified)
- **Schema-driven inspector**: `ParamField` renders the right control from the
  manifest `ParamSpec` (choices, widgets, `display_when`/`hide_when`, credential
  selectors, `load_options`, key/value, routes, file upload, expression editor,
  code editor). The form is generated from the Python `@node` decorator — no
  hand-maintained per-node UI. This is the payoff of the node-system design.
- **Genuine component decomposition**: despite 3880 LOC, `NodeDetails.tsx` is ~30
  named sub-components (`JsonField`, `KeyValueField`, `ExpressionEditorModal`,
  `CredentialCreateModal`, `CodeEditorModal`, `LoadOptionsField`, `RoutesField`,
  `FileUploadField`, `NodeCodePanel`, `ToolModeSection`, …) — not a single
  god-component, just co-located in one file.
- **Broken-state coverage exists as first-class infra**:
  - `ErrorBoundary` (tested, `ErrorBoundary.test.tsx`) with `resetKey` recovery.
  - `ToastProvider` (tested) for transient errors/success.
  - `ConfirmProvider` + `ConfirmDialog` for destructive-action confirmation
    (replaces native `confirm`).
  - `BackendLoading` for the API-warming/cold-start state.
- **Accessibility scaffolding**: `useModalA11y` (focus-trap/restore + Escape,
  tested in `useModalA11y.test.tsx`) is reused by modals; aria/role attributes
  appear across 46 component files. The earlier focus-steal bug
  (commit 897a6d2 "stop modal stealing focus to first button on every keystroke")
  shows a11y is actively maintained, not bolted on.
- **Expression editor with live resolution + history** (`ExpressionEditorModal`,
  `readExprHistory`/`appendExprHistory`) — recent commits (5e1bab6, b49619b) wired
  it to resolve against the store's eval context, a real UX investment.

## Findings

### FE-7 — `NodeDetails.tsx` should be split into a folder (LOW, maintainability)
3880 LOC in one file is hard to navigate and review even with good internal
decomposition. The sub-components are already cleanly separated — they just need to
move into `editor/nodeDetails/` (one file per field type + an index).
- **Recommendation:** mechanical extraction (`fields/JsonField.tsx`,
  `fields/KeyValueField.tsx`, `modals/ExpressionEditorModal.tsx`, …). No behavior
  change; improves reviewability and lets test files target individual fields.
- **Status:** Reviewed.

### FE-8 — Confirm whether all field controls have labels/aria for screen readers (LOW — verify)
Schema-driven controls are powerful but easy to ship without programmatic labels.
Spot-check shows aria usage is broad, but a sweep specifically over `ParamField`
control variants (key/value rows, routes, file upload, expression/code launchers)
would confirm each has an associated `<label>`/`aria-label` and that icon-only
buttons (add/remove row) carry accessible names.
- **Recommendation:** an axe/jest-axe pass over the rendered inspector for a couple
  of representative node manifests; fix any unlabeled icon buttons.
- **Status:** Verify.

### FE-9 — Code/expression editors load weight even for non-code nodes (LOW — verify)
`CodeEditorModal`/`ExpressionEditorModal` (syntax-highlighting/textarea machinery)
live in the same module as every field. Confirm they're lazily mounted (only when
opened) so simple nodes don't pay the cost; if not, `React.lazy` the heavy editors.
- **Status:** Verify.

## Verdict
Node-config UX and broken-state handling are **above the bar**: a fully
schema-driven inspector, real error/empty/loading/confirm infrastructure (most of
it unit-tested), and active a11y maintenance. Findings are maintainability
(split the mega-file) and a focused a11y/lazy-load verification — all LOW, none
blocking.
