# Frontend Production Audit (apps/web) — Handoff

A resumable audit of the Nodyra web app (`apps/web`) ahead of release, mirroring
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
| FE-4 | WS `1008` (auth refused) close doesn't trigger re-auth | Low | `fixed` (test) |
| FE-5 | EditorPage run stream not closed before reopening → socket leak + cross-run events | Medium | `fixed` |
| FE-6 | Modals lack Esc / focus trap / focus return (no shared util) | Medium (a11y) | `fixed` (ConfirmDialog, AiDraftModal) + util for the rest |
| FE-7 | No unsaved-changes guard → tab close/refresh silently loses editor edits | Medium | `fixed` |
| FE-8 | DataPanel renders unbounded `JSON.stringify` of run output (main-thread freeze risk) | Low | `fixed` (+ FE-8b regression fix) |
| FE-9 | No catch-all route → unknown URL renders a blank screen | Low | `fixed` (build) |
| FE-10 | 3 `NodeDetails` modals lack dialog semantics / Esc / focus mgmt | Medium (a11y) | `fixed` (build + hook tests) |
| FE-11 | Composite param fields (JSON/key-value/routes) don't resync on external value change (undo/redo/AI-fix) | Medium | `fixed` (test) |
| FE-12 | `addMissingToEnv` install poller + ticker leak past unmount | Low | `fixed` |
| FE-13 | App-wide modal a11y gap — ~15 page/editor modals lacked Esc/focus mgmt | Medium (a11y) | `fixed` (test) |
| FE-14 | `.muted` text fails WCAG AA contrast (~3:1) in both themes | Low (a11y) | `fixed` (measured) |
| _next_ | _(add findings here)_ | | ⏳ |

---

## Review ledger (pick the next ⏳ pending row)

| Area / file | Reviewed | Notes |
|---|---|---|
| Security basics (XSS / postMessage / token) | ✅ | from backend audit — see above |
| **Error & 401/403 handling flows** | ✅ reviewed | found FE-2 (no error boundary), FE-3 (error text leakage), FE-4 (WS 1008). 401 path is sound (clears token + flips auth → LoginPage); 403 falls through to a generic toast — acceptable now that FE-3 makes the backend's `detail` message readable. `api.ts` `request()`/`uploadArtifact()` handle 204 + non-JSON bodies correctly. |
| **Accessibility (a11y)** | ✅ reviewed | FE-6 built `useModalA11y` (Esc + focus trap + return); FE-10 covered the 3 `NodeDetails` modals; **FE-13 finished the app-wide sweep** — every remaining page/editor modal (EditorPage shortcuts + publish-review; RunnerPools ×4; Deployments ×2; Environments ×2; Workflows ×3; CodeLibrary; WorkflowHistory; Credentials) now has `role=dialog`/`aria-modal`/`aria-labelledby` + Esc + focus trap/return. Hook gained an `enabled` flag for inline modals in always-mounted pages. **Every modal in `src/` is now covered.** Icon-only-button `aria-label` sweep done (UX-2). **Color-contrast pass done (FE-14):** measured every text token with a WCAG script — `.muted`/`--ink-3` was ~2.7–3.7:1 (AA fail) in both themes; bumped to ≥4.7:1 everywhere. `--ink`/`--ink-2`/accent buttons already pass. **a11y area complete.** |
| **Performance** | ✅ reviewed | UX-4: route-level splitting cut initial JS ~80% (index 331→60 KB gzip; editor + Plotly deferred). **Selector hygiene clean** — no selector returns a fresh array/object, so no spurious re-renders / snapshot churn; FE-EditorPage selectors hoisted module-level. **Big lists are server-paginated** (Executions/Workflows/Activity) — no unbounded client render; virtualization unnecessary at expected sizes. Run-output rendering capped (FE-8). Store updates are granular + immutable. Conclusion: no re-render or list-size hotspots; live profiling not warranted. |
| **Editor correctness/UX** | ✅ reviewed | FE-7 fixed; publish flow verified. **`store.ts` deep pass done:** `applyRunEvent` is fully immutable and idempotent (replayed events re-apply the same node_started-clears / node_finished-sets, so the EVT-1 replay-then-live stream needs no client dedup); `undo`/`redo` snapshot `{nodes,edges}` with a 50-entry `HISTORY_LIMIT`, and FE-11 now resyncs open composite fields after an undo; history commits on structural changes + position-drop-end only (`shouldCommitChanges`); `cloneValue` uses `structuredClone` w/ JSON fallback; agent sub-node activity tracked + released correctly. Backed by 7 `store.*.test.ts` suites. Minor: undo/redo always set `dirty:true` (doesn't detect return-to-saved). |
| **`editor/NodeDetails.tsx` (~3550 LOC)** | ✅ reviewed | **Full pass done.** FE-10 (modal a11y), FE-11 (composite-field external resync), FE-12 (install-poller unmount leak) all fixed. **Reviewed clean:** integer/number parsing null-safe (`:1947`); expression-preview effects debounced + `cancelled`-guarded + error-caught (both editor modals); `ParamField` correctly keyed `node.id:spec.name` (node-switch remounts fields); webhook param visibility/label/synthetic-cred logic is tidy + tested (`webhookFields.test.ts`); `NodeCodePanel` load effect uses a `cancelled` guard; HighlightedTextarea mirror renders text (React-escaped, no XSS); all `String(err)` sites are clean post-FE-3. `display_when`/`group` gating is pure + predictable. |
| **`EditorPage.tsx` (~1580 LOC)** | ✅ reviewed | **Full pass done.** FE-5, FE-7, FE-13 (its 2 inline modals) fixed here. **Reviewed clean:** deep-link to a deleted workflow → graceful error state + "Back to workflows" (`:1019`), not a blank; load + `debug_run` effects both `cancelled`-guarded; `save()` re-entrancy-guarded (`saveInProgressRef`) and per-child-workflow failures isolated (parent stays clean, child stays dirty → `beforeunload` still warns); `publishDraft` saves-then-publishes-then-refetches; `connectRunStream` tears down the prior socket (FE-5); run-event `notify` mapping handles success/waiting/error; `toggleActive` optimistic with rollback; unmount effect closes WS + clears webhook timer; selectors hoisted module-level. Minor: webhook-listen poll has no max duration (by design) and the keydown effect re-subscribes each render (cheap). |
| **`CredentialsPage.tsx` (~1445 LOC)** | ✅ reviewed | **Full pass done.** Secret fields `type="password"`, no secret logging, secrets in bodies not URLs (prior audit). FE-13 added modal a11y. **Reviewed clean:** `collectData()` enforces required fields + per-preset rules (e.g. google_sheets needs api_key|access_token) before submit; `validateScopeInputs()` gates workflow/env/pool IDs; OAuth `postMessage` origin-checked + popup-blocked fallback to redirect; `load()` uses `Promise.allSettled` so the list survives a types-endpoint failure; test/refresh use per-id busy maps. **Minor UX (not blockers):** delete uses native `window.confirm` (inconsistent with the app `ConfirmDialog` — UX-5 class); OAuth popup is orphaned if the modal is closed mid-flow (listener detaches on unmount). |
| **`editor/DataPanel.tsx` (1296 LOC) / NDVPanels (1043)** | ✅ reviewed | FE-8 + FE-8b: capped the raw-JSON `<pre>` fallback in DataPanel via `pretty()` (100 K chars) and guarded `pretty()` against `undefined` (unrun node) — see `DataPanel.test.tsx`. NDVPanels truncates previews to 500 chars and its `JSON.stringify(...).slice()` sites are guarded by `preview != null` (no FE-8b-class crash). Text body capped at 20 K. DataFrame/record views render previews, not full payloads. |
| **`RunnerPoolsPage` / `EnvironmentsPage` / `DeploymentsPage`** | ✅ reviewed | All modals now a11y-complete (FE-13). **Mutations use refresh-after-mutate, not optimistic** — so there's no rollback gap (the one optimistic toggle, EditorPage `toggleActive`, *does* roll back). `DeploymentsPage` 409-unsafe-node re-prompt is solid; `runNow` navigates to the run. `EnvironmentsPage` build-status poll is a clean self-rescheduling `setTimeout` (cleanup on every re-run + 20-min per-env cap); transition toasts diff against a `prevStatuses` ref. `RunnerPoolsPage` has no polling intervals. |
| **State & data fetching** | ✅ reviewed | The only **param-driven** load (EditorPage workflow switch + `debug_run`) is `cancelled`-guarded — no stale write. Every other page loads **once on mount** (`useEffect(load, [])`), so there's no changing-dependency race. `save()` is re-entrancy-guarded; FE-7 covers refresh data-loss. **Accepted nit (consistent, benign):** mount loads don't abort in-flight fetches on unmount → a possible "setState after unmount" warning, never corruption. |
| **WebSocket run streaming** | ✅ reviewed | FE-5 fixed (EditorPage leak). `ExecutionsPage` (effect cleanup) + `ChatPanel` (closes before reconnect, unmount cleanup) are correct. Reconnect backoff resets on any message; `run_waiting` handled (toast + ChatPanel reconnect). FE-4 (1008→re-auth) now fixed. |
| **Forms & validation** | ✅ reviewed | Consistent pattern across all forms: client validates required fields + JSON-parse + numeric clamps, API is authoritative, and its errors are readable post-FE-3. Node params (`ParamField`: integer/number null-safe, `required` shown, `display_when` gating), credentials (`collectData` per-preset rules + `validateScopeInputs`), deployments (`DeploymentDialog`: JSON params guarded, name required, `every` clamped ≥1; cron left to server validation — acceptable), environments/runner-pools (required-field guards). No client/server parity gaps that surface as confusing UX. |
| **Routing / deep links / refresh survival** | ✅ reviewed | FE-9 fixed: added a `path="*"` `NotFound` catch-all (unknown URLs no longer render a blank `<Routes>`). `authBootstrap` retry/backoff verified sound — transient (network/5xx) retries with capped exponential backoff, definitive 404 → no-auth fallback, never an infinite request storm. Chat routes (`/chat/:id`) correctly sit outside the auth gate. Deep-link to a deleted workflow handled by `EditorPage` (see its row). The bootstrap loader (`auth === null` → `BackendLoading`) prevents rendering against an unready API. |

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
- **Fix:** extracted a shared `handleUnauthorized()` in `api.ts` (clear token +
  user + `unauthorizedHandler`), used by both REST 401 sites and the run-stream
  `onclose` — a `1008` close now routes the user to login immediately instead of
  waiting for the next REST 401.
- **Status:** `fixed`. Verified: `api.runStream.test.ts` (2 tests — 1008 fires
  the handler, 1000 does not) + full suite 140/140, `typecheck` clean.

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

### FE-9 — No catch-all route; unknown URLs render a blank screen (Low)
- **Evidence:** `App.tsx` `<Routes>` had no `path="*"` fallback (grep clean). Any
  unmatched authed path (a typo, a stale bookmark, a removed page) matched no
  `<Route>`, so `<Routes>` rendered **nothing** — a fully blank screen with no
  navigation back. Same "black/white screen" class FE-2 and the auth-bootstrap
  fix were guarding against, just via routing rather than a throw.
- **Impact:** A mistyped or stale deep link strands the user on an empty page;
  they must know to edit the URL or hard-navigate home.
- **Fix:** added a `NotFound` component (heading + explanation + "Back to
  workflows" link, styled with the existing `.screen-center` / `.btn` tokens) and
  a `<Route path="*" element={<NotFound />} />` as the last route in the authed
  `<Routes>`. The chat-route `<Routes>` is single-path by design and unaffected.
- **Status:** `fixed`. Verified: `typecheck` + `build` clean; full suite
  134/134. (Presentational catch-all — no unit test; the route table is exercised
  by the build's type-check of the element types.)

### FE-10 — `NodeDetails` modals lack dialog semantics / Esc / focus management (Medium, a11y)
- **Evidence:** FE-6 swept the top-level modals but `editor/NodeDetails.tsx` has
  three of its own that were missed: `CredentialCreateModal` (697),
  `ExpressionEditorModal` (1360), and `CodeEditorModal` (2524). All three closed
  only via overlay-click — no `role="dialog"`/`aria-modal`, no global `Esc`
  (the cred modal's `Esc` only fired while its name field was focused; the expr
  modal's `Esc` only dismissed the autocomplete dropdown), and no focus
  return-on-close. Same WCAG 2.4.3 / 2.1.2 gap FE-6 fixed elsewhere.
- **Impact:** Keyboard/AT users can't reliably dismiss these dialogs, focus can
  escape behind them, and focus isn't returned to the trigger — and these are
  high-traffic surfaces (every credential creation + every expression/code edit).
- **Fix:** wired the existing `useModalA11y` hook into all three + added
  `role="dialog"`/`aria-modal`/`aria-labelledby` (heading `id`s) and
  `tabIndex={-1}` on each container. `CredentialCreateModal` gets the full hook
  (Esc + focus trap + return — it's a plain form). `ExpressionEditorModal` and
  `CodeEditorModal` get the hook with **`trapFocus:false`** (the FE-6-documented
  escape hatch) because they embed editors/autocomplete that own `Tab`/arrow
  keys — they still get Esc + focus-in + focus-return without the hook fighting
  the inner widget.
- **Status:** `fixed`. Verified: `typecheck` + `build` clean; full suite
  134/134; the hook's own behavior (Esc / focus-in / focus-return / Tab-wrap) is
  covered by `useModalA11y.test.tsx`. (The *app-wide* modal sweep across every
  page was finished separately — see FE-13.)
- **Behavior note:** in `ExpressionEditorModal` the hook's `Esc` (capture phase)
  now closes the modal even while the autocomplete dropdown is open, where it
  previously dismissed just the dropdown. Acceptable — Esc-closes-dialog is the
  standard expectation and the dropdown still dismisses on blur/selection/typing.

### FE-11 — Composite param fields don't resync on external value change (Medium)
- **Evidence:** `JsonField` (`NodeDetails.tsx:164`), `KeyValueField` (`:206`),
  and `RoutesField` (`:1700`) seed local editing state once
  (`useState(() => …value)`) with no resync to the prop. `ParamField` is keyed
  `node.id:spec.name` (`:3371`) so switching nodes remounts them correctly — but
  a value change to the **same mounted node** (undo/redo while the inspector is
  open, an applied AI fix, a pin/restore) leaves these three fields showing the
  *old* content, and the next keystroke commits from that stale base. Plain
  string/number/select/segmented fields read `value` directly and were unaffected.
- **Impact:** Editing a JSON / key-value / routes param after an undo (or AI fix)
  on the open node silently writes from stale state — a data-integrity bug on the
  exact fields where it's hardest to notice.
- **Fix:** added `useExternalValueSync(value, isFocused, apply)` — reseeds the
  buffer when the serialized upstream value changes **and** the field isn't
  focused (so a value update caused by the field's own `onChange` while typing is
  absorbed, never reapplied / never clobbers typing). Wired into all three
  fields (textarea-focus check for `JsonField`; `wrapRef.contains(activeElement)`
  for the multi-input `KeyValueField`/`RoutesField`).
- **Status:** `fixed`. Verified: `paramFieldResync.test.tsx` (3 tests — routes
  resync, JSON resync, and the "don't clobber while typing" guard) + full suite
  137/137, `typecheck` + `build` clean.

### FE-12 — Package-install poller + ticker leak past unmount (Low)
- **Evidence:** `NodeDetails.addMissingToEnv` (`:3191`) starts a 1 s
  `setInterval` ticker and a `while` loop that polls `api.getEnvironment` every
  2 s for up to `PACKAGE_INSTALL_TIMEOUT_MS` (10 min). Both live inside an async
  function, not a `useEffect`, so the FE-8 interval-cleanup sweep didn't cover
  them — closing the NDV / switching nodes mid-install left the loop polling and
  `setState`-ing on an unmounted tree for up to 10 minutes.
- **Impact:** Minor — bounded background API churn + React "state update on
  unmounted component" warnings; no user-visible corruption.
- **Fix:** added an `aliveRef` (set false on unmount via effect cleanup) and a
  guard after each poll `await` that clears the ticker and returns when the
  component is gone.
- **Status:** `fixed`. Verified: `typecheck` + `build` clean; full suite green.

### FE-13 — App-wide modal a11y gap; ~15 modals lacked Esc / focus management (Medium, a11y)
- **Evidence:** beyond the modals FE-6/FE-10 covered, a repo sweep
  (`grep modal-overlay` vs `grep Escape|useModalA11y`) found page/editor dialogs
  that closed only via overlay-click — no reliable global `Esc`, no focus trap,
  no focus return, several missing `aria-modal`/`aria-labelledby`:
  `EditorPage` (shortcuts + publish-review, inline), `RunnerPoolsPage` (×4:
  Pool / SSH onboard / Add machine / Edit runner), `DeploymentsPage` (Unsafe
  nodes + Deployment editor), `EnvironmentsPage` (Create + Edit env),
  `WorkflowsPage` (Create + Rename + Provider-status), `CodeLibraryPage`
  (CodeModuleDialog), `WorkflowHistory`, `CredentialsPage` (CreateCredentialModal).
- **Impact:** keyboard/AT users couldn't reliably dismiss these dialogs, focus
  could sit behind the modal, and focus wasn't returned to the trigger — a
  WCAG 2.4.3 / 2.1.2 gap spanning most admin/ops surfaces.
- **Fix:** wired `useModalA11y` into every one + added `role=dialog` /
  `aria-modal` / `aria-labelledby` / `tabIndex={-1}` to each container.
  Code-editor modals (`CodeModuleDialog`) use `trapFocus:false`. For modals
  rendered **inline in always-mounted pages** (EditorPage's two — extracted into
  a small `A11yModal` shell — and WorkflowsPage's rename/provider dialogs), the
  hook gained an **`enabled` option** so it activates only while the dialog is
  open instead of leaving a global Esc handler attached.
- **Status:** `fixed`. Verified: `typecheck` + `build` clean; full suite
  137/137; new `enabled`-flag test in `useModalA11y.test.tsx` (5 tests).
  **Every `modal-overlay` in `src/` now routes through `useModalA11y`.**

### FE-14 — `.muted` secondary text fails WCAG AA contrast in both themes (Low, a11y)
- **Evidence:** `.muted` (used app-wide for descriptions, hints, counts,
  timestamps, empty states) maps to `--ink-3`. Measured with a WCAG-2 relative-
  luminance script against every panel surface:
  - Dark `--ink-3` `#5d6878`: **3.48 / 3.26 / 3.05 / 2.69** vs
    `--bg` / `--surface` / `--surface-2` / `--surface-3`.
  - Light `--ink-3` `#79869a`: **3.47 / 3.69 / 3.28 / 2.96**.
  All below the 4.5:1 AA floor for normal-size text (some below the 3:1 large-
  text floor). `--ink` (primary) and `--ink-2` already pass (~6.8–16:1).
- **Impact:** Low-vision users struggle to read secondary text — a broad but
  low-severity WCAG 1.4.3 gap.
- **Fix:** bumped the token only (no logic): dark `--ink-3` → `#8a93a6`
  (now **6.4 / 6.0 / 5.6 / 4.9:1**), light `--ink-3` → `#5a6577`
  (now **5.5 / 5.9 / 5.2 / 4.7:1**) — clears AA on every surface in both themes.
  Propagates to all `var(--ink-3)` users in `index.css` + `editor.css`.
- **Status:** `fixed`. Verified: contrast script (values above); `build` clean
  (Vite processes the CSS); full suite 138/138. Visual change is global but
  conservative (same hue, adjusted lightness) — easy to tune if the owner wants.

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
| ~~UX-5~~ | Errors | ✅ **Done.** Adopted a consistent convention and removed every double-display (inline banner **and** toast for the same failure, ~18 handlers): **form-submission errors → inline near the form** (credential create/test, invite user, OAuth start, node code/cred modals); **item/toolbar-action errors → a single toast carrying the detail** via `errorMessage(err)` (deploy run/toggle/delete, save/publish/run/cancel/AI, role/user delete, workflow delete). Inline page banners are now reserved for blocking **load** errors. | — |
| ~~UX-6~~ | Feedback | ✅ **Done.** Persistent EditorPage banner ("⏸ This run is paused for tool approval") with inline approve/reject (reused `RunApprovalsPanel`, extracted to its own file so the editor chunk doesn't pull ExecutionsPage) + "View run →" link + dismiss. Deciding reconnects the run stream so the editor resumes. Replaces the easy-to-miss transient toast. | — |
| ~~UX-7~~ | Toasts | ✅ **Done.** `ToastProvider` rewritten: errors are sticky (manual dismiss), success/info auto-dismiss, all pause-on-hover; errors get `role="alert"`. Tests in `ToastProvider.test.tsx`. | — |
| ~~UX-8~~ | Editor nav | ✅ **Done.** Migrated the root to a data router (`createBrowserRouter`/`RouterProvider` in `main.tsx`; App keeps its descendant `<Routes>` + auth gating unchanged) so EditorPage can `useBlocker` — in-app nav away from a dirty editor (Logo link, browser back, programmatic) now prompts a "Leave with unsaved changes?" ConfirmDialog. Complements FE-7 (refresh/close). Runtime-verified: deep link renders correctly, no console errors. | — |
| ~~UX-9~~ | Polish | ✅ **Done.** EditorPage's `message` status line (publish/AI/debug-snapshot info) was rendered in the red `.toolbar-error` style — a UX-5 follow-on. Gave it a neutral `.toolbar-message` style so success/info text no longer looks like an error. | — |

### Opportunity scan (this pass) — what was checked & cleared
A sweep for additional issues found the codebase in good shape:
- **Clean:** no `target="_blank"` missing `rel`, no `console.log/debug`, no `<img>`
  without `alt`, no external `href` missing `rel` (all of `src/`).
- **Index keys** (`key={i}`) appear only on render-only or *controlled* lists
  (chart segments, preview tables, text spans, KV/routes rows) — values derive
  from state, so no data corruption; at most a minor focus nit on reorder. Not
  worth adding id fields.
- **Data tables** (`PortDataViewer`, artifact/dataset previews) are
  preview-capped (`.slice(0,3)`, bounded `shownRows`/`shownColumns`) — no
  unbounded render (consistent with FE-8).
- **Deferred (nice-to-have, not a strict gap):** no SPA route-change
  announcement/focus-move for screen-reader users. Reasonable future a11y polish;
  left out as over-reach for a trusted-team internal tool.
