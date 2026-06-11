import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

const AUTH_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000, 60000];
const AUTH_MAX_RETRIES = AUTH_RETRY_DELAYS_MS.length;
import { QueryClientProvider } from "@tanstack/react-query";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import { api, apiLogout, onUnauthorized, setToken, setUser } from "./api";
import { NO_AUTH_FALLBACK, shouldRetryAuthError } from "./authBootstrap";
import { BackendLoading } from "./BackendLoading";
import { ErrorBoundary } from "./ErrorBoundary";
// AppAssistant is temporarily unmounted from the UI (see Routes below) but kept
// in the codebase for continued iteration.
// import { AppAssistant } from "./AppAssistant";
import { ConfirmProvider } from "./ConfirmProvider";
import { LoginPage } from "./LoginPage";
import { queryClient } from "./queries";
import { ToastProvider } from "./ToastProvider";
import type { AuthState, UserInfo } from "./types";

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
      setUser(null);
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
    setAuth((current) => ({
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
    setToken(null);
    setUser(null);
    setAuth((current) =>
      current ? { ...current, signed_in: false, user: null } : current,
    );
    api
      .authRequired()
      .then(setAuth)
      .catch(() => setAuth(NO_AUTH_FALLBACK));
  }, []);

  useEffect(() => {
    if (auth?.user) setUser(auth.user);
  }, [auth?.user]);

  useEffect(() => {
    // Surface signOut to HomeHeader without prop-drilling through route pages.
    const target = window as unknown as { __noodle_sign_out?: () => void };
    target.__noodle_sign_out = signOut;
    return () => {
      if (target.__noodle_sign_out === signOut) {
        delete target.__noodle_sign_out;
      }
    };
  }, [signOut]);

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
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ConfirmProvider>
          <ErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<BackendLoading retrying={false} />}>
              <Routes>
                <Route path="/" element={<WorkflowsPage />} />
                <Route path="/environments" element={<EnvironmentsPage />} />
                <Route path="/code-library" element={<CodeLibraryPage />} />
                <Route path="/deployments" element={<DeploymentsPage />} />
                <Route path="/executions" element={<ExecutionsPage />} />
                <Route path="/credentials" element={<CredentialsPage />} />
                <Route path="/activity" element={<ActivityPage />} />
                <Route path="/runner-pools" element={<RunnerPoolsPage />} />
                <Route path="/security" element={<SecurityPage />} />
                <Route path="/organization" element={<OrganizationPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/workflows/:id" element={<EditorPage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
          {/* App-wide AI assistant (floating dock). Temporarily disabled in the UI
              while it is iterated on — the component and its backend wiring remain
              in the codebase (apps/web/src/AppAssistant.tsx). To re-enable, restore:
              {location.pathname.startsWith("/workflows/") ? null : <AppAssistant />} */}
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
