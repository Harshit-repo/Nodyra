# Error Handling Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `useServerPlatform` failures instead of silently swallowing them; replace `credentialsQuery.refetch()` with `invalidateQueries` so React Query's stale-while-revalidate semantics apply correctly.

**Architecture:** Two files, two targeted edits. No new files, no new tests.

**Tech Stack:** React 18, React Query v5 (`@tanstack/react-query`), TypeScript.

## Global Constraints

- Files modified: `apps/web/src/hooks/useServerPlatform.ts`, `apps/web/src/CredentialsPage.tsx`
- No new test files
- `useServerPlatform` catch: log with `console.error` only — do not add error state, do not throw, do not call `notify` (the hook is used outside the toast provider tree in some contexts)
- `CredentialsPage`: use `useQueryClient()` from `@tanstack/react-query` and call `queryClient.invalidateQueries({ queryKey: queryKeys.credentials })`; import `queryKeys` from `"./queries"`
- `queryKeys.credentials` is `["credentials"]` — this prefix-matches both `credentials` and `credentials/types` queries, so both are invalidated (correct behaviour)
- Full test suite must pass; `npx tsc --noEmit` zero new errors

---

### Task 1: Fix useServerPlatform silent catch

**Files:**
- Modify: `apps/web/src/hooks/useServerPlatform.ts`

- [ ] **Step 1: Read the full file**

Read `apps/web/src/hooks/useServerPlatform.ts` (22 lines). Confirm the exact catch block text.

- [ ] **Step 2: Replace the silent catch**

Find:
```ts
.catch(() => {});
```

Replace with:
```ts
.catch((err: unknown) => {
  console.error("Failed to detect server platform:", err);
});
```

- [ ] **Step 3: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors.

- [ ] **Step 4: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/hooks/useServerPlatform.ts
git commit -m "fix(hooks): log useServerPlatform fetch errors instead of silently swallowing"
```

---

### Task 2: Replace credentialsQuery.refetch() with invalidateQueries

**Files:**
- Modify: `apps/web/src/CredentialsPage.tsx`

**Context:**
- `CredentialsPage.tsx` currently has NO import from `@tanstack/react-query` and NO `queryClient` variable
- `queryKeys` is exported from `"./queries"` (alongside the hook imports already present)
- The refetch is at line 878: `void credentialsQuery.refetch()` inside the `onCreated` callback of `CreateCredentialModal`

- [ ] **Step 1: Read the relevant sections**

Read `apps/web/src/CredentialsPage.tsx`:
- Lines 1–30 (imports section)
- Lines 870–885 (the refetch call site)

Confirm there is no existing `@tanstack/react-query` import and no `queryClient` usage.

- [ ] **Step 2: Add @tanstack/react-query import**

Add a new import line near the top of the file (after the `"react"` import, before the `"./queries"` import):
```ts
import { useQueryClient } from "@tanstack/react-query";
```

- [ ] **Step 3: Add queryKeys to the ./queries import**

Find the existing import from `"./queries"`:
```ts
import {
  useCredentialTypes,
  useCredentials,
  useDeleteCredentialMutation,
  useRefreshCredentialMutation,
  useTestCredentialMutation,
} from "./queries";
```

Add `queryKeys` to it:
```ts
import {
  queryKeys,
  useCredentialTypes,
  useCredentials,
  useDeleteCredentialMutation,
  useRefreshCredentialMutation,
  useTestCredentialMutation,
} from "./queries";
```

- [ ] **Step 4: Add useQueryClient hook call**

Inside the `CredentialsPage` component function body, near the other hook calls at the top of the function (after `useState`/`useRef` calls), add:
```ts
const queryClient = useQueryClient();
```

- [ ] **Step 5: Replace refetch with invalidateQueries**

Find (line 878):
```ts
void credentialsQuery.refetch();
```

Replace with:
```ts
void queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
```

- [ ] **Step 6: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors.

- [ ] **Step 7: Run test suite**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/CredentialsPage.tsx
git commit -m "fix(credentials): invalidate credentials query after OAuth creation instead of calling refetch"
```
