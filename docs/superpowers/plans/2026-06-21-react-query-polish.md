# React Query Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix layout shifts on WorkflowsPage refetch, stop `useNodes` over-fetching, and widen `QueryControls` to allow `placeholderData`/`gcTime`.

**Architecture:** Three targeted edits across two files. No new files, no new tests — changes are configuration/type-level; correctness is verified by TypeScript and the existing test suite.

**Tech Stack:** React Query v5 (`@tanstack/react-query`), TypeScript strict mode.

## Global Constraints

- All changes are in exactly two files: `apps/web/src/queries/index.ts` and `apps/web/src/WorkflowsPage.tsx`
- Do NOT create new test files — no logic changes requiring new tests
- TypeScript `--noEmit` must pass with zero new errors after each task
- Full test suite must pass after each task
- `QueryControls` remains a `Pick` from `UseQueryOptions` — do not change the pattern, only add two more keys
- `staleTime: Infinity, gcTime: 30 * 60 * 1000` must appear BEFORE `...options` in `useNodes` so callers can override
- `keepPreviousData` must be imported from `@tanstack/react-query` (not inlined as `(prev) => prev`)
- Do NOT add `placeholderData` to `useWorkflowProviderTriggers` — that query is modal-scoped, not a page list

---

### Task 1: Extend QueryControls + tune useNodes

**Files:**
- Modify: `apps/web/src/queries/index.ts`

**Interfaces:**
- Produces:
  ```ts
  // QueryControls now includes placeholderData and gcTime
  type QueryControls<TData> = Pick<
    UseQueryOptions<TData, Error, TData, QueryKey>,
    "enabled" | "refetchInterval" | "staleTime" | "placeholderData" | "gcTime"
  >;
  
  // useNodes has staleTime/gcTime baked in
  export function useNodes(options?: QueryControls<...>): UseQueryResult<...>
  ```

- [ ] **Step 1: Read the current file around the two target blocks**

Read `apps/web/src/queries/index.ts` lines 24–32 (QueryControls) and lines 184–195 (useNodes). Confirm exact current text before editing.

- [ ] **Step 2: Widen QueryControls**

In `apps/web/src/queries/index.ts`, find:
```ts
type QueryControls<TData> = Pick<
  UseQueryOptions<TData, Error, TData, QueryKey>,
  "enabled" | "refetchInterval" | "staleTime"
>;
```

Replace with:
```ts
type QueryControls<TData> = Pick<
  UseQueryOptions<TData, Error, TData, QueryKey>,
  "enabled" | "refetchInterval" | "staleTime" | "placeholderData" | "gcTime"
>;
```

- [ ] **Step 3: Tune useNodes**

In `apps/web/src/queries/index.ts`, find the `useNodes` function body. It currently contains:
```ts
return useQuery({
  queryKey: queryKeys.nodes,
  queryFn: api.nodes,
  ...options,
});
```

Replace with:
```ts
return useQuery({
  queryKey: queryKeys.nodes,
  queryFn: api.nodes,
  staleTime: Infinity,
  gcTime: 30 * 60 * 1000,
  ...options,
});
```

The `...options` spread MUST come after `staleTime`/`gcTime` so callers can override if needed.

- [ ] **Step 4: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors. If `placeholderData` type is not directly pickable from `UseQueryOptions` (React Query v5 may use a conditional type), add `| undefined` as needed or use `Partial<Pick<...>>` — but prefer the exact Pick first.

- [ ] **Step 5: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass (same count as before — 334 tests in 60 files).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/queries/index.ts
git commit -m "perf(queries): widen QueryControls, add staleTime+gcTime to useNodes"
```

---

### Task 2: Add placeholderData to WorkflowsPage list queries

**Files:**
- Modify: `apps/web/src/WorkflowsPage.tsx`

**Interfaces:**
- Consumes: `keepPreviousData` from `@tanstack/react-query`, `QueryControls` now includes `placeholderData` (Task 1)

- [ ] **Step 1: Read the current query block**

Read `apps/web/src/WorkflowsPage.tsx` lines 215–235. Confirm exact text of the four hook calls before editing.

- [ ] **Step 2: Add keepPreviousData import**

In `apps/web/src/WorkflowsPage.tsx`, find the existing import from `@tanstack/react-query`. It likely imports `useQuery`, `useMutation`, or similar. Add `keepPreviousData` to that import:

```ts
import { ..., keepPreviousData } from "@tanstack/react-query";
```

If there is no existing `@tanstack/react-query` import, add a new line:
```ts
import { keepPreviousData } from "@tanstack/react-query";
```

- [ ] **Step 3: Add placeholderData to the four list queries**

Update the four hook calls (lines ~219–227). Replace:

```ts
const workflowsQuery = useWorkflows({
  refetchInterval: (query) =>
    query.state.data?.some((wf) => wf.last_run_status === "running") ? 3000 : false,
});
const deploymentsQuery = useDeployments();
const credentialsQuery = useCredentials();
const environmentsQuery = useEnvironments();
```

With:

```ts
const workflowsQuery = useWorkflows({
  refetchInterval: (query) =>
    query.state.data?.some((wf) => wf.last_run_status === "running") ? 3000 : false,
  placeholderData: keepPreviousData,
});
const deploymentsQuery = useDeployments({ placeholderData: keepPreviousData });
const credentialsQuery = useCredentials({ placeholderData: keepPreviousData });
const environmentsQuery = useEnvironments({ placeholderData: keepPreviousData });
```

Do NOT add `placeholderData` to `useWorkflowProviderTriggers` — it is modal-scoped.

- [ ] **Step 4: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors. The `QueryControls` type now includes `placeholderData` (from Task 1), so the call sites should compile.

- [ ] **Step 5: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/WorkflowsPage.tsx
git commit -m "perf(ui): prevent layout shifts with keepPreviousData on WorkflowsPage list queries"
```
