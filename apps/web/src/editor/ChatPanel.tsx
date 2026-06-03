import { useState } from "react";

import { api } from "../api";

interface ChatMessage {
  role: "user" | "bot";
  text: string;
  runId?: string | null;
  error?: boolean;
}

interface ChatPanelProps {
  workflowId: string;
  title: string;
  placeholder: string;
  initialMessage: string;
  onRun: (runId: string) => void;
  onClose: () => void;
}

function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sess-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ChatPanel({
  workflowId,
  title,
  placeholder,
  initialMessage,
  onRun,
  onClose,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<string>(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(
    initialMessage ? [{ role: "bot", text: initialMessage }] : [],
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setSending(true);
    try {
      const res = await api.sendChatMessage(workflowId, text, sessionId);
      if (res.run_id) onRun(res.run_id);
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: res.reply,
          runId: res.run_id,
          error: res.status !== "success",
        },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        { role: "bot", text: "Could not reach the workflow.", error: true },
      ]);
    } finally {
      setSending(false);
    }
  }

  function reset(): void {
    setSessionId(newSessionId());
    setMessages(initialMessage ? [{ role: "bot", text: initialMessage }] : []);
  }

  return (
    <div className="chat-panel">
      <div className="chat-panel-header">
        <span>{title || "Chat"}</span>
        <div className="chat-panel-actions">
          <button type="button" onClick={reset} aria-label="Reset chat">
            Reset
          </button>
          <button type="button" onClick={onClose} aria-label="Close chat">
            ✕
          </button>
        </div>
      </div>
      <div className="chat-panel-messages">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`chat-bubble chat-bubble-${msg.role}${
              msg.error ? " chat-bubble-error" : ""
            }`}
          >
            {msg.text}
          </div>
        ))}
        {sending ? (
          <div className="chat-bubble chat-bubble-bot chat-typing">…</div>
        ) : null}
      </div>
      <div className="chat-panel-input">
        <input
          value={input}
          placeholder={placeholder || "Type a message…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button type="button" onClick={send} disabled={sending} aria-label="Send">
          Send
        </button>
      </div>
    </div>
  );
}
