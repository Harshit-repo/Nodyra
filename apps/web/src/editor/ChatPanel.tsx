import {
  ArrowCounterClockwise,
  CaretRight,
  Check,
  CheckCircle,
  CircleNotch,
  Copy,
  PaperPlaneTilt,
  Sparkle,
  Warning,
  Wrench,
  X,
} from "@phosphor-icons/react";
import { marked } from "marked";
import { useEffect, useRef, useState } from "react";

import {
  api,
  type RunStreamHandle,
  type RunTimelineEvent,
  subscribeToRunEvents,
} from "../api";
import type { ChatTurnResponse } from "../types";

marked.use({ gfm: true, breaks: true });

/** A single tool the agent invoked during a turn, reconstructed from the run
 *  timeline (or live run events) so the chat surface can show what the agent
 *  actually did. */
export interface AgentStep {
  id: string;
  tool: string;
  step: number;
  arguments?: Record<string, unknown>;
  status: "running" | "success" | "error" | "blocked";
  durationMs?: number;
  result?: string;
  isError?: boolean;
  approval?: "required" | "approved" | "rejected" | "auto";
}

interface ChatMessage {
  role: "user" | "bot";
  text: string;
  runId?: string | null;
  error?: boolean;
  steps?: AgentStep[];
  /** True while the run is still streaming events into this message. */
  streaming?: boolean;
  ts: number;
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
  /** When true, stream the run's live agent/tool events over the run
   *  WebSocket instead of fetching the trace after the turn completes. Only
   *  the authenticated editor surface (which can open the run socket) sets
   *  this; the public page leaves it off and uses the synchronous path. */
  live?: boolean;
}

function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sess-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const AGENT_EVENT_TYPES = new Set([
  "agent_action_requested",
  "agent_tool_started",
  "agent_tool_finished",
  "agent_tool_approval_required",
  "agent_tool_auto_approved",
  "agent_tool_approval_decided",
]);

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/** Accumulator for an ordered tool-call trace. Fed either by run-timeline
 *  events (after a turn) or by live run-stream events (during a turn). */
export interface AgentTrace {
  byKey: Map<string, AgentStep>;
  order: string[];
}

export function newAgentTrace(): AgentTrace {
  return { byKey: new Map(), order: [] };
}

/** Fold one agent event (timeline or live) into the trace.
 *
 *  Timeline events nest their fields under ``data``; live run-stream events
 *  carry them at the top level. Callers pass whichever record holds the
 *  fields as ``data`` so this stays transport-agnostic. */
export function applyAgentEvent(
  trace: AgentTrace,
  type: string,
  data: Record<string, unknown>,
): void {
  if (!AGENT_EVENT_TYPES.has(type)) return;
  if (type === "agent_action_requested") return;
  const callId = asString(data.tool_call_id);
  if (!callId) return;

  let step = trace.byKey.get(callId);
  if (!step) {
    step = { id: callId, tool: "tool", step: 0, status: "running" };
    trace.byKey.set(callId, step);
    trace.order.push(callId);
  }
  if (typeof data.step === "number") step.step = data.step;
  const toolName = asString(data.tool_name);
  if (toolName) step.tool = toolName;
  if (data.arguments && typeof data.arguments === "object") {
    step.arguments = data.arguments as Record<string, unknown>;
  }

  if (type === "agent_tool_started") {
    step.status = "running";
  } else if (type === "agent_tool_approval_required") {
    step.status = "blocked";
    step.approval = "required";
  } else if (type === "agent_tool_auto_approved") {
    step.approval = "auto";
  } else if (type === "agent_tool_approval_decided") {
    const decided = asString(data.status);
    step.approval = decided === "approved" ? "approved" : "rejected";
    if (decided === "rejected") step.status = "blocked";
  } else if (type === "agent_tool_finished") {
    if (typeof data.duration_ms === "number") step.durationMs = data.duration_ms;
    const result = data.tool_result as Record<string, unknown> | undefined;
    if (result && typeof result === "object") {
      step.result = asString(result.content);
      step.isError = result.is_error === true;
    }
    step.status =
      step.isError || asString(data.status) === "error" ? "error" : "success";
  }
}

export function traceSteps(trace: AgentTrace): AgentStep[] {
  return trace.order.map((k) => ({ ...trace.byKey.get(k)! }));
}

/** Reconstruct an ordered tool-call trace from a run's timeline events. */
export function extractAgentSteps(events: RunTimelineEvent[]): AgentStep[] {
  const trace = newAgentTrace();
  for (const ev of events) {
    applyAgentEvent(trace, ev.type, (ev.data || {}) as Record<string, unknown>);
  }
  return traceSteps(trace);
}

function previewValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Render a bot reply as markdown. Falls back to a JSON code block when the
 *  text is a bare JSON object/array (e.g. an unwired trigger's raw output). */
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
      /* not JSON after all — fall through to markdown */
    }
  }
  const html = marked.parse(trimmed) as string;
  return (
    <div
      className="chat-md"
      // Content originates from the workflow owner's own model on their own
      // server — same trust level as the existing <pre> output path.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function StepStatusIcon({ step }: { step: AgentStep }) {
  if (step.status === "running") {
    return <CircleNotch size={13} weight="bold" className="chat-agent-spin" />;
  }
  if (step.status === "error" || step.status === "blocked") {
    return <Warning size={13} weight="fill" />;
  }
  return <CheckCircle size={13} weight="fill" />;
}

function AgentStepRow({ step }: { step: AgentStep }) {
  const [open, setOpen] = useState(false);
  const argEntries = step.arguments ? Object.entries(step.arguments) : [];
  const hasDetail = argEntries.length > 0 || Boolean(step.result);
  return (
    <li className={`chat-agent-step is-${step.status}`}>
      <button
        type="button"
        className="chat-agent-step-head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}
      >
        <span className="chat-agent-step-icon" aria-hidden>
          <StepStatusIcon step={step} />
        </span>
        <span className="chat-agent-step-name">{step.tool}</span>
        {step.approval === "required" ? (
          <span className="chat-agent-tag">needs approval</span>
        ) : null}
        {step.approval === "rejected" ? (
          <span className="chat-agent-tag">rejected</span>
        ) : null}
        {typeof step.durationMs === "number" ? (
          <span className="chat-agent-step-time">{step.durationMs}ms</span>
        ) : null}
        {hasDetail ? (
          <CaretRight
            size={11}
            weight="bold"
            className={`chat-agent-caret${open ? " is-open" : ""}`}
          />
        ) : null}
      </button>
      {open && hasDetail ? (
        <div className="chat-agent-step-body">
          {argEntries.length > 0 ? (
            <div className="chat-agent-kv">
              {argEntries.map(([key, value]) => (
                <div key={key} className="chat-agent-kv-row">
                  <span className="chat-agent-kv-key">{key}</span>
                  <span className="chat-agent-kv-val">{previewValue(value)}</span>
                </div>
              ))}
            </div>
          ) : null}
          {step.result ? (
            <pre className={`chat-agent-result${step.isError ? " is-error" : ""}`}>
              {step.result}
            </pre>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function AgentActivity({
  steps,
  live = false,
}: {
  steps: AgentStep[];
  live?: boolean;
}) {
  const [open, setOpen] = useState(live);
  // Keep the panel expanded while events are still streaming in.
  useEffect(() => {
    if (live) setOpen(true);
  }, [live]);
  if (steps.length === 0) return null;
  const failed = steps.filter(
    (s) => s.status === "error" || s.status === "blocked",
  ).length;
  const running = steps.some((s) => s.status === "running");
  return (
    <div className={`chat-agent${live ? " is-live" : ""}`}>
      <button
        type="button"
        className="chat-agent-summary"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <CaretRight
          size={11}
          weight="bold"
          className={`chat-agent-caret${open ? " is-open" : ""}`}
        />
        <span className="chat-agent-summary-icon" aria-hidden>
          {running ? (
            <CircleNotch size={12} weight="bold" className="chat-agent-spin" />
          ) : (
            <Wrench size={12} weight="fill" />
          )}
        </span>
        <span className="chat-agent-summary-text">
          {running ? "Using" : "Used"} {steps.length} tool
          {steps.length === 1 ? "" : "s"}
        </span>
        {failed > 0 ? (
          <span className="chat-agent-summary-err">{failed} failed</span>
        ) : null}
      </button>
      {open ? (
        <ol className="chat-agent-steps">
          {steps.map((step) => (
            <AgentStepRow key={step.id} step={step} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable — ignore */
    }
  }
  return (
    <button
      type="button"
      className="chat-copy"
      onClick={copy}
      title="Copy reply"
      aria-label="Copy reply"
    >
      {copied ? <Check size={12} weight="bold" /> : <Copy size={12} weight="bold" />}
    </button>
  );
}

function formatTime(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

const WORKING_PHRASES = [
  "Thinking…",
  "Working…",
  "Calling tools…",
  "Gathering context…",
];

/** Patch the in-flight streaming bot message (the most recent one still
 *  marked ``streaming``) with new fields. */
function patchStreaming(
  messages: ChatMessage[],
  patch: Partial<ChatMessage>,
): ChatMessage[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].streaming) {
      const next = messages.slice();
      next[i] = { ...next[i], ...patch };
      return next;
    }
  }
  return messages;
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
  live = false,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<string>(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(
    initialMessage
      ? [{ role: "bot", text: initialMessage, ts: Date.now() }]
      : [],
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [phraseIdx, setPhraseIdx] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const socketRef = useRef<RunStreamHandle | null>(null);

  // Tear the run socket down if the panel unmounts mid-stream.
  useEffect(() => {
    return () => {
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  // Keep the latest message in view as the conversation grows.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  // Cycle the "working" status label so the wait feels alive.
  useEffect(() => {
    if (!sending) return;
    setPhraseIdx(0);
    const timer = window.setInterval(() => {
      setPhraseIdx((i) => (i + 1) % WORKING_PHRASES.length);
    }, 1600);
    return () => window.clearInterval(timer);
  }, [sending]);

  // Auto-grow the composer textarea up to its max-height.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [input]);

  async function loadAgentSteps(runId: string): Promise<AgentStep[]> {
    try {
      const timeline = await api.runTimeline(runId);
      return extractAgentSteps(timeline.events);
    } catch {
      // Best-effort: the public chat surface may not expose the timeline.
      return [];
    }
  }

  /** Live path (editor): start the run, stream agent/tool events over the run
   *  WebSocket into a placeholder bubble, then fetch the final reply. */
  async function sendLive(text: string): Promise<void> {
    let runId: string | null = null;
    try {
      const start = await api.startChatTurn(workflowId, text, sessionId);
      runId = start.run_id;
    } catch {
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: "Could not reach the workflow.",
          error: true,
          ts: Date.now(),
        },
      ]);
      setSending(false);
      return;
    }
    if (!runId) {
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: "The workflow did not start.",
          error: true,
          ts: Date.now(),
        },
      ]);
      setSending(false);
      return;
    }
    const activeRunId = runId;
    onRun(activeRunId);
    setMessages((m) => [
      ...m,
      { role: "bot", text: "", runId: activeRunId, streaming: true, steps: [], ts: Date.now() },
    ]);

    const trace = newAgentTrace();
    let finalized = false;

    const finalize = async (): Promise<void> => {
      if (finalized) return;
      finalized = true;
      socketRef.current?.close();
      socketRef.current = null;
      let reply = "";
      let status = "success";
      try {
        const res = await api.chatTurnResult(workflowId, activeRunId, sessionId);
        reply = res.reply;
        status = res.status;
      } catch {
        reply = "Could not load the workflow's reply.";
        status = "error";
      }
      let steps = traceSteps(trace);
      if (steps.length === 0) steps = await loadAgentSteps(activeRunId);
      setMessages((m) =>
        patchStreaming(m, {
          text: reply,
          error: status !== "success",
          streaming: false,
          steps: steps.length ? steps : undefined,
        }),
      );
      setSending(false);
    };

    socketRef.current = subscribeToRunEvents(activeRunId, {
      onMessage: (data) => {
        const ev = (data || {}) as { type?: string } & Record<string, unknown>;
        const type = typeof ev.type === "string" ? ev.type : "";
        if (AGENT_EVENT_TYPES.has(type)) {
          applyAgentEvent(trace, type, ev);
          const steps = traceSteps(trace);
          setMessages((m) => patchStreaming(m, { steps }));
        } else if (
          type === "run_finished" ||
          type === "run_error" ||
          type === "run_cancelled"
        ) {
          void finalize();
        }
      },
      onClosed: () => {
        void finalize();
      },
    });
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text, ts: Date.now() }]);
    setSending(true);

    // Editor surface: stream the run live. Public/non-live: synchronous send.
    if (live && !sendMessage) {
      await sendLive(text);
      return;
    }

    try {
      const res = sendMessage
        ? await sendMessage(text, sessionId)
        : await api.sendChatMessage(workflowId, text, sessionId);
      if (res.run_id) onRun(res.run_id);
      const steps = res.run_id ? await loadAgentSteps(res.run_id) : [];
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: res.reply,
          runId: res.run_id,
          error: res.status !== "success",
          steps: steps.length ? steps : undefined,
          ts: Date.now(),
        },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: "Could not reach the workflow.",
          error: true,
          ts: Date.now(),
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  function reset(): void {
    socketRef.current?.close();
    socketRef.current = null;
    setSending(false);
    setSessionId(newSessionId());
    setMessages(
      initialMessage
        ? [{ role: "bot", text: initialMessage, ts: Date.now() }]
        : [],
    );
  }

  const empty = messages.length === 0 && !sending;
  const hasStreaming = messages.some((m) => m.streaming);

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
              shows the agent's tool calls and the final reply.
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
            <div className="chat-row-content">
              {msg.role === "bot" && msg.steps && msg.steps.length > 0 ? (
                <AgentActivity steps={msg.steps} live={msg.streaming} />
              ) : null}
              {msg.streaming ? (
                <div
                  className="chat-bubble chat-bubble-bot chat-working"
                  aria-label="Agent is working"
                >
                  <span className="chat-typing" aria-hidden>
                    <span />
                    <span />
                    <span />
                  </span>
                  <span className="chat-working-label">
                    {WORKING_PHRASES[phraseIdx]}
                  </span>
                </div>
              ) : (
                <>
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
                  <div className={`chat-meta chat-meta-${msg.role}`}>
                    <span className="chat-meta-time">{formatTime(msg.ts)}</span>
                    {msg.role === "bot" && !msg.error ? (
                      <CopyButton text={msg.text} />
                    ) : null}
                  </div>
                </>
              )}
            </div>
          </div>
        ))}

        {sending && !hasStreaming ? (
          <div className="chat-row chat-row-bot">
            <span className="chat-avatar" aria-hidden>
              <Sparkle size={14} weight="fill" />
            </span>
            <div className="chat-row-content">
              <div
                className="chat-bubble chat-bubble-bot chat-working"
                aria-label="Agent is working"
              >
                <span className="chat-typing" aria-hidden>
                  <span />
                  <span />
                  <span />
                </span>
                <span className="chat-working-label">
                  {WORKING_PHRASES[phraseIdx]}
                </span>
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div className="chat-composer">
        <textarea
          ref={inputRef}
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
