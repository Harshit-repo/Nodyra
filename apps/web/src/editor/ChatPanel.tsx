import { ArrowCounterClockwise, PaperPlaneTilt, Sparkle, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { ChatTurnResponse } from "../types";

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
  onViewRun?: (runId: string) => void;
  /** Override the default authenticated send; used by the public chat page. */
  sendMessage?: (message: string, sessionId: string) => Promise<ChatTurnResponse>;
}

function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sess-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Render a bot reply: pretty-print JSON payloads (e.g. an unwired trigger's
 *  raw output) as a code block, otherwise plain text. */
function MessageBody({ text }: { text: string }) {
  const trimmed = text.trim();
  const looksJson =
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"));
  if (looksJson) {
    try {
      const parsed = JSON.parse(trimmed);
      return <pre className="chat-json">{JSON.stringify(parsed, null, 2)}</pre>;
    } catch {
      /* not JSON after all — fall through to text */
    }
  }
  return <span className="chat-text">{text}</span>;
}

export function ChatPanel({
  workflowId,
  title,
  placeholder,
  initialMessage,
  onRun,
  onClose,
  onViewRun,
  sendMessage,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<string>(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(
    initialMessage ? [{ role: "bot", text: initialMessage }] : [],
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Keep the latest message in view as the conversation grows.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setSending(true);
    try {
      const res = sendMessage
        ? await sendMessage(text, sessionId)
        : await api.sendChatMessage(workflowId, text, sessionId);
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

  const empty = messages.length === 0 && !sending;

  return (
    <div className="chat-panel">
      <header className="chat-head">
        <span className="chat-head-badge" aria-hidden>
          <Sparkle size={15} weight="fill" />
        </span>
        <div className="chat-head-titles">
          <div className="chat-head-title">{title || "Chat"}</div>
          <div className="chat-head-sub">
            test chat · {sessionId.slice(0, 8)}
          </div>
        </div>
        <button
          type="button"
          className="chat-icon-btn"
          onClick={reset}
          title="Reset conversation"
          aria-label="Reset chat"
        >
          <ArrowCounterClockwise size={15} weight="bold" />
        </button>
        <button
          type="button"
          className="chat-icon-btn"
          onClick={onClose}
          title="Close chat"
          aria-label="Close chat"
        >
          <X size={15} weight="bold" />
        </button>
      </header>

      <div className="chat-body" ref={bodyRef}>
        {empty ? (
          <div className="chat-empty">
            <span className="chat-empty-mark" aria-hidden>
              <Sparkle size={26} weight="duotone" />
            </span>
            <p className="chat-empty-title">Test your chat workflow</p>
            <p className="chat-empty-hint">
              Send a message — it runs the workflow from the Chat Trigger and
              shows the final node's reply.
            </p>
          </div>
        ) : null}

        {messages.map((msg, i) => (
          <div key={i} className={`chat-row chat-row-${msg.role}`}>
            {msg.role === "bot" ? (
              <span
                className={`chat-avatar${msg.error ? " is-error" : ""}`}
                aria-hidden
              >
                <Sparkle size={14} weight="fill" />
              </span>
            ) : null}
            <div
              className={`chat-bubble chat-bubble-${msg.role}${
                msg.error ? " chat-bubble-error" : ""
              }`}
            >
              <MessageBody text={msg.text} />
              {msg.error && msg.runId && onViewRun ? (
                <button
                  type="button"
                  className="chat-view-run"
                  onClick={() => onViewRun(msg.runId!)}
                >
                  View run →
                </button>
              ) : null}
            </div>
          </div>
        ))}

        {sending ? (
          <div className="chat-row chat-row-bot">
            <span className="chat-avatar" aria-hidden>
              <Sparkle size={14} weight="fill" />
            </span>
            <div className="chat-bubble chat-bubble-bot chat-typing" aria-label="Thinking">
              <span />
              <span />
              <span />
            </div>
          </div>
        ) : null}
      </div>

      <div className="chat-composer">
        <textarea
          className="chat-input"
          value={input}
          rows={1}
          placeholder={placeholder || "Message your workflow…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="button"
          className="chat-send"
          onClick={send}
          disabled={sending || !input.trim()}
          aria-label="Send"
          title="Send (Enter)"
        >
          <PaperPlaneTilt size={16} weight="fill" />
        </button>
      </div>
    </div>
  );
}
