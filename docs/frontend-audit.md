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
| FE-2 | No React error boundary → render throw white-screens the app | High | `fixed` (test + build) |
| FE-3 | User-facing errors leak `ApiError:` prefix + raw 422 JSON | Medium | `fixed` (test) |
| FE-4 | WS `1008` (auth refused) close doesn't trigger re-auth | Low | `todo` |
| FE-5 | EditorPage run stream not closed before reopening → socket leak + cross-run events | Medium | `fixed` |
| FE-6 | Modals lack Esc / focus trap / focus return (no shared util) | Medium (a11y) | `fixed` (ConfirmDialog, AiDraftModal) + util for the rest |
| FE-7 | No unsaved-changes guard → tab close/refresh silently loses editor edits | Medium | `fixed` |
| FE-8 | DataPanel renders unbounded `JSON.stringify` of run output (main-thread freeze risk) | Low | `fixed` (+ FE-8b regression fix) |
| _next_ | _(add findings here)_ | | ⏳ |

---

## Review ledger (pick the next ⏳ pending row)

| Area / file | Reviewed | Notes |
|---|---|---|
| Security basics (XSS / postMessage / token) | ✅ | from backend audit — see above |
| **Error & 401/403 handling flows** | ✅ reviewed | found FE-2 (no error boundary), FE-3 (error text leakage), FE-4 (WS 1008). 401 path is sound (clears token + flips auth → LoginPage); 403 falls through to a generic toast — acceptable now that FE-3 makes the backend's `detail` message readable. `api.ts` `request()`/`uploadArtifact()` handle 204 + non-JSON bodies correctly. |
| **Accessibility (a11y)** | 🔶 partial | FE-6: built `useModalA11y` (Esc + focus trap + focus return). Applied to `ConfirmDialog`, `AiDraftModal`, `DatasetSqlModal`, `SdkModal` (full); `CommandPalette` + `NodeDetailModal` given `role/aria-modal` + focus-return without the trap (they have bespoke keyboard handling / embedded editors). Icon-only-button `aria-label` sweep done (UX-2). Color-contrast pass still pending. |
| **Performance** | 🔶 partial | UX-4 done: route-level code splitting cut initial JS ~80% (index 331→60 KB gzip; editor + Plotly deferred). **Zustand selector hygiene reviewed clean** — no selector returns a fresh array/object (`.find()` yields stable element refs; block selectors return primitives), so no spurious re-renders / `useSyncExternalStore` snapshot churn. Editor re-render profiling + big-list virtualization still pending. |
| **Editor correctness/UX** | 🔶 partial | FE-7 (no unsaved-changes guard) fixed. Workflow-switch load effect has a correct `cancelled` guard. **Publish flow verified**: `publishDraft` saves first and aborts if save fails, then refetches to resync `has_unpublished_changes`. Undo/redo/copy/paste/loops/metanodes well-covered by existing `store.*.test.ts`. Deeper `store.ts` review still pending. |
| **`editor/NodeDetails.tsx` (3491 LOC)** | ⏳ pending | the largest component — param rendering, validation, expression preview |
| **`EditorPage.tsx` (1512 LOC)** | ⏳ pending | autosave/publish flow, draft vs published, websocket run events |
| **`CredentialsPage.tsx` (1432 LOC)** | 🔶 partial | Secret handling clean: secret fields render `type="password"` (`fieldInputType`, line 543), no `console.*` secret logging anywhere in `src` (only 1 benign `store.ts` warn), secrets posted in request bodies (not URLs). Full form-flow/validation review still pending. |
| **`editor/DataPanel.tsx` (1296 LOC) / NDVPanels (1043)** | ✅ reviewed | FE-8 + FE-8b: capped the raw-JSON `<pre>` fallback in DataPanel via `pretty()` (100 K chars) and guarded `pretty()` against `undefined` (unrun node) — see `DataPanel.test.tsx`. NDVPanels truncates previews to 500 chars and its `JSON.stringify(...).slice()` sites are guarded by `preview != null` (no FE-8b-class crash). Text body capped at 20 K. DataFrame/record views render previews, not full payloads. |
| **`RunnerPoolsPage` / `EnvironmentsPage` / `DeploymentsPage`** | 🔶 partial | Close buttons labelled (UX-2). `DeploymentsPage` has solid 409-unsafe-node handling + refresh-after-mutate. Polling intervals clean. Deeper optimistic-update/rollback review still pending. |
| **State & data fetching** | 🔶 partial | EditorPage load uses `cancelled` flag (no stale write on workflow switch); `save()` guarded by `saveInProgressRef`. FE-7 covers refresh data-loss. Broader page-level fetch race/cancellation review still pending. |
| **WebSocket run streaming** | ✅ reviewed | FE-5 fixed (EditorPage leak). `ExecutionsPage` (effect cleanup) + `ChatPanel` (closes before reconnect, unmount cleanup) are correct. Reconnect backoff resets on any message; `run_waiting` handled (toast + ChatPanel reconnect). FE-4 (1008→re-auth) still `todo`. |
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

### FE-2 — No React error boundary → any render throw white-screens the whole app (High)
- **Evidence:** No `componentDidCatch` / `getDerivedStateFromError` anywhere in
  `apps/web/src` (grep clean). `main.tsx` rendered `<App/>` bare; `App.tsx`
  rendered `<Routes>` with no boundary. A single render-time exception in any
  route page (e.g. an unexpected `null` / malformed run payload) unmounts the
  entire tree to a blank page with no recovery — exactly the "black/white
  screen" class the recent auth-bootstrap fix was guarding against.
- **Impact:** One bad component takes down the whole SPA; user must hard-reload
  and may not know to.
- **Fix:** Added `src/ErrorBoundary.tsx` (class component) with a friendly,
  recoverable fallback ("Something went wrong" + **Reload** + **Try again**),
  styled with existing theme tokens (`.app-error*` in `index.css`). Wired two
  layers: an outer catch-all in `main.tsx` around `<App/>` (covers bootstrap +
  login), and an inner boundary in `App.tsx` around the authed `<Routes>` and
  the public chat route, keyed on `location.pathname` so **navigating away
  auto-clears the error** without a hard reload.
- **Status:** `fixed`. Verified: `ErrorBoundary.test.tsx` (4 tests — renders
  children, shows fallback on throw, resets on `resetKey` change, "Try again"
  re-renders in place). Full suite 119/119, `typecheck` clean, `build` clean.

### FE-3 — User-facing error text leaks `ApiError:` prefix and raw 422 JSON (Medium)
- **Evidence:** ~30+ call sites surface errors with `setError(String(err))` /
  `notify(String(err))` (e.g. `EditorPage.tsx`, `CredentialsPage.tsx:876,1255,1268`,
  `EnvironmentsPage.tsx`, `ExecutionsPage.tsx`, `ActivityPage.tsx:30`). `String()`
  on an `Error` prepends the class name, so an `ApiError` rendered to a user read
  `"ApiError: 422 …"`. Worse, `api.ts request()`/`uploadArtifact()` built the
  message via `(detail as {message?}).message ?? JSON.stringify(detail)`, so a
  FastAPI validation error (`detail` = `[{loc,msg,type},…]`) dumped raw JSON like
  `[{"loc":["body","name"],"msg":"field required",…}]` into the toast.
- **Impact:** Confusing, unprofessional error messages; validation failures are
  unreadable to users.
- **Fix (centralized, no call-site churn):** in `api.ts` (1) added
  `formatErrorDetail(detail)` — passes strings through, joins FastAPI
  `[{msg}]` arrays into a sentence, unwraps `{message}` objects, JSON only as a
  last resort; used it in both `request()` and `uploadArtifact()` (also dedupes
  the previously-duplicated parsing block). (2) Overrode `ApiError.toString()`
  to return just `this.message` so every existing `String(err)` stops leaking
  the class name. `err.status` / `err.detail` stay intact for callers that
  branch on them (e.g. `DeploymentsPage` 409 handling).
- **Status:** `fixed`. Verified: `api.errors.test.ts` (6 tests) + full suite.
- **Follow-up (not fixed):** plain non-API errors (`TypeError: Failed to fetch`)
  still show their class prefix via `String(err)`. A shared `errorMessage(err)`
  helper adopted at the toast/setError sites would fully normalize this; deferred
  as broader churn — the API-error path (the common case) is now clean.

### FE-4 — WebSocket `1008` (auth refused) close doesn't trigger re-auth (Low)
- **Evidence:** `api.ts subscribeToRunEvents` treats close code `1008` as
  terminal (stops reconnecting, calls `onClosed`) but never invokes the global
  `unauthorizedHandler`. If a session expires mid-run, the run stream dies
  silently while the rest of the UI stays in a stale "signed-in" state until the
  next REST call 401s.
- **Impact:** Minor — the next REST request recovers the user to login; only the
  live run view is affected in the interim.
- **Fix (proposed):** on a `1008` close, call the same unauthorized path REST
  uses (clear token + `unauthorizedHandler`). Deferred — low blast radius.
- **Status:** `todo`.

> **Perf note (for the Performance area, not a finding yet):** `npm run build`
> shows Plotly is already code-split (`PlotlyChartView` chunk, 1.1 MB / 378 KB
> gzip — good), but the main `index` chunk is ~1.15 MB / 331 KB gzip. Worth
> revisiting route-level `lazy()` splitting when the Performance area is picked up.

### FE-5 — EditorPage run stream not torn down before reopening (Medium)
- **Evidence:** `apps/web/src/EditorPage.tsx` `connectRunStream()` did
  `wsRef.current = subscribeToRunEvents(...)` without first closing an existing
  handle. (`ExecutionsPage` uses effect cleanup and `ChatPanel` closes before
  reconnect — only EditorPage was missing the guard.)
- **Impact:** Starting a second run, or a rapid re-run before the previous run
  finished, orphans the prior WebSocket (it keeps its own reconnect loop alive)
  and its `onMessage` keeps calling `applyRunEvent` — mutating editor node
  state for the *wrong* run. Socket leak + cross-run state bleed.
- **Fix:** close + null `wsRef.current` at the top of `connectRunStream` before
  opening the new stream.
- **Status:** `fixed`. Verified: `typecheck` + `build` clean; existing
  `store.*`/`ChatPanel` tests still green. (No unit test — the socket lifecycle
  isn't currently harnessed in EditorPage; verified by code path + the symmetric
  pattern already proven in ChatPanel/ExecutionsPage.)

### FE-6 — Modals lack Esc-to-close, focus trap, and focus return (Medium, a11y)
- **Evidence:** No shared focus-management utility existed. `ConfirmDialog.tsx`
  (used for destructive actions across every page) had `role="dialog"` +
  `aria-modal` but no `Esc`, no focus trap, and no focus return. `AiDraftModal`
  had none of the dialog ARIA at all. Several modals do handle `Esc`
  (`CommandPalette`, `NodeDetailModal`), so behaviour was inconsistent.
- **Impact:** Keyboard users can't dismiss dialogs with `Esc`, focus can escape
  behind the modal, and focus isn't returned to the trigger on close — a
  WCAG 2.4.3 / 2.1.2 gap and a daily friction for power users.
- **Fix:** added `src/useModalA11y.ts` — `Esc` to close, focus first focusable
  on open, trap `Tab`/`Shift+Tab`, restore focus on unmount; `trapFocus:false`
  escape hatch for dialogs embedding their own keyboard widgets. Applied to
  `ConfirmDialog` (focuses Cancel first — safer default) and `AiDraftModal`
  (also added `role="dialog"`/`aria-modal`/`aria-labelledby`, `aria-label` on
  the close button and prompt textarea).
- **Status:** `fixed` for those two. Verified: `useModalA11y.test.tsx`
  (4 tests — Esc, focus-in, focus-return, Tab-wrap) + `typecheck`.
- **Rollout (done this run):** full hook on `DatasetSqlModal` + `SdkModal`
  (added `role="dialog"`/`aria-modal`/`aria-labelledby` + close-button labels).
  `CommandPalette` (combobox with its own arrow-key nav) and `NodeDetailModal`
  (embeds the NodeDetails code editors) kept their bespoke `Esc` handling and
  got `aria-modal` + focus-return added, but **not** the `Tab` trap — it would
  fight their inner controls.

### FE-7 — No unsaved-changes guard; tab close / refresh silently drops edits (Medium)
- **Evidence:** `EditorPage.tsx` saves only manually — Cmd/Ctrl+S, the Save
  button, name-input `onBlur`, and implicitly before a run. There was no
  `beforeunload` handler anywhere in `src` (grep clean), so a refresh or tab
  close with a dirty graph lost the work with no prompt.
- **Impact:** Real data loss for the app's core surface (the editor). The
  dirty-dot indicator hints at unsaved state but nothing intercepts the unload.
- **Fix:** added a `beforeunload` effect in `EditorPage` that arms only while
  `dirty || childDirty` (parent graph or any dirty map-body child) so the
  browser shows its native "leave site?" prompt. Cleaned up on change/unmount.
- **Scope note:** in-app React Router navigation isn't blocked — this
  `BrowserRouter` setup has no data-router `useBlocker`; adding that is a larger
  change tracked as a UX opportunity. The refresh/close vector (the common one)
  is covered.
- **Status:** `fixed`. Verified: `typecheck` clean; full suite green.

---

### FE-8 — DataPanel renders unbounded run output JSON (Low)
- **Evidence:** `editor/DataPanel.tsx` had two `<pre>{JSON.stringify(x, null, 2)}</pre>`
  sites (the raw-JSON value fallback, line ~954, and the variable preview,
  line ~1016) with no size cap. The text-body path (`slice(0, 20000)`) and
  NDVPanels (`slice(0, 500)`) were already bounded; these two weren't.
- **Impact:** The backend caps payloads, so this is unlikely in practice, but a
  large object would block the main thread in `JSON.stringify` and inflate the
  DOM with a giant `<pre>`. Defensive only.
- **Fix:** added a `MAX_JSON_CHARS` (100 K) clamp inside the existing `pretty()`
  helper (with a "… output truncated (N chars)" footer) and routed both `<pre>`
  sites through `pretty()`.
- **Status:** `fixed`. Verified: `typecheck` + `build` clean.

#### FE-8b — `pretty()` crashed every node-open (regression from FE-8) (High)
- **Evidence:** the FE-8 rewrite did `text = JSON.stringify(value, null, 2)` then
  read `text.length`. `JSON.stringify(undefined)` returns `undefined` (not a
  throw), so `text.length` threw `TypeError: Cannot read properties of undefined
  (reading 'length')`. `DataPanel` computes `copyPayload = pretty(display)` on
  **every** render, and an unrun node's Input/Output panels both pass
  `data === undefined` → `display === undefined`. So double-clicking any node
  that hadn't run yet crashed the render and tripped the new ErrorBoundary
  ("Something went wrong") — every time.
- **Fix:** guard `pretty()` for `text === undefined` (return `""` for `undefined`,
  else `String(value)`) before the length check (`editor/DataPanel.tsx`).
- **Status:** `fixed`. Regression test `editor/DataPanel.test.tsx` (renders the
  empty state for `undefined`/`null` data without throwing); full suite
  134 passing, `typecheck` clean.

> **Interval/cleanup review (clean):** all `setInterval` consumers
> (`ExecutionsPage` ×3, `WorkflowsPage`, `ChatPanel`, `NDVPanels`,
> `NodeDetails` ×2, `EditorPage` webhook poll) clear their timers on unmount /
> dependency change (effect cleanup or a `timerRef` cleared via
> `useEffect(() => stop, [])`). No leaks found. Console hygiene also clean —
> only one benign `store.ts` warn, no secret logging.

---

## 🎨 UI/UX improvement opportunities (running list)

Captured opportunistically during the audit. These are polish/UX, not
correctness bugs — triage separately from the FE-N findings.

| # | Area | Opportunity | Effort |
|---|---|---|---|
| ~~UX-1~~ | Errors | ✅ **Done.** `safeFetch` in `api.ts` converts network failures (`TypeError: Failed to fetch`) into a clean `ApiError(0, "Could not reach the server…")` at the source — every existing `String(err)` site is now clean, no migration needed. Also exported `errorMessage(err)` helper for explicit use. Tests in `api.errors.test.ts`. | — |
| ~~UX-2~~ | a11y | ✅ **Done.** Swept close/remove buttons (`✕`/`×`) across `src/` and added `aria-label` to the ones missing it (`EditorPage`×2, `CodeLibraryPage`, `DeploymentsPage`, `ExecutionsPage`, `DataPanel`, `FunctionsPanel`×2, `RunnerPoolsPage`×4, `NodeDetails`×3). | — |
| ~~UX-3~~ | a11y | ✅ **Done.** Modal a11y rollout — see FE-6. | — |
| ~~UX-4~~ | Perf/UX | ✅ **Done.** Route-level `React.lazy()` + `Suspense` in `App.tsx`. Main `index` chunk **1,153 KB → 187 KB** (331 → 60 KB gzip); editor (700 KB) + Plotly (1.1 MB) now load only on the editor route. | — |
| UX-5 | Errors | Error display is split between inline banners (`setError`) and toasts (`notify`) inconsistently across pages; pick one convention per error class (transient → toast, blocking → inline) for predictability. | M |
| UX-6 | Feedback | `run_waiting` (approval) surfaces as a transient `info` toast in EditorPage; a persistent affordance (badge/banner with an "approve" jump) would be clearer since the run is genuinely blocked. | M |
| ~~UX-7~~ | Toasts | ✅ **Done.** `ToastProvider` rewritten: errors are sticky (manual dismiss), success/info auto-dismiss, all pause-on-hover; errors get `role="alert"`. Tests in `ToastProvider.test.tsx`. | — |
| UX-8 | Editor nav | In-app route navigation away from a dirty editor isn't blocked (FE-7 only guards refresh/close). A React Router `useBlocker` (needs a data router) would prompt on in-app nav too. | M |
