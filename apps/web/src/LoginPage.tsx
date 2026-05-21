import { useState } from "react";

import { api, setToken, setUser } from "./api";
import { Logo } from "./Logo";
import type { UserInfo } from "./types";

export function LoginPage({
  onSignedIn,
}: {
  onSignedIn: (user: UserInfo) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(): Promise<void> {
    if (busy || !email.trim() || !password) return;
    setBusy(true);
    setError("");
    try {
      const result =
        mode === "login"
          ? await api.login(email.trim(), password)
          : await api.register(email.trim(), password);
      setToken(result.token);
      setUser(result.user);
      onSignedIn(result.user);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <Logo size={36} />
          <span>noodle</span>
        </div>
        <h1>{mode === "login" ? "Sign in" : "Create an account"}</h1>
        <p className="muted">
          {mode === "login"
            ? "Welcome back."
            : "Your secret key encrypts credentials and signs this session."}
        </p>
        <input
          className="field-input"
          type="email"
          autoFocus
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
          disabled={busy}
          onClick={() => void submit()}
        >
          {busy
            ? "…"
            : mode === "login"
              ? "Sign in"
              : "Create account"}
        </button>
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
      </div>
    </div>
  );
}
