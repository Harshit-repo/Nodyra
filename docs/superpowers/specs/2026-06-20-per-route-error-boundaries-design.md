# Per-Route Error Boundaries — Design Spec

**Date:** 2026-06-20  
**Status:** Approved

## Problem

The app has a single `ErrorBoundary` wrapping all routes in `App.tsx`. When any page crashes the full-screen fallback blanks the entire viewport — nav, providers, everything — and the user's only escape is a hard reload. Per-route boundaries isolate crashes to the specific page so the nav remains usable and no reload is required.

## Solution

Extract `HomeHeader` into a shared `HomeLayout` route shell. Each non-editor page becomes a nested route child whose element is wrapped in a new `PageErrorBoundary` with an inline fallback. The editor keeps its own full-screen `ErrorBoundary`.

---

## Architecture

### New: `HomeLayout.tsx`

A React Router layout route component. Renders:

```
<HomeHeader />
<Suspense fallback={<BackendLoading retrying={false} />}>
  <Outlet />
</Suspense>
```

All non-editor, non-chat pages nest under this route in `App.tsx`'s `<Routes>`. This eliminates the current `HomeHeader`-in-every-page duplication.

### New: `PageErrorBoundary.tsx`

A class-based `ErrorBoundary` variant with an **inline** (non-fixed) fallback. No `resetKey` prop needed — per-route instances unmount/remount naturally on navigation.

Fallback renders:
- Phosphor `PlugsConnected` icon (~40 px, `--ink-2` color) — Noodle's own metaphor for a broken connection
- Headline: `"This page ran into a problem"`
- Body: `"The error is contained here — the rest of the app is unaffected."`
- Two actions: `"Try again"` (ghost button, clears boundary in-place) · `"Back to workflows"` (`<Link to="/">`, primary button)

### Changes to `App.tsx`

Remove the single `<ErrorBoundary><Suspense><Routes>` wrapper. Replace inner routes with:

```tsx
<Routes>
  {/* Layout shell — nav survives page crashes */}
  <Route element={<HomeLayout />}>
    <Route path="/"              element={<PageErrorBoundary><WorkflowsPage /></PageErrorBoundary>} />
    <Route path="/environments"  element={<PageErrorBoundary><EnvironmentsPage /></PageErrorBoundary>} />
    <Route path="/code-library"  element={<PageErrorBoundary><CodeLibraryPage /></PageErrorBoundary>} />
    <Route path="/deployments"   element={<PageErrorBoundary><DeploymentsPage /></PageErrorBoundary>} />
    <Route path="/executions"    element={<PageErrorBoundary><ExecutionsPage /></PageErrorBoundary>} />
    <Route path="/credentials"   element={<PageErrorBoundary><CredentialsPage /></PageErrorBoundary>} />
    <Route path="/activity"      element={<PageErrorBoundary><ActivityPage /></PageErrorBoundary>} />
    <Route path="/runner-pools"  element={<PageErrorBoundary><RunnerPoolsPage /></PageErrorBoundary>} />
    <Route path="/security"      element={<PageErrorBoundary><SecurityPage /></PageErrorBoundary>} />
    <Route path="/organization"  element={<PageErrorBoundary><OrganizationPage /></PageErrorBoundary>} />
    <Route path="/settings"      element={<PageErrorBoundary><SettingsPage /></PageErrorBoundary>} />
    <Route path="*"              element={<NotFound />} />
  </Route>

  {/* Editor — full-screen boundary; resetKey handles /workflows/A → /workflows/B */}
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

`ChatPublicPage` remains in its existing early-return block (before auth gate), unchanged.

### Remove `HomeHeader` from pages

The following pages currently render `<HomeHeader />` — remove that import and JSX from each:

- `WorkflowsPage`, `EnvironmentsPage`, `CodeLibraryPage`, `DeploymentsPage`, `ExecutionsPage`
- `CredentialsPage`, `ActivityPage`, `RunnerPoolsPage`, `SecurityPage`, `OrganizationPage`, `SettingsPage`

### CSS additions (`index.css`)

New `.page-error` class — inline variant of `.app-error`:

```css
.page-error {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 56px); /* account for HomeHeader height */
  padding: 24px;
}
```

Reuses existing `.app-error-card`, `.app-error-title`, `.app-error-text`, `.app-error-actions` — only the wrapper changes.

---

## Edge Cases

| Case | Resolution |
|---|---|
| **HomeHeader itself crashes** | Top-level `ErrorBoundary` in `main.tsx` catches it; full-screen fallback. Acceptable last resort for a stable chrome component. |
| **Lazy chunk fails to load** | React.lazy throws an `Error` after Promise rejection. `PageErrorBoundary` catches it and shows inline fallback. ✓ |
| **EditorPage: `/workflows/A` → `/workflows/B`** | Same route instance, same `ErrorBoundary` instance. `resetKey={location.pathname}` detects path change and clears error state. ✓ |
| **Same page crashes twice** | "Try again" clears boundary state, re-renders, boundary catches again, re-shows inline error. Nav above remains usable throughout. ✓ |
| **License banner** | Rendered above `<Routes>` in `App.tsx`, unaffected. ✓ |
| **ChatPublicPage** | Already in its own `ErrorBoundary` before the auth gate. No change. ✓ |
| **NotFound (`*` route)** | Nested inside `HomeLayout` — nav visible on 404. ✓ |
| **QueryClient / auth state** | Both providers live above all route boundaries. A page crash never evicts the query cache or resets auth. ✓ |
| **`<Link>` in class component** | `react-router-dom`'s `<Link>` works in class component `render()`. ✓ |
| **Double Suspense** | `HomeLayout`'s Suspense handles home pages; EditorPage gets an inline Suspense. No nested Suspense conflicts. ✓ |

---

## Files

| Action | File |
|---|---|
| Create | `apps/web/src/HomeLayout.tsx` |
| Create | `apps/web/src/PageErrorBoundary.tsx` |
| Create | `apps/web/src/PageErrorBoundary.test.tsx` |
| Modify | `apps/web/src/App.tsx` |
| Modify | `apps/web/src/index.css` |
| Modify (remove HomeHeader) | `apps/web/src/WorkflowsPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/EnvironmentsPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/CodeLibraryPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/DeploymentsPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/ExecutionsPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/CredentialsPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/ActivityPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/RunnerPoolsPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/SecurityPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/OrganizationPage.tsx` |
| Modify (remove HomeHeader) | `apps/web/src/SettingsPage.tsx` |

---

## Tests

- `PageErrorBoundary.test.tsx`: mirrors `ErrorBoundary.test.tsx` — children render normally; crash shows inline fallback with `role="alert"`; "Try again" clears the error; "Back to workflows" link present.
- `ErrorBoundary.test.tsx`: no changes (existing tests pass unmodified).
- `HomeLayout`: no dedicated test — layout tested implicitly via integration.
