# Per-Route Error Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate page crashes to a per-route boundary so the nav remains usable and no full app reload is ever needed.

**Architecture:** A new `HomeLayout` route shell renders `HomeHeader` + `Suspense` + `Outlet`; all non-editor pages become nested children whose route element is wrapped in `PageErrorBoundary` (inline fallback). `EditorPage` keeps its own full-screen `ErrorBoundary` with `resetKey={location.pathname}`.

**Tech Stack:** React 18, React Router v6 (`<Route element>` nesting + `<Outlet>`), `@phosphor-icons/react` v2.1.10, Vitest + React Testing Library, TypeScript.

## Global Constraints

- All new components use named exports (not default).
- `PageErrorBoundary` is a class component (React does not support error boundaries as function components).
- Do NOT touch `ErrorBoundary.tsx` or its tests — the existing full-screen boundary stays in `main.tsx` and on the editor route.
- `HomeHeader` takes no props; remove only the import and the `<HomeHeader />` JSX line from each page — leave everything else untouched.
- Tasks 1 and 2 are independently committable. Task 3 must update `App.tsx` AND remove `HomeHeader` from all 11 pages in the same commit to avoid a double-header intermediate state.

---

### Task 1: PageErrorBoundary component + CSS

**Files:**
- Create: `apps/web/src/PageErrorBoundary.tsx`
- Create: `apps/web/src/PageErrorBoundary.test.tsx`
- Modify: `apps/web/src/index.css`

**Interfaces:**
- Produces: `export class PageErrorBoundary extends Component<{ children: ReactNode }, State>` — wraps any route element, catches render errors, shows an inline fallback.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/PageErrorBoundary.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageErrorBoundary } from "./PageErrorBoundary";

function Boom(): never {
  throw new Error("kaboom");
}

describe("PageErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children when nothing throws", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <div>healthy</div>
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("healthy")).toBeTruthy();
  });

  it("shows inline fallback with alert role when a child throws", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    expect(alert.className).toContain("page-error");
    expect(alert.className).not.toContain("app-error");
  });

  it("shows the correct headline and containment message", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("This page ran into a problem")).toBeTruthy();
    expect(
      screen.getByText(/The error is contained here/),
    ).toBeTruthy();
  });

  it("shows 'Try again' button and 'Back to workflows' link", () => {
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Boom />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to workflows" })).toBeTruthy();
  });

  it("'Try again' clears the error and re-renders children", () => {
    let crash = true;
    function Maybe() {
      if (crash) throw new Error("kaboom");
      return <div>recovered</div>;
    }
    render(
      <MemoryRouter>
        <PageErrorBoundary>
          <Maybe />
        </PageErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("This page ran into a problem")).toBeTruthy();
    crash = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("recovered")).toBeTruthy();
    expect(screen.queryByText("This page ran into a problem")).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/web && pnpm test -- src/PageErrorBoundary.test.tsx
```

Expected: all 5 tests FAIL with "Cannot find module './PageErrorBoundary'".

- [ ] **Step 3: Create PageErrorBoundary.tsx**

Create `apps/web/src/PageErrorBoundary.tsx`:

```tsx
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Plugs } from "@phosphor-icons/react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Page error:", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page-error" role="alert">
        <div className="app-error-card">
          <Plugs size={40} className="page-error-icon" />
          <div className="app-error-title">This page ran into a problem</div>
          <div className="app-error-text">
            The error is contained here — the rest of the app is unaffected.
          </div>
          <div className="app-error-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => this.setState({ error: null })}
            >
              Try again
            </button>
            <Link to="/" className="btn btn-primary">
              Back to workflows
            </Link>
          </div>
        </div>
      </div>
    );
  }
}
```

- [ ] **Step 4: Add `.page-error` CSS to index.css**

Find the `.app-error` block in `apps/web/src/index.css` (around line 428) and add the new class immediately after the closing brace of `.app-error-actions`:

```css
.page-error {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: 24px;
}

.page-error-icon {
  color: var(--ink-2, #aab1bd);
  margin-bottom: 4px;
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd apps/web && pnpm test -- src/PageErrorBoundary.test.tsx
```

Expected: 5 tests PASS.

- [ ] **Step 6: Run the full test suite to check for regressions**

```
cd apps/web && pnpm test
```

Expected: all tests pass (no regressions in ErrorBoundary.test.tsx or others).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/PageErrorBoundary.tsx apps/web/src/PageErrorBoundary.test.tsx apps/web/src/index.css
git commit -m "feat(error): add PageErrorBoundary with inline page-level fallback"
```

---

### Task 2: HomeLayout component

**Files:**
- Create: `apps/web/src/HomeLayout.tsx`

**Interfaces:**
- Consumes: `HomeHeader` (no props), `BackendLoading` (`retrying: boolean`), `Outlet` from `react-router-dom`, `Suspense` from `react`.
- Produces: `export function HomeLayout(): ReactNode` — layout route shell that renders the nav above lazy-loaded page content.

No dedicated test: `HomeHeader` makes real API calls (org switcher), making it expensive to unit test. Behaviour is verified end-to-end in Task 3 via TypeScript compilation and manual smoke-test.

- [ ] **Step 1: Create HomeLayout.tsx**

Create `apps/web/src/HomeLayout.tsx`:

```tsx
import { Suspense } from "react";
import { Outlet } from "react-router-dom";

import { BackendLoading } from "./BackendLoading";
import { HomeHeader } from "./HomeHeader";

export function HomeLayout() {
  return (
    <>
      <HomeHeader />
      <Suspense fallback={<BackendLoading retrying={false} />}>
        <Outlet />
      </Suspense>
    </>
  );
}
```

- [ ] **Step 2: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: no errors for the new file (it may surface pre-existing errors elsewhere — only fail if there are new errors in `HomeLayout.tsx`).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/HomeLayout.tsx
git commit -m "feat(layout): add HomeLayout shell for shared nav + Suspense"
```

---

### Task 3: Rewire App.tsx routes + remove HomeHeader from 11 pages

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/WorkflowsPage.tsx` (line 7 import, line 399 JSX)
- Modify: `apps/web/src/EnvironmentsPage.tsx` (line 5 import, line 887 JSX)
- Modify: `apps/web/src/CodeLibraryPage.tsx` (line 5 import, line 134 JSX)
- Modify: `apps/web/src/DeploymentsPage.tsx` (line 7 import, line 234 JSX)
- Modify: `apps/web/src/ExecutionsPage.tsx` (line 12 import, line 369 JSX)
- Modify: `apps/web/src/CredentialsPage.tsx` (line 16 import, line 720 JSX)
- Modify: `apps/web/src/ActivityPage.tsx` (line 4 import, line 85 JSX)
- Modify: `apps/web/src/RunnerPoolsPage.tsx` (line 5 import, line 1316 JSX)
- Modify: `apps/web/src/SecurityPage.tsx` (line 6 import, line 115 JSX)
- Modify: `apps/web/src/OrganizationPage.tsx` (line 5 import, line 163 JSX)
- Modify: `apps/web/src/SettingsPage.tsx` (line 6 import, line 355 JSX)

**IMPORTANT:** Make ALL the changes below before running any tests or committing. Applying App.tsx alone (before removing HomeHeader from pages) would render a double header in the app.

**Interfaces:**
- Consumes: `PageErrorBoundary` (Task 1), `HomeLayout` (Task 2).

- [ ] **Step 1: Update App.tsx imports**

In `apps/web/src/App.tsx`, add two imports after the existing `ErrorBoundary` import:

```tsx
import { HomeLayout } from "./HomeLayout";
import { PageErrorBoundary } from "./PageErrorBoundary";
```

- [ ] **Step 2: Replace the Routes block in App.tsx**

Find the block that starts with `<ErrorBoundary resetKey={location.pathname}>` and ends with the closing `</ErrorBoundary>` that wraps `<Suspense><Routes>...</Routes></Suspense>` (roughly lines 189–208 in the current file). Replace the entire `<ErrorBoundary>...<Suspense>...<Routes>...</Routes></Suspense>...</ErrorBoundary>` block with:

```tsx
<Routes>
  <Route element={<HomeLayout />}>
    <Route path="/" element={<PageErrorBoundary><WorkflowsPage /></PageErrorBoundary>} />
    <Route path="/environments" element={<PageErrorBoundary><EnvironmentsPage /></PageErrorBoundary>} />
    <Route path="/code-library" element={<PageErrorBoundary><CodeLibraryPage /></PageErrorBoundary>} />
    <Route path="/deployments" element={<PageErrorBoundary><DeploymentsPage /></PageErrorBoundary>} />
    <Route path="/executions" element={<PageErrorBoundary><ExecutionsPage /></PageErrorBoundary>} />
    <Route path="/credentials" element={<PageErrorBoundary><CredentialsPage /></PageErrorBoundary>} />
    <Route path="/activity" element={<PageErrorBoundary><ActivityPage /></PageErrorBoundary>} />
    <Route path="/runner-pools" element={<PageErrorBoundary><RunnerPoolsPage /></PageErrorBoundary>} />
    <Route path="/security" element={<PageErrorBoundary><SecurityPage /></PageErrorBoundary>} />
    <Route path="/organization" element={<PageErrorBoundary><OrganizationPage /></PageErrorBoundary>} />
    <Route path="/settings" element={<PageErrorBoundary><SettingsPage /></PageErrorBoundary>} />
    <Route path="*" element={<NotFound />} />
  </Route>
  <Route
    path="/workflows/:id"
    element={
      <Suspense fallback={<BackendLoading retrying={false} />}>
        <ErrorBoundary resetKey={location.pathname}>
          <EditorPage />
        </ErrorBoundary>
      </Suspense>
    }
  />
</Routes>
```

The `BackendLoading` import is already in `App.tsx`. The `Suspense` import is already at the top (`import { lazy, Suspense, ... }`). Verify both are present; do not add duplicates.

- [ ] **Step 3: Remove HomeHeader from WorkflowsPage.tsx**

Delete line 7 (`import { HomeHeader } from "./HomeHeader";`) and line 399 (`<HomeHeader />`). Verify the JSX surrounding line 399 still makes sense — the `<HomeHeader />` is typically the first element in the component's return fragment. After removal the next sibling element becomes the first.

- [ ] **Step 4: Remove HomeHeader from the remaining 10 pages**

For each file below, remove the import line and the `<HomeHeader />` JSX line:

| File | Import line | JSX line |
|---|---|---|
| `EnvironmentsPage.tsx` | 5 | 887 |
| `CodeLibraryPage.tsx` | 5 | 134 |
| `DeploymentsPage.tsx` | 7 | 234 |
| `ExecutionsPage.tsx` | 12 | 369 |
| `CredentialsPage.tsx` | 16 | 720 |
| `ActivityPage.tsx` | 4 | 85 |
| `RunnerPoolsPage.tsx` | 5 | 1316 |
| `SecurityPage.tsx` | 6 | 115 |
| `OrganizationPage.tsx` | 5 | 163 |
| `SettingsPage.tsx` | 6 | 355 |

The `<HomeHeader />` element in each page is always a standalone line within a JSX fragment or container — removing it does not require any surrounding adjustment.

**Note on line numbers:** These are accurate at plan-writing time. If the file has been edited since, search for `HomeHeader` within each file and remove both occurrences (import and JSX).

- [ ] **Step 5: Type-check the full workspace**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors. If `HomeHeader` was mistakenly left in any file it will appear as "imported but never used" (TypeScript strict mode) or a dangling JSX element.

- [ ] **Step 6: Run the full test suite**

```
cd apps/web && pnpm test
```

Expected: all tests pass.

- [ ] **Step 7: Smoke-test in the browser**

Start the dev server:
```
cd apps/web && pnpm dev
```

Verify:
1. `http://localhost:5173/` — nav header visible, Workflows page renders below it.
2. Navigate to `/deployments`, `/credentials`, `/settings` — nav stays consistent, no double header on any page.
3. Navigate to `/workflows/<any-id>` (editor) — editor page opens correctly with no nav header above it.
4. Navigate to `/nonexistent` — 404 page shows WITH nav header.
5. In browser console, confirm no React warnings about duplicate keys or missing Router context.

- [ ] **Step 8: Commit all changes atomically**

```bash
git add apps/web/src/App.tsx \
        apps/web/src/WorkflowsPage.tsx \
        apps/web/src/EnvironmentsPage.tsx \
        apps/web/src/CodeLibraryPage.tsx \
        apps/web/src/DeploymentsPage.tsx \
        apps/web/src/ExecutionsPage.tsx \
        apps/web/src/CredentialsPage.tsx \
        apps/web/src/ActivityPage.tsx \
        apps/web/src/RunnerPoolsPage.tsx \
        apps/web/src/SecurityPage.tsx \
        apps/web/src/OrganizationPage.tsx \
        apps/web/src/SettingsPage.tsx
git commit -m "feat(layout): hoist HomeHeader into HomeLayout shell, add per-route PageErrorBoundary"
```
