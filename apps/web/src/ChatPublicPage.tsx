import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { api, getToken, setToken } from "./api";
import { ChatPanel } from "./editor/ChatPanel";
import type { ChatPublicConfig, ChatTurnResponse } from "./types";

function ChatLoginGate({
  config,
  onLogin,
}: {
  config: ChatPublicConfig;
  onLogin: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.login(email.trim(), password);
      setToken(res.token);
      onLogin();
    } catch {
      setError("Invalid email or password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-public-page chat-login-gate">
      <div className="chat-login-box">
        <h2>{config.title || "Chat"}</h2>
        <p className="field-desc">Sign in to access this chat.</p>
        <form onSubmit={(e) => void handleSubmit(e)}>
          <input
            className="field-input"
            type="email"
            placeholder="Email"
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="field-input"
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && <p className="error-text">{error}</p>}
          <button
            type="submit"
            className="btn btn-sm"
            disabled={busy || !email.trim() || !password}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

export function ChatPublicPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const [searchParams] = useSearchParams();
  const urlToken = searchParams.get("token") ?? undefined;

  const [config, setConfig] = useState<ChatPublicConfig | null>(null);
  const [error, setError] = useState("");
  const [needsLogin, setNeedsLogin] = useState(false);
  const [loggedIn, setLoggedIn] = useState(() => getToken() !== null);

  useEffect(() => {
    if (!workflowId) return;
    api
      .getPublicChatConfig(workflowId)
      .then((cfg) => {
        setConfig(cfg);
        if (cfg.require_login && !loggedIn) {
          setNeedsLogin(true);
        }
      })
      .catch(() => setError("This chat is not available or has been disabled."));
  }, [workflowId]);

  if (!workflowId || error) {
    return (
      <div className="chat-public-error">
        <p>{error || "Invalid URL."}</p>
      </div>
    );
  }

  if (!config) {
    return <div className="chat-public-loading">Loading…</div>;
  }

  if (needsLogin && !loggedIn) {
    return (
      <ChatLoginGate
        config={config}
        onLogin={() => {
          setLoggedIn(true);
          setNeedsLogin(false);
        }}
      />
    );
  }

  function sendPublicMessage(
    message: string,
    sessionId: string,
  ): Promise<ChatTurnResponse> {
    return api.sendPublicChatMessage(workflowId!, message, sessionId, urlToken);
  }

  return (
    <div className="chat-public-page">
      <ChatPanel
        workflowId={workflowId}
        title={config.title || "Chat"}
        placeholder={config.placeholder || "Message…"}
        initialMessage={config.initial_message}
        onRun={() => {}}
        onClose={() => {}}
        sendMessage={sendPublicMessage}
      />
    </div>
  );
}
