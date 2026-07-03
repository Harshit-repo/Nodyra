import { Eye, EyeSlash } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { api, errorMessage, setUser } from "./api";
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
  const [showPassword, setShowPassword] = useState(false);
  const [ssoDetect, setSsoDetect] = useState<SSODetectResponse | null>(null);
  const [, setSsoChecking] = useState(false);
  const ssoTimerRef = useRef<number | null>(null);
  const isLoginMode = mode === "login";

  useEffect(() => {
    setMode(registrationOpen ? "register" : "login");
  }, [registrationOpen]);

  // SSO email domain detection: check when email changes (debounced)
  useEffect(() => {
    if (ssoTimerRef.current) window.clearTimeout(ssoTimerRef.current);
    setSsoDetect(null);
    if (!email.includes("@")) return;
    let cancelled = false;
    ssoTimerRef.current = window.setTimeout(async () => {
      setSsoChecking(true);
      try {
        const result = await api.detectSSO(email);
        if (!cancelled) setSsoDetect(result);
      } catch {
        if (!cancelled) setSsoDetect(null);
      } finally {
        if (!cancelled) setSsoChecking(false);
      }
    }, 500);
    return () => {
      if (ssoTimerRef.current) window.clearTimeout(ssoTimerRef.current);
      cancelled = true;
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
      // an httpOnly ``nodyra_session`` cookie (plus the readable ``nodyra_csrf``
      // double-submit cookie), which the client uses automatically — the session
      // credential is never reachable by JavaScript, so an XSS can't exfiltrate
      // it. We keep only the (non-secret) user profile for UI state.
      setUser(result.user);
      onSignedIn(result.user);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <header className="login-nav" aria-label="Nodyra sign-in">
        <div className="login-brand">
          <Logo size={30} />
          <span>Nodyra</span>
        </div>
        <div className="login-nav-meta" aria-label="Workspace security summary">
          <span>Self-hosted</span>
          <span>Python-native</span>
          <span>RBAC</span>
        </div>
      </header>

      <main className="login-hero">
        <section className="login-canvas" aria-label="Workflow canvas preview">
          <div className="login-prompt-card" aria-hidden="true">
            <span className="login-prompt-mark">&gt;&gt;</span>
            <span>{isLoginMode ? "Open production workspace" : "Create owner workspace"}</span>
            <span className="login-prompt-cursor" />
          </div>

          <div className="login-graph" aria-hidden="true">
            <span className="login-edge login-edge-a" />
            <span className="login-edge login-edge-b" />
            <span className="login-node login-node-auth">
              <b>Auth</b>
              <small>session</small>
            </span>
            <span className="login-node login-node-rbac">
              <b>RBAC</b>
              <small>roles</small>
            </span>
            <span className="login-node login-node-secrets">
              <b>Secrets</b>
              <small>protected</small>
            </span>
            <span className="login-node login-node-runner">
              <b>Runner</b>
              <small>isolated</small>
            </span>
          </div>

          <dl className="login-canvas-stats" aria-label="Access model summary">
            <div><dt>Session</dt><dd>httpOnly cookie</dd></div>
            <div><dt>Access</dt><dd>Owner/admin invites</dd></div>
            <div><dt>Runtime</dt><dd>Trusted Python authors</dd></div>
          </dl>
        </section>

        <section className="login-copy" aria-labelledby="login-heading">
          <p className="login-status-pill">
            <span aria-hidden="true" />
            {isLoginMode ? "Secure workspace access" : "First owner setup"}
          </p>
          <h1 id="login-heading">
            {isLoginMode
              ? "Sign in to the workflow control plane."
              : "Set up the workflow control plane."}
          </h1>
          <p className="login-subcopy">
            {isLoginMode
              ? "Continue to workflows, credentials, executions, environments, and runners in your self-hosted Nodyra workspace."
              : "Create the first owner account. This user controls invites, roles, credentials, and workspace settings."}
          </p>

          <div className="login-card">
            <div className="login-card-head">
              <h2>{isLoginMode ? "Workspace sign in" : "Create owner account"}</h2>
              <p className="muted">
                {isLoginMode
                  ? "Use your workspace credentials to continue."
                  : "Name, company, email, and password are required."}
              </p>
            </div>
            <form
              className="login-form"
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              {mode === "register" && (
                <>
                  <div className="login-field">
                    <label className="login-field-label" htmlFor="login-name">
                      Full name
                    </label>
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
                  </div>
                  <div className="login-field">
                    <label className="login-field-label" htmlFor="login-company">
                      Company
                    </label>
                    <input
                      id="login-company"
                      className="field-input"
                      type="text"
                      placeholder="Company"
                      autoComplete="organization"
                      value={company}
                      onChange={(e) => setCompany(e.target.value)}
                    />
                  </div>
                </>
              )}
              <div className="login-field">
                <label className="login-field-label" htmlFor="login-email">
                  Email
                </label>
                <input
                  id="login-email"
                  className="field-input"
                  type="email"
                  autoFocus={isLoginMode}
                  placeholder="you@example.com"
                  autoComplete="email"
                  spellCheck={false}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div className="login-field">
                <label className="login-field-label" htmlFor="login-password">
                  Password
                </label>
                <div className="login-password-wrap">
                  <input
                    id="login-password"
                    className="field-input"
                    type={showPassword ? "text" : "password"}
                    placeholder="Minimum 8 characters"
                    autoComplete={isLoginMode ? "current-password" : "new-password"}
                    aria-describedby="login-password-hint"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button
                    type="button"
                    className="login-password-toggle"
                    aria-label={showPassword ? "Hide secret" : "Show secret"}
                    aria-pressed={showPassword}
                    onClick={() => setShowPassword((value) => !value)}
                  >
                    {showPassword ? (
                      <EyeSlash size={18} aria-hidden="true" />
                    ) : (
                      <Eye size={18} aria-hidden="true" />
                    )}
                  </button>
                </div>
                <p id="login-password-hint" className="login-field-help">
                  Minimum 8 characters.
                </p>
              </div>
              {error && (
                <p id="login-error" className="error-text login-error" role="alert">
                  {error}
                </p>
              )}
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
                  ? isLoginMode ? "Signing in..." : "Creating..."
                  : isLoginMode ? "Sign in" : "Create owner"}
              </button>
              {isLoginMode && ssoDetect?.has_sso && (
                <div className="sso-login-section" aria-label="Single sign-on">
                  <div className="sso-divider">
                    <span>or</span>
                  </div>
                  <a
                    className="btn btn-secondary sso-login-btn"
                    href={api.ssoAuthorize(ssoDetect.org_slug!)}
                  >
                    Continue with SSO
                  </a>
                </div>
              )}
            </form>
            {registrationOpen ? (
              <button
                type="button"
                className="login-toggle"
                onClick={() => {
                  setError("");
                  setMode((m) => (m === "login" ? "register" : "login"));
                }}
              >
                {isLoginMode
                  ? "Need an account? Register"
                  : "Already have an account? Sign in"}
              </button>
            ) : (
              <p className="login-closed">
                Registration is invite-only. Ask an owner or admin to invite you.
              </p>
            )}
          </div>

          <div className="login-proof-row" aria-label="Platform traits">
            <span>Self-hostable</span>
            <span>Python-native</span>
            <span>MCP server + client</span>
            <span>Runner pools</span>
          </div>
        </section>
      </main>
    </div>
  );
}
