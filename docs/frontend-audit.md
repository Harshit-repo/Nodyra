# Frontend Production Audit (apps/web) — Handoff

A resumable audit of the Noodle web app (`apps/web`) ahead of release, mirroring
the format of `docs/production-readiness-audit.md`. This file is the operating
manual: a fresh agent should be able to pick up the next `⏳ pending` area and
work without re-deriving context.

**Goal:** ship the web client with no known correctness, security, accessibility,
or performance regressions. The *backend* security/correctness audit is already
done (see `docs/production-readiness-audit.md`); this is the **frontend** pass.

**Scope:** `apps/web/src/**`. The backend API surface is out of scope (audited
separately). Focus on the React/TypeScript client: data flow, error/auth handling,
accessibility, perf, and correctness of the editor.

---

## 🤝 Handoff context (read first)

### Stack
- **React 18 + TypeScript 5.5**, Vite 5, Zustand 5 (state), React Router 6,
  `@xyflow/react` (React Flow) for the node editor, Plotly (charts), `marked` +
  `dompurify` (markdown), Phosphor/simple-icons.
- **Tests:** Vitest 4 + Testing Library + jsdom. 20 test files exist (mostly the
  editor store + pure helpers).

### How to run / verify (run from `apps/web`)
- Typecheck: `npm run typecheck`  (must stay clean — it's part of `build`)
- Unit tests: `npm run test`  (vitest run)
- Build: `npm run build`  (tsc + vite build — catches type + bundling issues)
- Dev server: `npm run dev`
- For live behavior testing, the Chrome DevTools MCP / Playwright MCP are
  available (see the session's skills) — useful for the a11y and error-flow
  areas. Confirm against a running backend (`apps/api`).

### Conventions / policy (follow these)
- **Log every finding here** with a stable ID (`FE-2`, `FE-3`, … — `FE-1` is
  taken), severity, evidence (`file:line`), and status. Keep the ledger table
  and the per-finding detail in sync.
- **TDD where it fits:** the editor store and pure helpers are well-tested
  (`*.test.ts(x)`); add a failing test before fixing logic bugs there. UI/visual
  issues won't always have a unit test — note how you verified instead.
- **Match surrounding style.** Components are large but consistent; keep the
  existing patterns (Zustand selectors, the `api.ts` client wrapper, the toast
  provider for user-facing errors).
- **Don't commit** unless the owner (Harry) says so — the backend audit left its
  changes for review; do the same unless told otherwise.
- **Don't over-engineer.** Prefer the smallest change that fixes a real,
  user-visible problem. Calibrate to a trusted-team SaaS, not hostile multi-tenant.

### Severity legend
**Critical** (data loss / auth bypass / XSS), **High** (broken core flow likely
to hit users), **Medium** (real bug, narrower blast radius), **Low** (polish /
a11y / perf nit). Status flow: `todo` → `in-progress` → `fixed` (+ test/verify) →
`verified`.

---

## Already reviewed (backend audit, Wave 2 "frontend security basics")
Do **not** redo these — they were checked and are clean/accepted:
- **XSS sinks:** the only `dangerouslySetInnerHTML` is in `editor/ChatPanel.tsx`,
  and it's `DOMPurify.sanitize(marked.parse(...))` — correct. No `innerHTML` /
  `eval` / `new Function` sinks elsewhere.
- **OAuth popup:** `CredentialsPage.tsx` verifies `event.origin ===
  window.location.origin` before trusting `postMessage` — correct.
- **FE-1 (Low, accepted):** the session token lives in `localStorage`
  (`api.ts`, `editor/artifactValues.ts`, `editor/datasetValues.ts`,
  `editor/NDVPanels.tsx`) — XSS-stealable, but the standard SPA tradeoff and the
  sinks above are clean. Defence-in-depth (httpOnly cookie + CSRF, strict CSP)
  is a future hardening, not a launch blocker. Re-confirm no NEW token sinks
  appear, but treat the tradeoff itself as decided.

**Backend facts the frontend must stay consistent with** (from the API audit):
- Auth is a bearer **session** token; the API now stamps `typ="session"` and the
  global auth gate rejects non-session tokens (TOK-1). On **401** the SPA should
  detect an expired/invalid session and route to login (see FE-2 below).
- CORS headers are now present even on error responses (REL-1), so the client
  *can* read 401/413 bodies cross-origin — make sure it does.
- RBAC roles are `viewer < editor < admin < owner`; mutating actions need
  `editor`+. The UI should hide/disable controls a `viewer` can't use (and
  handle the 403 gracefully if it doesn't). See `src/permissions.ts`.

---

## Status summary

| ID | Title | Severity | Status |
|---|---|---|---|
| FE-1 | Session token in localStorage | Low | accepted/documented (backend audit) |
| _next_ | _(add findings here)_ | | ⏳ |

---

## Review ledger (pick the next ⏳ pending row)

| Area / file | Reviewed | Notes |
|---|---|---|
| Security basics (XSS / postMessage / token) | ✅ | from backend audit — see above |
| **Error & 401/403 handling flows** | ⏳ pending | `api.ts` (857 LOC), `authBootstrap.ts`, `ToastProvider.tsx`, route guards in `App.tsx` |
| **Accessibility (a11y)** | ⏳ pending | keyboard nav, focus traps in modals, ARIA, contrast, tap targets — whole `src/` |
| **Performance** | ⏳ pending | editor re-renders, big lists (`ExecutionsPage`, `WorkflowsPage`), Plotly/`DataPanel`, bundle size |
| **Editor correctness/UX** | ⏳ pending | `editor/store.ts` (2258 LOC), `Canvas.tsx`, `NoodleEdge.tsx`, `LoopFrame.tsx`, undo/redo, copy/paste |
| **`editor/NodeDetails.tsx` (3491 LOC)** | ⏳ pending | the largest component — param rendering, validation, expression preview |
| **`EditorPage.tsx` (1512 LOC)** | ⏳ pending | autosave/publish flow, draft vs published, websocket run events |
| **`CredentialsPage.tsx` (1432 LOC)** | ⏳ pending | beyond the OAuth-origin check: secret handling in forms, never log secrets |
| **`editor/DataPanel.tsx` (1296 LOC) / NDVPanels (1043)** | ⏳ pending | large payload rendering, truncation, artifact/dataset value display |
| **`RunnerPoolsPage` / `EnvironmentsPage` / `DeploymentsPage`** | ⏳ pending | admin flows, optimistic updates, error surfacing |
| **State & data fetching** | ⏳ pending | Zustand store hygiene, stale-closure bugs, race conditions on rapid edits, request cancellation |
| **WebSocket run streaming** | ⏳ pending | `/ws/runs/{id}` consumer: reconnect, dedupe, `run_waiting`/`run_finished` handling, leak on unmount |
| **Forms & validation** | ⏳ pending | client-side validation parity with the API (e.g. node types, schedules) |
| **Routing / deep links / refresh survival** | ⏳ pending | `App.tsx`, `authBootstrap.ts` (recent "no black screen" fix — verify edge cases) |

---

## What to check per area (concrete prompts)

### 1. Error & 401/403 flows (highest value — start here)
- Does `api.ts` centralize fetch + error handling? On **401**, does it clear the
  token and redirect to `/login` (not leave the user on a broken page)? On **403**,
  does it surface a clear "you don't have permission" toast rather than a silent
  failure or a generic crash?
- Are network/5xx errors caught and shown via `ToastProvider`, or do some
  `await`ed fetches throw unhandled (white screen)?
- Does a **session expiring mid-session** (token invalidated by the TOK-1 change)
  recover gracefully? Reproduce by clearing/666-ing the localStorage token.
- Are there `.json()` calls on responses that may be empty (204) or non-JSON
  (error HTML) → throws?

### 2. Accessibility
- Modals (`ConfirmDialog`, `NodeDetailModal`, `AiDraftModal`, `SdkModal`,
  `DatasetSqlModal`, `CommandPalette`): focus trap, `Esc` to close, focus return,
  `role="dialog"` + `aria-modal`, labelled by a heading.
- Keyboard operability of the node editor canvas and the node palette.
- Form inputs have associated `<label>`s; icon-only buttons have `aria-label`.
- Color contrast (check `theme.ts` / `index.css` / `editor.css`) and visible
  focus rings. Tap-target sizes on interactive controls.

### 3. Performance
- Editor: are large components re-rendering on every store change? Look for
  Zustand selectors that return new objects each call, missing `React.memo`,
  inline handlers causing child re-renders. `editor/store.ts` is 2258 LOC —
  check selector granularity.
- Long lists (`ExecutionsPage`, `WorkflowsPage`, `ActivityPage`): pagination is
  server-side (good) — confirm no accidental full-list renders; consider
  virtualization only if a list is unbounded.
- `DataPanel` / `NDVPanels` rendering large run outputs — confirm truncation is
  applied client-side too (backend caps payloads, but the client should not try
  to render multi-MB JSON).
- Plotly and `plotly.js-basic-dist-min` bundle weight; lazy-load chart code if
  it's in the main chunk. Run `npm run build` and inspect chunk sizes.

### 4. Editor correctness / state
- Undo/redo, copy/paste (`store.clipboard.test.ts` exists), metanodes
  (`store.metanodes.test.ts`), loops (`store.loops.test.ts`), tool-mode — these
  have tests; extend them for any logic gap you find.
- Race conditions: rapid edits during autosave; switching workflows mid-request;
  websocket events arriving after unmount/navigation (cleanup in `useEffect`).
- Draft vs published consistency (`EditorPage` publish flow) — matches the
  backend `has_unpublished_changes` semantics.

### 5. WebSocket run streaming
- The run-events socket replays history then streams live (backend reaps
  abandoned buffers now — EVT-1). Verify the client: handles `run_waiting`
  (doesn't hang the UI waiting for `run_finished`), reconnects on drop, cleans up
  the socket + heartbeat on unmount, and dedupes replayed-vs-live events.

---

## Findings — (add detail blocks here as you go)

> Template:
>
> ### FE-N — <title> (<severity>)
> - **Evidence:** `apps/web/src/<file>:<line>` …
> - **Impact:** …
> - **Fix:** …
> - **Status:** `todo` | `fixed` (+ test/how-verified)

_(none yet — backend FE-1 lives in the production-readiness audit)_
