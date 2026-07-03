# Product & UX Review (Synthesis)

Independent audit, 2026-06-16. Synthesizes frontend reviews (01 architecture/state,
02 canvas, 03 node-UI/polish, 04 API integration) plus the node-system (08) and
README/product framing.

## Product positioning (clear and differentiated)
Nodyra is **n8n/Windmill-adjacent but Python-first**: every node is a plain Python
function, workflows run in per-environment virtualenvs at native speed, and the
builder exposes full input/output inspection. The differentiator is real — it's
not "JS wrappers around Python," it's Python end-to-end with a schema-driven UI
generated from decorators. The docs (`n8n-vs-nodyra`, `nodyra-vs-windmill`) frame
this honestly.

## UX strengths (verified)
- **Schema-driven inspector**: `ParamField` renders the correct control from each
  node's `ParamSpec` — choices, conditional `display_when`/`hide_when`, credential
  pickers, `load_options`, key/value, routes, file upload, expression + code
  editors. New nodes get a full UI for free from the `@node` decorator.
- **Builder maturity**: drag-drop canvas, quick-add on pane/edge, undo/redo (50
  deep), copy/cut/paste, auto-layout, loop frames, metanode collapse, sticky
  notes, sub-workflows, pinned data, retry-from-failed-node.
- **Live execution feedback**: per-node logs/errors/timing/debug vars, streaming
  run events overlaid on the canvas, expression live-resolution against the store's
  eval context (recent commits show active polish).
- **Broken-state handling is first-class**: `ErrorBoundary` (route-scoped,
  auto-resetting), `ToastProvider`, `ConfirmProvider` (no native `confirm`),
  `BackendLoading` cold-start state — all unit-tested.
- **Save-safety**: dual unsaved-changes guard (`beforeunload` + SPA nav), covering
  dirty child/map-body workflows — users don't silently lose work.
- **AI draft builder** creates *editable graphs* rather than hidden agent
  execution — the right product choice for a transparency-focused tool.

## UX / product findings
| ID | Severity | Issue | Recommendation |
|---|---|---|---|
| FE-7 | Low | `NodeDetails.tsx` 3880 LOC | Split into `editor/nodeDetails/` for maintainability (no UX change) |
| FE-8 | Low | Verify all controls/icon-buttons have accessible labels | jest-axe sweep over the inspector |
| FE-2 | Low | No silent re-auth — users hard-logout at 24h TTL | Add refresh/extend for long editing sessions |
| OBS-3 | Low-Med | No in-app ops dashboard (README acknowledges this gap) | Surface queue/run metrics in UI eventually |
| NODE-2 | Low | No saved-graph param migration when a node's schema changes | Define additive-only policy + migration hook before 3rd-party nodes |
| DOC-3 | Low | No CHANGELOG | Add before public launch |

## Top product recommendations (priority order)
1. **Ship the node-schema compatibility policy** (NODE-2): before opening the node
   API to OSS authors, guarantee saved graphs survive node updates (additive-only
   params + a migration hook). This protects every user's existing workflows.
2. **a11y verification pass** (FE-8): the scaffolding (`useModalA11y`, broad aria
   usage) is there; confirm the schema-driven controls are fully labeled.
3. **Long-session auth** (FE-2): silent refresh so a builder session doesn't die
   mid-edit at the 24h boundary.
4. **Maintainability split** (FE-7): mechanical, improves contributor velocity.

## Verdict
The product is **differentiated and the builder is mature** — schema-driven config,
real execution transparency, strong broken-state and save-safety handling, and an
honest AI-draft model. UX findings are all LOW and mostly polish/maintainability.
The one item with real product weight is the node-schema migration policy (NODE-2)
before third-party node authoring opens up.
