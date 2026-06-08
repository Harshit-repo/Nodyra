import { useCallback, useEffect, useRef, useState } from "react";

const AUTH_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000, 60000];
const AUTH_MAX_RETRIES = AUTH_RETRY_DELAYS_MS.length;
import { Route, Routes, useLocation } from "react-router-dom";

import { api, onUnauthorized, setToken, setUser } from "./api";
import { NO_AUTH_FALLBACK, shouldRetryAuthError } from "./authBootstrap";
import { BackendLoading } from "./BackendLoading";
import { ActivityPage } from "./ActivityPage";
// AppAssistant is temporarily unmounted from the UI (see Routes below) but kept
// in the codebase for continued iteration.
// import { AppAssistant } from "./AppAssistant";
import { ChatPublicPage } from "./ChatPublicPage";
import { CodeLibraryPage } from "./CodeLibraryPage";
import { CredentialsPage } from "./CredentialsPage";
import { DeploymentsPage } from "./DeploymentsPage";
import { EditorPage } from "./EditorPage";
import { EnvironmentsPage } from "./EnvironmentsPage";
import { ExecutionsPage } from "./ExecutionsPage";
import { LoginPage } from "./LoginPage";
import { RunnerPoolsPage } from "./RunnerPoolsPage";
import { SecurityPage } from "./SecurityPage";
import { SettingsPage } from "./SettingsPage";
import { ToastProvider } from "./ToastProvider";
import { WorkflowsPage } from "./WorkflowsPage";
import type { AuthState, UserInfo } from "./types";

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
      user,
    }));
  }

  const signOut = useCallback((): void => {
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
      <Routes>
        <Route path="/chat/:workflowId" element={<ChatPublicPage />} />
      </Routes>
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
    <ToastProvider>
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
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/workflows/:id" element={<EditorPage />} />
      </Routes>
      {/* App-wide AI assistant (floating dock). Temporarily disabled in the UI
          while it is iterated on — the component and its backend wiring remain
          in the codebase (apps/web/src/AppAssistant.tsx). To re-enable, restore:
          {location.pathname.startsWith("/workflows/") ? null : <AppAssistant />} */}
    </ToastProvider>
  );
}
