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
import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useRef, useState } from "react";

import {
  api,
  type RunApprovalInfo,
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
  id: string;
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

function newMsgId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
  const html = DOMPurify.sanitize(marked.parse(trimmed) as string);
  return (
    <div
      className="chat-md"
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
  const failed = steps.filter((s) => s.status === "error").length;
  const awaiting = steps.filter((s) => s.status === "blocked").length;
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
        {awaiting > 0 ? (
          <span className="chat-agent-summary-wait">
            {awaiting} awaiting approval
          </span>
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

/** Inline approval card shown when the agent paused on a side-effecting tool
 *  and is waiting for the operator to approve or reject it. */
function ApprovalPrompt({
  approvals,
  decidingId,
  onDecide,
}: {
  approvals: RunApprovalInfo[];
  decidingId: string | null;
  onDecide: (approvalId: string, decision: "approve" | "reject") => void;
}) {
  const pending = approvals.filter((a) => a.status === "pending");
  if (pending.length === 0) return null;
  return (
    <div className="chat-approval">
      <div className="chat-approval-head">
        <Warning size={13} weight="fill" />
        <span>Approval needed to continue</span>
      </div>
      {pending.map((approval) => {
        const args = approval.arguments || {};
        const busy = decidingId === approval.id;
        return (
          <div key={approval.id} className="chat-approval-item">
            <div className="chat-approval-tool">
              <Wrench size={12} weight="fill" />
              <span>{approval.tool_name || "tool"}</span>
            </div>
            {approval.message ? (
              <div className="chat-approval-msg">{approval.message}</div>
            ) : null}
            {Object.keys(args).length > 0 ? (
              <div className="chat-agent-kv">
                {Object.entries(args).map(([key, value]) => (
                  <div key={key} className="chat-agent-kv-row">
                    <span className="chat-agent-kv-key">{key}</span>
                    <span className="chat-agent-kv-val">{previewValue(value)}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="chat-approval-actions">
              <button
                type="button"
                className="chat-approval-btn is-approve"
                disabled={busy}
                onClick={() => onDecide(approval.id, "approve")}
              >
                {busy ? (
                  <CircleNotch size={12} weight="bold" className="chat-agent-spin" />
                ) : (
                  <Check size={12} weight="bold" />
                )}
                Approve
              </button>
              <button
                type="button"
                className="chat-approval-btn is-reject"
                disabled={busy}
                onClick={() => onDecide(approval.id, "reject")}
              >
                <X size={12} weight="bold" />
                Reject
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setFailed(true);
      window.setTimeout(() => setFailed(false), 1400);
    }
  }
  return (
    <button
      type="button"
      className="chat-copy"
      onClick={copy}
      title={failed ? "Copy failed" : copied ? "Copied!" : "Copy reply"}
      aria-label="Copy reply"
    >
      {failed ? (
        <Warning size={12} weight="bold" />
      ) : copied ? (
        <Check size={12} weight="bold" />
      ) : (
        <Copy size={12} weight="bold" />
      )}
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
      ? [{ id: newMsgId(), role: "bot", text: initialMessage, ts: Date.now() }]
      : [],
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [phraseIdx, setPhraseIdx] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const socketRef = useRef<RunStreamHandle | null>(null);
  // Pending/decided tool approvals keyed by run id, so a paused agent run can
  // surface Approve/Reject buttons inline in the streaming bubble.
  const [approvalsByRun, setApprovalsByRun] = useState<
    Record<string, RunApprovalInfo[]>
  >({});
  const [decidingId, setDecidingId] = useState<string | null>(null);

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

  /** Pull the latest approval records for a run so the inline Approve/Reject
   *  card stays in sync with the engine's pause/resume state. */
  async function refreshApprovals(runId: string): Promise<void> {
    try {
      const rows = await api.runApprovals(runId);
      setApprovalsByRun((m) => ({ ...m, [runId]: rows }));
    } catch {
      // Best-effort: keep whatever we already have on transient failures.
    }
  }

  /** Send an approve/reject decision, then refresh so the buttons reflect the
   *  resolved state and the run resumes. */
  async function decideApproval(
    runId: string,
    approvalId: string,
    decision: "approve" | "reject",
  ): Promise<void> {
    setDecidingId(approvalId);
    try {
      const updated = await api.decideRunApproval(runId, approvalId, decision);
      setApprovalsByRun((m) => ({
        ...m,
        [runId]: (m[runId] || []).map((a) => (a.id === updated.id ? updated : a)),
      }));
    } catch {
      // Leave the buttons enabled so the operator can retry.
    } finally {
      setDecidingId(null);
      void refreshApprovals(runId);
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
          id: newMsgId(),
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
          id: newMsgId(),
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
      { id: newMsgId(), role: "bot", text: "", runId: activeRunId, streaming: true, steps: [], ts: Date.now() },
    ]);

    const trace = newAgentTrace();
    let finalized = false;
    // Set when the run pauses for operator approval (run_waiting). A paused run
    // is NOT terminal: the same run id resumes after the operator decides, so
    // we keep the stream open and only finalize on a real terminal event.
    let pausedForApproval = false;

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
      setApprovalsByRun((m) => {
        if (!(activeRunId in m)) return m;
        const next = { ...m };
        delete next[activeRunId];
        return next;
      });
      setSending(false);
    };

    const handleEvent = (data: unknown): void => {
      const ev = (data || {}) as {
        type?: string;
        status?: unknown;
      } & Record<string, unknown>;
      const type = typeof ev.type === "string" ? ev.type : "";
      if (AGENT_EVENT_TYPES.has(type)) {
        applyAgentEvent(trace, type, ev);
        const steps = traceSteps(trace);
        setMessages((m) => patchStreaming(m, { steps }));
        if (
          type === "agent_tool_approval_required" ||
          type === "agent_tool_approval_decided"
        ) {
          void refreshApprovals(activeRunId);
        }
        return;
      }
      if (type === "agent_resume_prepared") {
        // The operator decided and the run is resuming on the same socket.
        pausedForApproval = false;
        void refreshApprovals(activeRunId);
        return;
      }
      if (type === "run_waiting" || ev.status === "waiting") {
        // Paused for operator approval — surface the prompt and keep streaming.
        pausedForApproval = true;
        void refreshApprovals(activeRunId);
        return;
      }
      if (
        type === "run_finished" ||
        type === "run_error" ||
        type === "run_cancelled"
      ) {
        void finalize();
      }
    };

    const connect = (): void => {
      socketRef.current = subscribeToRunEvents(activeRunId, {
        onMessage: handleEvent,
        onClosed: () => {
          // If the socket drops while the run is paused for approval, reconnect
          // once (re-armed by the next run_waiting) instead of finalizing, so
          // the resumed run's events still reach the chat. ``finalized`` guards
          // against looping once the run has actually completed.
          if (pausedForApproval && !finalized) {
            pausedForApproval = false;
            connect();
            return;
          }
          void finalize();
        },
      });
    };
    connect();
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { id: newMsgId(), role: "user", text, ts: Date.now() }]);
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
          id: newMsgId(),
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
          id: newMsgId(),
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
    setApprovalsByRun({});
    setDecidingId(null);
    setSessionId(newSessionId());
    setMessages(
      initialMessage
        ? [{ id: newMsgId(), role: "bot", text: initialMessage, ts: Date.now() }]
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
          <div className="chat-head-sub" title={sessionId}>
            New conversation
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

        {messages.map((msg) => {
          const pendingApprovals = (
            (msg.runId && approvalsByRun[msg.runId]) || []
          ).filter((a) => a.status === "pending");
          return (
            <div key={msg.id} className={`chat-row chat-row-${msg.role}`}>
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
                {msg.role === "bot" && msg.runId && pendingApprovals.length > 0 ? (
                  <ApprovalPrompt
                    approvals={pendingApprovals}
                    decidingId={decidingId}
                    onDecide={(approvalId, decision) =>
                      void decideApproval(msg.runId!, approvalId, decision)
                    }
                  />
                ) : null}
                {msg.streaming ? (
                  <div
                    className="chat-bubble chat-bubble-bot chat-working"
                    aria-label={
                      pendingApprovals.length > 0
                        ? "Waiting for approval"
                        : "Agent is working"
                    }
                  >
                    {pendingApprovals.length > 0 ? (
                      <span className="chat-working-label">
                        Waiting for your approval…
                      </span>
                    ) : (
                      <>
                        <span className="chat-typing" aria-hidden>
                          <span />
                          <span />
                          <span />
                        </span>
                        <span className="chat-working-label">
                          {WORKING_PHRASES[phraseIdx]}
                        </span>
                      </>
                    )}
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
          );
        })}

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
              void send();
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
