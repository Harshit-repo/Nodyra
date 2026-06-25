# React Query Polish — Design Spec

**Date:** 2026-06-21
**Status:** Approved
**Batch:** Frontend Optimizations #2 of 6

## Problem

Three React Query gaps cause visible UX jank and unnecessary network traffic:

1. **Layout shifts on refetch** — `WorkflowsPage` renders four independent queries (`useWorkflows`, `useDeployments`, `useCredentials`, `useEnvironments`) with no `placeholderData`. When React Query refetches in the background, `data` briefly becomes `undefined`, collapsing list sections to loading states even though stale data is perfectly usable.

2. **Node manifests over-fetch** — `useNodes` runs with React Query defaults (staleTime: 0). Node manifests are static for the lifetime of a session; every mount of `EditorPage` triggers a redundant network call.

3. **`QueryControls` type is too narrow** — the shared `QueryControls<TData>` type (picked from `UseQueryOptions`) exposes only `enabled | refetchInterval | staleTime`. Adding `placeholderData` or `gcTime` at a call site is a type error, forcing raw `useQuery` bypasses.

## Solution

Three targeted changes, all in two files.

---

## Architecture

### Change 1: Extend `QueryControls` (`queries/index.ts` line 26)

```ts
// Before:
type QueryControls<TData> = Pick<
  UseQueryOptions<TData, Error, TData, QueryKey>,
  "enabled" | "refetchInterval" | "staleTime"
>;

// After:
type QueryControls<TData> = Pick<
  UseQueryOptions<TData, Error, TData, QueryKey>,
  "enabled" | "refetchInterval" | "staleTime" | "placeholderData" | "gcTime"
>;
```

No runtime change. Purely a type widening that unlocks the next two changes and allows future call sites to set caching options without bypassing the abstraction.

### Change 2: Tune `useNodes` (`queries/index.ts` lines 186–192)

```ts
export function useNodes(options?: QueryControls<Awaited<ReturnType<typeof api.nodes>>>) {
  return useQuery({
    queryKey: queryKeys.nodes,
    queryFn: api.nodes,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    ...options,           // caller can still override
  });
}
```

- `staleTime: Infinity` — manifests never go stale; React Query will not automatically background-refetch them.
- `gcTime: 30 * 60 * 1000` — cache entry lives for 30 min after last consumer unmounts, surviving editor open/close cycles within a session.
- Caller override via `...options` is preserved — test harnesses or future edge cases can still pass custom options.

### Change 3: `placeholderData` in `WorkflowsPage` (`WorkflowsPage.tsx` lines 219–227)

Import `keepPreviousData` from `@tanstack/react-query` and pass it to the four list queries:

```ts
import { keepPreviousData } from "@tanstack/react-query";

// In component:
const workflowsQuery = useWorkflows({
  refetchInterval: (query) =>
    query.state.data?.some((wf) => wf.last_run_status === "running") ? 3000 : false,
  placeholderData: keepPreviousData,
});
const deploymentsQuery = useDeployments({ placeholderData: keepPreviousData });
const credentialsQuery = useCredentials({ placeholderData: keepPreviousData });
const environmentsQuery = useEnvironments({ placeholderData: keepPreviousData });
```

`keepPreviousData` is a React Query v5 helper function (`(prev) => prev`) that returns the previous query result as placeholder data, keeping the UI populated with stale data while a background refetch is in flight. The `providerTriggersQuery` is omitted — it is scoped to a modal interaction, not a page-level list, so layout shift there is acceptable.

---

## Edge Cases

| Case | Resolution |
|---|---|
| **Nodes change on server mid-session** | `staleTime: Infinity` means the user sees stale manifests until they reload. Node manifests change only on server deploys, which require a user action to pick up anyway. Acceptable. |
| **`keepPreviousData` + `isLoading` distinction** | With `placeholderData`, `isLoading` stays false (data is "available"). Use `isFetching` to show a subtle refetch indicator if needed. Existing WorkflowsPage error guards use `isError && !data` — these remain correct. |
| **Caller overrides `staleTime` on `useNodes`** | The spread order `staleTime: Infinity, ...options` means callers can override. Intentional. |
| **`gcTime` type** | `gcTime` is `number` in React Query v5. `30 * 60 * 1000` = 1,800,000 ms = 30 minutes. |

---

## Files

| Action | File |
|---|---|
| Modify | `apps/web/src/queries/index.ts` |
| Modify | `apps/web/src/WorkflowsPage.tsx` |

---

## Tests

No new test files needed — these are configuration changes, not new logic:
- `QueryControls` widening: verified by TypeScript `--noEmit` (no compile error when passing `placeholderData` at a call site)
- `useNodes` options: verified by confirming `staleTime`/`gcTime` appear in the spread at the definition site (existing tests continue to pass)
- `WorkflowsPage` placeholderData: verified by full test suite pass (no snapshot tests reference loading states)
