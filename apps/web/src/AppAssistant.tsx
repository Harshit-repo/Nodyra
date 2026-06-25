import {
  ArrowSquareOut,
  CircleNotch,
  PaperPlaneTilt,
  Plus,
  Sparkle,
  Warning,
  X,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "./api";
import { LLM_PROVIDER_VARIANTS } from "./llmProviders";
import { safeGetItem, safeSetItem } from "./safeStorage";
import type { AiWorkflowDraftResponse } from "./types";

interface AssistantMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  error?: boolean;
  workflowId?: string;
  nodeCount?: number;
  planner?: string;
  missingCredentials?: string[];
}

/** Providers the server-side workflow planner can actually call. The picker is
 *  intentionally limited to these — other providers exist for node credentials
 *  but the draft planner only speaks OpenAI/Anthropic. */
const PLANNER_PROVIDERS = LLM_PROVIDER_VARIANTS.filter(
  (variant) => variant.value === "openai" || variant.value === "anthropic",
);

const PROVIDER_KEY = "noodle.assistant.provider";
const MODEL_KEY = "noodle.assistant.model";

function readStored(key: string): string {
  return safeGetItem(key) ?? "";
}

function writeStored(key: string, value: string): void {
  safeSetItem(key, value);
}

function curatedModels(provider: string): string[] {
  const variant = PLANNER_PROVIDERS.find((v) => v.value === provider);
  return variant?.curatedModels ?? [];
}

/** Turn a prompt into a short, human-friendly workflow name. */
function deriveName(prompt: string): string {
  const cleaned = prompt.trim().replace(/\s+/g, " ");
  if (!cleaned) return "AI workflow";
  const short =
    cleaned.length > 48 ? `${cleaned.slice(0, 48).trimEnd()}…` : cleaned;
  return short.charAt(0).toUpperCase() + short.slice(1);
}

/** Compose a concise assistant reply from a draft response. */
function summarizeDraft(draft: AiWorkflowDraftResponse): string {
  const nodeCount = draft.graph.nodes.length;
  const lines: string[] = [];
  lines.push(
    draft.explanation?.trim() ||
      `Built a workflow with ${nodeCount} node${nodeCount === 1 ? "" : "s"}.`,
  );
  const changes = (draft.change_summary || []).filter(Boolean);
  if (changes.length > 0) {
    lines.push("", ...changes.map((change) => `• ${change}`));
  }
  return lines.join("\n");
}

function friendlyError(message: string): string {
  if (/401|unauthorized/i.test(message)) {
    return "Your session expired. Sign in again and retry.";
  }
  if (/403|forbidden|permission/i.test(message)) {
    return "You don't have permission to create workflows.";
  }
  return "Something went wrong building that. Try rephrasing your request.";
}

export function AppAssistant(): JSX.Element {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [provider, setProvider] = useState<string>(
    () => readStored(PROVIDER_KEY) || "openai",
  );
  const [model, setModel] = useState<string>(
    () => readStored(MODEL_KEY) || curatedModels("openai")[0] || "",
  );
  const idRef = useRef(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Keep the newest message in view.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy, open]);

  // Focus the composer when the dock opens.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Auto-grow the composer up to a cap.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [input]);

  function pushMessage(message: Omit<AssistantMessage, "id">): void {
    idRef.current += 1;
    setMessages((prev) => [...prev, { ...message, id: idRef.current }]);
  }

  function onProviderChange(next: string): void {
    setProvider(next);
    writeStored(PROVIDER_KEY, next);
    const nextModel = curatedModels(next)[0] || "";
    setModel(nextModel);
    writeStored(MODEL_KEY, nextModel);
  }

  function onModelChange(next: string): void {
    setModel(next);
    writeStored(MODEL_KEY, next);
  }

  function startNew(): void {
    if (busy) return;
    setWorkflowId(null);
    setMessages([]);
    setInput("");
    inputRef.current?.focus();
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    pushMessage({ role: "user", text });
    setBusy(true);
    try {
      let wfId = workflowId;
      if (!wfId) {
        const created = await api.createWorkflow(deriveName(text));
        wfId = created.id;
        setWorkflowId(wfId);
      }
      const draft = await api.aiWorkflowDraft(wfId, {
        prompt: text,
        apply: true,
        mode: "draft",
        planner_provider: provider,
        planner_model: model,
      });
      pushMessage({
        role: "assistant",
        text: summarizeDraft(draft),
        workflowId: wfId,
        nodeCount: draft.graph.nodes.length,
        planner: draft.planner,
        missingCredentials: draft.missing_credentials,
      });
    } catch (err) {
      pushMessage({
        role: "assistant",
        error: true,
        text:
          err instanceof Error
            ? friendlyError(err.message)
            : "Something went wrong.",
      });
    } finally {
      setBusy(false);
    }
  }

  function openInEditor(id: string): void {
    setOpen(false);
    navigate(`/workflows/${id}`);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="assistant-fab"
        onClick={() => setOpen(true)}
        title="Build a workflow with AI"
        aria-label="Open the AI assistant"
      >
        <Sparkle size={20} weight="fill" />
      </button>
    );
  }

  return (
    <div className="assistant-dock" role="dialog" aria-label="AI assistant">
      <header className="assistant-head">
        <span className="assistant-head-badge" aria-hidden>
          <Sparkle size={15} weight="fill" />
        </span>
        <div className="assistant-head-titles">
          <div className="assistant-head-title">Assistant</div>
          <div className="assistant-head-sub">
            {workflowId ? "Refining your workflow" : "Describe a workflow to build"}
          </div>
        </div>
        <button
          type="button"
          className="assistant-icon-btn"
          onClick={startNew}
          disabled={busy || (messages.length === 0 && !workflowId)}
          title="New conversation"
          aria-label="Start a new conversation"
        >
          <Plus size={15} weight="bold" />
        </button>
        <button
          type="button"
          className="assistant-icon-btn"
          onClick={() => setOpen(false)}
          title="Close"
          aria-label="Close the assistant"
        >
          <X size={15} weight="bold" />
        </button>
      </header>

      <div className="assistant-picker">
        <label className="assistant-picker-field">
          <span>Provider</span>
          <select
            value={provider}
            onChange={(e) => onProviderChange(e.target.value)}
            disabled={busy}
          >
            {PLANNER_PROVIDERS.map((variant) => (
              <option key={variant.value} value={variant.value}>
                {variant.label}
              </option>
            ))}
          </select>
        </label>
        <label className="assistant-picker-field">
          <span>Model</span>
          <select
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            disabled={busy}
          >
            {curatedModels(provider).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="assistant-body" ref={bodyRef}>
        {messages.length === 0 ? (
          <div className="assistant-empty">
            <span className="assistant-empty-mark" aria-hidden>
              <Sparkle size={24} weight="duotone" />
            </span>
            <p className="assistant-empty-title">Build a workflow with AI</p>
            <p className="assistant-empty-hint">
              Tell me what you want to automate — I'll draft the nodes and open
              it in the editor. Uses your saved OpenAI/Anthropic credential.
            </p>
          </div>
        ) : null}

        {messages.map((msg) => (
          <div key={msg.id} className={`assistant-row assistant-row-${msg.role}`}>
            <div
              className={`assistant-bubble assistant-bubble-${msg.role}${
                msg.error ? " is-error" : ""
              }`}
            >
              <div className="assistant-bubble-text">{msg.text}</div>
              {msg.role === "assistant" && msg.missingCredentials &&
              msg.missingCredentials.length > 0 ? (
                <div className="assistant-note">
                  <Warning size={12} weight="fill" />
                  Needs credentials: {msg.missingCredentials.join(", ")}
                </div>
              ) : null}
              {msg.role === "assistant" && msg.planner &&
              msg.planner !== "llm" ? (
                <div className="assistant-note is-muted">
                  Built with the offline planner. Add an OpenAI/Anthropic
                  credential for smarter drafts.
                </div>
              ) : null}
              {msg.role === "assistant" && msg.workflowId ? (
                <button
                  type="button"
                  className="assistant-open-btn"
                  onClick={() => openInEditor(msg.workflowId!)}
                >
                  Open in editor
                  <ArrowSquareOut size={13} weight="bold" />
                </button>
              ) : null}
            </div>
          </div>
        ))}

        {busy ? (
          <div className="assistant-row assistant-row-assistant">
            <div className="assistant-bubble assistant-bubble-assistant assistant-working">
              <CircleNotch size={14} weight="bold" className="assistant-spin" />
              {workflowId ? "Updating…" : "Building your workflow…"}
            </div>
          </div>
        ) : null}
      </div>

      <div className="assistant-composer">
        <textarea
          ref={inputRef}
          className="assistant-input"
          value={input}
          rows={1}
          placeholder="Describe a workflow to build…"
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
          className="assistant-send"
          onClick={() => void send()}
          disabled={busy || !input.trim()}
          aria-label="Send"
          title="Send (Enter)"
        >
          <PaperPlaneTilt size={16} weight="fill" />
        </button>
      </div>
    </div>
  );
}
