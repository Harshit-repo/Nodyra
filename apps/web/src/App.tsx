import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";

import { api, onUnauthorized, setToken, setUser } from "./api";
import { ActivityPage } from "./ActivityPage";
import { CodeLibraryPage } from "./CodeLibraryPage";
import { CredentialsPage } from "./CredentialsPage";
import { DeploymentsPage } from "./DeploymentsPage";
import { EditorPage } from "./EditorPage";
import { EnvironmentsPage } from "./EnvironmentsPage";
import { ExecutionsPage } from "./ExecutionsPage";
import { LoginPage } from "./LoginPage";
import { SecurityPage } from "./SecurityPage";
import { SettingsPage } from "./SettingsPage";
import { ToastProvider } from "./ToastProvider";
import { WorkflowsPage } from "./WorkflowsPage";
import type { AuthState, UserInfo } from "./types";

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);

  useEffect(() => {
    api
      .authRequired()
      .then(setAuth)
      .catch(() =>
        setAuth({
          auth_required: false,
          signed_in: false,
          registration_open: false,
          user: null,
        }),
      );

    onUnauthorized(() => {
      setUser(null);
      setAuth((current) =>
        current ? { ...current, signed_in: false, user: null } : current,
      );
    });
    return () => onUnauthorized(null);
  }, []);

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
      .catch(() =>
        setAuth({
          auth_required: false,
          signed_in: false,
          registration_open: false,
          user: null,
        }),
      );
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

  if (auth === null) {
    return <div className="screen-center muted">Loading…</div>;
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
        <Route path="/security" element={<SecurityPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/workflows/:id" element={<EditorPage />} />
      </Routes>
    </ToastProvider>
  );
}
