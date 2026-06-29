import { useEffect, useRef, useState } from "react";

import { api, setUser } from "./api";
import { Logo } from "./Logo";
import type { SSODetectResponse, UserInfo } from "./types";

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
  const [ssoDetect, setSsoDetect] = useState<SSODetectResponse | null>(null);
  const [ssoChecking, setSsoChecking] = useState(false);
  const ssoTimerRef = useRef<number | null>(null);

  useEffect(() => {
    setMode(registrationOpen ? "register" : "login");
  }, [registrationOpen]);

  // SSO email domain detection: check when email changes (debounced)
  useEffect(() => {
    if (ssoTimerRef.current) window.clearTimeout(ssoTimerRef.current);
    setSsoDetect(null);
    if (!email.includes("@")) return;
    ssoTimerRef.current = window.setTimeout(async () => {
      setSsoChecking(true);
      try {
        const result = await api.detectSSO(email);
        setSsoDetect(result);
      } catch {
        setSsoDetect(null);
      } finally {
        setSsoChecking(false);
      }
    }, 500);
    return () => {
      if (ssoTimerRef.current) window.clearTimeout(ssoTimerRef.current);
    };
  }, [email]);

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
        <h2>{mode === "login" ? "Sign in" : "Create owner account"}</h2>
        <p className="muted">
          {mode === "login"
            ? "Use your workspace account."
            : "Name, company, email, and password are required."}
        </p>
        <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {mode === "register" && (
          <>
            <label className="login-field-label" htmlFor="login-name">Full name</label>
            <input
              id="login-name"
              className="field-input"
              type="text"
              autoFocus
              placeholder="Full name"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <label className="login-field-label" htmlFor="login-company">Company</label>
            <input
              id="login-company"
              className="field-input"
              type="text"
              placeholder="Company"
              autoComplete="organization"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </>
        )}
        <label className="login-field-label" htmlFor="login-email">Email</label>
        <input
          id="login-email"
          className="field-input"
          type="email"
          autoFocus={mode === "login"}
          placeholder="you@example.com"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <label className="login-field-label" htmlFor="login-password">Password</label>
        <input
          id="login-password"
          className="field-input"
          type="password"
          placeholder="min 8 characters"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="error-text" role="alert">{error}</p>}
        <button
          type="submit"
          className="btn btn-primary"
          disabled={!canSubmit}
          aria-busy={busy}
        >
          {busy ? (
            <span className="login-spinner" aria-hidden="true" />
          ) : null}
          {busy
            ? mode === "login" ? "Signing in…" : "Creating…"
            : mode === "login" ? "Sign in" : "Create owner"}
        </button>
        {/* SSO login */}
        {mode === "login" && ssoDetect?.has_sso && (
          <div className="sso-login-section">
            <div className="sso-divider">
              <span>or</span>
            </div>
            <a
              className="btn btn-outline sso-login-btn"
              href={api.ssoAuthorize(ssoDetect.org_slug!)}
            >
              Sign in with SSO
            </a>
          </div>
        )}
        </form>
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
