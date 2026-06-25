import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

const AUTH_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000, 60000];
const AUTH_MAX_RETRIES = AUTH_RETRY_DELAYS_MS.length;
import { QueryClientProvider } from "@tanstack/react-query";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import { api, apiLogout, getUser, onUnauthorized, setUser } from "./api";
import { AuthRuntimeProvider } from "./AuthRuntime";
import { NO_AUTH_FALLBACK, shouldRetryAuthError } from "./authBootstrap";
import { BackendLoading } from "./BackendLoading";
import { ErrorBoundary } from "./ErrorBoundary";
import { HomeLayout } from "./HomeLayout";
import { PageErrorBoundary } from "./PageErrorBoundary";
// AppAssistant is temporarily unmounted from the UI (see Routes below) but kept
// in the codebase for continued iteration.
// import { AppAssistant } from "./AppAssistant";
import { ConfirmProvider } from "./ConfirmProvider";
import { LoginPage } from "./LoginPage";
import { queryClient } from "./queries";
import { ToastProvider } from "./ToastProvider";
import type { AuthState, UserInfo } from "./types";
import { EntitlementsProvider } from "./entitlements";
import { WorkspaceAccessProvider } from "./WorkspaceAccess";
import { clearClientDataScope, clearClientSession } from "./sessionIsolation";

// Route pages are code-split so the initial bundle doesn't carry the editor
// (React Flow + Plotly) and every admin page. `named` adapts our named exports
// to the default-export shape `lazy()` expects.
function named<T extends Record<string, unknown>, K extends keyof T>(
  loader: () => Promise<T>,
  key: K,
): () => Promise<{ default: T[K] }> {
  return () => loader().then((m) => ({ default: m[key] }));
}

const ActivityPage = lazy(named(() => import("./ActivityPage"), "ActivityPage"));
const ChatPublicPage = lazy(named(() => import("./ChatPublicPage"), "ChatPublicPage"));
const CodeLibraryPage = lazy(named(() => import("./CodeLibraryPage"), "CodeLibraryPage"));
const CredentialsPage = lazy(named(() => import("./CredentialsPage"), "CredentialsPage"));
const DeploymentsPage = lazy(named(() => import("./DeploymentsPage"), "DeploymentsPage"));
const EditorPage = lazy(named(() => import("./EditorPage"), "EditorPage"));
const EnvironmentsPage = lazy(named(() => import("./EnvironmentsPage"), "EnvironmentsPage"));
const ExecutionsPage = lazy(named(() => import("./ExecutionsPage"), "ExecutionsPage"));
const RunnerPoolsPage = lazy(named(() => import("./RunnerPoolsPage"), "RunnerPoolsPage"));
const SecurityPage = lazy(named(() => import("./SecurityPage"), "SecurityPage"));
const OrganizationPage = lazy(
  named(() => import("./OrganizationPage"), "OrganizationPage"),
);
const SettingsPage = lazy(named(() => import("./SettingsPage"), "SettingsPage"));
const WorkflowsPage = lazy(named(() => import("./WorkflowsPage"), "WorkflowsPage"));

// Catch-all for unknown URLs. Without this, an unmatched path renders an empty
// <Routes> — a blank screen with no way back (the "black screen" class FE-9).
function NotFound() {
  return (
    <div className="screen-center">
      <h2>Page not found</h2>
      <p className="muted">That page doesn’t exist or may have moved.</p>
      <Link className="btn" to="/">
        Back to workflows
      </Link>
    </div>
  );
}

export default function App() {
  const location = useLocation();
  const [auth, setAuth] = useState<AuthState | null>(null);
  // false once we've hit a transient failure and are waiting for the API to
  // come up; flips back true the moment a bootstrap attempt succeeds.
  const [apiReachable, setApiReachable] = useState(true);
  const retryRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);

  // Bootstrap the auth state, retrying with exponential backoff while the backend
  // is still starting (network error / 5xx). Gives up after AUTH_MAX_RETRIES to
  // prevent infinite request storms on persistent server failures.
  const loadAuth = useCallback(() => {
    api
      .authRequired()
      .then((state) => {
        retryCountRef.current = 0;
        setAuth(state);
        setApiReachable(true);
      })
      .catch((err: unknown) => {
        if (shouldRetryAuthError(err) && retryCountRef.current < AUTH_MAX_RETRIES) {
          setApiReachable(false);
          const delay = AUTH_RETRY_DELAYS_MS[retryCountRef.current] ?? 60000;
          retryCountRef.current += 1;
          retryRef.current = window.setTimeout(loadAuth, delay);
        } else {
          setAuth(NO_AUTH_FALLBACK);
          setApiReachable(true);
        }
      });
  }, []);

  useEffect(() => {
    loadAuth();
    onUnauthorized(() => {
      clearClientSession();
      setAuth((current) =>
        current ? { ...current, signed_in: false, user: null } : current,
      );
    });
    return () => {
      onUnauthorized(null);
      if (retryRef.current) window.clearTimeout(retryRef.current);
    };
  }, [loadAuth]);

  function onSignedIn(user: UserInfo): void {
    clearClientDataScope();
    setAuth((current) => ({
      ...current,
      auth_required: true,
      signed_in: true,
      registration_open: current?.registration_open ?? false,
      multi_tenancy: current?.multi_tenancy ?? false,
      user,
    }));
  }

  const signOut = useCallback((): void => {
    // Fire-and-forget: clears server httpOnly cookie; local state cleared
    // synchronously below so the UI transitions immediately.
    void apiLogout();
    clearClientSession();
    setAuth((current) =>
      current ? { ...current, signed_in: false, user: null } : current,
    );
    api
      .authRequired()
      .then(setAuth)
      .catch(() => setAuth(NO_AUTH_FALLBACK));
  }, []);

  useEffect(() => {
    if (!auth?.user) return;
    const cachedUser = getUser();
    if (cachedUser && cachedUser.id !== auth.user.id) clearClientDataScope();
    setUser(auth.user);
  }, [auth?.user]);

  useEffect(() => {
    function refreshIdentity(): void {
      void api.authRequired().then(setAuth).catch(() => undefined);
    }
    window.addEventListener("focus", refreshIdentity);
    return () => window.removeEventListener("focus", refreshIdentity);
  }, []);


  // Chat pages are outside the auth gate: ChatPublicPage manages its own
  // login check based on the workflow's require_login param. (Placed after all
  // hooks so hook order stays stable across renders.)
  if (location.pathname.startsWith("/chat/")) {
    return (
      <ErrorBoundary resetKey={location.pathname}>
        <Suspense fallback={<BackendLoading retrying={false} />}>
          <Routes>
            <Route path="/chat/:workflowId" element={<ChatPublicPage />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
    );
  }

  // Still bootstrapping (or the backend isn't up yet): hold on a loader rather
  // than rendering the app against an unready API.
  if (auth === null) {
    return <BackendLoading retrying={!apiReachable} />;
  }
  if (auth.auth_required && !auth.signed_in) {
    return (
      <LoginPage
        registrationOpen={auth.registration_open}
        onSignedIn={onSignedIn}
      />
    );
  }

  return (
    <AuthRuntimeProvider auth={auth} signOut={signOut}>
      <QueryClientProvider client={queryClient}>
        <WorkspaceAccessProvider>
          <ToastProvider>
            <ConfirmProvider>
              <EntitlementsProvider auth={auth}>
          {auth.license_notice && (
            <div className="license-banner" role="status">
              {auth.license_notice}
            </div>
          )}
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
              <Route path="*" element={<PageErrorBoundary><NotFound /></PageErrorBoundary>} />
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
              </EntitlementsProvider>
          {/* App-wide AI assistant (floating dock). Temporarily disabled in the UI
              while it is iterated on — the component and its backend wiring remain
              in the codebase (apps/web/src/AppAssistant.tsx). To re-enable, restore:
              {location.pathname.startsWith("/workflows/") ? null : <AppAssistant />} */}
            </ConfirmProvider>
          </ToastProvider>
        </WorkspaceAccessProvider>
      </QueryClientProvider>
    </AuthRuntimeProvider>
  );
}
