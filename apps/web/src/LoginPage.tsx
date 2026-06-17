import { useEffect, useState } from "react";

import { api, setUser } from "./api";
import { Logo } from "./Logo";
import type { UserInfo } from "./types";

export function LoginPage({
  registrationOpen,
  onSignedIn,
}: {
  registrationOpen: boolean;
  onSignedIn: (user: UserInfo) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">(
    registrationOpen ? "register" : "login",
  );
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setMode(registrationOpen ? "register" : "login");
  }, [registrationOpen]);

  const canSubmit =
    !busy &&
    Boolean(email.trim()) &&
    password.length >= 8 &&
    (mode === "login" || (Boolean(name.trim()) && Boolean(company.trim())));

  async function submit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    setError("");
    try {
      const result =
        mode === "login"
          ? await api.login(email.trim(), password)
          : await api.register({
              name: name.trim(),
              company: company.trim(),
              email: email.trim(),
              password,
            });
      // FE-1: do NOT persist the bearer token in localStorage. /auth/login sets
      // an httpOnly ``noodle_session`` cookie (plus the readable ``noodle_csrf``
      // double-submit cookie), which the client uses automatically — the session
      // credential is never reachable by JavaScript, so an XSS can't exfiltrate
      // it. We keep only the (non-secret) user profile for UI state.
      setUser(result.user);
      onSignedIn(result.user);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <section className="login-panel">
        <div className="login-brand login-brand-large">
          <Logo size={42} />
          <span>noodle</span>
        </div>
        <h1>{mode === "login" ? "Welcome back" : "Set up your workspace"}</h1>
        <p>
          {mode === "login"
            ? "Sign in to manage workflows, credentials, executions, and runners."
            : "Create the workspace owner account. Owners have all admin rights."}
        </p>
        <div className="login-role-strip">
          <span>Owner/admin invites</span>
          <span>RBAC enforced</span>
          <span>Secrets protected</span>
        </div>
      </section>
      <div className="login-card">
        <div className="login-card-brand">
          <Logo size={36} />
          <span>noodle</span>
        </div>
        <h2>{mode === "login" ? "Sign in" : "Create owner account"}</h2>
        <p className="muted">
          {mode === "login"
            ? "Use your workspace account."
            : "Name, company, email, and password are required."}
        </p>
        {mode === "register" && (
          <>
            <input
              className="field-input"
              type="text"
              autoFocus
              placeholder="Full name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
            <input
              className="field-input"
              type="text"
              placeholder="Company"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
          </>
        )}
        <input
          className="field-input"
          type="email"
          autoFocus={mode === "login"}
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />
        <input
          className="field-input"
          type="password"
          placeholder="password (min 8 chars)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />
        {error && <p className="error-text">{error}</p>}
        <button
          className="btn btn-primary"
          disabled={!canSubmit}
          onClick={() => void submit()}
        >
          {busy
            ? "…"
            : mode === "login"
              ? "Sign in"
              : "Create owner"}
        </button>
        {registrationOpen ? (
          <button
            type="button"
            className="login-toggle"
            onClick={() =>
              setMode((m) => (m === "login" ? "register" : "login"))
            }
          >
            {mode === "login"
              ? "Need an account? Register"
              : "Already have an account? Sign in"}
          </button>
        ) : (
          <p className="login-closed">
            Registration is closed. Ask an owner or admin to invite you.
          </p>
        )}
      </div>
    </div>
  );
}
