# Phase 6: Frontend Data Layer + Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace page-owned REST lifecycle state with TanStack Query, then decompose the editor store and NodeDetails without changing the public component/store API.

**Architecture:** Keep `apps/web/src/api.ts` as the transport boundary. Add `apps/web/src/queries/` as the request lifecycle layer with typed hooks and mutation invalidation. Migrate pages in the master-plan order, preserving current UX defaults and keeping WebSocket run-event streaming in Zustand. Decomposition tasks are behavior-preserving refactors guarded by existing tests.

**Validation commands (Windows dev box):**

```powershell
cd apps\web
npm run typecheck
npm run test
npm run build
npm run test:e2e
```

Known workspace note: this branch already contains unrelated in-progress changes in editor/API/AI files. Do not revert them; keep Phase 6 edits scoped to the frontend data layer unless a later decomposition task explicitly touches editor files.

---

## Task 1: TanStack Query infrastructure

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `apps/web/src/App.tsx`
- Create: `apps/web/src/queries/client.ts`
- Create: `apps/web/src/queries/keys.ts`
- Create: `apps/web/src/queries/index.ts`

- [x] Step 1: Install `@tanstack/react-query`.
- [x] Step 2: Create a single shared `QueryClient` with defaults:
  - `staleTime: 30_000`
  - `retry: 1`
  - `refetchOnWindowFocus: false`
- [x] Step 3: Mount `QueryClientProvider` once around the authenticated app shell.
- [x] Step 4: Export stable query keys from `queries/keys.ts`.
- [x] Step 5: Run `npm run typecheck`.

Acceptance: app boot behavior is unchanged, no page has been migrated yet, and typecheck passes.

---

## Task 2: Typed query hooks and invalidation helpers

**Files:**
- Create/modify: `apps/web/src/queries/index.ts`

- [x] Step 1: Add read hooks wrapping existing `api.ts` functions:
  - `useWorkflows`
  - `useWorkflowProviderTriggers`
  - `useDeployments`
  - `useCredentials`
  - `useCredentialTypes`
  - `useEnvironments`
  - `useRunnerPools`
  - `useSystemSettings`
  - `useRuns`
  - `useAllRuns`
  - `useRuntimeMode`
  - `useQueueStats`
  - `useRun`
  - `useRunTimeline`
  - `useOrgUsage`
- [x] Step 2: Add mutation hooks with precise invalidation:
  - workflows: create, update, delete
  - environments: create, update, delete, rebuild
  - runs: rerun, retry, cancel
- [x] Step 3: Keep hook signatures explicit and typed; do not leak transport implementation details into pages.

Acceptance: query hooks compile and pages can opt in incrementally.

---

## Task 3: Migrate WorkflowsPage

**Files:**
- Modify: `apps/web/src/WorkflowsPage.tsx`

- [x] Step 1: Replace initial `useEffect(load)` and manual mounted refs with `useWorkflows`, `useDeployments`, `useCredentials`, and `useEnvironments`.
- [x] Step 2: Replace running-workflow interval with `refetchInterval`.
- [x] Step 3: Replace create/delete/rename/duplicate refresh calls with query mutation invalidation.
- [x] Step 4: Keep skeleton, error, empty, provider-trigger modal, and filter behavior identical.

Acceptance: WorkflowsPage renders the same states with no manual polling cleanup code.

---

## Task 4: Migrate ExecutionsPage

**Files:**
- Modify: `apps/web/src/ExecutionsPage.tsx`

- [x] Step 1: Replace workflow/run list effects with `useWorkflows` and `useAllRuns`.
- [x] Step 2: Replace run-list polling with `refetchInterval`.
- [x] Step 3: Convert Ops dashboard to `useRuntimeMode` and `useQueueStats` with `refetchInterval: 5000`.
- [x] Step 4: Convert run detail and timeline fetches to `useRun` and `useRunTimeline`.
- [x] Step 5: Keep WebSocket streaming as push state, using query invalidation/refetch on run events.

Acceptance: run list and detail panel stay fresh, and interval cleanup is owned by TanStack Query.

---

## Task 5: Migrate EnvironmentsPage

**Files:**
- Modify: `apps/web/src/EnvironmentsPage.tsx`

- [x] Step 1: Replace environment, runner pool, and settings effects with query hooks.
- [x] Step 2: Replace build-status timeout poller with `refetchInterval`.
- [x] Step 3: Replace create/update/delete/rebuild refresh calls with environment mutation hooks.
- [x] Step 4: Preserve build transition toasts and the 20-minute stuck-build warning.

Acceptance: no leaked timers on unmount; build polling stops once builds finish or all active builds have timed out.

---

## Task 6: Continue page-by-page migration

**Files:**
- Modify: `apps/web/src/CredentialsPage.tsx`
- Modify: `apps/web/src/DeploymentsPage.tsx`
- Modify: `apps/web/src/RunnerPoolsPage.tsx`
- Modify: `apps/web/src/OrganizationPage.tsx`
- Modify: editor-adjacent reads as separate narrow patches
- Modify: `apps/web/src/CodeLibraryPage.tsx`

- [x] Step 1: Migrate CredentialsPage.
- [x] Step 2: Migrate DeploymentsPage.
- [x] Step 3: Migrate RunnerPoolsPage.
- [x] Step 4: Migrate OrganizationPage.
- [x] Step 5: Migrate palette manifests and pinned data only after page reads are green.
- [x] Step 6: Migrate Code Library reads/writes to query hooks and improve the page UX with summary cards, search, clearer empty states, and save-and-preview workflow.

Acceptance: each page lands independently with typecheck, unit tests, and e2e smoke green.

---

## Task 7: Editor store decomposition

**Files:**
- Modify/create under `apps/web/src/editor/store/`
- Preserve: `apps/web/src/editor/store.ts` public imports

- [x] Step 1: Add slice files:
  - `graphSlice.ts`
  - `runSlice.ts`
  - `childWorkflowSlice.ts`
  - `clipboardSlice.ts`
  - `index.ts`
- [x] Step 2: Compose slice initial state into the same exported store hook and public helper API.
- [x] Step 3: Move code in narrow, test-backed chunks.
- [x] Step 4: Run all existing `editor/store.*.test.*` tests after each chunk.

Acceptance: store tests pass unmodified and component imports do not churn.

---

## Task 8: NodeDetails decomposition

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`
- Create under: `apps/web/src/editor/node-details/`

- [x] Step 1: Map current internal panel boundaries before moving code.
- [x] Step 2: Extract pure helpers/types first.
- [x] Step 3: Extract shared selector, display/credential/webhook/label/expression helpers, and package/system dependency panels behind the same parent export.
- [x] Step 4: Add or preserve focused tests for moved behavior.

Acceptance: `NodeDetails` public export remains stable, behavior is unchanged, and typecheck/tests pass after each extraction.

---

## Task 9: CSS tokens and cleanup

**Files:**
- Create: `apps/web/src/editor/tokens.css`
- Modify: editor CSS imports as needed

- [x] Step 1: Extract repeated editor color, spacing, and z-index values into tokens.
- [x] Step 2: Require newly extracted components to use module-scoped or component-scoped CSS.
- [x] Step 3: Remove dead styles only when proven unused by the extracted components.

Acceptance: no broad visual rewrite; desktop and mobile editor layouts remain stable.
