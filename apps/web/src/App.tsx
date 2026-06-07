import { useCallback, useEffect, useRef, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { api, onUnauthorized, setToken, setUser } from "./api";
import { NO_AUTH_FALLBACK, shouldRetryAuthError } from "./authBootstrap";
import { BackendLoading } from "./BackendLoading";
import { ActivityPage } from "./ActivityPage";
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

  // Bootstrap the auth state, retrying while the backend is still starting
  // (network error / 5xx) instead of falling through to render against a dead
  // API — which previously left the whole app on a black screen.
  const loadAuth = useCallback(() => {
    api
      .authRequired()
      .then((state) => {
        setAuth(state);
        setApiReachable(true);
      })
      .catch((err: unknown) => {
        if (shouldRetryAuthError(err)) {
          setApiReachable(false);
          retryRef.current = window.setTimeout(loadAuth, 2000);
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

  function signOut(): void {
    setToken(null);
    setUser(null);
    setAuth((current) =>
      current ? { ...current, signed_in: false, user: null } : current,
    );
    api
      .authRequired()
      .then(setAuth)
      .catch(() => setAuth(NO_AUTH_FALLBACK));
  }

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
  });

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
    </ToastProvider>
  );
}
