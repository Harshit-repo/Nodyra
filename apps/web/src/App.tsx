import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

const AUTH_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000, 60000];
import { QueryClientProvider } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

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
import { FirstRunWizard } from "./FirstRunWizard";
import { LoginPage } from "./LoginPage";
import { queryClient } from "./queries";
import { ToastProvider } from "./ToastProvider";
import type { AuthState, UserInfo } from "./types";
import { EntitlementsProvider } from "./entitlements";
import { WorkspaceAccessProvider } from "./WorkspaceAccess";
import { clearClientDataScope, clearClientSession } from "./sessionIsolation";
import { t } from "./i18n";

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
const ArtifactsPage = lazy(named(() => import("./ArtifactsPage"), "ArtifactsPage"));
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
const McpConnectionsPage = lazy(
  named(() => import("./settings/McpConnectionsPage"), "McpConnectionsPage"),
);
const NodeRegistryPage = lazy(
  named(() => import("./settings/NodeRegistryPage"), "NodeRegistryPage"),
);
const KMSSettingsPage = lazy(
  named(() => import("./settings/KMSSettingsPage"), "KMSSettingsPage"),
);
const SettingsPage = lazy(named(() => import("./SettingsPage"), "SettingsPage"));
const RolesPage = lazy(
  named(() => import("./settings/RolesPage"), "RolesPage"),
);
const AuditLogPage = lazy(
  named(() => import("./settings/AuditLogPage"), "AuditLogPage"),
);
const SSOSettingsPage = lazy(
  named(() => import("./settings/SSOSettingsPage"), "SSOSettingsPage"),
);
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

function EditorViewportGate({ children }: { children: ReactNode }) {
  const [phoneViewport, setPhoneViewport] = useState(() =>
    window.matchMedia("(max-width: 640px)").matches,
  );
  const [fullEditor, setFullEditor] = useState(() =>
    new URLSearchParams(window.location.search).get("full") === "1",
  );

  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const onChange = (event: MediaQueryListEvent) => setPhoneViewport(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  if (!phoneViewport || fullEditor) return children;
  return (
    <main className="mobile-editor-gate">
      <div>
        <span>{t("mobile.mode")}</span>
        <h1>{t("mobile.title")}</h1>
        <p>{t("mobile.description")}</p>
        <div className="mobile-editor-gate-actions">
          <Link className="btn btn-primary" to="/executions">{t("mobile.executions")}</Link>
          <Link className="btn" to="/">{t("mobile.workflows")}</Link>
          <button className="btn btn-ghost" type="button" onClick={() => setFullEditor(true)}>
            {t("mobile.editorAnyway")}
          </button>
        </div>
      </div>
    </main>
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
  // Ignore identity requests started before a login/logout transition. Focus
  // refreshes must never resurrect a session the user has just signed out of.
  const authRevisionRef = useRef(0);
  const signedOutRef = useRef(false);

  // Bootstrap auth with capped exponential backoff. Only a genuine legacy 404
  // may fall back to no-auth; every other failure stays fail-closed and keeps
  // probing at most once per minute so recovery needs no page reload.
  const loadAuth = useCallback(() => {
    const revision = authRevisionRef.current;
    api
      .authRequired()
      .then((state) => {
        if (revision !== authRevisionRef.current || signedOutRef.current) return;
        retryCountRef.current = 0;
        setAuth(state);
        setApiReachable(true);
      })
      .catch((err: unknown) => {
        if (revision !== authRevisionRef.current || signedOutRef.current) return;
        if (shouldRetryAuthError(err)) {
          setApiReachable(false);
          setAuth(null);
          const retryIndex = Math.min(
            retryCountRef.current,
            AUTH_RETRY_DELAYS_MS.length - 1,
          );
          const delay = AUTH_RETRY_DELAYS_MS[retryIndex] ?? 60000;
          retryCountRef.current = retryIndex + 1;
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
      authRevisionRef.current += 1;
      clearClientSession();
      setAuth((current) =>
        current ? { ...current, signed_in: false, user: null } : current,
      );
    });
    return () => {
      authRevisionRef.current += 1;
      onUnauthorized(null);
      if (retryRef.current) window.clearTimeout(retryRef.current);
    };
  }, [loadAuth]);

  function onSignedIn(user: UserInfo): void {
    authRevisionRef.current += 1;
    signedOutRef.current = false;
    if (retryRef.current) window.clearTimeout(retryRef.current);
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
    signedOutRef.current = true;
    const revision = ++authRevisionRef.current;
    if (retryRef.current) window.clearTimeout(retryRef.current);
    // Capture the current credentials before clearing the client, and finish
    // revoking the cookie before asking the server for the new auth state.
    const logout = apiLogout();
    clearClientSession();
    setAuth((current) =>
      current ? { ...current, signed_in: false, user: null } : current,
    );
    void logout
      .then(() => api.authRequired())
      .then((state) => {
        if (revision !== authRevisionRef.current) return;
        setAuth({ ...state, signed_in: false, user: null });
      })
      .catch(() => {
        // Keep the sign-in screen during an outage. Retrying identity here
        // could restore a cookie that the unreachable server has not revoked.
      });
  }, []);

  useEffect(() => {
    if (!auth?.user) return;
    const cachedUser = getUser();
    if (cachedUser && cachedUser.id !== auth.user.id) clearClientDataScope();
    setUser(auth.user);
  }, [auth?.user]);

  useEffect(() => {
    function refreshIdentity(): void {
      if (signedOutRef.current) return;
      const revision = authRevisionRef.current;
      void api.authRequired().then((state) => {
        if (revision === authRevisionRef.current && !signedOutRef.current) setAuth(state);
      }).catch(() => undefined);
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
            <Route path="*" element={<NotFound />} />
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
          <FirstRunWizard auth={auth} />
          <Routes>
            <Route element={<HomeLayout />}>
              <Route path="/" element={<PageErrorBoundary><WorkflowsPage /></PageErrorBoundary>} />
              <Route path="/environments" element={<PageErrorBoundary><EnvironmentsPage /></PageErrorBoundary>} />
              <Route path="/code-library" element={<PageErrorBoundary><CodeLibraryPage /></PageErrorBoundary>} />
              <Route path="/deployments" element={<PageErrorBoundary><DeploymentsPage /></PageErrorBoundary>} />
              <Route path="/executions" element={<PageErrorBoundary><ExecutionsPage /></PageErrorBoundary>} />
              <Route path="/artifacts" element={<PageErrorBoundary><ArtifactsPage /></PageErrorBoundary>} />
              <Route path="/credentials" element={<PageErrorBoundary><CredentialsPage /></PageErrorBoundary>} />
              <Route path="/activity" element={<PageErrorBoundary><ActivityPage /></PageErrorBoundary>} />
              <Route path="/runner-pools" element={<PageErrorBoundary><RunnerPoolsPage /></PageErrorBoundary>} />
              <Route path="/security" element={<PageErrorBoundary><SecurityPage /></PageErrorBoundary>} />
              <Route path="/organization" element={<PageErrorBoundary><OrganizationPage /></PageErrorBoundary>} />
              <Route path="/settings/roles" element={<PageErrorBoundary><RolesPage /></PageErrorBoundary>} />
              <Route path="/settings/audit-log" element={<PageErrorBoundary><AuditLogPage /></PageErrorBoundary>} />
              <Route path="/settings/mcp-connections" element={<PageErrorBoundary><McpConnectionsPage /></PageErrorBoundary>} />
              <Route path="/settings/node-registry" element={<PageErrorBoundary><NodeRegistryPage /></PageErrorBoundary>} />
              <Route path="/settings/kms" element={<PageErrorBoundary><KMSSettingsPage /></PageErrorBoundary>} />
              <Route path="/settings/sso" element={<PageErrorBoundary><SSOSettingsPage /></PageErrorBoundary>} />
              <Route path="/settings" element={<PageErrorBoundary><SettingsPage /></PageErrorBoundary>} />
              <Route path="/workflows" element={<Navigate to="/" replace />} />
              <Route path="*" element={<PageErrorBoundary><NotFound /></PageErrorBoundary>} />
            </Route>
            <Route
              path="/workflows/:id"
              element={
                <Suspense fallback={<BackendLoading retrying={false} />}>
                  <ErrorBoundary resetKey={location.pathname}>
                    <EditorViewportGate>
                      <EditorPage />
                    </EditorViewportGate>
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
