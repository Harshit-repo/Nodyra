import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";

import { api, getUser, onUnauthorized, setToken, setUser } from "./api";
import { ActivityPage } from "./ActivityPage";
import { CredentialsPage } from "./CredentialsPage";
import { DeploymentsPage } from "./DeploymentsPage";
import { EditorPage } from "./EditorPage";
import { EnvironmentsPage } from "./EnvironmentsPage";
import { ExecutionsPage } from "./ExecutionsPage";
import { LoginPage } from "./LoginPage";
import { WorkflowsPage } from "./WorkflowsPage";
import type { AuthState, UserInfo } from "./types";

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);

  useEffect(() => {
    api
      .authRequired()
      .then(setAuth)
      .catch(() =>
        setAuth({ auth_required: false, signed_in: false, user: null }),
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
    setAuth({ auth_required: true, signed_in: true, user });
  }

  function signOut(): void {
    setToken(null);
    setUser(null);
    setAuth((current) =>
      current ? { ...current, signed_in: false, user: null } : current,
    );
  }

  if (auth === null) {
    return <div className="screen-center muted">Loading…</div>;
  }
  if (auth.auth_required && !auth.signed_in) {
    return <LoginPage onSignedIn={onSignedIn} />;
  }

  // Surface signOut to HomeHeader via a global event the header can listen for.
  // Avoids prop-drilling through the router pages.
  (window as unknown as { __noodle_sign_out?: () => void }).__noodle_sign_out =
    signOut;
  // Keep the stored user fresh for the header on first load.
  if (auth.user && !getUser()) setUser(auth.user);

  return (
    <Routes>
      <Route path="/" element={<WorkflowsPage />} />
      <Route path="/environments" element={<EnvironmentsPage />} />
      <Route path="/deployments" element={<DeploymentsPage />} />
      <Route path="/executions" element={<ExecutionsPage />} />
      <Route path="/credentials" element={<CredentialsPage />} />
      <Route path="/activity" element={<ActivityPage />} />
      <Route path="/workflows/:id" element={<EditorPage />} />
    </Routes>
  );
}
